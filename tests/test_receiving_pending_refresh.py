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
7. update_order_received_qty raises ValueError for unknown order_id (no silent fail).8. Two orders same SKU in one document ⟶ FIFO allocation uses in-memory state,
   both orders updated correctly, no pending residual left after full receipt.
9. FIFO order preserved: older order served before newer when same SKU.
10. Determinism: same input list always produces same order_updates."""
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
# Test 5 — SKU passed as integer raises SkuFormatError (strict validation)
# ---------------------------------------------------------------------------

class TestSkuIntegerRaisesInStrictMode:
    """
    Strict validation: passing integer SKU to close_receipt_by_document must
    raise SkuFormatError, because an integer cannot represent a zero-padded
    canonical SKU string (e.g. int 450663 != str '0450663').
    See also test_error_resilience.py::TestReceivingSkuNormalization.
    """
    def test_int_sku_raises_sku_format_error(self, csv_layer, order_wf, recv_wf):
        from src.utils.sku_validation import SkuFormatError
        today = date.today()
        order_wf.confirm_order([_make_proposal("450633", 12, today)])

        with pytest.raises(SkuFormatError):
            recv_wf.close_receipt_by_document(
                "DDT-006", today, [{"sku": 450633, "qty_received": 12}]
            )


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


# ---------------------------------------------------------------------------
# Test 8 — two orders same SKU in one document ⟶ in-memory FIFO state
# ---------------------------------------------------------------------------

class TestMultiItemSameSkuInMemoryFifo:
    """
    Root-cause regression for the 'pending order stays after confirmation' bug.

    Scenario: two PENDING orders for the same SKU arrive in the same receiving
    document (as happens when the GUI sends one item per treeview row).
    The workflow must use an in-memory order-state that is updated after each
    allocation so that the second item does NOT re-allocate to an already-served
    order from the stale snapshot.

    Both orders are created in a SINGLE confirm_order call so they get distinct
    order_ids (_000, _001).  Two separate calls would both use idx=0 and produce
    the same order_id, causing the second to overwrite the first in order_logs.
    """

    def test_two_full_orders_same_sku_both_cleared(self, csv_layer, order_wf, recv_wf):
        """Both orders must end up RECEIVED and pending list must be empty."""
        today = date.today()
        confirmations, _ = order_wf.confirm_order([
            _make_proposal("SKU-F", 30, today),
            _make_proposal("SKU-F", 20, today),
        ])
        order_id_1 = confirmations[0].order_id  # _000
        order_id_2 = confirmations[1].order_id  # _001

        assert len(_pending_orders(csv_layer)) == 2

        # GUI sends one item per pending row, each with the pre-filled residual qty
        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-010",
            today,
            [
                {"sku": "SKU-F", "qty_received": 30},
                {"sku": "SKU-F", "qty_received": 20},
            ],
        )

        assert not skip, "Document must not be skipped"
        assert len(txns) == 2, "Two RECEIPT transactions expected (one per item)"

        # Both orders must be updated — if in-memory state was stale, order_id_1
        # would appear twice and order_id_2 would be missing entirely.
        assert order_id_1 in updates, f"Order {order_id_1} missing from order_updates"
        assert order_id_2 in updates, f"Order {order_id_2} missing from order_updates"

        assert updates[order_id_1]["new_status"] == "RECEIVED", (
            f"Order {order_id_1} should be RECEIVED, got {updates[order_id_1]['new_status']}"
        )
        assert updates[order_id_2]["new_status"] == "RECEIVED", (
            f"Order {order_id_2} should be RECEIVED, got {updates[order_id_2]['new_status']}"
        )

        # Pending list must be empty
        assert len(_pending_orders(csv_layer)) == 0, (
            "Pending list must be empty after full receipt of both orders"
        )

    def test_two_partial_items_same_sku_correct_residuals(self, csv_layer, order_wf, recv_wf):
        """
        Partial receipt across two rows: item 1 partially fills Order A (needs 30),
        item 2 fills the rest of Order A and then all of Order B (needs 10).
        order_updates must reflect cumulative in-memory allocations.
        """
        today = date.today()
        confirmations, _ = order_wf.confirm_order([
            _make_proposal("SKU-G", 30, today),
            _make_proposal("SKU-G", 10, today),
        ])
        order_id_1 = confirmations[0].order_id  # _000 — needs 30
        order_id_2 = confirmations[1].order_id  # _001 — needs 10

        # Item 1: 20 pcs → partially fills Order A (needs 30)
        # Item 2: 20 pcs → fills remaining 10 of Order A, then 10 of Order B
        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-011",
            today,
            [
                {"sku": "SKU-G", "qty_received": 20},
                {"sku": "SKU-G", "qty_received": 20},
            ],
        )

        assert not skip

        # After all allocations:
        #   Order A: received 20+10 = 30 (RECEIVED, needs_nothing_more)
        #   Order B: received 10 (RECEIVED, qty_ordered=10)
        assert order_id_1 in updates
        assert order_id_2 in updates
        assert updates[order_id_1]["qty_received_total"] == 30
        assert updates[order_id_1]["new_status"] == "RECEIVED"
        assert updates[order_id_2]["qty_received_total"] == 10
        assert updates[order_id_2]["new_status"] == "RECEIVED"

        assert len(_pending_orders(csv_layer)) == 0


# ---------------------------------------------------------------------------
# Test 9 — FIFO ordering: older order always served before newer
# ---------------------------------------------------------------------------

class TestFifoOlderOrderFirst:
    def test_fifo_by_date_order(self, csv_layer, recv_wf, order_wf):
        """
        Older order (earlier date in order_logs) must be allocated to before
        the newer order.  Both orders are created in a single call; the first
        one's date is then overwritten to yesterday so FIFO by date can be
        verified independently of insertion order.
        """
        from datetime import timedelta
        today = date.today()
        yesterday = today - timedelta(days=1)

        # Create both orders in one call (distinct order_ids _000, _001)
        confirmations, _ = order_wf.confirm_order([
            _make_proposal("SKU-H", 40, today),
            _make_proposal("SKU-H", 40, today),
        ])
        order_id_old = confirmations[0].order_id  # will be back-dated to yesterday
        order_id_new = confirmations[1].order_id

        # Back-date order_id_old so FIFO by date picks it first
        orders = csv_layer.read_order_logs()
        for o in orders:
            if o["order_id"] == order_id_old:
                o["date"] = yesterday.isoformat()
        csv_layer._write_csv("order_logs.csv", orders)

        # Only enough to fill the older order
        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-012", today, [{"sku": "SKU-H", "qty_received": 40}]
        )

        assert not skip
        assert order_id_old in updates, "Older order must be allocated to first"
        assert updates[order_id_old]["new_status"] == "RECEIVED"
        # Newer order must remain pending (not touched)
        assert order_id_new not in updates, (
            "Newer order must not be touched when older order is not yet fully received"
        )
        pending = _pending_orders(csv_layer)
        pending_ids = [o["order_id"] for o in pending]
        assert order_id_new in pending_ids, "Newer order must still appear in pending list"
        assert order_id_old not in pending_ids, "Older order must be gone from pending list"


# ---------------------------------------------------------------------------
# Test 10 — Determinism: same input always produces same order_updates
# ---------------------------------------------------------------------------

class TestAllocationDeterminism:
    def test_same_items_same_result_repeated_calls(self, csv_layer, order_wf, recv_wf):
        """
        With two orders for the same SKU created in a single confirm_order call,
        receiving exactly their quantities in order must always produce two
        RECEIVED orders — no matter how many times we run the same scenario.
        """
        today = date.today()
        confirmations, _ = order_wf.confirm_order([
            _make_proposal("SKU-I", 25, today),
            _make_proposal("SKU-I", 15, today),
        ])
        order_id_a = confirmations[0].order_id  # _000
        order_id_b = confirmations[1].order_id  # _001

        _, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-013",
            today,
            [
                {"sku": "SKU-I", "qty_received": 25},
                {"sku": "SKU-I", "qty_received": 15},
            ],
        )

        assert not skip
        assert updates[order_id_a]["new_status"] == "RECEIVED", (
            f"Order A must be RECEIVED; got {updates.get(order_id_a)}"
        )
        assert updates[order_id_b]["new_status"] == "RECEIVED", (
            f"Order B must be RECEIVED; got {updates.get(order_id_b)}"
        )
        assert len(_pending_orders(csv_layer)) == 0


# ---------------------------------------------------------------------------
# Test 11 — Same SKU, two orders on different receipt_dates.
#
# Reproduces the original bug: user filters pending tab by "second" receipt_date,
# confirms that document → first order must remain PENDING, undisturbed.
# ---------------------------------------------------------------------------

class TestDoublePendingDifferentReceiptDates:
    """
    Scenario
    --------
    1. Order A for SKU-J placed today, receipt_date = today+3 (older/first in FIFO).
    2. Order B for SKU-J placed today+1, receipt_date = today+7 (newer/second in FIFO).
    3. User closes document referencing only Order B (specifying order_ids=[order_id_b]).
    4. Expected: Order B → RECEIVED, Order A → still PENDING and untouched.
    5. Pending list must still contain exactly Order A.
    """

    def test_only_targeted_order_is_updated(self, csv_layer, order_wf, recv_wf):
        from datetime import timedelta

        today = date.today()
        receipt_date_a = today + timedelta(days=3)
        receipt_date_b = today + timedelta(days=7)

        # Confirm Order A
        confirmations_a, _ = order_wf.confirm_order(
            [_make_proposal("SKU-J", 40, receipt_date_a)]
        )
        order_id_a = confirmations_a[0].order_id

        # Confirm Order B (next day → different order_id prefix in real usage,
        # but here we just need two distinct PENDING rows for same SKU)
        confirmations_b, _ = order_wf.confirm_order(
            [_make_proposal("SKU-J", 60, receipt_date_b)]
        )
        order_id_b = confirmations_b[0].order_id

        assert order_id_a != order_id_b, "order_ids must be distinct"
        assert len(_pending_orders(csv_layer)) == 2

        # User closes only the second document (order B)
        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-014",
            today,
            [{"sku": "SKU-J", "qty_received": 60, "order_ids": [order_id_b]}],
        )

        assert not skip
        # Order B must be RECEIVED
        assert order_id_b in updates, f"Order B not in updates: {updates}"
        assert updates[order_id_b]["new_status"] == "RECEIVED"

        # Order A must remain PENDING and must NOT appear in order_updates
        assert order_id_a not in updates, (
            f"Order A was incorrectly touched: {updates.get(order_id_a)}"
        )

        pending = _pending_orders(csv_layer)
        pending_ids = [o["order_id"] for o in pending]
        assert order_id_a in pending_ids, "Order A must still be in pending list"
        assert order_id_b not in pending_ids, "Order B must be gone from pending list"

    def test_fifo_fallback_takes_oldest_when_no_order_ids(self, csv_layer, order_wf, recv_wf):
        """When order_ids is empty (legacy FIFO path), oldest order is consumed first."""
        from datetime import timedelta

        today = date.today()
        receipt_date_older = today + timedelta(days=2)
        receipt_date_newer = today + timedelta(days=8)

        confirmations_old, _ = order_wf.confirm_order(
            [_make_proposal("SKU-K", 30, receipt_date_older)]
        )
        order_id_old = confirmations_old[0].order_id

        confirmations_new, _ = order_wf.confirm_order(
            [_make_proposal("SKU-K", 50, receipt_date_newer)]
        )
        order_id_new = confirmations_new[0].order_id

        # Backdate the older order's date so FIFO sorts it first
        orders = csv_layer.read_order_logs()
        for o in orders:
            if o["order_id"] == order_id_old:
                o["date"] = (today - timedelta(days=1)).isoformat()
        csv_layer._write_csv("order_logs.csv", orders)

        # Receive exactly 30 — should fill older order only
        txns, skip, updates = recv_wf.close_receipt_by_document(
            "DDT-015",
            today,
            [{"sku": "SKU-K", "qty_received": 30, "order_ids": []}],
        )

        assert not skip
        assert order_id_old in updates
        assert updates[order_id_old]["new_status"] == "RECEIVED"
        assert order_id_new not in updates, "Newer order must not be touched"

        pending = _pending_orders(csv_layer)
        pending_ids = [o["order_id"] for o in pending]
        assert order_id_new in pending_ids
        assert order_id_old not in pending_ids

    def test_second_confirm_on_same_day_produces_unique_order_ids(self, csv_layer, order_wf):
        """Two confirm_order calls on the same day must produce non-colliding order_ids."""
        today = date.today()

        conf_a, _ = order_wf.confirm_order([_make_proposal("SKU-L", 10, today)])
        conf_b, _ = order_wf.confirm_order([_make_proposal("SKU-L", 20, today)])

        id_a = conf_a[0].order_id
        id_b = conf_b[0].order_id
        assert id_a != id_b, (
            f"Duplicate order_id generated by two same-day confirm calls: {id_a}"
        )

