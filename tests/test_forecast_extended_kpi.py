"""
Tests for Phase 5 extended KPI functions:
  - compute_pi80_coverage_kpi   (src/analytics/kpi.py)
  - compute_promo_event_forecast_kpi (src/analytics/kpi.py)

Strategy:
  - Use a temporary CSVLayer populated with constant daily sales so the
    rolling-forecast loop runs cleanly.
  - lookback=100 days → ≥ 14 residuals (sufficient).
  - lookback=70  days → < 14 residuals (insufficient, expected empty result).
"""

import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import EventType, Transaction, SalesRecord, SKU, PromoWindow, EventUpliftRule
from src.persistence.csv_layer import CSVLayer
from src.analytics.kpi import compute_pi80_coverage_kpi, compute_promo_event_forecast_kpi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKU_ID = "TESTSKU"
ASOF   = date(2024, 6, 1)

# window_weeks=8 → min_start_idx = 8*7 + 7 = 63
# lookback=100  → candidate residual count = 100 - 63 = 37  (≥ 14 ✓)
# lookback=70   → candidate residual count = 70  - 63 =  7  (< 14 → insufficient)
LOOKBACK_SUFFICIENT   = 100
LOOKBACK_INSUFFICIENT = 70


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_data_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d)


@pytest.fixture
def csv_layer(temp_data_dir):
    return CSVLayer(data_dir=temp_data_dir)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _populate_sales(layer: CSVLayer, sku: str, asof: date, lookback: int, daily_qty: int = 10):
    """
    Write a SNAPSHOT + *varied* daily SalesRecord entries.

    Constant sales produce zero-variance residuals (σ = 0) which triggers the
    degenerate-interval guard in compute_pi80_coverage_kpi.  To avoid that, we
    alternate the daily quantity slightly so the rolling-forecast residuals have
    a non-zero standard deviation.
    """
    start = asof - timedelta(days=lookback - 1)
    # Large snapshot so stock never reaches zero (no OOS censoring)
    layer.write_transaction(Transaction(start, sku, EventType.SNAPSHOT, daily_qty * lookback * 3))
    sales = [
        # Alternate +0 / +3 so sigma of residuals > 0
        SalesRecord(start + timedelta(days=i), sku, daily_qty + (3 if i % 2 == 0 else 0))
        for i in range(lookback)
    ]
    layer.write_sales(sales)


# ---------------------------------------------------------------------------
# 1.  PI80 Coverage KPI
# ---------------------------------------------------------------------------

class TestPI80CoverageKpi:
    """Unit tests for compute_pi80_coverage_kpi."""

    def test_empty_sku_no_crash(self, csv_layer):
        """Unknown SKU with no data must return all-None without raising."""
        result = compute_pi80_coverage_kpi("UNKNOWN", 100, "strict", csv_layer, asof_date=ASOF)
        assert result["pi80_coverage"]       is None
        assert result["pi80_coverage_error"] is None
        assert result["n_pi80_points"]       == 0
        assert result["sufficient_data"]     is False

    def test_required_keys_always_present(self, csv_layer):
        """Result dict exposes the four contracted keys even when data is absent."""
        result = compute_pi80_coverage_kpi("NODATA", 50, "strict", csv_layer, asof_date=ASOF)
        for key in ("pi80_coverage", "pi80_coverage_error", "n_pi80_points", "sufficient_data"):
            assert key in result, f"Missing key: {key}"

    def test_insufficient_data_returns_empty(self, csv_layer):
        """lookback=70 yields < 14 residuals → sufficient_data=False."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_INSUFFICIENT)
        result = compute_pi80_coverage_kpi(
            SKU_ID, LOOKBACK_INSUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["pi80_coverage"]   is None
        assert result["sufficient_data"] is False

    def test_sufficient_data_returns_coverage(self, csv_layer):
        """lookback=100 yields ≥ 14 residuals → coverage is computed and in [0, 1]."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        result = compute_pi80_coverage_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["sufficient_data"] is True
        assert result["pi80_coverage"]   is not None
        assert 0.0 <= result["pi80_coverage"] <= 1.0

    def test_coverage_error_equals_coverage_minus_target(self, csv_layer):
        """pi80_coverage_error must equal pi80_coverage − 0.80 (within rounding tolerance)."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        result = compute_pi80_coverage_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        if result["pi80_coverage"] is None:
            pytest.skip("Insufficient data — coverage_error not defined")
        expected_error = round(result["pi80_coverage"] - 0.80, 4)
        assert abs(result["pi80_coverage_error"] - expected_error) < 1e-6

    def test_n_pi80_points_positive_on_sufficient_data(self, csv_layer):
        """n_pi80_points reports the number of eval-half residuals (> 0 when sufficient)."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        result = compute_pi80_coverage_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["n_pi80_points"] > 0

    def test_determinism(self, csv_layer):
        """Same inputs produce identical results on repeated calls."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        r1 = compute_pi80_coverage_kpi(SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF)
        r2 = compute_pi80_coverage_kpi(SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF)
        assert r1 == r2


# ---------------------------------------------------------------------------
# 2.  Promo / Event Forecast KPI
# ---------------------------------------------------------------------------

class TestPromoEventForecastKpi:
    """Unit tests for compute_promo_event_forecast_kpi."""

    def test_empty_sku_no_crash(self, csv_layer):
        """No data for SKU → returns zero counts and None WMAPE without raising."""
        result = compute_promo_event_forecast_kpi("UNKNOWN", 100, "strict", csv_layer, asof_date=ASOF)
        assert result["n_promo_points"] == 0
        assert result["n_event_points"] == 0
        assert result["wmape_promo"]    is None
        assert result["wmape_event"]    is None

    def test_required_keys_always_present(self, csv_layer):
        """Return dict always exposes the six contracted keys."""
        result = compute_promo_event_forecast_kpi(SKU_ID, 100, "strict", csv_layer, asof_date=ASOF)
        for key in ("wmape_promo", "bias_promo", "n_promo_points",
                    "wmape_event", "bias_event", "n_event_points"):
            assert key in result, f"Missing key: {key}"

    def test_no_promo_windows_gives_none_promo(self, csv_layer):
        """Without any promo windows in the CSV, promo metrics must be None."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["wmape_promo"]    is None
        assert result["n_promo_points"] == 0

    def test_no_event_rules_gives_none_event(self, csv_layer):
        """Without any event rules in the CSV, event metrics must be None."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["wmape_event"]    is None
        assert result["n_event_points"] == 0

    def test_promo_window_in_eval_period_increments_count(self, csv_layer):
        """A promo window covering days inside the rolling evaluation range tags those days."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        # Evaluation starts at index 63 from history start.
        # ASOF - 30 days is index 70 — well inside eval range.
        promo_start = ASOF - timedelta(days=30)
        promo_end   = ASOF - timedelta(days=21)   # 10-day promo window
        csv_layer.write_promo_calendar([
            PromoWindow(sku=SKU_ID, start_date=promo_start, end_date=promo_end)
        ])
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["n_promo_points"] > 0

    def test_event_rule_in_eval_period_tags_window(self, csv_layer):
        """An event rule whose delivery_date is in the eval period tags ±3 days as event days."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        delivery = ASOF - timedelta(days=25)   # history index 75, safely inside eval range
        rule = EventUpliftRule(
            delivery_date=delivery,
            reason="holiday",
            strength=0.5,
            scope_type="SKU",
            scope_key=SKU_ID,
        )
        csv_layer.write_event_uplift_rule(rule)
        sku_obj = SKU(sku=SKU_ID, description="Test Product")
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer,
            asof_date=ASOF, sku_obj=sku_obj
        )
        # Expect ≥ 1 event day (up to 7 if no censoring on the ±3-day window)
        assert result["n_event_points"] > 0

    def test_promo_and_event_are_independent(self, csv_layer):
        """Having promo days does not imply event days and vice versa."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        promo_start = ASOF - timedelta(days=30)
        promo_end   = ASOF - timedelta(days=21)
        csv_layer.write_promo_calendar([
            PromoWindow(sku=SKU_ID, start_date=promo_start, end_date=promo_end)
        ])
        # No event rules written
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["n_promo_points"] > 0
        assert result["n_event_points"] == 0    # no events recorded

    def test_wmape_none_when_segment_has_fewer_than_three_points(self, csv_layer):
        """A 1-day promo window yields exactly 1 evaluation point → wmape_promo is None."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        # Single-day promo inside eval range
        promo_day = ASOF - timedelta(days=20)
        csv_layer.write_promo_calendar([
            PromoWindow(sku=SKU_ID, start_date=promo_day, end_date=promo_day)
        ])
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer, asof_date=ASOF
        )
        assert result["n_promo_points"] == 1
        assert result["wmape_promo"] is None  # < _MIN_SEGMENT_POINTS (3)

    def test_scope_all_event_rule_applies(self, csv_layer):
        """An event rule with scope_type=ALL applies regardless of sku_obj attributes."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        delivery = ASOF - timedelta(days=25)
        rule = EventUpliftRule(
            delivery_date=delivery,
            reason="holiday",
            strength=0.5,
            scope_type="ALL",
            scope_key="",            # required to be empty for ALL scope
        )
        csv_layer.write_event_uplift_rule(rule)
        # sku_obj=None → event_rules are NOT filtered (scope_type=ALL)
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer,
            asof_date=ASOF, sku_obj=None
        )
        assert result["n_event_points"] > 0

    def test_event_rule_wrong_sku_not_tagged(self, csv_layer):
        """An event rule scoped to a different SKU should not tag any days for SKU_ID."""
        _populate_sales(csv_layer, SKU_ID, ASOF, LOOKBACK_SUFFICIENT)
        delivery = ASOF - timedelta(days=25)
        rule = EventUpliftRule(
            delivery_date=delivery,
            reason="holiday",
            strength=0.5,
            scope_type="SKU",
            scope_key="OTHER_SKU",   # not our SKU
        )
        csv_layer.write_event_uplift_rule(rule)
        sku_obj = SKU(sku=SKU_ID, description="Test Product")
        result = compute_promo_event_forecast_kpi(
            SKU_ID, LOOKBACK_SUFFICIENT, "strict", csv_layer,
            asof_date=ASOF, sku_obj=sku_obj
        )
        assert result["n_event_points"] == 0
