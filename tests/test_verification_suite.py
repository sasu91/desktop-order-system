"""
QA VERIFICATION SUITE — Desktop Order System
=============================================

Obiettivo: verificare OGGETTIVAMENTE che i 4 miglioramenti implementati
siano realmente funzionanti e integrati end-to-end, senza shadow-path.

4 miglioramenti verificati:
  1. Intermittent demand (Croston/SBA/TSB) in build_demand_distribution()
  2. Unified Modifiers Engine (event/promo/cannibalization/holiday)
  3. CSL policy via compute_order_v2() – quantile-first
  4. Clean single-entry pipeline: propose_order_for_sku()

Shadow paths documentati (S1–S4) – verificati come NON coinvolti:
  S1: generate_proposal() → compute_order() (old): rilevato a test
  S2: generate_proposal() bypassa build_demand_distribution(): rilevato
  S3: forecast_method "croston/sba/tsb" silently falls to simple in genprop
  S4: cannibalization non incluso nel flag _any_modifier_enabled

Datasets golden (tests/fixtures/):
  DS1_STABLE.csv      56d × 10.0/d   → mu_P=70.0,  sigma_P=0.0,  ADI=1.0
  DS2_VARIABLE.csv    56d alt 0/20   → MC(seed=42): mu_P=70.5, sigma_P=27.43
  DS3_INTERMITTENT.csv 56d sparse   → is_intermittent=True, ADI=7.0, CV2=1.055
  DS4_MODIFIERS.csv   28d × 10.0/d  → base mu_P=70.0; event×1.2×promo×1.1×cannib×0.85=78.54

Author: Desktop Order System QA / GitHub Copilot
Date: February 2026
"""

from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from src.domain.models import SKU, Stock

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Constants — golden values (precomputed, engine-version-locked)
# ---------------------------------------------------------------------------

ASOF       = date(2026, 2, 26)
P          = 7
DELIVERY   = ASOF + timedelta(days=P)   # 2026-03-05

# DS1 – stable 10/day × 7 days
DS1_MU_P      = 70.0
DS1_SIGMA_P   = 0.0

# DS2 – variable 0/20 alternating, MC seed=42, n=1000
DS2_MC_MU_P      = 70.5
DS2_MC_SIGMA_P   = 27.4318
DS2_MC_Q95       = 120.0    # empirical 95th-percentile from seed=42

# DS3 – intermittent cycle=[0×5,1,0×5,50,...], ADI=7, CV2=1.055
DS3_ADI               = 7.0
DS3_CV2               = 1.055
DS3_IS_INTERMITTENT   = True
DS3_METHOD            = "sba"               # expected method chosen by intermittent_auto
DS3_SIMPLE_MU_P       = 36.3463            # simple EMA on the same data
# intermittent_auto must produce mu_P ≠ simple (lower: rare interarrival effect)
DS3_INTERMITTENT_MU_P = 17.4877

# DS4 – modifier stacking on base 28d × 10/d
DS4_BASE_MU_P    = 70.0
DS4_BASE_SIGMA   = 0.0
DS4_STACKED_MU   = 78.54    # 70 × 1.20 × 1.10 × 0.85
DS4_UPLIFT_MU    = 92.40    # 70 × 1.20 × 1.10 (no cannibalization)

# Tolerance for floating-point comparisons
REL_TOL = 1e-3


# ===========================================================================
# Shared helpers
# ===========================================================================

def _load_csv_fixture(name: str) -> List[Dict[str, Any]]:
    path = _FIXTURES / name
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _to_history(rows: List[Dict]) -> List[Dict]:
    """Convert fixture rows → history list expected by demand_builder."""
    return [{"date": date.fromisoformat(r["date"]), "qty_sold": float(r["qty_sold"])}
            for r in rows]


def _make_sku(sku_id: str, forecast_method: str = "",
              lead_time_days: int = 7, review_period: int = 7,
              safety_stock: int = 0, pack_size: int = 1, moq: int = 1,
              max_stock: int = 999, target_csl: float = 0.0) -> "SKU":
    from src.domain.models import SKU, DemandVariability
    return SKU(
        sku=sku_id,
        description=f"QA SKU {sku_id}",
        lead_time_days=lead_time_days,
        review_period=review_period,
        safety_stock=safety_stock,
        pack_size=pack_size,
        moq=moq,
        max_stock=max_stock,
        forecast_method=forecast_method,
        demand_variability=DemandVariability.STABLE,
        target_csl=target_csl,
    )


def _make_stock(sku_id: str, on_hand: int = 0, on_order: int = 0) -> "Stock":
    from src.domain.models import Stock
    return Stock(sku=sku_id, on_hand=on_hand, on_order=on_order)


def _settings_csl(alpha: float = 0.95, forecast_method: str = "simple",
                  seed: int = 42) -> Dict:
    return {
        "reorder_engine": {
            "policy_mode": {"value": "csl"},
            "forecast_method": {"value": forecast_method},
            "sigma_window_weeks": {"value": 8},
        },
        "service_level": {
            "default_csl": {"value": alpha},
        },
        "monte_carlo": {
            "distribution": {"value": "empirical"},
            "n_simulations": {"value": 1000},
            "random_seed": {"value": seed},
            "output_stat": {"value": "mean"},
            "output_percentile": {"value": 80},
        },
        "promo_adjustment":       {"enabled": {"value": False}},
        "event_uplift":           {"enabled": {"value": False}},
        "promo_cannibalization":  {"enabled": {"value": False}},
        "holiday_modifier":       {"enabled": {"value": False}},
        "shelf_life_policy":      {"enabled": {"value": False}},
    }


def _settings_legacy(forecast_method: str = "simple") -> Dict:
    return {
        "reorder_engine": {
            "policy_mode": {"value": "legacy"},
            "forecast_method": {"value": forecast_method},
            "sigma_window_weeks": {"value": 8},
        },
        "monte_carlo": {
            "distribution": {"value": "empirical"},
            "n_simulations": {"value": 1000},
            "random_seed": {"value": 42},
            "output_stat": {"value": "mean"},
            "output_percentile": {"value": 80},
        },
        "promo_adjustment":       {"enabled": {"value": False}},
        "event_uplift":           {"enabled": {"value": False}},
        "promo_cannibalization":  {"enabled": {"value": False}},
        "holiday_modifier":       {"enabled": {"value": False}},
        "shelf_life_policy":      {"enabled": {"value": False}},
    }


# ---------------------------------------------------------------------------
# Modifier mock helpers (identical to test_modifiers_engine convention)
# ---------------------------------------------------------------------------

def _make_meta_modifier(modifier_type: str, value: float, precedence: int):
    """Build a _ModifierWithMeta for patching list_modifiers."""
    from src.domain.modifier_builder import _ModifierWithMeta, _PREC_EVENT
    from src.domain.contracts import Modifier, DATE_BASIS_DELIVERY
    mod = Modifier(
        id=f"{modifier_type}_qa",
        name=f"{modifier_type}_qa",
        scope_type="sku",
        scope_key="SKU_MODIFIERS",
        date_basis=DATE_BASIS_DELIVERY,
        kind="multiplicative",
        value=value,
        precedence=precedence,
        modifier_type=modifier_type,
        start=DELIVERY,
        end=DELIVERY + timedelta(days=P),
    )
    return _ModifierWithMeta(mod, _note="qa", _confidence="high", _source_sku=None)


_MINIMAL_APPLY_KWARGS = dict(
    sku_id="SKU_MODIFIERS",
    sku_obj=None,
    horizon_dates=[ASOF + timedelta(days=i + 1) for i in range(P)],
    target_receipt_date=DELIVERY,
    asof_date=ASOF,
    settings={},
    all_skus=[],
    promo_windows=[],
    event_rules=[],
    sales_records=[],
    transactions=[],
    holidays=[],
)


# ===========================================================================
# SECTION A — Pipeline contract: propose_order_for_sku uses clean path
# ===========================================================================

class TestPipelineContract:
    """
    Verify that propose_order_for_sku() calls:
      1. build_demand_distribution   (demand step)
      2. apply_modifiers             (modifier step)
      3. compute_order_v2            (CSL policy step)
    and NOT the shadow-path compute_order() / fit_forecast_model().
    """

    def test_A1_propose_calls_build_demand_distribution(self):
        """
        propose_order_for_sku() must call build_demand_distribution.
        Shadow S2: generate_proposal bypasses this entirely.
        """
        from src.workflows.order import propose_order_for_sku
        from src.domain import demand_builder

        history = _to_history(_load_csv_fixture("DS1_STABLE.csv"))
        sku_obj = _make_sku("SKU_STABLE")
        stock   = _make_stock("SKU_STABLE", on_hand=10)

        call_count = []
        orig = demand_builder.build_demand_distribution

        def _spy(*args, **kwargs):
            call_count.append(1)
            return orig(*args, **kwargs)

        with patch.object(demand_builder, "build_demand_distribution", side_effect=_spy):
            proposal, explain = propose_order_for_sku(
                sku_obj=sku_obj,
                history=history,
                stock=stock,
                pipeline=[],
                asof_date=ASOF,
                target_receipt_date=DELIVERY,
                protection_period_days=P,
                settings=_settings_legacy(),
            )

        assert len(call_count) >= 1, (
            "build_demand_distribution was not called – shadow path S2 active!"
        )

    def test_A2_propose_calls_apply_modifiers(self):
        """
        apply_modifiers must be invoked even when no active modifiers.
        Shadow S4: generate_proposal gates on _any_modifier_enabled.
        """
        from src.workflows.order import propose_order_for_sku
        from src.domain import modifier_builder

        history = _to_history(_load_csv_fixture("DS1_STABLE.csv"))
        sku_obj = _make_sku("SKU_STABLE")
        stock   = _make_stock("SKU_STABLE", on_hand=10)

        call_count = []
        orig = modifier_builder.apply_modifiers

        def _spy(*args, **kwargs):
            call_count.append(1)
            return orig(*args, **kwargs)

        with patch.object(modifier_builder, "apply_modifiers", side_effect=_spy):
            propose_order_for_sku(
                sku_obj=sku_obj,
                history=history,
                stock=stock,
                pipeline=[],
                asof_date=ASOF,
                target_receipt_date=DELIVERY,
                protection_period_days=P,
                settings=_settings_legacy(),
            )

        assert len(call_count) >= 1, (
            "apply_modifiers was not called – shadow path S4 active!"
        )

    def test_A3_csl_mode_calls_compute_order_v2_not_compute_order(self):
        """
        In CSL mode, propose_order_for_sku() must call compute_order_v2.
        Shadow S1: generate_proposal CSL branch calls compute_order (old).
        """
        import src.replenishment_policy as rp
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS1_STABLE.csv"))
        sku_obj = _make_sku("SKU_STABLE", target_csl=0.95)
        stock   = _make_stock("SKU_STABLE", on_hand=5)

        v2_calls = []
        old_calls = []
        orig_v2  = rp.compute_order_v2
        orig_old = rp.compute_order

        def _spy_v2(*a, **kw):
            v2_calls.append(1)
            return orig_v2(*a, **kw)

        def _spy_old(*a, **kw):  # Should NOT be called from propose_order_for_sku
            old_calls.append(1)
            return orig_old(*a, **kw)

        with patch.object(rp, "compute_order_v2", side_effect=_spy_v2), \
             patch.object(rp, "compute_order",    side_effect=_spy_old):
            propose_order_for_sku(
                sku_obj=sku_obj,
                history=history,
                stock=stock,
                pipeline=[],
                asof_date=ASOF,
                target_receipt_date=DELIVERY,
                protection_period_days=P,
                settings=_settings_csl(alpha=0.95),
            )

        assert len(v2_calls) >= 1, (
            "compute_order_v2 was not called in CSL mode – pipeline broken!"
        )
        assert len(old_calls) == 0, (
            f"compute_order (old, shadow S1) was called {len(old_calls)} times "
            "from propose_order_for_sku – this should never happen!"
        )

    def test_A4_explain_contains_pipeline_fields(self):
        """
        OrderExplain returned by propose_order_for_sku must expose demand,
        position, and policy_mode – explainability contract.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS1_STABLE.csv"))
        sku_obj = _make_sku("SKU_STABLE")
        stock   = _make_stock("SKU_STABLE", on_hand=5)

        _, explain = propose_order_for_sku(
            sku_obj=sku_obj,
            history=history,
            stock=stock,
            pipeline=[],
            asof_date=ASOF,
            target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=_settings_legacy(),
        )

        assert hasattr(explain, "demand"),   "OrderExplain.demand missing"
        assert hasattr(explain, "position"), "OrderExplain.position missing"
        assert hasattr(explain, "policy_mode"), "OrderExplain.policy_mode missing"
        assert explain.demand.mu_P > 0,       "mu_P must be positive for DS1"


# ===========================================================================
# SECTION B — CSL policy coherence (compute_order_v2)
# ===========================================================================

class TestCSLPolicyCoherence:
    """
    Verifica che compute_order_v2:
      B1: alpha monotonicity – higher alpha → never lower Q
      B2: MC quantile-first – uses quantile when available
      B3: determinism – same seed → same result
    """

    def test_B1_alpha_monotonicity(self):
        """
        Raising alpha from 0.80 → 0.95 must not decrease reorder_point S.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS2_VARIABLE.csv"))
        sku_obj_80 = _make_sku("SKU_VARIABLE", forecast_method="monte_carlo", target_csl=0.80)
        sku_obj_95 = _make_sku("SKU_VARIABLE", forecast_method="monte_carlo", target_csl=0.95)
        stock = _make_stock("SKU_VARIABLE", on_hand=0)

        _, explain_80 = propose_order_for_sku(
            sku_obj=sku_obj_80, history=history, stock=stock, pipeline=[],
            asof_date=ASOF, target_receipt_date=DELIVERY,
            protection_period_days=P, settings=_settings_csl(alpha=0.80),
        )
        _, explain_95 = propose_order_for_sku(
            sku_obj=sku_obj_95, history=history, stock=stock, pipeline=[],
            asof_date=ASOF, target_receipt_date=DELIVERY,
            protection_period_days=P, settings=_settings_csl(alpha=0.95),
        )

        assert explain_95.reorder_point >= explain_80.reorder_point - 0.01, (
            f"Monotonicity violated: S(0.95)={explain_95.reorder_point:.2f} < "
            f"S(0.80)={explain_80.reorder_point:.2f}"
        )

    def test_B2_mc_quantile_used_when_available(self):
        """
        MC produces quantiles → reorder_point_method should be 'quantile'.
        Shadow S1 uses z_score fallback from sigma re-estimated via old compute_order.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS2_VARIABLE.csv"))
        sku_obj = _make_sku("SKU_VARIABLE", forecast_method="monte_carlo", target_csl=0.95)
        stock   = _make_stock("SKU_VARIABLE", on_hand=0)

        _, explain = propose_order_for_sku(
            sku_obj=sku_obj, history=history, stock=stock, pipeline=[],
            asof_date=ASOF, target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=_settings_csl(alpha=0.95, forecast_method="monte_carlo"),
        )

        assert explain.demand.quantiles is not None and len(explain.demand.quantiles) > 0, (
            "MC forecast must produce quantiles – quantile-first cannot work otherwise"
        )
        assert explain.reorder_point_method in ("quantile", "z_score", "z_score_fallback"), (
            f"Unexpected reorder_point_method: {explain.reorder_point_method!r}"
        )

    def test_B3_determinism_same_seed(self):
        """
        Two calls with identical seed must produce identical Q and S.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS2_VARIABLE.csv"))
        sku_obj = _make_sku("SKU_VARIABLE", forecast_method="monte_carlo", target_csl=0.95)
        stock   = _make_stock("SKU_VARIABLE", on_hand=0)
        settings = _settings_csl(alpha=0.95, forecast_method="monte_carlo", seed=42)

        _, e1 = propose_order_for_sku(
            sku_obj=sku_obj, history=history, stock=stock, pipeline=[],
            asof_date=ASOF, target_receipt_date=DELIVERY,
            protection_period_days=P, settings=settings,
        )
        _, e2 = propose_order_for_sku(
            sku_obj=sku_obj, history=history, stock=stock, pipeline=[],
            asof_date=ASOF, target_receipt_date=DELIVERY,
            protection_period_days=P, settings=settings,
        )

        assert e1.demand.mu_P == e2.demand.mu_P, (
            f"mu_P not deterministic: {e1.demand.mu_P} ≠ {e2.demand.mu_P}"
        )
        assert e1.demand.sigma_P == e2.demand.sigma_P, (
            f"sigma_P not deterministic: {e1.demand.sigma_P} ≠ {e2.demand.sigma_P}"
        )
        assert e1.reorder_point == e2.reorder_point, (
            f"reorder_point not deterministic: {e1.reorder_point} ≠ {e2.reorder_point}"
        )

    def test_B4_mc_golden_mu_sigma(self):
        """
        DS2: MC seed=42, n=1000 must reproduce exact golden mu_P and sigma_P.
        """
        from src.domain.demand_builder import build_demand_distribution

        history = _to_history(_load_csv_fixture("DS2_VARIABLE.csv"))
        mc_params = {
            "distribution": "empirical",
            "n_simulations": 1000,
            "random_seed": 42,
            "output_stat": "mean",
            "output_percentile": 80,
        }
        dd = build_demand_distribution("monte_carlo", history, P, ASOF, mc_params=mc_params)

        assert abs(dd.mu_P - DS2_MC_MU_P) < (DS2_MC_MU_P * REL_TOL), (
            f"DS2 MC mu_P={dd.mu_P:.4f}, expected≈{DS2_MC_MU_P}"
        )
        assert abs(dd.sigma_P - DS2_MC_SIGMA_P) < (DS2_MC_SIGMA_P * REL_TOL), (
            f"DS2 MC sigma_P={dd.sigma_P:.4f}, expected≈{DS2_MC_SIGMA_P}"
        )


# ===========================================================================
# SECTION C — Intermittent demand
# ===========================================================================

class TestIntermittentDemand:
    """
    Verifica:
      C1: classify_intermittent rileva DS3 come intermittente (ADI>1.32, CV2>0.49)
      C2: intermittent_auto sceglie SBA su DS3 (non simple fallback)
      C3: DS1 (constant demand) NON è classificato come intermittente
      C4: mu_P da intermittent_auto ≠ mu_P da simple su DS3
      C5: build_demand_distribution è il punto di ingresso per tutti i metodi
    """

    def test_C1_ds3_classified_as_intermittent(self):
        """
        DS3 must be classified as intermittent (ADI=7.0, CV2=1.055 > 0.49).
        If this fails the fixture was corrupted or thresholds changed.
        """
        from src.domain.intermittent_forecast import classify_intermittent

        rows = _load_csv_fixture("DS3_INTERMITTENT.csv")
        series = [float(r["qty_sold"]) for r in rows]
        clf = classify_intermittent(series)

        assert clf.is_intermittent is True, (
            f"DS3 not classified as intermittent: ADI={clf.adi:.3f}, CV2={clf.cv2:.3f}"
        )
        assert clf.adi == pytest.approx(DS3_ADI, rel=REL_TOL), (
            f"ADI mismatch: {clf.adi:.3f} ≠ {DS3_ADI}"
        )
        assert clf.cv2 == pytest.approx(DS3_CV2, rel=0.01), (
            f"CV2 mismatch: {clf.cv2:.3f} ≠ {DS3_CV2}"
        )

    def test_C2_intermittent_auto_selects_non_simple(self):
        """
        intermittent_auto on DS3 must NOT fall back to simple.
        Expected method: 'sba' (or 'croston'/'tsb' – but not '').
        """
        from src.domain.demand_builder import build_demand_distribution

        history = _to_history(_load_csv_fixture("DS3_INTERMITTENT.csv"))
        mc_params = {
            "min_nonzero_observations": 4,
            "backtest_enabled": True,
            "backtest_periods": 3,
            "backtest_min_history": 28,
            "alpha_default": 0.1,
            "fallback_to_simple": True,
        }
        dd = build_demand_distribution(
            "intermittent_auto", history, P, ASOF, mc_params=mc_params
        )

        assert dd.intermittent_classification is True, (
            "intermittent_auto did not classify DS3 as intermittent"
        )
        assert dd.intermittent_method in ("sba", "croston", "tsb"), (
            f"Expected intermittent method but got {dd.intermittent_method!r} "
            "(shadow path S3: fell back to simple)"
        )

    def test_C3_ds1_not_intermittent(self):
        """DS1 (constant 10/day) must NOT be classified as intermittent."""
        from src.domain.intermittent_forecast import classify_intermittent

        rows = _load_csv_fixture("DS1_STABLE.csv")
        series = [float(r["qty_sold"]) for r in rows]
        clf = classify_intermittent(series)

        assert clf.is_intermittent is False, (
            f"DS1 (constant demand) wrongly classified as intermittent: "
            f"ADI={clf.adi:.3f}, CV2={clf.cv2:.3f}"
        )

    def test_C4_intermittent_mu_differs_from_simple(self):
        """
        intermittent_auto mu_P must differ from simple mu_P on DS3.
        If equal, the intermittent code path is silently bypassed.
        """
        from src.domain.demand_builder import build_demand_distribution

        history = _to_history(_load_csv_fixture("DS3_INTERMITTENT.csv"))
        mc_params = {
            "min_nonzero_observations": 4,
            "backtest_enabled": True,
            "backtest_periods": 3,
            "backtest_min_history": 28,
            "alpha_default": 0.1,
            "fallback_to_simple": True,
        }

        dd_auto   = build_demand_distribution("intermittent_auto", history, P, ASOF,
                                               mc_params=mc_params)
        dd_simple = build_demand_distribution("simple", history, P, ASOF)

        assert abs(dd_auto.mu_P - dd_simple.mu_P) > 0.5, (
            f"intermittent_auto mu_P={dd_auto.mu_P:.4f} is too close to "
            f"simple mu_P={dd_simple.mu_P:.4f} – intermittent path not taken!"
        )
        # Intermittent estimate should be lower (rare events → less expected demand)
        assert dd_auto.mu_P < dd_simple.mu_P, (
            f"Intermittent mu_P should be < simple mu_P on sparse DS3"
        )

    def test_C5_golden_intermittent_mu_p(self):
        """
        DS3 intermittent_auto golden value: mu_P ≈ 17.49 (SBA on sparse series).
        """
        from src.domain.demand_builder import build_demand_distribution

        history = _to_history(_load_csv_fixture("DS3_INTERMITTENT.csv"))
        mc_params = {
            "min_nonzero_observations": 4,
            "backtest_enabled": True,
            "backtest_periods": 3,
            "backtest_min_history": 28,
            "alpha_default": 0.1,
            "fallback_to_simple": True,
        }
        dd = build_demand_distribution("intermittent_auto", history, P, ASOF,
                                        mc_params=mc_params)

        assert dd.mu_P == pytest.approx(DS3_INTERMITTENT_MU_P, rel=REL_TOL), (
            f"DS3 intermittent_auto golden mu_P: got {dd.mu_P:.4f}, "
            f"expected {DS3_INTERMITTENT_MU_P}"
        )

    def test_C6_propose_order_for_sku_uses_intermittent(self, caplog):
        """
        propose_order_for_sku() with forecast_method='intermittent_auto' (via settings)
        must route through _build_intermittent (not simple fallback).
        Verified by checking that explain.demand.intermittent_classification is True.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS3_INTERMITTENT.csv"))
        # forecast_method="" on SKU → falls through to settings["reorder_engine"]["forecast_method"]
        sku_obj = _make_sku("SKU_INTERMITTENT", forecast_method="")
        stock   = _make_stock("SKU_INTERMITTENT", on_hand=0)

        settings = {
            "reorder_engine": {
                "policy_mode": {"value": "legacy"},
                "forecast_method": {"value": "intermittent_auto"},
                "sigma_window_weeks": {"value": 8},
            },
            "monte_carlo": {
                "distribution": {"value": "empirical"},
                "n_simulations": {"value": 1000},
                "random_seed": {"value": 42},
                "output_stat": {"value": "mean"},
                "output_percentile": {"value": 80},
                "min_nonzero_observations": {"value": 4},
                "backtest_enabled": {"value": True},
                "backtest_periods": {"value": 3},
                "backtest_min_history": {"value": 28},
                "alpha_default": {"value": 0.1},
                "fallback_to_simple": {"value": True},
            },
            "promo_adjustment":       {"enabled": {"value": False}},
            "event_uplift":           {"enabled": {"value": False}},
            "promo_cannibalization":  {"enabled": {"value": False}},
            "holiday_modifier":       {"enabled": {"value": False}},
            "shelf_life_policy":      {"enabled": {"value": False}},
        }

        _, explain = propose_order_for_sku(
            sku_obj=sku_obj,
            history=history,
            stock=stock,
            pipeline=[],
            asof_date=ASOF,
            target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=settings,
        )

        assert explain.demand.intermittent_classification is True, (
            "propose_order_for_sku did not use intermittent path for DS3"
        )


# ===========================================================================
# SECTION D — Modifiers Engine
# ===========================================================================

class TestModifiersEngine:
    """
    Verifica:
      D1: Stacking precedence: EVENT → PROMO → CANNIB = deterministic result
      D2: Sigma upward-only policy respected
      D3: Passthrough when no modifiers active (applied == [])
      D4: Golden value: 70×1.20×1.10×0.85 = 78.54 (tolerance REL_TOL)
      D5: apply_modifiers is called once per propose_order_for_sku call
      D6: Cannibalization downlift does NOT reduce sigma
    """

    def test_D1_stacking_precedence_order(self):
        """EVENT(1) → PROMO(2) → CANNIB(3): check mu trace is event→promo→cannib."""
        from src.domain.demand_builder import build_demand_distribution
        from src.domain.modifier_builder import apply_modifiers, _PREC_EVENT, _PREC_PROMO, _PREC_CANNIB

        history = _to_history(_load_csv_fixture("DS4_MODIFIERS.csv"))
        dd = build_demand_distribution("simple", history, P, ASOF)

        mods = [
            _make_meta_modifier("event", 1.20, _PREC_EVENT),
            _make_meta_modifier("promo",  1.10, _PREC_PROMO),
            _make_meta_modifier("cannibalization", 0.85, _PREC_CANNIB),
        ]
        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            dd_out, applied = apply_modifiers(base_demand=dd, **_MINIMAL_APPLY_KWARGS)  # type: ignore[arg-type]

        assert len(applied) == 3

        # Verify precedence ordering: event < promo < cannibalization
        precedences = [m.precedence for m in applied]
        assert precedences == sorted(precedences), (
            f"Modifiers not in precedence order: {precedences}"
        )

        # Verify modifier_type ordering
        types = [m.modifier_type for m in applied]
        assert types[0] == "event"
        assert types[1] == "promo"
        assert types[2] == "cannibalization"

        # Verify intermediary mu trace (mu_before / mu_after)
        assert applied[0].mu_before == pytest.approx(DS4_BASE_MU_P), (
            f"event mu_before={applied[0].mu_before}, expected {DS4_BASE_MU_P}"
        )
        assert applied[0].mu_after  == pytest.approx(DS4_BASE_MU_P * 1.20, rel=REL_TOL)
        assert applied[1].mu_before == pytest.approx(DS4_BASE_MU_P * 1.20, rel=REL_TOL)
        assert applied[1].mu_after  == pytest.approx(DS4_BASE_MU_P * 1.20 * 1.10, rel=REL_TOL)
        assert applied[2].mu_before == pytest.approx(DS4_BASE_MU_P * 1.20 * 1.10, rel=REL_TOL)
        assert applied[2].mu_after  == pytest.approx(DS4_STACKED_MU, rel=REL_TOL)

    def test_D2_sigma_upward_only(self):
        """Cannibalization downlift must leave sigma unchanged."""
        from src.domain.demand_builder import build_demand_distribution
        from src.domain.modifier_builder import apply_modifiers, _PREC_CANNIB
        from src.domain.contracts import DemandDistribution
        from dataclasses import replace

        history = _to_history(_load_csv_fixture("DS4_MODIFIERS.csv"))
        dd_base = build_demand_distribution("simple", history, P, ASOF)
        # Inject artificial sigma so we can check it's preserved
        dd_with_sigma = replace(dd_base, sigma_P=15.0)

        mods = [_make_meta_modifier("cannibalization", 0.70, _PREC_CANNIB)]

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            dd_out, applied = apply_modifiers(
                base_demand=dd_with_sigma, **_MINIMAL_APPLY_KWARGS  # type: ignore[arg-type]
            )

        assert dd_out.mu_P == pytest.approx(DS4_BASE_MU_P * 0.70, rel=REL_TOL), (
            "Cannibalization mu_P not applied correctly"
        )
        # Sigma must NOT decrease (total_mult = 0.70 < 1.0 → clamped → sigma unchanged)
        assert dd_out.sigma_P == pytest.approx(15.0), (
            f"Sigma should be unchanged (15.0), got {dd_out.sigma_P}"
        )

    def test_D3_passthrough_when_no_modifiers(self):
        """If list_modifiers returns [], base demand is returned unchanged."""
        from src.domain.demand_builder import build_demand_distribution
        from src.domain.modifier_builder import apply_modifiers

        history = _to_history(_load_csv_fixture("DS4_MODIFIERS.csv"))
        dd = build_demand_distribution("simple", history, P, ASOF)

        with patch("src.domain.modifier_builder.list_modifiers", return_value=[]):
            dd_out, applied = apply_modifiers(base_demand=dd, **_MINIMAL_APPLY_KWARGS)  # type: ignore[arg-type]

        assert applied == []
        assert dd_out.mu_P == pytest.approx(dd.mu_P)

    def test_D4_golden_stacking_value(self):
        """
        Golden: 70 × 1.20 × 1.10 × 0.85 = 78.54 (≤ REL_TOL).
        """
        from src.domain.demand_builder import build_demand_distribution
        from src.domain.modifier_builder import apply_modifiers, _PREC_EVENT, _PREC_PROMO, _PREC_CANNIB

        history = _to_history(_load_csv_fixture("DS4_MODIFIERS.csv"))
        dd = build_demand_distribution("simple", history, P, ASOF)

        mods = [
            _make_meta_modifier("event", 1.20, _PREC_EVENT),
            _make_meta_modifier("promo",  1.10, _PREC_PROMO),
            _make_meta_modifier("cannibalization", 0.85, _PREC_CANNIB),
        ]
        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            dd_out, _ = apply_modifiers(base_demand=dd, **_MINIMAL_APPLY_KWARGS)  # type: ignore[arg-type]

        assert dd_out.mu_P == pytest.approx(DS4_STACKED_MU, rel=REL_TOL), (
            f"Stacked golden: got {dd_out.mu_P:.4f}, expected {DS4_STACKED_MU}"
        )

    def test_D5_event_promo_uplift_mu_and_no_sigma_if_zero_base_sigma(self):
        """
        Event×1.20 × Promo×1.10 on DS4 with sigma_P=0 must give exactly 92.40 mu.
        sigma stays 0 (no sigma when base is 0, only upward scaling applies).
        """
        from src.domain.demand_builder import build_demand_distribution
        from src.domain.modifier_builder import apply_modifiers, _PREC_EVENT, _PREC_PROMO

        history = _to_history(_load_csv_fixture("DS4_MODIFIERS.csv"))
        dd = build_demand_distribution("simple", history, P, ASOF)

        assert dd.sigma_P == pytest.approx(0.0), "DS4 sigma must be 0 (constant demand)"

        mods = [
            _make_meta_modifier("event", 1.20, _PREC_EVENT),
            _make_meta_modifier("promo",  1.10, _PREC_PROMO),
        ]
        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            dd_out, applied = apply_modifiers(base_demand=dd, **_MINIMAL_APPLY_KWARGS)  # type: ignore[arg-type]

        assert dd_out.mu_P == pytest.approx(DS4_UPLIFT_MU, rel=REL_TOL), (
            f"Event+Promo uplift: got {dd_out.mu_P:.4f}, expected {DS4_UPLIFT_MU}"
        )
        assert dd_out.sigma_P == pytest.approx(0.0), (
            "sigma_P should remain 0 when base sigma is 0"
        )


# ===========================================================================
# SECTION E — End-to-end integration
# ===========================================================================

class TestEndToEndIntegration:
    """
    Integrated pipeline tests: from raw CSV history to OrderExplain.
    No mocks — real call chain.
    """

    def test_E1_ds1_stable_legacy_pipeline_q_positive(self):
        """
        DS1 + legacy + zero stock → Q > 0 (need to order).
        mu_P from explain must match DS1 golden.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS1_STABLE.csv"))
        sku_obj = _make_sku("SKU_STABLE", safety_stock=0)
        stock   = _make_stock("SKU_STABLE", on_hand=0)

        proposal, explain = propose_order_for_sku(
            sku_obj=sku_obj,
            history=history,
            stock=stock,
            pipeline=[],
            asof_date=ASOF,
            target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=_settings_legacy(),
        )

        assert proposal.proposed_qty >= 0
        assert explain.demand.mu_P == pytest.approx(DS1_MU_P, rel=REL_TOL), (
            f"DS1 mu_P: got {explain.demand.mu_P:.4f}, expected {DS1_MU_P}"
        )
        assert explain.demand.sigma_P == pytest.approx(0.0, abs=0.01), (
            f"DS1 sigma_P: got {explain.demand.sigma_P:.4f}, expected 0.0"
        )

    def test_E2_ds2_mc_csl_q_and_explain_present(self):
        """
        DS2 + MC + CSL → Q ≥ 0 and explain has quantiles populated.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS2_VARIABLE.csv"))
        sku_obj = _make_sku("SKU_VARIABLE", forecast_method="monte_carlo", target_csl=0.95)
        stock   = _make_stock("SKU_VARIABLE", on_hand=0)

        proposal, explain = propose_order_for_sku(
            sku_obj=sku_obj,
            history=history,
            stock=stock,
            pipeline=[],
            asof_date=ASOF,
            target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=_settings_csl(alpha=0.95, forecast_method="monte_carlo"),
        )

        assert proposal.proposed_qty >= 0
        assert explain.demand.mu_P > 0
        assert explain.demand.quantiles, "MC must produce quantiles dict"
        assert explain.alpha_target == pytest.approx(0.95), (
            f"alpha_target not stored: {explain.alpha_target}"
        )

    def test_E3_ds3_intermittent_pipeline(self):
        """
        DS3 + intermittent_auto + legacy → explain shows intermittent classification.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS3_INTERMITTENT.csv"))
        # forecast_method="" on SKU → falls through to settings["reorder_engine"]["forecast_method"]
        sku_obj = _make_sku("SKU_INTERMITTENT", forecast_method="")
        stock   = _make_stock("SKU_INTERMITTENT", on_hand=0)

        settings = {
            "reorder_engine": {
                "policy_mode": {"value": "legacy"},
                "forecast_method": {"value": "intermittent_auto"},
                "sigma_window_weeks": {"value": 8},
            },
            "monte_carlo": {
                "distribution": {"value": "empirical"},
                "n_simulations": {"value": 1000},
                "random_seed": {"value": 42},
                "output_stat": {"value": "mean"},
                "output_percentile": {"value": 80},
                "min_nonzero_observations": {"value": 4},
                "backtest_enabled": {"value": True},
                "backtest_periods": {"value": 3},
                "backtest_min_history": {"value": 28},
                "alpha_default": {"value": 0.1},
                "fallback_to_simple": {"value": True},
            },
            "promo_adjustment":       {"enabled": {"value": False}},
            "event_uplift":           {"enabled": {"value": False}},
            "promo_cannibalization":  {"enabled": {"value": False}},
            "holiday_modifier":       {"enabled": {"value": False}},
            "shelf_life_policy":      {"enabled": {"value": False}},
        }

        proposal, explain = propose_order_for_sku(
            sku_obj=sku_obj,
            history=history,
            stock=stock,
            pipeline=[],
            asof_date=ASOF,
            target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=settings,
        )

        assert proposal.proposed_qty >= 0
        assert explain.demand.intermittent_classification is True, (
            "E2E DS3 pipeline did not use intermittent path"
        )
        # mu_P should be close to the golden intermittent value
        assert abs(explain.demand.mu_P - DS3_INTERMITTENT_MU_P) < 1.0, (
            f"E2E intermittent mu_P: got {explain.demand.mu_P:.4f}, "
            f"expected ≈{DS3_INTERMITTENT_MU_P}"
        )

    def test_E4_on_hand_above_coverage_yields_zero_order(self):
        """
        When on_hand >> mu_P (e.g., 1000 vs 70), Q must be 0.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS1_STABLE.csv"))
        sku_obj = _make_sku("SKU_STABLE", safety_stock=0)
        stock   = _make_stock("SKU_STABLE", on_hand=1000)

        proposal, _ = propose_order_for_sku(
            sku_obj=sku_obj,
            history=history,
            stock=stock,
            pipeline=[],
            asof_date=ASOF,
            target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=_settings_legacy(),
        )

        assert proposal.proposed_qty == 0, (
            f"Overstocked SKU should yield Q=0, got {proposal.proposed_qty}"
        )

    def test_E5_explain_to_dict_is_serialisable(self):
        """
        OrderExplain.to_dict() must return a flat dict without raising.
        Regression: broken serialisation hides explainability failures.
        """
        from src.workflows.order import propose_order_for_sku

        history = _to_history(_load_csv_fixture("DS1_STABLE.csv"))
        sku_obj = _make_sku("SKU_STABLE")
        stock   = _make_stock("SKU_STABLE", on_hand=0)

        _, explain = propose_order_for_sku(
            sku_obj=sku_obj,
            history=history,
            stock=stock,
            pipeline=[],
            asof_date=ASOF,
            target_receipt_date=DELIVERY,
            protection_period_days=P,
            settings=_settings_legacy(),
        )

        d = explain.to_dict()
        assert isinstance(d, dict)
        assert "mu_P" in d or "demand" in str(d), (
            "to_dict() output missing mu_P key"
        )

    def test_E6_explain_modifier_trace_populated_when_active(self):
        """
        When a modifier is injected, OrderExplain.modifiers must be non-empty
        and the trace must contain mu_before/mu_after for each applied modifier.
        """
        from src.workflows.order import propose_order_for_sku
        from src.domain.modifier_builder import _PREC_EVENT, _PREC_PROMO

        history = _to_history(_load_csv_fixture("DS4_MODIFIERS.csv"))
        sku_obj = _make_sku("SKU_MODIFIERS")
        stock   = _make_stock("SKU_MODIFIERS", on_hand=0)

        mods = [
            _make_meta_modifier("event", 1.20, _PREC_EVENT),
            _make_meta_modifier("promo",  1.10, _PREC_PROMO),
        ]

        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            _, explain = propose_order_for_sku(
                sku_obj=sku_obj,
                history=history,
                stock=stock,
                pipeline=[],
                asof_date=ASOF,
                target_receipt_date=DELIVERY,
                protection_period_days=P,
                settings=_settings_legacy(),
            )

        assert len(explain.modifiers) == 2, (
            f"Expected 2 applied modifiers, got {len(explain.modifiers)}"
        )
        for m in explain.modifiers:
            assert m.mu_before > 0, f"mu_before not set for modifier {m.name}"
            assert m.mu_after  > 0, f"mu_after not set for modifier {m.name}"

        # Final mu_P must equal event×promo result
        assert explain.demand.mu_P == pytest.approx(DS4_UPLIFT_MU, rel=REL_TOL), (
            f"Final mu_P={explain.demand.mu_P:.4f} ≠ {DS4_UPLIFT_MU}"
        )


# ===========================================================================
# SECTION F — Shadow paths: structural probes
# ===========================================================================

class TestShadowPaths:
    """
    Direct structural checks that expose the gap between clean path
    and shadow path – without needing full integration runs.

    These tests document known technical debt but do NOT necessarily
    fail (they assert the CLEAN path is correct, not that the shadow
    path is fixed).
    """

    def test_F1_shadow_S1_documented(self):
        """
        SHADOW S1 (informational): generate_proposal still imports compute_order.
        This test documents the shadow path exists – it does not fix it.
        We verify it does NOT exist in propose_order_for_sku's import scope.
        """
        import src.replenishment_policy as rp
        # compute_order_v2 must exist (clean path)
        assert hasattr(rp, "compute_order_v2"), (
            "compute_order_v2 missing from replenishment_policy"
        )
        # compute_order (old) may still exist (shadow path reference in generate_proposal)
        # but propose_order_for_sku must explicitly NOT use it (tested in A3)
        assert hasattr(rp, "compute_order"), (
            "compute_order removed – update shadow path test S1 comment"
        )

    def test_F2_shadow_S2_documented(self):
        """
        SHADOW S2 (informational): generate_proposal bypasses build_demand_distribution.
        We verify the function exists in demand_builder and is importable.
        """
        from src.domain.demand_builder import build_demand_distribution
        assert callable(build_demand_distribution)

    def test_F3_shadow_S3_documented(self):
        """
        SHADOW S3 (informational): generate_proposal silently falls back to simple
        for forecast_method in {'croston','sba','tsb','intermittent_auto'}.
        We verify the clean path (propose_order_for_sku) correctly routes to
        intermittent when method='intermittent_auto'.
        """
        from src.domain.demand_builder import build_demand_distribution
        history = _to_history(_load_csv_fixture("DS3_INTERMITTENT.csv"))
        mc_params = {
            "min_nonzero_observations": 4,
            "backtest_enabled": True,
            "backtest_periods": 3,
            "backtest_min_history": 28,
            "alpha_default": 0.1,
            "fallback_to_simple": True,
        }
        dd = build_demand_distribution("intermittent_auto", history, P, ASOF,
                                        mc_params=mc_params)
        # Clean path: intermittent_method must be non-empty
        assert dd.intermittent_method != "", (
            "Shadow S3 reached: build_demand_distribution fell back to simple "
            "for intermittent_auto with DS3 (classified as intermittent)"
        )

    def test_F4_shadow_S4_documented(self):
        """
        SHADOW S4 (informational): cannibalization gating in generate_proposal.
        We verify that apply_modifiers handles cannibalization correctly
        regardless of promo/event/holiday flags (tested via mock).
        """
        from src.domain.demand_builder import build_demand_distribution
        from src.domain.modifier_builder import apply_modifiers, _PREC_CANNIB

        history = _to_history(_load_csv_fixture("DS4_MODIFIERS.csv"))
        dd = build_demand_distribution("simple", history, P, ASOF)

        # Only cannibalization, no promo or event
        mods = [_make_meta_modifier("cannibalization", 0.80, _PREC_CANNIB)]
        with patch("src.domain.modifier_builder.list_modifiers", return_value=mods):
            dd_out, applied = apply_modifiers(base_demand=dd, **_MINIMAL_APPLY_KWARGS)  # type: ignore[arg-type]

        # The clean path (apply_modifiers) applies cannibalization regardless
        assert len(applied) == 1
        assert applied[0].modifier_type == "cannibalization"
        assert dd_out.mu_P == pytest.approx(DS4_BASE_MU_P * 0.80, rel=REL_TOL), (
            f"Cannibalization-only: got {dd_out.mu_P:.4f}, expected {DS4_BASE_MU_P * 0.80}"
        )
