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

    def test_receiving_with_int_sku_matches_orders(self, data_dir, csv_layer):
        """
        Regression: SKU stored as integer ('450633') must match order logs
        even when passed as int to close_receipt_by_document.
        Previously produced false 'No PENDING/PARTIAL orders found'.
        """
        self._setup_sku_and_order(data_dir, csv_layer, "450633")
        workflow = ReceivingWorkflow(csv_layer)

        txns, already_processed, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-20260324",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": 450633, "qty_received": 30}],  # int sku
        )

        assert not already_processed
        assert len(txns) == 1
        assert txns[0].event == EventType.RECEIPT
        assert txns[0].qty == 30
        assert len(order_updates) == 1, (
            "Order must be found and updated even when sku is passed as int"
        )

    def test_receiving_with_str_sku_matches_orders(self, data_dir, csv_layer):
        """Normal case: str sku still works correctly after normalization."""
        self._setup_sku_and_order(data_dir, csv_layer, "450634")
        workflow = ReceivingWorkflow(csv_layer)

        txns, already_processed, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-20260324-B",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "450634", "qty_received": 6}],
        )

        assert not already_processed
        assert len(txns) == 1
        assert txns[0].qty == 6
        assert len(order_updates) == 1


# ---------------------------------------------------------------------------
# 3. Receiving workflow: case-insensitive order status matching
# ---------------------------------------------------------------------------

class TestReceivingStatusNormalization:

    def test_lowercase_pending_status_matched(self, data_dir, csv_layer):
        """
        Orders with status='pending' (lowercase) must be found by receiving.
        """
        _write_raw_skus_csv(data_dir, [
            {"sku": "SKU_LOW", "description": "SKU lowercase status"},
        ])
        _write_raw_order_logs(data_dir, [
            {"order_id": "ORD_LOW_1", "date": "2026-03-20",
             "sku": "SKU_LOW", "qty_ordered": "10", "qty_received": "0",
             "status": "pending"},  # lowercase — non-standard
        ])

        workflow = ReceivingWorkflow(csv_layer)
        txns, _, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-LOW",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "SKU_LOW", "qty_received": 10}],
        )

        assert len(txns) == 1
        assert len(order_updates) == 1, "lowercase 'pending' status must match"

    def test_partial_lowercase_status_matched(self, data_dir, csv_layer):
        """Orders with status='partial' (lowercase) must be found."""
        _write_raw_skus_csv(data_dir, [
            {"sku": "SKU_PART", "description": "SKU partial lower"},
        ])
        _write_raw_order_logs(data_dir, [
            {"order_id": "ORD_PART_1", "date": "2026-03-20",
             "sku": "SKU_PART", "qty_ordered": "20", "qty_received": "5",
             "status": "partial"},  # lowercase
        ])

        workflow = ReceivingWorkflow(csv_layer)
        txns, _, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-PART",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "SKU_PART", "qty_received": 15}],
        )

        assert len(txns) == 1
        assert len(order_updates) == 1, "lowercase 'partial' status must match"

    def test_received_status_not_matched(self, data_dir, csv_layer):
        """
        Orders with status='RECEIVED' must NOT be matched (already completed).
        A new RECEIPT event without order linkage is still created.
        """
        _write_raw_skus_csv(data_dir, [
            {"sku": "SKU_DONE", "description": "SKU already received"},
        ])
        _write_raw_order_logs(data_dir, [
            {"order_id": "ORD_DONE_1", "date": "2026-03-10",
             "sku": "SKU_DONE", "qty_ordered": "50", "qty_received": "50",
             "status": "RECEIVED"},
        ])

        workflow = ReceivingWorkflow(csv_layer)
        txns, _, order_updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-DONE",
            receipt_date=date(2026, 3, 24),
            items=[{"sku": "SKU_DONE", "qty_received": 10}],
        )

        # RECEIPT event is still created (manual stock-in), but no order is updated
        assert len(txns) == 1
        assert txns[0].event == EventType.RECEIPT
        assert len(order_updates) == 0, "RECEIVED orders must not be re-opened"
