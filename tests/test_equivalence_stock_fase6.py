"""
FASE 6: Equivalence Tests - Stock Calculation

Test that stock calculation produces identical results 
regardless of backend (CSV or SQLite).

Critical validation: Ledger semantics must be preserved.
"""

import pytest
import json
import shutil
import sqlite3
from pathlib import Path
from datetime import date
from typing import Dict

from src.domain.ledger import StockCalculator
from src.domain.models import Stock
from src.persistence.storage_adapter import StorageAdapter
from src.db import open_connection, initialize_database
from src.migrate_csv_to_sqlite import MigrationOrchestrator


# Golden dataset path
GOLDEN_DATA_DIR = Path(__file__).parent / "golden_data"


@pytest.fixture
def golden_csv_dir(tmp_path):
    """
    Copy golden dataset to temporary directory for test isolation.
    
    Returns:
        Path: Temporary directory with golden CSV files
    """
    test_data_dir = tmp_path / "golden_test_data"
    shutil.copytree(GOLDEN_DATA_DIR, test_data_dir)
    return test_data_dir


@pytest.fixture
def golden_sqlite_db(tmp_path, golden_csv_dir):
    """
    Create SQLite database and migrate golden dataset.
    
    Returns:
        Path: SQLite database path
    """
    db_path = tmp_path / "golden_test.db"
    
    # Initialize database with schema (creates empty tables)
    conn = open_connection(db_path)
    
    # Apply schema migrations
    from src.db import apply_migrations
    apply_migrations(conn)
    
    # Migrate golden dataset
    orchestrator = MigrationOrchestrator(conn=conn, csv_dir=golden_csv_dir)
    report = orchestrator.migrate_all()
    
    # Verify migration succeeded
    assert not report.has_errors(), f"Migration failed with {report.total_errors()} errors"
    
    conn.close()
    
    return db_path


def load_expected_stock(asof_date: date) -> Dict[str, Dict[str, int]]:
    """Load expected stock values from golden dataset"""
    expected_file = GOLDEN_DATA_DIR / "expected" / f"stock_asof_{asof_date.isoformat()}.json"
    with open(expected_file) as f:
        return json.load(f)


# ============================================================
# Test: Stock Calculation Equivalence
# ============================================================

@pytest.mark.parametrize("validation_date", [
    date(2025, 1, 15),   # Mid-January
    date(2025, 3, 1),    # Start of March
    date(2025, 6, 30),   # End of June
    date(2025, 12, 31),  # End of year
])
def test_stock_calculation_csv_vs_sqlite(golden_csv_dir, golden_sqlite_db, validation_date):
    """
    Validate that stock calculation produces identical results 
    for CSV and SQLite backends.
    
    Given: Golden dataset with known SKUs, transactions, and sales
    When: Calculate stock AsOf using CSV backend
    And: Calculate stock AsOf using SQLite backend
    Then: Both results are identical
    And: Both match expected golden values
    """
    # Given: Load golden dataset via CSV backend
    csv_adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='csv')
    skus = csv_adapter.read_skus()
    transactions = csv_adapter.read_transactions()
    sales = csv_adapter.read_sales()
    
    # And: Load golden dataset via SQLite backend
    sqlite_adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='sqlite')
    sqlite_adapter.conn = open_connection(golden_sqlite_db)  # Use pre-migrated DB
    from src.repositories import RepositoryFactory
    sqlite_adapter.repos = RepositoryFactory(sqlite_adapter.conn)
    sqlite_adapter.backend = 'sqlite'
    
    sqlite_skus = sqlite_adapter.read_skus()
    sqlite_transactions = sqlite_adapter.read_transactions()
    sqlite_sales = sqlite_adapter.read_sales()
    
    # And: Load expected stock from golden values
    expected_stock = load_expected_stock(validation_date)
    
    # When: Calculate stock for all SKUs using CSV data
    csv_stocks = {}
    for sku in skus:
        stock = StockCalculator.calculate_asof(
            sku=sku.sku,
            asof_date=validation_date,
            transactions=transactions,
            sales_records=sales
        )
        csv_stocks[sku.sku] = stock
    
    # And: Calculate stock for all SKUs using SQLite data
    sqlite_stocks = {}
    for sku in sqlite_skus:
        stock = StockCalculator.calculate_asof(
            sku=sku.sku,
            asof_date=validation_date,
            transactions=sqlite_transactions,
            sales_records=sqlite_sales
        )
        sqlite_stocks[sku.sku] = stock
    
    # Then: CSV and SQLite results are identical
    assert len(csv_stocks) == len(sqlite_stocks), "Different number of SKUs"
    
    for sku_id in csv_stocks.keys():
        csv_stock = csv_stocks[sku_id]
        sqlite_stock = sqlite_stocks[sku_id]
        
        assert csv_stock.on_hand == sqlite_stock.on_hand, \
            f"[{sku_id}] on_hand mismatch: CSV={csv_stock.on_hand}, SQLite={sqlite_stock.on_hand}"
        
        assert csv_stock.on_order == sqlite_stock.on_order, \
            f"[{sku_id}] on_order mismatch: CSV={csv_stock.on_order}, SQLite={sqlite_stock.on_order}"
    
    # And: Both match expected golden values
    for sku_id in csv_stocks.keys():
        csv_stock = csv_stocks[sku_id]
        expected = expected_stock[sku_id]
        
        assert csv_stock.on_hand == expected['on_hand'], \
            f"[{sku_id}] CSV on_hand != expected: {csv_stock.on_hand} != {expected['on_hand']}"
        
        assert csv_stock.on_order == expected['on_order'], \
            f"[{sku_id}] CSV on_order != expected: {csv_stock.on_order} != {expected['on_order']}"
    
    # Cleanup
    csv_adapter.close()
    sqlite_adapter.close()


def test_stock_calculation_event_order_determinism(golden_csv_dir):
    """
    Validate that events on the same day are applied in deterministic order.
    
    Event priority: SNAPSHOT (0) → ORDER/RECEIPT (1) → SALE/WASTE/ADJUST (2) → UNFULFILLED (3)
    """
    # Given: Storage adapter with golden dataset
    adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='csv')
    transactions = adapter.read_transactions()
    sales = adapter.read_sales()
    
    # When: Find days with multiple transactions for same SKU
    from collections import defaultdict
    events_by_sku_date = defaultdict(list)
    for txn in transactions:
        events_by_sku_date[(txn.sku, txn.date)].append(txn)
    
    multi_events = {k: v for k, v in events_by_sku_date.items() if len(v) > 1}
    
    # Then: If we have multi-event days, verify deterministic sorting
    if multi_events:
        for (sku_id, event_date), events in multi_events.items():
            # Calculate stock twice with shuffled events - should be identical
            from random import shuffle
            
            events_copy1 = list(events)
            events_copy2 = list(events)
            shuffle(events_copy2)
            
            # Calculate with original order
            stock1 = StockCalculator.calculate_asof(
                sku=sku_id,
                asof_date=event_date + __import__('datetime').timedelta(days=1),
                transactions=transactions,
                sales_records=sales
            )
            
            # Calculate with shuffled order (should be re-sorted deterministically)
            stock2 = StockCalculator.calculate_asof(
                sku=sku_id,
                asof_date=event_date + __import__('datetime').timedelta(days=1),
                transactions=[t if (t.sku, t.date) != (sku_id, event_date) else events_copy2.pop(0) for t in transactions],
                sales_records=sales
            )
            
            assert stock1.on_hand == stock2.on_hand, \
                f"[{sku_id}] Determinism broken: {stock1.on_hand} != {stock2.on_hand}"
    
    adapter.close()


def test_stock_calculation_asof_boundary(golden_csv_dir):
    """
    Validate AsOf date boundary: events with date < asof_date are included,
    events with date >= asof_date are excluded.
    """
    # Given: Storage adapter with golden dataset
    adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='csv')
    transactions = adapter.read_transactions()
    sales = adapter.read_sales()
    skus = adapter.read_skus()
    
    # Pick a SKU with transactions
    test_sku = next(s for s in skus if any(t.sku == s.sku for t in transactions))
    
    # Find transactions for this SKU
    sku_txns = [t for t in transactions if t.sku == test_sku.sku]
    sku_txns_sorted = sorted(sku_txns, key=lambda t: t.date)
    
    if len(sku_txns_sorted) >= 2:
        # Pick a date between two transactions
        mid_date = sku_txns_sorted[len(sku_txns_sorted) // 2].date
        
        # Calculate stock AsOf mid_date
        stock_mid = StockCalculator.calculate_asof(
            sku=test_sku.sku,
            asof_date=mid_date,
            transactions=transactions,
            sales_records=sales
        )
        
        # Calculate stock AsOf mid_date + 1 day
        stock_next = StockCalculator.calculate_asof(
            sku=test_sku.sku,
            asof_date=mid_date + __import__('datetime').timedelta(days=1),
            transactions=transactions,
            sales_records=sales
        )
        
        # Then: Stock should be different (mid_date stock excludes mid_date events)
        # (Only assert if there's an event ON mid_date)
        events_on_mid_date = [t for t in sku_txns if t.date == mid_date]
        if events_on_mid_date:
            # Stock should change because mid_date events are now included in stock_next
            assert (stock_mid.on_hand, stock_mid.on_order) != (stock_next.on_hand, stock_next.on_order), \
                f"Stock unchanged despite events on {mid_date}"
    
    adapter.close()


# ============================================================
# Test: SKU Data Equivalence
# ============================================================

def test_sku_data_equivalence(golden_csv_dir, golden_sqlite_db):
    """
    Validate that SKU data is identical between CSV and SQLite.
    
    Given: Golden dataset migrated to SQLite
    When: Read SKUs from CSV
    And: Read SKUs from SQLite
    Then: All SKU fields are identical
    """
    # Given: Load SKUs via CSV backend
    csv_adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='csv')
    csv_skus = csv_adapter.read_skus()
    
    # And: Load SKUs via SQLite backend
    sqlite_adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='sqlite')
    sqlite_adapter.conn = open_connection(golden_sqlite_db)
    from src.repositories import RepositoryFactory
    sqlite_adapter.repos = RepositoryFactory(sqlite_adapter.conn)
    sqlite_adapter.backend = 'sqlite'
    
    sqlite_skus = sqlite_adapter.read_skus()
    
    # Then: Same number of SKUs
    assert len(csv_skus) == len(sqlite_skus), \
        f"SKU count mismatch: CSV={len(csv_skus)}, SQLite={len(sqlite_skus)}"
    
    # And: All SKUs match by ID
    csv_sku_dict = {s.sku: s for s in csv_skus}
    sqlite_sku_dict = {s.sku: s for s in sqlite_skus}
    
    assert csv_sku_dict.keys() == sqlite_sku_dict.keys(), "SKU IDs don't match"
    
    # And: All fields match
    from dataclasses import asdict
    for sku_id in csv_sku_dict.keys():
        csv_sku = csv_sku_dict[sku_id]
        sqlite_sku = sqlite_sku_dict[sku_id]
        
        # Compare all fields
        csv_dict = asdict(csv_sku)
        sqlite_dict = asdict(sqlite_sku)
        
        assert csv_dict == sqlite_dict, \
            f"[{sku_id}] SKU data mismatch:\nCSV: {csv_dict}\nSQLite: {sqlite_dict}"
    
    # Cleanup
    csv_adapter.close()
    sqlite_adapter.close()


# ============================================================
# Test: Transaction Data Equivalence
# ============================================================

def test_transaction_data_equivalence(golden_csv_dir, golden_sqlite_db):
    """
    Validate that transaction data is identical between CSV and SQLite.
    
    Given: Golden dataset migrated to SQLite
    When: Read transactions from CSV
    And: Read transactions from SQLite
    Then: All transaction records are identical
    """
    # Given: Load transactions via CSV backend
    csv_adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='csv')
    csv_txns = csv_adapter.read_transactions()
    
    # And: Load transactions via SQLite backend
    sqlite_adapter = StorageAdapter(data_dir=golden_csv_dir, force_backend='sqlite')
    sqlite_adapter.conn = open_connection(golden_sqlite_db)
    from src.repositories import RepositoryFactory
    sqlite_adapter.repos = RepositoryFactory(sqlite_adapter.conn)
    sqlite_adapter.backend = 'sqlite'
    
    sqlite_txns = sqlite_adapter.read_transactions()
    
    # Then: Same number of transactions
    assert len(csv_txns) == len(sqlite_txns), \
        f"Transaction count mismatch: CSV={len(csv_txns)}, SQLite={len(sqlite_txns)}"
    
    # And: All transactions match (sorted for comparison)
    csv_txns_sorted = sorted(csv_txns, key=lambda t: (t.date, t.sku, t.event.value, t.qty))
    sqlite_txns_sorted = sorted(sqlite_txns, key=lambda t: (t.date, t.sku, t.event.value, t.qty))
    
    for i, (csv_txn, sqlite_txn) in enumerate(zip(csv_txns_sorted, sqlite_txns_sorted)):
        assert csv_txn.date == sqlite_txn.date, f"Transaction {i}: date mismatch"
        assert csv_txn.sku == sqlite_txn.sku, f"Transaction {i}: sku mismatch"
        assert csv_txn.event == sqlite_txn.event, f"Transaction {i}: event mismatch"
        assert csv_txn.qty == sqlite_txn.qty, f"Transaction {i}: qty mismatch"
        assert csv_txn.receipt_date == sqlite_txn.receipt_date, f"Transaction {i}: receipt_date mismatch"
    
    # Cleanup
    csv_adapter.close()
    sqlite_adapter.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
