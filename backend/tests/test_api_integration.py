"""
backend/tests/test_api_integration.py — Integration tests via TestClient.

Each test uses a fresh TestClient (function-scoped fixtures) with:
  - In-memory _MemStorage (seed SKUs, empty ledger)
  - Temp SQLite DB (idempotency table only)
  - Auth bypassed via dependency override

Tests are grouped by endpoint:
  TestGetSkusByEan    — GET /api/v1/skus/by-ean/{ean}
  TestGetStock        — GET /api/v1/stock/{sku}
  TestPostExceptions  — POST /api/v1/exceptions
  TestPostReceiptsClose — POST /api/v1/receipts/close
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import SEED_EAN_EXPIRY, SEED_EAN_PLAIN, EAN_UNKNOWN

# Base URL prefix for all versioned endpoints
_V1 = "/api/v1"


# ===========================================================================
# GET /api/v1/skus/by-ean/{ean}
# ===========================================================================


class TestGetSkusByEan:
    """EAN lookup: 200 hit, 400 invalid format, 404 valid but no match."""

    def test_200_found(self, client: TestClient) -> None:
        """Valid EAN-13 that matches a seeded SKU → 200 with correct sku."""
        r = client.get(f"{_V1}/skus/by-ean/{SEED_EAN_PLAIN}")
        assert r.status_code == 200
        body = r.json()
        assert body["sku"] == "PRD-001"
        assert body["ean"] == SEED_EAN_PLAIN
        assert body["ean_valid"] is True

    def test_200_expiry_sku(self, client: TestClient) -> None:
        """EAN matching a has_expiry_label SKU → 200."""
        r = client.get(f"{_V1}/skus/by-ean/{SEED_EAN_EXPIRY}")
        assert r.status_code == 200
        assert r.json()["sku"] == "PRD-EXP"

    def test_400_non_digit_ean(self, client: TestClient) -> None:
        """EAN with letters → 400 BAD_REQUEST with EAN-related message."""
        r = client.get(f"{_V1}/skus/by-ean/ABC123")
        assert r.status_code == 400
        err = r.json()["error"]
        assert err["code"] == "BAD_REQUEST"
        assert "ean" in err["message"].lower()

    def test_400_too_short_ean(self, client: TestClient) -> None:
        """EAN with only 5 digits → 400 (wrong length)."""
        r = client.get(f"{_V1}/skus/by-ean/12345")
        assert r.status_code == 400

    def test_404_no_match(self, client: TestClient) -> None:
        """Valid EAN-13 not in catalogue → 404 NOT_FOUND."""
        r = client.get(f"{_V1}/skus/by-ean/{EAN_UNKNOWN}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"


# ===========================================================================
# GET /api/v1/stock/{sku}
# ===========================================================================


class TestGetStock:
    """Stock AsOf: 200 for known SKU (zero stock), 404 for unknown."""

    def test_200_known_sku_zero_stock(self, client: TestClient) -> None:
        """Known SKU with no transactions → 200, on_hand=0, on_order=0."""
        r = client.get(f"{_V1}/stock/PRD-001")
        assert r.status_code == 200
        body = r.json()
        assert body["sku"] == "PRD-001"
        assert body["on_hand"] == 0
        assert body["on_order"] == 0
        assert "mode" in body
        assert "asof" in body

    def test_200_default_mode_is_point_in_time(self, client: TestClient) -> None:
        """Default mode → POINT_IN_TIME."""
        r = client.get(f"{_V1}/stock/PRD-001")
        assert r.status_code == 200
        assert r.json()["mode"] == "POINT_IN_TIME"

    def test_200_end_of_day_mode(self, client: TestClient) -> None:
        """Explicit mode=END_OF_DAY round-trips in response."""
        r = client.get(f"{_V1}/stock/PRD-001?mode=END_OF_DAY")
        assert r.status_code == 200
        assert r.json()["mode"] == "END_OF_DAY"

    def test_404_unknown_sku(self, client: TestClient) -> None:
        """Unknown SKU → 404 NOT_FOUND."""
        r = client.get(f"{_V1}/stock/GHOST-SKU")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"

    def test_200_list_stock(self, client: TestClient) -> None:
        """GET /stock (list, no filter) → 200 with items for all seed SKUs."""
        r = client.get(f"{_V1}/stock")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        skus_returned = {item["sku"] for item in body["items"]}
        assert "PRD-001" in skus_returned


# ===========================================================================
# POST /api/v1/exceptions
# ===========================================================================

_BASE_EXCEPTION = {
    "date": "2026-02-25",
    "sku": "PRD-001",
    "event": "WASTE",
    "qty": 3,
    "note": "damaged packaging",
}


class TestPostExceptions:
    """201 first write, 200 UUID replay, no 409 when client_event_id absent."""

    def test_201_new_event(self, client: TestClient) -> None:
        """First submission → 201 Created, already_recorded=False."""
        r = client.post(f"{_V1}/exceptions", json=_BASE_EXCEPTION)
        assert r.status_code == 201
        body = r.json()
        assert body["already_recorded"] is False
        assert body["sku"] == "PRD-001"
        assert body["event"] == "WASTE"
        assert body["qty"] == 3

    def test_201_stores_transaction_in_ledger(
        self, client: TestClient, mem_storage
    ) -> None:
        """After 201, the transaction should appear in the in-memory ledger."""
        client.post(f"{_V1}/exceptions", json=_BASE_EXCEPTION)
        assert len(mem_storage._transactions) == 1
        txn = mem_storage._transactions[0]
        assert txn.sku == "PRD-001"

    # -- UUID idempotency (client_event_id) -----------------------------------

    def test_200_client_event_id_replay(self, client: TestClient) -> None:
        """Same client_event_id sent twice → second call 200 already_recorded=True."""
        payload = {**_BASE_EXCEPTION, "client_event_id": "uuid-test-exceptions-1"}

        r1 = client.post(f"{_V1}/exceptions", json=payload)
        assert r1.status_code == 201

        r2 = client.post(f"{_V1}/exceptions", json=payload)
        assert r2.status_code == 200
        assert r2.json()["already_recorded"] is True

    def test_uuid_replay_does_not_write_twice(
        self, client: TestClient, mem_storage
    ) -> None:
        """UUID replay must NOT write a second transaction to the ledger."""
        payload = {**_BASE_EXCEPTION, "client_event_id": "uuid-test-exceptions-2"}

        client.post(f"{_V1}/exceptions", json=payload)   # first write
        client.post(f"{_V1}/exceptions", json=payload)   # replay

        # Only one transaction must exist
        assert len(mem_storage._transactions) == 1

    # -- No legacy idempotency (date + sku + event) --------------------------

    def test_201_allows_same_day_duplicate_without_client_event_id(
        self, client: TestClient, mem_storage
    ) -> None:
        """Two identical events (same date+sku+event), no client_event_id → both 201."""
        payload = {k: v for k, v in _BASE_EXCEPTION.items() if k != "note"}

        r1 = client.post(f"{_V1}/exceptions", json=payload)
        assert r1.status_code == 201

        r2 = client.post(f"{_V1}/exceptions", json=payload)
        assert r2.status_code == 201
        assert r2.json()["already_recorded"] is False

        # Both transactions must be in the ledger
        assert len(mem_storage._transactions) == 2

    def test_201_allows_multiple_different_events_same_day(
        self, client: TestClient, mem_storage
    ) -> None:
        """WASTE then ADJUST on same day+sku, no client_event_id → both 201."""
        waste = {**_BASE_EXCEPTION, "event": "WASTE"}
        adjust = {**_BASE_EXCEPTION, "event": "ADJUST"}

        r1 = client.post(f"{_V1}/exceptions", json=waste)
        assert r1.status_code == 201

        r2 = client.post(f"{_V1}/exceptions", json=adjust)
        assert r2.status_code == 201

        assert len(mem_storage._transactions) == 2

    # -- Validation ------------------------------------------------------------

    def test_404_unknown_sku(self, client: TestClient) -> None:
        """Unknown SKU → 404 NOT_FOUND."""
        r = client.post(
            f"{_V1}/exceptions",
            json={**_BASE_EXCEPTION, "sku": "GHOST"},
        )
        assert r.status_code == 404

    def test_400_invalid_event(self, client: TestClient) -> None:
        """Invalid event type → 422 Unprocessable (Pydantic literal validation)."""
        r = client.post(
            f"{_V1}/exceptions",
            json={**_BASE_EXCEPTION, "event": "SUPEREVENT"},
        )
        # Pydantic rejects the literal; FastAPI returns 422
        assert r.status_code == 422


# ===========================================================================
# POST /api/v1/receipts/close
# ===========================================================================

_BASE_RECEIPT = {
    "receipt_id": "REC-2026-001",
    "receipt_date": "2026-02-25",
    "lines": [
        {"sku": "PRD-001", "qty_received": 12, "note": "box-A"},
    ],
}


class TestPostReceiptsClose:
    """201 first write, 400 validation errors, 200 UUID replay, 200 legacy replay."""

    # -- Happy path ------------------------------------------------------------

    def test_201_new_receipt(self, client: TestClient) -> None:
        """Valid receipt → 201, already_posted=False, status='ok'."""
        r = client.post(f"{_V1}/receipts/close", json=_BASE_RECEIPT)
        assert r.status_code == 201
        body = r.json()
        assert body["already_posted"] is False
        assert body["receipt_id"] == "REC-2026-001"
        assert body["lines"][0]["status"] == "ok"
        assert body["lines"][0]["sku"] == "PRD-001"
        assert body["lines"][0]["qty_received"] == 12

    def test_201_writes_transaction(
        self, client: TestClient, mem_storage
    ) -> None:
        """After 201, one RECEIPT transaction must be in the ledger."""
        client.post(f"{_V1}/receipts/close", json=_BASE_RECEIPT)
        assert len(mem_storage._transactions) == 1
        txn = mem_storage._transactions[0]
        assert txn.sku == "PRD-001"
        assert txn.qty == 12

    def test_201_ean_resolved_to_sku(self, client: TestClient) -> None:
        """Line with ean instead of sku → server resolves + echoes both."""
        payload = {
            "receipt_id": "REC-EAN-001",
            "receipt_date": "2026-02-25",
            "lines": [{"ean": SEED_EAN_PLAIN, "qty_received": 6}],
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 201
        line = r.json()["lines"][0]
        assert line["sku"] == "PRD-001"
        assert line["ean"] == SEED_EAN_PLAIN

    def test_201_qty_zero_gives_skipped(self, client: TestClient) -> None:
        """qty_received=0 → status='skipped', no RECEIPT event written."""
        payload = {
            "receipt_id": "REC-ZERO",
            "receipt_date": "2026-02-25",
            "lines": [{"sku": "PRD-001", "qty_received": 0}],
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 201
        assert r.json()["lines"][0]["status"] == "skipped"

    def test_201_qty_zero_no_transaction(
        self, client: TestClient, mem_storage
    ) -> None:
        """qty_received=0 → no Transaction object in ledger."""
        payload = {
            "receipt_id": "REC-ZERO-2",
            "receipt_date": "2026-02-25",
            "lines": [{"sku": "PRD-001", "qty_received": 0}],
        }
        client.post(f"{_V1}/receipts/close", json=payload)
        assert len(mem_storage._transactions) == 0

    def test_201_expiry_sku_with_expiry_date(self, client: TestClient) -> None:
        """SKU with has_expiry_label=True + expiry_date provided → 201."""
        payload = {
            "receipt_id": "REC-EXP-001",
            "receipt_date": "2026-02-25",
            "lines": [
                {
                    "sku": "PRD-EXP",
                    "qty_received": 4,
                    "expiry_date": "2026-08-01",
                }
            ],
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 201
        assert r.json()["lines"][0]["expiry_date"] == "2026-08-01"

    # -- Validation errors (atomic: nothing written on 400) --------------------

    def test_400_unknown_sku(self, client: TestClient, mem_storage) -> None:
        """Unknown SKU → 400 with per-line error, ledger untouched."""
        payload = {
            "receipt_id": "REC-BAD-001",
            "receipt_date": "2026-02-25",
            "lines": [{"sku": "GHOST", "qty_received": 5}],
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 400
        details = r.json()["error"]["details"]
        assert any("lines[0].sku" in d["field"] for d in details)
        assert len(mem_storage._transactions) == 0

    def test_400_missing_expiry_date(self, client: TestClient, mem_storage) -> None:
        """SKU requires expiry but none given → 400 on lines[0].expiry_date."""
        payload = {
            "receipt_id": "REC-BAD-002",
            "receipt_date": "2026-02-25",
            "lines": [{"sku": "PRD-EXP", "qty_received": 3}],  # no expiry_date
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 400
        details = r.json()["error"]["details"]
        assert any("expiry_date" in d["field"] for d in details)
        assert len(mem_storage._transactions) == 0

    def test_400_invalid_ean_format(self, client: TestClient) -> None:
        """EAN with letters → 400 on lines[0].ean."""
        payload = {
            "receipt_id": "REC-BAD-003",
            "receipt_date": "2026-02-25",
            "lines": [{"ean": "BADEAN", "qty_received": 1}],
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 400
        details = r.json()["error"]["details"]
        assert any("lines[0].ean" in d["field"] for d in details)

    def test_400_multi_line_collects_all_errors(
        self, client: TestClient
    ) -> None:
        """Multiple bad lines → all errors collected atomically, 400 returned."""
        payload = {
            "receipt_id": "REC-BAD-004",
            "receipt_date": "2026-02-25",
            "lines": [
                {"sku": "GHOST1", "qty_received": 1},           # unknown
                {"sku": "PRD-EXP", "qty_received": 2},          # missing expiry
                {"ean": "NOTDIGITS", "qty_received": 1},         # bad EAN
            ],
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 400
        details = r.json()["error"]["details"]
        assert len(details) >= 3
        fields = [d["field"] for d in details]
        assert any("lines[0]" in f for f in fields)
        assert any("lines[1]" in f for f in fields)
        assert any("lines[2]" in f for f in fields)

    def test_400_no_sku_and_no_ean(self, client: TestClient) -> None:
        """Line with neither sku nor ean → 400."""
        payload = {
            "receipt_id": "REC-BAD-005",
            "receipt_date": "2026-02-25",
            "lines": [{"qty_received": 5}],
        }
        r = client.post(f"{_V1}/receipts/close", json=payload)
        assert r.status_code == 400

    # -- UUID idempotency (client_receipt_id) ---------------------------------

    def test_200_client_receipt_id_replay(self, client: TestClient) -> None:
        """Same client_receipt_id sent twice → second call 200 already_posted=True."""
        payload = {**_BASE_RECEIPT, "client_receipt_id": "uuid-receipt-idem-1"}

        r1 = client.post(f"{_V1}/receipts/close", json=payload)
        assert r1.status_code == 201

        r2 = client.post(f"{_V1}/receipts/close", json=payload)
        assert r2.status_code == 200
        assert r2.json()["already_posted"] is True

    def test_uuid_replay_does_not_write_twice(
        self, client: TestClient, mem_storage
    ) -> None:
        """UUID replay must NOT append more transactions."""
        payload = {**_BASE_RECEIPT, "client_receipt_id": "uuid-receipt-idem-2"}

        client.post(f"{_V1}/receipts/close", json=payload)
        client.post(f"{_V1}/receipts/close", json=payload)

        assert len(mem_storage._transactions) == 1

    # -- Legacy receipt_id idempotency ----------------------------------------

    def test_200_legacy_receipt_id_replay(
        self, client: TestClient, mem_storage
    ) -> None:
        """
        When receipt_id is already in receiving_logs (simulated by a first POST),
        a second POST returns 200 already_posted=True without client_receipt_id.
        """
        # First call: no client_receipt_id so it won't create an idempotency row;
        # it WILL create receiving_log entries (legacy path).
        r1 = client.post(f"{_V1}/receipts/close", json=_BASE_RECEIPT)
        assert r1.status_code == 201
        # Verify receiving_log was written (legacy idempotency seed)
        assert any(
            log.get("receipt_id") == "REC-2026-001"
            for log in mem_storage._recv_logs
        )

        # Second call with same receipt_id (no UUID) → legacy replay 200
        r2 = client.post(f"{_V1}/receipts/close", json=_BASE_RECEIPT)
        assert r2.status_code == 200
        assert r2.json()["already_posted"] is True
        all_received = [l["status"] for l in r2.json()["lines"]]
        assert all(s == "already_received" for s in all_received)

    def test_legacy_replay_does_not_double_write(
        self, client: TestClient, mem_storage
    ) -> None:
        """Legacy receipt_id replay must not write a second RECEIPT txn."""
        client.post(f"{_V1}/receipts/close", json=_BASE_RECEIPT)
        client.post(f"{_V1}/receipts/close", json=_BASE_RECEIPT)

        assert len(mem_storage._transactions) == 1
