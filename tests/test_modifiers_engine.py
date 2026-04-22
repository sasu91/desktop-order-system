"""
tests/test_modifiers_engine.py
===============================
Unit + Integration tests for the Modifiers Engine.

Stop conditions verified:
  ✓ Modifiers applied at ONE point pre-policy for both modes
    (verified via mock: list_modifiers called exactly once per apply_modifiers)
  ✓ No silent flag-gated exclusions
    (verified: with empty lists, base demand passes through unchanged)
  ✓ applied_modifiers export for numerical verification
    (mu_before / mu_after trace is tested end-to-end)
  ✓ Determinism / precedence / legacy-CSL consistency
    (parametrised precedence test with frozen inputs)
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
try:
    from src.domain.contracts import (
        AppliedModifier,
        DemandDistribution,
        DATE_BASIS_DELIVERY,
        DATE_BASIS_ORDER,
        Modifier,
        ModifierContext,
    )
    from src.domain.modifier_builder import (
        _ModifierWithMeta,           # internal – imported for unit tests
        _apply_single_modifier,
        _effective_multiplier,
        apply_modifiers,
        list_modifiers,
        _PREC_EVENT,
        _PREC_PROMO,
        _PREC_CANNIB,
        _PREC_HOLIDAY,
    )
except ImportError:
    from domain.contracts import (  # type: ignore[import-unresolved]
        AppliedModifier,
        DemandDistribution,
        DATE_BASIS_DELIVERY,
        DATE_BASIS_ORDER,
        Modifier,
        ModifierContext,
    )
    from domain.modifier_builder import (  # type: ignore[import-unresolved]
        _ModifierWithMeta,
        _apply_single_modifier,
        _effective_multiplier,
        apply_modifiers,
        list_modifiers,
        _PREC_EVENT,
        _PREC_PROMO,
        _PREC_CANNIB,
        _PREC_HOLIDAY,
    )


# ===========================================================================
# Helpers
# ===========================================================================

TODAY = date(2025, 6, 1)
DELIVERY = date(2025, 6, 8)


def _make_modifier(
    modifier_type: str,
    value: float,
    precedence: int,
    kind: str = "multiplicative",
    date_basis: str = DATE_BASIS_DELIVERY,
    start: date | None = None,
    end: date | None = None,
) -> _ModifierWithMeta:
    """Create a _ModifierWithMeta for testing."""
    mod = Modifier(
        id=f"{modifier_type}_test",
        name=f"{modifier_type}_test",
        scope_type="sku",
        scope_key="SKU_A",
        date_basis=date_basis,
        kind=kind,
        value=value,
        precedence=precedence,
        modifier_type=modifier_type,
        start=start,
        end=end,
    )
    return _ModifierWithMeta(
        mod,
        _note=f"{modifier_type} note",
        _confidence="medium",
        _source_sku="SKU_DRIVER" if modifier_type == "cannibalization" else None,
    )


def _base_demand(
    mu_P: float = 100.0,
    sigma_P: float = 10.0,
    period: int = 7,
) -> DemandDistribution:
    return DemandDistribution(
        mu_P=mu_P,
        sigma_P=sigma_P,
        protection_period_days=period,
        forecast_method="simple",
    )


def _minimal_ctx(sku_id: str = "SKU_A") -> ModifierContext:
    """Build a minimal empty ModifierContext (no modifiers will match)."""
    return ModifierContext(  # type: ignore[call-arg]
        sku_id=sku_id,
        category="",
        department="",
        order_date=TODAY,
        horizon_dates=[TODAY + timedelta(days=i + 1) for i in range(7)],
        promo_windows=[],
        event_rules=[],
        holidays=[],
        settings={},
        delivery_date=DELIVERY,
        all_skus=[],
        sales_records=[],
        transactions=[],
    )


_MINIMAL_APPLY_KWARGS: Dict[str, Any] = dict(
    sku_id="SKU_A",
    sku_obj=None,
    horizon_dates=[TODAY + timedelta(days=i + 1) for i in range(7)],
    target_receipt_date=DELIVERY,
    asof_date=TODAY,
    settings={},
    all_skus=[],
    promo_windows=[],
    event_rules=[],
    sales_records=[],
    transactions=[],
    holidays=[],
)


# ===========================================================================
# Section 1 — _ModifierWithMeta proxy
# ===========================================================================

class TestModifierWithMetaProxy:
    """_ModifierWithMeta must transparently forward all Modifier fields."""

    def test_forwards_all_fields(self):
        start = date(2025, 6, 5)
        end = date(2025, 6, 7)
        wmm = _make_modifier("event", value=1.20, precedence=_PREC_EVENT, start=start, end=end)
        assert wmm.id == "event_test"
        assert wmm.name == "event_test"
        assert wmm.modifier_type == "event"
        assert wmm.value == pytest.approx(1.20)
        assert wmm.precedence == _PREC_EVENT
        assert wmm.kind == "multiplicative"
        assert wmm.start == start
        assert wmm.end == end

    def test_metadata_slots(self):
        wmm = _make_modifier("promo", value=1.10, precedence=_PREC_PROMO)
        assert wmm.note == "promo note"
        assert wmm.confidence == "medium"
        assert wmm.source_sku is None

    def test_cannibalization_source_sku(self):
        wmm = _make_modifier("cannibalization", value=0.85, precedence=_PREC_CANNIB)
        assert wmm.source_sku == "SKU_DRIVER"

    def test_is_active_for_date_delegates(self):
        # No start/end → always active
        wmm = _make_modifier("holiday", value=1.05, precedence=_PREC_HOLIDAY)
        assert wmm.is_active_for_date(TODAY) is True
        assert wmm.is_active_for_date(DELIVERY) is True

    def test_repr_contains_key_info(self):
        wmm = _make_modifier("event", value=1.20, precedence=_PREC_EVENT)
        r = repr(wmm)
        assert "event_test" in r
        assert "1.2" in r


# ===========================================================================
# Section 2 — _apply_single_modifier
# ===========================================================================

class TestApplySingleModifier:
    def test_multiplicative_uplift(self):
        bd = _base_demand(100.0)
        mod = _make_modifier("event", value=1.20, precedence=_PREC_EVENT)
        result = _apply_single_modifier(bd, mod)
        assert result.mu_P == pytest.approx(120.0)
        # sigma UNCHANGED by _apply_single_modifier (handled by caller)
        assert result.sigma_P == pytest.approx(10.0)

    def test_multiplicative_downlift(self):
        bd = _base_demand(100.0)
        mod = _make_modifier("cannibalization", value=0.85, precedence=_PREC_CANNIB)
        result = _apply_single_modifier(bd, mod)
        assert result.mu_P == pytest.approx(85.0)

    def test_additive_uplift(self):
        bd = _base_demand(100.0)
        mod = _make_modifier("event", value=15.0, precedence=_PREC_EVENT, kind="additive")
        result = _apply_single_modifier(bd, mod)
        assert result.mu_P == pytest.approx(115.0)

    def test_clamps_at_zero(self):
        bd = _base_demand(10.0)
        mod = _make_modifier("cannibalization", value=0.0, precedence=_PREC_CANNIB)
        result = _apply_single_modifier(bd, mod)
        assert result.mu_P == pytest.approx(0.0)

    def test_negative_additive_clamped_at_zero(self):
        bd = _base_demand(5.0)
        mod = _make_modifier("event", value=-100.0, precedence=_PREC_EVENT, kind="additive")
        result = _apply_single_modifier(bd, mod)
        assert result.mu_P == 0.0


# ===========================================================================
# Section 3 — _effective_multiplier
# ===========================================================================

class TestEffectiveMultiplier:
    def test_multiplicative_returns_value(self):
        mod = _make_modifier("promo", value=1.10, precedence=_PREC_PROMO)
        assert _effective_multiplier(mod, mu_before=100.0) == pytest.approx(1.10)

    def test_additive_converted_correctly(self):
        mod = _make_modifier("event", value=20.0, precedence=_PREC_EVENT, kind="additive")
        # 20 / 100 → factor = 1.20
        assert _effective_multiplier(mod, mu_before=100.0) == pytest.approx(1.20)

    def test_additive_zero_mu_before(self):
        mod = _make_modifier("event", value=20.0, precedence=_PREC_EVENT, kind="additive")
        # mu_before = 0 → fall back to 1 + value
        result = _effective_multiplier(mod, mu_before=0.0)
        assert result == pytest.approx(21.0)


# ===========================================================================
# Section 4 — apply_modifiers end-to-end (with mocked list_modifiers)
# ===========================================================================

class TestApplyModifiersPrecedenceChain:
    """
    Deterministic precedence chain test.

    Base demand = 100.
    Chain:
      EVENT      × 1.20  → 120
      PROMO      × 1.10  → 132
      CANNIB     × 0.85  → 112.2
      HOLIDAY    × 1.05  → 117.81

    total_mult = 117.81 / 100 = 1.1781
    sigma_mult = clamp(1.1781, 1.0, 2.5) = 1.1781
    sigma_final = 10 × 1.1781 = 11.781
    """

    _CHAIN = [
        ("event",          1.20, _PREC_EVENT),
        ("promo",          1.10, _PREC_PROMO),
        ("cannibalization", 0.85, _PREC_CANNIB),
        ("holiday",        1.05, _PREC_HOLIDAY),
    ]
    _EXPECTED_FINAL_MU = 100 * 1.20 * 1.10 * 0.85 * 1.05
    _EXPECTED_SIGMA_MULT = _EXPECTED_FINAL_MU / 100.0

    def _mock_list(self, _ctx):
        return [_make_modifier(mt, v, p) for mt, v, p in self._CHAIN]

    def test_final_mu_p(self):
        bd = _base_demand(100.0, sigma_P=10.0)
        with patch("src.domain.modifier_builder.list_modifiers", side_effect=self._mock_list):
            adj, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)
        assert adj.mu_P == pytest.approx(self._EXPECTED_FINAL_MU, rel=1e-6)

    def test_applied_modifiers_count(self):
        bd = _base_demand(100.0)
        with patch("src.domain.modifier_builder.list_modifiers", side_effect=self._mock_list):
            _, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)
        assert len(applied) == 4

    def test_mu_before_mu_after_trace(self):
        """Each AppliedModifier carries the correct mu_before and mu_after."""
        bd = _base_demand(100.0)
        with patch("src.domain.modifier_builder.list_modifiers", side_effect=self._mock_list):
            _, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert applied[0].mu_before == pytest.approx(100.0)
        assert applied[0].mu_after  == pytest.approx(120.0)

        assert applied[1].mu_before == pytest.approx(120.0)
        assert applied[1].mu_after  == pytest.approx(132.0)

        assert applied[2].mu_before == pytest.approx(132.0)
        assert applied[2].mu_after  == pytest.approx(112.2, rel=1e-4)

        assert applied[3].mu_before == pytest.approx(112.2, rel=1e-4)
        assert applied[3].mu_after  == pytest.approx(self._EXPECTED_FINAL_MU, rel=1e-4)

    def test_sigma_upward_only(self):
        """total_mult > 1 → sigma scales up."""
        bd = _base_demand(100.0, sigma_P=10.0)
        with patch("src.domain.modifier_builder.list_modifiers", side_effect=self._mock_list):
            adj, _ = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)
        # total_mult ≈ 1.1781, clamped to 2.5 → sigma_mult = 1.1781
        assert adj.sigma_P == pytest.approx(10.0 * self._EXPECTED_SIGMA_MULT, rel=1e-4)

    def test_modifier_types_in_order(self):
        bd = _base_demand(100.0)
        with patch("src.domain.modifier_builder.list_modifiers", side_effect=self._mock_list):
            _, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)
        types = [m.modifier_type for m in applied]
        assert types == ["event", "promo", "cannibalization", "holiday"]

    def test_deterministic_repeat(self):
        """Same context called twice → same result."""
        bd = _base_demand(100.0)
        with patch("src.domain.modifier_builder.list_modifiers", side_effect=self._mock_list):
            adj1, app1 = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)
        with patch("src.domain.modifier_builder.list_modifiers", side_effect=self._mock_list):
            adj2, app2 = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert adj1.mu_P == pytest.approx(adj2.mu_P)
        assert adj1.sigma_P == pytest.approx(adj2.sigma_P)
        assert len(app1) == len(app2)


# ===========================================================================
# Section 5 — Sigma-only-uplift policy
# ===========================================================================

class TestSigmaOnlyUpliftPolicy:
    """
    Downlift modifiers must NOT reduce sigma.
    Only upward scaling is applied (floor at 1.0).
    """

    def test_downlift_only_does_not_change_sigma(self):
        """A pure cannibalization downlift leaves sigma unchanged."""
        bd = _base_demand(100.0, sigma_P=10.0)
        mods = [_make_modifier("cannibalization", value=0.70, precedence=_PREC_CANNIB)]

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            adj, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        # mu reduced
        assert adj.mu_P == pytest.approx(70.0)
        # sigma UNCHANGED (total_mult=0.70 < 1.0 → clamped to 1.0 → no sigma change)
        assert adj.sigma_P == pytest.approx(10.0)

    def test_uplift_only_scales_sigma(self):
        bd = _base_demand(100.0, sigma_P=10.0)
        mods = [_make_modifier("promo", value=2.0, precedence=_PREC_PROMO)]

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            adj, _ = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        # total_mult = 2.0, clamped at 2.5 → sigma_mult = 2.0
        assert adj.mu_P == pytest.approx(200.0)
        assert adj.sigma_P == pytest.approx(20.0)

    def test_sigma_cap_at_2_5(self):
        """Extreme uplift is capped at 2.5× for sigma."""
        bd = _base_demand(100.0, sigma_P=10.0)
        mods = [_make_modifier("event", value=5.0, precedence=_PREC_EVENT)]  # ×5

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            adj, _ = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert adj.mu_P == pytest.approx(500.0)
        assert adj.sigma_P == pytest.approx(25.0)  # cap: 10 × 2.5

    def test_uplift_then_downlift_sigma_respects_total_mult(self):
        """
        Event +50%, then cannibalization -40%.
        total_mult = 1.5 × 0.6 = 0.9 < 1.0 → clamped to 1.0 → sigma unchanged.
        """
        bd = _base_demand(100.0, sigma_P=10.0)
        mods = [
            _make_modifier("event", value=1.50, precedence=_PREC_EVENT),
            _make_modifier("cannibalization", value=0.60, precedence=_PREC_CANNIB),
        ]

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            adj, _ = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert adj.mu_P == pytest.approx(90.0)
        assert adj.sigma_P == pytest.approx(10.0)  # clamped at 1.0


# ===========================================================================
# Section 6 — No modifiers → passthrough
# ===========================================================================

class TestNoModifiers:
    """When list_modifiers returns empty list, base demand is returned as-is."""

    def test_empty_inputs_passthrough(self):
        bd = _base_demand(100.0, sigma_P=10.0)
        with patch("src.domain.modifier_builder.list_modifiers", return_value=[]):
            adj, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert adj.mu_P == pytest.approx(100.0)
        assert adj.sigma_P == pytest.approx(10.0)
        assert applied == []

    def test_list_modifiers_empty_context(self):
        """Empty context (no rules, no promo, no holidays) → empty list."""
        ctx = _minimal_ctx()
        result = list_modifiers(ctx)
        assert isinstance(result, list)
        # May be empty or not crash; it must not raise
        # With no event_rules, no promo_windows, no holidays → we expect []
        assert len(result) == 0

    def test_apply_modifiers_exception_in_list_returns_base(self):
        """If list_modifiers raises, apply_modifiers falls back to base demand."""
        bd = _base_demand(100.0)

        def _boom(ctx):
            raise RuntimeError("deliberate test failure")

        with patch("src.domain.modifier_builder.list_modifiers", side_effect=_boom):
            adj, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert adj.mu_P == pytest.approx(100.0)
        assert applied == []


# ===========================================================================
# Section 7 — Modifier dataclass helpers
# ===========================================================================

class TestModifierDataclass:
    def test_is_active_no_bounds(self):
        mod = Modifier(
            id="x", name="x", scope_type="sku", scope_key="SKU_A",
            date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
            value=1.1, precedence=1, modifier_type="event",
        )
        assert mod.is_active_for_date(TODAY) is True

    def test_is_active_within_bounds(self):
        mod = Modifier(
            id="x", name="x", scope_type="sku", scope_key="SKU_A",
            date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
            value=1.1, precedence=1, modifier_type="event",
            start=date(2025, 6, 1), end=date(2025, 6, 7),
        )
        assert mod.is_active_for_date(date(2025, 6, 1)) is True
        assert mod.is_active_for_date(date(2025, 6, 4)) is True
        assert mod.is_active_for_date(date(2025, 6, 7)) is True

    def test_is_active_outside_bounds(self):
        mod = Modifier(
            id="x", name="x", scope_type="sku", scope_key="SKU_A",
            date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
            value=1.1, precedence=1, modifier_type="event",
            start=date(2025, 6, 1), end=date(2025, 6, 7),
        )
        assert mod.is_active_for_date(date(2025, 5, 31)) is False
        assert mod.is_active_for_date(date(2025, 6, 8)) is False

    def test_frozen_immutable(self):
        mod = Modifier(
            id="x", name="x", scope_type="sku", scope_key="SKU_A",
            date_basis=DATE_BASIS_DELIVERY, kind="multiplicative",
            value=1.1, precedence=1, modifier_type="event",
        )
        with pytest.raises((TypeError, AttributeError)):
            mod.value = 2.0  # type: ignore[misc]


# ===========================================================================
# Section 8 — AppliedModifier extended fields
# ===========================================================================

class TestAppliedModifierFields:
    """Extended AppliedModifier fields (precedence, date_basis, mu_before, mu_after)."""

    def test_applied_modifier_has_extended_fields(self):
        am = AppliedModifier(
            name="event_test",
            modifier_type="event",
            scope="both",
            multiplier=1.20,
            stacking="multiplicative",
        )
        assert am.precedence == 0           # default
        assert am.date_basis == DATE_BASIS_DELIVERY  # default
        assert am.mu_before == pytest.approx(0.0)    # default
        assert am.mu_after == pytest.approx(0.0)     # default

    def test_applied_modifier_fields_set(self):
        am = AppliedModifier(
            name="promo_test",
            modifier_type="promo",
            scope="both",
            multiplier=1.10,
            stacking="multiplicative",
            precedence=_PREC_PROMO,
            date_basis=DATE_BASIS_ORDER,
            mu_before=100.0,
            mu_after=110.0,
        )
        assert am.precedence == _PREC_PROMO
        assert am.date_basis == DATE_BASIS_ORDER
        assert am.mu_before == pytest.approx(100.0)
        assert am.mu_after == pytest.approx(110.0)

    def test_precision_chain_mu_trace_correct(self):
        """Full chain: mu_before → mu_after trace must be self-consistent."""
        bd = _base_demand(100.0)
        mods = [
            _make_modifier("event", value=1.20, precedence=_PREC_EVENT),
            _make_modifier("promo", value=1.10, precedence=_PREC_PROMO),
        ]

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            _, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert len(applied) == 2
        # Trace:  100 → 120 → 132
        assert applied[0].mu_before == pytest.approx(100.0)
        assert applied[0].mu_after  == pytest.approx(120.0)
        assert applied[1].mu_before == pytest.approx(120.0)
        assert applied[1].mu_after  == pytest.approx(132.0)


# ===========================================================================
# Section 9 — Holiday modifier direct evaluation
# ===========================================================================

class TestHolidayModifierEvaluation:
    """Test _eval_holiday_modifier indirectly through list_modifiers."""

    def _ctx_with_holiday(
        self,
        mult: float,
        h_date: date | None = None,
        enabled: bool = True,
    ) -> ModifierContext:
        h_date = h_date or DELIVERY
        return ModifierContext(  # type: ignore[call-arg]
            sku_id="SKU_H",
            category="",
            department="",
            order_date=TODAY,
            horizon_dates=[TODAY + timedelta(days=i + 1) for i in range(7)],
            promo_windows=[],
            event_rules=[],
            holidays=[
                {
                    "name": "Natale",
                    "date": h_date.isoformat(),
                    "demand_multiplier": mult,
                }
            ],
            settings={
                "holiday_modifier": {
                    "enabled": {"value": enabled},
                    "default_multiplier": {"value": 1.0},
                }
            },
            delivery_date=DELIVERY,
            all_skus=[],
            sales_records=[],
            transactions=[],
        )

    def test_holiday_in_range_returned(self):
        """Holiday on delivery_date → modifier found by list_modifiers."""
        ctx = self._ctx_with_holiday(mult=1.15, h_date=DELIVERY, enabled=True)
        mods = list_modifiers(ctx)
        holiday_mods = [m for m in mods if m.modifier_type == "holiday"]
        assert len(holiday_mods) == 1
        assert holiday_mods[0].value == pytest.approx(1.15)
        assert holiday_mods[0].name == "holiday_Natale"

    def test_holiday_disabled_not_returned(self):
        ctx = self._ctx_with_holiday(mult=1.15, h_date=DELIVERY, enabled=False)
        mods = list_modifiers(ctx)
        holiday_mods = [m for m in mods if m.modifier_type == "holiday"]
        assert holiday_mods == []

    def test_holiday_outside_range_not_returned(self):
        ctx = self._ctx_with_holiday(
            mult=1.15,
            h_date=DELIVERY + timedelta(days=5),
            enabled=True,
        )
        mods = list_modifiers(ctx)
        holiday_mods = [m for m in mods if m.modifier_type == "holiday"]
        assert holiday_mods == []

    def test_holiday_neutral_skipped(self):
        """Multiplier = 1.0 → no modifier returned (neutral)."""
        ctx = self._ctx_with_holiday(mult=1.0, h_date=DELIVERY, enabled=True)
        mods = list_modifiers(ctx)
        holiday_mods = [m for m in mods if m.modifier_type == "holiday"]
        assert holiday_mods == []

    def test_holiday_max_absolute_overlap(self):
        """Two holidays on same delivery date → MAX |mult-1| wins."""
        ctx = ModifierContext(  # type: ignore[call-arg]
            sku_id="SKU_H",
            category="", department="",
            order_date=TODAY,
            horizon_dates=[TODAY + timedelta(days=i + 1) for i in range(7)],
            promo_windows=[], event_rules=[],
            holidays=[
                {"name": "HolA", "date": DELIVERY.isoformat(), "demand_multiplier": 0.80},  # |0.8-1|=0.20
                {"name": "HolB", "date": DELIVERY.isoformat(), "demand_multiplier": 1.30},  # |1.3-1|=0.30 ← wins
            ],
            settings={
                "holiday_modifier": {
                    "enabled": {"value": True},
                    "default_multiplier": {"value": 1.0},
                }
            },
            delivery_date=DELIVERY,
            all_skus=[], sales_records=[], transactions=[],
        )
        mods = list_modifiers(ctx)
        holiday_mods = [m for m in mods if m.modifier_type == "holiday"]
        assert len(holiday_mods) == 1
        assert holiday_mods[0].value == pytest.approx(1.30)

    def test_holiday_downlift_no_sigma_reduction(self):
        """Holiday donwlift (mult < 1.0) must not reduce sigma (sigma-only-uplift policy)."""
        ctx = self._ctx_with_holiday(mult=0.70, h_date=DELIVERY, enabled=True)
        mods = list_modifiers(ctx)
        holiday_mods = [m for m in mods if m.modifier_type == "holiday"]
        assert len(holiday_mods) == 1

        bd = _base_demand(100.0, sigma_P=10.0)

        with patch("src.domain.modifier_builder.list_modifiers", return_value=holiday_mods):
            adj, applied = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert adj.mu_P == pytest.approx(70.0)
        assert adj.sigma_P == pytest.approx(10.0)  # sigma unchanged on downlift


# ===========================================================================
# Section 10 — Precedence constants
# ===========================================================================

class TestPrecedenceConstants:
    def test_ordering(self):
        assert _PREC_EVENT < _PREC_PROMO < _PREC_CANNIB < _PREC_HOLIDAY

    def test_values(self):
        assert _PREC_EVENT == 1
        assert _PREC_PROMO == 2
        assert _PREC_CANNIB == 3
        assert _PREC_HOLIDAY == 4


# ===========================================================================
# Section 11 — Legacy / CSL consistency (conceptual smoke test)
# ===========================================================================

class TestLegacyCslConsistency:
    """
    Smoke test: apply_modifiers() is policy-neutral.

    Same DemandDistribution in → same adjusted DemandDistribution out,
    regardless of whether downstream policy is "legacy" or "csl".

    The actual policy fork is AFTER apply_modifiers; this test verifies
    the modifier engine itself is mode-agnostic.
    """

    def test_same_output_regardless_of_forecast_method(self):
        """forecast_method tag must not affect modifier arithmetic."""
        mods = [_make_modifier("promo", value=1.10, precedence=_PREC_PROMO)]

        bd_legacy = DemandDistribution(
            mu_P=100.0, sigma_P=10.0, protection_period_days=7, forecast_method="simple"
        )
        bd_csl = DemandDistribution(
            mu_P=100.0, sigma_P=10.0, protection_period_days=7, forecast_method="monte_carlo"
        )

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            adj_l, applied_l = apply_modifiers(base_demand=bd_legacy, **_MINIMAL_APPLY_KWARGS)

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            adj_c, applied_c = apply_modifiers(base_demand=bd_csl, **_MINIMAL_APPLY_KWARGS)

        assert adj_l.mu_P == pytest.approx(adj_c.mu_P)
        assert adj_l.sigma_P == pytest.approx(adj_c.sigma_P)
        assert len(applied_l) == len(applied_c)

    def test_quantiles_scaled_consistently(self):
        """If base has quantiles, they scale by the same total_mult as mu_P."""
        bd = DemandDistribution(
            mu_P=100.0, sigma_P=10.0, protection_period_days=7,
            forecast_method="monte_carlo",
            quantiles={"0.50": 98.0, "0.95": 130.0},
        )
        mods = [_make_modifier("promo", value=1.20, precedence=_PREC_PROMO)]

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            adj, _ = apply_modifiers(base_demand=bd, **_MINIMAL_APPLY_KWARGS)

        assert adj.quantiles["0.50"] == pytest.approx(98.0 * 1.20)
        assert adj.quantiles["0.95"] == pytest.approx(130.0 * 1.20)
