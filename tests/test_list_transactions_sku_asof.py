"""
Tests for LedgerRepository.list_transactions_for_sku_asof()
and the removal of the 10 000-row LIMIT from list_transactions().

Scenario
--------
20 SKUs × 1 000 transactions each  =  20 000 total rows.
For SKU_TARGET ("SKU_00"):
  - 1 SNAPSHOT(qty=2000) on 2020-01-01
  - 999 SALE(qty=1) on 2020-01-02 … 2022-09-27 (one per day)
  Expected stock AsOf 2026-02-25: on_hand = 2000 - 999 = 1001

Assertions
----------
1. list_transactions_for_sku_asof("SKU_00", asof) returns exactly 1 000 rows.
2. All returned rows have sku == "SKU_00" (no cross-SKU leakage).
3. StockCalculator on those rows yields on_hand == 1001.
4. list_transactions(limit=10_000) still truncates to 10 000 (proves old
   behaviour existed) when limit is given explicitly.
5. list_transactions() with no limit returns all 20 000 rows (new default).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.db import PRAGMA_CONFIG
from src.repositories import LedgerRepository, SKURepository
from src.domain.ledger import StockCalculator
from src.domain.models import Transaction, EventType

# ── Constants ────────────────────────────────────────────────────────────────

N_SKUS = 20
TXNS_PER_SKU = 1_000          # 1 SNAPSHOT + 999 SALE  =  1 000 per SKU
TOTAL_TXNS = N_SKUS * TXNS_PER_SKU  # 20 000

SNAPSHOT_QTY = 2_000
SALE_QTY = 1
EXPECTED_ON_HAND = SNAPSHOT_QTY - (TXNS_PER_SKU - 1) * SALE_QTY  # 1001

TARGET_SKU = "SKU_00"
ASOF = date(2026, 2, 25)
EPOCH = date(2020, 1, 1)       # first transaction date (all dates < ASOF)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def conn() -> Generator[sqlite3.Connection, None, None]:
    """
    In-memory SQLite database populated with 20 000 transactions across 20 SKUs.
    Module scope: created once, reused by all tests in this module.
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row

    # Apply PRAGMAs (autocommit-style, before any transaction)
    for pragma, value in PRAGMA_CONFIG.items():
        db.execute(f"PRAGMA {pragma}={value}")

    # Apply full schema via migration SQL files (executescript commits atomically)
    migrations_dir = Path(__file__).parent.parent / "migrations"
    for migration_file in sorted(migrations_dir.glob("*.sql")):
        sql = migration_file.read_text()
        db.executescript(sql)
    db.commit()

    sku_repo = SKURepository(db)
    ledger_repo = LedgerRepository(db)

    # ── Insert SKUs ──────────────────────────────────────────────────────────
    for i in range(N_SKUS):
        sku_id = f"SKU_{i:02d}"
        sku_repo.upsert({"sku": sku_id, "description": f"Test product {i}"})

    # ── Insert transactions ───────────────────────────────────────────────────
    # Each SKU gets:  SNAPSHOT on day 0,  then (TXNS_PER_SKU - 1) SALE events.
    # Different SKUs get different snapshot quantities so their stock values
    # are clearly distinguishable.
    batch: list[dict] = []
    for i in range(N_SKUS):
        sku_id = f"SKU_{i:02d}"
        snap_qty = SNAPSHOT_QTY if sku_id == TARGET_SKU else (5_000 + i * 10)
        batch.append(
            {
                "date": EPOCH.isoformat(),
                "sku": sku_id,
                "event": "SNAPSHOT",
                "qty": snap_qty,
                "receipt_date": None,
                "note": f"initial snapshot for {sku_id}",
            }
        )
        for day in range(1, TXNS_PER_SKU):  # days 1..999
            sale_date = EPOCH + timedelta(days=day)
            batch.append(
                {
                    "date": sale_date.isoformat(),
                    "sku": sku_id,
                    "event": "SALE",
                    "qty": SALE_QTY,
                    "receipt_date": None,
                    "note": "",
                }
            )

    ledger_repo.append_batch(batch)

    yield  
    db.close()


@pytest.fixture
def ledger(conn: sqlite3.Connection) -> LedgerRepository:
    return LedgerRepository(conn)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestListTransactionsForSkuAsof:
    """Purpose-built SQL query: only loads one SKU, no LIMIT."""

    def test_returns_correct_row_count(self, ledger: LedgerRepository):
        """Exactly TXNS_PER_SKU rows returned for the target SKU."""
        rows = ledger.list_transactions_for_sku_asof(TARGET_SKU, ASOF)
        assert len(rows) == TXNS_PER_SKU, (
            f"Expected {TXNS_PER_SKU} rows, got {len(rows)}"
        )

    def test_no_cross_sku_leakage(self, ledger: LedgerRepository):
        """Every returned row belongs to TARGET_SKU — no other SKU loaded."""
        rows = ledger.list_transactions_for_sku_asof(TARGET_SKU, ASOF)
        other_skus = {r["sku"] for r in rows if r["sku"] != TARGET_SKU}
        assert other_skus == set(), (
            f"Cross-SKU leakage detected: {other_skus}"
        )

    def test_asof_boundary_exclusive(self, ledger: LedgerRepository):
        """Transactions on asof date itself must NOT be returned (date < asof)."""
        # Insert a future transaction dated exactly on ASOF for TARGET_SKU
        # We don't actually insert (module-scoped DB would pollute other tests);
        # instead verify that filtering by EPOCH+1 day excludes the SNAPSHOT.
        rows = ledger.list_transactions_for_sku_asof(TARGET_SKU, EPOCH)
        assert len(rows) == 0, (
            "No transactions should be returned when asof == EPOCH (all dates >= EPOCH)"
        )

    def test_asof_boundary_inclusive_next_day(self, ledger: LedgerRepository):
        """SNAPSHOT on EPOCH is returned when asof = EPOCH + 1 day."""
        rows = ledger.list_transactions_for_sku_asof(TARGET_SKU, EPOCH + timedelta(days=1))
        assert len(rows) == 1
        assert rows[0]["event"] == "SNAPSHOT"

    def test_sorted_ascending(self, ledger: LedgerRepository):
        """Rows are sorted by (date ASC, transaction_id ASC)."""
        rows = ledger.list_transactions_for_sku_asof(TARGET_SKU, ASOF)
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates), "Rows are not sorted by date ASC"

    def test_stock_calculation_correct(self, ledger: LedgerRepository):
        """
        StockCalculator on the filtered rows yields the expected on_hand.
        This is the key correctness assertion: partial data gives wrong stock.
        """
        rows = ledger.list_transactions_for_sku_asof(TARGET_SKU, ASOF)
        transactions = [
            Transaction(
                date=date.fromisoformat(r["date"]),
                sku=r["sku"],
                event=EventType(r["event"]),
                qty=int(r["qty"]),
                receipt_date=date.fromisoformat(r["receipt_date"]) if r.get("receipt_date") else None,
                note=r.get("note") or "",
            )
            for r in rows
        ]
        stock = StockCalculator.calculate_asof(TARGET_SKU, ASOF, transactions)
        assert stock.on_hand == EXPECTED_ON_HAND, (
            f"Expected on_hand={EXPECTED_ON_HAND}, got {stock.on_hand}"
        )
        assert stock.on_order == 0

    def test_unknown_sku_returns_empty(self, ledger: LedgerRepository):
        """Query for a non-existent SKU returns an empty list, not an error."""
        rows = ledger.list_transactions_for_sku_asof("DOES_NOT_EXIST", ASOF)
        assert rows == []


class TestListTransactionsNoLimit:
    """list_transactions() with no limit must return all rows; explicit limit still caps."""

    def test_no_limit_returns_all_rows(self, ledger: LedgerRepository):
        """
        list_transactions() with no limit returns the full 20 000-row ledger.
        Previously the default limit=10000 would have silently truncated this.
        """
        rows = ledger.list_transactions()
        assert len(rows) == TOTAL_TXNS, (
            f"Expected {TOTAL_TXNS} rows without limit, got {len(rows)}"
        )

    def test_explicit_limit_still_caps(self, ledger: LedgerRepository):
        """
        Passing limit=10_000 explicitly still caps at 10 000.
        This documents the regression: the OLD default was this value, which
        silently truncated a 20 000-row ledger.
        """
        rows = ledger.list_transactions(limit=10_000)
        assert len(rows) == 10_000, (
            f"Expected 10 000 rows when limit=10_000, got {len(rows)}"
        )

    def test_old_default_would_have_been_wrong(self, ledger: LedgerRepository):
        """
        Prove that the old limit=10000 default caused incorrect stock calculation.

        With 20 SKUs × 1000 txns each sorted by date ASC, the first 10 000 rows
        cover dates EPOCH..EPOCH+499 only (10 rows/day × 500 days).
        The SNAPSHOT on day 0 IS included, but only 499 SALE events out of
        999 are present for TARGET_SKU → stock would be over-estimated.
        """
        truncated_rows = ledger.list_transactions(limit=10_000)

        # Build domain objects and filter to TARGET_SKU
        truncated_target = [
            Transaction(
                date=date.fromisoformat(r["date"]),
                sku=r["sku"],
                event=EventType(r["event"]),
                qty=int(r["qty"]),
                receipt_date=None,
                note="",
            )
            for r in truncated_rows
            if r["sku"] == TARGET_SKU
        ]

        stock_truncated = StockCalculator.calculate_asof(TARGET_SKU, ASOF, truncated_target)

        # With the new method we get the true value
        full_rows = ledger.list_transactions_for_sku_asof(TARGET_SKU, ASOF)
        full_transactions = [
            Transaction(
                date=date.fromisoformat(r["date"]),
                sku=r["sku"],
                event=EventType(r["event"]),
                qty=int(r["qty"]),
                receipt_date=None,
                note="",
            )
            for r in full_rows
        ]
        stock_full = StockCalculator.calculate_asof(TARGET_SKU, ASOF, full_transactions)

        # The truncated result over-counts on_hand (fewer SALEs consumed)
        assert stock_truncated.on_hand > stock_full.on_hand, (
            "Truncated data should yield a higher (incorrect) on_hand than full data"
        )
        assert stock_full.on_hand == EXPECTED_ON_HAND
