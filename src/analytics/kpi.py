"""
KPI calculation module for reorder performance measurement.

Provides measurable, updateable KPIs to assess reordering effectiveness:
- OOS (Out-of-Stock) rate and frequency
- Lost sales estimation (base + forecast-driven)
- Forecast accuracy (WMAPE, bias)
- Supplier proxy KPIs (fill rate, OTIF, delay)

All functions reuse existing domain logic (OOS detection, stock calculation, 
forecast/uncertainty) and respect assortment exclusion + override markers.
"""

from datetime import date as Date, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

from ..domain.ledger import StockCalculator
from ..domain.models import EventType
from ..persistence.csv_layer import CSVLayer
from ..forecast import fit_forecast_model, predict_single_day, predict
from ..uncertainty import calculate_forecast_residuals


def compute_oos_kpi(
    sku: str,
    lookback_days: int,
    mode: str,
    csv_layer: CSVLayer,
    asof_date: Optional[Date] = None,
    return_details: bool = False
) -> Dict[str, Any]:
    """
    Calculate Out-of-Stock KPI for a SKU over a lookback period.
    
    Excludes:
    - Days marked with OOS_ESTIMATE_OVERRIDE in transaction notes
    - Days when SKU was out-of-assortment (between ASSORTMENT_OUT and ASSORTMENT_IN)
    
    Args:
        sku: SKU code to analyze
        lookback_days: Number of days to look back (e.g., 30, 90)
        mode: OOS detection mode ("strict" = on_hand==0, "relaxed" = on_hand+on_order==0)
        csv_layer: CSV persistence layer
        asof_date: Reference date (default: today)
        return_details: If True, include list of OOS dates in result
    
    Returns:
        Dict with:
            - oos_days_count: Number of OOS days detected
            - oos_rate: OOS rate as decimal (0.0-1.0)
            - n_periods: Number of days analyzed (excludes overrides and assortment-out)
            - oos_days_list: List of OOS dates (only if return_details=True)
    
    Raises:
        ValueError: If mode is not "strict" or "relaxed"
    """
    if mode not in ["strict", "relaxed"]:
        raise ValueError(f"Invalid OOS mode: {mode}. Must be 'strict' or 'relaxed'.")
    
    if asof_date is None:
        asof_date = Date.today()
    
    # Load transactions and determine assortment periods
    all_transactions = csv_layer.read_transactions()
    sku_transactions = [t for t in all_transactions if t.sku == sku]
    
    # Find ASSORTMENT_OUT/IN events to exclude out-of-assortment periods
    assortment_out_periods = _find_assortment_out_periods(sku_transactions)
    
    # Find OOS_ESTIMATE_OVERRIDE markers to exclude those days
    override_dates = _find_override_dates(sku_transactions)
    
    # Load sales records for stock calculation
    sales_records = csv_layer.read_sales()
    
    # Loop over lookback period day by day
    oos_days_list = []
    valid_days_count = 0
    
    for i in range(lookback_days):
        check_date = asof_date - timedelta(days=i)
        
        # Skip if date is in override list
        if check_date in override_dates:
            continue
        
        # Skip if date is in assortment-out period
        if _is_date_in_assortment_out(check_date, assortment_out_periods):
            continue
        
        # Calculate stock as-of this date
        stock = StockCalculator.calculate_asof(sku, check_date, all_transactions, sales_records)
        
        # Check OOS condition based on mode
        is_oos = False
        if mode == "strict":
            is_oos = (stock.on_hand == 0)
        else:  # relaxed
            is_oos = (stock.on_hand + stock.on_order == 0)
        
        if is_oos:
            oos_days_list.append(check_date)
        
        valid_days_count += 1
    
    # Calculate OOS rate
    oos_days_count = len(oos_days_list)
    oos_rate = oos_days_count / valid_days_count if valid_days_count > 0 else 0.0
    
    result = {
        "oos_days_count": oos_days_count,
        "oos_rate": oos_rate,
        "n_periods": valid_days_count,
    }
    
    if return_details:
        result["oos_days_list"] = oos_days_list
    
    return result


def _find_assortment_out_periods(transactions: List) -> List[Tuple[Date, Optional[Date]]]:
    """
    Find periods when SKU was out of assortment.
    
    Returns list of tuples (out_date, in_date).
    If no matching ASSORTMENT_IN found, in_date is None (remains out until now).
    """
    periods = []
    out_date = None
    
    # Sort transactions by date
    sorted_txns = sorted(transactions, key=lambda t: t.date)
    
    for txn in sorted_txns:
        if txn.event == EventType.ASSORTMENT_OUT:
            if out_date is None:  # Start new out period
                out_date = txn.date
        elif txn.event == EventType.ASSORTMENT_IN:
            if out_date is not None:  # Close current out period
                periods.append((out_date, txn.date))
                out_date = None
    
    # If still out at end, add open-ended period
    if out_date is not None:
        periods.append((out_date, None))
    
    return periods


def _find_override_dates(transactions: List) -> set:
    """
    Find dates marked with OOS_ESTIMATE_OVERRIDE in transaction notes.
    
    Returns set of dates to exclude from OOS calculation.
    """
    override_dates = set()
    
    for txn in transactions:
        if txn.note and "OOS_ESTIMATE_OVERRIDE" in txn.note:
            override_dates.add(txn.date)
    
    return override_dates


def _is_date_in_assortment_out(check_date: Date, periods: List[Tuple[Date, Optional[Date]]]) -> bool:
    """
    Check if a date falls within any assortment-out period.
    
    Args:
        check_date: Date to check
        periods: List of (out_date, in_date) tuples
    
    Returns:
        True if date is in an out period, False otherwise
    """
    for out_date, in_date in periods:
        if in_date is None:
            # Open-ended period: check if date >= out_date
            if check_date >= out_date:
                return True
        else:
            # Closed period: check if out_date <= date < in_date
            if out_date <= check_date < in_date:
                return True
    
    return False


def estimate_lost_sales(
    sku: str,
    lookback_days: int,
    mode: str,
    csv_layer: CSVLayer,
    asof_date: Optional[Date] = None,
    method: str = "forecast",
) -> Dict[str, Any]:
    """
    Estimate lost sales due to out-of-stock situations.
    
    Two estimation methods:
    1. "base": avg_sales_excluding_oos × oos_days_count
       - Simple, fast, works with minimal history
       - May underestimate if OOS occurs on high-demand days
    
    2. "forecast" (default): day-by-day forecast on OOS days
       - Fit forecast model on non-OOS, non-assortment-out days
       - Predict demand on each OOS day using DOW factors
       - Sum forecasted demand across all OOS days
       - More accurate but requires sufficient history (14+ days recommended)
    
    Args:
        sku: SKU code to analyze
        lookback_days: Number of days to look back
        mode: OOS detection mode ("strict" or "relaxed")
        csv_layer: CSV persistence layer
        asof_date: Reference date (default: today)
        method: Estimation method ("base" or "forecast")
    
    Returns:
        Dict with:
            - lost_units_est: Estimated lost units (float)
            - method_used: Method actually used ("base" or "forecast")
            - oos_days_count: Number of OOS days
            - fallback_reason: If method downgraded, reason string (optional)
    
    Raises:
        ValueError: If method is not "base" or "forecast"
    """
    if method not in ["base", "forecast"]:
        raise ValueError(f"Invalid method: {method}. Must be 'base' or 'forecast'.")
    
    if asof_date is None:
        asof_date = Date.today()
    
    # Load sales and transactions
    sales_records = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    
    # Import here to avoid circular dependency
    from ..workflows.order import calculate_daily_sales_average
    
    # Get average sales and OOS details using existing function
    avg_sales, oos_count, oos_days_list, assortment_out_list = calculate_daily_sales_average(
        sales_records=sales_records,
        sku=sku,
        days_lookback=lookback_days,
        transactions=transactions,
        asof_date=asof_date,
        oos_detection_mode=mode,
        return_details=True,
    )
    
    result = {
        "oos_days_count": oos_count,
        "method_used": method,
    }
    
    # If no OOS days, no lost sales
    if oos_count == 0:
        result["lost_units_est"] = 0.0
        return result
    
    # Base method: simple multiplication
    if method == "base":
        lost_units = avg_sales * oos_count
        result["lost_units_est"] = lost_units
        return result
    
    # Forecast method: day-by-day prediction on OOS days
    # Build history excluding OOS days and assortment-out days
    start_date = asof_date - timedelta(days=lookback_days - 1)
    oos_days_set = set(oos_days_list)
    assortment_out_set = set(assortment_out_list)
    
    history = []
    for i in range(lookback_days):
        day = start_date + timedelta(days=i)
        
        # Skip OOS days and assortment-out days for model training
        if day in oos_days_set or day in assortment_out_set:
            continue
        
        # Get sales for this day
        day_sales = sum(s.qty_sold for s in sales_records if s.sku == sku and s.date == day)
        history.append({"date": day, "qty_sold": day_sales})
    
    # Check if we have enough history for forecast model
    if len(history) < 7:
        # Fallback to base method
        lost_units = avg_sales * oos_count
        result["lost_units_est"] = lost_units
        result["method_used"] = "base"
        result["fallback_reason"] = f"Insufficient history for forecast ({len(history)} < 7 days)"
        return result
    
    # Fit forecast model on non-OOS days
    try:
        model_state = fit_forecast_model(history, alpha=0.3)
        
        # Predict on each OOS day and sum
        lost_units = 0.0
        for oos_day in oos_days_list:
            forecast_qty = predict_single_day(model_state, oos_day)
            lost_units += forecast_qty
        
        result["lost_units_est"] = lost_units
        return result
    
    except Exception as e:
        # If forecast fails, fallback to base method
        lost_units = avg_sales * oos_count
        result["lost_units_est"] = lost_units
        result["method_used"] = "base"
        result["fallback_reason"] = f"Forecast error: {str(e)}"
        return result


def compute_forecast_accuracy(
    sku: str,
    lookback_days: int,
    mode: str,
    csv_layer: CSVLayer,
    asof_date: Optional[Date] = None,
    window_weeks: int = 8,
) -> Dict[str, Any]:
    """
    Calculate forecast accuracy metrics for a SKU.
    
    Computes:
    - WMAPE (Weighted Mean Absolute Percentage Error): Sum(|error|) / Sum(|actual|) × 100
    - Bias: Mean(error) where error = actual - forecast
    
    Uses rolling window one-step-ahead forecasts to generate realistic error estimates.
    Excludes censored days (OOS) to avoid artificially low error estimates.
    
    Args:
        sku: SKU code to analyze
        lookback_days: Number of days to look back for history
        mode: OOS detection mode ("strict" or "relaxed") for censoring
        csv_layer: CSV persistence layer
        asof_date: Reference date (default: today)
        window_weeks: Rolling window size for forecast training (default: 8 weeks)
    
    Returns:
        Dict with:
            - wmape: Weighted MAPE as percentage (0.0-100.0+), or None if no valid points
            - bias: Mean forecast error (negative = under-forecast, positive = over-forecast)
            - n_points: Number of residual points calculated
            - n_censored_excluded: Number of censored (OOS) days excluded
            - sufficient_data: True if enough data for reliable estimate
    """
    if asof_date is None:
        asof_date = Date.today()
    
    # Load sales and transactions
    sales_records = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    
    # Build sales history
    start_date = asof_date - timedelta(days=lookback_days - 1)
    history = []
    
    for i in range(lookback_days):
        day = start_date + timedelta(days=i)
        day_sales = sum(s.qty_sold for s in sales_records if s.sku == sku and s.date == day)
        history.append({"date": day, "qty_sold": day_sales})
    
    # Build censored flags using OOS detection
    # Import here to avoid circular dependency
    from ..workflows.order import calculate_daily_sales_average
    
    _, _, oos_days_list, assortment_out_list = calculate_daily_sales_average(
        sales_records=sales_records,
        sku=sku,
        days_lookback=lookback_days,
        transactions=transactions,
        asof_date=asof_date,
        oos_detection_mode=mode,
        return_details=True,
    )
    
    oos_days_set = set(oos_days_list)
    assortment_out_set = set(assortment_out_list)
    
    # Create censored flags: True if day is OOS or out-of-assortment
    censored_flags = []
    for record in history:
        is_censored = (record["date"] in oos_days_set or record["date"] in assortment_out_set)
        censored_flags.append(is_censored)
    
    # Define forecast function for residual calculation
    def forecast_func(hist):
        """One-step-ahead forecast wrapper."""
        model = fit_forecast_model(hist, alpha=0.3)
        return predict(model, horizon=1)
    
    # Calculate residuals
    try:
        residuals, n_censored = calculate_forecast_residuals(
            history=history,
            forecast_func=forecast_func,
            window_weeks=window_weeks,
            censored_flags=censored_flags,
        )
    except Exception as e:
        # If calculation fails, return None values
        return {
            "wmape": None,
            "bias": None,
            "n_points": 0,
            "n_censored_excluded": 0,
            "sufficient_data": False,
            "error": str(e),
        }
    
    n_points = len(residuals)
    
    # Check if we have enough data
    min_required_days = window_weeks * 7 + 7
    sufficient_data = (len(history) >= min_required_days and n_points >= 7)
    
    # If no residuals, return None
    if n_points == 0:
        return {
            "wmape": None,
            "bias": None,
            "n_points": 0,
            "n_censored_excluded": n_censored,
            "sufficient_data": False,
        }
    
    # Calculate bias: mean of residuals (actual - forecast)
    bias = sum(residuals) / n_points
    
    # Calculate WMAPE: need actual values and absolute errors
    # Re-calculate with actual values to get WMAPE
    # Residuals are (actual - forecast), so |residual| = |actual - forecast|
    # We need Sum(|actual - forecast|) / Sum(|actual|) × 100
    
    # To calculate WMAPE, we need to re-traverse the evaluation period
    # and compute sum of |actual| alongside sum of |error|
    window_days = window_weeks * 7
    min_start_idx = window_days + 7
    
    sum_abs_error = 0.0
    sum_abs_actual = 0.0
    wmape_points = 0
    
    for i in range(min_start_idx, len(history)):
        if censored_flags[i]:
            continue  # Skip censored days
        
        # Train on window ending at i-1
        train_hist = history[i - window_days : i]
        
        try:
            model = fit_forecast_model(train_hist, alpha=0.3)
            forecast_values = predict(model, horizon=1)
            forecast_val = forecast_values[0] if forecast_values else 0.0
            
            actual_val = history[i]["qty_sold"]
            error = abs(actual_val - forecast_val)
            
            sum_abs_error += error
            sum_abs_actual += abs(actual_val)
            wmape_points += 1
        except:
            continue
    
    # Calculate WMAPE
    if sum_abs_actual > 0:
        wmape = (sum_abs_error / sum_abs_actual) * 100.0
    else:
        wmape = None  # Cannot calculate if all actuals are zero
    
    return {
        "wmape": wmape,
        "bias": bias,
        "n_points": n_points,
        "n_censored_excluded": n_censored,
        "sufficient_data": sufficient_data,
    }


def compute_supplier_proxy_kpi(
    sku: str,
    lookback_days: int,
    csv_layer: CSVLayer,
    asof_date: Optional[Date] = None,
) -> Dict[str, Any]:
    """
    Calculate supplier performance proxy KPIs for a SKU.
    
    Metrics:
    - Fill Rate: sum(qty_received) / sum(qty_ordered) from order logs
    - OTIF (On-Time In-Full): % of orders received on time AND in full quantity
    - Average Delay: mean(actual_receipt_date - expected_receipt_date) in days
    
    Actual receipt date matching strategy (degradation):
    1. Priority 1: receiving_logs.order_ids (links receipts to orders explicitly)
    2. Priority 2: First RECEIPT event in ledger after order date
    3. Priority 3: Mark as "n/a" if no actual receipt date found
    
    OTIF calculation:
    - Only counted for orders with known actual receipt date (not "n/a")
    - On-Time: actual_receipt_date <= expected_receipt_date
    - In-Full: qty_received == qty_ordered
    - Both conditions must be true for OTIF=True
    
    Args:
        sku: SKU code to analyze
        lookback_days: Number of days to look back for orders
        csv_layer: CSV persistence layer
        asof_date: Reference date (default: today)
    
    Returns:
        Dict with:
            - fill_rate: Fill rate as decimal (0.0-1.0+), or None if no orders
            - otif_rate: OTIF rate as decimal (0.0-1.0), or None if no calculable orders
            - avg_delay_days: Average delay in days (can be negative if early), or None
            - n_orders: Total number of orders in period
            - n_otif_calculable: Number of orders with known actual receipt date
    """
    if asof_date is None:
        asof_date = Date.today()
    
    # Load order logs and receiving logs
    order_logs = csv_layer.read_order_logs()
    receiving_logs = csv_layer.read_receiving_logs()
    transactions = csv_layer.read_transactions()
    
    # Filter orders for this SKU within lookback period
    start_date = asof_date - timedelta(days=lookback_days - 1)
    
    sku_orders = []
    for order in order_logs:
        if order.get("sku") != sku:
            continue
        
        order_date_str = order.get("date", "")
        if not order_date_str:
            continue
        
        try:
            order_date = Date.fromisoformat(order_date_str)
        except (ValueError, TypeError):
            continue
        
        if start_date <= order_date <= asof_date:
            sku_orders.append(order)
    
    n_orders = len(sku_orders)
    
    # If no orders, return None values
    if n_orders == 0:
        return {
            "fill_rate": None,
            "otif_rate": None,
            "avg_delay_days": None,
            "n_orders": 0,
            "n_otif_calculable": 0,
        }
    
    # Build receipt mapping: order_id -> actual_receipt_date
    order_to_receipt_date = {}
    
    # Priority 1: Use receiving_logs.order_ids
    for receipt in receiving_logs:
        if receipt.get("sku") != sku:
            continue
        
        order_ids_str = receipt.get("order_ids", "")
        if not order_ids_str:
            continue
        
        receipt_date_str = receipt.get("receipt_date", "")
        if not receipt_date_str:
            continue
        
        try:
            receipt_date = Date.fromisoformat(receipt_date_str)
        except (ValueError, TypeError):
            continue
        
        # Parse comma-separated order_ids
        order_ids_list = [oid.strip() for oid in order_ids_str.split(",") if oid.strip()]
        
        for order_id in order_ids_list:
            # Use earliest receipt date if multiple receipts for same order
            if order_id not in order_to_receipt_date:
                order_to_receipt_date[order_id] = receipt_date
            else:
                order_to_receipt_date[order_id] = min(order_to_receipt_date[order_id], receipt_date)
    
    # Priority 2: Fallback to ledger RECEIPT events for orders without receiving_logs match
    sku_receipt_events = [
        t for t in transactions 
        if t.sku == sku and t.event == EventType.RECEIPT
    ]
    sku_receipt_events.sort(key=lambda t: t.date)
    
    for order in sku_orders:
        order_id = order.get("order_id", "")
        if not order_id:
            continue
        
        # Skip if already matched via receiving_logs
        if order_id in order_to_receipt_date:
            continue
        
        # Find first RECEIPT event after order date
        order_date_str = order.get("date", "")
        if not order_date_str:
            continue
        
        try:
            order_date = Date.fromisoformat(order_date_str)
        except (ValueError, TypeError):
            continue
        
        # Look for first RECEIPT after order date
        for receipt_event in sku_receipt_events:
            if receipt_event.date > order_date:
                order_to_receipt_date[order_id] = receipt_event.date
                break
    
    # Calculate fill rate
    total_qty_ordered = 0
    total_qty_received = 0
    
    for order in sku_orders:
        qty_ordered = int(order.get("qty_ordered", 0))
        qty_received = int(order.get("qty_received", 0))
        
        total_qty_ordered += qty_ordered
        total_qty_received += qty_received
    
    fill_rate = total_qty_received / total_qty_ordered if total_qty_ordered > 0 else None
    
    # Calculate OTIF and delay
    otif_count = 0
    delay_days_list = []
    n_otif_calculable = 0
    
    for order in sku_orders:
        order_id = order.get("order_id", "")
        if not order_id:
            continue
        
        # Check if we have actual receipt date
        actual_receipt_date = order_to_receipt_date.get(order_id)
        if actual_receipt_date is None:
            continue  # Cannot calculate OTIF/delay without actual receipt date
        
        n_otif_calculable += 1
        
        # Get expected receipt date
        expected_receipt_str = order.get("receipt_date", "")
        if not expected_receipt_str:
            continue  # Cannot calculate OTIF/delay without expected date
        
        try:
            expected_receipt_date = Date.fromisoformat(expected_receipt_str)
        except (ValueError, TypeError):
            continue
        
        # Calculate delay
        delay_days = (actual_receipt_date - expected_receipt_date).days
        delay_days_list.append(delay_days)
        
        # Check OTIF conditions
        qty_ordered = int(order.get("qty_ordered", 0))
        qty_received = int(order.get("qty_received", 0))
        
        on_time = (actual_receipt_date <= expected_receipt_date)
        in_full = (qty_received == qty_ordered)
        
        if on_time and in_full:
            otif_count += 1
    
    # Calculate OTIF rate
    otif_rate = otif_count / n_otif_calculable if n_otif_calculable > 0 else None
    
    # Calculate average delay
    avg_delay_days = sum(delay_days_list) / len(delay_days_list) if delay_days_list else None
    
    return {
        "fill_rate": fill_rate,
        "otif_rate": otif_rate,
        "avg_delay_days": avg_delay_days,
        "n_orders": n_orders,
        "n_otif_calculable": n_otif_calculable,
    }
