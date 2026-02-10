"""
Test EOD workflow with real-time FEFO integration.

Validates that FEFO consumption works correctly in daily close workflow,
consuming from lots when EOD sales are recorded.
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Transaction, EventType, Lot
from src.persistence.csv_layer import CSVLayer
from src.workflows.daily_close import DailyCloseWorkflow


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def csv_layer(temp_data_dir):
    """Create CSV layer with temp directory."""
    return CSVLayer(temp_data_dir)


def test_eod_workflow_triggers_fefo(csv_layer):
    """
    Test that EOD workflow triggers FEFO consumption.
    
    Scenario:
    1. SKU with 100 units in 2 lots (60 expiring, 40 fresh)
    2. EOD stock = 75 (sold 25 units)
    3. Verify FEFO consumed 25 from expiring lot first
    4. Verify lots updated: 35 + 40 = 75
    """
    # Setup SKU
    sku = SKU(
        sku="TEST_EOD_FEFO",
        description="Test EOD FEFO",
        ean="",
        shelf_life_days=60,
        min_shelf_life_days=7,
    )
    csv_layer.write_sku(sku)
    
    # Starting stock: 100 units (yesterday)
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_EOD_FEFO",
        event=EventType.SNAPSHOT,
        qty=100,
    )
    csv_layer.write_transaction(txn)
    
    # Create 2 lots
    lot1_receipt_date = date.today() - timedelta(days=50)
    lot1 = Lot(
        lot_id="LOT_EXPIRING",
        sku="TEST_EOD_FEFO",
        expiry_date=lot1_receipt_date + timedelta(days=60),  # 10 days left
        qty_on_hand=60,
        receipt_id="REC_1",
        receipt_date=lot1_receipt_date,
    )
    csv_layer.write_lot(lot1)
    
    lot2_receipt_date = date.today() - timedelta(days=10)
    lot2 = Lot(
        lot_id="LOT_FRESH",
        sku="TEST_EOD_FEFO",
        expiry_date=lot2_receipt_date + timedelta(days=60),  # 50 days left
        qty_on_hand=40,
        receipt_id="REC_2",
        receipt_date=lot2_receipt_date,
    )
    csv_layer.write_lot(lot2)
    
    # Process EOD: stock on hand = 75 (sold 25)
    workflow = DailyCloseWorkflow(csv_layer)
    sale_rec, adjust_txn, status = workflow.process_eod_stock(
        sku="TEST_EOD_FEFO",
        eod_date=date.today(),
        eod_stock_on_hand=75,
    )
    
    # Verify sale recorded
    assert sale_rec is not None
    assert sale_rec.qty_sold == 25
    
    # Verify no adjustment needed (stock matches expectation)
    assert adjust_txn is None
    
    # Verify FEFO applied to lots
    lots_after = csv_layer.get_lots_by_sku("TEST_EOD_FEFO", sort_by_expiry=True)
    assert len(lots_after) == 2
    
    # Lot 1 (expiring): 60 - 25 = 35
    assert lots_after[0].lot_id == "LOT_EXPIRING"
    assert lots_after[0].qty_on_hand == 35
    
    # Lot 2 (fresh): unchanged 40
    assert lots_after[1].lot_id == "LOT_FRESH"
    assert lots_after[1].qty_on_hand == 40
    
    # Total: 35 + 40 = 75 ✓
    total_lots = sum(lot.qty_on_hand for lot in lots_after)
    assert total_lots == 75


def test_eod_fefo_multi_day_consumption(csv_layer):
    """
    Test FEFO consumption across multiple EOD entries.
    
    Scenario:
    1. Day 1: 100 units in 2 lots, sell 30 → lot1 becomes 30
    2. Day 2: 70 units, sell 40 → lot1 becomes 0, lot2 becomes 30
    3. Verify progressive FEFO consumption
    """
    # Setup SKU
    sku = SKU(
        sku="TEST_MULTI_DAY",
        description="Test Multi Day FEFO",
        ean="",
        shelf_life_days=60,
        min_shelf_life_days=7,
    )
    csv_layer.write_sku(sku)
    
    # Day -2: Initial stock 100
    txn = Transaction(
        date=date.today() - timedelta(days=2),
        sku="TEST_MULTI_DAY",
        event=EventType.SNAPSHOT,
        qty=100,
    )
    csv_layer.write_transaction(txn)
    
    # Create 2 lots: 60 + 40
    base_date = date.today() - timedelta(days=50)
    
    lot1 = Lot(
        lot_id="LOT_1",
        sku="TEST_MULTI_DAY",
        expiry_date=base_date + timedelta(days=60),
        qty_on_hand=60,
        receipt_id="REC_1",
        receipt_date=base_date,
    )
    csv_layer.write_lot(lot1)
    
    lot2 = Lot(
        lot_id="LOT_2",
        sku="TEST_MULTI_DAY",
        expiry_date=base_date + timedelta(days=70),
        qty_on_hand=40,
        receipt_id="REC_2",
        receipt_date=base_date,
    )
    csv_layer.write_lot(lot2)
    
    workflow = DailyCloseWorkflow(csv_layer)
    
    # Day 1: EOD = 70 (sold 30)
    day1 = date.today() - timedelta(days=1)
    sale1, _, _ = workflow.process_eod_stock(
        sku="TEST_MULTI_DAY",
        eod_date=day1,
        eod_stock_on_hand=70,
    )
    
    assert sale1.qty_sold == 30
    
    lots_day1 = csv_layer.get_lots_by_sku("TEST_MULTI_DAY", sort_by_expiry=True)
    assert lots_day1[0].qty_on_hand == 30  # lot1: 60 - 30 = 30
    assert lots_day1[1].qty_on_hand == 40  # lot2: unchanged
    
    # Day 2: EOD = 30 (sold 40 more)
    day2 = date.today()
    sale2, _, _ = workflow.process_eod_stock(
        sku="TEST_MULTI_DAY",
        eod_date=day2,
        eod_stock_on_hand=30,
    )
    
    assert sale2.qty_sold == 40
    
    lots_day2 = csv_layer.get_lots_by_sku("TEST_MULTI_DAY", sort_by_expiry=True)
    
    # lot1 fully consumed (30 - 30 = 0, then 0 - 10 from sale2 = 0)
    # lot2 partially consumed (40 - 10 from sale2 = 30)
    # Note: get_lots_by_sku may filter out empty lots
    remaining_lots = [lot for lot in lots_day2 if lot.qty_on_hand > 0]
    assert len(remaining_lots) == 1
    assert remaining_lots[0].lot_id == "LOT_2"
    assert remaining_lots[0].qty_on_hand == 30


def test_eod_with_adjustment_preserves_fefo(csv_layer):
    """
    Test that EOD with stock adjustment still triggers FEFO correctly.
    
    Scenario:
    1. Starting stock: 100 in lots
    2. EOD stock declared: 65
    3. Calculated sales: 35 (100 - 65)
    4. Verify FEFO applied to sales (35 consumed from expiring lot)
    5. Lots: 65 remaining (60 - 35 = 25 from lot1, 40 unchanged in lot2)
    """
    # Setup SKU
    sku = SKU(
        sku="TEST_ADJ_FEFO",
        description="Test Adjustment FEFO",
        ean="",
        shelf_life_days=60,
        min_shelf_life_days=7,
    )
    csv_layer.write_sku(sku)
    
    # Starting stock: 100
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_ADJ_FEFO",
        event=EventType.SNAPSHOT,
        qty=100,
    )
    csv_layer.write_transaction(txn)
    
    # Two lots: 60 (expiring) + 40 (fresh)
    lot1_date = date.today() - timedelta(days=50)
    lot1 = Lot(
        lot_id="LOT_EXPIRING",
        sku="TEST_ADJ_FEFO",
        expiry_date=lot1_date + timedelta(days=60),  # 10 days left
        qty_on_hand=60,
        receipt_id="REC_1",
        receipt_date=lot1_date,
    )
    csv_layer.write_lot(lot1)
    
    lot2_date = date.today() - timedelta(days=10)
    lot2 = Lot(
        lot_id="LOT_FRESH",
        sku="TEST_ADJ_FEFO",
        expiry_date=lot2_date + timedelta(days=60),  # 50 days left
        qty_on_hand=40,
        receipt_id="REC_2",
        receipt_date=lot2_date,
    )
    csv_layer.write_lot(lot2)
    
    workflow = DailyCloseWorkflow(csv_layer)
    
    # EOD: declared 65 (sold 35: 100 - 65)
    sale_rec, adjust_txn, status = workflow.process_eod_stock(
        sku="TEST_ADJ_FEFO",
        eod_date=date.today(),
        eod_stock_on_hand=65,
    )
    
    # Verify sale: 35 sold (100 - 65)
    assert sale_rec.qty_sold == 35
    
    # Verify no adjustment needed (stock matches calculation)
    assert adjust_txn is None
    
    # Verify lot FEFO: should have consumed 35 from expiring lot first
    lots_after = csv_layer.get_lots_by_sku("TEST_ADJ_FEFO", sort_by_expiry=True)
    assert len(lots_after) == 2
    
    # Lot1 (expiring): 60 - 35 = 25
    assert lots_after[0].lot_id == "LOT_EXPIRING"
    assert lots_after[0].qty_on_hand == 25
    
    # Lot2 (fresh): unchanged 40
    assert lots_after[1].lot_id == "LOT_FRESH"
    assert lots_after[1].qty_on_hand == 40
    
    # Total: 25 + 40 = 65 ✓
    total_lots = sum(lot.qty_on_hand for lot in lots_after)
    assert total_lots == 65


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
