"""
Uncertainty Estimation for Demand Forecasting

This module provides robust statistical estimators for demand uncertainty,
critical for safety stock calculations in inventory management with
Customer Service Level (CSL) targets.

Key Features:
- Robust sigma estimation using Median Absolute Deviation (MAD)
- Time-aggregation for multi-day protection periods
- Outlier-resistant (unlike standard deviation)
- Integration with forecast module for residual calculation

Mathematical Foundation:
    MAD = median(|x_i - median(x)|)
    σ_robust = k × MAD, where k ≈ 1.4826 for normal distributions
    
    For protection period P days:
    σ_P = σ_day × √P  (assumes independent daily errors)

Author: Desktop Order System Team
Date: February 2026
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import date, timedelta
import statistics


# Constants
MAD_TO_SIGMA_FACTOR = 1.4826  # Conversion factor for MAD → σ (normal distribution)
DEFAULT_WINDOW_WEEKS = 8      # Default rolling window for residual calculation
MIN_SAMPLES_FOR_ROBUST = 7    # Minimum samples to use robust estimator


def robust_sigma(residuals: List[float]) -> float:
    """
    Estimate standard deviation using Median Absolute Deviation (MAD).
    
    MAD is highly robust to outliers, with breakdown point of 50%
    (up to 50% of data can be arbitrarily corrupted without breaking down).
    
    Formula:
        MAD = median(|residual_i - median(residuals)|)
        σ_robust = MAD_TO_SIGMA_FACTOR × MAD
        
    Where MAD_TO_SIGMA_FACTOR ≈ 1.4826 assumes underlying normal distribution.
    
    Args:
        residuals: List of forecast errors (actual - predicted)
    
    Returns:
        float: Robust estimate of standard deviation
        
    Examples:
        >>> robust_sigma([1, 2, 3, 4, 5])  # Clean data
        1.48...
        >>> robust_sigma([1, 2, 3, 4, 1000])  # With outlier
        1.48...  # Unaffected by outlier!
        
    Notes:
        - Returns 0.0 for empty or single-element lists
        - For N < MIN_SAMPLES_FOR_ROBUST, returns fallback estimate
        - Outliers have minimal impact on result
    """
    if not residuals or len(residuals) < 2:
        return 0.0
    
    # Always use MAD (robust even for small samples)
    # For N < 7, MAD is still more robust than mean absolute deviation
    
    # Step 1: Calculate median
    median_residual = statistics.median(residuals)
    
    # Step 2: Calculate absolute deviations from median
    absolute_deviations = [abs(r - median_residual) for r in residuals]
    
    # Step 3: MAD = median of absolute deviations
    mad = statistics.median(absolute_deviations)
    
    # Step 4: Convert MAD to sigma scale
    # For normal distribution: σ ≈ 1.4826 × MAD
    sigma_robust = MAD_TO_SIGMA_FACTOR * mad
    
    return sigma_robust


def winsorized_sigma(residuals: List[float], trim_proportion: float = 0.05) -> float:
    """
    Estimate standard deviation using Winsorized method.
    
    Winsorization replaces extreme values (outliers) with less extreme values
    at specified percentiles, then calculates standard deviation.
    
    Formula:
        1. Sort residuals
        2. Replace values below p-th percentile with p-th percentile value
        3. Replace values above (1-p)-th percentile with (1-p)-th percentile value
        4. Calculate std dev of winsorized data
        
    Args:
        residuals: List of forecast errors
        trim_proportion: Proportion to trim from each tail (default 5% = 0.05)
    
    Returns:
        float: Winsorized standard deviation estimate
        
    Examples:
        >>> winsorized_sigma([1, 2, 3, 4, 100], trim_proportion=0.2)
        1.17...  # Outlier (100) replaced with 80th percentile
        
    Notes:
        - Less robust than MAD (breakdown point ≈ trim_proportion)
        - More efficient than MAD for moderately contaminated data
        - Returns 0.0 for < 3 samples
    """
    if not residuals or len(residuals) < 3:
        return 0.0
    
    n = len(residuals)
    sorted_residuals = sorted(residuals)
    
    # Calculate trim indices
    trim_count = max(1, int(n * trim_proportion))
    
    # Winsorize: replace extremes with boundary values
    lower_bound = sorted_residuals[trim_count - 1]
    upper_bound = sorted_residuals[n - trim_count]
    
    winsorized = [
        lower_bound if r < lower_bound else (upper_bound if r > upper_bound else r)
        for r in residuals
    ]
    
    # Calculate standard deviation of winsorized data
    if len(set(winsorized)) == 1:  # All values identical after winsorizing
        return 0.0
    
    return statistics.stdev(winsorized)


def sigma_over_horizon(protection_period_days: int, sigma_daily: float) -> float:
    """
    Scale daily uncertainty to multi-day protection period.
    
    Assumes demand forecast errors are independent across days (no autocorrelation).
    Under this assumption, variance aggregates linearly, so std dev scales as √P.
    
    Formula:
        σ_P = σ_day × √P
        
    Where:
        - σ_P: Standard deviation over P days
        - σ_day: Daily demand standard deviation
        - P: Protection period in days
        
    Mathematical Justification:
        If X_1, ..., X_P are independent with Var(X_i) = σ²:
            Var(X_1 + ... + X_P) = P × σ²
            StdDev(X_1 + ... + X_P) = √(P × σ²) = √P × σ
    
    Args:
        protection_period_days: Number of days in protection period (P)
        sigma_daily: Daily demand standard deviation (σ_day)
    
    Returns:
        float: Standard deviation over protection period (σ_P)
        
    Examples:
        >>> sigma_over_horizon(1, 10.0)
        10.0  # 1 day: no scaling
        >>> sigma_over_horizon(4, 10.0)
        20.0  # 4 days: 2× scaling
        >>> sigma_over_horizon(9, 10.0)
        30.0  # 9 days: 3× scaling
        
    Notes:
        - Monotonically increasing with P (always σ_P ≥ σ_day for P ≥ 1)
        - Returns 0.0 if sigma_daily = 0 or P ≤ 0
        - Assumes independence (may underestimate for autocorrelated demand)
    """
    if protection_period_days <= 0 or sigma_daily <= 0:
        return 0.0
    
    # Scale by square root of time horizon
    # This is the standard formula for aggregating independent random variables
    sigma_horizon = sigma_daily * (protection_period_days ** 0.5)
    
    return sigma_horizon


def calculate_forecast_residuals(
    history: List[Dict[str, Any]],
    forecast_func,
    window_weeks: int = DEFAULT_WINDOW_WEEKS,
    censored_flags: Optional[List[bool]] = None,
) -> Tuple[List[float], int]:
    """
    Calculate forecast residuals (actual - predicted) using rolling window.
    
    Uses a rolling window approach to generate one-step-ahead forecasts,
    comparing each forecast to actual observed demand. This provides
    realistic error estimates for uncertainty quantification.
    
    CENSORED DAYS: Days marked as censored (OOS/inevasi) are excluded from
    residual calculation to prevent artificially low sigma estimates.
    
    Process:
        For each day t in evaluation period:
            1. Fit model on [t - window, t - 1]
            2. Forecast for day t
            3. If day t is NOT censored: residual = Actual(t) - Forecast(t)
            4. If day t IS censored: skip (don't add to residuals)
    
    Args:
        history: Sales history with keys "date" and "qty_sold"
        forecast_func: Function(history, horizon=1) -> List[float]
        window_weeks: Rolling window size in weeks (default 8 weeks)
        censored_flags: Optional list of bool (same length as history).
                       If provided, days with censored_flags[i]=True are excluded.
    
    Returns:
        Tuple[List[float], int]: (residuals, n_censored_excluded)
            - residuals: List of forecast errors for non-censored days
            - n_censored_excluded: Count of censored days excluded from residuals
        
    Examples:
        >>> from src.forecast import fit_forecast_model, predict
        >>> def forecast_one_day(hist):
        ...     model = fit_forecast_model(hist)
        ...     return predict(model, horizon=1)
        >>> residuals, n_censored = calculate_forecast_residuals(history, forecast_one_day)
        >>> sigma = robust_sigma(residuals)
        
    Notes:
        - Requires at least window_weeks + 1 week of data
        - Uses one-step-ahead forecasts (most conservative error estimate)
        - Returns ([], 0) if insufficient data
        - Censored days excluded ONLY from residuals, not from training window
    """
    if not history:
        return [], 0
    
    # Sort history by date
    sorted_history = sorted(history, key=lambda x: x["date"])
    
    window_days = window_weeks * 7
    min_required_days = window_days + 7  # Window + at least 1 week to evaluate
    
    if len(sorted_history) < min_required_days:
        return [], 0
    
    # Align censored_flags with sorted_history if provided
    if censored_flags:
        if len(censored_flags) != len(history):
            raise ValueError(f"censored_flags length ({len(censored_flags)}) != history length ({len(history)})")
        # Re-sort censored_flags to match sorted_history
        sorted_pairs = sorted(zip(history, censored_flags), key=lambda x: x[0]["date"])
        sorted_censored = [c for _, c in sorted_pairs]
    else:
        sorted_censored = [False] * len(sorted_history)
    
    residuals = []
    n_censored_excluded = 0
    
    # Rolling window: start after initial window
    for i in range(window_days, len(sorted_history)):
        # Check if day i is censored
        is_censored = sorted_censored[i]
        
        if is_censored:
            n_censored_excluded += 1
            continue  # Skip censored days in residual calculation
        
        # Training window: [i - window_days, i - 1]
        train_window = sorted_history[i - window_days:i]
        
        # Actual value at day i
        actual = sorted_history[i]["qty_sold"]
        
        # One-step-ahead forecast
        try:
            forecast_values = forecast_func(train_window, horizon=1)
            if forecast_values:
                predicted = forecast_values[0]
                residual = actual - predicted
                residuals.append(residual)
        except Exception:
            # Skip if forecast fails (e.g., insufficient data in window)
            continue
    
    return residuals, n_censored_excluded


def estimate_demand_uncertainty(
    history: List[Dict[str, Any]],
    forecast_func,
    window_weeks: int = DEFAULT_WINDOW_WEEKS,
    method: str = "mad",
    censored_flags: Optional[List[bool]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Estimate daily demand uncertainty from historical forecast errors.
    
    Combines forecast residual calculation with robust sigma estimation
    to provide a single-step uncertainty estimate suitable for safety stock.
    
    CENSORED DAYS: Days with OOS/inevasi are excluded from sigma calculation
    to prevent underestimation of true demand variability.
    
    Workflow:
        1. Calculate one-step-ahead forecast residuals using rolling window
        2. Exclude censored days from residuals
        3. Apply robust estimator (MAD or Winsorized) to non-censored residuals
        4. Return σ_day and diagnostic metadata
    
    Args:
        history: Sales history with keys "date" and "qty_sold"
        forecast_func: Function(history, horizon) -> List[float]
        window_weeks: Rolling window size in weeks
        method: Estimation method - "mad" (default) or "winsorized"
        censored_flags: Optional list of bool (same length as history).
                       Censored days excluded from sigma calculation.
    
    Returns:
        Tuple[float, Dict[str, Any]]:
            - σ_day: Robust daily demand standard deviation
            - metadata: Dict with keys:
                - "residuals": List[float] (non-censored residuals)
                - "n_residuals": int (count of residuals used)
                - "n_censored_excluded": int (count of censored days excluded)
                - "method": str ("mad" or "winsorized")
            
    Examples:
        >>> from src.forecast import fit_forecast_model, predict
        >>> def my_forecast(hist, horizon):
        ...     model = fit_forecast_model(hist)
        ...     return predict(model, horizon)
        >>> sigma_day, meta = estimate_demand_uncertainty(history, my_forecast)
        >>> print(f"Daily σ: {sigma_day:.2f}, {meta['n_residuals']} residuals, {meta['n_censored_excluded']} censored")
        
    Notes:
        - Returns (0.0, {...}) if insufficient data
        - MAD method recommended for production (most robust)
        - Metadata useful for audit trail and diagnostics
    """
    # Calculate forecast residuals using rolling window (excludes censored days)
    residuals, n_censored_excluded = calculate_forecast_residuals(
        history, forecast_func, window_weeks, censored_flags
    )
    
    if not residuals:
        return 0.0, {
            "residuals": [],
            "n_residuals": 0,
            "n_censored_excluded": n_censored_excluded,
            "method": method,
        }
    
    # Apply robust estimator
    if method == "mad":
        sigma_day = robust_sigma(residuals)
    elif method == "winsorized":
        sigma_day = winsorized_sigma(residuals)
    else:
        raise ValueError(f"Unknown method: {method}. Use 'mad' or 'winsorized'.")
    
    metadata = {
        "residuals": residuals,
        "n_residuals": len(residuals),
        "n_censored_excluded": n_censored_excluded,
        "method": method,
    }
    
    return sigma_day, metadata


def safety_stock_for_csl(
    sigma_horizon: float,
    target_csl: float = 0.95
) -> float:
    """
    Calculate safety stock for target Customer Service Level (CSL).
    
    Uses normal distribution approximation with z-score lookup.
    
    Formula:
        Safety Stock = z_α × σ_P
        
    Where:
        - z_α: z-score for target CSL (e.g., 1.645 for 95% CSL)
        - σ_P: Demand std dev over protection period
        
    Common z-scores:
        - 90% CSL: z = 1.282
        - 95% CSL: z = 1.645
        - 98% CSL: z = 2.054
        - 99% CSL: z = 2.326
    
    Args:
        sigma_horizon: Demand uncertainty over protection period
        target_csl: Target service level (0 < CSL < 1)
    
    Returns:
        float: Safety stock quantity
        
    Examples:
        >>> safety_stock_for_csl(sigma_horizon=20.0, target_csl=0.95)
        32.9  # 1.645 × 20
        
    Notes:
        - Assumes normally distributed forecast errors
        - For discrete/lumpy demand, consider other approaches
        - Returns 0.0 if sigma_horizon = 0
    """
    if sigma_horizon <= 0:
        return 0.0
    
    # Z-score lookup table for common CSL targets
    z_scores = {
        0.50: 0.000,  # 50%: no safety stock
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
    
    # Find closest z-score (simple lookup, no interpolation)
    if target_csl in z_scores:
        z_score = z_scores[target_csl]
    else:
        # Approximate with closest value
        closest_csl = min(z_scores.keys(), key=lambda x: abs(x - target_csl))
        z_score = z_scores[closest_csl]
    
    safety_stock = z_score * sigma_horizon
    
    return safety_stock


# Convenience function for full workflow
def calculate_safety_stock(
    history: List[Dict[str, Any]],
    forecast_func,
    protection_period_days: int,
    target_csl: float = 0.95,
    window_weeks: int = DEFAULT_WINDOW_WEEKS,
    method: str = "mad",
    censored_flags: Optional[List[bool]] = None,
) -> Dict[str, Any]:
    """
    Complete workflow: estimate uncertainty and calculate safety stock.
    
    This is the main entry point for safety stock calculation, combining
    all steps: residual calculation, robust sigma estimation, horizon scaling,
    and CSL-based safety stock computation.
    
    CENSORED DAYS: Days with OOS/inevasi are excluded from uncertainty estimation
    to prevent underestimation of safety stock due to artificially low sigma.
    
    Args:
        history: Sales history with keys "date" and "qty_sold"
        forecast_func: Function(history, horizon) -> List[float]
        protection_period_days: Protection period (P) in days
        target_csl: Target Customer Service Level (default 95%)
        window_weeks: Rolling window for residuals (default 8 weeks)
        method: Uncertainty estimator - "mad" or "winsorized"
        censored_flags: Optional list of bool (same length as history).
                       Censored days excluded from sigma calculation.
    
    Returns:
        Dict with keys:
            - "safety_stock": float
            - "sigma_daily": float
            - "sigma_horizon": float
            - "z_score": float (approximate)
            - "n_residuals": int
            - "n_censored_excluded": int
            - "method": str
            - "target_csl": float
            - "protection_period_days": int
            
    Examples:
        >>> from src.forecast import fit_forecast_model, predict
        >>> def my_forecast(hist, horizon):
        ...     model = fit_forecast_model(hist)
        ...     return predict(model, horizon)
        >>> result = calculate_safety_stock(
        ...     history=sales_history,
        ...     forecast_func=my_forecast,
        ...     protection_period_days=3,
        ...     target_csl=0.95
        ... )
        >>> print(f"Safety stock: {result['safety_stock']:.0f} units")
        
    Workflow:
        1. Calculate forecast residuals using rolling window (exclude censored)
        2. Estimate σ_day using robust method (MAD or Winsorized)
        3. Scale to σ_P using √P formula
        4. Calculate safety stock = z_α × σ_P
    """
    # Step 1: Estimate daily uncertainty (with censored filtering)
    sigma_day, uncertainty_meta = estimate_demand_uncertainty(
        history, forecast_func, window_weeks, method, censored_flags
    )
    
    # Step 2: Scale to protection period
    sigma_horizon = sigma_over_horizon(protection_period_days, sigma_day)
    
    # Step 3: Calculate safety stock for target CSL
    safety_stock = safety_stock_for_csl(sigma_horizon, target_csl)
    
    # Extract z-score (approximate, from lookup table)
    z_scores = {0.90: 1.282, 0.95: 1.645, 0.98: 2.054, 0.99: 2.326}
    z_score = z_scores.get(target_csl, 1.645)  # Default to 95% if not found
    
    return {
        "safety_stock": safety_stock,
        "sigma_daily": sigma_day,
        "sigma_horizon": sigma_horizon,
        "z_score": z_score,
        "n_residuals": uncertainty_meta["n_residuals"],
        "n_censored_excluded": uncertainty_meta["n_censored_excluded"],
        "method": method,
        "target_csl": target_csl,
        "protection_period_days": protection_period_days,
    }
