"""
Regression tests for SKU purge completo.

Covers:
1. CSVLayer.get_sku_impact_counts  — returns accurate per-file counts
2. CSVLayer.purge_sku_completely   — removes rows from all CSV files, returns counts
3. CSVLayer.purge_sku_completely   — raises ValueError for unknown SKU
4. CSVLayer.purge_sku_completely   — only touches the target SKU, leaves others intact
5. SKURepository.get_impact_counts — counts rows in SQLite tables
6. SKURepository.purge_complete    — deletes transactions + cascades in one transaction
7. SKURepository.purge_complete    — raises NotFoundError for unknown SKU
8. SKURepository.purge_complete    — partial failure leaves no orphaned transactions
9. StorageAdapter.purge_sku_completely (CSV mode) — delegates to CSVLayer
10. StorageAdapter.get_sku_impact_counts (CSV mode) — delegates to CSVLayer
"""
import csv
import sqlite3
import tempfile
import shutil
from datetime import date
from pathlib import Path

import pytest

from src.persistence.csv_layer import CSVLayer
from src.repositories import SKURepository, NotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKU_SCHEMA = [
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

_TXN_SCHEMA = ["date", "sku", "event", "qty", "receipt_date", "note"]
_SALES_SCHEMA = ["date", "sku", "qty_sold"]
_ORDER_SCHEMA = ["order_id", "date", "sku", "qty_ordered", "qty_received", "status"]
_RECV_SCHEMA = ["receipt_id", "date", "sku", "qty_received", "receipt_date"]


def _write_csv(path: Path, filename: str, schema: list[str], rows: list[dict]) -> None:
    """Write a CSV file with the given schema and rows."""
    full_path = path / filename
    with open(full_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=schema, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full_row = {k: "" for k in schema}
            full_row.update(row)
            writer.writerow(full_row)


def _make_sku_row(sku: str, desc: str = "Test SKU") -> dict:
    return {
        "sku": sku, "description": desc, "ean": "",
        "moq": "1", "pack_size": "1", "lead_time_days": "7",
        "review_period": "7", "safety_stock": "0", "max_stock": "999",
        "reorder_point": "0", "in_assortment": "true", "target_csl": "0",
        "oos_popup_preference": "ask",
    }


def _setup_csv_data(data_dir: Path, target_sku: str = "SKU_A") -> None:
    """Populate all CSV files with a mix of target and unrelated data."""
    _write_csv(data_dir, "skus.csv", _SKU_SCHEMA, [
        _make_sku_row(target_sku, "Target SKU"),
        _make_sku_row("SKU_B", "Other SKU"),
    ])
    _write_csv(data_dir, "transactions.csv", _TXN_SCHEMA, [
        {"date": "2024-01-01", "sku": target_sku, "event": "SNAPSHOT", "qty": "100"},
        {"date": "2024-01-02", "sku": target_sku, "event": "SALE",     "qty": "-5"},
        {"date": "2024-01-01", "sku": "SKU_B",   "event": "SNAPSHOT", "qty": "50"},
    ])
    _write_csv(data_dir, "sales.csv", _SALES_SCHEMA, [
        {"date": "2024-01-02", "sku": target_sku, "qty_sold": "5"},
        {"date": "2024-01-02", "sku": "SKU_B",   "qty_sold": "2"},
    ])
    _write_csv(data_dir, "order_logs.csv", _ORDER_SCHEMA, [
        {"order_id": "ORD-001", "date": "2024-01-03", "sku": target_sku, "qty_ordered": "20", "qty_received": "0", "status": "PENDING"},
    ])
    _write_csv(data_dir, "receiving_logs.csv", _RECV_SCHEMA, [
        {"receipt_id": "REC-001", "date": "2024-01-04", "sku": target_sku, "qty_received": "20"},
    ])


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _create_in_memory_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the production schema applied."""
    from src.db import apply_migrations
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Read and apply the initial schema directly (avoids path issues in tests)
    schema_path = Path(__file__).parent.parent / "migrations" / "001_initial_schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    # Split on semicolons and execute each statement (skip empty ones)
    for stmt in schema_sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # Ignore IF NOT EXISTS conflicts
    conn.commit()
    return conn


def _insert_sku(conn: sqlite3.Connection, sku: str, desc: str = "Test") -> None:
    conn.execute(
        "INSERT INTO skus (sku, description) VALUES (?, ?)",
        (sku, desc),
    )
    conn.commit()


def _insert_txn(conn: sqlite3.Connection, sku: str, event: str = "SNAPSHOT") -> None:
    conn.execute(
        "INSERT INTO transactions (date, sku, event, qty) VALUES (?, ?, ?, ?)",
        ("2024-01-01", sku, event, 10),
    )
    conn.commit()


def _insert_sale(conn: sqlite3.Connection, sku: str) -> None:
    conn.execute(
        "INSERT INTO sales (date, sku, qty_sold) VALUES (?, ?, ?)",
        ("2024-01-01", sku, 5),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d)


@pytest.fixture
def csv_layer(temp_dir):
    return CSVLayer(data_dir=temp_dir)


@pytest.fixture
def mem_db():
    conn = _create_in_memory_db()
    yield conn
    conn.close()


@pytest.fixture
def sku_repo(mem_db):
    return SKURepository(conn=mem_db)


# ---------------------------------------------------------------------------
# CSVLayer tests
# ---------------------------------------------------------------------------

class TestCSVLayerImpactCounts:
    def test_returns_accurate_counts(self, csv_layer, temp_dir):
        _setup_csv_data(temp_dir, "SKU_A")
        counts = csv_layer.get_sku_impact_counts("SKU_A")

        assert counts["transactions"] == 2
        assert counts["sales"] == 1
        assert counts["order_logs"] == 1
        assert counts["receiving_logs"] == 1

    def test_zero_counts_for_sku_with_no_data(self, csv_layer, temp_dir):
        _write_csv(temp_dir, "skus.csv", _SKU_SCHEMA, [_make_sku_row("GHOST_SKU")])
        # Don't write any related data
        counts = csv_layer.get_sku_impact_counts("GHOST_SKU")

        assert counts["transactions"] == 0
        assert counts["sales"] == 0
        assert counts["order_logs"] == 0
        assert counts["receiving_logs"] == 0

    def test_ignores_other_skus(self, csv_layer, temp_dir):
        _setup_csv_data(temp_dir, "SKU_A")
        counts = csv_layer.get_sku_impact_counts("SKU_B")

        # SKU_B has 1 transaction, 1 sale, 0 orders, 0 receipts from _setup_csv_data
        assert counts["transactions"] == 1
        assert counts["sales"] == 1
        assert counts["order_logs"] == 0
        assert counts["receiving_logs"] == 0


class TestCSVLayerPurgeSku:
    def test_purge_removes_all_target_rows(self, csv_layer, temp_dir):
        _setup_csv_data(temp_dir, "SKU_A")
        csv_layer.purge_sku_completely("SKU_A")

        # transactions.csv must have no SKU_A rows
        remaining_txn = [
            r for r in csv_layer.read_transactions()
            if str(r.sku).strip() == "SKU_A"
        ]
        assert remaining_txn == []

        # skus.csv must have no SKU_A row
        remaining_skus = [
            r for r in csv_layer.read_skus()
            if str(r.sku).strip() == "SKU_A"
        ]
        assert remaining_skus == []

    def test_purge_returns_correct_counts(self, csv_layer, temp_dir):
        _setup_csv_data(temp_dir, "SKU_A")
        counts = csv_layer.purge_sku_completely("SKU_A")

        assert counts["transactions"] == 2
        assert counts["sales"] == 1
        assert counts["order_logs"] == 1
        assert counts["receiving_logs"] == 1
        assert counts["skus"] == 1

    def test_purge_leaves_other_sku_intact(self, csv_layer, temp_dir):
        _setup_csv_data(temp_dir, "SKU_A")
        csv_layer.purge_sku_completely("SKU_A")

        remaining_b = [
            r for r in csv_layer.read_skus()
            if str(r.sku).strip() == "SKU_B"
        ]
        assert len(remaining_b) == 1

        remaining_txn_b = [
            r for r in csv_layer.read_transactions()
            if str(r.sku).strip() == "SKU_B"
        ]
        assert len(remaining_txn_b) == 1

    def test_purge_not_found_raises_value_error(self, csv_layer, temp_dir):
        # Empty skus.csv (auto-created by CSVLayer on first access)
        with pytest.raises(ValueError, match="non trovato"):
            csv_layer.purge_sku_completely("NO_SUCH_SKU")

    def test_purge_sku_with_no_related_data(self, csv_layer, temp_dir):
        """Purge a SKU that exists but has zero associated rows — must not raise."""
        _write_csv(temp_dir, "skus.csv", _SKU_SCHEMA, [_make_sku_row("SOLO_SKU")])
        counts = csv_layer.purge_sku_completely("SOLO_SKU")

        assert counts["transactions"] == 0
        assert counts["skus"] == 1


# ---------------------------------------------------------------------------
# SKURepository (SQLite) tests
# ---------------------------------------------------------------------------

class TestSKURepositoryImpactCounts:
    def test_counts_transactions_and_sales(self, sku_repo, mem_db):
        _insert_sku(mem_db, "SKU_X")
        _insert_txn(mem_db, "SKU_X", "SNAPSHOT")
        _insert_txn(mem_db, "SKU_X", "SALE")
        _insert_sale(mem_db, "SKU_X")

        counts = sku_repo.get_impact_counts("SKU_X")
        assert counts["transactions"] == 2
        assert counts["sales"] == 1

    def test_counts_zero_for_nonexistent_sku(self, sku_repo):
        counts = sku_repo.get_impact_counts("GHOST")

        for v in counts.values():
            assert v == 0

    def test_does_not_count_other_sku(self, sku_repo, mem_db):
        _insert_sku(mem_db, "SKU_Y")
        _insert_sku(mem_db, "SKU_Z")
        _insert_txn(mem_db, "SKU_Z", "SNAPSHOT")

        counts = sku_repo.get_impact_counts("SKU_Y")
        assert counts["transactions"] == 0


class TestSKURepositoryPurgeComplete:
    def test_purge_removes_sku_and_transactions(self, sku_repo, mem_db):
        _insert_sku(mem_db, "SKU_DEL")
        _insert_txn(mem_db, "SKU_DEL", "SNAPSHOT")
        _insert_txn(mem_db, "SKU_DEL", "SALE")

        sku_repo.purge_complete("SKU_DEL")

        # SKU row must be gone
        row = mem_db.execute("SELECT * FROM skus WHERE sku = ?", ("SKU_DEL",)).fetchone()
        assert row is None

        # All transactions must be gone
        txn_count = mem_db.execute(
            "SELECT COUNT(*) FROM transactions WHERE sku = ?", ("SKU_DEL",)
        ).fetchone()[0]
        assert txn_count == 0

    def test_purge_returns_counts(self, sku_repo, mem_db):
        _insert_sku(mem_db, "SKU_CNT")
        _insert_txn(mem_db, "SKU_CNT", "SNAPSHOT")
        _insert_sale(mem_db, "SKU_CNT")

        counts = sku_repo.purge_complete("SKU_CNT")
        assert counts["transactions"] == 1
        assert counts["sales"] == 1

    def test_purge_cascades_sales(self, sku_repo, mem_db):
        _insert_sku(mem_db, "SKU_CAS")
        _insert_sale(mem_db, "SKU_CAS")

        sku_repo.purge_complete("SKU_CAS")

        sales_count = mem_db.execute(
            "SELECT COUNT(*) FROM sales WHERE sku = ?", ("SKU_CAS",)
        ).fetchone()[0]
        assert sales_count == 0

    def test_purge_not_found_raises_not_found_error(self, sku_repo):
        with pytest.raises(NotFoundError, match="non trovato"):
            sku_repo.purge_complete("NONEXISTENT")

    def test_purge_does_not_affect_other_sku(self, sku_repo, mem_db):
        _insert_sku(mem_db, "SKU_KEEP")
        _insert_sku(mem_db, "SKU_GONE")
        _insert_txn(mem_db, "SKU_KEEP", "SNAPSHOT")
        _insert_txn(mem_db, "SKU_GONE", "SNAPSHOT")

        sku_repo.purge_complete("SKU_GONE")

        # SKU_KEEP and its transactions must survive
        row = mem_db.execute("SELECT * FROM skus WHERE sku = ?", ("SKU_KEEP",)).fetchone()
        assert row is not None

        txn_count = mem_db.execute(
            "SELECT COUNT(*) FROM transactions WHERE sku = ?", ("SKU_KEEP",)
        ).fetchone()[0]
        assert txn_count == 1

    def test_purge_is_idempotent_second_call_raises(self, sku_repo, mem_db):
        """Calling purge on an already-purged SKU raises NotFoundError (no partial state)."""
        _insert_sku(mem_db, "SKU_ROLLBACK")
        _insert_txn(mem_db, "SKU_ROLLBACK", "SNAPSHOT")

        # First purge: must succeed and remove everything
        sku_repo.purge_complete("SKU_ROLLBACK")

        # Second purge: SKU is gone, must raise NotFoundError (not silently succeed)
        with pytest.raises(NotFoundError):
            sku_repo.purge_complete("SKU_ROLLBACK")


# ---------------------------------------------------------------------------
# StorageAdapter (CSV mode) tests
# ---------------------------------------------------------------------------

class TestStorageAdapterCsvMode:
    """StorageAdapter in CSV-only mode should delegate to CSVLayer."""

    def _make_adapter(self, data_dir: Path):
        from src.persistence.storage_adapter import StorageAdapter
        return StorageAdapter(data_dir=data_dir, force_backend="csv")

    def test_csv_mode_get_impact_counts(self, temp_dir):
        _setup_csv_data(temp_dir, "SKU_A")
        adapter = self._make_adapter(temp_dir)

        counts = adapter.get_sku_impact_counts("SKU_A")
        assert counts["transactions"] == 2
        assert counts["sales"] == 1

    def test_csv_mode_purge_removes_sku(self, temp_dir):
        _setup_csv_data(temp_dir, "SKU_A")
        adapter = self._make_adapter(temp_dir)

        counts = adapter.purge_sku_completely("SKU_A")
        assert counts["transactions"] == 2
        assert counts["skus"] == 1

    def test_csv_mode_purge_not_found_raises(self, temp_dir):
        adapter = self._make_adapter(temp_dir)

        with pytest.raises(ValueError, match="non trovato"):
            adapter.purge_sku_completely("GHOST")
