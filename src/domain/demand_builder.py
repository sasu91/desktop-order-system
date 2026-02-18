"""
Demand Builder: the SINGLE authoritative point that computes DemandDistribution.

No policy, no modifier, no GUI should ever call fit_forecast_model() or
monte_carlo_forecast() directly to produce mu_P / sigma_P for a policy run.
All paths converge here.

Public API
----------
build_demand_distribution(method, history, protection_period_days, ...)
    → DemandDistribution

Internally dispatches to:
    _build_simple(...)     – EMA level + DOW model
    _build_mc(...)         – Monte Carlo simulation

Author: Desktop Order System Team
Date: February 2026
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import date
from typing import Dict, List, Optional, Any

from .contracts import DemandDistribution

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def build_demand_distribution(
    method: str,
    history: List[Dict[str, Any]],
    protection_period_days: int,
    asof_date: date,
    censored_flags: Optional[List[bool]] = None,
    alpha_boost_for_censored: float = 0.05,
    window_weeks: int = 8,
    # Monte Carlo extras (ignored for simple)
    mc_params: Optional[Dict[str, Any]] = None,
    expected_waste_rate: float = 0.0,
) -> DemandDistribution:
    """
    Build a DemandDistribution for one SKU.

    This is the ONLY entry point for mu_P / sigma_P computation used by
    policies.  Any caller that needs demand estimation for an order decision
    MUST go through this function.

    Parameters
    ----------
    method : str
        "simple" or "monte_carlo".  Falls back to "simple" on unknown value.
    history : list of {"date": date, "qty_sold": float}
        Raw sales history, oldest-first.
    protection_period_days : int
        Length of the planning horizon P.
    asof_date : date
        Date on which the proposal is computed (used for logging / metadata).
    censored_flags : list[bool] or None
        True = day is censored (OOS / unavailable); excluded from model.
    alpha_boost_for_censored : float
        Added to EMA alpha if censored days are present.
    window_weeks : int
        Rolling window for uncertainty estimation.
    mc_params : dict or None
        Monte Carlo parameters (distribution, n_simulations, random_seed,
        output_stat, output_percentile, horizon_mode, horizon_days).
        Only used when method=="monte_carlo".
    expected_waste_rate : float
        Shelf-life waste adjustment (0–1).  Passed directly to MC forecast.

    Returns
    -------
    DemandDistribution
    """
    if protection_period_days <= 0:
        # Degenerate: no horizon
        return DemandDistribution(
            mu_P=0.0,
            sigma_P=0.0,
            protection_period_days=0,
            forecast_method=method,
        )

    if not history:
        return DemandDistribution(
            mu_P=0.0,
            sigma_P=0.0,
            protection_period_days=protection_period_days,
            forecast_method=method,
            n_samples=0,
            n_censored=0,
        )

    method_norm = method.lower().strip()

    if method_norm == "monte_carlo":
        return _build_mc(
            history=history,
            protection_period_days=protection_period_days,
            asof_date=asof_date,
            censored_flags=censored_flags,
            window_weeks=window_weeks,
            mc_params=mc_params or {},
            expected_waste_rate=expected_waste_rate,
        )
    elif method_norm in ("croston", "sba", "tsb", "intermittent_auto"):
        return _build_intermittent(
            method=method_norm,
            history=history,
            protection_period_days=protection_period_days,
            asof_date=asof_date,
            censored_flags=censored_flags,
            window_weeks=window_weeks,
            # Pass mc_params as settings carrier (or extract separate intermittent_params)
            settings=mc_params or {},
        )
    else:
        if method_norm not in ("simple", ""):
            logger.warning(
                "build_demand_distribution: unknown method %r; falling back to 'simple'",
                method,
            )
        return _build_simple(
            history=history,
            protection_period_days=protection_period_days,
            asof_date=asof_date,
            censored_flags=censored_flags,
            alpha_boost_for_censored=alpha_boost_for_censored,
            window_weeks=window_weeks,
        )


# ---------------------------------------------------------------------------
# Simple (EMA + DOW) builder
# ---------------------------------------------------------------------------

def _build_simple(
    history: List[Dict[str, Any]],
    protection_period_days: int,
    asof_date: date,
    censored_flags: Optional[List[bool]],
    alpha_boost_for_censored: float,
    window_weeks: int,
) -> DemandDistribution:
    """
    Build DemandDistribution using the EMA Level + DOW model.

    mu_P   = sum(predict(fit_forecast_model(history), P))
    sigma_P = sigma_over_horizon(P, sigma_day)
              where sigma_day comes from estimate_demand_uncertainty()
              on the same censored-filtered history.
    """
    try:
        from src.forecast import fit_forecast_model, predict
        from src.uncertainty import estimate_demand_uncertainty, sigma_over_horizon
    except ImportError:
        from forecast import fit_forecast_model, predict
        from uncertainty import estimate_demand_uncertainty, sigma_over_horizon

    # --- mu_P -----------------------------------------------------------
    model = fit_forecast_model(
        history,
        censored_flags=censored_flags,
        alpha_boost_for_censored=alpha_boost_for_censored,
    )
    forecast_values = predict(model, horizon=protection_period_days)
    mu_P = max(0.0, sum(forecast_values))

    n_samples = model.get("n_samples", 0)
    n_censored_model = model.get("n_censored", 0)

    # --- sigma_P --------------------------------------------------------
    def _forecast_func(hist: list, horizon: int) -> List[float]:
        m = fit_forecast_model(
            hist,
            censored_flags=censored_flags,
            alpha_boost_for_censored=alpha_boost_for_censored,
        )
        return predict(m, horizon)

    sigma_day, meta = estimate_demand_uncertainty(
        history,
        _forecast_func,
        window_weeks=window_weeks,
        method="mad",
        censored_flags=censored_flags,
    )
    sigma_P = sigma_over_horizon(protection_period_days, sigma_day)

    return DemandDistribution(
        mu_P=mu_P,
        sigma_P=sigma_P,
        protection_period_days=protection_period_days,
        forecast_method="simple",
        n_samples=n_samples,
        n_censored=n_censored_model,
        quantiles={},
    )


# ---------------------------------------------------------------------------
# Monte Carlo builder
# ---------------------------------------------------------------------------

def _build_mc(
    history: List[Dict[str, Any]],
    protection_period_days: int,
    asof_date: date,
    censored_flags: Optional[List[bool]],
    window_weeks: int,
    mc_params: Dict[str, Any],
    expected_waste_rate: float,
) -> DemandDistribution:
    """
    Build DemandDistribution using Monte Carlo simulation.

    NEW (Feb 2026): Coherent quantile-first approach.
    - Construct D_P: distribution of total demand over P days (sum per trajectory).
    - mu_P = mean(D_P), sigma_P = std(D_P) → both from same distribution.
    - quantiles = percentiles of D_P (not sum of daily percentiles).
    - Keys: "0.50", "0.80", "0.90", "0.95" (normalized alpha values).

    This ensures mu and sigma are coherent with the simulated distribution,
    eliminating the hybrid where mu came from MC but sigma from residuals.
    """
    try:
        from src.forecast import monte_carlo_forecast_with_stats, fit_forecast_model, predict
    except ImportError:
        from forecast import monte_carlo_forecast_with_stats, fit_forecast_model, predict

    distribution = mc_params.get("distribution", "empirical")
    n_simulations = mc_params.get("n_simulations", 1000)
    random_seed = mc_params.get("random_seed", 42)
    output_stat = mc_params.get("output_stat", "mean")
    output_percentile = mc_params.get("output_percentile", 80)

    # --- Build D_P: distribution of P-day sums ---------------------------
    try:
        # Run simulations via internal engine to get full trajectory matrix
        import random
        import numpy as np

        if random_seed > 0:
            random.seed(random_seed)
            np.random.seed(random_seed)

        if not history or len(history) < 3:
            # Fallback: simple model
            simple_model = fit_forecast_model(history, censored_flags=censored_flags)
            mu_P = max(0.0, sum(predict(simple_model, horizon=protection_period_days)))
            sigma_P = 0.0
            quantiles = {}
            n_censored = sum(censored_flags) if censored_flags else 0
            n_samples = len([h for h, c in zip(history, censored_flags or [False] * len(history)) if not c])
            return DemandDistribution(
                mu_P=mu_P,
                sigma_P=sigma_P,
                protection_period_days=protection_period_days,
                forecast_method="monte_carlo",
                n_samples=n_samples,
                n_censored=n_censored,
                quantiles=quantiles,
                mc_n_simulations=n_simulations,
                mc_random_seed=random_seed,
                mc_distribution=distribution,
                mc_horizon_days=protection_period_days,
                mc_output_percentile=output_percentile if output_stat == "percentile" else 0,
            )

        quantities = [float(rec["qty_sold"]) for rec in history]

        # Run MC simulations: generate n_simulations trajectories of length P
        simulations = []  # Shape: (n_simulations, P)

        for _ in range(n_simulations):
            path = []
            for day in range(protection_period_days):
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
                    simple_model = fit_forecast_model(history, censored_flags=censored_flags)
                    level = simple_model["level"]
                    residuals = [q - level for q in quantities]
                    sampled_residual = random.choice(residuals)
                    sampled_qty = level + sampled_residual
                else:
                    raise ValueError(f"Unknown distribution: {distribution}")

                path.append(max(0.0, sampled_qty))

            simulations.append(path)

        # Construct D_P: sum each trajectory over P days
        simulations_array = np.array(simulations)  # Shape: (n_simulations, P)
        D_P = np.sum(simulations_array, axis=1)  # Shape: (n_simulations,)

        # Apply shelf life waste adjustment
        if expected_waste_rate > 0 and 0.0 <= expected_waste_rate <= 1.0:
            D_P = D_P * (1.0 - expected_waste_rate)

        # --- Coherent mu_P and sigma_P from D_P --------------------------
        mu_P = max(0.0, float(np.mean(D_P)))
        sigma_P = max(0.0, float(np.std(D_P, ddof=1) if len(D_P) > 1 else 0.0))

        # --- Quantiles from D_P ------------------------------------------
        quantiles = {
            "0.50": float(np.percentile(D_P, 50)),
            "0.80": float(np.percentile(D_P, 80)),
            "0.90": float(np.percentile(D_P, 90)),
            "0.95": float(np.percentile(D_P, 95)),
            "0.98": float(np.percentile(D_P, 98)),
        }

    except Exception as exc:
        logger.warning("MC D_P construction failed (%s); falling back to simple", exc)
        simple_model = fit_forecast_model(history, censored_flags=censored_flags)
        mu_P = max(0.0, sum(predict(simple_model, horizon=protection_period_days)))
        sigma_P = 0.0
        quantiles = {}
        distribution = "simple_fallback"

    n_censored = sum(censored_flags) if censored_flags else 0
    n_samples = len([h for h, c in zip(history, censored_flags or [False] * len(history)) if not c])

    return DemandDistribution(
        mu_P=mu_P,
        sigma_P=sigma_P,
        protection_period_days=protection_period_days,
        forecast_method="monte_carlo",
        n_samples=n_samples,
        n_censored=n_censored,
        quantiles=quantiles,
        mc_n_simulations=n_simulations,
        mc_random_seed=random_seed,
        mc_distribution=distribution,
        mc_horizon_days=protection_period_days,
        mc_output_percentile=output_percentile if output_stat == "percentile" else 0,
    )


# ---------------------------------------------------------------------------
# Intermittent forecast builder (Croston/SBA/TSB)
# ---------------------------------------------------------------------------

def _build_intermittent(
    method: str,
    history: List[Dict[str, Any]],
    protection_period_days: int,
    asof_date: date,
    censored_flags: Optional[List[bool]],
    window_weeks: int,
    settings: Dict[str, Any],
) -> DemandDistribution:
    """
    Build DemandDistribution using intermittent demand methods.

    Methods:
    - croston: Classic Croston's method
    - sba: Syntetos-Boylan Approximation (bias-corrected)
    - tsb: Teunter-Syntetos-Babai (better for obsolescence)
    - intermittent_auto: Automatic selection via classification + backtest

    Args:
        method: 'croston', 'sba', 'tsb', or 'intermittent_auto'
        history: sales history
        protection_period_days: P horizon
        asof_date: proposal date (for logging)
        censored_flags: OOS censored days (excluded from training)
        window_weeks: rolling window (for sigma estimation)
        settings: intermittent settings from config

    Returns:
        DemandDistribution with intermittent metadata
    """
    try:
        from src.domain.intermittent_forecast import (
            classify_intermittent,
            fit_croston,
            fit_sba,
            fit_tsb,
            predict_P_days,
            select_best_method,
            estimate_sigma_P_rolling,
            detect_obsolescence,
        )
    except ImportError:
        from domain.intermittent_forecast import (
            classify_intermittent,
            fit_croston,
            fit_sba,
            fit_tsb,
            predict_P_days,
            select_best_method,
            estimate_sigma_P_rolling,
            detect_obsolescence,
        )

    # Extract series and censored indices
    series = [float(rec["qty_sold"]) for rec in history]
    exclude_indices = [i for i, c in enumerate(censored_flags or [])] if censored_flags else []

    if len(series) == 0:
        # Empty history
        return DemandDistribution(
            mu_P=0.0,
            sigma_P=0.0,
            protection_period_days=protection_period_days,
            forecast_method=method,
            n_samples=0,
            n_censored=len(exclude_indices),
            intermittent_classification=False,
        )

    # Get intermittent settings (with defaults matching config)
    adi_threshold = settings.get("adi_threshold", 1.32)
    cv2_threshold = settings.get("cv2_threshold", 0.49)
    alpha_default = settings.get("alpha_default", 0.1)
    min_nonzero = settings.get("min_nonzero_observations", 5)
    backtest_enabled = settings.get("backtest_enabled", True)
    backtest_periods = settings.get("backtest_periods", 4)
    backtest_metric = settings.get("backtest_metric", "wmape")
    backtest_min_history = settings.get("backtest_min_history", 28)
    default_method = settings.get("default_method", "sba")
    fallback_to_simple = settings.get("fallback_to_simple", True)
    obsolescence_window = settings.get("obsolescence_window", 14)

    # --- Classification ---
    classification = classify_intermittent(
        series=series,
        adi_threshold=adi_threshold,
        cv2_threshold=cv2_threshold,
        exclude_indices=exclude_indices
    )

    # Check if SKU is intermittent or has sufficient non-zero demands
    if classification.n_nonzero < min_nonzero:
        if fallback_to_simple:
            logger.info(
                "Intermittent method %r requested but insufficient non-zero demands (%d < %d); "
                "falling back to simple",
                method, classification.n_nonzero, min_nonzero
            )
            return _build_simple(
                history=history,
                protection_period_days=protection_period_days,
                asof_date=asof_date,
                censored_flags=censored_flags,
                alpha_boost_for_censored=0.05,
                window_weeks=window_weeks,
            )
        else:
            # Return zero forecast
            logger.warning(
                "Intermittent method %r requested but insufficient data; returning zero forecast",
                method
            )
            return DemandDistribution(
                mu_P=0.0,
                sigma_P=0.0,
                protection_period_days=protection_period_days,
                forecast_method=method,
                n_samples=len(series) - len(exclude_indices),
                n_censored=len(exclude_indices),
                intermittent_classification=classification.is_intermittent,
                intermittent_adi=classification.adi,
                intermittent_cv2=classification.cv2,
                intermittent_n_nonzero=classification.n_nonzero,
            )

    # --- Method selection ---
    final_method = method
    backtest_wmape = 0.0
    backtest_bias = 0.0

    if method == "intermittent_auto":
        # Automatic selection via classification + backtest
        if not classification.is_intermittent:
            # Not intermittent → use simple
            if fallback_to_simple:
                logger.info(
                    "intermittent_auto: SKU not classified as intermittent "
                    "(ADI=%.2f, CV²=%.2f); falling back to simple",
                    classification.adi, classification.cv2
                )
                return _build_simple(
                    history=history,
                    protection_period_days=protection_period_days,
                    asof_date=asof_date,
                    censored_flags=censored_flags,
                    alpha_boost_for_censored=0.05,
                    window_weeks=window_weeks,
                )

        # SKU is intermittent → select method
        if backtest_enabled and len(series) >= backtest_min_history:
            try:
                # Detect obsolescence to prioritize TSB
                is_obsolete = detect_obsolescence(
                    series=series,
                    window=obsolescence_window,
                    exclude_indices=exclude_indices
                )

                if is_obsolete:
                    # Prioritize TSB for declining demand
                    candidates = ["tsb", "sba"]
                else:
                    # Standard intermittent: test SBA and TSB
                    candidates = ["sba", "tsb"]

                best_method, results = select_best_method(
                    series=series,
                    candidate_methods=candidates,
                    test_periods=backtest_periods,
                    alpha=alpha_default,
                    exclude_indices=exclude_indices,
                    metric=backtest_metric
                )
                final_method = best_method
                backtest_wmape = results[best_method].wmape
                backtest_bias = results[best_method].bias

                logger.info(
                    "intermittent_auto: selected %s via backtest (WMAPE=%.4f, bias=%.4f)",
                    final_method, backtest_wmape, backtest_bias
                )

            except Exception as exc:
                logger.warning(
                    "intermittent_auto: backtest failed (%s); using default method %s",
                    exc, default_method
                )
                final_method = default_method
        else:
            # Insufficient history or backtest disabled → use default
            final_method = default_method
            logger.info(
                "intermittent_auto: insufficient history or backtest disabled; using default %s",
                final_method
            )

    # --- Fit model ---
    try:
        if final_method == "croston":
            model = fit_croston(series, alpha=alpha_default, exclude_indices=exclude_indices)
        elif final_method == "sba":
            model = fit_sba(series, alpha=alpha_default, exclude_indices=exclude_indices)
        elif final_method == "tsb":
            model = fit_tsb(series, alpha_demand=alpha_default, alpha_probability=alpha_default,
                           exclude_indices=exclude_indices)
        else:
            raise ValueError(f"Unknown intermittent method: {final_method}")

    except Exception as exc:
        logger.error("Intermittent fit failed for method %s: %s", final_method, exc)
        if fallback_to_simple:
            logger.info("Falling back to simple method")
            return _build_simple(
                history=history,
                protection_period_days=protection_period_days,
                asof_date=asof_date,
                censored_flags=censored_flags,
                alpha_boost_for_censored=0.05,
                window_weeks=window_weeks,
            )
        else:
            return DemandDistribution(
                mu_P=0.0,
                sigma_P=0.0,
                protection_period_days=protection_period_days,
                forecast_method=method,
                n_samples=len(series) - len(exclude_indices),
                n_censored=len(exclude_indices),
                intermittent_classification=classification.is_intermittent,
                intermittent_adi=classification.adi,
                intermittent_cv2=classification.cv2,
                intermittent_n_nonzero=classification.n_nonzero,
            )

    # --- mu_P ---
    mu_P = max(0.0, predict_P_days(model, protection_period_days))

    # --- sigma_P ---
    try:
        sigma_P = estimate_sigma_P_rolling(
            series=series,
            model=model,
            P=protection_period_days,
            exclude_indices=exclude_indices
        )
    except Exception as exc:
        logger.warning("sigma_P estimation failed: %s; using fallback", exc)
        # Fallback: sigma proportional to z_t and sqrt(P)
        sigma_P = model.z_t * math.sqrt(protection_period_days) if model.z_t > 0 else 1.0

    sigma_P = max(0.1, sigma_P)  # Ensure non-zero

    # --- Build DemandDistribution ---
    return DemandDistribution(
        mu_P=mu_P,
        sigma_P=sigma_P,
        protection_period_days=protection_period_days,
        forecast_method=final_method,
        n_samples=model.n_total,
        n_censored=model.n_censored,
        quantiles={},  # Could add empirical quantiles via bootstrap in future
        intermittent_classification=classification.is_intermittent,
        intermittent_adi=classification.adi,
        intermittent_cv2=classification.cv2,
        intermittent_method=model.method,
        intermittent_alpha=model.alpha,
        intermittent_p_t=model.p_t,
        intermittent_z_t=model.z_t,
        intermittent_b_t=model.b_t if model.b_t is not None else 0.0,
        intermittent_backtest_wmape=backtest_wmape,
        intermittent_backtest_bias=backtest_bias,
        intermittent_n_nonzero=classification.n_nonzero,
    )
