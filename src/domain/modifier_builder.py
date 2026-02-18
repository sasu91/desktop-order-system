"""
Modifier Builder: the SINGLE authoritative point that applies ALL demand
modifiers (promo, event, cannibalization, holiday) to a DemandDistribution.

ARCHITECTURE RULE
-----------------
No policy and no other module is allowed to apply promo / event / cannibalization
uplifts to mu_P independently.  All modifier logic lives here.

Flow
----
apply_modifiers(base_demand, context) → (adjusted: DemandDistribution, applied: list[AppliedModifier])

Modifier precedence (fixed, deterministic):
    1. EVENT uplift        (delivery-date-based demand driver)
    2. PROMO uplift        (calendar window-based uplift on promo days)
    3. CANNIBALIZATION     (downlift on non-promo days if driver SKU is in promo)

Post-policy adjustments (prebuild, post-promo guardrail) are returned as
AppliedModifier(scope="qty_correction") entries so the explain payload is
complete, but they are NOT applied to mu_P here.

Bug fix implemented here
------------------------
Eliminates the double event-uplift application present in the legacy
generate_proposal() path: previously event uplift was applied once inside
promo_adjusted_forecast() AND again in the independent event_uplift_enabled
branch.  Here it is applied exactly once (precedence #1).

Author: Desktop Order System Team
Date: February 2026
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .contracts import AppliedModifier, DemandDistribution

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_modifiers(
    base_demand: DemandDistribution,
    sku_id: str,
    sku_obj: Any,                          # SKU domain object
    horizon_dates: List[date],             # forecast days [d+1 … d+P]
    target_receipt_date: Optional[date],   # actual delivery date (for event)
    asof_date: date,
    settings: Dict[str, Any],
    all_skus: Optional[List[Any]] = None,
    promo_windows: Optional[List[Any]] = None,
    event_rules: Optional[List[Any]] = None,
    sales_records: Optional[List[Any]] = None,
    transactions: Optional[List[Any]] = None,
) -> Tuple[DemandDistribution, List[AppliedModifier]]:
    """
    Apply all demand modifiers in precedence order and return the adjusted
    DemandDistribution together with the complete list of AppliedModifiers.

    Parameters
    ----------
    base_demand : DemandDistribution
        Output of build_demand_distribution() – must not have any modifiers
        already applied (sigma_adj_multiplier == 1.0 is expected).
    sku_id : str
    sku_obj : SKU
    horizon_dates : list[date]
        Forecast horizon dates (used for promo window lookup and day-level uplift).
    target_receipt_date : date or None
        Actual delivery date; required for event uplift matching.
    asof_date : date
    settings : dict
        Global settings dict (read_settings() output).
    all_skus, promo_windows, event_rules, sales_records, transactions :
        Pre-loaded domain objects; pass None to skip that modifier class.

    Returns
    -------
    (adjusted_demand, applied_modifiers)
    """
    applied: List[AppliedModifier] = []
    cumulative = base_demand  # DemandDistribution (immutable)

    # ---- 1. EVENT UPLIFT ------------------------------------------------
    # Applied once here; NOT applied again elsewhere.
    event_settings = settings.get("event_uplift", {})
    event_enabled = event_settings.get("enabled", {}).get("value", False)

    if event_enabled and sku_obj and event_rules and target_receipt_date:
        try:
            mod, new_demand = _apply_event_uplift(
                demand=cumulative,
                sku_obj=sku_obj,
                sku_id=sku_id,
                horizon_dates=horizon_dates,
                target_receipt_date=target_receipt_date,
                event_rules=event_rules,
                all_skus=all_skus or [],
                sales_records=sales_records or [],
                settings=settings,
            )
            if mod is not None:
                applied.append(mod)
                cumulative = new_demand
        except Exception as exc:
            logger.warning("Event uplift failed for %s: %s. Skipping.", sku_id, exc)

    # ---- 2. PROMO UPLIFT ------------------------------------------------
    promo_settings = settings.get("promo_adjustment", {})
    promo_enabled = promo_settings.get("enabled", {}).get("value", False)

    if promo_enabled and promo_windows is not None and horizon_dates:
        try:
            mod_promo, mod_cannib, new_demand = _apply_promo_and_cannibalization(
                demand=cumulative,
                sku_id=sku_id,
                sku_obj=sku_obj,
                horizon_dates=horizon_dates,
                promo_windows=promo_windows,
                all_skus=all_skus or [],
                sales_records=sales_records or [],
                transactions=transactions or [],
                settings=settings,
                asof_date=asof_date,
            )
            if mod_promo is not None:
                applied.append(mod_promo)
            if mod_cannib is not None:
                applied.append(mod_cannib)
            cumulative = new_demand
        except Exception as exc:
            logger.warning("Promo adjustment failed for %s: %s. Skipping.", sku_id, exc)

    # ---- Apply all collected modifiers to base distribution -------------
    # We already applied per-step above.  Here we only update sigma & metadata
    # via with_modifiers_applied to ensure the sigma_adj_multiplier is recorded.
    if applied:
        final_demand, _ = base_demand.with_modifiers_applied(applied)
        # Keep mu_P from the incremental application (more precise),
        # but take sigma_P and sigma_adj_multiplier from the combined call.
        final_demand = replace(final_demand, mu_P=cumulative.mu_P)
    else:
        final_demand = cumulative

    return final_demand, applied


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_event_uplift(
    demand: DemandDistribution,
    sku_obj: Any,
    sku_id: str,
    horizon_dates: List[date],
    target_receipt_date: date,
    event_rules: List[Any],
    all_skus: List[Any],
    sales_records: List[Any],
    settings: Dict[str, Any],
) -> Tuple[Optional[AppliedModifier], DemandDistribution]:
    """
    Apply event uplift exactly once.  Returns (modifier, updated_demand).
    Returns (None, demand) if no event matches or rule_matched is None.
    """
    try:
        from src.domain.event_uplift import apply_event_uplift_to_forecast
    except ImportError:
        from domain.event_uplift import apply_event_uplift_to_forecast

    baseline_fc: Dict[date, float] = {d: demand.mu_P / max(len(horizon_dates), 1)
                                       for d in horizon_dates}

    adjusted_fc, explain = apply_event_uplift_to_forecast(
        sku_obj=sku_obj,
        delivery_date=target_receipt_date,
        horizon_dates=horizon_dates,
        baseline_forecast=baseline_fc,
        event_rules=event_rules,
        all_skus=all_skus,
        sales_records=sales_records,
        settings=settings,
    )

    if explain.rule_matched is None:
        return None, demand

    m_i = explain.m_i
    new_mu_P = max(0.0, sum(adjusted_fc.values()))

    # Determine impact window
    date_range_event: Optional[Tuple[date, date]] = None
    if explain.impact_start_date and explain.impact_end_date:
        date_range_event = (explain.impact_start_date, explain.impact_end_date)
    elif horizon_dates:
        date_range_event = (horizon_dates[0], horizon_dates[-1])

    reason = ""
    if explain.rule_matched:
        reason = getattr(explain.rule_matched, "reason", "") or ""

    mod = AppliedModifier(
        name=f"event_uplift_{reason}" if reason else "event_uplift",
        modifier_type="event",
        scope="both",          # event uplift is correlated with higher variance
        multiplier=m_i,
        stacking="multiplicative",
        date_range=date_range_event,
        source_sku=None,
        confidence="",
        note=(
            f"U_store={explain.u_store_day:.3f}, beta={explain.beta_i:.3f}, "
            f"m_i={explain.m_i:.3f}, "
            f"P{int(explain.u_quantile * 100)}, "
            f"{explain.u_fallback_level}"
        ),
    )

    new_demand = replace(
        demand,
        mu_P=new_mu_P,
        sigma_P=max(0.0, demand.sigma_P * max(1.0, min(m_i, 2.5))),
    )
    return mod, new_demand


def _apply_promo_and_cannibalization(
    demand: DemandDistribution,
    sku_id: str,
    sku_obj: Any,
    horizon_dates: List[date],
    promo_windows: List[Any],
    all_skus: List[Any],
    sales_records: List[Any],
    transactions: List[Any],
    settings: Dict[str, Any],
    asof_date: date,
) -> Tuple[Optional[AppliedModifier], Optional[AppliedModifier], DemandDistribution]:
    """
    Apply promo uplift and cannibalization downlift.
    Returns (promo_modifier, cannib_modifier, updated_demand).
    """
    try:
        from src.domain.promo_uplift import estimate_uplift, estimate_cannibalization_downlift
        from src.promo_calendar import is_promo
    except ImportError:
        from domain.promo_uplift import estimate_uplift, estimate_cannibalization_downlift
        from promo_calendar import is_promo

    promo_adj_settings = settings.get("promo_adjustment", {})
    smoothing_enabled = promo_adj_settings.get("smoothing_enabled", {}).get("value", False)
    ramp_in_days = promo_adj_settings.get("ramp_in_days", {}).get("value", 0)
    ramp_out_days = promo_adj_settings.get("ramp_out_days", {}).get("value", 0)

    # Check which horizon days are in a promo window for this SKU
    promo_days = [d for d in horizon_dates if is_promo(sku_id, d, promo_windows)]
    non_promo_days = [d for d in horizon_dates if d not in promo_days]

    promo_modifier: Optional[AppliedModifier] = None
    cannib_modifier: Optional[AppliedModifier] = None
    new_demand = demand  # Default: no change

    if promo_days:
        # --- Promo uplift ---
        try:
            uplift_report = estimate_uplift(
                sku_id=sku_id,
                promo_windows=promo_windows,
                sales_records=sales_records,
                transactions=transactions,
                all_skus=all_skus,
                asof_date=asof_date,
            )

            if uplift_report and uplift_report.uplift_factor > 0:
                uplift_factor = uplift_report.uplift_factor
                promo_frac = len(promo_days) / max(len(horizon_dates), 1)

                # Smoothing ramp-in/ramp-out (per-day fraction adjustment)
                smoothing_mult = 1.0
                if smoothing_enabled and (ramp_in_days > 0 or ramp_out_days > 0):
                    smoothing_mult = _compute_smoothing_multiplier(
                        horizon_dates=horizon_dates,
                        promo_days=promo_days,
                        ramp_in_days=ramp_in_days,
                        ramp_out_days=ramp_out_days,
                        uplift_factor=uplift_factor,
                    )

                # Blended effective multiplier over whole horizon:
                # promo days get uplift, non-promo days get 1.0
                effective_mult = (promo_frac * uplift_factor * smoothing_mult +
                                  (1.0 - promo_frac) * 1.0)

                new_mu_P = max(0.0, demand.mu_P * effective_mult)

                promo_range = (promo_days[0], promo_days[-1])
                promo_modifier = AppliedModifier(
                    name="promo_uplift",
                    modifier_type="promo",
                    scope="both",
                    multiplier=effective_mult,
                    stacking="multiplicative",
                    date_range=promo_range,
                    source_sku=None,
                    confidence=getattr(uplift_report, "confidence", ""),
                    note=(
                        f"promo fraction={promo_frac:.2f}, "
                        f"uplift={uplift_factor:.3f}, "
                        f"smoothing={smoothing_mult:.3f}, "
                        f"effective={effective_mult:.3f}"
                    ),
                )

                new_demand = replace(
                    demand,
                    mu_P=new_mu_P,
                    sigma_P=max(0.0, demand.sigma_P * max(1.0, min(effective_mult, 2.5))),
                )

        except Exception as exc:
            logger.warning("Promo uplift estimation failed for %s: %s", sku_id, exc)

    # --- Cannibalization downlift (non-promo days only) ---
    if non_promo_days and all_skus:
        try:
            downlift_report = estimate_cannibalization_downlift(
                sku_id=sku_id,
                promo_windows=promo_windows,
                sales_records=sales_records,
                transactions=transactions,
                all_skus=all_skus,
                asof_date=asof_date,
            )

            if downlift_report and downlift_report.downlift_factor < 1.0:
                # Only reduce demand on non-promo days
                non_promo_frac = len(non_promo_days) / max(len(horizon_dates), 1)
                effective_downlift = (1.0 - non_promo_frac) * 1.0 + non_promo_frac * downlift_report.downlift_factor

                new_mu_P = max(0.0, new_demand.mu_P * effective_downlift)
                cannib_modifier = AppliedModifier(
                    name=f"cannibalization_{downlift_report.driver_sku}",
                    modifier_type="cannibalization",
                    scope="mu_only",      # downlift → conservative: don't reduce sigma
                    multiplier=effective_downlift,
                    stacking="multiplicative",
                    date_range=(non_promo_days[0], non_promo_days[-1]) if non_promo_days else None,
                    source_sku=downlift_report.driver_sku,
                    confidence=getattr(downlift_report, "confidence", ""),
                    note=(
                        f"driver={downlift_report.driver_sku}, "
                        f"downlift={downlift_report.downlift_factor:.3f}, "
                        f"non_promo_frac={non_promo_frac:.2f}"
                    ),
                )

                new_demand = replace(
                    new_demand,
                    mu_P=new_mu_P,
                    # sigma unchanged (conservative: don't reduce sigma on downlift)
                )

        except Exception as exc:
            logger.warning("Cannibalization downlift failed for %s: %s", sku_id, exc)

    return promo_modifier, cannib_modifier, new_demand


def _compute_smoothing_multiplier(
    horizon_dates: List[date],
    promo_days: List[date],
    ramp_in_days: int,
    ramp_out_days: int,
    uplift_factor: float,
) -> float:
    """
    Compute a blended smoothing multiplier across the horizon.

    For days in the ramp-in/ramp-out window, the uplift fraction is reduced
    linearly.  Returns the weighted mean multiplier across all promo days.
    """
    if not promo_days:
        return 1.0

    promo_set = set(promo_days)
    promo_sorted = sorted(promo_days)
    promo_start = promo_sorted[0]
    promo_end = promo_sorted[-1]

    total_weight = 0.0
    weighted_mult = 0.0
    for d in promo_days:
        days_since_start = (d - promo_start).days
        days_to_end = (promo_end - d).days

        if ramp_in_days > 0 and days_since_start < ramp_in_days:
            frac = (days_since_start + 1) / ramp_in_days
        elif ramp_out_days > 0 and days_to_end < ramp_out_days:
            frac = (days_to_end + 1) / ramp_out_days
        else:
            frac = 1.0

        day_mult = 1.0 + (uplift_factor - 1.0) * frac
        weighted_mult += day_mult
        total_weight += 1.0

    return weighted_mult / max(total_weight, 1.0)


# ---------------------------------------------------------------------------
# Convenience: build qty-correction AppliedModifier entries (post-policy)
# ---------------------------------------------------------------------------

def make_promo_prebuild_modifier(
    prebuild_delta_qty: int,
    target_receipt_date: date,
    promo_start_date: date,
    coverage_days: int,
) -> AppliedModifier:
    """
    Create a qty_correction modifier for promo prebuild.
    This is recorded in OrderExplain but NOT applied to mu_P.
    """
    return AppliedModifier(
        name="promo_prebuild",
        modifier_type="promo",
        scope="qty_correction",
        multiplier=float(prebuild_delta_qty),
        stacking="additive",
        date_range=(target_receipt_date, promo_start_date),
        source_sku=None,
        confidence="",
        note=(
            f"prebuild_delta={prebuild_delta_qty}, "
            f"coverage={coverage_days}d, "
            f"promo_start={promo_start_date.isoformat()}"
        ),
    )


def make_post_promo_guardrail_modifier(
    qty_before: int,
    qty_after: int,
    cooldown_factor: float,
    window_days: int,
) -> AppliedModifier:
    """
    Create a qty_correction modifier for post-promo guardrail.
    This is recorded in OrderExplain but NOT applied to mu_P.
    """
    delta = qty_after - qty_before  # Negative (reduction)
    return AppliedModifier(
        name="post_promo_guardrail",
        modifier_type="promo",
        scope="qty_correction",
        multiplier=float(delta),
        stacking="additive",
        date_range=None,
        source_sku=None,
        confidence="",
        note=(
            f"cooldown={cooldown_factor:.2f}, "
            f"window={window_days}d, "
            f"qty: {qty_before}→{qty_after}"
        ),
    )
