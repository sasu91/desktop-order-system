"""
Pipeline-level integration tests for the SKU scoring system.

Scope: end-to-end path   build_feature_row → score_all_skus
       with realistic synthetic SalesRecord / Transaction / SKU objects
       and dict-based KPI records (as produced by csv_layer.read_kpi_daily).

These tests complement test_scoring.py (unit invariants on pure functions)
by verifying that:
  - The full data pipeline produces valid outputs with real domain objects
  - None / missing KPI data is handled gracefully (no crash, correct flag)
  - Cold-start SKUs (no sales, no KPI) have low importance + LOW_DATA flag
  - Determinism: identical inputs → identical outputs
  - Cross-SKU ordering: high-volume > low-volume in importance
  - CSV-layer column alignment: asdict() keys ⊇ SCHEMAS["sku_scores_daily.csv"]
  - Perishable weight renormalisation end-to-end
  - Feature extraction: sales window filter, DOS, avg_daily_sales
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pytest

from src.analytics.scoring import (
    FeatureRow,
    SKUScoringResult,
    SCORING_VERSION,
    build_feature_row,
    score_all_skus,
)
from src.domain.models import SalesRecord, Transaction, SKU, EventType


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

REF_DATE = date(2026, 1, 15)
LOOKBACK = 30


def _make_sku(sku_id: str, shelf_life_days: int = 0) -> SKU:
    return SKU(
        sku=sku_id,
        description=f"Test SKU {sku_id}",
        shelf_life_days=shelf_life_days,
    )


def _make_sales(
    sku_id: str,
    ref_date: date,
    lookback_days: int,
    daily_qty: int,
    skip_last_n_days: int = 0,
) -> List[SalesRecord]:
    """Generate daily SalesRecords for the window [ref_date - lookback, ref_date)."""
    records = []
    start = ref_date - timedelta(days=lookback_days)
    for i in range(lookback_days - skip_last_n_days):
        day = start + timedelta(days=i)
        if day < ref_date:
            records.append(SalesRecord(date=day, sku=sku_id, qty_sold=daily_qty))
    return records


def _make_snapshot_tx(sku_id: str, ref_date: date, qty: int) -> Transaction:
    """A single SNAPSHOT transaction placing qty on-hand."""
    return Transaction(
        date=ref_date - timedelta(days=1),
        sku=sku_id,
        event=EventType.SNAPSHOT,
        qty=qty,
    )


def _make_kpi_record(
    sku_id: str,
    ref_date: date,
    oos_rate: float = 0.05,
    waste_rate: float = 0.02,
    fill_rate: float = 0.95,
    otif_rate: float = 0.90,
    avg_delay_days: float = 1.0,
    wmape: float = 20.0,
    bias: float = 0.3,
    # Phase 5 v4 fields (optional – omitted in legacy records)
    pi80_coverage: Optional[float] = None,
    pi80_coverage_error: Optional[float] = None,
    wmape_promo: Optional[float] = None,
    bias_promo: Optional[float] = None,
    n_promo_points: Optional[int] = None,
    wmape_event: Optional[float] = None,
    bias_event: Optional[float] = None,
    n_event_points: Optional[int] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "sku": sku_id,
        "date": ref_date.isoformat(),
        "oos_rate": str(oos_rate),
        "waste_rate": str(waste_rate),
        "fill_rate": str(fill_rate),
        "otif_rate": str(otif_rate),
        "avg_delay_days": str(avg_delay_days),
        "wmape": str(wmape),
        "bias": str(bias),
        "lookback_days": "30",
        "mode": "strict",
    }
    # Conditionally include v4 fields so tests can simulate legacy records (no v4 keys)
    _v4: Dict[str, Any] = {
        "pi80_coverage":       pi80_coverage,
        "pi80_coverage_error": pi80_coverage_error,
        "wmape_promo":         wmape_promo,
        "bias_promo":          bias_promo,
        "n_promo_points":      n_promo_points,
        "wmape_event":         wmape_event,
        "bias_event":          bias_event,
        "n_event_points":      n_event_points,
    }
    for k, v in _v4.items():
        if v is not None:
            record[k] = str(v)
    return record


# Expected CSV schema columns (must stay in sync with csv_layer.SCHEMAS)
_SCHEMA_COLS = [
    "date", "sku", "lookback_days", "scoring_version",
    "importance_score", "health_score", "priority_score",
    "importance_units_component", "importance_freq_component",
    "health_availability_score", "health_waste_score",
    "health_inventory_eff_score", "health_supplier_score",
    "health_forecast_score",
    "weight_availability", "weight_waste", "weight_inventory_eff",
    "weight_supplier", "weight_forecast",
    "raw_priority",
    "is_perishable", "confidence_score", "data_quality_flag",
    "missing_features_count", "notes",
]


# ---------------------------------------------------------------------------
# 1. Full pipeline – range invariants
# ---------------------------------------------------------------------------

class TestPipelineRangeInvariants:
    """Even with real domain objects the three scores must stay in [0, 100]."""

    def _run(self, n_skus: int = 5) -> List[SKUScoringResult]:
        all_sales: List[SalesRecord] = []
        all_tx: List[Transaction] = []
        feature_rows = []

        for i in range(n_skus):
            sid = f"P{i:03d}"
            sku_obj = _make_sku(sid, shelf_life_days=5 if i % 2 == 0 else 0)
            sales = _make_sales(sid, REF_DATE, LOOKBACK, daily_qty=10 + i * 5)
            tx = [_make_snapshot_tx(sid, REF_DATE, qty=50)]
            all_sales.extend(sales)
            all_tx.extend(tx)
            kpi = _make_kpi_record(sid, REF_DATE)
            row = build_feature_row(
                sku=sid,
                ref_date=REF_DATE,
                lookback_days=LOOKBACK,
                sales_records=all_sales,
                transactions=all_tx,
                kpi_record=kpi,
                sku_obj=sku_obj,
                stock_on_hand=50.0,
            )
            feature_rows.append(row)

        return score_all_skus(feature_rows)

    def test_all_scores_in_range(self):
        results = self._run(6)
        for r in results:
            assert 0.0 <= r.importance_score <= 100.0, f"{r.sku} importance={r.importance_score}"
            assert 0.0 <= r.health_score <= 100.0,     f"{r.sku} health={r.health_score}"
            assert 0.0 <= r.priority_score <= 100.0,   f"{r.sku} priority={r.priority_score}"

    def test_no_nan_or_none(self):
        results = self._run(4)
        for r in results:
            for fname in [
                "importance_score", "health_score", "priority_score",
                "confidence_score", "raw_priority",
            ]:
                val = getattr(r, fname)
                assert val is not None, f"{r.sku}.{fname} is None"
                assert not math.isnan(val), f"{r.sku}.{fname} is NaN"

    def test_confidence_in_unit_interval(self):
        results = self._run(4)
        for r in results:
            assert 0.0 <= r.confidence_score <= 1.0, f"{r.sku} confidence={r.confidence_score}"

    def test_version_tag(self):
        results = self._run(2)
        for r in results:
            assert r.scoring_version == SCORING_VERSION


# ---------------------------------------------------------------------------
# 2. None KPI record — no crash, degraded flag
# ---------------------------------------------------------------------------

class TestPipelineNullKPI:

    def _row_no_kpi(self, sku_id: str = "NOKPI") -> FeatureRow:
        sku_obj = _make_sku(sku_id)
        sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=8)
        return build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=sales,
            transactions=[],
            kpi_record=None,         # <— no KPI cache
            sku_obj=sku_obj,
            stock_on_hand=40.0,
        )

    def test_no_crash_with_none_kpi(self):
        row = self._row_no_kpi()
        results = score_all_skus([row])
        assert len(results) == 1

    def test_null_kpi_scores_still_in_range(self):
        row = self._row_no_kpi()
        r = score_all_skus([row])[0]
        assert 0.0 <= r.importance_score <= 100.0
        assert 0.0 <= r.health_score <= 100.0
        assert 0.0 <= r.priority_score <= 100.0

    def test_null_kpi_flag_not_ok(self):
        """Without KPI data the quality flag should signal degraded quality."""
        row = self._row_no_kpi()
        r = score_all_skus([row])[0]
        assert r.data_quality_flag != "OK", \
            f"Expected a non-OK flag when KPI is missing, got '{r.data_quality_flag}'"

    def test_null_kpi_kpi_fields_are_none(self):
        row = self._row_no_kpi()
        assert row.oos_rate is None
        assert row.wmape is None
        assert row.fill_rate is None

    def test_null_kpi_sales_still_extracted(self):
        """Sales-derived features (units_sold, selling_days) must be computed even without KPI."""
        row = self._row_no_kpi()
        assert row.units_sold > 0
        assert row.selling_days > 0


# ---------------------------------------------------------------------------
# 3. Cold-start SKU (no sales, no KPI)
# ---------------------------------------------------------------------------

class TestPipelineColdStart:

    def _cold_row(self, sku_id: str = "COLD01") -> FeatureRow:
        sku_obj = _make_sku(sku_id)
        return build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=[],        # no sales
            transactions=[],
            kpi_record=None,
            sku_obj=sku_obj,
            stock_on_hand=0.0,
        )

    def test_cold_start_no_crash(self):
        row = self._cold_row()
        results = score_all_skus([row])
        assert len(results) == 1

    def test_cold_start_importance_penalised(self):
        """
        New SKU with zero sales must be penalised vs the neutral mid-point.
        In a single-element population _robust_scale_list gives 50.0 (neutral);
        the low-data penalty (selling_days=0) halves that → 25.0.
        Either way the score must be strictly below 50.0 (not a full score).
        """
        row = self._cold_row()
        r = score_all_skus([row])[0]
        assert r.importance_score < 50.0

    def test_cold_start_flag_low_data(self):
        row = self._cold_row()
        r = score_all_skus([row])[0]
        assert r.data_quality_flag == "LOW_DATA"

    def test_cold_start_confidence_low(self):
        row = self._cold_row()
        r = score_all_skus([row])[0]
        assert r.confidence_score < 0.5

    def test_cold_start_dos_zero(self):
        row = self._cold_row()
        assert row.days_of_supply == 0.0


# ---------------------------------------------------------------------------
# 4. Determinism (idempotency of scoring)
# ---------------------------------------------------------------------------

class TestPipelineDeterminism:

    def _build_rows(self) -> List[FeatureRow]:
        rows = []
        for i, sid in enumerate(["D001", "D002", "D003"]):
            sku_obj = _make_sku(sid, shelf_life_days=5 if i else 0)
            sales = _make_sales(sid, REF_DATE, LOOKBACK, daily_qty=5 + i * 3)
            kpi = _make_kpi_record(sid, REF_DATE, oos_rate=0.05 + i * 0.02)
            rows.append(
                build_feature_row(
                    sku=sid,
                    ref_date=REF_DATE,
                    lookback_days=LOOKBACK,
                    sales_records=sales,
                    transactions=[],
                    kpi_record=kpi,
                    sku_obj=sku_obj,
                    stock_on_hand=30.0,
                )
            )
        return rows

    def test_same_input_same_output(self):
        rows = self._build_rows()
        r1 = score_all_skus(rows)
        r2 = score_all_skus(rows)
        for a, b in zip(r1, r2):
            assert a.importance_score == b.importance_score
            assert a.health_score == b.health_score
            assert a.priority_score == b.priority_score
            assert a.data_quality_flag == b.data_quality_flag

    def test_order_invariant(self):
        """Reversing row order must not change per-SKU scores (only ordering in list)."""
        rows = self._build_rows()
        forward = {r.sku: r for r in score_all_skus(rows)}
        backward = {r.sku: r for r in score_all_skus(list(reversed(rows)))}
        for sku in forward:
            assert abs(forward[sku].importance_score - backward[sku].importance_score) < 1e-9
            assert abs(forward[sku].health_score - backward[sku].health_score) < 1e-9
            assert abs(forward[sku].priority_score - backward[sku].priority_score) < 1e-9


# ---------------------------------------------------------------------------
# 5. Cross-SKU ordering
# ---------------------------------------------------------------------------

class TestPipelineCrossSkuOrdering:

    def test_high_volume_higher_importance(self):
        """A SKU selling 10× more units must get a higher importance score."""
        common_kpi = _make_kpi_record("X", REF_DATE)

        def _row(sku_id, daily_qty):
            sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=daily_qty)
            kpi = {**common_kpi, "sku": sku_id}
            return build_feature_row(
                sku=sku_id,
                ref_date=REF_DATE,
                lookback_days=LOOKBACK,
                sales_records=sales,
                transactions=[],
                kpi_record=kpi,
                sku_obj=_make_sku(sku_id),
                stock_on_hand=50.0,
            )

        rows = [_row("HIGH", 100), _row("LOW", 10)]
        results = {r.sku: r for r in score_all_skus(rows)}
        assert results["HIGH"].importance_score > results["LOW"].importance_score

    def test_high_oos_lower_health(self):
        """A SKU with 80% OOS rate should have lower health than one with 5% OOS."""
        def _row(sku_id, oos_rate):
            sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=10)
            kpi = _make_kpi_record(sku_id, REF_DATE, oos_rate=oos_rate)
            return build_feature_row(
                sku=sku_id,
                ref_date=REF_DATE,
                lookback_days=LOOKBACK,
                sales_records=sales,
                transactions=[],
                kpi_record=kpi,
                sku_obj=_make_sku(sku_id),
                stock_on_hand=50.0,
            )

        rows = [_row("GOOD_OOS", 0.05), _row("BAD_OOS", 0.80)]
        results = {r.sku: r for r in score_all_skus(rows)}
        assert results["GOOD_OOS"].health_score > results["BAD_OOS"].health_score

    def test_high_wmape_lower_health(self):
        """SKU with WMAPE=80 should score lower health (forecast sub-score) than WMAPE=10."""
        def _row(sku_id, wmape):
            sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=10)
            kpi = _make_kpi_record(sku_id, REF_DATE, wmape=wmape)
            return build_feature_row(
                sku=sku_id,
                ref_date=REF_DATE,
                lookback_days=LOOKBACK,
                sales_records=sales,
                transactions=[],
                kpi_record=kpi,
                sku_obj=_make_sku(sku_id),
                stock_on_hand=50.0,
            )

        rows = [_row("GOOD_FC", 10.0), _row("BAD_FC", 80.0)]
        results = {r.sku: r for r in score_all_skus(rows)}
        assert results["GOOD_FC"].health_forecast_score > results["BAD_FC"].health_forecast_score


# ---------------------------------------------------------------------------
# 6. CSV-layer column alignment
# ---------------------------------------------------------------------------

class TestPipelineCsvKeys:
    """asdict() output must cover all columns expected by write_sku_scores_daily_batch."""

    def test_asdict_covers_schema_columns(self):
        sku_obj = _make_sku("CSV001", shelf_life_days=3)
        sales = _make_sales("CSV001", REF_DATE, LOOKBACK, daily_qty=7)
        kpi = _make_kpi_record("CSV001", REF_DATE)
        row = build_feature_row(
            sku="CSV001",
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=sales,
            transactions=[],
            kpi_record=kpi,
            sku_obj=sku_obj,
            stock_on_hand=35.0,
        )
        result = score_all_skus([row])[0]
        d = asdict(result)
        missing = [col for col in _SCHEMA_COLS if col not in d]
        assert not missing, f"Missing keys in asdict output: {missing}"

    def test_asdict_no_extra_mandatory_key_missing(self):
        """All schema columns are present and not None (except 'notes' which can be empty)."""
        sku_obj = _make_sku("CSV002")
        sales = _make_sales("CSV002", REF_DATE, LOOKBACK, daily_qty=5)
        kpi = _make_kpi_record("CSV002", REF_DATE)
        row = build_feature_row(
            sku="CSV002",
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=sales,
            transactions=[],
            kpi_record=kpi,
            sku_obj=sku_obj,
            stock_on_hand=20.0,
        )
        result = score_all_skus([row])[0]
        d = asdict(result)
        for col in _SCHEMA_COLS:
            if col == "notes":
                continue   # notes is allowed to be empty string
            assert d.get(col) is not None, f"Column '{col}' is None in asdict output"


# ---------------------------------------------------------------------------
# 7. Perishable vs non-perishable weight renormalisation (end-to-end)
# ---------------------------------------------------------------------------

class TestPipelinePerishable:

    def _row_pair(self):
        """Return (perishable_FeatureRow, non_perishable_FeatureRow) with identical data."""
        def _row(sku_id, shelf_life):
            sku_obj = _make_sku(sku_id, shelf_life_days=shelf_life)
            sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=10)
            kpi = _make_kpi_record(sku_id, REF_DATE, waste_rate=0.05)
            return build_feature_row(
                sku=sku_id,
                ref_date=REF_DATE,
                lookback_days=LOOKBACK,
                sales_records=sales,
                transactions=[],
                kpi_record=kpi,
                sku_obj=sku_obj,
                stock_on_hand=50.0,
            )
        return _row("PERISH", 5), _row("NOPERISH", 0)

    def test_perishable_flag_set(self):
        p_row, np_row = self._row_pair()
        p_res, np_res = score_all_skus([p_row, np_row])
        assert p_res.is_perishable is True
        assert np_res.is_perishable is False

    def test_non_perishable_waste_weight_zero(self):
        p_row, np_row = self._row_pair()
        results = {r.sku: r for r in score_all_skus([p_row, np_row])}
        assert results["NOPERISH"].weight_waste == 0.0

    def test_perishable_waste_weight_positive(self):
        p_row, np_row = self._row_pair()
        results = {r.sku: r for r in score_all_skus([p_row, np_row])}
        assert results["PERISH"].weight_waste > 0.0

    def test_active_weights_sum_to_one(self):
        p_row, np_row = self._row_pair()
        for r in score_all_skus([p_row, np_row]):
            total = (
                r.weight_availability + r.weight_waste +
                r.weight_inventory_eff + r.weight_supplier +
                r.weight_forecast
            )
            assert abs(total - 1.0) < 1e-9, f"{r.sku} weights sum to {total}"


# ---------------------------------------------------------------------------
# 8. Feature extraction detail checks
# ---------------------------------------------------------------------------

class TestBuildFeatureRowExtraction:

    def test_sales_window_filter(self):
        """Only sales within [ref_date - lookback, ref_date) are counted."""
        sku_id = "WIN001"
        sku_obj = _make_sku(sku_id)
        # 20 days inside window + 10 days outside (before window)
        in_window = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=5)
        outside = [
            SalesRecord(date=REF_DATE - timedelta(days=LOOKBACK + i), sku=sku_id, qty_sold=99)
            for i in range(1, 11)
        ]
        row = build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=in_window + outside,
            transactions=[],
            kpi_record=None,
            sku_obj=sku_obj,
            stock_on_hand=0.0,
        )
        # Only in-window sales should count (5 * 30 days = 150)
        assert row.units_sold == 5 * LOOKBACK

    def test_avg_daily_sales_formula(self):
        sku_id = "AVG001"
        sku_obj = _make_sku(sku_id)
        sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=4)
        row = build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=sales,
            transactions=[],
            kpi_record=None,
            sku_obj=sku_obj,
            stock_on_hand=0.0,
        )
        # avg_daily_sales = units_sold / lookback_days = (4*30)/30 = 4.0
        assert abs(row.avg_daily_sales_for_bias - 4.0) < 1e-9

    def test_dos_positive_when_stock_and_sales(self):
        sku_id = "DOS001"
        sku_obj = _make_sku(sku_id)
        sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=5)
        row = build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=sales,
            transactions=[],
            kpi_record=None,
            sku_obj=sku_obj,
            stock_on_hand=50.0,       # 50 units / 5 avg = 10 days
        )
        assert row.days_of_supply == pytest.approx(10.0, rel=1e-6)

    def test_dos_none_when_stock_but_no_sales(self):
        """DOS is None (undefined) if there is stock but no sales → slow mover."""
        sku_id = "DOS002"
        sku_obj = _make_sku(sku_id)
        row = build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=[],          # no sales
            transactions=[],
            kpi_record=None,
            sku_obj=sku_obj,
            stock_on_hand=100.0,       # stock exists
        )
        assert row.days_of_supply is None

    def test_dos_zero_when_no_stock_no_sales(self):
        sku_id = "DOS003"
        sku_obj = _make_sku(sku_id)
        row = build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=[],
            transactions=[],
            kpi_record=None,
            sku_obj=sku_obj,
            stock_on_hand=0.0,
        )
        assert row.days_of_supply == 0.0

    def test_kpi_string_values_parsed_correctly(self):
        """csv_layer returns strings; build_feature_row must coerce them to float."""
        sku_id = "PARSE01"
        sku_obj = _make_sku(sku_id)
        kpi = _make_kpi_record(sku_id, REF_DATE, oos_rate=0.10, wmape=35.0)
        row = build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=[],
            transactions=[],
            kpi_record=kpi,
            sku_obj=sku_obj,
            stock_on_hand=0.0,
        )
        assert isinstance(row.oos_rate, float)
        assert abs(row.oos_rate - 0.10) < 1e-9
        assert isinstance(row.wmape, float)
        assert abs(row.wmape - 35.0) < 1e-9

    def test_kpi_none_string_treated_as_missing(self):
        """'None' string values in KPI dict must be treated as missing (→ field = None)."""
        sku_id = "PARSENIL"
        sku_obj = _make_sku(sku_id)
        kpi = {
            "sku": sku_id,
            "date": REF_DATE.isoformat(),
            "oos_rate": "None",
            "wmape": "",
            "fill_rate": None,
            "otif_rate": "0.9",
        }
        row = build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=[],
            transactions=[],
            kpi_record=kpi,
            sku_obj=sku_obj,
            stock_on_hand=0.0,
        )
        assert row.oos_rate is None    # "None" → None
        assert row.wmape is None        # ""     → None
        assert row.fill_rate is None    # None   → None
        assert isinstance(row.otif_rate, float)

    def test_shelf_life_zero_means_not_perishable(self):
        sku_id = "NP001"
        sku_obj = _make_sku(sku_id, shelf_life_days=0)
        row = build_feature_row(
            sku=sku_id, ref_date=REF_DATE, lookback_days=LOOKBACK,
            sales_records=[], transactions=[], kpi_record=None,
            sku_obj=sku_obj, stock_on_hand=0.0,
        )
        assert row.shelf_life_days is None

    def test_shelf_life_positive_means_perishable(self):
        sku_id = "P001"
        sku_obj = _make_sku(sku_id, shelf_life_days=7)
        row = build_feature_row(
            sku=sku_id, ref_date=REF_DATE, lookback_days=LOOKBACK,
            sales_records=[], transactions=[], kpi_record=None,
            sku_obj=sku_obj, stock_on_hand=0.0,
        )
        assert row.shelf_life_days == 7


# ---------------------------------------------------------------------------
# 9. Phase 5 v4 extended forecast KPI fields
# ---------------------------------------------------------------------------

class TestPipelineV4Fields:
    """
    Verify that the 8 Phase-5 extended KPI fields (PI80 + promo/event) are
    correctly wired through build_feature_row → FeatureRow → score_all_skus.
    """

    def _base_row(self, sku_id: str, kpi: Dict[str, Any]) -> FeatureRow:
        sku_obj = _make_sku(sku_id)
        sales = _make_sales(sku_id, REF_DATE, LOOKBACK, daily_qty=5)
        return build_feature_row(
            sku=sku_id,
            ref_date=REF_DATE,
            lookback_days=LOOKBACK,
            sales_records=sales,
            transactions=[],
            kpi_record=kpi,
            sku_obj=sku_obj,
            stock_on_hand=25.0,
        )

    def test_v4_fields_parsed_from_kpi_record(self):
        """build_feature_row correctly parses all 8 Phase-5 fields from the KPI dict."""
        kpi = _make_kpi_record(
            "V4A", REF_DATE,
            pi80_coverage=0.85,
            pi80_coverage_error=0.05,
            wmape_promo=15.0,
            bias_promo=-0.5,
            n_promo_points=6,
            wmape_event=25.0,
            bias_event=1.0,
            n_event_points=4,
        )
        row = self._base_row("V4A", kpi)
        assert abs(row.pi80_coverage       - 0.85) < 1e-9
        assert abs(row.pi80_coverage_error - 0.05) < 1e-9
        assert abs(row.wmape_promo         - 15.0) < 1e-9
        assert abs(row.bias_promo          - (-0.5)) < 1e-9
        assert row.n_promo_points == 6
        assert abs(row.wmape_event         - 25.0) < 1e-9
        assert abs(row.bias_event          - 1.0)  < 1e-9
        assert row.n_event_points == 4

    def test_legacy_kpi_record_without_v4_fields_no_crash(self):
        """A KPI record that predates Phase 5 (no v4 keys) must parse without error."""
        kpi = _make_kpi_record("V4B", REF_DATE)   # no v4 kwargs → no v4 keys in dict
        row = self._base_row("V4B", kpi)           # must not raise
        assert row.pi80_coverage       is None
        assert row.pi80_coverage_error is None
        assert row.wmape_promo         is None
        assert row.wmape_event         is None
        assert row.n_promo_points      == 0
        assert row.n_event_points      == 0

    def test_v4_fields_none_produces_valid_score(self):
        """score_all_skus must succeed with all v4 fields absent (fallback to legacy)."""
        kpi = _make_kpi_record("V4C", REF_DATE)
        row = self._base_row("V4C", kpi)
        results = score_all_skus([row])
        r = results[0]
        assert 0.0 <= r.health_score <= 100.0
        assert 0.0 <= r.priority_score <= 100.0

    def test_perfect_pi80_improves_forecast_score(self):
        """
        A SKU with perfect PI80 (error=0) must score higher on forecast than
        a SKU with the worst PI80 (error=0.5), all else equal.
        """
        kpi_good = _make_kpi_record("GOOD", REF_DATE, wmape=20.0, pi80_coverage_error=0.0)
        kpi_bad  = _make_kpi_record("BAD",  REF_DATE, wmape=20.0, pi80_coverage_error=0.5)

        sku_obj_g = _make_sku("GOOD")
        sku_obj_b = _make_sku("BAD")
        sales_g = _make_sales("GOOD", REF_DATE, LOOKBACK, daily_qty=5)
        sales_b = _make_sales("BAD",  REF_DATE, LOOKBACK, daily_qty=5)

        def _row(sid, sku_obj, sales, kpi):
            return build_feature_row(
                sku=sid, ref_date=REF_DATE, lookback_days=LOOKBACK,
                sales_records=sales, transactions=[], kpi_record=kpi,
                sku_obj=sku_obj, stock_on_hand=25.0,
            )

        row_good = _row("GOOD", sku_obj_g, sales_g, kpi_good)
        row_bad  = _row("BAD",  sku_obj_b, sales_b, kpi_bad)

        results = {r.sku: r for r in score_all_skus([row_good, row_bad])}
        assert results["GOOD"].health_score > results["BAD"].health_score

    def test_v4_int_fields_parsed_as_int(self):
        """n_promo_points and n_event_points must be integers, not floats."""
        kpi = _make_kpi_record(
            "INTCHK", REF_DATE, n_promo_points=7, n_event_points=3
        )
        row = self._base_row("INTCHK", kpi)
        assert isinstance(row.n_promo_points, int)
        assert isinstance(row.n_event_points, int)

    def test_string_none_v4_fields_treated_as_missing(self):
        """'None' string for v4 field must be treated as missing (not raise)."""
        sku_id = "STRNIL"
        sku_obj = _make_sku(sku_id)
        kpi = _make_kpi_record(sku_id, REF_DATE)
        kpi["pi80_coverage"]       = "None"
        kpi["pi80_coverage_error"] = ""
        kpi["wmape_promo"]         = "None"
        row = build_feature_row(
            sku=sku_id, ref_date=REF_DATE, lookback_days=LOOKBACK,
            sales_records=[], transactions=[], kpi_record=kpi,
            sku_obj=sku_obj, stock_on_hand=0.0,
        )
        assert row.pi80_coverage       is None
        assert row.pi80_coverage_error is None
        assert row.wmape_promo         is None
