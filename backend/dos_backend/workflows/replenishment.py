"""
Replenishment workflow: order generation using CSL-based policy.

Handles:
- Single order generation for standard days (Monday-Thursday)
- Dual order generation for Friday (Saturday + Monday lanes)
- Pipeline update between Friday orders to avoid double-counting

New in Feb 2026: generate_order_for_sku() and generate_orders_for_date() are
thin wrappers that delegate to compute_order() (existing) or compute_order_v2()
(new typed-contract API).  They retain their original signatures for backward
compatibility.
"""
from datetime import date
from typing import List, Dict, Optional
from dataclasses import dataclass

from ..domain.calendar import Lane, next_receipt_date
from ..replenishment_policy import compute_order, compute_order_v2, OrderConstraints

# Re-export contract types so callers can import from this module
from ..domain.contracts import DemandDistribution, InventoryPosition, AppliedModifier, OrderExplain  # noqa: F401


@dataclass
class OrderSuggestion:
    """
    Order suggestion with full breakdown.
    
    Attributes:
        sku: SKU identifier
        order_date: Date of order placement
        lane: Order lane (STANDARD, SATURDAY, MONDAY)
        receipt_date: Expected delivery date
        order_qty: Recommended order quantity
        reorder_point: Calculated reorder point (S)
        inventory_position: Current IP (on_hand + pipeline)
        forecast_demand: Forecasted demand for protection period
        sigma_horizon: Uncertainty over protection period
        alpha: Target CSL
        breakdown: Full computation breakdown from policy
    """
    sku: str
    order_date: date
    lane: Lane
    receipt_date: date
    order_qty: int
    reorder_point: float
    inventory_position: int
    forecast_demand: float
    sigma_horizon: float
    alpha: float
    breakdown: dict


def generate_orders_for_date(
    order_date: date,
    sku_data: Dict[str, Dict],
    alpha: float = 0.95,
) -> List[OrderSuggestion]:
    """
    Generate order suggestions for a given date.
    
    For Monday-Thursday: Single order (STANDARD lane)
    For Friday: Two orders (SATURDAY lane + MONDAY lane)
    
    CRITICAL: Friday logic ensures no double-counting:
    1. Compute Saturday order
    2. Add Saturday order to pipeline
    3. Compute Monday order with updated pipeline
    
    Args:
        order_date: Date for which to generate orders
        sku_data: Dict of SKU data:
            {
                "SKU-A": {
                    "on_hand": int,
                    "pipeline": [{"receipt_date": date, "qty": int}],
                    "constraints": OrderConstraints,
                    "history": [{"date": date, "qty_sold": float}]
                },
                ...
            }
        alpha: Target CSL (0-1)
    
    Returns:
        List of OrderSuggestion objects (1 for Mon-Thu, 2 for Friday)
    
    Example:
        >>> sku_data = {
        ...     "WIDGET-A": {
        ...         "on_hand": 50,
        ...         "pipeline": [],
        ...         "constraints": OrderConstraints(pack_size=10, moq=20),
        ...         "history": [...]
        ...     }
        ... }
        >>> suggestions = generate_orders_for_date(date(2024, 4, 5), sku_data)  # Friday
        >>> len(suggestions)
        2  # Saturday + Monday orders
    """
    suggestions = []
    dow = order_date.weekday()
    
    # Determine lanes for this order date
    if dow == 4:  # Friday
        lanes = [Lane.SATURDAY, Lane.MONDAY]
    elif dow in (0, 1, 2, 3):  # Monday-Thursday
        lanes = [Lane.STANDARD]
    else:  # Saturday/Sunday (not valid order days)
        raise ValueError(f"Invalid order date: {order_date.strftime('%A')} is not a valid order day")
    
    # Track pipeline updates for Friday dual orders
    pipeline_updates = {}  # {sku: [new orders to add to pipeline]}
    
    for lane in lanes:
        for sku, data in sku_data.items():
            # Get current pipeline (with any updates from previous lane)
            current_pipeline = data["pipeline"].copy()
            if sku in pipeline_updates:
                current_pipeline.extend(pipeline_updates[sku])
            
            # Compute order using CSL policy
            result = compute_order(
                sku=sku,
                order_date=order_date,
                lane=lane,
                alpha=alpha,
                on_hand=data["on_hand"],
                pipeline=current_pipeline,
                constraints=data["constraints"],
                history=data["history"]
            )
            
            # Create suggestion
            suggestion = OrderSuggestion(
                sku=sku,
                order_date=order_date,
                lane=lane,
                receipt_date=result["receipt_date"],
                order_qty=result["order_final"],
                reorder_point=result["reorder_point"],
                inventory_position=result["inventory_position"],
                forecast_demand=result["forecast_demand"],
                sigma_horizon=result["sigma_horizon"],
                alpha=alpha,
                breakdown=result
            )
            
            suggestions.append(suggestion)
            
            # If Friday and this is SATURDAY lane, update pipeline for MONDAY calculation
            if lane == Lane.SATURDAY and result["order_final"] > 0:
                if sku not in pipeline_updates:
                    pipeline_updates[sku] = []
                pipeline_updates[sku].append({
                    "receipt_date": result["receipt_date"],
                    "qty": result["order_final"]
                })
    
    return suggestions


def generate_order_for_sku(
    sku: str,
    order_date: date,
    lane: Lane,
    on_hand: int,
    pipeline: List[Dict],
    constraints: OrderConstraints,
    history: List[Dict],
    alpha: float = 0.95
) -> OrderSuggestion:
    """
    Generate single order suggestion for one SKU on specific lane.
    
    This is a convenience wrapper around compute_order() that returns
    an OrderSuggestion object.
    
    Args:
        sku: SKU identifier
        order_date: Order placement date
        lane: Order lane
        on_hand: Current on-hand inventory
        pipeline: Existing pipeline orders
        constraints: Operational constraints
        history: Historical sales data
        alpha: Target CSL
    
    Returns:
        OrderSuggestion with full breakdown
    """
    result = compute_order(
        sku=sku,
        order_date=order_date,
        lane=lane,
        alpha=alpha,
        on_hand=on_hand,
        pipeline=pipeline,
        constraints=constraints,
        history=history
    )
    
    return OrderSuggestion(
        sku=sku,
        order_date=order_date,
        lane=lane,
        receipt_date=result["receipt_date"],
        order_qty=result["order_final"],
        reorder_point=result["reorder_point"],
        inventory_position=result["inventory_position"],
        forecast_demand=result["forecast_demand"],
        sigma_horizon=result["sigma_horizon"],
        alpha=alpha,
        breakdown=result
    )


def generate_order_for_sku_v2(
    demand: "DemandDistribution",
    position: "InventoryPosition",
    sku: str,
    order_date: date,
    lane: Lane,
    constraints: OrderConstraints,
    alpha: float = 0.95,
) -> "OrderSuggestion":
    """
    Typed-contract wrapper: generates an OrderSuggestion using the
    pre-built DemandDistribution and InventoryPosition.

    Unlike ``generate_order_for_sku()``, this function does NOT call
    ``fit_forecast_model`` or ``estimate_demand_uncertainty`` internally –
    it delegates entirely to ``compute_order_v2()``.

    Parameters
    ----------
    demand : DemandDistribution
        Output of demand_builder.build_demand_distribution() after modifiers.
    position : InventoryPosition
        Current inventory state.
    sku : str
    order_date : date
    lane : Lane
    constraints : OrderConstraints
    alpha : float

    Returns
    -------
    OrderSuggestion
    """
    result = compute_order_v2(
        demand=demand,
        position=position,
        alpha=alpha,
        constraints=constraints,
        order_date=order_date,
        lane=lane,
    )
    return OrderSuggestion(
        sku=sku,
        order_date=order_date,
        lane=lane,
        receipt_date=result["receipt_date"],
        order_qty=result["order_final"],
        reorder_point=result["reorder_point"],
        inventory_position=result["inventory_position"],
        forecast_demand=result["forecast_demand"],
        sigma_horizon=result["sigma_horizon"],
        alpha=alpha,
        breakdown=result,
    )


def calculate_inventory_position_asof(
    order_date: date,
    on_hand: int,
    pipeline: List[Dict],
    asof_date: date
) -> int:
    """
    Calculate inventory position as-of a specific date.
    
    IP(asof) = on_hand + sum(pipeline where receipt_date <= asof_date)
    
    Args:
        order_date: Reference order date (for context)
        on_hand: Current on-hand inventory
        pipeline: List of pending orders with receipt_date and qty
        asof_date: Date for which to calculate IP
    
    Returns:
        Inventory position as of the specified date
    
    Example:
        >>> pipeline = [
        ...     {"receipt_date": date(2024, 4, 6), "qty": 50},   # Saturday
        ...     {"receipt_date": date(2024, 4, 8), "qty": 100}   # Monday
        ... ]
        >>> calculate_inventory_position_asof(
        ...     order_date=date(2024, 4, 5),  # Friday
        ...     on_hand=20,
        ...     pipeline=pipeline,
        ...     asof_date=date(2024, 4, 6)  # Saturday
        ... )
        70  # 20 + 50 (Monday order not yet received)
    """
    on_order = sum(
        order["qty"]
        for order in pipeline
        if order["receipt_date"] <= asof_date
    )
    return on_hand + on_order
