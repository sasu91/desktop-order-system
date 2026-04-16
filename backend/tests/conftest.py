"""
backend/tests/conftest.py — Shared fixtures for API integration tests.

All fixtures are function-scoped (default) so each test gets its own
fresh storage + SQLite connection. Idempotency tests make multiple
requests within the same test function using the shared client/db.

Dependency override strategy
-----------------------------
  verify_token   → returns "__test__" unconditionally (skips auth)
  get_db         → yields a temp SQLite connection (idempotency table only)
  get_storage    → yields a _MemStorage backed by Python lists
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from dos_backend.api.app import create_app
from dos_backend.api.auth import verify_token
from dos_backend.api.deps import get_db, get_storage
from dos_backend.api import idempotency
from dos_backend.domain.models import SKU, Transaction


# ---------------------------------------------------------------------------
# In-memory storage stub — minimal interface consumed by our routers
# ---------------------------------------------------------------------------

class _MemStorage:
    """
    Pure-Python stand-in for StorageAdapter.

    Implements only the methods called by the four tested endpoints:
      read_skus / read_transactions / write_transaction / write_transactions_batch
      read_receiving_logs / write_receiving_log / close
    """

    def __init__(self, skus: list[SKU]) -> None:
        self._skus: list[SKU] = list(skus)
        self._transactions: list[Transaction] = []
        self._recv_logs: list[dict] = []

    # -- SKU ------------------------------------------------------------------
    def read_skus(self) -> list[SKU]:
        return list(self._skus)

    # -- Transactions ---------------------------------------------------------
    def read_transactions(self) -> list[Transaction]:
        return list(self._transactions)

    def write_transaction(self, txn: Transaction) -> None:
        """Used by POST /exceptions."""
        self._transactions.append(txn)

    def write_transactions_batch(self, txns: list[Transaction]) -> None:
        """Used by POST /receipts/close."""
        self._transactions.extend(txns)

    # -- Receiving logs -------------------------------------------------------
    def read_receiving_logs(self) -> list[dict]:
        return list(self._recv_logs)

    def write_receiving_log(self, **kwargs) -> None:  # noqa: ANN003
        self._recv_logs.append(dict(kwargs))

    def overwrite_transactions(self, txns: list[Transaction]) -> None:
        """Replace the entire transaction list (used by daily-upsert replace mode)."""
        self._transactions = list(txns)

    # -- Order dispatches (send to Android) -----------------------------------
    def __init_dispatch_store(self):
        if not hasattr(self, "_dispatches"):
            self._dispatches: list[dict] = []
            self._dispatch_lines: list[dict] = []

    def read_order_dispatches(self) -> list[dict]:
        self.__init_dispatch_store()
        return sorted(self._dispatches, key=lambda r: r.get("sent_at", ""), reverse=True)

    def read_order_dispatch_lines(self, dispatch_id: str) -> list[dict]:
        self.__init_dispatch_store()
        return [r for r in self._dispatch_lines if r.get("dispatch_id") == dispatch_id]

    def write_order_dispatch(self, dispatch_id: str, sent_at: str, line_count: int, note: str = "") -> None:
        self.__init_dispatch_store()
        self._dispatches.append({
            "dispatch_id": dispatch_id,
            "sent_at": sent_at,
            "line_count": str(line_count),
            "note": note,
        })

    def write_order_dispatch_lines_batch(self, lines: list[dict]) -> None:
        self.__init_dispatch_store()
        self._dispatch_lines.extend(lines)

    def delete_order_dispatch(self, dispatch_id: str) -> bool:
        self.__init_dispatch_store()
        before = len(self._dispatches)
        self._dispatches = [r for r in self._dispatches if r["dispatch_id"] != dispatch_id]
        self._dispatch_lines = [r for r in self._dispatch_lines if r.get("dispatch_id") != dispatch_id]
        return len(self._dispatches) < before

    def delete_all_order_dispatches(self) -> int:
        self.__init_dispatch_store()
        count = len(self._dispatches)
        self._dispatches = []
        self._dispatch_lines = []
        return count

    # -- Lifecycle ------------------------------------------------------------
    def close(self) -> None:  # noqa: D401
        """No-op — nothing to close for in-memory storage."""

    # -- SKU write (used by POST /skus) ---------------------------------------
    def write_sku(self, sku: SKU) -> None:
        """Upsert a SKU by sku code."""
        self._skus = [s for s in self._skus if s.sku != sku.sku]
        self._skus.append(sku)

    def sku_exists(self, sku_id: str) -> bool:
        return any(s.sku == sku_id for s in self._skus)

    def search_skus(self, query: str) -> list[SKU]:
        q = query.lower()
        return [s for s in self._skus if q in s.sku.lower() or q in s.description.lower()]


# ---------------------------------------------------------------------------
# Seed data (shared across all tests; each test gets a fresh _MemStorage copy)
# ---------------------------------------------------------------------------

# EAN-13 strings — 13 digits, no check-digit validation enforced by validate_ean.
_SEED_SKUS: list[SKU] = [
    SKU(sku="0010001", description="Latte UHT 1L", ean="1234567890128"),
    SKU(
        sku="0010002",
        description="Mozzarella 125g",
        ean="9780201379624",
        has_expiry_label=True,
    ),
    SKU(sku="0010003", description="Acqua 50cl", ean=None),
]

# EAN that matches 0010001 (used in multiple tests)
SEED_EAN_PLAIN = "1234567890128"
# EAN that matches 0010002 (expiry required)
SEED_EAN_EXPIRY = "9780201379624"
# Well-formed EAN that doesn't match any SKU
EAN_UNKNOWN = "1111111111111"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_conn(tmp_path):
    """
    Temp SQLite connection used for the idempotency table (api_idempotency_keys).

    The connection is kept open for the duration of the test so that multiple
    HTTP requests in the same test share the same idempotency state.
    """
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Bootstrap the idempotency schema (one table, idempotent DDL)
    idempotency.ensure_schema(conn)

    yield conn
    conn.close()


@pytest.fixture()
def mem_storage() -> _MemStorage:
    """Fresh in-memory storage with seed SKUs, empty ledger."""
    return _MemStorage(_SEED_SKUS)


@pytest.fixture()
def client(db_conn: sqlite3.Connection, mem_storage: _MemStorage) -> TestClient:
    """
    TestClient with all three deps overridden.

    All requests in one test share the same db_conn and mem_storage so
    idempotency scenarios (two calls in the same test) work correctly.
    """
    app = create_app()

    # Override verify_token → no auth header required in tests
    def _no_auth() -> str:
        return "__test__"

    # Override get_db → reuse the fixture SQLite connection (don't close)
    def _override_db():
        yield db_conn

    # Override get_storage → use in-memory storage (don't call .close())
    def _override_storage():
        yield mem_storage

    app.dependency_overrides[verify_token] = _no_auth
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_storage] = _override_storage

    with TestClient(app, raise_server_exceptions=True) as tc:
        yield tc

    app.dependency_overrides.clear()
