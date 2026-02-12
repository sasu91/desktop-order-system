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

# Import is_day_censored for baseline filtering
try:
    from src.domain.ledger import is_day_censored
except ImportError:
    from domain.ledger import is_day_censored

# Import promo calendar for promo-adjusted forecast
try:
    from src.promo_calendar import is_promo, promo_windows_for_sku
except ImportError:
    from promo_calendar import is_promo, promo_windows_for_sku


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
    
    # Handle case where all days are censored
    if not filtered_history:
        return {
            "level": 0.0,
            "dow_factors": [1.0] * 7,
            "last_date": history[-1]["date"] if history else None,
            "n_samples": 0,
            "n_censored": n_censored,
            "alpha_eff": alpha,
            "method": "fallback",
        }
    
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
        raise ValueError(f"Invalid forecast inputs: {error}")
    
    # Fit model
    model = fit_forecast_model(history)
    
    # Predict
    forecast_values = predict(model, horizon)
    
    # Stats
    stats = get_forecast_stats(model)
    
    return {
        "forecast": forecast_values,
        "model": model,
        "stats": stats,
    }


# ======================================================================
# BASELINE FORECAST (NON-PROMO, NON-CENSORED TRAINING)
# ======================================================================

def baseline_forecast(
    sku_id: str,
    horizon_dates: List[date],
    sales_records: List[Any],  # List[SalesRecord]
    transactions: List[Any],  # List[Transaction]
    asof_date: Optional[date] = None,
    alpha: float = 0.3,
    min_samples_for_dow: int = 14,
    alpha_boost_for_censored: float = 0.0,
) -> Dict[date, float]:
    """
    Generate baseline demand forecast trained ONLY on non-promo, non-censored days.
    
    This function represents the "normal" demand without promotional uplift.
    Training data is filtered to exclude:
    - Days with promo_flag=1 (promotional periods)
    - Days that are censored (OOS/stockout events detected by is_day_censored)
    
    The baseline forecast is generated for ALL dates in horizon_dates,
    regardless of whether those future dates have promos scheduled or not.
    This allows comparison of actual forecast vs baseline to measure promo impact.
    
    Args:
        sku_id: SKU identifier
        horizon_dates: List of future dates to forecast (sorted, oldest first)
        sales_records: List of SalesRecord objects (must have .sku, .date, .qty_sold, .promo_flag)
        transactions: List of Transaction objects (for censored day detection)
        asof_date: AsOf date for stock calculation in censoring logic (defaults to today)
        alpha: Smoothing parameter for exponential moving average (0 < alpha <= 1)
        min_samples_for_dow: Minimum samples needed to compute DOW factors
        alpha_boost_for_censored: Increase alpha for SKUs with censored days
    
    Returns:
        Dict[date, float]: Mapping of each date in horizon_dates to baseline forecast value
                          (non-negative floats, representing expected daily demand)
    
    Example:
        >>> from datetime import date, timedelta
        >>> horizon = [date.today() + timedelta(days=i) for i in range(1, 8)]
        >>> baseline = baseline_forecast(
        ...     sku_id="SKU001",
        ...     horizon_dates=horizon,
        ...     sales_records=sales,
        ...     transactions=txns,
        ... )
        >>> baseline[horizon[0]]  # Baseline forecast for first day
        12.5
    
    Invariant Test:
        If promo_calendar is empty and all sales have promo_flag=0,
        then final forecast (with promo enrichment) MUST equal baseline forecast.
    """
    if asof_date is None:
        asof_date = date.today()
    
    # Filter sales records for this SKU
    sku_sales = [s for s in sales_records if s.sku == sku_id]
    
    # Build training dataset: exclude promo_flag=1 and censored days
    training_history = []
    
    for sale in sku_sales:
        # Skip promotional periods
        if hasattr(sale, 'promo_flag') and sale.promo_flag == 1:
            continue
        
        # Skip censored days (OOS/stockout)
        is_censored, _reason = is_day_censored(
            sku=sku_id,
            check_date=sale.date,
            transactions=transactions,
            sales_records=sales_records,
        )
        if is_censored:
            continue
        
        # Add to training set
        training_history.append({
            "date": sale.date,
            "qty_sold": sale.qty_sold,
        })
    
    # Sort training history by date (fit_forecast_model expects chronological order)
    training_history.sort(key=lambda x: x["date"])
    
    # Fit model on baseline (non-promo, non-censored) data
    model_state = fit_forecast_model(
        history=training_history,
        alpha=alpha,
        min_samples_for_dow=min_samples_for_dow,
        censored_flags=None,  # Already filtered out censored days
        alpha_boost_for_censored=alpha_boost_for_censored,
    )
    
    # Generate baseline forecast for each date in horizon
    baseline_predictions = {}
    
    for forecast_date in horizon_dates:
        baseline_value = predict_single_day(model_state, forecast_date)
        baseline_predictions[forecast_date] = baseline_value
    
    return baseline_predictions


def baseline_forecast_mc(
    sku_id: str,
    horizon_dates: List[date],
    sales_records: List[Any],  # List[SalesRecord]
    transactions: List[Any],  # List[Transaction]
    asof_date: Optional[date] = None,
    distribution: str = "empirical",
    n_simulations: int = 1000,
    random_seed: int = 42,
    output_stat: str = "mean",
    output_percentile: int = 80,
    expected_waste_rate: float = 0.0,
) -> Dict[date, float]:
    """
    Generate baseline demand forecast using Monte Carlo simulation.
    
    Similar to baseline_forecast() but uses Monte Carlo sampling instead of
    deterministic Level+DOW model. Training data is filtered identically:
    - Exclude promo_flag=1 (promotional periods)
    - Exclude censored days (OOS/stockout events)
    
    Args:
        sku_id: SKU identifier
        horizon_dates: List of future dates to forecast (sorted, oldest first)
        sales_records: List of SalesRecord objects
        transactions: List of Transaction objects (for censored day detection)
        asof_date: AsOf date for stock calculation in censoring logic (defaults to today)
        distribution: Distribution type ("empirical", "normal", "lognormal", "residuals")
        n_simulations: Number of Monte Carlo simulation runs
        random_seed: Random seed for reproducibility
        output_stat: Aggregation method ("mean", "median", "percentile")
        output_percentile: Percentile value if output_stat="percentile" (1-99)
        expected_waste_rate: Expected waste rate due to shelf life (0.0-1.0)
    
    Returns:
        Dict[date, float]: Mapping of each date in horizon_dates to baseline forecast value
    
    Example:
        >>> horizon = [date.today() + timedelta(days=i) for i in range(1, 8)]
        >>> baseline_mc = baseline_forecast_mc(
        ...     sku_id="SKU001",
        ...     horizon_dates=horizon,
        ...     sales_records=sales,
        ...     transactions=txns,
        ...     distribution="empirical",
        ...     n_simulations=1000,
        ... )
        >>> baseline_mc[horizon[0]]
        12.8
    """
    if asof_date is None:
        asof_date = date.today()
    
    # Filter sales records for this SKU
    sku_sales = [s for s in sales_records if s.sku == sku_id]
    
    # Build training dataset: exclude promo_flag=1 and censored days
    training_history = []
    
    for sale in sku_sales:
        # Skip promotional periods
        if hasattr(sale, 'promo_flag') and sale.promo_flag == 1:
            continue
        
        # Skip censored days (OOS/stockout)
        is_censored, _reason = is_day_censored(
            sku=sku_id,
            check_date=sale.date,
            transactions=transactions,
            sales_records=sales_records,
        )
        if is_censored:
            continue
        
        # Add to training set
        training_history.append({
            "date": sale.date,
            "qty_sold": sale.qty_sold,
        })
    
    # Sort training history by date
    training_history.sort(key=lambda x: x["date"])
    
    # Run Monte Carlo forecast on baseline data
    horizon_days = len(horizon_dates)
    
    mc_forecast_list = monte_carlo_forecast(
        history=training_history,
        horizon_days=horizon_days,
        distribution=distribution,
        n_simulations=n_simulations,
        random_seed=random_seed,
        output_stat=output_stat,
        output_percentile=output_percentile,
        expected_waste_rate=expected_waste_rate,
    )
    
    # Map forecast list to dates
    baseline_predictions = {}
    
    for i, forecast_date in enumerate(horizon_dates):
        if i < len(mc_forecast_list):
            baseline_predictions[forecast_date] = mc_forecast_list[i]
        else:
            # Fallback in case of mismatch
            baseline_predictions[forecast_date] = 0.0
    
    return baseline_predictions


# ======================================================================
# MONTE CARLO FORECAST ENGINE
# ======================================================================

def monte_carlo_forecast(
    history: List[Dict[str, Any]],
    horizon_days: int,
    distribution: str = "empirical",
    n_simulations: int = 1000,
    random_seed: int = 42,
    output_stat: str = "mean",
    output_percentile: int = 80,
    expected_waste_rate: float = 0.0,  # NEW (Fase 3): % perdite attese da shelf life (0.0-1.0)
) -> List[float]:
    """
    Monte Carlo demand forecasting via simulation.
    
    Approach:
    - Sample historical demand distribution (empirical, normal, lognormal, residuals)
    - Simulate n_simulations demand trajectories over horizon_days
    - Aggregate using mean or percentile
    - Apply waste rate adjustment if shelf life losses expected
    
    Args:
        history: List of dicts {"date": date, "qty_sold": float}
        horizon_days: Forecast horizon (days)
        distribution: Sampling method
            - "empirical": Bootstrap from historical demand (default)
            - "normal": N(μ, σ²) from history
            - "lognormal": LogNormal(μ_log, σ_log²)
            - "residuals": Bootstrap residuals from simple model
        n_simulations: Number of simulation paths (default 1000)
        random_seed: RNG seed (0 = random, >0 = deterministic)
        output_stat: Aggregation method
            - "mean": Average across simulations (default)
            - "percentile": Use specified percentile
        output_percentile: Percentile value if output_stat="percentile" (50-99)
        expected_waste_rate: Expected waste fraction due to shelf life (0.0-1.0)
                            e.g., 0.1 = 10% of demand unusable due to expiry
                            Forecast is reduced by this factor: fc_adj = fc × (1 - waste_rate)
    
    Returns:
        List[float] of length horizon_days with forecasted demand
    
    Example:
        >>> history = [{"date": date(2024, 1, i), "qty_sold": 10 + i % 3} for i in range(1, 31)]
        >>> fc = monte_carlo_forecast(history, horizon_days=7, distribution="empirical")
        >>> len(fc)
        7
    """
    import random
    import numpy as np
    
    # Set random seed for reproducibility
    if random_seed > 0:
        random.seed(random_seed)
        np.random.seed(random_seed)
    
    # Extract quantities
    if not history:
        return [0.0] * horizon_days
    
    quantities = [float(rec["qty_sold"]) for rec in history]
    
    if len(quantities) < 3:
        # Insufficient data: use simple mean
        mean_qty = statistics.mean(quantities) if quantities else 0.0
        return [max(0.0, mean_qty)] * horizon_days
    
    # Initialize simulation paths
    simulations = []  # Shape: (n_simulations, horizon_days)
    
    for _ in range(n_simulations):
        path = []
        
        for day in range(horizon_days):
            if distribution == "empirical":
                # Bootstrap: sample random historical day
                sampled_qty = random.choice(quantities)
            
            elif distribution == "normal":
                # Normal distribution N(μ, σ)
                mean_qty = statistics.mean(quantities)
                std_qty = statistics.stdev(quantities) if len(quantities) > 1 else 0.0
                sampled_qty = np.random.normal(mean_qty, std_qty)
            
            elif distribution == "lognormal":
                # LogNormal for non-negative demand
                positive_quantities = [q for q in quantities if q > 0]
                if not positive_quantities:
                    sampled_qty = 0.0
                else:
                    log_quantities = np.log(positive_quantities)
                    mu_log = np.mean(log_quantities)
                    sigma_log = np.std(log_quantities) if len(log_quantities) > 1 else 0.1
                    sampled_qty = np.random.lognormal(mu_log, sigma_log)
            
            elif distribution == "residuals":
                # Bootstrap residuals from simple model
                simple_model = fit_forecast_model(history, alpha=0.3)
                level = simple_model["level"]
                residuals = [q - level for q in quantities]
                sampled_residual = random.choice(residuals)
                sampled_qty = level + sampled_residual
            
            else:
                raise ValueError(f"Unknown distribution: {distribution}")
            
            # Ensure non-negative
            path.append(max(0.0, sampled_qty))
        
        simulations.append(path)
    
    # Aggregate across simulations
    simulations_array = np.array(simulations)  # Shape: (n_simulations, horizon_days)
    
    if output_stat == "mean":
        forecast_values = np.mean(simulations_array, axis=0).tolist()
    
    elif output_stat == "percentile":
        if not (50 <= output_percentile <= 99):
            raise ValueError(f"output_percentile must be 50-99, got {output_percentile}")
        forecast_values = np.percentile(simulations_array, output_percentile, axis=0).tolist()
    
    else:
        raise ValueError(f"Unknown output_stat: {output_stat}")
    
    # Apply shelf life waste adjustment (Fase 3)
    # If expected_waste_rate > 0, reduce forecast to account for unusable stock
    if expected_waste_rate > 0:
        if not (0.0 <= expected_waste_rate <= 1.0):
            raise ValueError(f"expected_waste_rate must be 0.0-1.0, got {expected_waste_rate}")
        # Reduce forecast: usable demand = total demand × (1 - waste_rate)
        forecast_values = [v * (1.0 - expected_waste_rate) for v in forecast_values]
    
    # Ensure non-negative (already done in path generation, but double-check after waste adjustment)
    forecast_values = [max(0.0, v) for v in forecast_values]
    
    return forecast_values


def monte_carlo_forecast_with_stats(
    history: List[Dict[str, Any]],
    horizon_days: int,
    distribution: str = "empirical",
    n_simulations: int = 1000,
    random_seed: int = 42,
    expected_waste_rate: float = 0.0,  # NEW (Fase 3): % perdite attese da shelf life
) -> Dict[str, Any]:
    """
    Monte Carlo forecast with full statistical summary.
    
    Returns mean, median, and multiple percentiles for each forecast day.
    
    Args:
        Same as monte_carlo_forecast (without output_stat/percentile)
    
    Returns:
        Dict with keys:
            - "mean": List[float] (mean forecast per day)
            - "median": List[float] (50th percentile per day)
            - "p10": List[float] (10th percentile)
            - "p25": List[float] (25th percentile)
            - "p75": List[float] (75th percentile)
            - "p90": List[float] (90th percentile)
            - "p95": List[float] (95th percentile)
            - "n_simulations": int
            - "distribution": str
    
    Example:
        >>> history = [{"date": date(2024, 1, i), "qty_sold": 10 + i % 3} for i in range(1, 31)]
        >>> result = monte_carlo_forecast_with_stats(history, horizon_days=7)
        >>> "mean" in result and "p90" in result
        True
    """
    import random
    import numpy as np
    
    # Set random seed
    if random_seed > 0:
        random.seed(random_seed)
        np.random.seed(random_seed)
    
    # Extract quantities
    if not history:
        zeros = [0.0] * horizon_days
        return {
            "mean": zeros,
            "median": zeros,
            "p10": zeros,
            "p25": zeros,
            "p75": zeros,
            "p90": zeros,
            "p95": zeros,
            "n_simulations": n_simulations,
            "distribution": distribution,
        }
    
    quantities = [float(rec["qty_sold"]) for rec in history]
    
    if len(quantities) < 3:
        mean_qty = statistics.mean(quantities) if quantities else 0.0
        fallback = [max(0.0, mean_qty)] * horizon_days
        return {
            "mean": fallback,
            "median": fallback,
            "p10": fallback,
            "p25": fallback,
            "p75": fallback,
            "p90": fallback,
            "p95": fallback,
            "n_simulations": n_simulations,
            "distribution": distribution,
        }
    
    # Run simulations (same logic as monte_carlo_forecast)
    simulations = []
    
    for _ in range(n_simulations):
        path = []
        
        for day in range(horizon_days):
            if distribution == "empirical":
                sampled_qty = random.choice(quantities)
            elif distribution == "normal":
                mean_qty = statistics.mean(quantities)
                std_qty = statistics.stdev(quantities) if len(quantities) > 1 else 0.0
                sampled_qty = np.random.normal(mean_qty, std_qty)
            elif distribution == "lognormal":
                positive_quantities = [q for q in quantities if q > 0]
                if not positive_quantities:
                    sampled_qty = 0.0
                else:
                    log_quantities = np.log(positive_quantities)
                    mu_log = np.mean(log_quantities)
                    sigma_log = np.std(log_quantities) if len(log_quantities) > 1 else 0.1
                    sampled_qty = np.random.lognormal(mu_log, sigma_log)
            elif distribution == "residuals":
                simple_model = fit_forecast_model(history, alpha=0.3)
                level = simple_model["level"]
                residuals = [q - level for q in quantities]
                sampled_residual = random.choice(residuals)
                sampled_qty = level + sampled_residual
            else:
                raise ValueError(f"Unknown distribution: {distribution}")
            
            path.append(max(0.0, sampled_qty))
        
        simulations.append(path)
    
    # Compute statistics
    simulations_array = np.array(simulations)
    
    # Calculate raw statistics
    mean_fc = np.mean(simulations_array, axis=0).tolist()
    median_fc = np.percentile(simulations_array, 50, axis=0).tolist()
    p10_fc = np.percentile(simulations_array, 10, axis=0).tolist()
    p25_fc = np.percentile(simulations_array, 25, axis=0).tolist()
    p75_fc = np.percentile(simulations_array, 75, axis=0).tolist()
    p90_fc = np.percentile(simulations_array, 90, axis=0).tolist()
    p95_fc = np.percentile(simulations_array, 95, axis=0).tolist()
    
    # Apply shelf life waste adjustment (Fase 3)
    if expected_waste_rate > 0:
        if not (0.0 <= expected_waste_rate <= 1.0):
            raise ValueError(f"expected_waste_rate must be 0.0-1.0, got {expected_waste_rate}")
        waste_factor = 1.0 - expected_waste_rate
        mean_fc = [v * waste_factor for v in mean_fc]
        median_fc = [v * waste_factor for v in median_fc]
        p10_fc = [v * waste_factor for v in p10_fc]
        p25_fc = [v * waste_factor for v in p25_fc]
        p75_fc = [v * waste_factor for v in p75_fc]
        p90_fc = [v * waste_factor for v in p90_fc]
        p95_fc = [v * waste_factor for v in p95_fc]
    
    return {
        "mean": [max(0.0, v) for v in mean_fc],
        "median": [max(0.0, v) for v in median_fc],
        "p10": [max(0.0, v) for v in p10_fc],
        "p25": [max(0.0, v) for v in p25_fc],
        "p75": [max(0.0, v) for v in p75_fc],
        "p90": [max(0.0, v) for v in p90_fc],
        "p95": [max(0.0, v) for v in p95_fc],
        "n_simulations": n_simulations,
        "distribution": distribution,
    }

    stats = get_forecast_stats(model)
    
    return {
        "forecast": forecast,
        "model": model,
        "stats": stats,
    }


# ======================================================================
# PROMO-ADJUSTED FORECAST (BASELINE × UPLIFT)
# ======================================================================

def promo_adjusted_forecast(
    sku_id: str,
    horizon_dates: List[date],
    sales_records: List[Any],  # List[SalesRecord]
    transactions: List[Any],  # List[Transaction]
    promo_windows: List[Any],  # List[PromoWindow]
    all_skus: List[Any],  # List[SKU] (for category/department pooling in uplift)
    csv_layer: Any,  # CSVLayer instance (for settings)
    store_id: Optional[str] = None,
    asof_date: Optional[date] = None,
    alpha: float = 0.3,
    min_samples_for_dow: int = 14,
    alpha_boost_for_censored: float = 0.0,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate promo-adjusted demand forecast by applying uplift to baseline.
    
    This function applies promotional uplift effects to baseline demand forecasts
    for dates that fall within promotional periods. The uplift is calculated by
    calling estimate_uplift() from promo_uplift module.
    
    Optional smoothing (disabled by default) can apply progressive ramp-in/ramp-out
    multipliers at promo calendar borders to avoid sudden demand jumps.
    
    Design Invariants:
    - If promo_adjustment.enabled = false, adjusted = baseline for all dates
    - If no promo active on date, adjusted = baseline
    - If promo active but uplift estimation fails, adjusted = baseline (safety fallback)
    - Smoothing only affects calendar borders (first/last N days of promo window)
    
    Args:
        sku_id: SKU identifier
        horizon_dates: List of future dates to forecast (sorted, oldest first)
        sales_records: List of SalesRecord objects
        transactions: List of Transaction objects
        promo_windows: List of PromoWindow objects (for is_promo checks)
        all_skus: List of SKU objects (for category/department lookups in uplift estimation)
        csv_layer: CSVLayer instance (for settings loading)
        store_id: Optional store ID for store-specific promo windows (None = global only)
        asof_date: AsOf date for stock calculation in censoring logic (defaults to today)
        alpha: Smoothing parameter for baseline forecast
        min_samples_for_dow: Minimum samples for DOW factors in baseline
        alpha_boost_for_censored: Alpha boost for SKUs with censored history
        settings: Settings dict (defaults to csv_layer.read_settings() if None)
    
    Returns:
        Dict with keys:
            - "baseline_forecast": Dict[date, float] - baseline demand (non-promo)
            - "adjusted_forecast": Dict[date, float] - promo-adjusted demand
            - "promo_active": Dict[date, bool] - whether promo was active on each date
            - "uplift_factor": Dict[date, float] - uplift factor applied (1.0 if no promo)
            - "smoothing_multiplier": Dict[date, float] - smoothing ramp multiplier (1.0 if no smoothing)
            - "adjustment_enabled": bool - whether promo adjustment is enabled globally
            - "smoothing_enabled": bool - whether smoothing is enabled
            - "uplift_report": Optional[UpliftReport] - full uplift estimation report (if promo active)
    
    Example:
        >>> from datetime import date, timedelta
        >>> horizon = [date.today() + timedelta(days=i) for i in range(1, 8)]
        >>> all_skus = csv_layer.read_skus()
        >>> result = promo_adjusted_forecast(
        ...     sku_id="SKU001",
        ...     horizon_dates=horizon,
        ...     sales_records=sales,
        ...     transactions=txns,
        ...     promo_windows=promo_windows,
        ...     all_skus=all_skus,
        ...     csv_layer=csv_layer,
        ... )
        >>> result["adjusted_forecast"][horizon[0]]  # Adjusted forecast for first day
        15.3
        >>> result["uplift_factor"][horizon[0]]  # Uplift factor applied
        1.22
    """
    if asof_date is None:
        asof_date = date.today()
    
    # Load settings (promo_adjustment section)
    if settings is None:
        settings = csv_layer.read_settings()
    
    promo_adj_settings = settings.get("promo_adjustment", {})
    adjustment_enabled = promo_adj_settings.get("enabled", {}).get("value", False)
    smoothing_enabled = promo_adj_settings.get("smoothing_enabled", {}).get("value", False)
    ramp_in_days = promo_adj_settings.get("ramp_in_days", {}).get("value", 0)
    ramp_out_days = promo_adj_settings.get("ramp_out_days", {}).get("value", 0)
    
    # Generate baseline forecast (non-promo demand)
    baseline_predictions = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon_dates,
        sales_records=sales_records,
        transactions=transactions,
        asof_date=asof_date,
        alpha=alpha,
        min_samples_for_dow=min_samples_for_dow,
        alpha_boost_for_censored=alpha_boost_for_censored,
    )
    
    # Initialize result maps
    adjusted_predictions = {}
    promo_active_map = {}
    uplift_factor_map = {}
    smoothing_multiplier_map = {}
    uplift_report = None
    
    # If adjustment disabled, return baseline as adjusted
    if not adjustment_enabled:
        for forecast_date in horizon_dates:
            adjusted_predictions[forecast_date] = baseline_predictions[forecast_date]
            promo_active_map[forecast_date] = False
            uplift_factor_map[forecast_date] = 1.0
            smoothing_multiplier_map[forecast_date] = 1.0
        
        return {
            "baseline_forecast": baseline_predictions,
            "adjusted_forecast": adjusted_predictions,
            "promo_active": promo_active_map,
            "uplift_factor": uplift_factor_map,
            "smoothing_multiplier": smoothing_multiplier_map,
            "adjustment_enabled": False,
            "smoothing_enabled": smoothing_enabled,
            "uplift_report": None,
        }
    
    # Check if any date in horizon has promo active
    # Filter promo_windows: if store_id=None (global only mode), exclude store-specific promos
    filtered_promo_windows = promo_windows
    if store_id is None:
        # Global-only mode: exclude store-specific promo windows
        filtered_promo_windows = [w for w in promo_windows if w.store_id is None]
    
    any_promo_active = False
    for forecast_date in horizon_dates:
        promo_active = is_promo(
            check_date=forecast_date,
            sku=sku_id,
            promo_windows=filtered_promo_windows,
            store_id=None,  # Already filtered, pass None
        )
        promo_active_map[forecast_date] = promo_active
        if promo_active:
            any_promo_active = True
    
    # If no promo in horizon, return baseline as adjusted
    if not any_promo_active:
        for forecast_date in horizon_dates:
            adjusted_predictions[forecast_date] = baseline_predictions[forecast_date]
            uplift_factor_map[forecast_date] = 1.0
            smoothing_multiplier_map[forecast_date] = 1.0
        
        return {
            "baseline_forecast": baseline_predictions,
            "adjusted_forecast": adjusted_predictions,
            "promo_active": promo_active_map,
            "uplift_factor": uplift_factor_map,
            "smoothing_multiplier": smoothing_multiplier_map,
            "adjustment_enabled": True,
            "smoothing_enabled": smoothing_enabled,
            "uplift_report": None,
        }
    
    # Estimate uplift factor (live calculation, no caching - user decision)
    try:
        # Import here to avoid circular dependency (promo_uplift imports baseline_forecast)
        try:
            from src.domain.promo_uplift import estimate_uplift
        except ImportError:
            from domain.promo_uplift import estimate_uplift
        
        uplift_report = estimate_uplift(
            sku_id=sku_id,
            all_skus=all_skus,
            promo_windows=promo_windows,
            sales_records=sales_records,
            transactions=transactions,
            settings=settings,
        )
        uplift_factor = uplift_report.uplift_factor
    except Exception as e:
        # Fallback: if uplift estimation fails, use 1.0 (no adjustment, return baseline)
        import logging
        logging.warning(f"Uplift estimation failed for SKU {sku_id}: {e}. Using uplift=1.0 (baseline).")
        uplift_factor = 1.0
        uplift_report = None
    
    # Apply uplift to each date in horizon
    for forecast_date in horizon_dates:
        baseline_value = baseline_predictions[forecast_date]
        
        # If promo not active on this date, use baseline
        if not promo_active_map[forecast_date]:
            adjusted_predictions[forecast_date] = baseline_value
            uplift_factor_map[forecast_date] = 1.0
            smoothing_multiplier_map[forecast_date] = 1.0
            continue
        
        # Calculate smoothing multiplier (progressive ramp-in/ramp-out)
        smoothing_multiplier = 1.0
        if smoothing_enabled and (ramp_in_days > 0 or ramp_out_days > 0):
            # Find promo window containing this date
            promo_window = _find_promo_window_for_date(
                forecast_date, sku_id, promo_windows, store_id
            )
            
            if promo_window is not None:
                days_from_start = (forecast_date - promo_window.start_date).days
                days_from_end = (promo_window.end_date - forecast_date).days
                
                # Ramp-in: progressive increase from 0.0 to 1.0
                if ramp_in_days > 0 and days_from_start < ramp_in_days:
                    smoothing_multiplier = (days_from_start + 1) / (ramp_in_days + 1)
                
                # Ramp-out: progressive decrease from 1.0 to 0.0
                elif ramp_out_days > 0 and days_from_end < ramp_out_days:
                    smoothing_multiplier = (days_from_end + 1) / (ramp_out_days + 1)
        
        # Apply uplift and smoothing to baseline
        adjusted_value = baseline_value * uplift_factor * smoothing_multiplier
        
        adjusted_predictions[forecast_date] = max(0.0, adjusted_value)
        uplift_factor_map[forecast_date] = uplift_factor
        smoothing_multiplier_map[forecast_date] = smoothing_multiplier
    
    return {
        "baseline_forecast": baseline_predictions,
        "adjusted_forecast": adjusted_predictions,
        "promo_active": promo_active_map,
        "uplift_factor": uplift_factor_map,
        "smoothing_multiplier": smoothing_multiplier_map,
        "adjustment_enabled": True,
        "smoothing_enabled": smoothing_enabled,
        "uplift_report": uplift_report,
    }


def _find_promo_window_for_date(
    check_date: date,
    sku_id: str,
    promo_windows: List[Any],
    store_id: Optional[str] = None,
) -> Optional[Any]:
    """
    Find the promo window that contains a specific date for a SKU.
    
    Args:
        check_date: Date to check
        sku_id: SKU identifier
        promo_windows: List of PromoWindow objects
        store_id: Optional store ID filter (None = global only)
    
    Returns:
        PromoWindow object if found, else None
    """
    for window in promo_windows:
        # SKU filter
        if window.sku != sku_id:
            continue
        
        # Store filter (None = global only, per user decision)
        if store_id is None and window.store_id is not None:
            continue
        if store_id is not None and window.store_id != store_id:
            continue
        
        # Date range check
        if window.start_date <= check_date <= window.end_date:
            return window
    
    return None

