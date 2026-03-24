"""
Regression tests for the receiving pending-list refresh bug.

Scenarios covered
-----------------
1. Full receipt removes order from pending (RECEIVED status, qty_pending=0).
2. Partial receipt keeps order in pending with updated residual qty.
3. Receipt with no matching PENDING/PARTIAL orders ⟶ order_updates is empty,
   transactions are still written (generic stock-in).
4. Duplicate document (idempotent) ⟶ neither orders nor transactions change.
5. SKU passed as integer ⟶ normalised, order is found and updated correctly.
6. Status stored in lowercase in order_logs ⟶ still matched as PENDING/PARTIAL.
7. update_order_received_qty raises ValueError for unknown order_id (no silent fail).
"""
import pytest
from datetime import date
from pathlib import Path
import tempfile
import shutil

from src.persistence.csv_layer import CSVLayer
from src.workflows.receiving_v2 import ReceivingWorkflow
from src.workflows.order import OrderWorkflow
from src.domain.models import EventType, OrderProposal


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


@pytest.fixture
def order_wf(csv_layer):
    return OrderWorkflow(csv_layer, lead_time_days=3)


@pytest.fixture
def recv_wf(csv_layer):
    return ReceivingWorkflow(csv_layer)


def _make_proposal(sku: str, qty: int, today: date) -> OrderProposal:
    return OrderProposal(
        sku=sku,
        description=f"Desc {sku}",
        current_on_hand=0,
        current_on_order=0,
        daily_sales_avg=1.0,
        proposed_qty=qty,
        receipt_date=today,
    )


def _pending_orders(csv_layer: CSVLayer) -> list:
    """Return order_logs rows that are still PENDING or PARTIAL."""
    return [
        o for o in csv_layer.read_order_logs()
        if o.get("status", "PENDING").upper().strip() in ("PENDING", "PARTIAL")
    ]


# ---------------------------------------------------------------------------
# Test 1 — full receipt removes order from pending
# ---------------------------------------------------------------------------

class TestFullReceiptClearsPending:
    def test_order_status_becomes_received(self, csv_layer, order_wf, recv_wf):
        today = date.today()
        confirmations, _ = order_wf.confirm_order([_make_proposal("SKU-A", 50, today)])
        order_id = confirmations[0].order_id

        assert len(_pending_orders(csv_layer)) == 1

        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-001", today, [{"sku": "SKU-A", "qty_received": 50}]
        )

        assert not skip
        assert len(txns) == 1
        assert txns[0].event == EventType.RECEIPT
        assert order_id in updates
        assert updates[order_id]["new_status"] == "RECEIVED"

        # Pending list must now be empty
        assert len(_pending_orders(csv_layer)) == 0

    def test_receipt_transaction_is_persisted(self, csv_layer, order_wf, recv_wf):
        today = date.today()
        order_wf.confirm_order([_make_proposal("SKU-B", 20, today)])
        recv_wf.close_receipt_by_document("DDT-002", today, [{"sku": "SKU-B", "qty_received": 20}])

        all_txns = csv_layer.read_transactions()
        receipts = [t for t in all_txns if t.sku == "SKU-B" and t.event == EventType.RECEIPT]
        assert len(receipts) == 1
        assert receipts[0].qty == 20


# ---------------------------------------------------------------------------
# Test 2 — partial receipt keeps order in pending with updated residual
# ---------------------------------------------------------------------------

class TestPartialReceiptUpdatesPending:
    def test_partial_status_and_residual(self, csv_layer, order_wf, recv_wf):
        today = date.today()
        confirmations, _ = order_wf.confirm_order([_make_proposal("SKU-C", 30, today)])
        order_id = confirmations[0].order_id

        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-003", today, [{"sku": "SKU-C", "qty_received": 10}]
        )

        assert not skip
        assert order_id in updates
        assert updates[order_id]["new_status"] == "PARTIAL"
        assert updates[order_id]["qty_received_total"] == 10

        pending = _pending_orders(csv_layer)
        assert len(pending) == 1
        assert pending[0]["order_id"] == order_id
        assert int(pending[0]["qty_received"]) == 10
        assert int(pending[0]["qty_ordered"]) == 30


# ---------------------------------------------------------------------------
# Test 3 — no matching orders ⟶ generic stock-in, order_updates empty
# ---------------------------------------------------------------------------

class TestNoMatchingOrdersGenericReceipt:
    def test_order_updates_empty_transactions_written(self, csv_layer, recv_wf):
        today = date.today()
        # No orders placed for SKU-X
        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-004", today, [{"sku": "SKU-X", "qty_received": 5}]
        )

        assert not skip
        # order_updates must be empty (no orders to allocate to)
        assert updates == {}
        # But a RECEIPT transaction must still be written (generic stock-in)
        assert len(txns) == 1
        assert txns[0].event == EventType.RECEIPT
        assert txns[0].qty == 5

        persisted = csv_layer.read_transactions()
        assert any(t.sku == "SKU-X" and t.event == EventType.RECEIPT for t in persisted)


# ---------------------------------------------------------------------------
# Test 4 — duplicate document is idempotent
# ---------------------------------------------------------------------------

class TestIdempotentDocument:
    def test_duplicate_document_no_side_effects(self, csv_layer, order_wf, recv_wf):
        today = date.today()
        order_wf.confirm_order([_make_proposal("SKU-D", 40, today)])

        recv_wf.close_receipt_by_document("DDT-005", today, [{"sku": "SKU-D", "qty_received": 40}])

        txns_before = csv_layer.read_transactions()
        logs_before = csv_layer.read_order_logs()

        # Second call with same document_id
        txns2, skip2, updates2 = recv_wf.close_receipt_by_document(
            "DDT-005", today, [{"sku": "SKU-D", "qty_received": 40}]
        )

        assert skip2 is True
        assert txns2 == []
        assert updates2 == {}

        # Nothing changed in storage
        assert len(csv_layer.read_transactions()) == len(txns_before)
        assert csv_layer.read_order_logs() == logs_before


# ---------------------------------------------------------------------------
# Test 5 — SKU passed as integer is normalised
# ---------------------------------------------------------------------------

class TestSkuIntegerNormalization:
    def test_int_sku_matches_string_order(self, csv_layer, order_wf, recv_wf):
        today = date.today()
        # Order placed with string SKU "450633"
        confirmations, _ = order_wf.confirm_order([_make_proposal("450633", 12, today)])
        order_id = confirmations[0].order_id

        # Receipt submitted with integer 450633 (simulating external caller)
        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-006", today, [{"sku": 450633, "qty_received": 12}]
        )

        assert not skip
        assert order_id in updates, (
            "Integer SKU not normalised to string — order not matched"
        )
        assert updates[order_id]["new_status"] == "RECEIVED"
        assert len(_pending_orders(csv_layer)) == 0


# ---------------------------------------------------------------------------
# Test 6 — lowercase/mixed-case status in order_logs is matched
# ---------------------------------------------------------------------------

class TestLegacyStatusCaseInsensitive:
    def test_lowercase_pending_is_matched(self, csv_layer, order_wf, recv_wf):
        today = date.today()
        confirmations, _ = order_wf.confirm_order([_make_proposal("SKU-E", 8, today)])
        order_id = confirmations[0].order_id

        # Forcibly overwrite the status to lowercase (legacy data)
        orders = csv_layer.read_order_logs()
        for o in orders:
            if o["order_id"] == order_id:
                o["status"] = "pending"
        csv_layer._write_csv("order_logs.csv", orders)

        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-007", today, [{"sku": "SKU-E", "qty_received": 8}]
        )

        assert not skip
        assert order_id in updates, (
            "Lowercase 'pending' status not matched — order invisibile to receiving"
        )
        assert updates[order_id]["new_status"] == "RECEIVED"


# ---------------------------------------------------------------------------
# Test 7 — update_order_received_qty raises on unknown order_id
# ---------------------------------------------------------------------------

class TestUpdateOrderRaisesOnMissing:
    def test_unknown_order_id_raises_value_error(self, csv_layer):
        with pytest.raises(ValueError, match="not found in order_logs.csv"):
            csv_layer.update_order_received_qty(
                order_id="NONEXISTENT-999",
                qty_received=10,
                status="RECEIVED",
            )
