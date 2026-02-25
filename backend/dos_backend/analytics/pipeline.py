"""
Pipeline builder for CSL-based order policy.

This module constructs the open pipeline (unfulfilled orders) from order_logs.csv,
filtering by receipt_date and aggregating quantities expected to arrive.
"""

from datetime import date, datetime
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def build_open_pipeline(csv_layer, sku: str, asof_date: date) -> List[Dict[str, Any]]:
    """
    Build open pipeline for a SKU from unfulfilled orders.
    
    Extracts unfulfilled orders from order_logs.csv, filters for valid receipt dates,
    and returns a sorted list of expected arrivals for CSL policy calculation.
    
    Args:
        csv_layer: CSV persistence layer instance (must have get_unfulfilled_orders method)
        sku: SKU code to filter orders
        asof_date: Reference date for pipeline calculation (future orders only)
        
    Returns:
        List of dicts with keys:
            - receipt_date: date object (expected arrival date)
            - qty: int (quantity expected to arrive)
        
        Sorted by receipt_date ascending. Empty list if no unfulfilled orders.
        
    Example:
        >>> pipeline = build_open_pipeline(csv_layer, "SKU001", date(2025, 1, 15))
        >>> # [{"receipt_date": date(2025, 1, 20), "qty": 50},
        >>> #  {"receipt_date": date(2025, 1, 27), "qty": 30}]
        
    Notes:
        - Filters out orders with qty_unfulfilled <= 0
        - Filters out orders with missing or invalid receipt_date
        - Filters out orders with receipt_date <= asof_date (already received/late)
        - ISO date parsing with error handling (invalid dates logged and skipped)
        - Multiple orders with same receipt_date are kept separate (no aggregation)
    """
    unfulfilled_orders = csv_layer.get_unfulfilled_orders(sku)
    
    pipeline = []
    for order in unfulfilled_orders:
        qty_unfulfilled = order.get("qty_unfulfilled", 0)
        receipt_date_str = order.get("receipt_date", "")
        
        # Skip if no unfulfilled quantity
        if qty_unfulfilled <= 0:
            continue
            
        # Skip if no receipt date
        if not receipt_date_str:
            logger.debug(
                f"Skipping order {order.get('order_id', 'unknown')} for {sku}: "
                f"missing receipt_date"
            )
            continue
        
        # Parse receipt_date (ISO format YYYY-MM-DD)
        try:
            receipt_date = datetime.strptime(receipt_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Invalid receipt_date '{receipt_date_str}' for order "
                f"{order.get('order_id', 'unknown')} (SKU {sku}): {e}"
            )
            continue
        
        # Skip if receipt_date is in the past or today (already should have arrived)
        if receipt_date <= asof_date:
            logger.debug(
                f"Skipping order {order.get('order_id', 'unknown')} for {sku}: "
                f"receipt_date {receipt_date} <= asof_date {asof_date}"
            )
            continue
        
        pipeline.append({
            "receipt_date": receipt_date,
            "qty": int(qty_unfulfilled)
        })
    
    # Sort by receipt_date ascending
    pipeline.sort(key=lambda x: x["receipt_date"])
    
    logger.debug(
        f"Built pipeline for {sku} (asof {asof_date}): {len(pipeline)} pending orders"
    )
    
    return pipeline
