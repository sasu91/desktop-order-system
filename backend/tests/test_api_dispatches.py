"""
backend/tests/test_api_dispatches.py — Integration tests for Order Dispatch endpoints.

Tests cover:
  TestCreateDispatch    — POST /api/v1/order-dispatches
  TestListDispatches    — GET  /api/v1/order-dispatches
  TestGetDispatch       — GET  /api/v1/order-dispatches/{id}
  TestDeleteDispatch    — DELETE /api/v1/order-dispatches/{id}
  TestDeleteAllDispatches — DELETE /api/v1/order-dispatches
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_V1 = "/api/v1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_LINES = [
    {
        "sku": "0010001",
        "description": "Latte UHT 1L",
        "qty_ordered": 12,
        "ean": "1234567890128",
        "order_id": "20260101_001",
        "receipt_date": "2026-01-08",
    },
    {
        "sku": "0010002",
        "description": "Mozzarella 125g",
        "qty_ordered": 6,
        "ean": None,
        "order_id": "20260101_002",
        "receipt_date": None,
    },
]

_SAMPLE_REQUEST = {
    "lines": _SAMPLE_LINES,
    "note": "ordine settimanale",
}


def _create_dispatch(client: TestClient, request: dict | None = None) -> dict:
    """POST to create a dispatch, assert 201, return body."""
    payload = request or _SAMPLE_REQUEST
    r = client.post(f"{_V1}/order-dispatches", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ===========================================================================
# POST /api/v1/order-dispatches
# ===========================================================================


class TestCreateDispatch:

    def test_201_returns_dispatch_id(self, client: TestClient) -> None:
        body = _create_dispatch(client)
        assert body["dispatch_id"].startswith("DSP_")
        assert body["line_count"] == 2
        assert body["note"] == "ordine settimanale"
        assert len(body["lines"]) == 2

    def test_lines_qty_and_sku(self, client: TestClient) -> None:
        body = _create_dispatch(client)
        first = body["lines"][0]
        assert first["sku"] == "0010001"
        assert first["qty_ordered"] == 12
        assert first["ean"] == "1234567890128"

    def test_null_ean_accepted(self, client: TestClient) -> None:
        """Lines without EAN should be accepted (ean → null in response)."""
        body = _create_dispatch(client)
        second = body["lines"][1]
        assert second["ean"] is None

    def test_empty_lines_rejected(self, client: TestClient) -> None:
        r = client.post(f"{_V1}/order-dispatches", json={"lines": [], "note": ""})
        assert r.status_code == 422

    def test_missing_lines_rejected(self, client: TestClient) -> None:
        r = client.post(f"{_V1}/order-dispatches", json={"note": "test"})
        assert r.status_code == 422

    def test_empty_note_accepted(self, client: TestClient) -> None:
        payload = {**_SAMPLE_REQUEST, "note": ""}
        body = _create_dispatch(client, payload)
        assert body["note"] == ""


# ===========================================================================
# GET /api/v1/order-dispatches
# ===========================================================================


class TestListDispatches:

    def test_empty_list(self, client: TestClient) -> None:
        r = client.get(f"{_V1}/order-dispatches")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_created_dispatch(self, client: TestClient) -> None:
        _create_dispatch(client)
        r = client.get(f"{_V1}/order-dispatches")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        assert items[0]["dispatch_id"].startswith("DSP_")
        assert items[0]["line_count"] == 2

    def test_max_10_returned(self, client: TestClient) -> None:
        """Create 12 dispatches; list must return exactly 10."""
        for _ in range(12):
            _create_dispatch(client)
        r = client.get(f"{_V1}/order-dispatches")
        assert r.status_code == 200
        assert len(r.json()) == 10

    def test_sorted_newest_first(self, client: TestClient) -> None:
        """Most recent dispatch must appear first."""
        _create_dispatch(client)
        _create_dispatch(client)
        items = client.get(f"{_V1}/order-dispatches").json()
        # dispatch IDs embed UTC timestamp: later ID >= earlier ID lexicographically
        assert items[0]["sent_at"] >= items[1]["sent_at"]


# ===========================================================================
# GET /api/v1/order-dispatches/{dispatch_id}
# ===========================================================================


class TestGetDispatch:

    def test_200_with_lines(self, client: TestClient) -> None:
        created = _create_dispatch(client)
        dispatch_id = created["dispatch_id"]

        r = client.get(f"{_V1}/order-dispatches/{dispatch_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["dispatch_id"] == dispatch_id
        assert len(body["lines"]) == 2

    def test_404_unknown_id(self, client: TestClient) -> None:
        r = client.get(f"{_V1}/order-dispatches/DSP_UNKNOWN")
        assert r.status_code == 404


# ===========================================================================
# DELETE /api/v1/order-dispatches/{dispatch_id}
# ===========================================================================


class TestDeleteDispatch:

    def test_deletes_and_disappears_from_list(self, client: TestClient) -> None:
        created = _create_dispatch(client)
        dispatch_id = created["dispatch_id"]

        r = client.delete(f"{_V1}/order-dispatches/{dispatch_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] is True
        assert body["dispatch_id"] == dispatch_id

        # Should not appear in list
        items = client.get(f"{_V1}/order-dispatches").json()
        assert all(item["dispatch_id"] != dispatch_id for item in items)

    def test_404_on_missing(self, client: TestClient) -> None:
        r = client.delete(f"{_V1}/order-dispatches/DSP_NONEXISTENT")
        assert r.status_code == 404


# ===========================================================================
# DELETE /api/v1/order-dispatches  (delete all)
# ===========================================================================


class TestDeleteAllDispatches:

    def test_deletes_all(self, client: TestClient) -> None:
        _create_dispatch(client)
        _create_dispatch(client)
        assert len(client.get(f"{_V1}/order-dispatches").json()) == 2

        r = client.delete(f"{_V1}/order-dispatches")
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] is True

        assert client.get(f"{_V1}/order-dispatches").json() == []

    def test_idempotent_on_empty(self, client: TestClient) -> None:
        """Delete all on empty store must succeed."""
        r = client.delete(f"{_V1}/order-dispatches")
        assert r.status_code == 200
