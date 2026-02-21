"""
Order workflow: proposal generation and confirmation.
"""
from datetime import date, timedelta
from typing import TYPE_CHECKING, List, Tuple, Optional, Dict, Any
import logging

if TYPE_CHECKING:
    from ..domain.contracts import OrderExplain

from ..domain.models import Stock, OrderProposal, OrderConfirmation, Transaction, EventType, SKU, SalesRecord
from ..persistence.csv_layer import CSVLayer
from ..domain.ledger import StockCalculator, ShelfLifeCalculator
from ..domain.promo_uplift import is_in_post_promo_window, estimate_post_promo_dip
from ..analytics.target_resolver import TargetServiceLevelResolver
from ..domain.calendar import Lane, next_receipt_date, calculate_protection_period_days
from ..analytics.pipeline import build_open_pipeline
from ..replenishment_policy import compute_order, OrderConstraints


def _normalize_boost_to_fraction(boost: float) -> float:
    """Normalize OOS boost to a fraction.

    Accepts either:
    - fraction (e.g. 0.20 for 20%)
    - percent points (e.g. 20 for 20%)
    """
    try:
        boost_val = float(boost)
    except (TypeError, ValueError):
        return 0.0

    if boost_val <= 0:
        return 0.0

    # Heuristic: values > 1 are interpreted as "percent points" (0..100)
    if boost_val > 1.0:
        return boost_val / 100.0

    return boost_val

def simulate_intermittent_demand(
    daily_sales_avg: float,
    current_ip: int,
    pack_size: int,
    lead_time: int,
    review_period: int,
    moq: int,
    max_stock: int,
) -> Tuple[int, int, str]:
    """
    Simulate intermittent/low-volume demand day-by-day.
    
    For SKUs with low movement (< 1 pack every 2-3 days), linear forecast
    can underestimate. This simulates daily consumption and triggers reorder
    when IP would drop below 1 pack.
    
    Args:
        daily_sales_avg: Average daily sales (can be < 1)
        current_ip: Current inventory position (on_hand + on_order - unfulfilled)
        pack_size: Pack size
        lead_time: Lead time in days
        review_period: Review period in days
        moq: Minimum order quantity
        max_stock: Max stock cap
    
    Returns:
        (proposed_qty, trigger_day, notes)
        - proposed_qty: Quantity to order (in units, not packs)
        - trigger_day: Day when IP drops below 1 pack (0=today, 1=tomorrow, etc.)
        - notes: Simulation explanation
    """
    horizon = lead_time + review_period
    ip_simulated = current_ip
    trigger_day = -1
    
    # Simulate consumption day by day
    for day in range(horizon):
        # Expected consumption for this day
        expected_consumption = daily_sales_avg
        ip_simulated -= expected_consumption
        
        # Trigger if IP would drop below 1 pack
        if ip_simulated < pack_size and trigger_day == -1:
            trigger_day = day
            break
    
    # If no trigger found in horizon, no order needed
    if trigger_day == -1:
        return 0, -1, "Simulazione: IP rimane sopra 1 collo per tutto l'orizzonte"
    
    # Propose 1 pack (or MOQ if higher)
    proposed_packs = 1
    proposed_qty = proposed_packs * pack_size
    
    # Apply MOQ
    if proposed_qty < moq:
        proposed_qty = moq
    
    # Cap at max_stock
    if current_ip + proposed_qty > max_stock:
        proposed_qty = max(0, max_stock - current_ip)
        # Re-apply pack constraint
        if proposed_qty > 0 and pack_size > 1:
            proposed_qty = (proposed_qty // pack_size) * pack_size
    
    notes = f"Simulazione: IP scenderebbe sotto 1 collo al giorno {trigger_day} (orizzonte {horizon}d)"
    return proposed_qty, trigger_day, notes


class OrderWorkflow:
    """Order processing: proposal generation and confirmation."""
    
    def __init__(self, csv_layer: CSVLayer, lead_time_days: Optional[int] = None):
        """
        Initialize order workflow.
        
        Args:
            csv_layer: CSV persistence layer
            lead_time_days: Default lead time for orders (days). If None, reads from settings.
        """
        self.csv_layer = csv_layer
        
        # Read lead_time from settings if not provided
        if lead_time_days is None:
            settings = csv_layer.read_settings()
            self.lead_time_days = settings.get("reorder_engine", {}).get("lead_time_days", {}).get("value", 7)
        else:
            self.lead_time_days = lead_time_days
    
    def _get_mc_parameters(self, sku_obj: Optional[SKU], settings: dict) -> dict:
        """
        Get Monte Carlo parameters with SKU override → global fallback logic.
        
        Args:
            sku_obj: SKU object (may have MC overrides)
            settings: Global settings dict
        
        Returns:
            Dict with MC parameters: distribution, n_simulations, random_seed, etc.
        """
        mc_section = settings.get("monte_carlo", {})
        
        # Helper to get value with SKU override fallback
        def _get_param(sku_field, global_key, default):
            if sku_obj:
                sku_value = getattr(sku_obj, sku_field, None)
                # For string fields: empty string means use global
                if isinstance(default, str):
                    if sku_value and sku_value.strip():
                        return sku_value
                # For numeric fields: 0 means use global
                elif isinstance(default, int):
                    if sku_value and sku_value > 0:
                        return sku_value
            
            # Fallback to global
            return mc_section.get(global_key, {}).get("value", default)
        
        return {
            "distribution": _get_param("mc_distribution", "distribution", "empirical"),
            "n_simulations": _get_param("mc_n_simulations", "n_simulations", 1000),
            "random_seed": _get_param("mc_random_seed", "random_seed", 42),
            "output_stat": _get_param("mc_output_stat", "output_stat", "mean"),
            "output_percentile": _get_param("mc_output_percentile", "output_percentile", 80),
            "horizon_mode": _get_param("mc_horizon_mode", "horizon_mode", "auto"),
            "horizon_days": _get_param("mc_horizon_days", "horizon_days", 14),
        }
    
    def _deduce_lane(
        self,
        target_receipt_date: Optional[date],
        protection_period_days: Optional[int],
        order_date: date
    ) -> Lane:
        """
        Deduce Lane from target_receipt_date and protection_period_days.
        
        Uses reverse-engineering: try different lanes and see which one matches
        the provided target_receipt_date and protection_period_days.
        
        Args:
            target_receipt_date: Target receipt date (calendar-aware)
            protection_period_days: Protection period P (calendar-aware)
            order_date: Order date (typically today)
        
        Returns:
            Lane enum (STANDARD, SATURDAY, MONDAY). Defaults to STANDARD if no match.
        """
        if not target_receipt_date or not protection_period_days:
            return Lane.STANDARD
        
        # Try each lane and see if it produces matching results
        for lane_candidate in [Lane.SATURDAY, Lane.MONDAY, Lane.STANDARD]:
            try:
                # Calculate expected receipt_date and protection_period for this lane
                expected_receipt_date = next_receipt_date(order_date, lane_candidate)
                expected_protection_period = calculate_protection_period_days(
                    order_date, lane_candidate
                )
                
                # Check if both match
                if (expected_receipt_date == target_receipt_date and
                    expected_protection_period == protection_period_days):
                    return lane_candidate
            except ValueError:
                # Lane validation failed (e.g., SATURDAY from Thursday)
                continue
        
        # Fallback to STANDARD if no match
        logging.debug(
            f"Could not deduce lane for target_receipt_date={target_receipt_date}, "
            f"protection_period={protection_period_days}. Using STANDARD."
        )
        return Lane.STANDARD

    
    def generate_proposal(
        self,
        sku: str,
        description: str,
        current_stock: Stock,
        daily_sales_avg: float,
        min_stock: int = 10,
        days_cover: int = 30,
        sku_obj: Optional[SKU] = None,
        oos_days_count: int = 0,
        oos_boost_percent: float = 0.0,
        target_receipt_date: Optional[date] = None,
        protection_period_days: Optional[int] = None,
        transactions: Optional[List[Transaction]] = None,
        sales_records: Optional[List[SalesRecord]] = None,
        pipeline_extra: Optional[List[dict]] = None,
    ) -> OrderProposal:
        """
        Generate order proposal based on stock and sales history.
        
        NEW FORMULA (2026-01-29):
        S = forecast × (lead_time + review_period) + safety_stock
        proposed = max(0, S − IP)
        
        CALENDAR-AWARE (2026-02-11):
        - If target_receipt_date is provided, IP uses inventory_position(as_of=target_receipt_date)
          which filters on_order by receipt_date <= target, enabling dual-order Friday scenarios
        - If protection_period_days is provided, it replaces (lead_time + review_period) for forecast horizon
        
        Then: apply pack_size rounding → MOQ rounding → cap at max_stock
        
        Args:
            sku: SKU identifier
            description: SKU description
            current_stock: Current stock state (on_hand, on_order from ledger as-of today)
            daily_sales_avg: Average daily sales (from historical data)
            min_stock: Minimum stock threshold (global default, overridden by SKU reorder_point)
            days_cover: Days of sales to cover (DEPRECATED, now uses lead_time + review_period)
            sku_obj: SKU object (for pack_size, MOQ, lead_time, review_period, safety_stock, max_stock)
            oos_days_count: Count of OOS days (for display/notes)
            oos_boost_percent: OOS boost percentage (for safety stock adjustment)
            target_receipt_date: Target receipt date (calendar-aware, filters pipeline by receipt_date)
            protection_period_days: Protection period P (calendar-aware, replaces lead_time + review_period)
            transactions: All transactions (required for inventory_position calculation if target_receipt_date provided)
            sales_records: All sales records (required for inventory_position calculation if target_receipt_date provided)
            pipeline_extra: Extra pipeline items for CSL mode (Friday dual-lane support). List of dicts with keys: receipt_date (date), qty (int). Appended to unfulfilled orders from order_logs.csv.
        
        Returns:
            OrderProposal with suggested quantity (adjusted for pack_size, MOQ, and max_stock cap)
        """
        # Use SKU-specific parameters if available
        pack_size = sku_obj.pack_size if sku_obj else 1
        moq = sku_obj.moq if sku_obj else 1
        lead_time = sku_obj.lead_time_days if (sku_obj and sku_obj.lead_time_days > 0) else self.lead_time_days
        review_period = sku_obj.review_period if sku_obj else 7
        safety_stock_base = sku_obj.safety_stock if sku_obj else 0
        shelf_life_days = sku_obj.shelf_life_days if sku_obj else 0
        max_stock = sku_obj.max_stock if sku_obj else 999
        demand_variability = sku_obj.demand_variability if sku_obj else None
        
        # Apply demand variability multiplier to safety stock
        # HIGH variability → increase safety stock by 50%
        # STABLE → reduce by 20%
        # SEASONAL/LOW → no adjustment (base value)
        safety_stock = safety_stock_base
        if demand_variability:
            from ..domain.models import DemandVariability
            if demand_variability == DemandVariability.HIGH:
                safety_stock = int(safety_stock_base * 1.5)
            elif demand_variability == DemandVariability.STABLE:
                safety_stock = int(safety_stock_base * 0.8)
            # SEASONAL and LOW keep base value
        
        # Use SKU-specific OOS boost if set (> 0), otherwise use global setting.
        # Note: SKU stores boost as percent points (0..100), while UI may pass a fraction (0..1).
        effective_boost_raw = sku_obj.oos_boost_percent if (sku_obj and sku_obj.oos_boost_percent > 0) else oos_boost_percent
        effective_boost = _normalize_boost_to_fraction(effective_boost_raw)
        
        # Detect intermittent demand pattern (low movement)
        # Threshold: if daily_sales_avg < pack_size / 2.5, use simulation
        intermittent_threshold = pack_size / 2.5 if pack_size > 1 else 0.5
        use_simulation = daily_sales_avg < intermittent_threshold
        
        simulation_used = False
        simulation_trigger_day = 0
        simulation_notes = ""
        
        # === CALENDAR-AWARE PLANNING HORIZON ===
        # Use protection_period_days if provided (calendar-aware), otherwise lead_time + review_period
        if protection_period_days is not None:
            forecast_period = protection_period_days
            # For receipt_date calc below, derive effective lead_time from target date
            if target_receipt_date:
                effective_lead_time = (target_receipt_date - date.today()).days
            else:
                effective_lead_time = lead_time
        else:
            # Traditional formula
            forecast_period = lead_time + review_period
            effective_lead_time = lead_time
        
        # === FORECAST METHOD SELECTION (SIMPLE vs MONTE CARLO) ===
        # Read global settings
        settings = self.csv_layer.read_settings()
        global_forecast_method = settings.get("reorder_engine", {}).get("forecast_method", {}).get("value", "simple")
        mc_show_comparison = settings.get("monte_carlo", {}).get("show_comparison", {}).get("value", False)
        
        # === POLICY MODE SELECTION (LEGACY vs CSL) ===
        policy_mode = settings.get("reorder_engine", {}).get("policy_mode", {}).get("value", "legacy")
        
        # Resolve target CSL (alpha) for CSL mode
        target_alpha = 0.95  # Default fallback
        if policy_mode == "csl":
            try:
                resolver = TargetServiceLevelResolver(settings)
                if sku_obj:
                    target_alpha = resolver.get_target_csl(sku_obj)
                else:
                    # Fallback to default CSL if no SKU object
                    target_alpha = settings.get("service_level", {}).get("default_csl", {}).get("value", 0.95)
            except Exception as e:
                logging.warning(f"Failed to resolve target CSL for {sku}: {e}. Using default 0.95")
                target_alpha = 0.95
        
        # === SHELF LIFE INTEGRATION (Fase 2/3) ===
        # Calculate usable stock BEFORE forecast to use in IP and waste_rate
        shelf_life_enabled = settings.get("shelf_life_policy", {}).get("enabled", {}).get("value", True)
        usable_result = None
        usable_qty = current_stock.on_hand  # Default: use total on_hand
        unusable_qty = 0
        waste_risk_percent = 0.0
        expected_waste_rate = 0.0  # For Monte Carlo adjustment
        
        if shelf_life_enabled and shelf_life_days > 0:
            # Determina parametri shelf life con category override (SKU > Category > Global)
            category = demand_variability.value if demand_variability else "STABLE"
            category_overrides = settings.get("shelf_life_policy", {}).get("category_overrides", {}).get("value", {})
            category_params = category_overrides.get(category, {})
            
            # SKU-specific > Category > Global fallback
            min_shelf_life = sku_obj.min_shelf_life_days if (sku_obj and sku_obj.min_shelf_life_days > 0) else \
                             category_params.get("min_shelf_life_days", 
                             settings.get("shelf_life_policy", {}).get("min_shelf_life_global", {}).get("value", 7))
            
            waste_horizon_days = settings.get("shelf_life_policy", {}).get("waste_horizon_days", {}).get("value", 14)
            
            # Fetch lots for SKU and calculate usable stock
            lots = self.csv_layer.get_lots_by_sku(sku, sort_by_expiry=True)
            
            # CALENDAR-AWARE: Use target_receipt_date as check_date if provided
            # This accounts for lots that will expire between today and receipt
            check_date_for_usable = target_receipt_date if target_receipt_date else date.today()
            
            usable_result = ShelfLifeCalculator.calculate_usable_stock(
                lots=lots,
                check_date=check_date_for_usable,
                min_shelf_life_days=min_shelf_life,
                waste_horizon_days=waste_horizon_days
            )
            
            usable_qty = usable_result.usable_qty
            unusable_qty = usable_result.unusable_qty
            waste_risk_percent = usable_result.waste_risk_percent
            
            # SAFETY FALLBACK: If lots don't cover ledger stock, use ledger as source of truth
            # This prevents artificial IP deflation when lot tracking is incomplete/desynchronized
            lots_total = usable_result.total_on_hand
            ledger_stock = current_stock.on_hand
            discrepancy_threshold = max(5, ledger_stock * 0.1)  # 10% or 5 units

            # SYNTHETIC LOT RESYNC: A synthetic lot is a ledger-derived placeholder.
            # After any ADJUST / WASTE / SALE, its qty may differ from the current ledger
            # stock in *either* direction.  Always keep it in sync so IP is never stale.
            only_synthetic = lots and all(l.lot_id.startswith("SYNTHETIC_") for l in lots)
            if only_synthetic and lots_total != ledger_stock:
                from ..domain.models import Lot as _Lot
                from datetime import timedelta as _td
                synthetic_id = f"SYNTHETIC_{sku}"
                today_d = date.today()
                synthetic_expiry = today_d + _td(days=shelf_life_days)
                for _lot in lots:
                    if _lot.lot_id == synthetic_id:
                        synced_lot = _Lot(
                            lot_id=synthetic_id,
                            sku=sku,
                            expiry_date=synthetic_expiry,
                            qty_on_hand=ledger_stock,
                            receipt_id="SYNTHETIC",
                            receipt_date=_lot.receipt_date,
                        )
                        self.csv_layer.write_lot(synced_lot)
                        logging.info(
                            f"Resynced synthetic lot '{synthetic_id}' for {sku}: "
                            f"qty {lots_total} → {ledger_stock} pz (ledger-driven resync)"
                        )
                        break
                # Re-fetch and recalculate with updated qty
                lots = self.csv_layer.get_lots_by_sku(sku, sort_by_expiry=True)
                usable_result = ShelfLifeCalculator.calculate_usable_stock(
                    lots=lots,
                    check_date=check_date_for_usable,
                    min_shelf_life_days=min_shelf_life,
                    waste_horizon_days=waste_horizon_days,
                )
                usable_qty = usable_result.usable_qty
                unusable_qty = usable_result.unusable_qty
                waste_risk_percent = usable_result.waste_risk_percent
                lots_total = usable_result.total_on_hand

            if not lots or lots_total < ledger_stock - discrepancy_threshold:
                if not lots and ledger_stock > 0:
                    # No lots at all: stock was received outside the lot-tracking workflow
                    # (e.g. SNAPSHOT, legacy import). Auto-create a synthetic lot so shelf-life
                    # tracking starts working immediately. lot_id is stable → idempotent.
                    from ..domain.models import Lot as _Lot
                    from datetime import timedelta as _td
                    synthetic_id = f"SYNTHETIC_{sku}"
                    today_d = date.today()
                    # Assume stock is freshly received today — pessimistic but safe.
                    synthetic_expiry = today_d + _td(days=shelf_life_days)
                    synthetic_lot = _Lot(
                        lot_id=synthetic_id,
                        sku=sku,
                        expiry_date=synthetic_expiry,
                        qty_on_hand=ledger_stock,
                        receipt_id="SYNTHETIC",
                        receipt_date=today_d,
                    )
                    self.csv_layer.write_lot(synthetic_lot)
                    logging.info(
                        f"Auto-created synthetic lot '{synthetic_id}' for {sku}: "
                        f"qty={ledger_stock}, expiry={synthetic_expiry} "
                        f"(shelf_life={shelf_life_days}d). "
                        f"Re-run receiving workflow for accurate expiry dates."
                    )
                    # Re-fetch lots and recalculate with the new synthetic lot
                    lots = self.csv_layer.get_lots_by_sku(sku, sort_by_expiry=True)
                    usable_result = ShelfLifeCalculator.calculate_usable_stock(
                        lots=lots,
                        check_date=check_date_for_usable,
                        min_shelf_life_days=min_shelf_life,
                        waste_horizon_days=waste_horizon_days,
                    )
                    usable_qty = usable_result.usable_qty
                    unusable_qty = usable_result.unusable_qty
                    waste_risk_percent = usable_result.waste_risk_percent
                else:
                    # Lots exist but are lower than ledger (partial discrepancy) → fallback
                    logging.warning(
                        f"Shelf life fallback for {sku}: "
                        f"lots total={lots_total}, ledger on_hand={ledger_stock}. "
                        f"Using ledger stock, waste risk set to 0%. "
                        f"Reconcile lots.csv with ledger to restore shelf life tracking."
                    )
                    usable_qty = ledger_stock
                    unusable_qty = 0
                    waste_risk_percent = 0.0
                    # Note: expected_waste_rate stays 0.0 (already initialized)
            else:
                # Lots data reliable: use shelf life calculations normally
                # Calculate expected waste rate for Monte Carlo adjustment (Fase 3)
                # Use demand-adjusted waste risk if forecast available, otherwise use current risk
                if waste_risk_percent > 0:
                    from ..uncertainty import WasteUncertainty
                    waste_realization_factor = settings.get("shelf_life_policy", {}).get("waste_realization_factor", {}).get("value", 0.5)
                    
                    # Calculate demand-adjusted current waste risk (prospective, without order)
                    # This gives a more realistic expected waste rate for Monte Carlo
                    if daily_sales_avg > 0:
                        min_shelf_life_mc = sku_obj.min_shelf_life_days if (sku_obj and sku_obj.min_shelf_life_days > 0) else \
                                           category_params.get("min_shelf_life_days", 
                                           settings.get("shelf_life_policy", {}).get("min_shelf_life_global", {}).get("value", 7))
                        waste_horizon_days_mc = settings.get("shelf_life_policy", {}).get("waste_horizon_days", {}).get("value", 14)
                        
                        # Calculate current waste risk adjusted for expected demand (no incoming order)
                        waste_risk_adjusted_current, _, _, _ = ShelfLifeCalculator.calculate_forward_waste_risk_demand_adjusted(
                            lots=lots,
                            receipt_date=date.today(),  # Current state
                            proposed_qty=0,  # No incoming order yet
                            sku_shelf_life_days=shelf_life_days,
                            min_shelf_life_days=min_shelf_life_mc,
                            waste_horizon_days=waste_horizon_days_mc,
                            forecast_daily_demand=daily_sales_avg
                        )
                        
                        # Use demand-adjusted risk for expected waste rate (more realistic)
                        expected_waste_rate = WasteUncertainty.calculate_expected_waste_rate(
                            waste_risk_percent=waste_risk_adjusted_current,
                            waste_realization_factor=waste_realization_factor
                        )
                    else:
                        # No forecast: fallback to current waste risk
                        expected_waste_rate = WasteUncertainty.calculate_expected_waste_rate(
                            waste_risk_percent=waste_risk_percent,
                            waste_realization_factor=waste_realization_factor
                        )
        
        # Override with SKU-specific method if provided
        forecast_method = sku_obj.forecast_method if (sku_obj and sku_obj.forecast_method) else global_forecast_method
        
        # Variables for MC comparison and details
        mc_comparison_qty = None
        mc_method_used = ""
        mc_distribution_used = ""
        mc_n_simulations_used = 0
        mc_random_seed_used = 0
        mc_output_stat_used = ""
        mc_output_percentile_used = 0
        mc_horizon_mode_used = ""
        mc_horizon_days_used = 0
        mc_forecast_values_summary = ""
        
        # Execute forecast based on selected method
        if forecast_method == "monte_carlo":
            # === MONTE CARLO FORECAST ===
            # Get MC parameters (SKU override → global fallback)
            mc_params = self._get_mc_parameters(sku_obj, settings)
            
            # Store MC parameters for proposal details
            mc_method_used = "monte_carlo"
            mc_distribution_used = mc_params["distribution"]
            mc_n_simulations_used = mc_params["n_simulations"]
            mc_random_seed_used = mc_params["random_seed"]
            mc_output_stat_used = mc_params["output_stat"]
            mc_output_percentile_used = mc_params["output_percentile"]
            mc_horizon_mode_used = mc_params["horizon_mode"]
            
            # Determine horizon
            if mc_params["horizon_mode"] == "auto":
                horizon_days = forecast_period
            else:  # custom
                horizon_days = mc_params["horizon_days"]
            
            mc_horizon_days_used = horizon_days
            
            # Fetch historical sales data for SKU
            sales_records = self.csv_layer.read_sales()
            sku_sales_history = [
                {"date": rec.date, "qty_sold": rec.qty_sold}
                for rec in sales_records if rec.sku == sku
            ]
            
            # Run Monte Carlo forecast
            from ..forecast import monte_carlo_forecast
            try:
                mc_forecast_values = monte_carlo_forecast(
                    history=sku_sales_history,
                    horizon_days=horizon_days,
                    distribution=mc_params["distribution"],
                    n_simulations=mc_params["n_simulations"],
                    random_seed=mc_params["random_seed"],
                    output_stat=mc_params["output_stat"],
                    output_percentile=mc_params["output_percentile"],
                    expected_waste_rate=expected_waste_rate,  # NEW (Fase 3): shelf life waste adjustment
                )
                
                # Build summary of forecast values
                if mc_forecast_values:
                    mc_min = int(min(mc_forecast_values))
                    mc_max = int(max(mc_forecast_values))
                    mc_avg = int(sum(mc_forecast_values) / len(mc_forecast_values))
                    mc_forecast_values_summary = f"min={mc_min}, max={mc_max}, avg={mc_avg}"
                
                # Use sum of forecast over horizon as total demand
                forecast_qty = int(sum(mc_forecast_values))
                lead_time_demand = int(sum(mc_forecast_values[:effective_lead_time])) if len(mc_forecast_values) >= effective_lead_time else forecast_qty
            except Exception as e:
                # Fallback to simple forecast if MC fails
                logging.warning(f"Monte Carlo forecast failed for SKU {sku}: {e}. Falling back to simple forecast.")
                forecast_qty = int(daily_sales_avg * forecast_period)
                lead_time_demand = int(daily_sales_avg * lead_time)
                mc_method_used = ""  # Reset since MC failed
        
        else:
            # === SIMPLE FORECAST (level × period) ===
            forecast_qty = int(daily_sales_avg * forecast_period)
            lead_time_demand = int(daily_sales_avg * effective_lead_time)
        
        # === UNIFIED MODIFIER BRIDGE ===
        # Single canonical point for ALL pre-policy demand modifiers
        # (promo uplift, event uplift, cannibalization, holiday) for BOTH
        # legacy and CSL policy modes.
        #
        # Design rationale:
        #   • Replaces two separate inline blocks that diverged on flag-gating;
        #     the former inline block only fed CSL when promo_adjustment_enabled=True,
        #     meaning event-only adjustments were silently excluded from CSL.
        #   • Holiday modifiers are new (no inline equivalent to replace).
        #   • apply_modifiers() is deterministic: same inputs → same outputs.
        #
        # Stop condition (architecture): modifiers applied at ONE point
        # pre-policy for both modes; no silent flag-gated exclusions.
        baseline_forecast_qty = forecast_qty  # Store baseline for traceability
        promo_adjusted_forecast_qty = forecast_qty  # Default: same as baseline
        promo_adjustment_note = ""
        promo_uplift_factor_used = 1.0
        _modifiers_applied = False  # Track whether any modifier changed forecast_qty

        # Cannibalization tracking
        cannibalization_applied_val = False
        cannibalization_driver_sku_val = ""
        cannibalization_downlift_factor_val = 1.0
        cannibalization_confidence_val = ""
        cannibalization_note_val = ""

        # Event uplift tracking
        event_uplift_active_val = False
        event_uplift_factor_val = 1.0
        event_u_store_day_val = 1.0
        event_beta_i_val = 1.0
        event_m_i_val = 1.0
        event_reason_val = ""
        event_delivery_date_val = None
        event_quantile_val = 0.0
        event_fallback_level_val = ""
        event_beta_fallback_level_val = ""
        event_explain_short_val = ""

        # Read modifier enable flags (kept as variables for downstream CSL check)
        promo_adj_settings = settings.get("promo_adjustment", {})
        promo_adjustment_enabled = promo_adj_settings.get("enabled", {}).get("value", False)
        event_uplift_settings = settings.get("event_uplift", {})
        event_uplift_enabled = event_uplift_settings.get("enabled", {}).get("value", False)
        _holiday_mod_enabled = settings.get("holiday_modifier", {}).get("enabled", {}).get("value", False)

        _any_modifier_enabled = promo_adjustment_enabled or event_uplift_enabled or _holiday_mod_enabled

        if _any_modifier_enabled and forecast_qty > 0:
            try:
                import re as _re
                from ..domain.modifier_builder import apply_modifiers as _apply_mods
                from ..domain.contracts import DemandDistribution as _DD

                # Load inputs (lazy; each branch only if the flag is enabled)
                _sales_for_mods = self.csv_layer.read_sales() if sales_records is None else sales_records
                _trans_for_mods = self.csv_layer.read_transactions() if transactions is None else transactions
                _all_skus_mods = self.csv_layer.read_skus()
                _promo_wins = self.csv_layer.read_promo_calendar() if promo_adjustment_enabled else []
                _evt_rules = self.csv_layer.read_event_uplift_rules() if event_uplift_enabled else []
                _holidays_mods: list = []
                if _holiday_mod_enabled:
                    try:
                        _holidays_mods = self.csv_layer.read_holidays()
                    except Exception:
                        _holidays_mods = []

                # Horizon: delivery_date is the anchor (or today + 1..period as fallback)
                if target_receipt_date:
                    _anchor = target_receipt_date
                    _horizon = [
                        _anchor - timedelta(days=forecast_period - 1 - i)
                        for i in range(forecast_period)
                    ]
                else:
                    _horizon = [
                        date.today() + timedelta(days=i + 1)
                        for i in range(forecast_period)
                    ]

                _base_dd = _DD(
                    mu_P=float(forecast_qty),
                    sigma_P=max(1.0, float(daily_sales_avg) * (forecast_period ** 0.5)),
                    protection_period_days=forecast_period,
                    forecast_method="legacy",
                )

                _adj_dd, _applied_mods = _apply_mods(
                    base_demand=_base_dd,
                    sku_id=sku,
                    sku_obj=sku_obj,
                    horizon_dates=_horizon,
                    target_receipt_date=target_receipt_date,
                    asof_date=date.today(),
                    settings=settings,
                    all_skus=_all_skus_mods,
                    promo_windows=_promo_wins,
                    event_rules=_evt_rules,
                    sales_records=_sales_for_mods,
                    transactions=_trans_for_mods,
                    holidays=_holidays_mods,
                )

                # Apply adjusted forecast
                _new_fc = int(round(_adj_dd.mu_P))
                if _new_fc != forecast_qty:
                    _modifiers_applied = True
                    forecast_qty = _new_fc

                # ── Extract legacy tracking vars from applied_modifiers ──
                for _mod in _applied_mods:
                    _mtype = _mod.modifier_type

                    if _mtype == "promo":
                        promo_uplift_factor_used = _mod.multiplier
                        _conf = _mod.confidence or ""
                        promo_adjusted_forecast_qty = int(round(_mod.mu_after)) if _mod.mu_after else forecast_qty
                        if abs(_mod.multiplier - 1.0) > 0.01:
                            promo_adjustment_note = (
                                f"Promo attiva: Uplift {_mod.multiplier:.2f}x"
                                + (f" ({_conf})" if _conf else "")
                            )
                        else:
                            promo_adjustment_note = "Nessuna promo attiva (baseline usata)"

                    elif _mtype == "cannibalization":
                        cannibalization_applied_val = True
                        cannibalization_driver_sku_val = _mod.source_sku or ""
                        cannibalization_downlift_factor_val = _mod.multiplier
                        cannibalization_confidence_val = _mod.confidence or ""
                        _red = (1.0 - _mod.multiplier) * 100
                        cannibalization_note_val = (
                            f"Riduzione cannibalizzazione: -{_red:.1f}% "
                            f"(driver: {cannibalization_driver_sku_val}, "
                            f"confidence {cannibalization_confidence_val})"
                        )

                    elif _mtype == "event":
                        event_uplift_active_val = True
                        event_m_i_val = _mod.multiplier
                        event_uplift_factor_val = _mod.multiplier
                        event_delivery_date_val = target_receipt_date
                        # Parse display-only detail fields from note string
                        # Note format: "U_store=X.XXX, beta=Y.YYY, m_i=Z.ZZZ, PNNN, level"
                        _nm = _re.search(
                            r"U_store=([\d.]+).*?beta=([\d.]+).*?P(\d+),\s*(\S+)",
                            _mod.note or "",
                        )
                        if _nm:
                            event_u_store_day_val = float(_nm.group(1))
                            event_beta_i_val = float(_nm.group(2))
                            event_quantile_val = int(_nm.group(3)) / 100.0
                            event_fallback_level_val = _nm.group(4)
                        _ename = _mod.name or ""
                        event_reason_val = _ename.replace("event_uplift_", "") if "event_uplift_" in _ename else ""
                        _chg = (event_m_i_val - 1.0) * 100
                        event_explain_short_val = (
                            f"Event {'+' if _chg >= 0 else ''}{_chg:.0f}% "
                            f"({event_reason_val or 'no reason'}, "
                            f"P{int(event_quantile_val * 100)}, "
                            f"{event_fallback_level_val})"
                        )
                        logging.info(
                            "Event uplift applied to %s: m_i=%.3f, "
                            "forecast adjusted from %d to %d",
                            sku, event_m_i_val, baseline_forecast_qty, forecast_qty,
                        )

            except Exception as _bridge_exc:
                logging.warning(
                    "Unified modifier bridge failed for SKU %s: %s. "
                    "Using baseline forecast.",
                    sku, _bridge_exc,
                )
                forecast_qty = baseline_forecast_qty
        
        S = forecast_qty + safety_stock
        
        # Check shelf life warning (if shelf_life_days > 0)
        shelf_life_warning = False
        capped_by_shelf_life = False
        if shelf_life_days > 0 and daily_sales_avg > 0:
            shelf_life_capacity = int(daily_sales_avg * shelf_life_days)
            if S > shelf_life_capacity:
                shelf_life_warning = True
        
        # === CALENDAR-AWARE INVENTORY POSITION ===
        # If target_receipt_date provided, use PROJECTED IP that considers:
        # 1. Forecast sales between today and target_date
        # 2. Orders arriving between today and target_date
        # 3. Lots expiring between today and target_date (already in usable_qty)
        # Otherwise, use traditional on_hand + on_order (unfiltered)
        if target_receipt_date and transactions is not None:
            # Build current_stock with adjusted on_hand for shelf life
            # Use usable_qty as on_hand since it already accounts for lots expiring by target_date
            adjusted_stock = Stock(
                sku=sku,
                on_hand=usable_qty,  # Already projected to target_receipt_date
                on_order=current_stock.on_order,
                unfulfilled_qty=current_stock.unfulfilled_qty,
                asof_date=current_stock.asof_date
            )
            
            # Use projected IP: accounts for sales between today and target
            inventory_position = StockCalculator.projected_inventory_position(
                sku=sku,
                target_date=target_receipt_date,
                current_stock=adjusted_stock,
                transactions=transactions,
                daily_sales_forecast=daily_sales_avg,
                sales_records=sales_records,
            )
        else:
            # Traditional: on_order unfiltered (legacy behavior)
            inventory_position = usable_qty + current_stock.on_order - current_stock.unfulfilled_qty
        
        unfulfilled_qty = current_stock.unfulfilled_qty
        
        # === MONTE CARLO COMPARISON (se richiesto) ===
        if mc_show_comparison and forecast_method != "monte_carlo":
            # Calcola MC forecast come riferimento informativo
            try:
                mc_params = self._get_mc_parameters(sku_obj, settings)
                mc_horizon = forecast_period if mc_params["horizon_mode"] == "auto" else mc_params["horizon_days"]
                
                # Store MC parameters for comparison details
                mc_distribution_used = mc_params["distribution"]
                mc_n_simulations_used = mc_params["n_simulations"]
                mc_random_seed_used = mc_params["random_seed"]
                mc_output_stat_used = mc_params["output_stat"]
                mc_output_percentile_used = mc_params["output_percentile"]
                mc_horizon_mode_used = mc_params["horizon_mode"]
                mc_horizon_days_used = mc_horizon
                
                sales_records = self.csv_layer.read_sales()
                sku_sales_history = [
                    {"date": rec.date, "qty_sold": rec.qty_sold}
                    for rec in sales_records if rec.sku == sku
                ]
                
                from ..forecast import monte_carlo_forecast
                mc_forecast_values = monte_carlo_forecast(
                    history=sku_sales_history,
                    horizon_days=mc_horizon,
                    distribution=mc_params["distribution"],
                    n_simulations=mc_params["n_simulations"],
                    random_seed=mc_params["random_seed"],
                    output_stat=mc_params["output_stat"],
                    output_percentile=mc_params["output_percentile"],
                    expected_waste_rate=expected_waste_rate,  # NEW (Fase 3): shelf life waste adjustment
                )
                
                # Build summary of forecast values
                if mc_forecast_values:
                    mc_min = int(min(mc_forecast_values))
                    mc_max = int(max(mc_forecast_values))
                    mc_avg = int(sum(mc_forecast_values) / len(mc_forecast_values))
                    mc_forecast_values_summary = f"min={mc_min}, max={mc_max}, avg={mc_avg}"
                
                mc_forecast_qty = int(sum(mc_forecast_values))
                mc_S = mc_forecast_qty + safety_stock
                mc_proposed_raw = max(0, mc_S - inventory_position)
                
                # Applica arrotondamenti come per proposta principale
                if mc_proposed_raw > 0 and pack_size > 1:
                    mc_comparison_qty = ((mc_proposed_raw + pack_size - 1) // pack_size) * pack_size
                else:
                    mc_comparison_qty = mc_proposed_raw
                
                if mc_comparison_qty > 0 and moq > 1:
                    mc_comparison_qty = ((mc_comparison_qty + moq - 1) // moq) * moq
                
                # Cap at max_stock
                if inventory_position + mc_comparison_qty > max_stock:
                    mc_comparison_qty = max(0, max_stock - inventory_position)
            except Exception as e:
                logging.warning(f"MC comparison failed for SKU {sku}: {e}")
                mc_comparison_qty = None
        
        # === POLICY MODE BRANCH: CSL vs LEGACY ===
        csl_breakdown = {}  # Store CSL calculation details
        
        if policy_mode == "csl":
            # CSL mode: use compute_order from replenishment_policy.py
            # Build open pipeline from unfulfilled orders + pipeline_extra
            try:
                order_date = date.today()
                pipeline = build_open_pipeline(self.csv_layer, sku, order_date)
                
                # Append pipeline_extra if provided (Friday dual-lane support)
                if pipeline_extra:
                    pipeline.extend(pipeline_extra)
                    # Re-sort by receipt_date
                    pipeline.sort(key=lambda x: x["receipt_date"])
                
                # Deduce lane from calendar parameters
                lane = self._deduce_lane(target_receipt_date, protection_period_days, order_date)
                
                # Build order constraints
                constraints = OrderConstraints(
                    pack_size=pack_size,
                    moq=moq,
                    max_stock=max_stock
                )
                
                # Prepare sales history for compute_order
                if sales_records:
                    history = [
                        {"date": rec.date, "qty_sold": rec.qty_sold}
                        for rec in sales_records if rec.sku == sku
                    ]
                else:
                    history = []
                
                # Feed modifier-adjusted forecast into CSL policy.
                # Previously gated on promo_adjustment_enabled only; now always
                # set when any modifier (event / promo / holiday) changed the forecast.
                forecast_demand_override = None
                if _modifiers_applied or forecast_qty != baseline_forecast_qty:
                    forecast_demand_override = float(forecast_qty)
                
                # Call compute_order with optional forecast override
                csl_result = compute_order(
                    sku=sku,
                    order_date=order_date,
                    lane=lane,
                    alpha=target_alpha,
                    on_hand=usable_qty,  # Use shelf-life adjusted on_hand
                    pipeline=pipeline,
                    constraints=constraints,
                    history=history,
                    window_weeks=12,  # Default 12 weeks for forecast
                    censored_flags=None,  # TODO: integrate censored days detection
                    forecast_demand_override=forecast_demand_override,  # NEW: event/promo-adjusted forecast
                )
                
                # Extract proposed_qty_raw from CSL result
                proposed_qty_raw = int(csl_result.get("order_final", 0))
                proposed_qty_before_rounding = proposed_qty_raw
                
                # Store CSL breakdown for OrderProposal
                csl_breakdown = {
                    "policy_mode": "csl",
                    "alpha_target": target_alpha,
                    "alpha_eff": csl_result.get("alpha_eff", target_alpha),
                    "reorder_point": csl_result.get("reorder_point", 0.0),
                    "forecast_demand": csl_result.get("forecast_demand", 0.0),
                    "sigma_horizon": csl_result.get("sigma_horizon", 0.0),
                    "z_score": csl_result.get("z_score", 0.0),
                    "lane": lane.value,
                    "n_censored": csl_result.get("n_censored", 0),
                }
                
                logging.info(
                    f"CSL policy for {sku}: lane={lane.value}, alpha={target_alpha:.3f}, "
                    f"S={csl_result.get('reorder_point', 0):.1f}, "
                    f"order_final={proposed_qty_raw}"
                )
                
            except Exception as e:
                logging.error(f"CSL computation failed for {sku}: {e}. Falling back to legacy mode.")
                # Fallback to legacy formula
                proposed_qty_raw = max(0, S - inventory_position)
                proposed_qty_before_rounding = proposed_qty_raw
                csl_breakdown = {"policy_mode": "legacy_fallback"}
        
        else:
            # LEGACY mode: traditional formula with optional simulation
            csl_breakdown = {"policy_mode": "legacy"}
            
            # Use simulation for intermittent demand
            if use_simulation:
                sim_qty, sim_trigger, sim_notes = simulate_intermittent_demand(
                    daily_sales_avg=daily_sales_avg,
                    current_ip=inventory_position,
                    pack_size=pack_size,
                    lead_time=lead_time,
                    review_period=review_period,
                    moq=moq,
                    max_stock=max_stock,
                )
                proposed_qty_raw = sim_qty
                simulation_used = True
                simulation_trigger_day = sim_trigger
                simulation_notes = sim_notes
                proposed_qty_before_rounding = proposed_qty_raw
            else:
                # Standard formula: proposed = max(0, S − IP)
                proposed_qty_raw = max(0, S - inventory_position)
                proposed_qty_before_rounding = proposed_qty_raw
        
        # === END POLICY MODE BRANCH ===
        
        # === PROMO PREBUILD ANTICIPATION ===
        # Calculate prebuild quantity if upcoming promo and this order arrives before promo start
        promo_prebuild_enabled = False
        promo_start_date_val = None
        target_open_qty = 0
        projected_stock_on_promo_start = 0
        prebuild_delta_qty = 0
        prebuild_qty = 0
        prebuild_coverage_days_val = 0
        prebuild_distribution_note = ""
        
        # Check if prebuild enabled in settings
        prebuild_settings = settings.get("promo_prebuild", {})
        prebuild_enabled_flag = prebuild_settings.get("enabled", {}).get("value", False)
        
        if prebuild_enabled_flag and target_receipt_date and transactions is not None:
            # Load prebuild parameters
            prebuild_coverage_days = prebuild_settings.get("coverage_days", {}).get("value", 0)
            prebuild_safety_mode = prebuild_settings.get("safety_component_mode", {}).get("value", "multiplier")
            prebuild_safety_value = prebuild_settings.get("safety_component_value", {}).get("value", 0.2)
            prebuild_min_days_to_start = prebuild_settings.get("min_days_to_promo_start", {}).get("value", 3)
            prebuild_max_horizon_days = prebuild_settings.get("max_prebuild_horizon_days", {}).get("value", 30)
            
            # Find upcoming promo for this SKU
            # Load promo calendar
            all_promo_windows = self.csv_layer.read_promo_calendar()
            all_skus_list = self.csv_layer.read_skus()
            all_sales_records = sales_records if sales_records else self.csv_layer.read_sales()
            
            # Filter promos for this SKU that start AFTER target_receipt_date (order arrives before promo)
            upcoming_promos = [
                pw for pw in all_promo_windows
                if pw.sku == sku and pw.start_date > target_receipt_date
            ]
            
            # Sort by start_date (earliest first)
            upcoming_promos.sort(key=lambda pw: pw.start_date)
            
            if upcoming_promos:
                # Use earliest upcoming promo
                next_promo = upcoming_promos[0]
                promo_start_candidate = next_promo.start_date
                
                # Validate constraints:
                # 1. target_receipt_date must be at least min_days_to_promo_start BEFORE promo
                # 2. promo must be within max_prebuild_horizon_days from today
                days_to_promo_start = (promo_start_candidate - target_receipt_date).days
                days_from_today_to_promo = (promo_start_candidate - date.today()).days
                
                if days_to_promo_start >= prebuild_min_days_to_start and days_from_today_to_promo <= prebuild_max_horizon_days:
                    # Calculate target opening stock at promo start
                    try:
                        target_open_qty, coverage_used, forecast_total = calculate_prebuild_target(
                            sku=sku,
                            promo_start_date=promo_start_candidate,
                            coverage_days=prebuild_coverage_days,
                            safety_component_mode=prebuild_safety_mode,
                            safety_component_value=prebuild_safety_value,
                            promo_windows=all_promo_windows,
                            sales_records=all_sales_records,
                            transactions=transactions,
                            all_skus=all_skus_list,
                            csv_layer=self.csv_layer,
                            settings=settings,
                        )
                        prebuild_coverage_days_val = coverage_used
                        
                        # Calculate projected stock at promo start date (OPENING stock before promo sales)
                        # Use baseline forecast (NOT promo-adjusted) because we want stock projection BEFORE promo starts
                        projected_stock_on_promo_start = StockCalculator.projected_inventory_position(
                            sku=sku,
                            target_date=promo_start_candidate,
                            current_stock=current_stock,
                            transactions=transactions,
                            daily_sales_forecast=daily_sales_avg,  # Baseline, not promo-adjusted
                            sales_records=all_sales_records,
                        )
                        
                        # Calculate prebuild delta (how much MORE we need by promo start)
                        prebuild_delta_qty = max(0, target_open_qty - projected_stock_on_promo_start)
                        
                        # Mark prebuild as ATTEMPTED (even if delta=0)
                        promo_prebuild_enabled = True
                        promo_start_date_val = promo_start_candidate
                        
                        if prebuild_delta_qty > 0:
                            # Add prebuild quantity to proposal
                            # For MVP: allocate FULL delta to THIS order (no distribution across multiple dates)
                            # Future enhancement: distribute across multiple pre-start order opportunities
                            prebuild_qty = prebuild_delta_qty
                            proposed_qty_raw += prebuild_qty
                            
                            # Distribution note (for traceability)
                            prebuild_distribution_note = f"Prebuild totale allocato a questo ordine (arrivo {target_receipt_date.isoformat()}, promo start {promo_start_candidate.isoformat()})"
                        else:
                            # Delta <= 0: no prebuild needed (projected >= target)
                            prebuild_distribution_note = f"Prebuild non necessario: projected stock ({projected_stock_on_promo_start}) >= target ({target_open_qty})"
                        
                    except Exception as e:
                        logging.warning(f"Promo prebuild calculation failed for SKU {sku}: {e}. Skipping prebuild.")
        
        # === END PROMO PREBUILD ===
        
        # === POST-PROMO GUARDRAIL (Anti-Overstock) ===
        # If receipt_date falls within post-promo window (X days after promo end),
        # apply cooldown factor + optional qty cap to avoid overstock from continued ordering
        post_promo_guardrail_applied = False
        post_promo_window_days_val = 0
        post_promo_factor_used_val = 1.0
        post_promo_cap_applied_val = False
        post_promo_dip_factor_val = 1.0
        post_promo_alert_val = ""
        
        # Load post-promo guardrail settings
        post_promo_settings = settings.get("post_promo_guardrail", {})
        post_promo_enabled = post_promo_settings.get("enabled", {}).get("value", False)
        
        if post_promo_enabled and target_receipt_date and transactions is not None:
            # Load all promo calendar for post-promo detection
            all_promo_windows = self.csv_layer.read_promo_calendar()
            all_skus_list = self.csv_layer.read_skus()
            all_sales_records = sales_records if sales_records else self.csv_layer.read_sales()
            
            # Load parameters
            post_promo_window_days = post_promo_settings.get("window_days", {}).get("value", 7)
            cooldown_factor = post_promo_settings.get("cooldown_factor", {}).get("value", 0.8)
            qty_cap_enabled = post_promo_settings.get("qty_cap_enabled", {}).get("value", False)
            qty_cap_value = post_promo_settings.get("qty_cap_value", {}).get("value", 0)
            use_historical_dip = post_promo_settings.get("use_historical_dip", {}).get("value", False)
            dip_min_events = post_promo_settings.get("dip_min_events", {}).get("value", 2)
            dip_floor = post_promo_settings.get("dip_floor", {}).get("value", 0.5)
            dip_ceiling = post_promo_settings.get("dip_ceiling", {}).get("value", 1.0)
            shelf_life_severity_enabled = post_promo_settings.get("shelf_life_severity_enabled", {}).get("value", True)
            
            # Check if receipt_date falls in any post-promo window
            active_post_promo_window = is_in_post_promo_window(
                receipt_date=target_receipt_date,
                promo_windows=all_promo_windows,
                sku=sku,
                window_days=post_promo_window_days
            )
            
            if active_post_promo_window:
                # We are in post-promo window: apply guardrail
                post_promo_guardrail_applied = True
                post_promo_window_days_val = post_promo_window_days
                
                qty_before_post_promo = proposed_qty_raw
                
                # Step 1: Apply cooldown factor (reduction)
                if cooldown_factor < 1.0:
                    proposed_qty_raw = int(proposed_qty_raw * cooldown_factor)
                    post_promo_factor_used_val = cooldown_factor
                
                # Step 2: Apply historical dip factor if enabled
                if use_historical_dip:
                    try:
                        dip_report = estimate_post_promo_dip(
                            sku_id=sku,
                            promo_windows=all_promo_windows,
                            sales_records=all_sales_records,
                            transactions=transactions,
                            all_skus=all_skus_list,
                            window_days=post_promo_window_days,
                            min_events=dip_min_events,
                            dip_floor=dip_floor,
                            dip_ceiling=dip_ceiling,
                            asof_date=date.today(),
                        )
                        
                        if dip_report.confidence in ["A", "B"]:
                            # Use dip factor from historical analysis
                            proposed_qty_raw = int(proposed_qty_raw * dip_report.dip_factor)
                            post_promo_dip_factor_val = dip_report.dip_factor
                        else:
                            # Insufficient data (confidence C) → use cooldown only
                            post_promo_dip_factor_val = 1.0
                    except Exception as e:
                        logging.warning(f"Post-promo dip estimation failed for SKU {sku}: {e}. Using cooldown only.")
                        post_promo_dip_factor_val = 1.0
                
                # Step 3: Apply absolute qty cap (if enabled)
                if qty_cap_enabled and qty_cap_value > 0:
                    if proposed_qty_raw > qty_cap_value:
                        proposed_qty_raw = qty_cap_value
                        post_promo_cap_applied_val = True
                
                # Step 4: Shelf-life severity modifier (increase reduction for short shelf life)
                if shelf_life_severity_enabled and shelf_life_days > 0 and daily_sales_avg > 0:
                    # Use existing waste_risk_demand_adjusted_percent thresholds
                    waste_risk_threshold = settings.get("waste_risk_demand_adjusted_percent", {}).get("value", 15.0)
                    
                    # Estimate forward waste risk at receipt_date + shelf_life_days
                    # (simplified: check if projected stock exceeds max or shelf capacity)
                    shelf_life_capacity = int(daily_sales_avg * shelf_life_days)
                    projected_stock_at_receipt = inventory_position + proposed_qty_raw
                    
                    # If projected stock > shelf_life_capacity OR > max_stock → apply additional reduction
                    if projected_stock_at_receipt > shelf_life_capacity or projected_stock_at_receipt > max_stock:
                        # Apply 20% additional reduction (arbitrary, can be parameterized)
                        severity_factor = 0.8
                        proposed_qty_raw = int(proposed_qty_raw * severity_factor)
                        post_promo_alert_val = f"Rischio overstock post-promo: riduzione shelf-life severity applicata (projected: {projected_stock_at_receipt}, max: {max_stock})"
                
                # Step 5: Alert if projected stock > max_stock after all reductions
                projected_stock_final = inventory_position + proposed_qty_raw
                if projected_stock_final > max_stock:
                    post_promo_alert_val = f"⚠️ RISCHIO OVERSTOCK POST PROMO: Projected stock ({projected_stock_final}) > Max stock ({max_stock})"
                elif not post_promo_alert_val:
                    # No shelf-life alert already set
                    post_promo_alert_val = f"Post-promo guardrail attivo: riduzione da {qty_before_post_promo} a {proposed_qty_raw}"
        
        # === END POST-PROMO GUARDRAIL ===
        
        # === OOS BOOST (DEPRECATED - NOW HANDLED IN FORECAST) ===
        # OOS boost is now applied as censored-demand correction in forecast model
        # (via alpha_boost_for_censored in fit_forecast_model and event uplift).
        # Post-policy qty boost disabled to prevent double-counting with event uplift.
        # OOS popup in GUI (estimate override) still active for manual intervention.
        oos_boost_applied = False
        # ARCHIVED CODE (for reference):
        # if oos_days_count > 0 and effective_boost > 0 and proposed_qty_raw > 0:
        #     boost_qty = int(proposed_qty_raw * effective_boost)
        #     if boost_qty == 0:
        #         boost_qty = 1  # Almeno +1 pezzo quando boost attivo
        #     oos_boost_applied = True
        #     proposed_qty_raw += boost_qty
        
        # === APPLY CONSTRAINTS (CENTRALIZED) ===
        # Apply all constraints in deterministic order: pack → MOQ → max → shelf life penalty
        constraint_result = apply_order_constraints(
            proposed_qty_raw=proposed_qty_raw,
            pack_size=pack_size,
            moq=moq,
            max_stock=max_stock,
            inventory_position=inventory_position,
            simulation_used=simulation_used,
            shelf_life_enabled=shelf_life_enabled,
            shelf_life_days=shelf_life_days,
            sku_obj=sku_obj,
            settings=settings,
            lots=lots if 'lots' in locals() else None,
            lots_total=lots_total if 'lots_total' in locals() else 0,
            ledger_stock=ledger_stock if 'ledger_stock' in locals() else current_stock.on_hand,
            discrepancy_threshold=discrepancy_threshold if 'discrepancy_threshold' in locals() else 0.0,
            daily_sales_avg=daily_sales_avg,
            lead_time=effective_lead_time,
            demand_variability=demand_variability,
        )
        
        # Extract constraint results
        proposed_qty = constraint_result["final_qty"]
        capped_by_max_stock = constraint_result["capped_by_max_stock"]
        shelf_life_penalty_applied = constraint_result["shelf_life_penalty_applied"]
        shelf_life_penalty_message = constraint_result["shelf_life_penalty_message"]
        waste_risk_forward_percent = constraint_result["waste_risk_forward_percent"]
        waste_risk_demand_adjusted_percent = constraint_result["waste_risk_demand_adjusted_percent"]
        expected_waste_qty = constraint_result["expected_waste_qty"]
        constraints_applied_list = constraint_result["constraints_applied"]
        
        # Use target_receipt_date if provided (calendar-aware), otherwise calculate from lead_time
        if target_receipt_date:
            receipt_date = target_receipt_date
        else:
            receipt_date = date.today() + timedelta(days=effective_lead_time)
        
        # === EXPLAINABILITY DRIVERS EXTRACTION ===
        # Extract standard explainability fields for operational transparency
        
        # Policy mode and forecast method
        expl_policy_mode = csl_breakdown.get("policy_mode", "")
        expl_forecast_method = forecast_method  # Already computed earlier (simple, monte_carlo)
        
        # Target CSL, sigma_horizon, reorder_point depend on policy mode
        if expl_policy_mode == "csl":
            # CSL mode: extract from csl_breakdown (populated from compute_order result)
            expl_target_csl = float(csl_breakdown.get("alpha_target", 0.0))
            expl_sigma_horizon = float(csl_breakdown.get("sigma_horizon", 0.0))
            expl_reorder_point = int(csl_breakdown.get("reorder_point", 0))
            expl_equivalent_csl_legacy = 0.0  # Not applicable in CSL mode
        else:
            # LEGACY mode: compute equivalent CSL (informational, non-binding)
            # Approximation: CSL ~ z-score mapped from safety factor
            expl_target_csl = 0.0  # Legacy doesn't use explicit alpha
            expl_sigma_horizon = 0.0  # Legacy doesn't compute sigma_horizon explicitly
            expl_reorder_point = S  # S is the legacy reorder point
            
            # Compute equivalent CSL: if safety_stock > 0, infer approximate alpha
            # Using formula: safety_stock = z * σ * sqrt(lead_time + review_period)
            # We approximate z from safety_stock / (forecast_qty * variability_factor)
            # This is informational only, for user comparison
            if safety_stock > 0 and forecast_qty > 0:
                # Approximate z-score (assumes variability ~ 0.2 of forecast, rough estimate)
                approx_variability = 0.2  # Placeholder heuristic
                approx_sigma_daily = forecast_qty / (lead_time + review_period) * approx_variability if (lead_time + review_period) > 0 else 0
                approx_sigma_horizon_val = approx_sigma_daily * ((lead_time + review_period) ** 0.5) if approx_sigma_daily > 0 else 0
                if approx_sigma_horizon_val > 0:
                    approx_z = safety_stock / approx_sigma_horizon_val
                    # Map z to alpha (cumulative normal distribution approximation)
                    # For z in [0, 3]: alpha ~ 0.5 + 0.341*z for z < 1, else use lookup
                    # Simple mapping: z=1→0.84, z=2→0.98, z=3→0.999
                    if approx_z < 0:
                        expl_equivalent_csl_legacy = 0.5
                    elif approx_z < 1:
                        expl_equivalent_csl_legacy = 0.5 + 0.341 * approx_z
                    elif approx_z < 2:
                        expl_equivalent_csl_legacy = 0.84 + 0.14 * (approx_z - 1)  # Linear interp 0.84→0.98
                    else:
                        expl_equivalent_csl_legacy = min(0.999, 0.98 + 0.019 * (approx_z - 2))
                else:
                    expl_equivalent_csl_legacy = 0.0
            else:
                expl_equivalent_csl_legacy = 0.0
        
        # Constraints applied (use centralized constraint tracking)
        expl_constraints_pack = constraint_result["constraints_pack"]
        expl_constraints_moq = constraint_result["constraints_moq"]
        expl_constraints_max = constraint_result["constraints_max"]
        
        if expl_policy_mode == "csl" and "csl_result" in locals():
            # CSL mode: combine CSL constraints + centralized post-policy constraints
            csl_constraints = csl_result.get("constraints_applied", [])
            all_constraints = csl_constraints + constraints_applied_list
            expl_constraint_details = "; ".join(all_constraints) if all_constraints else "Nessun vincolo applicato"
        else:
            # LEGACY or other mode: use centralized constraint list
            expl_constraint_details = "; ".join(constraints_applied_list) if constraints_applied_list else "Nessun vincolo applicato"
        
        # === END EXPLAINABILITY EXTRACTION ===
        
        notes = f"S={S} (forecast={forecast_qty}+safety={safety_stock}), IP={inventory_position}, Pack={pack_size}, MOQ={moq}, Max={max_stock}"
        if unfulfilled_qty > 0:
            notes += f", Unfulfilled={unfulfilled_qty}"
        if shelf_life_warning:
            notes += f" ⚠️ SHELF LIFE: Target S={S} exceeds {shelf_life_days}d capacity"
        if shelf_life_enabled and shelf_life_days > 0:
            # Show current, forward, and demand-adjusted waste risk for visibility
            if waste_risk_demand_adjusted_percent > 0:
                notes += f" | Usable={usable_qty}, Waste Risk: Now={waste_risk_percent:.1f}%, Forward={waste_risk_forward_percent:.1f}%, Adjusted={waste_risk_demand_adjusted_percent:.1f}% (exp.waste={expected_waste_qty})"
            elif waste_risk_forward_percent > 0:
                notes += f" | Usable={usable_qty}, Waste Risk Now={waste_risk_percent:.1f}%, @Receipt={waste_risk_forward_percent:.1f}%"
            else:
                notes += f" | Usable={usable_qty}, Waste Risk={waste_risk_percent:.1f}%"
        if shelf_life_penalty_applied:
            notes += f" | {shelf_life_penalty_message}"
        
        # Calculate projected stock at receipt date (only ledger events, no forecast sales)
        projected_stock_at_receipt = 0
        if receipt_date:
            # Project stock as-of receipt_date using ledger
            transactions = self.csv_layer.read_transactions()
            projected_stock_obj = StockCalculator.calculate_asof(
                sku=sku,
                asof_date=receipt_date + timedelta(days=1),  # Include events on receipt_date
                transactions=transactions,
                sales_records=None,  # No forecast sales, only ledger events
            )
            # Projected stock = on_hand at receipt_date + this order qty
            projected_stock_at_receipt = projected_stock_obj.on_hand + proposed_qty
        
        return OrderProposal(
            sku=sku,
            description=description,
            current_on_hand=current_stock.on_hand,
            current_on_order=current_stock.on_order,
            daily_sales_avg=daily_sales_avg,
            proposed_qty=proposed_qty,
            receipt_date=receipt_date,
            notes=notes,
            shelf_life_warning=shelf_life_warning,
            mc_comparison_qty=mc_comparison_qty,
            # Monte Carlo calculation details
            mc_method_used=mc_method_used,
            mc_distribution=mc_distribution_used,
            mc_n_simulations=mc_n_simulations_used,
            mc_random_seed=mc_random_seed_used,
            mc_output_stat=mc_output_stat_used,
            mc_output_percentile=mc_output_percentile_used,
            mc_horizon_mode=mc_horizon_mode_used,
            mc_horizon_days=mc_horizon_days_used,
            mc_forecast_values_summary=mc_forecast_values_summary,
            # Calculation details
            forecast_period_days=forecast_period,
            forecast_qty=forecast_qty,
            lead_time_demand=lead_time_demand,
            safety_stock=safety_stock,
            target_S=S,
            inventory_position=inventory_position,
            unfulfilled_qty=unfulfilled_qty,
            proposed_qty_before_rounding=proposed_qty_before_rounding,
            pack_size=pack_size,
            moq=moq,
            max_stock=max_stock,
            shelf_life_days=shelf_life_days,
            capped_by_max_stock=capped_by_max_stock,
            capped_by_shelf_life=capped_by_shelf_life,
            projected_stock_at_receipt=projected_stock_at_receipt,
            oos_days_count=oos_days_count,
            oos_boost_applied=oos_boost_applied,
            oos_boost_percent=effective_boost,
            simulation_used=simulation_used,
            simulation_trigger_day=simulation_trigger_day,
            simulation_notes=simulation_notes,
            # Promo adjustment (forecast enrichment)
            baseline_forecast_qty=baseline_forecast_qty,
            promo_adjusted_forecast_qty=promo_adjusted_forecast_qty,
            promo_adjustment_note=promo_adjustment_note,
            promo_uplift_factor_used=promo_uplift_factor_used,
            # Event uplift (delivery-date-based demand driver)
            event_uplift_active=event_uplift_active_val,
            event_uplift_factor=event_uplift_factor_val,
            event_u_store_day=event_u_store_day_val,
            event_beta_i=event_beta_i_val,
            event_m_i=event_m_i_val,
            event_reason=event_reason_val,
            event_delivery_date=event_delivery_date_val,
            event_quantile=event_quantile_val,
            event_fallback_level=event_fallback_level_val,
            event_beta_fallback_level=event_beta_fallback_level_val,
            event_explain_short=event_explain_short_val,
            # Shelf life info (Fase 2)
            usable_stock=usable_qty,
            unusable_stock=unusable_qty,
            waste_risk_percent=waste_risk_percent,
            waste_risk_forward_percent=waste_risk_forward_percent,
            waste_risk_demand_adjusted_percent=waste_risk_demand_adjusted_percent,
            expected_waste_qty=expected_waste_qty,
            shelf_life_penalty_applied=shelf_life_penalty_applied,
            shelf_life_penalty_message=shelf_life_penalty_message,
            # Promo prebuild (anticipatory ordering)
            promo_prebuild_enabled=promo_prebuild_enabled,
            promo_start_date=promo_start_date_val,
            target_open_qty=target_open_qty,
            projected_stock_on_promo_start=projected_stock_on_promo_start,
            prebuild_delta_qty=prebuild_delta_qty,
            prebuild_qty=prebuild_qty,
            prebuild_coverage_days=prebuild_coverage_days_val,
            prebuild_distribution_note=prebuild_distribution_note,
            # Post-promo guardrail (anti-overstock)
            post_promo_guardrail_applied=post_promo_guardrail_applied,
            post_promo_window_days=post_promo_window_days_val,
            post_promo_factor_used=post_promo_factor_used_val,
            post_promo_cap_applied=post_promo_cap_applied_val,
            post_promo_dip_factor=post_promo_dip_factor_val,
            post_promo_alert=post_promo_alert_val,
            # Cannibalization (downlift anti-sostituzione)
            cannibalization_applied=cannibalization_applied_val,
            cannibalization_driver_sku=cannibalization_driver_sku_val,
            cannibalization_downlift_factor=cannibalization_downlift_factor_val,
            cannibalization_confidence=cannibalization_confidence_val,
            cannibalization_note=cannibalization_note_val,
            # Explainability drivers (standard transparency)
            target_csl=expl_target_csl,
            sigma_horizon=expl_sigma_horizon,
            reorder_point=expl_reorder_point,
            forecast_method=expl_forecast_method,
            policy_mode=expl_policy_mode,
            equivalent_csl_legacy=expl_equivalent_csl_legacy,
            constraints_applied_pack=expl_constraints_pack,
            constraints_applied_moq=expl_constraints_moq,
            constraints_applied_max=expl_constraints_max,
            constraint_details=expl_constraint_details,
            # CSL policy breakdown
            csl_policy_mode=str(csl_breakdown.get("policy_mode", "")),
            csl_alpha_target=float(csl_breakdown.get("alpha_target", 0.0)),
            csl_alpha_eff=float(csl_breakdown.get("alpha_eff", 0.0)),
            csl_reorder_point=float(csl_breakdown.get("reorder_point", 0.0)),
            csl_forecast_demand=float(csl_breakdown.get("forecast_demand", 0.0)),
            csl_sigma_horizon=float(csl_breakdown.get("sigma_horizon", 0.0)),
            csl_z_score=float(csl_breakdown.get("z_score", 0.0)),
            csl_lane=str(csl_breakdown.get("lane", "")),
            csl_n_censored=int(csl_breakdown.get("n_censored", 0)),
        )
    
    def confirm_order(
        self,
        proposals: List[OrderProposal],
        confirmed_qtys: Optional[List[int]] = None,
    ) -> Tuple[List[OrderConfirmation], List[Transaction]]:
        """
        Confirm order(s) and generate ORDER events.
        
        Args:
            proposals: List of order proposals
            confirmed_qtys: Confirmed quantities (if None, use proposal qty)
        
        Returns:
            (order_confirmations, transactions_to_write)
        """
        confirmed_qtys = confirmed_qtys or [p.proposed_qty for p in proposals]
        
        if len(proposals) != len(confirmed_qtys):
            raise ValueError("Number of proposals and confirmed quantities must match")
        
        today = date.today()
        order_id_base = today.isoformat().replace("-", "")
        
        confirmations = []
        transactions = []
        
        for idx, (proposal, qty) in enumerate(zip(proposals, confirmed_qtys)):
            if qty <= 0:
                continue
            
            order_id = f"{order_id_base}_{idx:03d}"
            
            confirmation = OrderConfirmation(
                order_id=order_id,
                date=today,
                sku=proposal.sku,
                qty_ordered=qty,
                receipt_date=proposal.receipt_date or today + timedelta(days=self.lead_time_days),
                status="PENDING",
            )
            confirmations.append(confirmation)
            
            # Create ORDER event in ledger
            txn = Transaction(
                date=today,
                sku=proposal.sku,
                event=EventType.ORDER,
                qty=qty,
                receipt_date=confirmation.receipt_date,
                note=f"Order {order_id}",
            )
            transactions.append(txn)
        
        # Write to ledger and logs
        if transactions:
            self.csv_layer.write_transactions_batch(transactions)
        
        for idx, confirmation in enumerate(confirmations):
            # Get corresponding proposal for prebuild fields
            proposal = proposals[idx] if idx < len(proposals) else None
            
            # Extract prebuild fields from proposal (default to empty if not present)
            prebuild_enabled = proposal.promo_prebuild_enabled if proposal else False
            prebuild_start = proposal.promo_start_date.isoformat() if (proposal and proposal.promo_start_date) else None
            prebuild_target = proposal.target_open_qty if proposal else 0
            prebuild_projected = proposal.projected_stock_on_promo_start if proposal else 0
            prebuild_delta = proposal.prebuild_delta_qty if proposal else 0
            prebuild_qty_val = proposal.prebuild_qty if proposal else 0
            prebuild_coverage = proposal.prebuild_coverage_days if proposal else 0
            prebuild_note = proposal.prebuild_distribution_note if proposal else ""
            
            # Extract event uplift fields from proposal (default to empty if not present)
            event_active = proposal.event_uplift_active if proposal else False
            event_delivery = proposal.event_delivery_date.isoformat() if (proposal and proposal.event_delivery_date) else None
            event_reason = proposal.event_reason if proposal else ""
            event_u = proposal.event_u_store_day if proposal else 1.0
            event_quantile = proposal.event_quantile if proposal else 0.0
            event_fallback = proposal.event_fallback_level if proposal else ""
            event_beta = proposal.event_beta_i if proposal else 1.0
            event_beta_fallback = proposal.event_beta_fallback_level if proposal else ""
            event_m = proposal.event_m_i if proposal else 1.0
            event_explain = proposal.event_explain_short if proposal else ""
            
            self.csv_layer.write_order_log(
                order_id=confirmation.order_id,
                date_str=confirmation.date.isoformat(),
                sku=confirmation.sku,
                qty=confirmation.qty_ordered,
                status=confirmation.status,
                receipt_date=confirmation.receipt_date.isoformat() if confirmation.receipt_date else None,
                promo_prebuild_enabled=prebuild_enabled,
                promo_start_date=prebuild_start,
                target_open_qty=prebuild_target,
                projected_stock_on_promo_start=prebuild_projected,
                prebuild_delta_qty=prebuild_delta,
                prebuild_qty=prebuild_qty_val,
                prebuild_coverage_days=prebuild_coverage,
                prebuild_distribution_note=prebuild_note,
                event_uplift_active=event_active,
                event_delivery_date=event_delivery,
                event_reason=event_reason,
                event_u_store_day=event_u,
                event_quantile=event_quantile,
                event_fallback_level=event_fallback,
                event_beta_i=event_beta,
                event_beta_fallback_level=event_beta_fallback,
                event_m_i=event_m,
                event_explain_short=event_explain,
            )
        
        return confirmations, transactions


def apply_order_constraints(
    proposed_qty_raw: int,
    pack_size: int,
    moq: int,
    max_stock: int,
    inventory_position: int,
    simulation_used: bool = False,
    shelf_life_enabled: bool = False,
    shelf_life_days: int = 0,
    sku_obj: Optional[Any] = None,
    settings: Optional[Dict[str, Any]] = None,
    lots: Optional[List[Any]] = None,
    lots_total: int = 0,
    ledger_stock: int = 0,
    discrepancy_threshold: float = 0.0,
    daily_sales_avg: float = 0.0,
    lead_time: int = 0,
    demand_variability: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Apply all order constraints in deterministic order: pack → MOQ → max → shelf life penalty.
    
    This centralizes constraint application logic to ensure consistency and traceability.
    
    Constraint Application Order:
    1. Pack size rounding (round up to nearest pack multiple)
    2. MOQ rounding (round up to nearest MOQ multiple)
    3. Max stock cap (with re-application of pack/MOQ after capping)
    4. Shelf life penalty (if waste risk exceeds threshold)
    
    Args:
        proposed_qty_raw: Raw proposed quantity before constraints
        pack_size: Package size (units per pack)
        moq: Minimum order quantity
        max_stock: Maximum stock level
        inventory_position: Current inventory position (on_hand + on_order)
        simulation_used: Whether simulation was used (simulation pre-applies pack/MOQ)
        shelf_life_enabled: Whether shelf life policy is enabled
        shelf_life_days: SKU shelf life in days
        sku_obj: SKU object (for shelf life params)
        settings: Settings dict (for shelf life params)
        lots: List of Lot objects (for forward waste risk)
        lots_total: Total quantity in lots
        ledger_stock: Ledger on_hand stock
        discrepancy_threshold: Threshold for lot/ledger discrepancy detection
        daily_sales_avg: Daily sales average (for demand-adjusted waste risk)
        lead_time: Lead time in days (for receipt date calculation)
        demand_variability: DemandVariability enum (for category overrides)
    
    Returns:
        Dict with keys:
            - "final_qty": int - Final quantity after all constraints
            - "capped_by_max_stock": bool - Whether max stock cap was applied
            - "shelf_life_penalty_applied": bool - Whether shelf life penalty was applied
            - "shelf_life_penalty_message": str - Penalty message
            - "waste_risk_forward_percent": float - Forward-looking waste risk %
            - "waste_risk_demand_adjusted_percent": float - Demand-adjusted waste risk %
            - "expected_waste_qty": int - Expected waste quantity
            - "constraints_applied": List[str] - List of constraint descriptions
            - "constraints_pack": bool - Pack constraint applied
            - "constraints_moq": bool - MOQ constraint applied
            - "constraints_max": bool - Max constraint applied
    """
    proposed_qty = proposed_qty_raw
    constraints_applied = []
    constraints_pack = False
    constraints_moq = False
    constraints_max = False
    capped_by_max_stock = False
    
    # === STEP 1: PACK SIZE ROUNDING ===
    # Skip if simulation already applied it
    if not simulation_used:
        if proposed_qty_raw > 0 and pack_size > 1:
            proposed_qty_after_pack = ((proposed_qty_raw + pack_size - 1) // pack_size) * pack_size
            if proposed_qty_after_pack != proposed_qty_raw:
                constraints_pack = True
                constraints_applied.append(
                    f"pack_size: {proposed_qty_raw} → {proposed_qty_after_pack} "
                    f"(rounded up to {pack_size} units/pack)"
                )
            proposed_qty = proposed_qty_after_pack
        else:
            proposed_qty = proposed_qty_raw
        
        # === STEP 2: MOQ ROUNDING ===
        if proposed_qty > 0 and moq > 1:
            proposed_qty_after_moq = ((proposed_qty + moq - 1) // moq) * moq
            if proposed_qty_after_moq != proposed_qty:
                constraints_moq = True
                constraints_applied.append(
                    f"moq: {proposed_qty} → {proposed_qty_after_moq} "
                    f"(rounded up to MOQ={moq})"
                )
            proposed_qty = proposed_qty_after_moq
    else:
        # Simulation already rounded
        proposed_qty = proposed_qty_raw
    
    # === STEP 3: MAX STOCK CAP ===
    if inventory_position + proposed_qty > max_stock:
        capped_by_max_stock = True
        constraints_max = True
        proposed_qty_before_cap = proposed_qty
        proposed_qty = max(0, max_stock - inventory_position)
        
        # Re-apply pack_size and MOQ constraints after capping
        if proposed_qty > 0 and pack_size > 1:
            proposed_qty = (proposed_qty // pack_size) * pack_size  # Round down
        if proposed_qty > 0 and moq > 1 and proposed_qty < moq:
            proposed_qty = 0  # Can't meet MOQ without exceeding max_stock
        
        constraints_applied.append(
            f"max_stock: {proposed_qty_before_cap} → {proposed_qty} "
            f"(capped by max_stock={max_stock}, IP={inventory_position})"
        )
    
    # === STEP 4: SHELF LIFE PENALTY ===
    shelf_life_penalty_applied = False
    shelf_life_penalty_message = ""
    waste_risk_forward_percent = 0.0
    waste_risk_demand_adjusted_percent = 0.0
    expected_waste_qty = 0
    
    if shelf_life_enabled and shelf_life_days > 0 and proposed_qty > 0:
        # Get shelf life parameters with category override
        category = demand_variability.value if demand_variability else "STABLE"
        category_overrides = settings.get("shelf_life_policy", {}).get("category_overrides", {}).get("value", {}) if settings else {}
        category_params = category_overrides.get(category, {})
        
        waste_penalty_mode = sku_obj.waste_penalty_mode if (sku_obj and sku_obj.waste_penalty_mode) else \
                             settings.get("shelf_life_policy", {}).get("waste_penalty_mode", {}).get("value", "soft") if settings else "soft"
        
        waste_penalty_factor = sku_obj.waste_penalty_factor if (sku_obj and sku_obj.waste_penalty_factor > 0) else \
                               category_params.get("waste_penalty_factor",
                               settings.get("shelf_life_policy", {}).get("waste_penalty_factor", {}).get("value", 0.5) if settings else 0.5)
        
        waste_risk_threshold = sku_obj.waste_risk_threshold if (sku_obj and sku_obj.waste_risk_threshold > 0) else \
                               category_params.get("waste_risk_threshold",
                               settings.get("shelf_life_policy", {}).get("waste_risk_threshold", {}).get("value", 15.0) if settings else 15.0)
        
        # Calculate FORWARD waste risk (at receipt_date, including incoming order)
        receipt_date_calc = date.today() + timedelta(days=lead_time)
        
        # Get shelf life parameters
        min_shelf_life_calc = sku_obj.min_shelf_life_days if (sku_obj and sku_obj.min_shelf_life_days > 0) else \
                              category_params.get("min_shelf_life_days", 
                              settings.get("shelf_life_policy", {}).get("min_shelf_life_global", {}).get("value", 7) if settings else 7)
        
        waste_horizon_days_calc = settings.get("shelf_life_policy", {}).get("waste_horizon_days", {}).get("value", 14) if settings else 14
        
        # Calculate forward-looking waste risk (traditional + demand-adjusted)
        if not lots or lots_total < ledger_stock - discrepancy_threshold:
            # Fallback case: no forward calculation possible
            waste_risk_forward_percent = 0.0
            waste_risk_demand_adjusted_percent = 0.0
            expected_waste_qty = 0
        else:
            # Traditional forward risk (for comparison/notes)
            waste_risk_forward_percent, _, _ = ShelfLifeCalculator.calculate_forward_waste_risk(
                lots=lots,
                current_date=date.today(),
                receipt_date=receipt_date_calc,
                proposed_qty=proposed_qty,
                sku_shelf_life_days=shelf_life_days,
                min_shelf_life_days=min_shelf_life_calc,
                waste_horizon_days=waste_horizon_days_calc
            )
            
            # Demand-adjusted forward risk (used for penalty decision)
            forecast_daily_demand = daily_sales_avg if daily_sales_avg > 0 else 0.0
            (
                waste_risk_demand_adjusted_percent,
                _,
                _,
                expected_waste_qty
            ) = ShelfLifeCalculator.calculate_forward_waste_risk_demand_adjusted(
                lots=lots,
                receipt_date=receipt_date_calc,
                proposed_qty=proposed_qty,
                sku_shelf_life_days=shelf_life_days,
                min_shelf_life_days=min_shelf_life_calc,
                waste_horizon_days=waste_horizon_days_calc,
                forecast_daily_demand=forecast_daily_demand
            )
        
        # Use DEMAND-ADJUSTED waste risk for penalty decision (more realistic)
        if waste_risk_demand_adjusted_percent >= waste_risk_threshold:
            original_proposed = proposed_qty
            proposed_qty, penalty_msg = ShelfLifeCalculator.apply_shelf_life_penalty(
                proposed_qty=proposed_qty,
                waste_risk_percent=waste_risk_demand_adjusted_percent,
                waste_risk_threshold=waste_risk_threshold,
                penalty_mode=waste_penalty_mode,
                penalty_factor=waste_penalty_factor
            )
            
            if penalty_msg:
                shelf_life_penalty_applied = True
                shelf_life_penalty_message = penalty_msg
                constraints_applied.append(
                    f"shelf_life_penalty: {original_proposed} → {proposed_qty} "
                    f"({penalty_msg})"
                )
    
    return {
        "final_qty": proposed_qty,
        "capped_by_max_stock": capped_by_max_stock,
        "shelf_life_penalty_applied": shelf_life_penalty_applied,
        "shelf_life_penalty_message": shelf_life_penalty_message,
        "waste_risk_forward_percent": waste_risk_forward_percent,
        "waste_risk_demand_adjusted_percent": waste_risk_demand_adjusted_percent,
        "expected_waste_qty": expected_waste_qty,
        "constraints_applied": constraints_applied,
        "constraints_pack": constraints_pack,
        "constraints_moq": constraints_moq,
        "constraints_max": constraints_max,
    }


def calculate_daily_sales_average(
    sales_records,
    sku: str,
    days_lookback: int = 30,
    transactions=None,
    asof_date: Optional[date] = None,
    oos_detection_mode: str = "strict",
    return_details: bool = False,
    sku_transactions=None,  # pre-filtered transactions for this SKU (perf fast-path)
    sku_sales=None,         # pre-filtered sales records for this SKU (perf fast-path)
) -> tuple:
    """
    Calculate average daily sales for a SKU using calendar-based approach.
    
    NEW BEHAVIOR (2026-02-04):
    - Uses real calendar days (30 days = 30 data points, including zeros)
    - Excludes days when SKU was out-of-stock based on detection mode:
      * "strict": on_hand == 0 (ignora on_order, più conservativo)
      * "relaxed": on_hand + on_order == 0 (comportamento precedente)
    - More accurate forecast for irregular sales patterns
    - OOS_ESTIMATE_OVERRIDE markers: Days with WASTE(qty=0) + note "OOS_ESTIMATE_OVERRIDE:{date}"
      are excluded from OOS detection (allows manual lost sales entry for OOS days)
    
    NEW BEHAVIOR (2026-02-09):
    - Excludes periods when SKU was OUT OF ASSORTMENT from average calculation
    - Uses ASSORTMENT_OUT/ASSORTMENT_IN events in ledger to identify excluded periods
    - Prevents "contamination" of forecast when SKU sells residual stock while discontinued
    
    NEW BEHAVIOR (2026-02-14):
    - Added optional return_details parameter for KPI analysis
    - When True, returns detailed OOS days and assortment exclusion lists
    
    Args:
        sales_records: List of SalesRecord objects
        sku: SKU identifier
        days_lookback: Number of calendar days to look back (default: 30)
        transactions: List of Transaction objects (for OOS detection + override markers + assortment tracking)
        asof_date: As-of date for calculation (defaults to today)
        oos_detection_mode: "strict" (on_hand==0) or "relaxed" (on_hand+on_order==0)
        return_details: If True, return detailed breakdown (default: False for backward compatibility)
    
    Returns:
        If return_details=False (default):
            Tuple (avg_daily_sales, oos_days_count):
            - avg_daily_sales: Average daily sales qty (excluding OOS days + out-of-assortment periods)
            - oos_days_count: Number of OOS days detected (after override exclusions)
        
        If return_details=True:
            Tuple (avg_daily_sales, oos_days_count, oos_days_list, out_of_assortment_days_list):
            - avg_daily_sales: Average daily sales qty
            - oos_days_count: Number of OOS days detected
            - oos_days_list: List of dates identified as OOS (sorted)
            - out_of_assortment_days_list: List of dates when SKU was out of assortment (sorted)
    
    Example:
        If last 30 days have 10 days with sales, 15 days zero, 5 days OOS (3 with overrides):
        avg, oos_count = calculate_daily_sales_average(...)
        # avg = sum(sales_10_days) / 27  (excludes 2 real OOS days, 3 overrides excluded)
        # oos_count = 2  (only non-override OOS days)
    """
    from ..domain.ledger import StockCalculator
    from ..domain.models import EventType
    
    if asof_date is None:
        asof_date = date.today()
    
    # Build sales map: {date: qty_sold}
    # Use pre-filtered sku_sales when provided — avoids O(|sales_records|) scan
    sku_sales_map = {}
    _source_sales = sku_sales if sku_sales is not None else (
        (s for s in sales_records if s.sku == sku) if sales_records else []
    )
    for s in _source_sales:
        sku_sales_map[s.date] = sku_sales_map.get(s.date, 0) + s.qty_sold
    
    # Generate calendar days range
    start_date = asof_date - timedelta(days=days_lookback - 1)
    calendar_days = [start_date + timedelta(days=i) for i in range(days_lookback)]
    calendar_days_set = set(calendar_days)
    
    # Resolve per-SKU transaction list once
    # Use pre-filtered sku_transactions when provided — avoids O(|transactions|) scan
    if sku_transactions is not None:
        _sku_txns = sku_transactions
    elif transactions:
        _sku_txns = [t for t in transactions if t.sku == sku]
    else:
        _sku_txns = []

    # Build map of out-of-assortment periods from ledger
    out_of_assortment_days = set()
    
    if _sku_txns:
        # Find all assortment transitions for this SKU (already filtered above)
        assortment_events = [
            txn for txn in _sku_txns
            if txn.event in (EventType.ASSORTMENT_OUT, EventType.ASSORTMENT_IN)
        ]
        
        # Sort by date
        assortment_events.sort(key=lambda t: t.date)
        
        # Build periods: assume SKU starts IN assortment unless first event is ASSORTMENT_IN
        currently_out = False
        out_start = None
        
        # If first event in history is ASSORTMENT_IN, SKU was initially OUT
        if assortment_events and assortment_events[0].event == EventType.ASSORTMENT_IN:
            currently_out = True
            out_start = start_date  # Entire lookback period starts as OUT
        
        for event in assortment_events:
            if event.event == EventType.ASSORTMENT_OUT:
                currently_out = True
                out_start = event.date
            elif event.event == EventType.ASSORTMENT_IN:
                if currently_out and out_start:
                    # Mark all days from out_start to event.date-1 as out of assortment
                    current = out_start
                    while current < event.date:
                        if current in calendar_days_set:
                            out_of_assortment_days.add(current)
                        current += timedelta(days=1)
                currently_out = False
                out_start = None
        
        # If still out at end of period
        if currently_out and out_start:
            current = out_start
            while current <= asof_date:
                if current in calendar_days_set:
                    out_of_assortment_days.add(current)
                current += timedelta(days=1)
    
    # Detect OOS days (if transactions provided)
    oos_days = set()
    oos_override_days = set()  # Days with OOS_ESTIMATE_OVERRIDE marker
    
    if _sku_txns or transactions:
        # Identify days with override markers using pre-filtered list
        for txn in _sku_txns:
            if txn.note and "OOS_ESTIMATE_OVERRIDE:" in txn.note:
                oos_override_days.add(txn.date)
        
        # Detect OOS using incremental series calculator — O(T log T + D) total
        # instead of O(D × T log T) from repeated calculate_asof calls.
        days_to_check = [d for d in calendar_days if d not in oos_override_days]
        if days_to_check:
            # Pass pre-filtered sku data; calculate_stock_series avoids full-list rescans
            stock_series = StockCalculator.calculate_stock_series(
                sku,
                days_to_check,
                transactions or [],
                sales_records,
                sku_transactions=_sku_txns,
                sku_sales=sku_sales,
            )
            for day, stock in stock_series.items():
                if oos_detection_mode == "strict":
                    if stock.on_hand == 0:
                        oos_days.add(day)
                else:  # "relaxed" or default
                    if stock.on_hand + stock.on_order == 0:
                        oos_days.add(day)
    
    # Calculate average excluding OOS days AND out-of-assortment days
    total_sales = 0
    valid_days = 0
    
    for day in calendar_days:
        if day in oos_days:
            continue  # Skip OOS days
        if day in out_of_assortment_days:
            continue  # Skip out-of-assortment days
        
        # Include day with sales qty (or zero if no sales)
        total_sales += sku_sales_map.get(day, 0)
        valid_days += 1
    
    avg_sales = total_sales / valid_days if valid_days > 0 else 0.0
    oos_days_count = len(oos_days)
    
    if return_details:
        # Return detailed breakdown for KPI analysis
        oos_days_list = sorted(list(oos_days))
        out_of_assortment_days_list = sorted(list(out_of_assortment_days))
        return (avg_sales, oos_days_count, oos_days_list, out_of_assortment_days_list)
    else:
        # Backward compatible return for existing callers
        return (avg_sales, oos_days_count)


def propose_order_for_sku(
    sku_obj: "SKU",
    history: List[Dict],
    stock: "Stock",
    pipeline: List[Dict],
    asof_date: date,
    target_receipt_date: date,
    protection_period_days: int,
    settings: Dict,
    promo_calendar: Optional[List] = None,
    event_uplift_rules: Optional[List] = None,
    sales_records: Optional[List] = None,
    transactions: Optional[List] = None,
    all_skus: Optional[List] = None,
    censored_flags: Optional[List] = None,
    expected_waste_rate: float = 0.0,
    holidays: Optional[List] = None,
) -> Tuple["OrderProposal", "OrderExplain"]:
    """
    Pure facade for a single-SKU order proposal. No I/O.

    This is the SINGLE canonical entry-point for the forecast→policy pipeline:

        build_demand_distribution()
            → apply_modifiers()
                → compute_order_v2() (CSL) | legacy_formula (legacy)
                    → apply_order_constraints()
                        → (OrderProposal, OrderExplain)

    Parameters
    ----------
    sku_obj : SKU
        Full SKU domain object.
    history : list[{"date": date, "qty_sold": float}]
        Raw sales history, oldest-first.
    stock : Stock
        Current inventory state (ledger-derived asof today).
    pipeline : list[{"receipt_date": date, "qty": int}]
        Open pipeline orders (from build_open_pipeline).
    asof_date : date
        Date of computation – MUST NOT be date.today() inside domain logic.
    target_receipt_date : date
        Expected delivery date for the order being proposed.
    protection_period_days : int
        Forecast horizon P (days from order to receipt + review).
    settings : dict
        Global settings (read_settings() output).
    promo_calendar, event_uplift_rules, sales_records, transactions, all_skus :
        Pre-loaded domain data.  Pass None to skip that modifier class.
    censored_flags : list[bool] or None
        Censoring mask (same length as history).
    expected_waste_rate : float
        Shelf-life waste adjustment passed to Monte Carlo (0–1).

    Returns
    -------
    (OrderProposal, OrderExplain)
    """
    from ..domain.contracts import DemandDistribution, InventoryPosition, OrderExplain
    from ..domain.demand_builder import build_demand_distribution
    from ..domain.modifier_builder import apply_modifiers
    from ..replenishment_policy import compute_order_v2, OrderConstraints

    sku_id = sku_obj.sku
    policy_mode = settings.get("reorder_engine", {}).get("policy_mode", {}).get("value", "legacy")
    forecast_method = (
        sku_obj.forecast_method
        if sku_obj.forecast_method
        else settings.get("reorder_engine", {}).get("forecast_method", {}).get("value", "simple")
    )

    # ------------------------------------------------------------------ #
    # SKU parameters                                                       #
    # ------------------------------------------------------------------ #
    pack_size = sku_obj.pack_size
    moq = sku_obj.moq
    max_stock = sku_obj.max_stock
    safety_stock_base = sku_obj.safety_stock
    lead_time = sku_obj.lead_time_days
    review_period = sku_obj.review_period

    # Demand variability multiplier for legacy safety stock
    from ..domain.models import DemandVariability
    demand_variability = sku_obj.demand_variability
    safety_stock = safety_stock_base
    if demand_variability == DemandVariability.HIGH:
        safety_stock = int(safety_stock_base * 1.5)
    elif demand_variability == DemandVariability.STABLE:
        safety_stock = int(safety_stock_base * 0.8)

    # ------------------------------------------------------------------ #
    # Step 1: Build DemandDistribution                                     #
    # ------------------------------------------------------------------ #
    mc_section = settings.get("monte_carlo", {})
    mc_params = {
        "distribution": mc_section.get("distribution", {}).get("value", "empirical"),
        "n_simulations": mc_section.get("n_simulations", {}).get("value", 1000),
        "random_seed": mc_section.get("random_seed", {}).get("value", 42),
        "output_stat": mc_section.get("output_stat", {}).get("value", "mean"),
        "output_percentile": mc_section.get("output_percentile", {}).get("value", 80),
    }
    # Override with SKU-specific MC params where set
    if sku_obj.mc_distribution:
        mc_params["distribution"] = sku_obj.mc_distribution
    if sku_obj.mc_n_simulations > 0:
        mc_params["n_simulations"] = sku_obj.mc_n_simulations
    if sku_obj.mc_random_seed > 0:
        mc_params["random_seed"] = sku_obj.mc_random_seed
    if sku_obj.mc_output_stat:
        mc_params["output_stat"] = sku_obj.mc_output_stat
    if sku_obj.mc_output_percentile > 0:
        mc_params["output_percentile"] = sku_obj.mc_output_percentile

    window_weeks = settings.get("reorder_engine", {}).get("sigma_window_weeks", {}).get("value", 8)

    base_demand = build_demand_distribution(
        method=forecast_method,
        history=history,
        protection_period_days=protection_period_days,
        asof_date=asof_date,
        censored_flags=censored_flags,
        window_weeks=window_weeks,
        mc_params=mc_params,
        expected_waste_rate=expected_waste_rate,
    )

    # ------------------------------------------------------------------ #
    # Step 2: Apply modifiers                                              #
    # ------------------------------------------------------------------ #
    horizon_dates = [asof_date + timedelta(days=i + 1) for i in range(protection_period_days)]

    adjusted_demand, applied_modifiers = apply_modifiers(
        base_demand=base_demand,
        sku_id=sku_id,
        sku_obj=sku_obj,
        horizon_dates=horizon_dates,
        target_receipt_date=target_receipt_date,
        asof_date=asof_date,
        settings=settings,
        all_skus=all_skus,
        promo_windows=promo_calendar,
        event_rules=event_uplift_rules,
        sales_records=sales_records,
        transactions=transactions,
        holidays=holidays,
    )

    # ------------------------------------------------------------------ #
    # Step 3: Compute policy                                               #
    # ------------------------------------------------------------------ #
    # Build InventoryPosition
    on_hand_eff = stock.on_hand   # caller may pass shelf-life-adjusted value
    on_order_eff = float(stock.on_order)
    unfulfilled = float(getattr(stock, "unfulfilled_qty", 0.0))

    pos = InventoryPosition(
        on_hand=on_hand_eff,
        on_order=on_order_eff,
        unfulfilled=unfulfilled,
        pipeline=pipeline,
    )

    constraints_applied: List[str] = []
    order_raw: float = 0.0
    reorder_point: float = 0.0
    alpha_target: Optional[float] = None
    z_score: Optional[float] = None
    csl_result: Dict = {}
    proposed_qty_raw: int = 0
    csl_breakdown: Dict = {}

    if policy_mode == "csl":
        from ..analytics.target_resolver import TargetServiceLevelResolver
        from ..domain.calendar import Lane, next_receipt_date as _nrd

        resolver = TargetServiceLevelResolver(settings)
        alpha_target = resolver.get_target_csl(sku_obj)

        lane = _deduce_lane_for_facade(target_receipt_date, protection_period_days, asof_date)

        csl_result = compute_order_v2(
            demand=adjusted_demand,
            position=pos,
            alpha=alpha_target,
            constraints=OrderConstraints(pack_size=pack_size, moq=moq, max_stock=max_stock),
            order_date=asof_date,
            lane=lane,
        )

        proposed_qty_raw = int(csl_result.get("order_final", 0))
        reorder_point = csl_result.get("reorder_point", 0.0)
        order_raw = csl_result.get("order_raw", 0.0)
        z_score = csl_result.get("z_score")
        constraints_applied = csl_result.get("constraints_applied", [])
        csl_breakdown = csl_result

    else:
        # Legacy formula: S = forecast_qty + safety_stock;  Q = max(0, S - IP)
        forecast_qty = adjusted_demand.mu_P  # mu_P already includes modifiers
        S = forecast_qty + safety_stock
        reorder_point = S
        order_raw = max(0.0, S - pos.inventory_position)
        proposed_qty_raw = int(order_raw)
        csl_breakdown = {"policy_mode": "legacy"}

    # ------------------------------------------------------------------ #
    # Step 4: Apply constraints                                            #
    # ------------------------------------------------------------------ #
    # Use centralized apply_order_constraints for consistency with generate_proposal()
    constraint_result = apply_order_constraints(
        proposed_qty_raw=proposed_qty_raw,
        pack_size=pack_size,
        moq=moq,
        max_stock=max_stock,
        inventory_position=int(pos.inventory_position),
        demand_variability=demand_variability,
        settings=settings,
        sku_obj=sku_obj,
    )
    proposed_qty = constraint_result["final_qty"]
    constraints_applied = constraints_applied + constraint_result["constraints_applied"]

    # ------------------------------------------------------------------ #
    # Step 5: Build OrderExplain                                           #
    # ------------------------------------------------------------------ #
    reorder_point_method = csl_breakdown.get("reorder_point_method", "legacy") if policy_mode == "csl" else "legacy"
    quantile_used = csl_breakdown.get("quantile_used") if policy_mode == "csl" else None
    
    explain = OrderExplain(
        sku=sku_id,
        asof_date=asof_date,
        demand=adjusted_demand,
        position=pos,
        modifiers=list(applied_modifiers),
        policy_mode=policy_mode,
        alpha_target=alpha_target,
        z_score=z_score,
        reorder_point=reorder_point,
        reorder_point_method=reorder_point_method,
        quantile_used=quantile_used,
        order_raw=order_raw,
        constraints_applied=constraints_applied,
        order_final=proposed_qty,
        safety_stock=safety_stock if policy_mode != "csl" else 0,
        equivalent_csl_legacy=_equiv_csl_legacy(safety_stock, adjusted_demand.mu_P, protection_period_days)
        if policy_mode != "csl" else 0.0,
    )

    # ------------------------------------------------------------------ #
    # Step 6: Build lightweight OrderProposal for backward compatibility   #
    # ------------------------------------------------------------------ #
    daily_sales_avg = adjusted_demand.mu_P / max(protection_period_days, 1)
    receipt_date = target_receipt_date

    # Extract modifier fields for OrderProposal
    promo_mod = next((m for m in applied_modifiers if m.modifier_type == "promo" and m.scope != "qty_correction"), None)
    event_mod = next((m for m in applied_modifiers if m.modifier_type == "event"), None)
    cannib_mod = next((m for m in applied_modifiers if m.modifier_type == "cannibalization"), None)

    proposal = OrderProposal(
        sku=sku_id,
        description=sku_obj.description,
        current_on_hand=stock.on_hand,
        current_on_order=stock.on_order,
        daily_sales_avg=daily_sales_avg,
        proposed_qty=proposed_qty,
        receipt_date=receipt_date,
        notes=f"propose_order_for_sku: mu_P={adjusted_demand.mu_P:.1f}, sigma_P={adjusted_demand.sigma_P:.1f}, IP={pos.inventory_position:.1f}",
        # Forecast details
        forecast_period_days=protection_period_days,
        forecast_qty=int(adjusted_demand.mu_P),
        safety_stock=safety_stock,
        target_S=int(reorder_point),
        inventory_position=int(pos.inventory_position),
        unfulfilled_qty=int(unfulfilled),
        proposed_qty_before_rounding=proposed_qty_raw,
        pack_size=pack_size,
        moq=moq,
        max_stock=max_stock,
        forecast_method=forecast_method,
        policy_mode=policy_mode,
        baseline_forecast_qty=int(base_demand.mu_P),
        promo_adjusted_forecast_qty=int(adjusted_demand.mu_P),
        promo_adjustment_note=promo_mod.note if promo_mod else "",
        promo_uplift_factor_used=promo_mod.multiplier if promo_mod else 1.0,
        event_uplift_active=event_mod is not None,
        event_uplift_factor=event_mod.multiplier if event_mod else 1.0,
        event_m_i=event_mod.multiplier if event_mod else 1.0,
        cannibalization_applied=cannib_mod is not None,
        cannibalization_driver_sku=cannib_mod.source_sku or "" if cannib_mod else "",
        cannibalization_downlift_factor=cannib_mod.multiplier if cannib_mod else 1.0,
        cannibalization_confidence=cannib_mod.confidence if cannib_mod else "",
        # CSL breakdown
        target_csl=alpha_target or 0.0,
        sigma_horizon=adjusted_demand.sigma_P,
        reorder_point=int(reorder_point),
        csl_policy_mode=policy_mode,
        csl_alpha_target=alpha_target or 0.0,
        csl_alpha_eff=csl_result.get("alpha_eff", alpha_target or 0.0),
        csl_reorder_point=reorder_point,
        csl_forecast_demand=adjusted_demand.mu_P,
        csl_sigma_horizon=adjusted_demand.sigma_P,
        csl_z_score=z_score or 0.0,
        csl_lane=csl_result.get("lane", ""),
        csl_n_censored=adjusted_demand.n_censored,
        constraints_applied_pack=constraint_result["constraints_pack"],
        constraints_applied_moq=constraint_result["constraints_moq"],
        constraints_applied_max=constraint_result["constraints_max"],
        constraint_details="; ".join(constraints_applied) if constraints_applied else "",
        capped_by_max_stock=constraint_result["capped_by_max_stock"],
    )

    return proposal, explain


def explain_order(
    sku_id: str,
    asof_date: date,
    history: List[Dict],
    stock: "Stock",
    pipeline: List[Dict],
    target_receipt_date: date,
    protection_period_days: int,
    settings: Dict,
    sku_obj: Optional["SKU"] = None,
    promo_calendar: Optional[List] = None,
    event_uplift_rules: Optional[List] = None,
    sales_records: Optional[List] = None,
    transactions: Optional[List] = None,
    all_skus: Optional[List] = None,
    censored_flags: Optional[List] = None,
) -> Dict:
    """
    Return a JSON-serialisable dict with the complete order decision chain.

    This is a thin wrapper around ``propose_order_for_sku()`` that
    immediately serialises the ``OrderExplain`` to a flat dict.

    The dict is suitable for:
    - Logging / debugging
    - CSV export (order_explain row)
    - GUI display
    - Regression / golden tests

    See ``OrderExplain.to_dict()`` for the full column list.
    """
    if sku_obj is None:
        raise ValueError("sku_obj is required for explain_order()")

    _, explain = propose_order_for_sku(
        sku_obj=sku_obj,
        history=history,
        stock=stock,
        pipeline=pipeline,
        asof_date=asof_date,
        target_receipt_date=target_receipt_date,
        protection_period_days=protection_period_days,
        settings=settings,
        promo_calendar=promo_calendar,
        event_uplift_rules=event_uplift_rules,
        sales_records=sales_records,
        transactions=transactions,
        all_skus=all_skus,
        censored_flags=censored_flags,
    )
    return explain.to_dict()


# ---------------------------------------------------------------------------
# Private helpers for facade
# ---------------------------------------------------------------------------

def _deduce_lane_for_facade(
    target_receipt_date: Optional[date],
    protection_period_days: Optional[int],
    order_date: date,
) -> "Lane":
    """Re-use existing lane deduction logic (standalone, no self)."""
    from ..domain.calendar import Lane, next_receipt_date, calculate_protection_period_days

    if not target_receipt_date or not protection_period_days:
        return Lane.STANDARD

    for lane_candidate in [Lane.SATURDAY, Lane.MONDAY, Lane.STANDARD]:
        try:
            exp_rd = next_receipt_date(order_date, lane_candidate)
            exp_pp = calculate_protection_period_days(order_date, lane_candidate)
            if exp_rd == target_receipt_date and exp_pp == protection_period_days:
                return lane_candidate
        except ValueError:
            continue
    return Lane.STANDARD


def _equiv_csl_legacy(safety_stock: int, forecast_qty: float, period: int) -> float:
    """Approximate equivalent CSL for legacy mode (informational, non-binding)."""
    if safety_stock <= 0 or forecast_qty <= 0 or period <= 0:
        return 0.0
    approx_variability = 0.2
    daily_demand = forecast_qty / period
    approx_sigma_daily = daily_demand * approx_variability
    approx_sigma_horizon = approx_sigma_daily * (period ** 0.5)
    if approx_sigma_horizon <= 0:
        return 0.0
    approx_z = safety_stock / approx_sigma_horizon
    if approx_z < 0:
        return 0.5
    elif approx_z < 1:
        return 0.5 + 0.341 * approx_z
    elif approx_z < 2:
        return 0.84 + 0.14 * (approx_z - 1)
    else:
        return min(0.999, 0.98 + 0.019 * (approx_z - 2))


def calculate_prebuild_target(
    sku: str,
    promo_start_date: date,
    coverage_days: int,
    safety_component_mode: str,
    safety_component_value: float,
    promo_windows: List,
    sales_records: List,
    transactions: List,
    all_skus: List,
    csv_layer,
    settings: dict,
) -> tuple:
    """
    Calculate target opening stock needed at promo start for prebuild logic.
    
    Formula:
        target_open = sum(adjusted_forecast[start_date : start_date + coverage_days]) + safety_component
    
    Where:
        - adjusted_forecast uses promo_adjusted_forecast with uplift during promo window
        - safety_component is either multiplier (target × value) or absolute (+ value)
    
    Args:
        sku: SKU identifier
        promo_start_date: Date when promo starts (need opening stock BY this date)
        coverage_days: Days of promo to cover (0 = use lead_time from settings)
        safety_component_mode: 'multiplier' or 'absolute'
        safety_component_value: Safety component value (0.2 = 20% for multiplier, or absolute qty)
        promo_windows: All promo windows
        sales_records: All sales records
        transactions: All transactions
        all_skus: All SKU objects
        csv_layer: CSV layer for data access
        settings: Settings dictionary
    
    Returns:
        (target_open_qty, coverage_days_used, adjusted_forecast_total)
    """
    from ..forecast import promo_adjusted_forecast
    
    # Determine coverage_days: if 0, use lead_time from settings
    if coverage_days == 0:
        lead_time = settings.get("reorder_engine", {}).get("lead_time_days", {}).get("value", 7)
        coverage_days_used = lead_time
    else:
        coverage_days_used = coverage_days
    
    # Build horizon dates for coverage period: [promo_start, promo_start+1, ..., promo_start+coverage-1]
    horizon_dates = [promo_start_date + timedelta(days=i) for i in range(coverage_days_used)]
    
    # Call promo_adjusted_forecast to get demand forecast during promo window
    try:
        promo_result = promo_adjusted_forecast(
            sku_id=sku,
            horizon_dates=horizon_dates,
            sales_records=sales_records,
            transactions=transactions,
            promo_windows=promo_windows,
            all_skus=all_skus,
            csv_layer=csv_layer,
            store_id=None,  # Global promo only
            settings=settings,
        )
        
        # Sum adjusted forecast over coverage period
        adjusted_forecast_total = int(sum(promo_result["adjusted_forecast"].values()))
    except Exception as e:
        logging.warning(f"Promo adjusted forecast failed for prebuild target calculation (SKU {sku}): {e}. Using 0.")
        adjusted_forecast_total = 0
    
    # Calculate safety component
    if safety_component_mode == "absolute":
        safety_component = int(safety_component_value)
    else:  # "multiplier"
        safety_component = int(adjusted_forecast_total * safety_component_value)
    
    # Target opening stock = forecast + safety
    target_open_qty = adjusted_forecast_total + safety_component
    
    return (target_open_qty, coverage_days_used, adjusted_forecast_total)

