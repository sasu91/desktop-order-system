"""
Regression tests for the error cascade identified on 2026-03-24.

Covers:
1. SKU row with invalid forecast_method is sanitized, NOT dropped
2. SKU row with invalid waste_penalty_mode is sanitized, NOT dropped
3. Receiving matching tolerates int SKU codes (str vs int boundary)
4. Receiving matching tolerates lowercase/legacy order status values
5. _sync_skus_to_sqlite finds SKU even when stored as int in CSV
"""
import csv
import tempfile
from datetime import date
from pathlib import Path

import pytest

from src.persistence.csv_layer import CSVLayer
from src.workflows.receiving_v2 import ReceivingWorkflow
from src.domain.models import EventType, SKU, DemandVariability
from src.utils.sku_validation import validate_sku_canonical, is_sku_canonical, SkuFormatError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_raw_skus_csv(data_dir: Path, rows: list[dict]) -> None:
    """Write a skus.csv bypassing CSVLayer validation (simulates legacy/external data)."""
    schema = [
        "sku", "description", "ean", "ean_secondary", "moq", "pack_size",
        "lead_time_days", "review_period", "safety_stock", "shelf_life_days",
        "min_shelf_life_days", "waste_penalty_mode", "waste_penalty_factor",
        "waste_risk_threshold", "max_stock", "reorder_point", "demand_variability",
        "category", "department", "oos_boost_percent", "oos_detection_mode",
        "oos_popup_preference", "forecast_method", "mc_distribution",
        "mc_n_simulations", "mc_random_seed", "mc_output_stat",
        "mc_output_percentile", "mc_horizon_mode", "mc_horizon_days",
        "in_assortment", "target_csl", "has_expiry_label",
    ]
    path = data_dir / "skus.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=schema, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full_row = {k: "" for k in schema}
            full_row.update(row)
            # Set safe defaults for required fields if missing
            full_row.setdefault("moq", "1")
            full_row.setdefault("pack_size", "1")
            full_row.setdefault("lead_time_days", "7")
            full_row.setdefault("review_period", "7")
            full_row.setdefault("safety_stock", "0")
            full_row.setdefault("max_stock", "999")
            full_row.setdefault("reorder_point", "0")
            full_row.setdefault("in_assortment", "true")
            full_row.setdefault("oos_popup_preference", "ask")
            full_row.setdefault("target_csl", "0")
            writer.writerow(full_row)


def _write_raw_order_logs(data_dir: Path, rows: list[dict]) -> None:
    """Write order_logs.csv bypassing CSVLayer (simulates external/legacy data)."""
    schema = [
        "order_id", "date", "sku", "qty_ordered", "qty_received", "status",
        "receipt_date", "promo_prebuild_enabled", "promo_start_date",
        "target_open_qty", "projected_stock_on_promo_start", "prebuild_delta_qty",
        "prebuild_qty", "prebuild_coverage_days", "prebuild_distribution_note",
        "event_uplift_active", "event_delivery_date", "event_reason",
        "event_u_store_day", "event_quantile", "event_fallback_level",
        "event_beta_i", "event_beta_fallback_level", "event_m_i", "event_explain_short",
    ]
    path = data_dir / "order_logs.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=schema, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full_row = {k: "" for k in schema}
            full_row.update(row)
            full_row.setdefault("qty_received", "0")
            full_row.setdefault("event_u_store_day", "1.0")
            full_row.setdefault("event_beta_i", "1.0")
            full_row.setdefault("event_m_i", "1.0")
            writer.writerow(full_row)


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def csv_layer(data_dir):
    return CSVLayer(data_dir=data_dir)


# ---------------------------------------------------------------------------
# 1. SKU with invalid forecast_method is sanitized, NOT dropped
# ---------------------------------------------------------------------------

class TestSkuEnumSanitization:

    def test_invalid_forecast_method_not_dropped(self, data_dir, csv_layer):
        """
        Regression: SKU with forecast_method='none' (invalid) previously caused
        ValueError in __post_init__ and the entire row was silently dropped.
        After fix, the row must be present with forecast_method reset to ''.
        """
        _write_raw_skus_csv(data_dir, [
            {"sku": "ACQUA_FRIZZANTE", "description": "Acqua Frizzante 0.5L",
             "forecast_method": "none"},  # invalid value
        ])
        skus = csv_layer.read_skus()
        sku_ids = [s.sku for s in skus]
        assert "ACQUA_FRIZZANTE" in sku_ids, (
            "SKU with invalid forecast_method must NOT be dropped from catalog"
        )
        sku_obj = next(s for s in skus if s.sku == "ACQUA_FRIZZANTE")
        assert sku_obj.forecast_method == "", (
            "Invalid forecast_method must be reset to '' (empty string)"
        )

    def test_invalid_waste_penalty_mode_not_dropped(self, data_dir, csv_layer):
        """
        Regression: SKU with waste_penalty_mode='none' (invalid) must be sanitized to ''.
        The row must survive read_skus.
        """
        _write_raw_skus_csv(data_dir, [
            {"sku": "SKU_WASTE", "description": "Test SKU",
             "waste_penalty_mode": "none"},  # invalid value
        ])
        skus = csv_layer.read_skus()
        sku_ids = [s.sku for s in skus]
        assert "SKU_WASTE" in sku_ids, (
            "SKU with invalid waste_penalty_mode must NOT be dropped from catalog"
        )
        sku_obj = next(s for s in skus if s.sku == "SKU_WASTE")
        assert sku_obj.waste_penalty_mode == ""

    def test_multiple_invalid_enums_all_sanitized(self, data_dir, csv_layer):
        """Multiple invalid enums in the same row: all reset, row retained."""
        _write_raw_skus_csv(data_dir, [
            {"sku": "MULTI_BAD", "description": "Multi bad enums",
             "forecast_method": "unknown_method",
             "waste_penalty_mode": "medium",
             "mc_distribution": "cauchy",
             "mc_output_stat": "variance",
             "mc_horizon_mode": "weekly"},
        ])
        skus = csv_layer.read_skus()
        assert any(s.sku == "MULTI_BAD" for s in skus), (
            "SKU with multiple invalid enum fields must still be present"
        )
        sku_obj = next(s for s in skus if s.sku == "MULTI_BAD")
        assert sku_obj.forecast_method == ""
        assert sku_obj.waste_penalty_mode == ""
        assert sku_obj.mc_distribution == ""
        assert sku_obj.mc_output_stat == ""
        assert sku_obj.mc_horizon_mode == ""

    def test_valid_forecast_method_preserved(self, data_dir, csv_layer):
        """Valid forecast_method values must not be altered."""
        valid_methods = ["", "simple", "monte_carlo", "croston", "sba", "tsb", "intermittent_auto"]
        rows = [
            {"sku": f"SKU_{m or 'empty'}", "description": f"Test {m}", "forecast_method": m}
            for m in valid_methods
        ]
        _write_raw_skus_csv(data_dir, rows)
        skus = csv_layer.read_skus()
        for m in valid_methods:
            sku_id = f"SKU_{m or 'empty'}"
            sku_obj = next((s for s in skus if s.sku == sku_id), None)
            assert sku_obj is not None, f"SKU {sku_id} should be present"
            assert sku_obj.forecast_method == m, (
                f"forecast_method='{m}' should be preserved unchanged"
            )


# ---------------------------------------------------------------------------
# 2. Receiving workflow: SKU type normalization (int vs str)
# ---------------------------------------------------------------------------

class TestReceivingSkuNormalization:

    def _setup_sku_and_order(self, data_dir: Path, csv_layer: CSVLayer, sku_id: str):
        """Create SKU and PENDING order, writing directly to CSV."""
        _write_raw_skus_csv(data_dir, [
            {"sku": sku_id, "description": f"Product {sku_id}",
             "moq": "1", "pack_size": "1", "lead_time_days": "3"},
        ])
        _write_raw_order_logs(data_dir, [
            {"order_id": f"ORD_{sku_id}_1", "date": "2026-03-20",
             "sku": str(sku_id), "qty_ordered": "30", "qty_received": "0",
             "status": "PENDING", "receipt_date": "2026-03-24"},
        ])

    def test_receiving_with_canonical_str_sku_matches_orders(self, data_dir, csv_layer):
        """
        Normal case: canonical 7-digit str sku (e.g. '0450633') matches order.
        """
        self._setup_sku_and_order(data_dir, csv_layer, "0450633")
        workflow = ReceivingWorkflow(csv_layer)

        txns, already_processed, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-20260324",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "0450633", "qty_received": 30}],
        )

        assert not already_processed
        assert len(txns) == 1
        assert txns[0].event == EventType.RECEIPT
        assert txns[0].qty == 30
        assert len(order_updates) == 1, (
            "Canonical SKU must match the order and produce an order update"
        )

    def test_receiving_with_non_canonical_int_sku_raises(self, data_dir, csv_layer):
        """
        Strict mode: passing int SKU (e.g. 450633) is rejected with SkuFormatError.
        Previously (tolerance mode) this would be silently converted to string.
        An int cannot represent a zero-padded SKU, so it is always non-canonical.
        """
        self._setup_sku_and_order(data_dir, csv_layer, "0450633")
        workflow = ReceivingWorkflow(csv_layer)

        with pytest.raises(SkuFormatError):
            workflow.close_receipt_by_document(
                document_id="DDT-20260324B",
                receipt_date=date(2026, 3, 24),
                items=[{"sku": 450633, "qty_received": 30}],  # int — non-canonical
            )

    def test_receiving_with_non_canonical_str_sku_raises(self, data_dir, csv_layer):
        """
        Strict mode: str SKU without leading zero ('450633') is rejected.
        This is the exact production scenario: '0450663' → lost leading zero → '450663'.
        """
        self._setup_sku_and_order(data_dir, csv_layer, "0450634")
        workflow = ReceivingWorkflow(csv_layer)

        with pytest.raises(SkuFormatError):
            workflow.close_receipt_by_document(
                document_id="DDT-20260324C",
                receipt_date=date(2026, 3, 24),
                items=[{"sku": "450634", "qty_received": 6}],  # missing leading zero
            )


# ---------------------------------------------------------------------------
# 3. Receiving workflow: case-insensitive order status matching
# ---------------------------------------------------------------------------

class TestReceivingStatusNormalization:

    def test_lowercase_pending_status_matched(self, data_dir, csv_layer):
        """
        Orders with status='pending' (lowercase) must be found by receiving.
        """
        _write_raw_skus_csv(data_dir, [
            {"sku": "0000001", "description": "SKU lowercase status"},
        ])
        _write_raw_order_logs(data_dir, [
            {"order_id": "ORD_LOW_1", "date": "2026-03-20",
             "sku": "0000001", "qty_ordered": "10", "qty_received": "0",
             "status": "pending"},  # lowercase — non-standard
        ])

        workflow = ReceivingWorkflow(csv_layer)
        txns, _, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-LOW",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "0000001", "qty_received": 10}],
        )

        assert len(txns) == 1
        assert len(order_updates) == 1, "lowercase 'pending' status must match"

    def test_partial_lowercase_status_matched(self, data_dir, csv_layer):
        """Orders with status='partial' (lowercase) must be found."""
        _write_raw_skus_csv(data_dir, [
            {"sku": "0000002", "description": "SKU partial lower"},
        ])
        _write_raw_order_logs(data_dir, [
            {"order_id": "ORD_PART_1", "date": "2026-03-20",
             "sku": "0000002", "qty_ordered": "20", "qty_received": "5",
             "status": "partial"},  # lowercase
        ])

        workflow = ReceivingWorkflow(csv_layer)
        txns, _, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-PART",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "0000002", "qty_received": 15}],
        )

        assert len(txns) == 1
        assert len(order_updates) == 1, "lowercase 'partial' status must match"

    def test_received_status_not_matched(self, data_dir, csv_layer):
        """
        Orders with status='RECEIVED' must NOT be matched (already completed).
        A new RECEIPT event without order linkage is still created.
        """
        _write_raw_skus_csv(data_dir, [
            {"sku": "0000003", "description": "SKU already received"},
        ])
        _write_raw_order_logs(data_dir, [
            {"order_id": "ORD_DONE_1", "date": "2026-03-10",
             "sku": "0000003", "qty_ordered": "50", "qty_received": "50",
             "status": "RECEIVED"},
        ])

        workflow = ReceivingWorkflow(csv_layer)
        txns, _, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-DONE",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "0000003", "qty_received": 10}],
        )

        # RECEIPT event is still created (manual stock-in), but no order is updated
        assert len(txns) == 1
        assert txns[0].event == EventType.RECEIPT
        assert len(order_updates) == 0, "RECEIVED orders must not be re-opened"


# ---------------------------------------------------------------------------
# 5. SKU canonical-format validator unit tests
# ---------------------------------------------------------------------------

class TestSkuCanonicalValidator:
    """Unit tests for src/utils/sku_validation – no I/O required."""

    # ---- validate_sku_canonical ----

    def test_canonical_7_digit_accepted(self):
        """'0450663' is canonical (leading zero, 7 digits)."""
        assert validate_sku_canonical("0450663") == "0450663"

    def test_canonical_all_zeros_accepted(self):
        """'0000000' is technically canonical (edge case)."""
        assert validate_sku_canonical("0000000") == "0000000"

    def test_canonical_no_leading_zero_accepted(self):
        """'1234567' has no leading zero but is still 7 numeric digits → canonical."""
        assert validate_sku_canonical("1234567") == "1234567"

    def test_non_canonical_6_digits_raises(self):
        """'450663' (6 digits) is non-canonical — the exact production failure case."""
        with pytest.raises(SkuFormatError) as exc_info:
            validate_sku_canonical("450663")
        assert "450663" in str(exc_info.value)

    def test_non_canonical_8_digits_raises(self):
        """'04506630' (8 digits) is non-canonical."""
        with pytest.raises(SkuFormatError):
            validate_sku_canonical("04506630")

    def test_non_canonical_alpha_raises(self):
        """'SKU_001' is not numeric — must be rejected."""
        with pytest.raises(SkuFormatError):
            validate_sku_canonical("SKU_001")

    def test_non_canonical_with_spaces_raises(self):
        """' 0450663 ' (with surrounding spaces) is non-canonical in strict mode."""
        with pytest.raises(SkuFormatError):
            validate_sku_canonical(" 0450663 ")

    def test_non_canonical_int_raises(self):
        """int 450663 is not a str — must be rejected."""
        with pytest.raises(SkuFormatError):
            validate_sku_canonical(450663)  # type: ignore[arg-type]

    def test_non_canonical_none_raises(self):
        """None is not a str — must be rejected."""
        with pytest.raises(SkuFormatError):
            validate_sku_canonical(None)  # type: ignore[arg-type]

    def test_context_included_in_error_message(self):
        """Error message must include the provided context for diagnostics."""
        with pytest.raises(SkuFormatError) as exc_info:
            validate_sku_canonical("450663", context="document DDT-20260328")
        assert "DDT-20260328" in str(exc_info.value)

    # ---- is_sku_canonical ----

    def test_is_canonical_true(self):
        assert is_sku_canonical("0450663") is True

    def test_is_canonical_false_short(self):
        assert is_sku_canonical("450663") is False

    def test_is_canonical_false_int(self):
        assert is_sku_canonical(450663) is False  # type: ignore[arg-type]

    def test_is_canonical_false_alpha(self):
        assert is_sku_canonical("ABC1234") is False


# ---------------------------------------------------------------------------
# 6. write_order_log rejects non-canonical SKUs at persistence boundary
# ---------------------------------------------------------------------------

class TestCsvLayerWriteOrderLogCanonical:
    """write_order_log must raise SkuFormatError before writing to order_logs.csv."""

    def test_canonical_sku_is_written(self, data_dir, csv_layer):
        csv_layer.write_order_log(
            order_id="ORD-001",
            date_str="2026-03-30",
            sku="0450663",
            qty=10,
            status="PENDING",
        )
        rows = csv_layer.read_order_logs()
        assert len(rows) == 1
        assert rows[0]["sku"] == "0450663"

    def test_non_canonical_int_raises_before_write(self, data_dir, csv_layer):
        with pytest.raises(SkuFormatError):
            csv_layer.write_order_log(
                order_id="ORD-002",
                date_str="2026-03-30",
                sku=450663,  # type: ignore[arg-type]  # integer — must be rejected
                qty=10,
                status="PENDING",
            )
        # Nothing must have been written
        assert csv_layer.read_order_logs() == []

    def test_non_canonical_6_digit_str_raises_before_write(self, data_dir, csv_layer):
        with pytest.raises(SkuFormatError):
            csv_layer.write_order_log(
                order_id="ORD-003",
                date_str="2026-03-30",
                sku="450663",  # 6 digits — missing leading zero
                qty=10,
                status="PENDING",
            )
        assert csv_layer.read_order_logs() == []

    def test_non_canonical_empty_raises_before_write(self, data_dir, csv_layer):
        with pytest.raises(SkuFormatError):
            csv_layer.write_order_log(
                order_id="ORD-004",
                date_str="2026-03-30",
                sku="",
                qty=10,
                status="PENDING",
            )
        assert csv_layer.read_order_logs() == []


# ---------------------------------------------------------------------------
# 7. write_receiving_log rejects non-canonical SKUs at persistence boundary
# ---------------------------------------------------------------------------

class TestCsvLayerWriteReceivingLogCanonical:
    """write_receiving_log must raise SkuFormatError before writing to receiving_logs.csv."""

    def test_canonical_sku_is_written(self, data_dir, csv_layer):
        csv_layer.write_receiving_log(
            document_id="DDT-001",
            date_str="2026-03-30",
            sku="0450663",
            qty=5,
            receipt_date="2026-03-30",
        )
        rows = csv_layer.read_receiving_logs()
        assert len(rows) == 1
        assert rows[0]["sku"] == "0450663"

    def test_non_canonical_int_raises_before_write(self, data_dir, csv_layer):
        with pytest.raises(SkuFormatError):
            csv_layer.write_receiving_log(
                document_id="DDT-002",
                date_str="2026-03-30",
                sku=450663,  # type: ignore[arg-type]
                qty=5,
                receipt_date="2026-03-30",
            )
        assert csv_layer.read_receiving_logs() == []

    def test_non_canonical_6_digit_str_raises_before_write(self, data_dir, csv_layer):
        with pytest.raises(SkuFormatError):
            csv_layer.write_receiving_log(
                document_id="DDT-003",
                date_str="2026-03-30",
                sku="450663",
                qty=5,
                receipt_date="2026-03-30",
            )
        assert csv_layer.read_receiving_logs() == []


# ---------------------------------------------------------------------------
# 8. SKUImporter._validate_row rejects non-canonical SKUs in bulk import
# ---------------------------------------------------------------------------

class TestSkuImportValidateRowCanonical:
    """_validate_row must reject rows where SKU is not in canonical 7-digit format."""

    @pytest.fixture
    def importer(self, csv_layer):
        from src.workflows.sku_import import SKUImporter
        return SKUImporter(csv_layer=csv_layer)

    def test_canonical_sku_no_error(self, importer):
        errors, _warnings = importer._validate_row(
            {"sku": "0450663", "description": "Product A"},
            existing_skus=set(),
            seen_skus_in_file=set(),
        )
        assert not any("SKU non canonico" in e for e in errors)

    def test_non_canonical_6_digit_produces_error(self, importer):
        errors, _warnings = importer._validate_row(
            {"sku": "450663", "description": "Product A"},
            existing_skus=set(),
            seen_skus_in_file=set(),
        )
        assert any("SKU non canonico" in e for e in errors)

    def test_non_canonical_8_digit_produces_error(self, importer):
        errors, _warnings = importer._validate_row(
            {"sku": "04506630", "description": "Product A"},
            existing_skus=set(),
            seen_skus_in_file=set(),
        )
        assert any("SKU non canonico" in e for e in errors)

    def test_non_canonical_alpha_produces_error(self, importer):
        errors, _warnings = importer._validate_row(
            {"sku": "SKU_001", "description": "Product A"},
            existing_skus=set(),
            seen_skus_in_file=set(),
        )
        assert any("SKU non canonico" in e for e in errors)

    def test_non_canonical_empty_produces_error(self, importer):
        # Empty SKU triggers CRITICAL_FIELDS error first, but canonical check
        # should not cause an unhandled exception
        errors, _warnings = importer._validate_row(
            {"sku": "", "description": "Product A"},
            existing_skus=set(),
            seen_skus_in_file=set(),
        )
        assert errors  # at least one error (missing critical field or non-canonical)

    def test_canonical_sku_allows_further_validation(self, importer):
        # With a canonical SKU, downstream validations (moq, pack_size, etc.) still run
        errors, _warnings = importer._validate_row(
            {"sku": "0000001", "description": "Test", "moq": "-5"},
            existing_skus=set(),
            seen_skus_in_file=set(),
        )
        assert any("moq" in e.lower() for e in errors)

