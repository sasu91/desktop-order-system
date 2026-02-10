"""
Test real-time FEFO consumption on SALE/WASTE events.

Validates that FEFO is automatically applied when transactions are written
to ledger, not just during EOD workflow.
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Transaction, EventType, Lot
from src.persistence.csv_layer import CSVLayer
from src.workflows.receiving_v2 import ExceptionWorkflow


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def csv_layer(temp_data_dir):
    """Create CSV layer with temp directory."""
    return CSVLayer(temp_data_dir)  # Pass Path object directly



def test_auto_fefo_on_manual_waste(csv_layer):
    """
    Test that FEFO is automatically applied when WASTE transaction is written.
    
    Scenario:
    1. SKU with 100 units in 2 lots (expiring soon + fresh)
    2. Manual WASTE event for 30 units
    3. Verify FEFO consumed from expiring lot first
    4. Verify transaction note contains FEFO details
    """
    # Setup SKU
    sku = SKU(
        sku="TEST_AUTO_FEFO",
        description="Test Auto FEFO",
        ean="",
        shelf_life_days=60,
        min_shelf_life_days=7,
    )
    csv_layer.write_sku(sku)
    
    # Ledger: 100 units
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_AUTO_FEFO",
        event=EventType.SNAPSHOT,
        qty=100,
    )
    csv_layer.write_transaction(txn)
    
    # Create 2 lots:
    # Lot 1: 60 units, expiring in 10 days (soon)
    # Lot 2: 40 units, expiring in 50 days (fresh)
    lot1_receipt_date = date.today() - timedelta(days=50)
    lot1 = Lot(
        lot_id="LOT_EXPIRING",
        sku="TEST_AUTO_FEFO",
        expiry_date=lot1_receipt_date + timedelta(days=60),  # 10 days left
        qty_on_hand=60,
        receipt_id="REC_1",
        receipt_date=lot1_receipt_date,
    )
    csv_layer.write_lot(lot1)
    
    lot2_receipt_date = date.today() - timedelta(days=10)
    lot2 = Lot(
        lot_id="LOT_FRESH",
        sku="TEST_AUTO_FEFO",
        expiry_date=lot2_receipt_date + timedelta(days=60),  # 50 days left
        qty_on_hand=40,
        receipt_id="REC_2",
        receipt_date=lot2_receipt_date,
    )
    csv_layer.write_lot(lot2)
    
    # Record manual WASTE event (30 units)
    waste_txn = Transaction(
        date=date.today(),
        sku="TEST_AUTO_FEFO",
        event=EventType.WASTE,
        qty=30,
        note="Manual waste entry",
    )
    csv_layer.write_transaction(waste_txn)
    
    # Verify FEFO applied
    lots_after = csv_layer.get_lots_by_sku("TEST_AUTO_FEFO", sort_by_expiry=True)
    
    assert len(lots_after) == 2
    
    # Lot 1 (expiring soon) should be consumed first: 60 - 30 = 30
    assert lots_after[0].lot_id == "LOT_EXPIRING"
    assert lots_after[0].qty_on_hand == 30, f"Expected 30, got {lots_after[0].qty_on_hand}"
    
    # Lot 2 (fresh) should be unchanged: 40
    assert lots_after[1].lot_id == "LOT_FRESH"
    assert lots_after[1].qty_on_hand == 40
    
    # Verify transaction note contains FEFO details
    txns = csv_layer.read_transactions()
    waste_txns = [t for t in txns if t.event == EventType.WASTE]
    assert len(waste_txns) == 1
    
    waste_note = waste_txns[0].note
    assert "FEFO" in waste_note, f"Expected FEFO in note, got: {waste_note}"
    assert "LOT_EXPIRING" in waste_note
    assert "30pz" in waste_note


def test_auto_fefo_on_multi_lot_consumption(csv_layer):
    """
    Test FEFO consuming from multiple lots.
    
    Scenario:
    1. SKU with 3 lots (20, 30, 50 units, ordered by expiry)
    2. WASTE event for 60 units
    3. Verify consumes entire lot1 (20), entire lot2 (30), partial lot3 (10)
    """
    # Setup SKU
    sku = SKU(
        sku="TEST_MULTI_LOT",
        description="Test Multi Lot FEFO",
        ean="",
        shelf_life_days=60,
        min_shelf_life_days=7,
    )
    csv_layer.write_sku(sku)
    
    # Ledger: 100 units
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_MULTI_LOT",
        event=EventType.SNAPSHOT,
        qty=100,
    )
    csv_layer.write_transaction(txn)
    
    # Create 3 lots with different expiry dates
    base_date = date.today() - timedelta(days=50)
    
    lot1 = Lot(
        lot_id="LOT_1",
        sku="TEST_MULTI_LOT",
        expiry_date=base_date + timedelta(days=60),  # 10 days left
        qty_on_hand=20,
        receipt_id="REC_1",
        receipt_date=base_date,
    )
    csv_layer.write_lot(lot1)
    
    lot2 = Lot(
        lot_id="LOT_2",
        sku="TEST_MULTI_LOT",
        expiry_date=base_date + timedelta(days=70),  # 20 days left
        qty_on_hand=30,
        receipt_id="REC_2",
        receipt_date=base_date,
    )
    csv_layer.write_lot(lot2)
    
    lot3 = Lot(
        lot_id="LOT_3",
        sku="TEST_MULTI_LOT",
        expiry_date=base_date + timedelta(days=90),  # 40 days left
        qty_on_hand=50,
        receipt_id="REC_3",
        receipt_date=base_date,
    )
    csv_layer.write_lot(lot3)
    
    # Record WASTE event (60 units - should consume lot1 + lot2 + partial lot3)
    waste_txn = Transaction(
        date=date.today(),
        sku="TEST_MULTI_LOT",
        event=EventType.WASTE,
        qty=60,
        note="Multi-lot waste",
    )
    csv_layer.write_transaction(waste_txn)
    
    # Verify FEFO applied across multiple lots
    lots_after = csv_layer.get_lots_by_sku("TEST_MULTI_LOT", sort_by_expiry=True)
    
    # Note: get_lots_by_sku may filter out lots with qty_on_hand=0
    # So we expect only LOT_3 with qty=40 remaining
    assert len(lots_after) == 1, f"Expected 1 lot with qty>0, got {len(lots_after)}"
    
    # Lot 3: partially consumed (50 - 10 = 40)
    assert lots_after[0].lot_id == "LOT_3"
    assert lots_after[0].qty_on_hand == 40
    
    # Verify transaction note contains all 3 lots
    txns = csv_layer.read_transactions()
    waste_txns = [t for t in txns if t.event == EventType.WASTE]
    waste_note = waste_txns[0].note
    
    assert "LOT_1" in waste_note
    assert "LOT_2" in waste_note
    assert "LOT_3" in waste_note


def test_auto_fefo_skips_sku_without_lots(csv_layer):
    """
    Test that FEFO is skipped gracefully for SKUs without lot tracking.
    
    Scenario:
    1. SKU with no shelf life configured
    2. WASTE event written
    3. Verify transaction succeeds without FEFO (no crash)
    """
    # Setup SKU without shelf life (shelf_life_days=0 means no tracking)
    sku = SKU(
        sku="TEST_NO_LOTS",
        description="Test No Lots",
        ean="",
        shelf_life_days=0,  # No shelf life tracking
        min_shelf_life_days=0,
    )
    csv_layer.write_sku(sku)
    
    # Ledger: 50 units
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_NO_LOTS",
        event=EventType.SNAPSHOT,
        qty=50,
    )
    csv_layer.write_transaction(txn)
    
    # Record WASTE event
    waste_txn = Transaction(
        date=date.today(),
        sku="TEST_NO_LOTS",
        event=EventType.WASTE,
        qty=10,
        note="Waste without lots",
    )
    csv_layer.write_transaction(waste_txn)
    
    # Verify transaction written successfully
    txns = csv_layer.read_transactions()
    waste_txns = [t for t in txns if t.event == EventType.WASTE]
    assert len(waste_txns) == 1
    
    # Note should NOT contain FEFO details (SKU has no lots)
    waste_note = waste_txns[0].note
    assert "FEFO" not in waste_note


def test_auto_fefo_via_exception_workflow(csv_layer):
    """
    Test that FEFO is applied when using ExceptionWorkflow.record_exception().
    
    This validates integration between receiving workflows and auto-FEFO.
    """
    # Setup SKU
    sku = SKU(
        sku="TEST_EXCEPTION",
        description="Test Exception FEFO",
        ean="",
        shelf_life_days=60,
        min_shelf_life_days=7,
    )
    csv_layer.write_sku(sku)
    
    # Ledger: 80 units
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_EXCEPTION",
        event=EventType.SNAPSHOT,
        qty=80,
    )
    csv_layer.write_transaction(txn)
    
    # Create lot
    lot_receipt_date = date.today() - timedelta(days=45)
    lot = Lot(
        lot_id="LOT_EXCEPTION",
        sku="TEST_EXCEPTION",
        expiry_date=lot_receipt_date + timedelta(days=60),  # 15 days left
        qty_on_hand=80,
        receipt_id="REC_EXCEPTION",
        receipt_date=lot_receipt_date,
    )
    csv_layer.write_lot(lot)
    
    # Use ExceptionWorkflow to record WASTE
    workflow = ExceptionWorkflow(csv_layer)
    waste_txn, already_recorded = workflow.record_exception(
        event_type=EventType.WASTE,
        sku="TEST_EXCEPTION",
        qty=25,
        notes="Recorded via workflow",
    )
    
    assert not already_recorded
    
    # Verify FEFO applied
    lots_after = csv_layer.get_lots_by_sku("TEST_EXCEPTION")
    assert len(lots_after) == 1
    assert lots_after[0].qty_on_hand == 55  # 80 - 25
    
    # Verify transaction note contains FEFO
    txns = csv_layer.read_transactions()
    waste_txns = [t for t in txns if t.event == EventType.WASTE]
    waste_note = waste_txns[0].note
    
    assert "FEFO" in waste_note
    assert "LOT_EXCEPTION" in waste_note


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
