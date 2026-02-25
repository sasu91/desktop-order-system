"""
Promo Calendar utilities for promotional period management.

Minimal implementation: manages promo windows (start/end dates) without
price, discount %, type, or visibility info.

All dates are inclusive (start_date and end_date both included).
Timezone-naive dates (assumes business timezone consistency).
"""
import logging
from datetime import date as Date
from typing import List, Optional

from .domain.models import PromoWindow, SalesRecord
from .persistence.csv_layer import CSVLayer


logger = logging.getLogger(__name__)


# ============ Query Functions ============

def is_promo(check_date: Date, sku: str, promo_windows: List[PromoWindow], store_id: Optional[str] = None) -> bool:
    """
    Check if a specific date is a promo day for given SKU.
    
    Args:
        check_date: Date to check
        sku: SKU identifier
        promo_windows: List of active promo windows
        store_id: Optional store filter
            - If None: check all windows for SKU regardless of store
            - If specified: check windows with matching store_id OR global windows (store_id=None)
    
    Returns:
        True if date falls within any promo window for the SKU
    """
    for window in promo_windows:
        if window.sku != sku:
            continue
        
        # Store filter logic:
        # - If no filter (None), include all windows
        # - If filter specified, include matching store OR global windows (None)
        if store_id is not None and window.store_id is not None and window.store_id != store_id:
            continue  # Store mismatch (both specified but different), skip
        
        if window.contains_date(check_date):
            return True
    
    return False


def promo_windows_for_sku(sku: str, promo_windows: List[PromoWindow], store_id: Optional[str] = None) -> List[PromoWindow]:
    """
    Get all promo windows for a specific SKU.
    
    Args:
        sku: SKU identifier
        promo_windows: List of all promo windows
        store_id: Optional store filter
            - If None: return all windows for SKU regardless of store
            - If specified: return windows with matching store_id OR global windows (store_id=None)
    
    Returns:
        Filtered list of PromoWindow objects for the SKU (sorted by start_date)
    """
    filtered = []
    for w in promo_windows:
        if w.sku != sku:
            continue
        
        # If no store filter, include all
        if store_id is None:
            filtered.append(w)
            continue
        
        # If store filter specified, include:
        # 1. Windows with matching store_id
        # 2. Global windows (store_id=None)
        if w.store_id == store_id or w.store_id is None:
            filtered.append(w)
    
    # Sort by start_date
    filtered.sort(key=lambda w: w.start_date)
    return filtered


# ============ Mutation Functions ============

def add_promo_window(
    csv_layer: CSVLayer,
    window: PromoWindow,
    allow_overlap: bool = False,
) -> bool:
    """
    Add a new promo window to the calendar.
    
    Args:
        csv_layer: CSV persistence layer
        window: PromoWindow to add
        allow_overlap: If False, reject if window overlaps with existing (same SKU+store)
    
    Returns:
        True if added successfully, False if rejected due to overlap
    """
    existing_windows = csv_layer.read_promo_calendar()
    
    if not allow_overlap:
        # Check for overlaps with same SKU and store
        for existing in existing_windows:
            if window.overlaps_with(existing):
                logger.warning(
                    f"Promo window overlap detected for SKU={window.sku}, store={window.store_id}: "
                    f"New window {window.start_date} to {window.end_date} overlaps with "
                    f"existing {existing.start_date} to {existing.end_date}"
                )
                return False
    
    # No overlap (or overlap allowed), add window
    csv_layer.write_promo_window(window)
    logger.info(f"Added promo window for SKU={window.sku}, store={window.store_id}, "
                f"{window.start_date} to {window.end_date} ({window.duration_days()} days)")
    return True


def remove_promo_window(
    csv_layer: CSVLayer,
    sku: str,
    start_date: Date,
    end_date: Date,
    store_id: Optional[str] = None,
) -> bool:
    """
    Remove a promo window matching exact SKU, dates, and store.
    
    Args:
        csv_layer: CSV persistence layer
        sku: SKU identifier
        start_date: Window start date
        end_date: Window end date
        store_id: Optional store identifier
    
    Returns:
        True if removed, False if not found
    """
    existing_windows = csv_layer.read_promo_calendar()
    
    # Filter out matching window
    filtered = [
        w for w in existing_windows
        if not (w.sku == sku and w.start_date == start_date and w.end_date == end_date and w.store_id == store_id)
    ]
    
    if len(filtered) == len(existing_windows):
        logger.warning(f"Promo window not found for removal: SKU={sku}, store={store_id}, "
                      f"{start_date} to {end_date}")
        return False
    
    # Overwrite calendar without the removed window
    csv_layer.write_promo_calendar(filtered)
    logger.info(f"Removed promo window for SKU={sku}, store={store_id}, {start_date} to {end_date}")
    return True


def validate_no_overlap(windows: List[PromoWindow]) -> List[tuple[PromoWindow, PromoWindow]]:
    """
    Validate that no promo windows overlap for the same SKU+store.
    
    Args:
        windows: List of PromoWindow objects to validate
    
    Returns:
        List of (window1, window2) tuples representing overlapping pairs.
        Empty list if no overlaps.
    """
    overlaps = []
    
    for i, w1 in enumerate(windows):
        for w2 in windows[i + 1:]:
            if w1.overlaps_with(w2):
                overlaps.append((w1, w2))
    
    return overlaps


# ============ Sales Data Integration ============

def apply_promo_flags_to_sales(
    sales_records: List[SalesRecord],
    promo_windows: List[PromoWindow],
    store_id: Optional[str] = None,
) -> List[SalesRecord]:
    """
    Apply promo_flag from promo calendar to sales records (retroactive/future).
    
    For each sale, check if date+sku falls within a promo window.
    If yes, set promo_flag=1; otherwise promo_flag=0.
    
    Args:
        sales_records: List of SalesRecord objects
        promo_windows: List of promo windows from calendar
        store_id: Optional store filter
    
    Returns:
        New list of SalesRecord objects with updated promo_flag
    """
    updated_sales = []
    
    for sale in sales_records:
        # Check if sale date is in a promo window
        promo_active = is_promo(sale.date, sale.sku, promo_windows, store_id)
        
        new_promo_flag = 1 if promo_active else 0
        
        # Create new SalesRecord with updated promo_flag
        updated_sale = SalesRecord(
            date=sale.date,
            sku=sale.sku,
            qty_sold=sale.qty_sold,
            promo_flag=new_promo_flag,
        )
        updated_sales.append(updated_sale)
    
    return updated_sales


def enrich_sales_with_promo_calendar(csv_layer: CSVLayer, store_id: Optional[str] = None):
    """
    Enrich sales.csv with promo_flag from promo_calendar.csv (in-place update).
    
    Reads promo calendar, applies flags to all sales records, overwrites sales.csv.
    
    Args:
        csv_layer: CSV persistence layer
        store_id: Optional store filter
    """
    logger.info("Enriching sales data with promo calendar...")
    
    sales = csv_layer.read_sales()
    promo_windows = csv_layer.read_promo_calendar()
    
    updated_sales = apply_promo_flags_to_sales(sales, promo_windows, store_id)
    
    # Count changes
    changes = sum(1 for old, new in zip(sales, updated_sales) if old.promo_flag != new.promo_flag)
    
    # Overwrite sales.csv
    csv_layer.write_sales(updated_sales)
    
    logger.info(f"Promo enrichment complete: {changes} sales records updated out of {len(sales)}")


# ============ Reporting Functions ============

def get_promo_stats(promo_windows: List[PromoWindow], sku: Optional[str] = None) -> dict:
    """
    Get summary statistics for promo calendar.
    
    Args:
        promo_windows: List of all promo windows
        sku: Optional SKU filter (if None, stats for all)
    
    Returns:
        Dictionary with stats:
        - total_windows: Total number of promo windows
        - total_promo_days: Total promo days (sum of all window durations)
        - avg_window_duration: Average duration per window (days)
        - sku_count: Number of unique SKUs with promos
    """
    if sku:
        promo_windows = [w for w in promo_windows if w.sku == sku]
    
    total_windows = len(promo_windows)
    total_promo_days = sum(w.duration_days() for w in promo_windows)
    avg_duration = total_promo_days / total_windows if total_windows > 0 else 0
    unique_skus = len(set(w.sku for w in promo_windows))
    
    return {
        "total_windows": total_windows,
        "total_promo_days": total_promo_days,
        "avg_window_duration": avg_duration,
        "sku_count": unique_skus,
    }


def get_active_promos(promo_windows: List[PromoWindow], check_date: Date) -> List[PromoWindow]:
    """
    Get all active promo windows for a specific date.
    
    Args:
        promo_windows: List of all promo windows
        check_date: Date to check
    
    Returns:
        List of PromoWindow objects active on check_date
    """
    active = [w for w in promo_windows if w.contains_date(check_date)]
    return active


def get_upcoming_promos(promo_windows: List[PromoWindow], check_date: Date, days_ahead: int = 30) -> List[PromoWindow]:
    """
    Get promo windows starting within the next N days.
    
    Args:
        promo_windows: List of all promo windows
        check_date: Reference date (usually today)
        days_ahead: Number of days to look ahead
    
    Returns:
        List of PromoWindow objects starting within [check_date, check_date + days_ahead]
    """
    from datetime import timedelta
    
    end_date = check_date + timedelta(days=days_ahead)
    
    upcoming = [
        w for w in promo_windows
        if check_date <= w.start_date <= end_date
    ]
    
    # Sort by start_date (earliest first)
    upcoming.sort(key=lambda w: w.start_date)
    return upcoming
