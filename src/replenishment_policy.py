"""
CSL-based Replenishment Policy

This module implements a Cycle Service Level (CSL) based reorder policy
that integrates demand forecasting, uncertainty estimation, and inventory
position tracking to compute optimal order quantities.

Policy Formula:
    S = μ_P + z(α) × σ_P    (Reorder point)
    Q_raw = max(0, S - IP)   (Raw order quantity)
    Q_final = apply_constraints(Q_raw)  (Pack size, MOQ, cap)

Where:
    - S: Reorder point (service level target)
    - μ_P: Forecasted demand over protection period P
    - σ_P: Demand uncertainty over protection period P
    - z(α): Z-score for target CSL (e.g., α=0.95 → z=1.645)
    - IP: Inventory Position = On-Hand + On-Order
    - Q: Order quantity

Author: Desktop Order System Team
Date: February 2026
"""

from typing import Dict, List, Any, Optional
from datetime import date, timedelta
from dataclasses import dataclass

# Import existing modules
from src.domain.calendar import calculate_protection_period_days, Lane
from src.forecast import fit_forecast_model, predict
from src.uncertainty import calculate_safety_stock, sigma_over_horizon


@dataclass
class OrderConstraints:
    """Constraints for order quantity calculation."""
    pack_size: int = 1           # Units per pack (rounding constraint)
    moq: int = 0                 # Minimum Order Quantity
    max_stock: Optional[int] = None  # Maximum stock cap (on-hand + on-order + order)
    
    def __post_init__(self):
        """Validate constraints."""
        if self.pack_size < 1:
            raise ValueError(f"pack_size must be >= 1, got {self.pack_size}")
        if self.moq < 0:
            raise ValueError(f"moq must be >= 0, got {self.moq}")
        if self.max_stock is not None and self.max_stock < 0:
            raise ValueError(f"max_stock must be >= 0 or None, got {self.max_stock}")


def _z_score_for_csl(alpha: float) -> float:
    """
    Get z-score for target Cycle Service Level (CSL).
    
    CSL = Probability of not stocking out during protection period.
    
    Args:
        alpha: Target CSL (0 < alpha < 1)
    
    Returns:
        float: Corresponding z-score
        
    Examples:
        >>> _z_score_for_csl(0.95)
        1.645
    """
    # Z-score lookup table
    z_scores = {
        0.50: 0.000,
        0.75: 0.674,
        0.80: 0.842,
        0.85: 1.036,
        0.90: 1.282,
        0.95: 1.645,
        0.98: 2.054,
        0.99: 2.326,
        0.995: 2.576,
        0.999: 3.090,
    }
    
    if alpha in z_scores:
        return z_scores[alpha]
    
    # Find closest value
    closest_alpha = min(z_scores.keys(), key=lambda x: abs(x - alpha))
    return z_scores[closest_alpha]


def _apply_pack_size(quantity: float, pack_size: int) -> int:
    """
    Round up to nearest pack size.
    
    Args:
        quantity: Raw quantity (may be fractional)
        pack_size: Units per pack
    
    Returns:
        int: Quantity rounded up to pack multiple
        
    Examples:
        >>> _apply_pack_size(10.0, 1)
        10
        >>> _apply_pack_size(10.1, 5)
        15  # Rounds up to next pack
        >>> _apply_pack_size(15.0, 5)
        15  # Already multiple
    """
    if quantity <= 0:
        return 0
    
    import math
    packs_needed = math.ceil(quantity / pack_size)
    return packs_needed * pack_size


def _apply_moq(quantity: int, moq: int) -> int:
    """
    Apply Minimum Order Quantity constraint.
    
    Args:
        quantity: Quantity after pack rounding
        moq: Minimum order quantity
    
    Returns:
        int: Quantity adjusted for MOQ (0 if below MOQ, otherwise unchanged)
        
    Examples:
        >>> _apply_moq(10, 0)
        10
        >>> _apply_moq(5, 10)
        0  # Below MOQ → don't order
        >>> _apply_moq(15, 10)
        15  # Above MOQ → order
    """
    if quantity < moq:
        return 0  # Below MOQ → don't order
    return quantity


def _apply_cap(quantity: int, current_ip: float, max_stock: Optional[int]) -> int:
    """
    Apply maximum stock cap constraint.
    
    Cap = max_stock - current_inventory_position
    Final quantity = min(quantity, cap)
    
    Args:
        quantity: Quantity after pack and MOQ
        current_ip: Current inventory position (on-hand + on-order)
        max_stock: Maximum allowed stock level (None = no cap)
    
    Returns:
        int: Quantity capped to max_stock limit
        
    Examples:
        >>> _apply_cap(100, 50, None)
        100  # No cap
        >>> _apply_cap(100, 50, 120)
        70  # Cap = 120 - 50 = 70
        >>> _apply_cap(100, 150, 120)
        0  # Already over cap
    """
    if max_stock is None:
        return quantity
    
    available_capacity = max(0, max_stock - int(current_ip))
    return min(quantity, available_capacity)


def _calculate_inventory_position(
    on_hand: float,
    pipeline: List[Dict[str, Any]],
    forecast_date: date
) -> float:
    """
    Calculate Inventory Position as of forecast date.
    
    IP = On-Hand + On-Order (expected to arrive by forecast date)
    
    Args:
        on_hand: Current on-hand inventory
        pipeline: List of on-order records with keys:
            - "receipt_date": date
            - "qty": float
        forecast_date: Date for which to calculate IP
    
    Returns:
        float: Inventory position
        
    Examples:
        >>> _calculate_inventory_position(
        ...     on_hand=50,
        ...     pipeline=[
        ...         {"receipt_date": date(2024, 2, 1), "qty": 20},
        ...         {"receipt_date": date(2024, 2, 10), "qty": 30}
        ...     ],
        ...     forecast_date=date(2024, 2, 5)
        ... )
        70.0  # 50 + 20 (first order arrives before forecast_date)
    """
    on_order = sum(
        item["qty"]
        for item in pipeline
        if item.get("receipt_date") and item["receipt_date"] <= forecast_date
    )
    
    return on_hand + on_order


def compute_order(
    sku: str,
    order_date: date,
    lane: Lane,
    alpha: float,
    on_hand: float,
    pipeline: List[Dict[str, Any]],
    constraints: OrderConstraints,
    history: List[Dict[str, Any]],
    window_weeks: int = 8,
    censored_flags: Optional[List[bool]] = None,
    alpha_boost_for_censored: float = 0.05,
    forecast_demand_override: Optional[float] = None,
    sigma_horizon_override: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute CSL-based order quantity with full breakdown.
    
    Implements the policy:
        1. Calculate protection period P (from calendar)
        2. Forecast demand μ_P over P days (exclude censored)
        3. Estimate uncertainty σ_P over P days (exclude censored)
        4. Compute reorder point S = μ_P + z(α) × σ_P
        5. Calculate inventory position IP
        6. Raw order Q_raw = max(0, S - IP)
        7. Apply constraints: pack size, MOQ, cap
    
    CENSORED DAYS HANDLING:
    - Days with OOS/inevasi are marked censored and excluded from:
      - Forecast model training (prevents underestimation)
      - Uncertainty/sigma calculation (prevents sigma collapse)
    - If censored days exist, alpha_eff = min(0.99, alpha + alpha_boost_for_censored)
    
    Args:
        sku: SKU identifier
        order_date: Date when order is placed
        lane: Logistics lane (STANDARD, SATURDAY, MONDAY)
        alpha: Target Cycle Service Level (0 < α < 1)
        on_hand: Current on-hand inventory
        pipeline: List of on-order items with receipt_date and qty
        constraints: OrderConstraints (pack_size, moq, max_stock)
        history: Sales history with keys "date" and "qty_sold"
        window_weeks: Rolling window for uncertainty estimation
        censored_flags: Optional list of bool (same length as history).
                       True = day censored (OOS/inevasi), exclude from model.
        alpha_boost_for_censored: Boost alpha if censored days present (default 0.05).
        forecast_demand_override: Optional override for μ_P (protection period forecast).
                                 If provided, skips fit_forecast_model/predict steps.
                                 Use for event/promo-adjusted external forecasts.
        sigma_horizon_override: Optional override for σ_P (protection period uncertainty).
                               If provided, skips uncertainty estimation step.
                               Use when external forecast includes uncertainty estimate.
    
    Returns:
        Dict with comprehensive breakdown:
            - "sku": str
            - "order_date": date
            - "lane": str
            - "alpha": float (original target CSL)
            - "alpha_eff": float (effective alpha used, possibly boosted)
            - "n_censored": int (number of censored days in history)
            - "censored_reasons": List[str] (reasons for censored days)
            - "protection_period": int (P days)
            - "forecast_demand": float (μ_P)
            - "sigma_daily": float (σ_day)
            - "sigma_horizon": float (σ_P)
            - "z_score": float (z(α))
            - "reorder_point": float (S)
            - "on_hand": float
            - "on_order": float (sum of pipeline)
            - "inventory_position": float (IP)
            - "order_raw": float (Q_raw before constraints)
            - "order_after_pack": int (after pack rounding)
            - "order_after_moq": int (after MOQ check)
            - "order_final": int (Q_final after all constraints)
            - "constraints_applied": List[str] (reasons for adjustments)
            - "service_level_target": float (α)
    
    Examples:
        >>> constraints = OrderConstraints(pack_size=10, moq=20, max_stock=500)
        >>> result = compute_order(
        ...     sku="SKU001",
        ...     order_date=date(2024, 2, 1),
        ...     lane=Lane.STANDARD,
        ...     alpha=0.95,
        ...     on_hand=50,
        ...     pipeline=[],
        ...     constraints=constraints,
        ...     history=sales_history
        ... )
        >>> print(result["order_final"])
        30  # Example: rounded to pack size
    """
    # Validation
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if on_hand < 0:
        raise ValueError(f"on_hand must be >= 0, got {on_hand}")
    
    # Track censored days metadata
    n_censored = 0
    censored_reasons = []
    if censored_flags:
        if len(censored_flags) != len(history):
            raise ValueError(f"censored_flags length must match history length")
        n_censored = sum(censored_flags)
        # Collect sample reasons (up to 3 for brevity)
        for i, (h, is_censored) in enumerate(zip(history[:10], censored_flags[:10])):
            if is_censored:
                censored_reasons.append(f"{h['date']} (OOS/inevaso)")
                if len(censored_reasons) >= 3:
                    if n_censored > 3:
                        censored_reasons.append(f"... +{n_censored - 3} more")
                    break
    
    # Calculate effective alpha (boost if censored days present)
    has_censored = n_censored > 0
    alpha_eff = min(0.99, alpha + (alpha_boost_for_censored if has_censored else 0.0))
    
    # Step 1: Calculate protection period from calendar and receipt date
    from src.domain.calendar import next_receipt_date
    protection_period = calculate_protection_period_days(order_date, lane)
    receipt_date = next_receipt_date(order_date, lane)
    
    # Step 2: Fit forecast model and predict demand (with censored filtering)
    # If forecast_demand_override is provided, use it directly (event/promo-adjusted forecast)
    if forecast_demand_override is not None:
        forecast_demand = forecast_demand_override
        # Run model fitting only for metadata tracking (not used for forecast_demand)
        model = fit_forecast_model(
            history,
            censored_flags=censored_flags,
            alpha_boost_for_censored=alpha_boost_for_censored
        )
        forecast_values = [forecast_demand / protection_period] * protection_period  # Dummy values for compatibility
    else:
        # Standard path: fit model and predict
        model = fit_forecast_model(
            history,
            censored_flags=censored_flags,
            alpha_boost_for_censored=alpha_boost_for_censored
        )
        forecast_values = predict(model, horizon=protection_period)
        forecast_demand = sum(forecast_values)  # μ_P
    
    # Step 3: Estimate uncertainty (with censored filtering)
    # If sigma_horizon_override is provided, use it directly (event/promo-adjusted uncertainty)
    if sigma_horizon_override is not None:
        sigma_horizon = sigma_horizon_override
        sigma_daily = sigma_horizon / (protection_period ** 0.5) if protection_period > 0 else 0.0
        uncertainty_meta = {
            "n_residuals": 0,
            "n_censored_excluded": 0,
            "method": "override",
        }
    else:
        # Standard path: estimate uncertainty from history
        from src.uncertainty import estimate_demand_uncertainty
        
        def forecast_func(hist, horizon):
            m = fit_forecast_model(hist, censored_flags=censored_flags, alpha_boost_for_censored=alpha_boost_for_censored)
            return predict(m, horizon)
        
        sigma_daily, uncertainty_meta = estimate_demand_uncertainty(
            history, forecast_func, window_weeks=window_weeks, method="mad", censored_flags=censored_flags
        )
        
        sigma_horizon = sigma_over_horizon(protection_period, sigma_daily)
    
    # Step 4: Get z-score for effective CSL (use alpha_eff)
    z_score = _z_score_for_csl(alpha_eff)
    
    # Step 5: Calculate reorder point S = μ_P + z(α) × σ_P
    reorder_point = forecast_demand + z_score * sigma_horizon
    
    # Step 6: Calculate inventory position IP
    # IP as of protection period end (order_date + P)
    forecast_end_date = order_date + timedelta(days=protection_period)
    inventory_position = _calculate_inventory_position(
        on_hand, pipeline, forecast_end_date
    )
    
    on_order = inventory_position - on_hand
    
    # Step 7: Raw order quantity Q_raw = max(0, S - IP)
    order_raw = max(0.0, reorder_point - inventory_position)
    
    # Step 8: Apply constraints with tracking
    constraints_applied = []
    
    # Pack size rounding
    order_after_pack = _apply_pack_size(order_raw, constraints.pack_size)
    if order_after_pack != int(order_raw):
        constraints_applied.append(
            f"pack_size: {order_raw:.1f} → {order_after_pack} "
            f"(rounded up to {constraints.pack_size} units/pack)"
        )
    
    # MOQ constraint
    order_after_moq = _apply_moq(order_after_pack, constraints.moq)
    if order_after_moq == 0 and order_after_pack > 0:
        constraints_applied.append(
            f"moq: {order_after_pack} < {constraints.moq} → 0 (below MOQ, don't order)"
        )
    
    # Cap constraint
    order_final = _apply_cap(order_after_moq, inventory_position, constraints.max_stock)
    if constraints.max_stock is not None and order_final < order_after_moq:
        cap = max(0, constraints.max_stock - int(inventory_position))
        constraints_applied.append(
            f"max_stock: {order_after_moq} → {order_final} "
            f"(capped by max_stock={constraints.max_stock}, available={cap})"
        )
    
    # Build comprehensive breakdown with censored metadata
    return {
        "sku": sku,
        "order_date": order_date,
        "receipt_date": receipt_date,
        "lane": lane.name,
        "alpha": alpha,
        "alpha_eff": alpha_eff,
        "n_censored": n_censored,
        "censored_reasons": censored_reasons,
        "n_censored_excluded_from_sigma": uncertainty_meta["n_censored_excluded"],
        "protection_period": protection_period,
        "forecast_demand": forecast_demand,
        "forecast_n_samples": model["n_samples"],
        "forecast_n_censored": model["n_censored"],
        "forecast_alpha_eff": model["alpha_eff"],
        "sigma_daily": sigma_daily,
        "sigma_horizon": sigma_horizon,
        "sigma_n_residuals": uncertainty_meta["n_residuals"],
        "z_score": z_score,
        "reorder_point": reorder_point,
        "on_hand": on_hand,
        "on_order": on_order,
        "inventory_position": inventory_position,
        "order_raw": order_raw,
        "order_after_pack": order_after_pack,
        "order_after_moq": order_after_moq,
        "order_final": order_final,
        "constraints_applied": constraints_applied,
        "service_level_target": alpha,
    }


def compute_order_v2(
    demand: "DemandDistribution",
    position: "InventoryPosition",
    alpha: float,
    constraints: OrderConstraints,
    order_date: date,
    lane: Lane,
) -> dict:
    """
    Typed-contract version of the CSL order computation.

    This is the preferred entry-point for all new code.  Unlike
    ``compute_order()``, it does NOT re-estimate mu_P or sigma_P
    internally – it relies entirely on the pre-built ``DemandDistribution``
    and ``InventoryPosition`` passed in.

    STOP condition enforcement
    --------------------------
    If you call this function with a ``demand`` object, the policy WILL NOT
    call ``fit_forecast_model`` or ``estimate_demand_uncertainty``.  If you
    need to verify this at runtime, call
    ``compute_order_v2_assert_no_internal_forecast()`` in tests.

    Parameters
    ----------
    demand : DemandDistribution
        Pre-built distribution (mu_P, sigma_P, protection_period_days).
        Must be the output of ``demand_builder.build_demand_distribution()``
        after ``modifier_builder.apply_modifiers()`` has been called.
    position : InventoryPosition
        Current inventory state.
    alpha : float
        Target Cycle Service Level (0 < alpha < 1).
    constraints : OrderConstraints
        Pack size, MOQ, max stock cap.
    order_date : date
        Date the order is placed.
    lane : Lane
        Logistics lane (STANDARD / SATURDAY / MONDAY).

    Returns
    -------
    dict  – same schema as ``compute_order()`` for backward compatibility:
        order_final, reorder_point, forecast_demand, sigma_horizon, z_score,
        inventory_position, on_order, on_hand, order_raw, order_after_pack,
        order_after_moq, constraints_applied, protection_period, alpha,
        alpha_eff, receipt_date, lane, sku, order_date, n_censored,
        forecast_method  (added), sigma_adj_multiplier (added).
    """
    # --- runtime guard: must not call internal forecast helpers ----------
    # (tests can patch these to assert they are never called)
    from src.domain.contracts import DemandDistribution as _DD, InventoryPosition as _IP

    if not isinstance(demand, _DD):
        raise TypeError(f"demand must be DemandDistribution, got {type(demand)}")
    if not isinstance(position, _IP):
        raise TypeError(f"position must be InventoryPosition, got {type(position)}")

    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    # Step 1: Protection period (from demand object – already calculated)
    P = demand.protection_period_days
    mu_P = demand.mu_P
    sigma_P = demand.sigma_P

    # Step 2: alpha – no boost here (censored boost already baked into demand if needed)
    alpha_eff = min(0.99, alpha)  # Hard cap; caller passes boosted alpha if desired

    # Step 3: z-score
    z = _z_score_for_csl(alpha_eff)

    # Step 4: Reorder point  S = mu_P + z × sigma_P
    S = mu_P + z * sigma_P

    # Step 5: Inventory position as-of end of protection period
    from src.domain.calendar import next_receipt_date as _nrd
    receipt_dt = _nrd(order_date, lane)
    forecast_end_date = order_date + timedelta(days=P)
    IP = position.ip_asof(forecast_end_date)
    on_order_val = IP - position.on_hand + position.unfulfilled

    # Step 6: Raw order
    order_raw = max(0.0, S - IP)

    # Step 7: Constraints
    constraints_applied = []

    order_after_pack = _apply_pack_size(order_raw, constraints.pack_size)
    if order_after_pack != int(order_raw):
        constraints_applied.append(
            f"pack_size: {order_raw:.1f} → {order_after_pack} "
            f"(rounded up to {constraints.pack_size} units/pack)"
        )

    order_after_moq = _apply_moq(order_after_pack, constraints.moq)
    if order_after_moq == 0 and order_after_pack > 0:
        constraints_applied.append(
            f"moq: {order_after_pack} < {constraints.moq} → 0 (below MOQ, don't order)"
        )

    order_final = _apply_cap(order_after_moq, IP, constraints.max_stock)
    if constraints.max_stock is not None and order_final < order_after_moq:
        cap = max(0, constraints.max_stock - int(IP))
        constraints_applied.append(
            f"max_stock: {order_after_moq} → {order_final} "
            f"(capped by max_stock={constraints.max_stock}, available={cap})"
        )

    return {
        # Identity
        "sku": getattr(position, "_sku", ""),  # optional – caller may attach
        "order_date": order_date,
        "receipt_date": receipt_dt,
        "lane": lane.name,
        # CSL parameters
        "alpha": alpha,
        "alpha_eff": alpha_eff,
        "z_score": z,
        # Demand
        "forecast_demand": mu_P,
        "sigma_daily": sigma_P / max(P ** 0.5, 1.0),
        "sigma_horizon": sigma_P,
        "forecast_method": demand.forecast_method,
        "sigma_adj_multiplier": demand.sigma_adj_multiplier,
        "n_censored": demand.n_censored,
        "n_samples": demand.n_samples,
        "protection_period": P,
        # Inventory
        "on_hand": position.on_hand,
        "on_order": max(0.0, on_order_val),
        "inventory_position": IP,
        # Order computation
        "reorder_point": S,
        "order_raw": order_raw,
        "order_after_pack": order_after_pack,
        "order_after_moq": order_after_moq,
        "order_final": order_final,
        "constraints_applied": constraints_applied,
        "service_level_target": alpha,
        # Compatibility: keep keys compute_order() returns
        "censored_reasons": [],
        "n_censored_excluded_from_sigma": 0,
        "forecast_n_samples": demand.n_samples,
        "forecast_n_censored": demand.n_censored,
        "forecast_alpha_eff": alpha_eff,
        "sigma_n_residuals": 0,
    }


def compute_order_batch(
    skus: List[str],
    order_date: date,
    lane: Lane,
    alpha: float,
    inventory_data: Dict[str, Dict[str, Any]],
    constraints_map: Dict[str, OrderConstraints],
    history_map: Dict[str, List[Dict[str, Any]]],
    window_weeks: int = 8
) -> Dict[str, Dict[str, Any]]:
    """
    Compute orders for multiple SKUs in batch.
    
    Args:
        skus: List of SKU identifiers
        order_date: Order placement date
        lane: Logistics lane
        alpha: Target CSL
        inventory_data: Map {sku: {"on_hand": float, "pipeline": List}}
        constraints_map: Map {sku: OrderConstraints}
        history_map: Map {sku: sales history}
        window_weeks: Rolling window size
    
    Returns:
        Dict mapping sku → compute_order result
        
    Examples:
        >>> results = compute_order_batch(
        ...     skus=["SKU001", "SKU002"],
        ...     order_date=date.today(),
        ...     lane=Lane.STANDARD,
        ...     alpha=0.95,
        ...     inventory_data={
        ...         "SKU001": {"on_hand": 50, "pipeline": []},
        ...         "SKU002": {"on_hand": 30, "pipeline": []}
        ...     },
        ...     constraints_map={
        ...         "SKU001": OrderConstraints(pack_size=10),
        ...         "SKU002": OrderConstraints(pack_size=5)
        ...     },
        ...     history_map={
        ...         "SKU001": sales_history_1,
        ...         "SKU002": sales_history_2
        ...     }
        ... )
    """
    results = {}
    
    for sku in skus:
        if sku not in inventory_data or sku not in history_map:
            # Skip SKUs with missing data
            continue
        
        inv_data = inventory_data[sku]
        constraints = constraints_map.get(sku, OrderConstraints())
        history = history_map[sku]
        
        result = compute_order(
            sku=sku,
            order_date=order_date,
            lane=lane,
            alpha=alpha,
            on_hand=inv_data["on_hand"],
            pipeline=inv_data.get("pipeline", []),
            constraints=constraints,
            history=history,
            window_weeks=window_weeks
        )
        
        results[sku] = result
    
    return results
