"""
Order workflow: proposal generation and confirmation.
"""
from datetime import date, timedelta
from typing import List, Tuple, Optional
import logging

from ..domain.models import Stock, OrderProposal, OrderConfirmation, Transaction, EventType, SKU
from ..persistence.csv_layer import CSVLayer
from ..domain.ledger import StockCalculator, ShelfLifeCalculator


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
    ) -> OrderProposal:
        """
        Generate order proposal based on stock and sales history.
        
        NEW FORMULA (2026-01-29):
        S = forecast × (lead_time + review_period) + safety_stock
        proposed = max(0, S − (on_hand + on_order))
        Then: apply pack_size rounding → MOQ rounding → cap at max_stock
        
        Args:
            sku: SKU identifier
            description: SKU description
            current_stock: Current stock state (on_hand, on_order)
            daily_sales_avg: Average daily sales (from historical data)
            min_stock: Minimum stock threshold (global default, overridden by SKU reorder_point)
            days_cover: Days of sales to cover (DEPRECATED, now uses lead_time + review_period)
            sku_obj: SKU object (for pack_size, MOQ, lead_time, review_period, safety_stock, max_stock)
        
        Returns:
            OrderProposal with suggested quantity (adjusted for pack_size, MOQ, and max_stock cap)
        """
        # Use SKU-specific parameters if available
        pack_size = sku_obj.pack_size if sku_obj else 1
        moq = sku_obj.moq if sku_obj else 1
        lead_time = sku_obj.lead_time_days if sku_obj else self.lead_time_days
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
        
        # NEW FORMULA: S = forecast × (lead_time + review_period) + safety_stock
        forecast_period = lead_time + review_period
        
        # === FORECAST METHOD SELECTION (SIMPLE vs MONTE CARLO) ===
        # Read global settings
        settings = self.csv_layer.read_settings()
        global_forecast_method = settings.get("reorder_engine", {}).get("forecast_method", {}).get("value", "simple")
        mc_show_comparison = settings.get("monte_carlo", {}).get("show_comparison", {}).get("value", False)
        
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
            
            usable_result = ShelfLifeCalculator.calculate_usable_stock(
                lots=lots,
                check_date=date.today(),
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
            
            if not lots or lots_total < ledger_stock - discrepancy_threshold:
                # Lots missing or significantly lower than ledger → fallback to ledger
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
                lead_time_demand = int(sum(mc_forecast_values[:lead_time])) if len(mc_forecast_values) >= lead_time else forecast_qty
            except Exception as e:
                # Fallback to simple forecast if MC fails
                logging.warning(f"Monte Carlo forecast failed for SKU {sku}: {e}. Falling back to simple forecast.")
                forecast_qty = int(daily_sales_avg * forecast_period)
                lead_time_demand = int(daily_sales_avg * lead_time)
                mc_method_used = ""  # Reset since MC failed
        
        else:
            # === SIMPLE FORECAST (level × period) ===
            forecast_qty = int(daily_sales_avg * forecast_period)
            lead_time_demand = int(daily_sales_avg * lead_time)
        
        S = forecast_qty + safety_stock
        
        # Check shelf life warning (if shelf_life_days > 0)
        shelf_life_warning = False
        capped_by_shelf_life = False
        if shelf_life_days > 0 and daily_sales_avg > 0:
            shelf_life_capacity = int(daily_sales_avg * shelf_life_days)
            if S > shelf_life_capacity:
                shelf_life_warning = True
        
        # NEW IP formula: IP = usable_qty + on_order - unfulfilled_qty (usa usable stock se shelf life enabled)
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
        
        # Apply OOS boost if requested (increase proposed qty)
        # Regola "almeno +1": se boost_qty calcolato è 0 ma boost è attivo, applica +1
        oos_boost_applied = False
        if oos_days_count > 0 and effective_boost > 0 and proposed_qty_raw > 0:
            boost_qty = int(proposed_qty_raw * effective_boost)
            if boost_qty == 0:
                boost_qty = 1  # Almeno +1 pezzo quando boost attivo
            oos_boost_applied = True
            proposed_qty_raw += boost_qty
        
        # Apply pack_size rounding (round up to nearest pack_size multiple)
        # Skip if simulation already applied it
        if not simulation_used:
            if proposed_qty_raw > 0 and pack_size > 1:
                proposed_qty = ((proposed_qty_raw + pack_size - 1) // pack_size) * pack_size
            else:
                proposed_qty = proposed_qty_raw
            
            # Apply MOQ rounding (round up to nearest MOQ multiple)
            if proposed_qty > 0 and moq > 1:
                proposed_qty = ((proposed_qty + moq - 1) // moq) * moq
        else:
            # Simulation already rounded
            proposed_qty = proposed_qty_raw
        
        # Cap at max_stock (use IP for capping logic)
        capped_by_max_stock = False
        if inventory_position + proposed_qty > max_stock:
            capped_by_max_stock = True
            proposed_qty = max(0, max_stock - inventory_position)
            # Re-apply pack_size and MOQ constraints after capping
            if proposed_qty > 0 and pack_size > 1:
                proposed_qty = (proposed_qty // pack_size) * pack_size  # Round down
            if proposed_qty > 0 and moq > 1 and proposed_qty < moq:
                proposed_qty = 0  # Can't meet MOQ without exceeding max_stock
        
        # === APPLY SHELF LIFE PENALTY (Fase 2 - FORWARD-LOOKING) ===
        shelf_life_penalty_applied = False
        shelf_life_penalty_message = ""
        waste_risk_forward_percent = 0.0  # Projected waste risk at receipt_date
        waste_risk_demand_adjusted_percent = 0.0  # Demand-adjusted waste risk
        expected_waste_qty = 0  # Expected waste quantity after demand consumption
        
        if shelf_life_enabled and shelf_life_days > 0 and proposed_qty > 0:
            # Determina parametri penalty con category override
            category = demand_variability.value if demand_variability else "STABLE"
            category_overrides = settings.get("shelf_life_policy", {}).get("category_overrides", {}).get("value", {})
            category_params = category_overrides.get(category, {})
            
            waste_penalty_mode = sku_obj.waste_penalty_mode if (sku_obj and sku_obj.waste_penalty_mode) else \
                                 settings.get("shelf_life_policy", {}).get("waste_penalty_mode", {}).get("value", "soft")
            
            waste_penalty_factor = sku_obj.waste_penalty_factor if (sku_obj and sku_obj.waste_penalty_factor > 0) else \
                                   category_params.get("waste_penalty_factor",
                                   settings.get("shelf_life_policy", {}).get("waste_penalty_factor", {}).get("value", 0.5))
            
            waste_risk_threshold = sku_obj.waste_risk_threshold if (sku_obj and sku_obj.waste_risk_threshold > 0) else \
                                   category_params.get("waste_risk_threshold",
                                   settings.get("shelf_life_policy", {}).get("waste_risk_threshold", {}).get("value", 15.0))
            
            # NEW: Calculate FORWARD waste risk (at receipt_date, including incoming order)
            # This gives a more realistic picture than current waste_risk_percent
            receipt_date_calc = date.today() + timedelta(days=lead_time)
            
            # Get shelf life parameters (same as earlier calculation)
            min_shelf_life_calc = sku_obj.min_shelf_life_days if (sku_obj and sku_obj.min_shelf_life_days > 0) else \
                                  category_params.get("min_shelf_life_days", 
                                  settings.get("shelf_life_policy", {}).get("min_shelf_life_global", {}).get("value", 7))
            
            waste_horizon_days_calc = settings.get("shelf_life_policy", {}).get("waste_horizon_days", {}).get("value", 14)
            
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
        
        receipt_date = date.today() + timedelta(days=lead_time)
        
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
            # Shelf life info (Fase 2)
            usable_stock=usable_qty,
            unusable_stock=unusable_qty,
            waste_risk_percent=waste_risk_percent,
            waste_risk_forward_percent=waste_risk_forward_percent,
            waste_risk_demand_adjusted_percent=waste_risk_demand_adjusted_percent,
            expected_waste_qty=expected_waste_qty,
            shelf_life_penalty_applied=shelf_life_penalty_applied,
            shelf_life_penalty_message=shelf_life_penalty_message,
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
        
        for confirmation in confirmations:
            self.csv_layer.write_order_log(
                order_id=confirmation.order_id,
                date_str=confirmation.date.isoformat(),
                sku=confirmation.sku,
                qty=confirmation.qty_ordered,
                status=confirmation.status,
                receipt_date=confirmation.receipt_date.isoformat() if confirmation.receipt_date else None,
            )
        
        return confirmations, transactions


def calculate_daily_sales_average(
    sales_records,
    sku: str,
    days_lookback: int = 30,
    transactions=None,
    asof_date: Optional[date] = None,
    oos_detection_mode: str = "strict",
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
    
    Args:
        sales_records: List of SalesRecord objects
        sku: SKU identifier
        days_lookback: Number of calendar days to look back (default: 30)
        transactions: List of Transaction objects (for OOS detection + override markers + assortment tracking)
        asof_date: As-of date for calculation (defaults to today)
        oos_detection_mode: "strict" (on_hand==0) or "relaxed" (on_hand+on_order==0)
    
    Returns:
        Tuple (avg_daily_sales, oos_days_count):
        - avg_daily_sales: Average daily sales qty (excluding OOS days + out-of-assortment periods)
        - oos_days_count: Number of OOS days detected (after override exclusions)
    
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
    sku_sales_map = {}
    for s in sales_records:
        if s.sku == sku:
            sku_sales_map[s.date] = sku_sales_map.get(s.date, 0) + s.qty_sold
    
    # Generate calendar days range
    start_date = asof_date - timedelta(days=days_lookback - 1)
    calendar_days = [start_date + timedelta(days=i) for i in range(days_lookback)]
    
    # Build map of out-of-assortment periods from ledger
    out_of_assortment_days = set()
    
    if transactions:
        # Find all assortment transitions for this SKU
        assortment_events = [
            txn for txn in transactions 
            if txn.sku == sku and txn.event in (EventType.ASSORTMENT_OUT, EventType.ASSORTMENT_IN)
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
                        if current in calendar_days:
                            out_of_assortment_days.add(current)
                        current += timedelta(days=1)
                currently_out = False
                out_start = None
        
        # If still out at end of period
        if currently_out and out_start:
            current = out_start
            while current <= asof_date:
                if current in calendar_days:
                    out_of_assortment_days.add(current)
                current += timedelta(days=1)
    
    # Detect OOS days (if transactions provided)
    oos_days = set()
    oos_override_days = set()  # Days with OOS_ESTIMATE_OVERRIDE marker
    
    if transactions:
        # First, identify days with override markers
        for txn in transactions:
            if txn.sku == sku and txn.note and "OOS_ESTIMATE_OVERRIDE:" in txn.note:
                oos_override_days.add(txn.date)
        
        # Then detect OOS days, excluding override days
        for day in calendar_days:
            if day in oos_override_days:
                continue  # Skip days with estimate override
            
            stock = StockCalculator.calculate_asof(sku, day, transactions, sales_records)
            # Apply OOS detection mode
            if oos_detection_mode == "strict":
                # Strict mode: count as OOS if on_hand == 0 (ignora on_order)
                if stock.on_hand == 0:
                    oos_days.add(day)
            else:  # "relaxed" or default
                # Relaxed mode: count as OOS only if both on_hand and on_order == 0
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
    
    return (avg_sales, oos_days_count)

