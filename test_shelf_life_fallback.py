"""
Test shelf life fallback logic for lots vs ledger discrepancy.
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Stock, Transaction, EventType, SalesRecord, Lot, DemandVariability
from src.persistence.csv_layer import CSVLayer
from src.workflows.order import OrderWorkflow


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


def test_fallback_when_lots_missing(temp_data_dir):
    """
    Test that reorder engine falls back to ledger stock when lots.csv is empty.
    
    Scenario:
    - Ledger shows on_hand = 50
    - No lots tracked (lots.csv empty)
    - Expected: usable_qty = 50, waste_risk_percent = 0.0
    """
    csv_layer = CSVLayer(data_dir=temp_data_dir)
    
    # Create SKU with shelf life
    sku = SKU(
        sku="TEST001",
        description="Test Product",
        shelf_life_days=30,
        min_shelf_life_days=7,
        lead_time_days=7,
        review_period=7,
        safety_stock=10,
    )
    csv_layer.write_sku(sku)
    
    # Create sales history FIRST
    for i in range(1, 31):
        sale = SalesRecord(
            date=date.today() - timedelta(days=30 - i + 1),
            sku="TEST001",
            qty_sold=2,
        )
        csv_layer.append_sales(sale)
    
    # Create ledger stock: SNAPSHOT 50 units (after sales period)
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST001",
        event=EventType.SNAPSHOT,
        qty=50,
    )
    csv_layer.write_transaction(txn)
    
    # No lots created → lots.csv empty for this SKU
    
    # Generate order proposal
    workflow = OrderWorkflow(csv_layer)
    
    # Get current stock
    from src.domain.ledger import StockCalculator
    transactions = csv_layer.read_transactions()
    sales_records = csv_layer.read_sales()
    current_stock = StockCalculator.calculate_asof(
        sku="TEST001",
        asof_date=date.today(),
        transactions=transactions,
        sales_records=sales_records,
    )
    
    proposal = workflow.generate_proposal(
        sku="TEST001",
        description="Test Product",
        current_stock=current_stock,
        daily_sales_avg=2.0,
        sku_obj=sku,
    )
    
    # ASSERTION: Should use ledger stock (48 = 50 - 2 for today's calculated sales), not lots (0)
    # Note: SNAPSHOT was yesterday (50), today's sales (2) were in history, so ledger shows 48
    assert proposal.usable_stock == 48, f"Expected usable_stock=48 (ledger fallback), got {proposal.usable_stock}"
    assert proposal.unusable_stock == 0, f"Expected unusable_stock=0, got {proposal.unusable_stock}"
    assert proposal.waste_risk_percent == 0.0, f"Expected waste_risk=0.0, got {proposal.waste_risk_percent}"
    
    # IP should be based on ledger stock
    assert proposal.inventory_position == 48, f"Expected IP=48, got {proposal.inventory_position}"


def test_fallback_when_lots_desynchronized(temp_data_dir):
    """
    Test fallback when lots.csv has significantly less stock than ledger.
    
    Scenario:
    - Ledger shows on_hand = 100
    - Lots show total = 30 (70% discrepancy, > 10% threshold)
    - Expected: fallback to ledger (100), waste_risk = 0.0
    """
    csv_layer = CSVLayer(data_dir=temp_data_dir)
    
    # Create SKU
    sku = SKU(
        sku="TEST002",
        description="Test Product 2",
        shelf_life_days=60,
        min_shelf_life_days=14,
        lead_time_days=7,
        review_period=7,
        safety_stock=20,
    )
    csv_layer.write_sku(sku)
    
    # Sales history FIRST
    for i in range(1, 31):
        sale = SalesRecord(
            date=date.today() - timedelta(days=30 - i + 1),
            sku="TEST002",
            qty_sold=3,
        )
        csv_layer.append_sales(sale)
    
    # Ledger: SNAPSHOT 100 units (after sales period)
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST002",
        event=EventType.SNAPSHOT,
        qty=100,
    )
    csv_layer.write_transaction(txn)
    
    # Create partial lot tracking (only 30 units) - created before snapshot
    lot1 = Lot(
        lot_id="LOT001",
        sku="TEST002",
        expiry_date=date.today() + timedelta(days=45),
        qty_on_hand=30,
        receipt_id="REC001",
        receipt_date=date.today() - timedelta(days=5),
    )
    csv_layer.write_lot(lot1)
    
    # Generate proposal
    workflow = OrderWorkflow(csv_layer)
    
    from src.domain.ledger import StockCalculator
    transactions = csv_layer.read_transactions()
    sales_records = csv_layer.read_sales()
    current_stock = StockCalculator.calculate_asof(
        sku="TEST002",
        asof_date=date.today(),
        transactions=transactions,
        sales_records=sales_records,
    )
    
    proposal = workflow.generate_proposal(
        sku="TEST002",
        description="Test Product 2",
        current_stock=current_stock,
        daily_sales_avg=3.0,
        sku_obj=sku,
    )
    
    # ASSERTION: Should fallback to ledger (97 = 100 - 3 for today), not lots (30)
    # Note: SNAPSHOT was yesterday (100), today's sales (3) were in history, so ledger shows 97
    assert proposal.usable_stock == 97, f"Expected usable_stock=97 (ledger fallback), got {proposal.usable_stock}"
    assert proposal.waste_risk_percent == 0.0, f"Expected waste_risk=0.0, got {proposal.waste_risk_percent}"
    
    # IP should be based on ledger stock
    # IP = on_hand (97) + on_order (0) - unfulfilled (0) = 97
    assert proposal.inventory_position == 97, f"Expected IP=97, got {proposal.inventory_position}"


def test_normal_shelf_life_when_lots_synchronized(temp_data_dir):
    """
    Test that shelf life calculations work normally when lots match ledger.
    
    Scenario:
    - Ledger shows on_hand = 50
    - Lots show total = 50 (synchronized)
    - Some lots expiring soon → waste risk > 0
    - Expected: use shelf life calculations normally
    """
    csv_layer = CSVLayer(data_dir=temp_data_dir)
    
    # Create SKU
    sku = SKU(
        sku="TEST003",
        description="Test Product 3",
        shelf_life_days=30,
        min_shelf_life_days=7,
        lead_time_days=7,
        review_period=7,
        safety_stock=10,
    )
    csv_layer.write_sku(sku)
    
    # Sales history FIRST
    for i in range(1, 31):
        sale = SalesRecord(
            date=date.today() - timedelta(days=30 - i + 1),
            sku="TEST003",
            qty_sold=2,
        )
        csv_layer.append_sales(sale)
    
    # Ledger: SNAPSHOT 50 units (after sales)
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST003",
        event=EventType.SNAPSHOT,
        qty=50,
    )
    csv_layer.write_transaction(txn)
    
    # Create lots matching ledger (created before snapshot)
    # Lot 1: 20 units expiring in 5 days (unusable: < min_shelf_life)
    lot1 = Lot(
        lot_id="LOT001",
        sku="TEST003",
        expiry_date=date.today() + timedelta(days=5),
        qty_on_hand=20,
        receipt_id="REC001",
        receipt_date=date.today() - timedelta(days=5),
    )
    csv_layer.write_lot(lot1)
    
    # Lot 2: 15 units expiring in 10 days (usable but waste risk)
    lot2 = Lot(
        lot_id="LOT002",
        sku="TEST003",
        expiry_date=date.today() + timedelta(days=10),
        qty_on_hand=15,
        receipt_id="REC002",
        receipt_date=date.today() - timedelta(days=3),
    )
    csv_layer.write_lot(lot2)
    
    # Lot 3: 15 units expiring in 25 days (usable, no waste risk)
    lot3 = Lot(
        lot_id="LOT003",
        sku="TEST003",
        expiry_date=date.today() + timedelta(days=25),
        qty_on_hand=15,
        receipt_id="REC003",
        receipt_date=date.today() - timedelta(days=2),
    )
    csv_layer.write_lot(lot3)
    
    # Generate proposal
    workflow = OrderWorkflow(csv_layer)
    
    from src.domain.ledger import StockCalculator
    transactions = csv_layer.read_transactions()
    sales_records = csv_layer.read_sales()
    current_stock = StockCalculator.calculate_asof(
        sku="TEST003",
        asof_date=date.today(),
        transactions=transactions,
        sales_records=sales_records,
    )
    
    proposal = workflow.generate_proposal(
        sku="TEST003",
        description="Test Product 3",
        current_stock=current_stock,
        daily_sales_avg=2.0,
        sku_obj=sku,
    )
    
    # ASSERTION: Should use shelf life calculations (not fallback)
    # Usable: Lot2 (15) + Lot3 (15) = 30 (Lot1 unusable: days_left < min_shelf_life)
    assert proposal.usable_stock == 30, f"Expected usable_stock=30, got {proposal.usable_stock}"
    assert proposal.unusable_stock == 20, f"Expected unusable_stock=20, got {proposal.unusable_stock}"
    
    # Waste risk: Lot2 (15 units) is in waste horizon (expiring ≤ 14 days)
    # waste_risk = 15 / 50 * 100 = 30%
    assert proposal.waste_risk_percent > 0, f"Expected waste_risk>0, got {proposal.waste_risk_percent}"
    assert 25 <= proposal.waste_risk_percent <= 35, f"Expected waste_risk≈30%, got {proposal.waste_risk_percent}"
    
    # IP should be based on usable stock (30), not total (50)
    assert proposal.inventory_position == 30, f"Expected IP=30 (usable only), got {proposal.inventory_position}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
