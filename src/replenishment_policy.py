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
    window_weeks: int = 8
) -> Dict[str, Any]:
    """
    Compute CSL-based order quantity with full breakdown.
    
    Implements the policy:
        1. Calculate protection period P (from calendar)
        2. Forecast demand μ_P over P days
        3. Estimate uncertainty σ_P over P days
        4. Compute reorder point S = μ_P + z(α) × σ_P
        5. Calculate inventory position IP
        6. Raw order Q_raw = max(0, S - IP)
        7. Apply constraints: pack size, MOQ, cap
    
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
    
    Returns:
        Dict with comprehensive breakdown:
            - "sku": str
            - "order_date": date
            - "lane": str
            - "alpha": float (target CSL)
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
    
    # Step 1: Calculate protection period from calendar and receipt date
    from src.domain.calendar import next_receipt_date
    protection_period = calculate_protection_period_days(order_date, lane)
    receipt_date = next_receipt_date(order_date, lane)
    
    # Step 2: Fit forecast model and predict demand
    model = fit_forecast_model(history)
    forecast_values = predict(model, horizon=protection_period)
    forecast_demand = sum(forecast_values)  # μ_P
    
    # Step 3: Estimate uncertainty
    from src.uncertainty import estimate_demand_uncertainty
    
    def forecast_func(hist, horizon):
        m = fit_forecast_model(hist)
        return predict(m, horizon)
    
    sigma_daily, residuals = estimate_demand_uncertainty(
        history, forecast_func, window_weeks=window_weeks, method="mad"
    )
    
    sigma_horizon = sigma_over_horizon(protection_period, sigma_daily)
    
    # Step 4: Get z-score for target CSL
    z_score = _z_score_for_csl(alpha)
    
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
    
    # Build comprehensive breakdown
    return {
        "sku": sku,
        "order_date": order_date,
        "receipt_date": receipt_date,
        "lane": lane.name,
        "alpha": alpha,
        "protection_period": protection_period,
        "forecast_demand": forecast_demand,
        "sigma_daily": sigma_daily,
        "sigma_horizon": sigma_horizon,
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
        "n_residuals": len(residuals),
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
