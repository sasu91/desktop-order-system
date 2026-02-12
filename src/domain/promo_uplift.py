"""
Promo uplift estimation using event-level ratios with hierarchical pooling.

Methodology:
- For each historical promo event (start-end window), compute:
  uplift_event = sum(actual_sales during promo, non-censored days) / sum(baseline_pred during promo, same days)
- Aggregate uplift_event per SKU using winsorized mean (trim outliers)
- Apply configurable guardrails (min=1.0, max=3.0)
- If SKU has too few events (<N), use hierarchical pooling:
  SKU → category → department → global

Output:
- estimate_uplift(sku_id) -> (uplift_factor, confidence_grade, report)
- confidence based on: number events, valid days, pooling depth (A/B/C)
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Dict, Tuple, Optional
import statistics
import logging

# Import dependencies
try:
    from ..forecast import baseline_forecast
    from .ledger import is_day_censored
    from .models import SKU, SalesRecord, PromoWindow, Transaction
    # promo_windows_for_sku not used directly (filtering done in extract_promo_events)
except ImportError:
    from forecast import baseline_forecast
    from domain.ledger import is_day_censored
    from domain.models import SKU, SalesRecord, PromoWindow, Transaction


logger = logging.getLogger(__name__)


@dataclass
class UpliftEvent:
    """Single promo event uplift calculation."""
    sku: str
    start_date: date
    end_date: date
    actual_sales: float  # Sum of actual sales during event (non-censored days)
    baseline_pred: float  # Sum of baseline prediction during event (same days)
    uplift_ratio: float  # actual_sales / baseline_pred
    valid_days: int  # Number of non-censored days in event
    note: str = ""  # Calculation notes


@dataclass
class UpliftReport:
    """SKU uplift estimation report."""
    sku: str
    uplift_factor: float  # Final aggregated uplift
    confidence: str  # "A", "B", or "C"
    events_used: List[UpliftEvent]  # Events contributing to uplift
    pooling_source: str  # "SKU", "category:<name>", "department:<name>", or "global"
    n_events: int  # Total events used
    n_valid_days_total: int  # Total valid days across all events
    notes: str = ""  # Additional notes


def extract_promo_events(
    sku_id: str,
    promo_windows: List[PromoWindow],
    sales_records: List[SalesRecord],
    transactions: List[Transaction],
    asof_date: Optional[date] = None,
) -> List[Tuple[date, date]]:
    """
    Extract historical promo event date ranges for a SKU.
    
    Merges overlapping/adjacent windows to avoid double-counting.
    Only returns events in the past (end_date < asof_date).
    
    Args:
        sku_id: SKU identifier
        promo_windows: List of all promo windows
        sales_records: All sales records (for validation)
        transactions: All transactions (for validation)
        asof_date: Reference date (default: today)
    
    Returns:
        List of (start_date, end_date) tuples for promo events
    """
    if asof_date is None:
        asof_date = date.today()
    
    # Filter windows for this SKU
    sku_windows = [w for w in promo_windows if w.sku == sku_id]
    
    # Filter out future events
    past_windows = [w for w in sku_windows if w.end_date < asof_date]
    
    if not past_windows:
        return []
    
    # Sort by start_date
    past_windows.sort(key=lambda w: w.start_date)
    
    # Merge overlapping/adjacent windows (gap <= 1 day)
    merged_events = []
    current_start = past_windows[0].start_date
    current_end = past_windows[0].end_date
    
    for window in past_windows[1:]:
        gap_days = (window.start_date - current_end).days
        
        if gap_days <= 1:  # Overlapping or adjacent (merge)
            current_end = max(current_end, window.end_date)
        else:  # Separate event
            merged_events.append((current_start, current_end))
            current_start = window.start_date
            current_end = window.end_date
    
    # Add last event
    merged_events.append((current_start, current_end))
    
    return merged_events


def calculate_uplift_for_event(
    sku_id: str,
    event_start: date,
    event_end: date,
    sales_records: List[SalesRecord],
    transactions: List[Transaction],
    epsilon: float = 0.1,
) -> Optional[UpliftEvent]:
    """
    Calculate uplift for a single promo event using event-level ratio.
    
    uplift_event = sum(actual_sales during promo, non-censored days) /
                   sum(baseline_pred during promo, same days)
    
    Baseline is trained with data STRICTLY BEFORE event_start (anti-leakage).
    
    Args:
        sku_id: SKU identifier
        event_start: Promo event start date
        event_end: Promo event end date
        sales_records: All sales records
        transactions: All transactions (for censoring)
        epsilon: Minimum baseline denominator to avoid div-by-zero
    
    Returns:
        UpliftEvent dataclass or None if insufficient data
    """
    # Build horizon dates for event
    horizon_dates = []
    current = event_start
    while current <= event_end:
        horizon_dates.append(current)
        current += timedelta(days=1)
    
    # Filter sales for this SKU (ONLY data before event_start for baseline training)
    sku_sales_before_event = [
        s for s in sales_records 
        if s.sku == sku_id and s.date < event_start
    ]
    
    if not sku_sales_before_event:
        logger.warning(f"No historical sales for {sku_id} before {event_start}, cannot estimate baseline")
        return None
    
    # Generate baseline forecast for event dates (trained on data BEFORE event)
    try:
        baseline_preds = baseline_forecast(
            sku_id=sku_id,
            horizon_dates=horizon_dates,
            sales_records=sku_sales_before_event,  # Anti-leakage: only past data
            transactions=transactions,
            asof_date=event_start - timedelta(days=1),  # Train up to day before event
        )
    except Exception as e:
        logger.error(f"Baseline forecast failed for {sku_id} event {event_start}-{event_end}: {e}")
        return None
    
    # Collect actual sales during event (filter by promo_flag=1 and non-censored)
    actual_sales_sum = 0.0
    baseline_sum = 0.0
    valid_days = 0
    
    for event_date in horizon_dates:
        # Check if day is censored (OOS)
        is_censored, _ = is_day_censored(
            sku=sku_id,
            check_date=event_date,
            transactions=transactions,
            sales_records=sales_records,
        )
        
        if is_censored:
            continue  # Skip censored days
        
        # Get actual sales for this day
        day_sales = [s for s in sales_records if s.sku == sku_id and s.date == event_date]
        day_actual = sum(s.qty_sold for s in day_sales)
        
        # Get baseline prediction for this day
        day_baseline = baseline_preds.get(event_date, 0.0)
        
        actual_sales_sum += day_actual
        baseline_sum += day_baseline
        valid_days += 1
    
    # Check minimum valid days
    if valid_days == 0:
        logger.warning(f"Event {event_start}-{event_end} for {sku_id}: no valid days (all censored)")
        return None
    
    # Check denominator epsilon
    if baseline_sum < epsilon:
        logger.warning(f"Event {event_start}-{event_end} for {sku_id}: baseline_sum={baseline_sum:.2f} < epsilon={epsilon}, skipping")
        return None
    
    # Calculate uplift ratio
    uplift_ratio = actual_sales_sum / baseline_sum
    
    return UpliftEvent(
        sku=sku_id,
        start_date=event_start,
        end_date=event_end,
        actual_sales=actual_sales_sum,
        baseline_pred=baseline_sum,
        uplift_ratio=uplift_ratio,
        valid_days=valid_days,
        note=f"Actual={actual_sales_sum:.1f}, Baseline={baseline_sum:.1f}, Valid days={valid_days}"
    )


def winsorized_mean(values: List[float], trim_percent: float = 10.0) -> float:
    """
    Compute winsorized mean (trim extreme values to reduce outlier impact).
    
    Args:
        values: List of numeric values
        trim_percent: Percentage to trim from each tail (0-50)
    
    Returns:
        Winsorized mean value
    
    Example:
        >>> winsorized_mean([1, 2, 3, 100], trim_percent=10)
        26.5  # 100 is trimmed to ~3, then mean computed
    """
    if not values:
        return 0.0
    
    if len(values) == 1:
        return values[0]
    
    # Sort values
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    
    # Calculate trim count
    trim_count = max(0, int(n * trim_percent / 100.0))
    
    if trim_count == 0 or trim_count >= n // 2:
        # No trimming or invalid trim → use regular mean
        return statistics.mean(sorted_vals)
    
    # Trim: replace lower trim_count with lower_bound, upper trim_count with upper_bound
    lower_bound = sorted_vals[trim_count]
    upper_bound = sorted_vals[-(trim_count + 1)]
    
    winsorized_vals = []
    for v in sorted_vals:
        if v < lower_bound:
            winsorized_vals.append(lower_bound)
        elif v > upper_bound:
            winsorized_vals.append(upper_bound)
        else:
            winsorized_vals.append(v)
    
    return statistics.mean(winsorized_vals)


def aggregate_uplift_events(
    events: List[UpliftEvent],
    trim_percent: float = 10.0,
    min_uplift: float = 1.0,
    max_uplift: float = 3.0,
) -> float:
    """
    Aggregate event-level uplifts using winsorized mean + guardrails.
    
    Args:
        events: List of UpliftEvent
        trim_percent: Winsorization trim percentage
        min_uplift: Minimum uplift guardrail (clipping)
        max_uplift: Maximum uplift guardrail (clipping)
    
    Returns:
        Final aggregated uplift factor
    """
    if not events:
        return 1.0  # No events → neutral uplift
    
    ratios = [e.uplift_ratio for e in events]
    
    # Winsorized mean
    agg_uplift = winsorized_mean(ratios, trim_percent=trim_percent)
    
    # Apply guardrails (clipping)
    agg_uplift = max(min_uplift, min(max_uplift, agg_uplift))
    
    return agg_uplift


def hierarchical_pooling(
    sku_id: str,
    sku_obj: SKU,
    all_skus: List[SKU],
    promo_windows: List[PromoWindow],
    sales_records: List[SalesRecord],
    transactions: List[Transaction],
    settings: Dict,
) -> Tuple[List[UpliftEvent], str]:
    """
    Hierarchical pooling fallback: SKU → category → department → global.
    
    If SKU has insufficient events, pool events from:
    1. Same category (if category set and min events met)
    2. Same department (if department set and min events met)
    3. Global (all SKUs)
    
    Args:
        sku_id: Target SKU
        sku_obj: Target SKU object (for category/department)
        all_skus: All SKUs in system
        promo_windows: All promo windows
        sales_records: All sales records
        transactions: All transactions
        settings: Global settings dict
    
    Returns:
        (pooled_events, pooling_source)
        - pooled_events: List of UpliftEvent from pooled SKUs
        - pooling_source: "category:<name>", "department:<name>", "global"
    """
    uplift_config = settings.get("promo_uplift", {})
    min_events_cat = uplift_config.get("min_events_category", {}).get("value", 5)
    min_events_dept = uplift_config.get("min_events_department", {}).get("value", 10)
    epsilon = uplift_config.get("denominator_epsilon", {}).get("value", 0.1)
    
    # Try category pooling
    if sku_obj.category:
        category_skus = [s for s in all_skus if s.category == sku_obj.category]
        category_events = []
        
        for cat_sku in category_skus:
            sku_promo_events = extract_promo_events(
                cat_sku.sku, promo_windows, sales_records, transactions
            )
            
            for event_start, event_end in sku_promo_events:
                uplift_event = calculate_uplift_for_event(
                    cat_sku.sku, event_start, event_end, 
                    sales_records, transactions, epsilon=epsilon
                )
                if uplift_event:
                    category_events.append(uplift_event)
        
        if len(category_events) >= min_events_cat:
            return (category_events, f"category:{sku_obj.category}")
    
    # Try department pooling
    if sku_obj.department:
        dept_skus = [s for s in all_skus if s.department == sku_obj.department]
        dept_events = []
        
        for dept_sku in dept_skus:
            sku_promo_events = extract_promo_events(
                dept_sku.sku, promo_windows, sales_records, transactions
            )
            
            for event_start, event_end in sku_promo_events:
                uplift_event = calculate_uplift_for_event(
                    dept_sku.sku, event_start, event_end,
                    sales_records, transactions, epsilon=epsilon
                )
                if uplift_event:
                    dept_events.append(uplift_event)
        
        if len(dept_events) >= min_events_dept:
            return (dept_events, f"department:{sku_obj.department}")
    
    # Fallback to global pooling (ALL SKUs)
    global_events = []
    
    for global_sku in all_skus:
        sku_promo_events = extract_promo_events(
            global_sku.sku, promo_windows, sales_records, transactions
        )
        
        for event_start, event_end in sku_promo_events:
            uplift_event = calculate_uplift_for_event(
                global_sku.sku, event_start, event_end,
                sales_records, transactions, epsilon=epsilon
            )
            if uplift_event:
                global_events.append(uplift_event)
    
    return (global_events, "global")


def estimate_uplift(
    sku_id: str,
    all_skus: List[SKU],
    promo_windows: List[PromoWindow],
    sales_records: List[SalesRecord],
    transactions: List[Transaction],
    settings: Dict,
) -> UpliftReport:
    """
    Estimate promo uplift factor for a SKU using event-level ratios and hierarchical pooling.
    
    Main API for uplift estimation. Returns uplift_factor, confidence grade, and detailed report.
    
    Args:
        sku_id: Target SKU identifier
        all_skus: All SKUs (for pooling fallback)
        promo_windows: All promo windows
        sales_records: All sales records
        transactions: All transactions
        settings: Global settings dict
    
    Returns:
        UpliftReport with final uplift_factor, confidence, and event details
    
    Example:
        >>> report = estimate_uplift("SKU001", skus, promo_windows, sales, txns, settings)
        >>> report.uplift_factor
        1.45
        >>> report.confidence
        'A'
        >>> report.pooling_source
        'SKU'
    """
    # Get uplift config
    uplift_config = settings.get("promo_uplift", {})
    min_uplift = uplift_config.get("min_uplift", {}).get("value", 1.0)
    max_uplift = uplift_config.get("max_uplift", {}).get("value", 3.0)
    min_events_sku = uplift_config.get("min_events_sku", {}).get("value", 3)
    min_valid_days_sku = uplift_config.get("min_valid_days_sku", {}).get("value", 7)
    trim_percent = uplift_config.get("winsorize_trim_percent", {}).get("value", 10.0)
    epsilon = uplift_config.get("denominator_epsilon", {}).get("value", 0.1)
    conf_threshold_a = uplift_config.get("confidence_threshold_a", {}).get("value", 3)
    conf_threshold_b = uplift_config.get("confidence_threshold_b", {}).get("value", 5)
    
    # Find SKU object
    sku_obj = next((s for s in all_skus if s.sku == sku_id), None)
    if not sku_obj:
        logger.error(f"SKU {sku_id} not found in all_skus")
        return UpliftReport(
            sku=sku_id,
            uplift_factor=1.0,
            confidence="C",
            events_used=[],
            pooling_source="not_found",
            n_events=0,
            n_valid_days_total=0,
            notes="SKU not found"
        )
    
    # Extract SKU-level events
    sku_promo_events = extract_promo_events(
        sku_id, promo_windows, sales_records, transactions
    )
    
    sku_uplift_events = []
    for event_start, event_end in sku_promo_events:
        uplift_event = calculate_uplift_for_event(
            sku_id, event_start, event_end, sales_records, transactions, epsilon=epsilon
        )
        if uplift_event:
            sku_uplift_events.append(uplift_event)
    
    # Check if SKU has sufficient events
    total_valid_days = sum(e.valid_days for e in sku_uplift_events)
    
    if len(sku_uplift_events) >= min_events_sku and total_valid_days >= min_valid_days_sku:
        # SKU has sufficient data → use SKU-level events
        final_events = sku_uplift_events
        pooling_source = "SKU"
        
        # Confidence: A if robust SKU data
        if len(final_events) >= conf_threshold_a:
            confidence = "A"
        else:
            confidence = "B"
    else:
        # SKU lacks sufficient data → hierarchical pooling
        final_events, pooling_source = hierarchical_pooling(
            sku_id, sku_obj, all_skus, promo_windows, sales_records, transactions, settings
        )
        
        # Confidence: B if pooled from category/department, C if global
        if pooling_source == "global":
            confidence = "C"
        else:
            confidence = "B" if len(final_events) >= conf_threshold_b else "C"
    
    # Aggregate uplift
    if final_events:
        uplift_factor = aggregate_uplift_events(
            final_events, trim_percent=trim_percent, 
            min_uplift=min_uplift, max_uplift=max_uplift
        )
    else:
        uplift_factor = 1.0  # No events → neutral uplift
        confidence = "C"
    
    total_valid_days_final = sum(e.valid_days for e in final_events)
    
    return UpliftReport(
        sku=sku_id,
        uplift_factor=uplift_factor,
        confidence=confidence,
        events_used=final_events,
        pooling_source=pooling_source,
        n_events=len(final_events),
        n_valid_days_total=total_valid_days_final,
        notes=f"Aggregated from {len(final_events)} events, pooling: {pooling_source}"
    )
