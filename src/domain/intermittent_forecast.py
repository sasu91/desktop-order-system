"""
Intermittent demand forecasting methods: Croston, SBA, TSB.

Purpose:
- Handle SKUs with frequent zero demand (intermittent patterns)
- Provide transparent, testable implementations
- Integrate with OOS censoring logic
- Support backtest-based method selection

Key concepts:
- ADI (Average Demand Interval): avg days between non-zero demands
- CV² (squared coefficient of variation): variance indicator
- Croston: separate exponential smoothing for intervals and sizes
- SBA (Syntetos-Boylan Approximation): bias-corrected Croston
- TSB (Teunter-Syntetos-Babai): handles obsolescence/trend better

Author: Desktop Order System Team
Date: February 2026
"""

from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from datetime import date
import numpy as np


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntermittentModel:
    """
    Fitted intermittent model parameters.
    
    Attributes:
        method: 'croston', 'sba', or 'tsb'
        alpha: smoothing parameter (0 < alpha <= 1)
        p_t: final smoothed interval between non-zero demands
        z_t: final smoothed size of non-zero demands
        b_t: (TSB only) final smoothed probability of demand occurrence
        n_nonzero: number of non-zero observations used in fit
        n_total: total observations (including zeros)
        n_censored: observations excluded due to OOS censoring
    """
    method: str
    alpha: float
    p_t: float
    z_t: float
    b_t: Optional[float] = None  # TSB only
    n_nonzero: int = 0
    n_total: int = 0
    n_censored: int = 0


@dataclass(frozen=True)
class IntermittentClassification:
    """
    Classification of demand pattern as intermittent.
    
    Attributes:
        is_intermittent: True if ADI and CV² thresholds met
        adi: Average Demand Interval (days between non-zero demands)
        cv2: Squared coefficient of variation
        n_nonzero: count of non-zero demands
        n_total: total observations
        n_censored: excluded due to OOS
    """
    is_intermittent: bool
    adi: float
    cv2: float
    n_nonzero: int
    n_total: int
    n_censored: int


@dataclass(frozen=True)
class BacktestResult:
    """
    Backtest performance metrics for a method.
    
    Attributes:
        method: method name tested
        wmape: weighted mean absolute percentage error
        bias: mean forecast error (positive = over-forecast)
        n_forecasts: number of forecasts evaluated
        n_observations: total observations in test
    """
    method: str
    wmape: float
    bias: float
    n_forecasts: int
    n_observations: int


# ---------------------------------------------------------------------------
# Core intermittent methods
# ---------------------------------------------------------------------------

def fit_croston(
    series: List[float],
    alpha: float = 0.1,
    exclude_indices: Optional[List[int]] = None
) -> IntermittentModel:
    """
    Fit Croston's method for intermittent demand.
    
    Croston smooths two components separately:
    - p (interval): time between non-zero demands
    - z (size): magnitude of non-zero demands
    
    Forecast = z_t / p_t (demand per period)
    
    Args:
        series: demand observations (zeros allowed)
        alpha: smoothing constant (0 < alpha <= 1)
        exclude_indices: indices to exclude (OOS censored days)
    
    Returns:
        IntermittentModel with fitted parameters
    
    Raises:
        ValueError: if no non-zero demands after censoring
    """
    if not 0 < alpha <= 1:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")
    
    # Apply censoring
    exclude_set = set(exclude_indices or [])
    clean_series = [series[i] for i in range(len(series)) if i not in exclude_set]
    
    if len(clean_series) == 0:
        raise ValueError("No observations after censoring")
    
    # Find non-zero demands
    nonzero_indices = [i for i, x in enumerate(clean_series) if x > 0]
    n_nonzero = len(nonzero_indices)
    
    if n_nonzero == 0:
        # All zeros - return model with default values (high interval, zero size)
        return IntermittentModel(
            method="croston",
            alpha=alpha,
            p_t=len(clean_series),  # entire period is one interval
            z_t=0.0,
            n_nonzero=0,
            n_total=len(clean_series),
            n_censored=len(exclude_set)
        )
    
    # Initialize with first non-zero demand
    first_idx = nonzero_indices[0]
    p_t = first_idx + 1  # interval to first demand
    z_t = clean_series[first_idx]
    
    # Smooth intervals and sizes
    last_nonzero_idx = first_idx
    
    for idx in nonzero_indices[1:]:
        interval = idx - last_nonzero_idx
        demand_size = clean_series[idx]
        
        # Update smoothed interval
        p_t = alpha * interval + (1 - alpha) * p_t
        
        # Update smoothed size
        z_t = alpha * demand_size + (1 - alpha) * z_t
        
        last_nonzero_idx = idx
    
    return IntermittentModel(
        method="croston",
        alpha=alpha,
        p_t=max(p_t, 0.1),  # prevent division by zero
        z_t=z_t,
        n_nonzero=n_nonzero,
        n_total=len(clean_series),
        n_censored=len(exclude_set)
    )


def fit_sba(
    series: List[float],
    alpha: float = 0.1,
    exclude_indices: Optional[List[int]] = None
) -> IntermittentModel:
    """
    Fit SBA (Syntetos-Boylan Approximation) method.
    
    SBA corrects Croston's positive bias:
    Forecast = (1 - alpha/2) * z_t / p_t
    
    Args:
        series: demand observations
        alpha: smoothing constant
        exclude_indices: OOS censored indices
    
    Returns:
        IntermittentModel with SBA parameters (same as Croston, bias correction applied in predict)
    """
    model = fit_croston(series, alpha, exclude_indices)
    return IntermittentModel(
        method="sba",
        alpha=model.alpha,
        p_t=model.p_t,
        z_t=model.z_t,
        n_nonzero=model.n_nonzero,
        n_total=model.n_total,
        n_censored=model.n_censored
    )


def fit_tsb(
    series: List[float],
    alpha_demand: float = 0.1,
    alpha_probability: float = 0.1,
    exclude_indices: Optional[List[int]] = None
) -> IntermittentModel:
    """
    Fit TSB (Teunter-Syntetos-Babai) method.
    
    TSB models:
    - Probability of demand occurrence (b_t)
    - Size of demand when it occurs (z_t)
    
    Forecast = b_t * z_t
    
    Better for obsolescence/declining demand patterns.
    
    Args:
        series: demand observations
        alpha_demand: smoothing for demand size
        alpha_probability: smoothing for demand probability
        exclude_indices: OOS censored indices
    
    Returns:
        IntermittentModel with TSB parameters (includes b_t)
    """
    if not 0 < alpha_demand <= 1:
        raise ValueError(f"alpha_demand must be in (0, 1], got {alpha_demand}")
    if not 0 < alpha_probability <= 1:
        raise ValueError(f"alpha_probability must be in (0, 1], got {alpha_probability}")
    
    # Apply censoring
    exclude_set = set(exclude_indices or [])
    clean_series = [series[i] for i in range(len(series)) if i not in exclude_set]
    
    if len(clean_series) == 0:
        raise ValueError("No observations after censoring")
    
    n_nonzero = sum(1 for x in clean_series if x > 0)
    
    if n_nonzero == 0:
        # All zeros
        return IntermittentModel(
            method="tsb",
            alpha=alpha_demand,
            p_t=0.0,  # not used in TSB
            z_t=0.0,
            b_t=0.0,
            n_nonzero=0,
            n_total=len(clean_series),
            n_censored=len(exclude_set)
        )
    
    # Initialize
    first_nonzero_idx = next(i for i, x in enumerate(clean_series) if x > 0)
    z_t = clean_series[first_nonzero_idx]
    b_t = 1.0 if clean_series[0] > 0 else 0.0
    
    # Update smoothed values period by period
    for t in range(1, len(clean_series)):
        demand = clean_series[t]
        
        # Update probability (1 if demand occurred, 0 otherwise)
        occurrence = 1.0 if demand > 0 else 0.0
        b_t = alpha_probability * occurrence + (1 - alpha_probability) * b_t
        
        # Update demand size (only when demand occurs)
        if demand > 0:
            z_t = alpha_demand * demand + (1 - alpha_demand) * z_t
    
    return IntermittentModel(
        method="tsb",
        alpha=alpha_demand,
        p_t=0.0,  # not used in TSB forecast
        z_t=z_t,
        b_t=max(b_t, 0.0001),  # prevent complete zero probability
        n_nonzero=n_nonzero,
        n_total=len(clean_series),
        n_censored=len(exclude_set)
    )


def predict_daily(model: IntermittentModel) -> float:
    """
    Predict expected daily demand using fitted intermittent model.
    
    Args:
        model: fitted IntermittentModel
    
    Returns:
        predicted daily demand (can be fractional)
    """
    if model.method == "croston":
        if model.p_t <= 0:
            return 0.0
        return model.z_t / model.p_t
    
    elif model.method == "sba":
        if model.p_t <= 0:
            return 0.0
        # Bias correction factor
        correction = 1.0 - model.alpha / 2.0
        return correction * model.z_t / model.p_t
    
    elif model.method == "tsb":
        if model.b_t is None:
            return 0.0
        return model.b_t * model.z_t
    
    else:
        raise ValueError(f"Unknown method: {model.method}")


def predict_P_days(model: IntermittentModel, P: int) -> float:
    """
    Predict total demand over P days using intermittent model.
    
    Args:
        model: fitted IntermittentModel
        P: protection period (days)
    
    Returns:
        mu_P: expected demand over P days
    """
    daily_forecast = predict_daily(model)
    return daily_forecast * P


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_intermittent(
    series: List[float],
    adi_threshold: float = 1.32,
    cv2_threshold: float = 0.49,
    exclude_indices: Optional[List[int]] = None
) -> IntermittentClassification:
    """
    Classify demand series as intermittent using ADI and CV² criteria.
    
    Classic thresholds (Syntetos et al.):
    - ADI > 1.32: demand is intermittent (not every period)
    - CV² > 0.49: demand is variable
    
    Both conditions → intermittent demand pattern
    
    Args:
        series: demand observations
        adi_threshold: threshold for average demand interval
        cv2_threshold: threshold for squared coefficient of variation
        exclude_indices: OOS censored indices to exclude
    
    Returns:
        IntermittentClassification with results
    """
    # Apply censoring
    exclude_set = set(exclude_indices or [])
    clean_series = [series[i] for i in range(len(series)) if i not in exclude_set]
    
    if len(clean_series) == 0:
        return IntermittentClassification(
            is_intermittent=False,
            adi=0.0,
            cv2=0.0,
            n_nonzero=0,
            n_total=0,
            n_censored=len(exclude_set)
        )
    
    # Find non-zero demands
    nonzero_values = [x for x in clean_series if x > 0]
    n_nonzero = len(nonzero_values)
    n_total = len(clean_series)
    
    if n_nonzero == 0:
        # All zeros → definitely intermittent (but degenerate case)
        return IntermittentClassification(
            is_intermittent=True,
            adi=float(n_total),
            cv2=0.0,
            n_nonzero=0,
            n_total=n_total,
            n_censored=len(exclude_set)
        )
    
    # Calculate ADI
    adi = n_total / n_nonzero
    
    # Calculate CV² (coefficient of variation squared)
    if n_nonzero < 2:
        cv2 = 0.0
    else:
        mean_nonzero = np.mean(nonzero_values)
        if mean_nonzero == 0:
            cv2 = 0.0
        else:
            std_nonzero = np.std(nonzero_values, ddof=1)
            cv = std_nonzero / mean_nonzero
            cv2 = cv ** 2
    
    # Classification decision
    is_intermittent = (adi > adi_threshold) and (cv2 > cv2_threshold)
    
    return IntermittentClassification(
        is_intermittent=bool(is_intermittent),
        adi=float(adi),
        cv2=float(cv2),
        n_nonzero=n_nonzero,
        n_total=n_total,
        n_censored=len(exclude_set)
    )


# ---------------------------------------------------------------------------
# Backtest (rolling origin)
# ---------------------------------------------------------------------------

def backtest_method(
    series: List[float],
    method: str,
    test_periods: int = 4,
    alpha: float = 0.1,
    exclude_indices: Optional[List[int]] = None
) -> BacktestResult:
    """
    Backtest intermittent method using rolling origin.
    
    Strategy:
    - Split series into train + test windows (rolling forward)
    - Fit on train, predict 1-step ahead, compare to test
    - Compute WMAPE and bias
    
    Args:
        series: full demand series
        method: 'croston', 'sba', or 'tsb'
        test_periods: number of test periods to evaluate
        alpha: smoothing parameter
        exclude_indices: OOS censored indices
    
    Returns:
        BacktestResult with performance metrics
    """
    if len(series) < test_periods + 7:  # need min train data
        raise ValueError(f"Series too short for backtest: {len(series)} < {test_periods + 7}")
    
    exclude_set = set(exclude_indices or [])
    
    errors = []
    actuals = []
    forecasts = []
    
    # Rolling origin: fit on [0:t], predict t+1
    for test_idx in range(len(series) - test_periods, len(series)):
        if test_idx in exclude_set:
            continue
        
        # Train on all data before test point
        train_series = series[:test_idx]
        train_exclude = [i for i in exclude_set if i < test_idx]
        
        # Fit model
        try:
            if method == "croston":
                model = fit_croston(train_series, alpha, train_exclude)
            elif method == "sba":
                model = fit_sba(train_series, alpha, train_exclude)
            elif method == "tsb":
                model = fit_tsb(train_series, alpha, alpha, train_exclude)
            else:
                raise ValueError(f"Unknown method: {method}")
            
            # Predict 1-step ahead
            forecast = predict_daily(model)
            actual = series[test_idx]
            
            forecasts.append(forecast)
            actuals.append(actual)
            errors.append(forecast - actual)
        
        except (ValueError, ZeroDivisionError):
            # Insufficient data or degenerate case
            continue
    
    if len(actuals) == 0:
        return BacktestResult(
            method=method,
            wmape=999.0,
            bias=0.0,
            n_forecasts=0,
            n_observations=0
        )
    
    # Compute metrics
    total_actual = sum(actuals)
    if total_actual == 0:
        wmape = 999.0
    else:
        wmape = sum(abs(e) for e in errors) / total_actual
    
    bias = float(np.mean(errors))
    
    return BacktestResult(
        method=method,
        wmape=float(wmape),
        bias=bias,
        n_forecasts=len(forecasts),
        n_observations=len(actuals)
    )


def select_best_method(
    series: List[float],
    candidate_methods: Optional[List[str]] = None,
    test_periods: int = 4,
    alpha: float = 0.1,
    exclude_indices: Optional[List[int]] = None,
    metric: str = "wmape"
) -> Tuple[str, Dict[str, BacktestResult]]:
    """
    Select best intermittent method via backtest comparison.
    
    Args:
        series: demand observations
        candidate_methods: list of methods to test (default: ['sba', 'tsb'])
        test_periods: backtest window
        alpha: smoothing parameter
        exclude_indices: OOS censored indices
        metric: 'wmape' or 'bias' to optimize
    
    Returns:
        Tuple of (best_method_name, dict_of_all_results)
    """
    if candidate_methods is None:
        candidate_methods = ["sba", "tsb"]
    
    results = {}
    for method in candidate_methods:
        try:
            results[method] = backtest_method(
                series, method, test_periods, alpha, exclude_indices
            )
        except (ValueError, Exception) as e:
            # Method failed, assign worst score
            results[method] = BacktestResult(
                method=method,
                wmape=999.0,
                bias=999.0,
                n_forecasts=0,
                n_observations=0
            )
    
    # Select best by metric
    if metric == "wmape":
        best_method = min(results.keys(), key=lambda m: results[m].wmape)
    elif metric == "bias":
        best_method = min(results.keys(), key=lambda m: abs(results[m].bias))
    else:
        raise ValueError(f"Unknown metric: {metric}")
    
    return best_method, results


# ---------------------------------------------------------------------------
# Uncertainty estimation for intermittent methods
# ---------------------------------------------------------------------------

def estimate_sigma_P_rolling(
    series: List[float],
    model: IntermittentModel,
    P: int,
    exclude_indices: Optional[List[int]] = None
) -> float:
    """
    Estimate sigma_P (forecast error std) via rolling residuals.
    
    Strategy:
    - Use last N periods, fit model on [0:t], predict t+1
    - Aggregate errors over P-day windows
    - Return std of aggregated errors
    
    Args:
        series: demand observations
        model: fitted intermittent model
        P: protection period
        exclude_indices: OOS censored indices
    
    Returns:
        sigma_P: estimated forecast error std over P days
    """
    if len(series) < P + 7:
        # Insufficient data, return fallback based on z_t
        return model.z_t * np.sqrt(P) if model.z_t > 0 else 1.0
    
    exclude_set = set(exclude_indices or [])
    
    # Compute 1-step forecast errors
    errors = []
    for t in range(7, len(series)):
        if t in exclude_set:
            continue
        
        train_series = series[:t]
        train_exclude = [i for i in exclude_set if i < t]
        
        try:
            if model.method == "croston":
                m = fit_croston(train_series, model.alpha, train_exclude)
            elif model.method == "sba":
                m = fit_sba(train_series, model.alpha, train_exclude)
            elif model.method == "tsb":
                m = fit_tsb(train_series, model.alpha, model.alpha, train_exclude)
            else:
                continue
            
            forecast = predict_daily(m)
            actual = series[t]
            errors.append(forecast - actual)
        
        except (ValueError, ZeroDivisionError):
            continue
    
    if len(errors) < P:
        # Fallback
        return model.z_t * np.sqrt(P) if model.z_t > 0 else 1.0
    
    # Aggregate errors over P-day windows
    aggregated_errors = []
    for i in range(len(errors) - P + 1):
        window_error = sum(errors[i:i+P])
        aggregated_errors.append(window_error)
    
    if len(aggregated_errors) == 0:
        return model.z_t * np.sqrt(P) if model.z_t > 0 else 1.0
    
    sigma_P = np.std(aggregated_errors)
    
    # Ensure non-zero
    return float(max(sigma_P, 0.1))


# ---------------------------------------------------------------------------
# Helper: detect obsolescence pattern for TSB preference
# ---------------------------------------------------------------------------

def detect_obsolescence(
    series: List[float],
    window: int = 14,
    exclude_indices: Optional[List[int]] = None
) -> bool:
    """
    Detect if demand shows obsolescence (declining trend).
    
    Simple heuristic: compare recent avg to older avg.
    
    Args:
        series: demand observations
        window: window size for comparison
        exclude_indices: OOS censored indices
    
    Returns:
        True if recent demand significantly lower than past
    """
    exclude_set = set(exclude_indices or [])
    clean_series = [series[i] for i in range(len(series)) if i not in exclude_set]
    
    if len(clean_series) < 2 * window:
        return False
    
    # Split into old and recent
    old_avg = np.mean(clean_series[-2*window:-window])
    recent_avg = np.mean(clean_series[-window:])
    
    # Obsolescence if recent < 70% of old
    if old_avg == 0:
        return False
    
    return bool(recent_avg < 0.7 * old_avg)
