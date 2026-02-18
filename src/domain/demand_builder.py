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

    mu_P     = sum of mean-path forecast (output_stat="mean")
    sigma_P  = estimated from the same rolling residual method as simple,
               using baseline (non-MC) model for residuals.
    quantiles = {p50, p80, p90, p95} for each P-day total (derived from
                per-day percentile arrays summed).

    The rolling-residual sigma is used (not cross-simulation std) because it
    reflects *out-of-sample* forecast error, not in-sample MC spread.
    """
    try:
        from src.forecast import monte_carlo_forecast, monte_carlo_forecast_with_stats, fit_forecast_model, predict
        from src.uncertainty import estimate_demand_uncertainty, sigma_over_horizon
    except ImportError:
        from forecast import monte_carlo_forecast, monte_carlo_forecast_with_stats, fit_forecast_model, predict
        from uncertainty import estimate_demand_uncertainty, sigma_over_horizon

    distribution = mc_params.get("distribution", "empirical")
    n_simulations = mc_params.get("n_simulations", 1000)
    random_seed = mc_params.get("random_seed", 42)
    output_stat = mc_params.get("output_stat", "mean")
    output_percentile = mc_params.get("output_percentile", 80)

    # --- mu_P via MC mean path ------------------------------------------
    try:
        mc_values = monte_carlo_forecast(
            history=history,
            horizon_days=protection_period_days,
            distribution=distribution,
            n_simulations=n_simulations,
            random_seed=random_seed,
            output_stat=output_stat,
            output_percentile=output_percentile,
            expected_waste_rate=expected_waste_rate,
        )
        mu_P = max(0.0, sum(mc_values))
    except Exception as exc:
        logger.warning("MC forecast failed (%s); falling back to simple mu_P", exc)
        # fallback: simple model
        simple_model = fit_forecast_model(history, censored_flags=censored_flags)
        mu_P = max(0.0, sum(predict(simple_model, horizon=protection_period_days)))
        distribution = "simple_fallback"

    # --- sigma_P via rolling residuals (baseline model) -----------------
    def _forecast_func(hist: list, horizon: int) -> List[float]:
        m = fit_forecast_model(hist, censored_flags=censored_flags)
        return predict(m, horizon)

    sigma_day, meta = estimate_demand_uncertainty(
        history,
        _forecast_func,
        window_weeks=window_weeks,
        method="mad",
        censored_flags=censored_flags,
    )
    sigma_P = sigma_over_horizon(protection_period_days, sigma_day)

    # --- quantiles via MC stats -----------------------------------------
    quantiles: Dict[str, float] = {}
    try:
        mc_stats = monte_carlo_forecast_with_stats(
            history=history,
            horizon_days=protection_period_days,
            distribution=distribution if distribution != "simple_fallback" else "empirical",
            n_simulations=n_simulations,
            random_seed=random_seed,
            expected_waste_rate=expected_waste_rate,
        )
        # mc_stats returns per-day percentile lists; sum across days → horizon totals
        def _sum_pct(key: str) -> float:
            vals = mc_stats.get(key, [])
            return sum(vals) if vals else 0.0

        quantiles = {
            "p50": _sum_pct("p50"),
            "p80": _sum_pct("p80"),
            "p90": _sum_pct("p90"),
            "p95": _sum_pct("p95"),
        }
    except Exception as exc:
        logger.debug("Could not compute MC quantiles: %s", exc)

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
    )
