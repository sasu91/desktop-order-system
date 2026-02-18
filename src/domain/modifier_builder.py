"""
Modifiers Engine – single authoritative point for ALL pre-policy demand
modifiers (event, promo, cannibalization, holiday).

ARCHITECTURE RULE
-----------------
No policy (legacy or CSL) and no other module may apply promo / event /
cannibalization / holiday uplifts to mu_P independently.
All modifier logic lives here; both ``propose_order_for_sku`` and the legacy
``generate_proposal`` bridge call exactly this module.

Public API
----------
list_modifiers(context: ModifierContext) -> List[Modifier]
    Evaluate and return all active Modifier rules for a given context.
    Sorted by precedence: EVENT=1, PROMO=2, CANNIBALIZATION=3, HOLIDAY=4.

apply_modifiers(base_demand, ...) -> (adjusted_demand, applied_modifiers)
    Builds a ModifierContext, calls list_modifiers, applies each Modifier
    in precedence order, returns adjusted DemandDistribution and
    AppliedModifier records with full provenance (mu_before / mu_after).

Modifier precedence (fixed, deterministic)
------------------------------------------
1. EVENT uplift        – delivery-date-based demand driver
2. PROMO uplift        – calendar window-based uplift on promo days
3. CANNIBALIZATION     – downlift on non-promo days when driver SKU in promo
4. HOLIDAY             – per-holiday demand multiplier from config

Overlap resolution (within same class)
----------------------------------------
PROMO: multiple active windows on the same day → select the one with the
largest |factor − 1| (MAX-absolute rule).  Deterministic, auditable.

Sigma policy (justified, documented)
--------------------------------------
sigma_P is scaled ONLY upward, never downward.
    sigma_final = sigma_base × clamp(total_multiplier, 1.0, 2.5)
Rationale: promo / event periods have HIGHER demand variance than baseline;
downlift phases (cannibalization, holiday) do not reduce variance – reducing
sigma would understate uncertainty and risk stockouts.

Date-basis semantics
--------------------
Every Modifier declares DATE_BASIS_ORDER or DATE_BASIS_DELIVERY explicitly.
Absence of a required date causes the modifier to be skipped with a warning,
never silently applied.

Fix (Feb 2026)
--------------
Eliminates double event-uplift present in legacy generate_proposal():
event uplift is now applied exactly once, as precedence-1 step.

Author: Desktop Order System Team
Date: February 2026
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .contracts import (
    AppliedModifier,
    DATE_BASIS_DELIVERY,
    DATE_BASIS_ORDER,
    DemandDistribution,
    Modifier,
    ModifierContext,
)

logger = logging.getLogger(__name__)

# Precedence constants
_PREC_EVENT = 1
_PREC_PROMO = 2
_PREC_CANNIB = 3
_PREC_HOLIDAY = 4


# ===========================================================================
# Public API
# ===========================================================================

def list_modifiers(context: ModifierContext) -> List["_ModifierWithMeta"]:
    """
    Evaluate all active modifier rules for the given context.

    Returns a list of Modifier-compatible objects sorted by precedence.
    Each object has ``value`` already estimated (not a bare template).

    Parameters
    ----------
    context : ModifierContext

    Returns
    -------
    List[Modifier]  – sorted ascending by precedence; empty if none active.
    """
    settings = context.settings
    mods: List[_ModifierWithMeta] = []

    # ---- 1. EVENT (precedence 1) ----------------------------------------
    event_enabled = settings.get("event_uplift", {}).get("enabled", {}).get("value", False)
    if event_enabled and context.event_rules and context.delivery_date:
        m = _eval_event_modifier(context)
        if m is not None:
            mods.append(m)

    # ---- 2. PROMO with MAX-absolute overlap resolution (precedence 2) ---
    promo_enabled = settings.get("promo_adjustment", {}).get("enabled", {}).get("value", False)
    if promo_enabled and context.promo_windows is not None and context.horizon_dates:
        candidates = _eval_promo_modifiers(context)
        if candidates:
            best = max(candidates, key=lambda m: abs(m.value - 1.0))
            mods.append(best)

    # ---- 3. CANNIBALIZATION (precedence 3) ------------------------------
    cannib_enabled = (
        settings.get("promo_cannibalization", {}).get("enabled", {}).get("value", False)
    )
    if cannib_enabled and context.promo_windows is not None and context.all_skus:
        m = _eval_cannibalization_modifier(context)
        if m is not None:
            mods.append(m)

    # ---- 4. HOLIDAY (precedence 4) --------------------------------------
    holiday_enabled = settings.get("holiday_modifier", {}).get("enabled", {}).get("value", False)
    if holiday_enabled and context.holidays and context.delivery_date:
        m = _eval_holiday_modifier(context)
        if m is not None:
            mods.append(m)

    return sorted(mods, key=lambda m: m.precedence)


def apply_modifiers(
    base_demand: DemandDistribution,
    sku_id: str,
    sku_obj: Any,
    horizon_dates: List[date],
    target_receipt_date: Optional[date],
    asof_date: date,
    settings: Dict[str, Any],
    all_skus: Optional[List[Any]] = None,
    promo_windows: Optional[List[Any]] = None,
    event_rules: Optional[List[Any]] = None,
    sales_records: Optional[List[Any]] = None,
    transactions: Optional[List[Any]] = None,
    holidays: Optional[List[Any]] = None,
) -> Tuple[DemandDistribution, List[AppliedModifier]]:
    """
    Apply all demand modifiers in precedence order and return the adjusted
    DemandDistribution together with the complete list of AppliedModifiers.

    This is the SINGLE pre-policy hook for BOTH legacy and CSL mode.

    Parameters
    ----------
    base_demand : DemandDistribution
        Output of build_demand_distribution(); no modifiers applied yet.
    sku_id, sku_obj, horizon_dates, target_receipt_date, asof_date, settings :
        Core context identifiers.
    all_skus, promo_windows, event_rules, sales_records, transactions :
        Pre-loaded domain data; pass None to skip that modifier class.
    holidays : list of holiday dicts from storage. Pass None to skip.

    Returns
    -------
    (adjusted_demand, applied_modifiers)
    """
    category = (getattr(sku_obj, "category", "") or "") if sku_obj else ""
    department = (getattr(sku_obj, "department", "") or "") if sku_obj else ""

    ctx = ModifierContext(
        sku_id=sku_id,
        category=category,
        department=department,
        order_date=asof_date,
        horizon_dates=horizon_dates,
        promo_windows=promo_windows if promo_windows is not None else [],
        event_rules=event_rules if event_rules is not None else [],
        holidays=holidays if holidays is not None else [],
        settings=settings,
        delivery_date=target_receipt_date,
        all_skus=all_skus if all_skus is not None else [],
        sales_records=sales_records if sales_records is not None else [],
        transactions=transactions if transactions is not None else [],
    )

    try:
        active_mods = list_modifiers(ctx)
    except Exception as exc:
        logger.warning("list_modifiers failed for %s: %s. Returning base demand.", sku_id, exc)
        return base_demand, []

    if not active_mods:
        return base_demand, []

    applied: List[AppliedModifier] = []
    cumulative = base_demand

    for mod in active_mods:
        mu_before = cumulative.mu_P
        try:
            new_demand = _apply_single_modifier(cumulative, mod)
        except Exception as exc:
            logger.warning("Modifier %r failed for %s: %s. Skipping.", mod.id, sku_id, exc)
            continue

        mu_after = new_demand.mu_P
        # Downlift (value < 1.0) only affects mu_P, not sigma
        scope = "mu_only" if (mod.kind == "multiplicative" and mod.value < 1.0) else "both"

        applied.append(AppliedModifier(
            name=mod.name,
            modifier_type=mod.modifier_type,
            scope=scope,
            multiplier=_effective_multiplier(mod, mu_before),
            stacking=mod.kind,
            date_range=(mod.start, mod.end) if (mod.start and mod.end) else None,
            source_sku=mod._source_sku,
            confidence=mod._confidence,
            note=mod._note,
            precedence=mod.precedence,
            date_basis=mod.date_basis,
            mu_before=mu_before,
            mu_after=mu_after,
        ))
        cumulative = new_demand

    # -------------------------------------------------------------------
    # Sigma policy: only scale upward; never reduce sigma
    # sigma_final = sigma_base × clamp(total_mult, 1.0, 2.5)
    # -------------------------------------------------------------------
    if applied:
        total_mult = cumulative.mu_P / max(base_demand.mu_P, 1e-10)
        sigma_mult = max(1.0, min(total_mult, 2.5))
        any_both = any(m.scope == "both" for m in applied)
        new_sigma = (base_demand.sigma_P * sigma_mult) if any_both else base_demand.sigma_P

        final_demand = replace(
            cumulative,
            sigma_P=max(0.0, new_sigma),
            sigma_adj_multiplier=sigma_mult if any_both else 1.0,
        )

        # Scale quantiles consistently with mu_P scaling
        if base_demand.quantiles and abs(total_mult - 1.0) > 1e-6:
            scaled_q = {k: max(0.0, v * total_mult) for k, v in base_demand.quantiles.items()}
            final_demand = replace(final_demand, quantiles=scaled_q)
    else:
        final_demand = cumulative

    return final_demand, applied


# ===========================================================================
# Internal: _ModifierWithMeta proxy class
# ===========================================================================

class _ModifierWithMeta:
    """
    Thin proxy around a frozen ``Modifier`` that adds mutable metadata slots.

    ``Modifier`` is a frozen dataclass; we can't attach runtime metadata such
    as free-text notes, confidence strings, or source-SKU annotations directly.
    This proxy forwards all Modifier field accesses and exposes extra slots.
    """

    __slots__ = (
        "_mod",
        "_note",
        "_confidence",
        "_source_sku",
    )

    def __init__(
        self,
        mod: Modifier,
        *,
        _note: str = "",
        _confidence: str = "",
        _source_sku: Optional[str] = None,
    ) -> None:
        object.__setattr__(self, "_mod", mod)
        object.__setattr__(self, "_note", _note)
        object.__setattr__(self, "_confidence", _confidence)
        object.__setattr__(self, "_source_sku", _source_sku)

    # ------------------------------------------------------------------
    # Forward all Modifier fields transparently
    # ------------------------------------------------------------------
    @property
    def id(self) -> str:                return self._mod.id
    @property
    def name(self) -> str:              return self._mod.name
    @property
    def scope_type(self) -> str:        return self._mod.scope_type
    @property
    def scope_key(self) -> str:         return self._mod.scope_key
    @property
    def date_basis(self) -> str:        return self._mod.date_basis
    @property
    def kind(self) -> str:              return self._mod.kind
    @property
    def value(self) -> float:           return self._mod.value
    @property
    def precedence(self) -> int:        return self._mod.precedence
    @property
    def modifier_type(self) -> str:     return self._mod.modifier_type
    @property
    def start(self) -> Optional[date]:  return self._mod.start
    @property
    def end(self) -> Optional[date]:    return self._mod.end
    @property
    def note(self) -> str:              return self._note
    @property
    def confidence(self) -> str:        return self._confidence
    @property
    def source_sku(self) -> Optional[str]: return self._source_sku

    def is_active_for_date(self, check_date: date) -> bool:
        return self._mod.is_active_for_date(check_date)

    def __repr__(self) -> str:
        return (
            f"_ModifierWithMeta(id={self.id!r}, type={self.modifier_type!r}, "
            f"value={self.value:.4f}, prec={self.precedence})"
        )


# ===========================================================================
# Internal: Modifier evaluation helpers
# ===========================================================================

def _eval_event_modifier(ctx: ModifierContext) -> Optional["_ModifierWithMeta"]:
    """Evaluate event uplift; return a _ModifierWithMeta or None."""
    try:
        try:
            from src.domain.event_uplift import apply_event_uplift_to_forecast
        except ImportError:
            from domain.event_uplift import apply_event_uplift_to_forecast
    except ImportError:
        logger.warning("event_uplift module unavailable; skipping.")
        return None

    sku_obj_real = next(
        (s for s in ctx.all_skus if getattr(s, "sku", None) == ctx.sku_id), None
    )
    if sku_obj_real is None:
        return None

    n = max(len(ctx.horizon_dates), 1)
    baseline_fc: Dict[date, float] = {d: 1.0 / n for d in ctx.horizon_dates}

    try:
        adjusted_fc, explain = apply_event_uplift_to_forecast(
            sku_obj=sku_obj_real,
            delivery_date=ctx.delivery_date,
            horizon_dates=ctx.horizon_dates,
            baseline_forecast=baseline_fc,
            event_rules=ctx.event_rules,
            all_skus=ctx.all_skus,
            sales_records=ctx.sales_records,
            settings=ctx.settings,
        )
    except Exception as exc:
        logger.warning("Event uplift estimation failed for %s: %s", ctx.sku_id, exc)
        return None

    if explain.rule_matched is None:
        return None
    m_i = float(explain.m_i) if explain.m_i is not None else 1.0
    if abs(m_i - 1.0) < 1e-6:
        return None

    reason = getattr(explain.rule_matched, "reason", "") or ""
    start_d = getattr(explain, "impact_start_date", ctx.delivery_date) or ctx.delivery_date
    end_d = getattr(explain, "impact_end_date", ctx.delivery_date) or ctx.delivery_date
    note = (
        f"U_store={explain.u_store_day:.3f}, beta={explain.beta_i:.3f}, "
        f"m_i={m_i:.3f}, "
        f"P{int((explain.u_quantile or 0) * 100)}, "
        f"{explain.u_fallback_level}"
    )
    mod = Modifier(
        id=f"event_{reason}_{ctx.sku_id}_{ctx.delivery_date}",
        name=f"event_uplift_{reason}" if reason else "event_uplift",
        scope_type="sku", scope_key=ctx.sku_id,
        date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
        value=m_i, precedence=_PREC_EVENT, modifier_type="event",
        start=start_d, end=end_d,
    )
    return _ModifierWithMeta(mod, _note=note, _confidence="", _source_sku=None)


def _eval_promo_modifiers(ctx: ModifierContext) -> List["_ModifierWithMeta"]:
    """
    Evaluate promo uplift for all active windows.
    Returns a list of candidates; caller applies MAX-absolute rule.
    """
    try:
        try:
            from src.domain.promo_uplift import estimate_uplift
            from src.promo_calendar import is_promo
        except ImportError:
            from domain.promo_uplift import estimate_uplift
            from promo_calendar import is_promo
    except ImportError:
        logger.warning("promo_uplift / promo_calendar unavailable; skipping.")
        return []

    promo_days = [d for d in ctx.horizon_dates if is_promo(ctx.sku_id, d, ctx.promo_windows)]
    if not promo_days:
        return []

    try:
        uplift_report = estimate_uplift(
            sku_id=ctx.sku_id,
            promo_windows=ctx.promo_windows,
            sales_records=ctx.sales_records,
            transactions=ctx.transactions,
            all_skus=ctx.all_skus,
            asof_date=ctx.order_date,
        )
    except Exception as exc:
        logger.warning("Promo uplift estimation failed for %s: %s", ctx.sku_id, exc)
        return []

    if uplift_report is None or uplift_report.uplift_factor <= 0:
        return []

    uplift_factor = float(uplift_report.uplift_factor)
    promo_frac = len(promo_days) / max(len(ctx.horizon_dates), 1)

    promo_adj = ctx.settings.get("promo_adjustment", {})
    smoothing_enabled = promo_adj.get("smoothing_enabled", {}).get("value", False)
    ramp_in = int(promo_adj.get("ramp_in_days", {}).get("value", 0))
    ramp_out = int(promo_adj.get("ramp_out_days", {}).get("value", 0))

    smoothing_mult = 1.0
    if smoothing_enabled and (ramp_in > 0 or ramp_out > 0):
        smoothing_mult = _compute_smoothing_multiplier(
            horizon_dates=ctx.horizon_dates,
            promo_days=promo_days,
            ramp_in_days=ramp_in,
            ramp_out_days=ramp_out,
            uplift_factor=uplift_factor,
        )

    effective_mult = promo_frac * uplift_factor * smoothing_mult + (1.0 - promo_frac) * 1.0
    note = (
        f"promo_frac={promo_frac:.2f}, uplift={uplift_factor:.3f}, "
        f"smoothing={smoothing_mult:.3f}, effective={effective_mult:.3f}"
    )
    mod = Modifier(
        id=f"promo_{ctx.sku_id}_{ctx.order_date}",
        name="promo_uplift",
        scope_type="sku", scope_key=ctx.sku_id,
        date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
        value=effective_mult, precedence=_PREC_PROMO, modifier_type="promo",
        start=promo_days[0], end=promo_days[-1],
    )
    return [_ModifierWithMeta(mod, _note=note,
                              _confidence=getattr(uplift_report, "confidence", ""),
                              _source_sku=None)]


def _eval_cannibalization_modifier(ctx: ModifierContext) -> Optional["_ModifierWithMeta"]:
    """Evaluate cannibalization downlift; return a _ModifierWithMeta or None."""
    try:
        try:
            from src.domain.promo_uplift import estimate_cannibalization_downlift
            from src.promo_calendar import is_promo
        except ImportError:
            from domain.promo_uplift import estimate_cannibalization_downlift
            from promo_calendar import is_promo
    except ImportError:
        logger.warning("promo_uplift unavailable; skipping cannibalization.")
        return None

    non_promo_days = [
        d for d in ctx.horizon_dates
        if not is_promo(ctx.sku_id, d, ctx.promo_windows)
    ]
    if not non_promo_days:
        return None

    try:
        downlift_report = estimate_cannibalization_downlift(
            sku_id=ctx.sku_id,
            promo_windows=ctx.promo_windows,
            sales_records=ctx.sales_records,
            transactions=ctx.transactions,
            all_skus=ctx.all_skus,
            asof_date=ctx.order_date,
        )
    except Exception as exc:
        logger.warning("Cannibalization estimation failed for %s: %s", ctx.sku_id, exc)
        return None

    if downlift_report is None or downlift_report.downlift_factor >= 1.0:
        return None

    non_promo_frac = len(non_promo_days) / max(len(ctx.horizon_dates), 1)
    effective = (1.0 - non_promo_frac) * 1.0 + non_promo_frac * downlift_report.downlift_factor
    driver = downlift_report.driver_sku
    note = (
        f"driver={driver}, downlift={downlift_report.downlift_factor:.3f}, "
        f"non_promo_frac={non_promo_frac:.2f}"
    )
    mod = Modifier(
        id=f"cannib_{ctx.sku_id}_{driver}_{ctx.order_date}",
        name=f"cannibalization_{driver}",
        scope_type="sku", scope_key=ctx.sku_id,
        date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
        value=effective, precedence=_PREC_CANNIB, modifier_type="cannibalization",
        start=non_promo_days[0] if non_promo_days else None,
        end=non_promo_days[-1] if non_promo_days else None,
    )
    return _ModifierWithMeta(mod, _note=note,
                             _confidence=getattr(downlift_report, "confidence", ""),
                             _source_sku=driver)


def _eval_holiday_modifier(ctx: ModifierContext) -> Optional["_ModifierWithMeta"]:
    """
    Evaluate holiday demand multiplier.

    Uses ``demand_multiplier`` field from each holiday dict (default from
    ``settings.holiday_modifier.default_multiplier``).
    Multiple overlapping holidays → MAX-absolute rule (consistent with promo).
    """
    if not ctx.delivery_date:
        return None

    default_mult = float(
        ctx.settings.get("holiday_modifier", {})
        .get("default_multiplier", {})
        .get("value", 1.0)
    )

    candidates = []
    for h in ctx.holidays:
        h_name = h.get("name", "holiday")
        mult = float(h.get("demand_multiplier", default_mult))
        if abs(mult - 1.0) < 1e-6:
            continue

        h_date_str = h.get("date") or h.get("start_date") or ""
        h_end_str = h.get("end_date") or h_date_str
        try:
            h_start = date.fromisoformat(h_date_str) if h_date_str else None
            h_end = date.fromisoformat(h_end_str) if h_end_str else h_start
        except ValueError:
            continue

        if h_start and h_end:
            if not (h_start <= ctx.delivery_date <= h_end):
                continue
        elif h_start:
            if ctx.delivery_date != h_start:
                continue
        else:
            continue

        candidates.append((h_name, mult, h_start, h_end))

    if not candidates:
        return None

    # MAX-absolute: select holiday with largest |multiplier − 1|
    best = max(candidates, key=lambda c: abs(c[1] - 1.0))
    h_name, mult, h_start, h_end = best

    mod = Modifier(
        id=f"holiday_{h_name}_{ctx.delivery_date}",
        name=f"holiday_{h_name}",
        scope_type="global", scope_key="",
        date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
        value=mult, precedence=_PREC_HOLIDAY, modifier_type="holiday",
        start=h_start, end=h_end,
    )
    return _ModifierWithMeta(mod, _note=f"holiday={h_name}, multiplier={mult:.3f}",
                             _confidence="", _source_sku=None)


# ===========================================================================
# Internal: single modifier application + utilities
# ===========================================================================

def _apply_single_modifier(
    demand: DemandDistribution,
    mod: "_ModifierWithMeta",
) -> DemandDistribution:
    """Apply one modifier to mu_P; sigma unchanged (handled by caller)."""
    value = float(mod.value)
    if mod.kind == "multiplicative":
        new_mu = max(0.0, demand.mu_P * value)
    else:
        new_mu = max(0.0, demand.mu_P + value)
    return replace(demand, mu_P=new_mu)


def _effective_multiplier(mod: "_ModifierWithMeta", mu_before: float) -> float:
    """Express modifier impact as an effective multiplier (for AppliedModifier.multiplier)."""
    if mod.kind == "multiplicative":
        return float(mod.value)
    if mu_before > 0:
        return 1.0 + float(mod.value) / mu_before
    return 1.0 + float(mod.value)


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
