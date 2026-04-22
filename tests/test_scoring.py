"""
Tests for src/analytics/scoring.py

Covers:
  - Range invariants: all scores in [0, 100], no NaN/None
  - Monotonicity: priority ~ importance*ill-health
  - Perishable vs non-perishable weight renormalisation
  - Cold-start / low-data SKU stability
  - waste_rate and oos_rate boundary conditions
  - Days-of-supply bell-curve scoring
  - Batch population scoring (cross-SKU percentile rank)
  - Confidence / data-quality flags
"""

import math
import pytest
from datetime import date

from src.analytics.scoring import (
    FeatureRow,
    SKUScoringResult,
    SCORING_VERSION,
    DOS_TARGET_LOW,
    DOS_TARGET_HIGH,
    MIN_SELLING_DAYS_HIGH_CONFIDENCE,
    build_feature_row,
    compute_importance_scores,
    compute_health_score,
    compute_priority_scores,
    score_all_skus,
    _availability_subscore,
    _waste_subscore,
    _inventory_eff_subscore,
    _supplier_subscore,
    _forecast_subscore,
    _clamp,
    _robust_scale_list,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    sku="SKU001",
    units_sold=100.0,
    selling_days=20,
    oos_rate=0.05,
    waste_rate=0.02,
    shelf_life_days=5,
    days_of_supply=14.0,
    fill_rate=0.95,
    otif_rate=0.90,
    avg_delay_days=1.0,
    wmape=20.0,
    bias=0.5,
    avg_daily_sales=5.0,
) -> FeatureRow:
    row = FeatureRow(
        sku=sku,
        ref_date=date(2025, 1, 15),
        lookback_days=30,
        units_sold=units_sold,
        selling_days=selling_days,
        oos_rate=oos_rate,
        waste_rate=waste_rate,
        shelf_life_days=shelf_life_days,
        days_of_supply=days_of_supply,
        fill_rate=fill_rate,
        otif_rate=otif_rate,
        avg_delay_days=avg_delay_days,
        wmape=wmape,
        bias=bias,
        avg_daily_sales_for_bias=avg_daily_sales,
    )
    observed = sum(
        1 for v in [oos_rate, waste_rate, days_of_supply, fill_rate,
                    otif_rate, avg_delay_days, wmape, bias]
        if v is not None
    )
    row.n_observed_fields = observed
    row.n_total_fields = 8
    return row


# ---------------------------------------------------------------------------
# 1. Range invariants
# ---------------------------------------------------------------------------

class TestRangeInvariants:

    def test_clamp_in_range(self):
        assert _clamp(50.0, 0.0, 100.0) == 50.0
        assert _clamp(-5.0, 0.0, 100.0) == 0.0
        assert _clamp(150.0, 0.0, 100.0) == 100.0

    def test_robust_scale_range(self):
        vals = [0.0, 10.0, 50.0, 100.0, 500.0]
        scaled = _robust_scale_list(vals)
        assert all(0.0 <= s <= 100.0 for s in scaled)

    def test_health_score_range(self):
        row = _make_row()
        health, _ = compute_health_score(row)
        assert 0.0 <= health <= 100.0

    def test_importance_range(self):
        rows = [_make_row(f"SKU{i:03d}", units_sold=i*10, selling_days=i) for i in range(1, 11)]
        imp_map = compute_importance_scores(rows)
        for imp, _, _ in imp_map.values():
            assert 0.0 <= imp <= 100.0

    def test_score_all_skus_range(self):
        rows = [_make_row(f"SKU{i:03d}", units_sold=i*5, selling_days=max(i, 1)) for i in range(1, 6)]
        results = score_all_skus(rows)
        for r in results:
            assert 0.0 <= r.importance_score <= 100.0, f"importance out of range: {r.importance_score}"
            assert 0.0 <= r.health_score <= 100.0,     f"health out of range: {r.health_score}"
            assert 0.0 <= r.priority_score <= 100.0,   f"priority out of range: {r.priority_score}"

    def test_no_none_in_results(self):
        rows = [_make_row("A"), _make_row("B")]
        results = score_all_skus(rows)
        for r in results:
            for attr in ["importance_score", "health_score", "priority_score",
                         "confidence_score", "raw_priority"]:
                assert getattr(r, attr) is not None
                assert not math.isnan(getattr(r, attr))


# ---------------------------------------------------------------------------
# 2. Monotonicity
# ---------------------------------------------------------------------------

class TestMonotonicity:

    def test_priority_increases_with_importance(self):
        """Higher importance → higher priority, at fixed health."""
        rows = [
            _make_row("LOW",  units_sold=10,   selling_days=5),
            _make_row("MED",  units_sold=100,  selling_days=15),
            _make_row("HIGH", units_sold=1000, selling_days=28),
        ]
        results = score_all_skus(rows)
        imp_map  = {r.sku: r.importance_score for r in results}
        prio_map = {r.sku: r.priority_score   for r in results}
        assert imp_map["LOW"] < imp_map["MED"] < imp_map["HIGH"]
        assert prio_map["LOW"] <= prio_map["MED"] <= prio_map["HIGH"]

    def test_priority_increases_with_worse_health(self):
        """Worse health (higher oos) → higher priority, at fixed importance."""
        # Same importance: same units/days; vary oos_rate
        rows = [
            _make_row("HEALTHY",  units_sold=200, selling_days=20, oos_rate=0.01),
            _make_row("MODERATE", units_sold=200, selling_days=20, oos_rate=0.20),
            _make_row("SICK",     units_sold=200, selling_days=20, oos_rate=0.80),
        ]
        results = score_all_skus(rows)
        prio_map   = {r.sku: r.priority_score for r in results}
        health_map = {r.sku: r.health_score   for r in results}
        assert health_map["HEALTHY"] > health_map["MODERATE"] > health_map["SICK"]
        assert prio_map["HEALTHY"] < prio_map["MODERATE"] < prio_map["SICK"]

    def test_important_sick_beats_unimportant_sick(self):
        """Important+sick must outscore unimportant+sick."""
        rows = [
            _make_row("IMP_SICK",  units_sold=1000, selling_days=28, oos_rate=0.80),
            _make_row("UNI_SICK",  units_sold=5,    selling_days=2,  oos_rate=0.80),
        ]
        results = score_all_skus(rows)
        prio_map = {r.sku: r.priority_score for r in results}
        assert prio_map["IMP_SICK"] >= prio_map["UNI_SICK"]


# ---------------------------------------------------------------------------
# 3. Perishable weight renormalisation
# ---------------------------------------------------------------------------

class TestPerishableWeights:

    def test_non_perishable_waste_weight_zero(self):
        row = _make_row(shelf_life_days=None)  # non-perishable
        _, detail = compute_health_score(row)
        assert detail["weight_waste"] == 0.0

    def test_perishable_waste_weight_nonzero(self):
        row = _make_row(shelf_life_days=5)
        _, detail = compute_health_score(row)
        assert detail["weight_waste"] > 0.0

    def test_weights_sum_to_one(self):
        for sl in [None, 0, 3, 7, 30]:
            row = _make_row(shelf_life_days=sl)
            _, detail = compute_health_score(row)
            total = (detail["weight_availability"] + detail["weight_waste"] +
                     detail["weight_inventory_eff"] + detail["weight_supplier"] +
                     detail["weight_forecast"])
            assert abs(total - 1.0) < 1e-9, f"weights sum {total} ≠ 1 for shelf_life={sl}"

    def test_non_perishable_waste_score_not_used(self):
        """Even if waste_rate is high, non-perishable health should not drop on waste."""
        row_np = _make_row(shelf_life_days=None, waste_rate=0.99)
        row_p  = _make_row(shelf_life_days=5,    waste_rate=0.99)
        h_np, _ = compute_health_score(row_np)
        h_p,  _ = compute_health_score(row_p)
        # Non-perishable must have higher (or equal) health since waste is ignored
        assert h_np >= h_p


# ---------------------------------------------------------------------------
# 4. Cold-start / low-data stability
# ---------------------------------------------------------------------------

class TestColdStart:

    def test_brand_new_sku_no_crash(self):
        """SKU with zero history should produce valid scores, not crash."""
        row = FeatureRow(
            sku="NEW",
            ref_date=date(2025, 1, 15),
            lookback_days=30,
            units_sold=0.0,
            selling_days=0,
            oos_rate=None,
            waste_rate=None,
            shelf_life_days=None,
            days_of_supply=None,
            fill_rate=None,
            otif_rate=None,
            avg_delay_days=None,
            wmape=None,
            bias=None,
            avg_daily_sales_for_bias=0.0,
        )
        row.n_observed_fields = 0
        row.n_total_fields = 8
        results = score_all_skus([row])
        r = results[0]
        assert 0.0 <= r.importance_score <= 100.0
        assert 0.0 <= r.health_score <= 100.0
        assert 0.0 <= r.priority_score <= 100.0
        assert r.data_quality_flag in ("LOW_DATA", "MISSING_KPI", "OK")

    def test_low_confidence_for_new_sku(self):
        row = FeatureRow(
            sku="NEW", ref_date=date(2025, 1, 1), lookback_days=30,
            units_sold=0, selling_days=0, n_observed_fields=0, n_total_fields=8,
        )
        results = score_all_skus([row])
        assert results[0].confidence_score < 0.5

    def test_partial_data_no_crash(self):
        """SKU with only oos_rate available should not crash."""
        row = FeatureRow(
            sku="PARTIAL",
            ref_date=date(2025, 1, 15),
            lookback_days=30,
            units_sold=50.0,
            selling_days=10,
            oos_rate=0.10,
            n_observed_fields=2,
            n_total_fields=8,
        )
        results = score_all_skus([row])
        r = results[0]
        for attr in ["importance_score", "health_score", "priority_score"]:
            val = getattr(r, attr)
            assert 0.0 <= val <= 100.0
            assert not math.isnan(val)


# ---------------------------------------------------------------------------
# 5. Boundary conditions for sub-scores
# ---------------------------------------------------------------------------

class TestSubScoreBoundaries:

    def test_availability_perfect(self):
        score, obs = _availability_subscore(0.0)
        assert score == 100.0 and obs

    def test_availability_worst(self):
        score, obs = _availability_subscore(1.0)
        assert score == 0.0 and obs

    def test_availability_none_neutral(self):
        score, obs = _availability_subscore(None)
        assert score == 50.0 and not obs

    def test_waste_non_perishable_disabled(self):
        score, app = _waste_subscore(0.99, is_perishable=False)
        assert score == 0.0 and not app

    def test_waste_perishable_none_neutral(self):
        score, app = _waste_subscore(None, is_perishable=True)
        assert score == 50.0 and app

    def test_inventory_eff_in_target(self):
        mid = (DOS_TARGET_LOW + DOS_TARGET_HIGH) / 2
        score, obs = _inventory_eff_subscore(mid)
        assert score == 100.0 and obs

    def test_inventory_eff_zero_dos(self):
        score, obs = _inventory_eff_subscore(0.0)
        assert score == 0.0 and obs

    def test_inventory_eff_very_high(self):
        score, obs = _inventory_eff_subscore(DOS_TARGET_HIGH * 3)
        assert score == 0.0 and obs

    def test_supplier_all_perfect(self):
        score, obs = _supplier_subscore(1.0, 1.0, 0.0)
        assert score == 100.0 and obs

    def test_supplier_all_none_neutral(self):
        score, obs = _supplier_subscore(None, None, None)
        assert score == 50.0 and not obs

    def test_forecast_perfect_wmape(self):
        score, obs = _forecast_subscore(0.0, 0.0, 10.0)
        assert score == 100.0 and obs

    def test_forecast_none_neutral(self):
        score, obs = _forecast_subscore(None, None, 0.0)
        assert score == 50.0 and not obs


# ---------------------------------------------------------------------------
# 5b. _forecast_subscore – Phase 5 extended (probabilistic + segmented)
# ---------------------------------------------------------------------------

class TestForecastSubscoreExtended:
    """
    Covers the 60/40 legacy/new blend introduced in Phase 5.

    Legacy component  = 0.70 * wmape_score + 0.30 * bias_score
    New component     = mean of available new scores (PI80, promo WMAPE, event WMAPE)
    Blend             = 0.60 * legacy + 0.40 * new_component
    Fallback          = 100% legacy when no new metrics are provided
    """

    # --- No-new-metrics: must equal pure-legacy behaviour ---

    def test_no_new_params_equals_pure_legacy(self):
        """Calling with only wmape/bias/avg_daily must not change score versus legacy."""
        # wmape=50 → wmape_score = (1 - 50/100)*100 = 50
        # bias=0, avg=10 → bias_score = 100
        # legacy = 0.70*50 + 0.30*100 = 65
        score, obs = _forecast_subscore(50.0, 0.0, 10.0)
        assert abs(score - 65.0) < 1e-9
        assert obs is True

    def test_score_in_range_all_neutral(self):
        """Edge case: all None → neutral 50, not observed."""
        score, obs = _forecast_subscore(None, None, 0.0)
        assert 0.0 <= score <= 100.0
        assert obs is False

    # --- PI80 coverage ---

    def test_pi80_perfect_raises_score(self):
        """PI80 error=0 (perfect) with wmape=50: blended score > pure-legacy."""
        legacy, _ = _forecast_subscore(50.0, 0.0, 10.0)          # 65.0
        blended, _ = _forecast_subscore(50.0, 0.0, 10.0, pi80_coverage_error=0.0)
        # pi80_score = 100; final = 0.60*65 + 0.40*100 = 79
        assert blended > legacy
        assert abs(blended - 79.0) < 1e-9

    def test_pi80_worst_lowers_score(self):
        """PI80 error=±0.5 (worst case) with perfect legacy: blended score < legacy."""
        # wmape=0, bias=0 → legacy = 100
        legacy, _   = _forecast_subscore(0.0, 0.0, 10.0)
        blended, _  = _forecast_subscore(0.0, 0.0, 10.0, pi80_coverage_error=0.5)
        # pi80_score = clamp(100 - 100, 0, 100) = 0; final = 0.60*100 + 0.40*0 = 60
        assert blended < legacy
        assert abs(blended - 60.0) < 1e-9

    def test_pi80_neutral_error_half_point(self):
        """PI80 error=0.5 produces pi80_score=0; verifies the ×200 scaling."""
        score, _ = _forecast_subscore(0.0, 0.0, 10.0, pi80_coverage_error=0.5)
        assert abs(score - 60.0) < 1e-9

    def test_pi80_none_treated_as_absent(self):
        """pi80_coverage_error=None must behave as if PI80 was not computed."""
        score_no_pi80, obs1    = _forecast_subscore(0.0, 0.0, 10.0)
        score_none_pi80, obs2  = _forecast_subscore(0.0, 0.0, 10.0, pi80_coverage_error=None)
        assert score_no_pi80 == score_none_pi80

    # --- Promo WMAPE gating ---

    def test_promo_wmape_gated_below_min_points(self):
        """n_promo_points < MIN_PROMO_POINTS (3) → promo metric ignored."""
        # Should fall back to pure legacy (65)
        score, _ = _forecast_subscore(50.0, 0.0, 10.0, wmape_promo=0.0, n_promo_points=2)
        assert abs(score - 65.0) < 1e-9

    def test_promo_wmape_included_above_min_points(self):
        """n_promo_points >= MIN_PROMO_POINTS (3) → promo metric contributes."""
        legacy, _  = _forecast_subscore(50.0, 0.0, 10.0)                           # 65
        blended, _ = _forecast_subscore(50.0, 0.0, 10.0, wmape_promo=0.0, n_promo_points=3)
        # promo_score=100; final = 0.60*65 + 0.40*100 = 79
        assert blended > legacy
        assert abs(blended - 79.0) < 1e-9

    def test_promo_wmape_gated_at_boundary(self):
        """Exactly n_promo_points=3 is included (boundary = MIN_PROMO_POINTS)."""
        score_2, _ = _forecast_subscore(50.0, 0.0, 10.0, wmape_promo=0.0, n_promo_points=2)
        score_3, _ = _forecast_subscore(50.0, 0.0, 10.0, wmape_promo=0.0, n_promo_points=3)
        assert score_2 < score_3   # crossing threshold changes the result

    # --- Event WMAPE gating ---

    def test_event_wmape_gated_below_min_points(self):
        """n_event_points < MIN_EVENT_POINTS (3) → event metric ignored."""
        score, _ = _forecast_subscore(50.0, 0.0, 10.0, wmape_event=0.0, n_event_points=2)
        assert abs(score - 65.0) < 1e-9

    def test_event_wmape_included_above_min_points(self):
        """n_event_points >= MIN_EVENT_POINTS (3) → event metric contributes."""
        legacy, _  = _forecast_subscore(50.0, 0.0, 10.0)
        blended, _ = _forecast_subscore(50.0, 0.0, 10.0, wmape_event=0.0, n_event_points=3)
        assert blended > legacy

    # --- All three new metrics together ---

    def test_all_new_metrics_present_perfect_blend(self):
        """All three new metrics perfect (error=0, promo WMAPE=0, event WMAPE=0) + perfect legacy → 100."""
        score, obs = _forecast_subscore(
            0.0, 0.0, 10.0,
            pi80_coverage_error=0.0,
            wmape_promo=0.0, n_promo_points=5,
            wmape_event=0.0, n_event_points=5,
        )
        assert abs(score - 100.0) < 1e-9
        assert obs is True

    def test_all_new_metrics_60_40_math_verified(self):
        """Explicit 60/40 arithmetic check with known values."""
        # wmape=50 → wmape_score=50; bias=0, avg=10 → bias_score=100
        # legacy = 0.70*50 + 0.30*100 = 65
        # pi80_score = 100 - 0*200 = 100
        # promo_score = (1 - 20/100)*100 = 80
        # event_score = (1 - 40/100)*100 = 60
        # new_component = (100 + 80 + 60) / 3 = 80
        # final = 0.60*65 + 0.40*80 = 39 + 32 = 71
        score, _ = _forecast_subscore(
            50.0, 0.0, 10.0,
            pi80_coverage_error=0.0,
            wmape_promo=20.0, n_promo_points=5,
            wmape_event=40.0, n_event_points=5,
        )
        assert abs(score - 71.0) < 1e-9

    # --- Output invariants ---

    def test_score_always_in_0_100_extreme_inputs(self):
        """Score must stay clamped in [0, 100] with bizarre inputs."""
        for pi80 in (-1.0, 0.0, 0.5, 2.0):
            for wm in (0.0, 100.0, 1000.0, None):
                score, _ = _forecast_subscore(
                    wm, 0.0, 1.0,
                    pi80_coverage_error=pi80,
                    wmape_promo=wm, n_promo_points=10,
                    wmape_event=wm, n_event_points=10,
                )
                assert 0.0 <= score <= 100.0, f"Out of range for pi80={pi80}, wm={wm}"

    def test_observed_true_when_any_new_metric_present(self):
        """observed=True if at least one new metric contributes (even without legacy)."""
        _, obs = _forecast_subscore(None, None, 0.0, pi80_coverage_error=0.0)
        assert obs is True

    def test_observed_flag_with_pi80_only(self):
        """observed=True when only PI80 is present and wmape/bias are None."""
        _, obs = _forecast_subscore(None, None, 0.0, pi80_coverage_error=0.1)
        assert obs is True


# ---------------------------------------------------------------------------
# 6. Scoring version and metadata
# ---------------------------------------------------------------------------

def test_scoring_version_in_result():
    rows = [_make_row("A")]
    results = score_all_skus(rows)
    assert results[0].scoring_version == SCORING_VERSION


def test_data_quality_flag_ok_for_full_row():
    row = _make_row()  # all fields populated
    results = score_all_skus([row])
    assert results[0].data_quality_flag == "OK"


def test_data_quality_flag_low_data_for_few_selling_days():
    row = _make_row(selling_days=2)  # well below MIN_SELLING_DAYS_HIGH_CONFIDENCE
    row.n_observed_fields = 8
    results = score_all_skus([row])
    assert results[0].data_quality_flag in ("LOW_DATA", "MISSING_KPI")


# ---------------------------------------------------------------------------
# 7. Population batch (cross-SKU)
# ---------------------------------------------------------------------------

def test_population_max_min_range():
    """In a diverse population, max and min priority should span a reasonable range."""
    rows = [
        _make_row("A", units_sold=1000, selling_days=30, oos_rate=0.90),  # imp high, sick
        _make_row("B", units_sold=1000, selling_days=30, oos_rate=0.01),  # imp high, healthy
        _make_row("C", units_sold=5,    selling_days=2,  oos_rate=0.90),  # imp low, sick
        _make_row("D", units_sold=5,    selling_days=2,  oos_rate=0.01),  # imp low, healthy
    ]
    results = score_all_skus(rows)
    prio_map = {r.sku: r.priority_score for r in results}
    # A (important+sick) should have highest priority; D (unimportant+healthy) lowest
    assert prio_map["A"] >= prio_map["C"]
    assert prio_map["A"] >= prio_map["B"]
    assert prio_map["D"] <= prio_map["A"]


def test_single_sku_population_no_crash():
    """A population of 1 SKU should not crash (no division errors)."""
    rows = [_make_row("SOLO")]
    results = score_all_skus(rows)
    assert len(results) == 1
    r = results[0]
    assert 0.0 <= r.priority_score <= 100.0
