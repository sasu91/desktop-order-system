"""
Simple and robust demand forecasting for daily sales.

Model: Level + Day-of-Week (DOW) factors with exponential smoothing.

Approach:
- Level: Base demand level (smoothed average)
- DOW Factors: Multiplicative factors per day of week (0=Monday, 6=Sunday)
- Forecast: level × dow_factor[day_of_week]

Fallback for short history:
- < 7 days: Use simple mean, uniform DOW factors
- 7-13 days: Calculate DOW factors if possible
- >= 14 days: Full model with smoothing

Output: Always non-negative.
"""

from datetime import date, timedelta
from typing import List, Dict, Any, Optional
import statistics


def fit_forecast_model(
    history: List[Dict[str, Any]],
    alpha: float = 0.3,
    min_samples_for_dow: int = 14,
    censored_flags: Optional[List[bool]] = None,
    alpha_boost_for_censored: float = 0.0,
) -> Dict[str, Any]:
    """
    Fit a simple level + DOW factor forecasting model.
    
    Args:
        history: List of dicts with keys {"date": date, "qty_sold": float}
                 Sorted by date (oldest first recommended).
        alpha: Smoothing parameter for exponential moving average (0 < alpha <= 1).
               Lower alpha = more smoothing. Default 0.3 for daily data.
        min_samples_for_dow: Minimum samples needed to compute DOW factors.
        censored_flags: Optional list of bool (same length as history) indicating
                       censored days (OOS/inevasi). Censored days excluded from model.
        alpha_boost_for_censored: Increase alpha for SKUs with censored days.
                                 alpha_eff = min(0.99, alpha + alpha_boost_for_censored)
    
    Returns:
        model_state: Dict containing:
            - "level": float (base demand level)
            - "dow_factors": List[float] (7 factors, Mon=0 to Sun=6)
            - "last_date": date (last date in training data)
            - "n_samples": int (number of samples used)
            - "n_censored": int (number of censored days excluded)
            - "alpha_eff": float (effective alpha used, possibly boosted)
            - "method": str (model method used: "full", "simple", "fallback")
    
    Example:
        >>> history = [
        ...     {"date": date(2024, 1, 1), "qty_sold": 10},
        ...     {"date": date(2024, 1, 2), "qty_sold": 12},
        ... ]
        >>> model = fit_forecast_model(history)
        >>> model["level"]
        11.0
    """
    if not history:
        # Empty history: return zero model
        return {
            "level": 0.0,
            "dow_factors": [1.0] * 7,
            "last_date": None,
            "n_samples": 0,
            "n_censored": 0,
            "alpha_eff": alpha,
            "method": "fallback",
        }
    
    # Filter out censored days if provided
    n_censored = 0
    if censored_flags:
        if len(censored_flags) != len(history):
            raise ValueError(f"censored_flags length ({len(censored_flags)}) != history length ({len(history)})")
        
        # Keep only non-censored days
        filtered_history = [h for h, is_censored in zip(history, censored_flags) if not is_censored]
        n_censored = sum(censored_flags)
    else:
        filtered_history = history
    
    # Calculate effective alpha (boost if censored days present)
    has_censored = n_censored > 0
    alpha_eff = min(0.99, alpha + (alpha_boost_for_censored if has_censored else 0.0))
    
    # Extract dates and quantities from filtered history
    dates = [h["date"] for h in filtered_history]
    quantities = [max(0, h["qty_sold"]) for h in filtered_history]  # Ensure non-negative
    n_samples = len(filtered_history)
    
    # Calculate base level using exponential smoothing
    if n_samples == 1:
        level = quantities[0]
    else:
        # Exponential moving average (EMA) with effective alpha
        level = quantities[0]
        for qty in quantities[1:]:
            level = alpha_eff * qty + (1 - alpha_eff) * level
    
    # Fallback if level is zero (no sales at all)
    if level == 0:
        level = 0.1  # Small positive number to avoid division by zero
    
    # Calculate DOW factors if enough data
    if n_samples >= min_samples_for_dow:
        dow_factors = _calculate_dow_factors(dates, quantities, level)
        method = "full"
    elif n_samples >= 7:
        # Partial DOW calculation (may have gaps)
        dow_factors = _calculate_dow_factors_partial(dates, quantities, level)
        method = "simple"
    else:
        # Too few samples: uniform factors
        dow_factors = [1.0] * 7
        method = "fallback"
    
    return {
        "level": level,
        "dow_factors": dow_factors,
        "last_date": dates[-1] if dates else None,
        "n_samples": n_samples,
        "n_censored": n_censored,
        "alpha_eff": alpha_eff,
        "method": method,
    }


def _calculate_dow_factors(
    dates: List[date],
    quantities: List[float],
    level: float,
) -> List[float]:
    """
    Calculate DOW factors (full model with sufficient data).
    
    For each day of week, calculate mean(qty / level) across all samples.
    """
    # Group quantities by day of week
    dow_groups = [[] for _ in range(7)]
    
    for d, qty in zip(dates, quantities):
        dow = d.weekday()  # 0=Monday, 6=Sunday
        dow_groups[dow].append(qty / level if level > 0 else 1.0)
    
    # Calculate mean factor per DOW
    dow_factors = []
    for group in dow_groups:
        if group:
            factor = statistics.mean(group)
        else:
            factor = 1.0  # No data for this DOW, use neutral factor
        dow_factors.append(max(0.1, factor))  # Min 0.1 to avoid zero forecasts
    
    # Normalize factors so mean = 1.0 (preserve overall level)
    mean_factor = statistics.mean(dow_factors)
    if mean_factor > 0:
        dow_factors = [f / mean_factor for f in dow_factors]
    
    return dow_factors


def _calculate_dow_factors_partial(
    dates: List[date],
    quantities: List[float],
    level: float,
) -> List[float]:
    """
    Calculate DOW factors with partial data (7-13 samples).
    
    Uses available data per DOW, fills gaps with 1.0.
    """
    dow_groups = [[] for _ in range(7)]
    
    for d, qty in zip(dates, quantities):
        dow = d.weekday()
        dow_groups[dow].append(qty / level if level > 0 else 1.0)
    
    # Calculate factors where we have data
    dow_factors = []
    for group in dow_groups:
        if len(group) >= 2:  # At least 2 samples for this DOW
            factor = statistics.mean(group)
            dow_factors.append(max(0.1, factor))
        else:
            dow_factors.append(1.0)  # Not enough data, use neutral
    
    return dow_factors


def predict(
    model_state: Dict[str, Any],
    horizon: int,
    start_date: Optional[date] = None,
) -> List[float]:
    """
    Generate forecast for next H days.
    
    Args:
        model_state: Model state from fit_forecast_model()
        horizon: Number of days to forecast (H)
        start_date: Start date for forecast. If None, uses model's last_date + 1.
    
    Returns:
        List of H forecast values (non-negative floats)
    
    Example:
        >>> model = {"level": 10.0, "dow_factors": [1.0] * 7, "last_date": date(2024, 1, 5)}
        >>> forecast = predict(model, horizon=3)
        >>> len(forecast)
        3
        >>> all(f >= 0 for f in forecast)
        True
    """
    level = model_state["level"]
    dow_factors = model_state["dow_factors"]
    last_date = model_state["last_date"]
    
    # Determine start date
    if start_date is None:
        if last_date is None:
            start_date = date.today()
        else:
            start_date = last_date + timedelta(days=1)
    
    # Generate forecast
    forecast = []
    for i in range(horizon):
        forecast_date = start_date + timedelta(days=i)
        dow = forecast_date.weekday()  # 0=Monday, 6=Sunday
        
        # Forecast = level × DOW factor
        value = level * dow_factors[dow]
        
        # Ensure non-negative
        forecast.append(max(0.0, value))
    
    return forecast


def predict_single_day(
    model_state: Dict[str, Any],
    target_date: date,
) -> float:
    """
    Generate forecast for a specific single day.
    
    Args:
        model_state: Model state from fit_forecast_model()
        target_date: Date to forecast
    
    Returns:
        Forecast value (non-negative float)
    
    Example:
        >>> model = {"level": 10.0, "dow_factors": [1.2, 1.0, 1.0, 1.0, 1.0, 0.8, 0.6]}
        >>> predict_single_day(model, date(2024, 1, 8))  # Monday
        12.0
    """
    level = model_state["level"]
    dow_factors = model_state["dow_factors"]
    
    dow = target_date.weekday()
    value = level * dow_factors[dow]
    
    return max(0.0, value)


def get_forecast_stats(model_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get statistical summary of the forecast model.
    
    Args:
        model_state: Model state from fit_forecast_model()
    
    Returns:
        Dict with keys:
            - "level": Base demand level
            - "min_daily_forecast": Minimum expected daily demand
            - "max_daily_forecast": Maximum expected daily demand
            - "mean_daily_forecast": Mean expected daily demand
            - "method": Model method used
            - "n_samples": Number of samples in training data
    """
    level = model_state["level"]
    dow_factors = model_state["dow_factors"]
    
    daily_forecasts = [level * factor for factor in dow_factors]
    
    return {
        "level": level,
        "min_daily_forecast": min(daily_forecasts),
        "max_daily_forecast": max(daily_forecasts),
        "mean_daily_forecast": statistics.mean(daily_forecasts),
        "method": model_state.get("method", "unknown"),
        "n_samples": model_state.get("n_samples", 0),
    }


def validate_forecast_inputs(
    history: List[Dict[str, Any]],
) -> tuple[bool, Optional[str]]:
    """
    Validate forecast input data.
    
    Args:
        history: List of dicts with keys {"date": date, "qty_sold": float}
    
    Returns:
        (is_valid, error_message)
        If is_valid=True, error_message=None
        If is_valid=False, error_message contains explanation
    
    Example:
        >>> history = [{"date": date(2024, 1, 1), "qty_sold": 10}]
        >>> is_valid, error = validate_forecast_inputs(history)
        >>> is_valid
        True
    """
    if not isinstance(history, list):
        return False, "history must be a list"
    
    if not history:
        # Empty history is valid (will use fallback model)
        return True, None
    
    # Check structure
    for i, record in enumerate(history):
        if not isinstance(record, dict):
            return False, f"Record {i} is not a dict"
        
        if "date" not in record:
            return False, f"Record {i} missing 'date' key"
        
        if "qty_sold" not in record:
            return False, f"Record {i} missing 'qty_sold' key"
        
        if not isinstance(record["date"], date):
            return False, f"Record {i} 'date' is not a date object"
        
        try:
            float(record["qty_sold"])
        except (TypeError, ValueError):
            return False, f"Record {i} 'qty_sold' is not numeric"
    
    return True, None


# Convenience function for quick forecasting
def quick_forecast(
    history: List[Dict[str, Any]],
    horizon: int = 7,
) -> Dict[str, Any]:
    """
    Quick one-shot forecasting (fit + predict).
    
    Args:
        history: List of dicts with keys {"date": date, "qty_sold": float}
        horizon: Number of days to forecast
    
    Returns:
        Dict with keys:
            - "forecast": List[float] (H forecast values)
            - "model": Model state
            - "stats": Forecast statistics
    
    Example:
        >>> history = [{"date": date(2024, 1, i), "qty_sold": 10 + i} for i in range(1, 15)]
        >>> result = quick_forecast(history, horizon=7)
        >>> len(result["forecast"])
        7
    """
    # Validate inputs
    is_valid, error = validate_forecast_inputs(history)
    if not is_valid:
        raise ValueError(f"Invalid forecast input: {error}")
    
    # Fit model
    model = fit_forecast_model(history)
    
    # Generate forecast
    forecast = predict(model, horizon)
    
    # Get stats
    stats = get_forecast_stats(model)
    
    return {
        "forecast": forecast,
        "model": model,
        "stats": stats,
    }
