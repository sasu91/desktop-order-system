"""
Test forward-looking waste risk calculation.

Validates that the reorder engine correctly projects waste risk
to the receipt_date including the incoming order, rather than
using only current stock.
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Stock, Transaction, EventType, SalesRecord, Lot, DemandVariability
from src.persistence.csv_layer import CSVLayer
from src.workflows.order import OrderWorkflow
from src.domain.ledger import StockCalculator, ShelfLifeCalculator


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


def test_forward_waste_risk_dilution_effect(temp_data_dir):
    """
    Test that forward waste risk correctly shows dilution effect of new order.
    
    Scenario:
    - TODAY: 50 units with 10 days shelf life left → 100% waste risk (all in window)
    - ORDER: 100 units arriving in 7 days with 30 days shelf life
    - AT RECEIPT (7 days later):
      * Old stock: 50 units with 3 days left (still in waste window)
      * New stock: 100 units with 30 days left (NOT in waste window)
      * Forward waste risk = 50/150 = 33.3%, not 100%
    
    Expected:
    - Current waste_risk_percent = 100%
    - Forward waste_risk_percent ≈ 33%
    - Penalty decision based on forward risk (should NOT trigger if threshold > 33%)
    """
    csv_layer = CSVLayer(data_dir=temp_data_dir)
    
    # Create SKU with 30-day shelf life
    sku = SKU(
        sku="TEST_DILUTION",
        description="Test Dilution Effect",
        shelf_life_days=30,
        min_shelf_life_days=7,
        lead_time_days=7,
        review_period=7,
        safety_stock=10,
        waste_penalty_mode="soft",
        waste_penalty_factor=0.5,
        waste_risk_threshold=40.0,  # Set threshold at 40% (should NOT trigger with forward risk)
    )
    csv_layer.write_sku(sku)
    
    # Create sales history
    for i in range(1, 31):
        sale = SalesRecord(
            date=date.today() - timedelta(days=30 - i + 1),
            sku="TEST_DILUTION",
            qty_sold=5,
        )
        csv_layer.append_sales(sale)
    
    # Create ledger stock: SNAPSHOT 50 units (yesterday)
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_DILUTION",
        event=EventType.SNAPSHOT,
        qty=50,
    )
    csv_layer.write_transaction(txn)
    
    # Create lot: 50 units expiring in 10 days (received 20 days ago)
    lot_receipt_date = date.today() - timedelta(days=20)
    lot = Lot(
        lot_id="LOT_OLD",
        sku="TEST_DILUTION",
        expiry_date=lot_receipt_date + timedelta(days=30),  # 30 days shelf life from receipt
        qty_on_hand=50,
        receipt_id="REC_OLD",
        receipt_date=lot_receipt_date,
    )
    csv_layer.write_lot(lot)
    
    # Generate order proposal
    workflow = OrderWorkflow(csv_layer)
    
    from src.domain.ledger import StockCalculator
    transactions = csv_layer.read_transactions()
    sales_records = csv_layer.read_sales()
    current_stock = StockCalculator.calculate_asof(
        sku="TEST_DILUTION",
        asof_date=date.today(),
        transactions=transactions,
        sales_records=sales_records,
    )
    
    proposal = workflow.generate_proposal(
        sku="TEST_DILUTION",
        description="Test Dilution Effect",
        current_stock=current_stock,
        daily_sales_avg=5.0,
        sku_obj=sku,
    )
    
    # ASSERTIONS
    # Current waste risk should be high (lot expiring soon)
    assert proposal.waste_risk_percent > 90, \
        f"Expected current waste_risk > 90%, got {proposal.waste_risk_percent}%"
    
    # Forward waste risk should be much lower (diluted by new order)
    assert proposal.waste_risk_forward_percent < 50, \
        f"Expected forward waste_risk < 50%, got {proposal.waste_risk_forward_percent}%"
    
    # Forward risk should be significantly less than current risk
    assert proposal.waste_risk_forward_percent < proposal.waste_risk_percent / 2, \
        f"Forward risk should be < half of current risk"
    
    # Penalty should NOT be applied (forward risk < threshold of 40%)
    assert not proposal.shelf_life_penalty_applied, \
        f"Penalty should NOT apply with forward risk {proposal.waste_risk_forward_percent}% < 40%"
    
    # Proposed qty should be > 0 (not blocked)
    assert proposal.proposed_qty > 0, \
        f"Proposal should not be blocked with forward waste risk < threshold"
    
    print(f"✓ Current waste risk: {proposal.waste_risk_percent:.1f}%")
    print(f"✓ Forward waste risk: {proposal.waste_risk_forward_percent:.1f}%")
    print(f"✓ Penalty applied: {proposal.shelf_life_penalty_applied}")
    print(f"✓ Proposed qty: {proposal.proposed_qty}")


def test_forward_waste_risk_penalty_avoidance(temp_data_dir):
    """
    Test that forward-looking calculation prevents unnecessary penalties.
    
    Scenario:
    - Current stock has high waste risk (80%)
    - But incoming order will dilute it to acceptable levels (25%)
    - Penalty threshold = 50%
    
    Expected:
    - Old behavior (current risk): would apply penalty
    - New behavior (forward risk): NO penalty applied
    """
    csv_layer = CSVLayer(data_dir=temp_data_dir)
    
    sku = SKU(
        sku="TEST_AVOID_PENALTY",
        description="Test Penalty Avoidance",
        shelf_life_days=60,
        min_shelf_life_days=14,
        lead_time_days=7,
        review_period=7,
        safety_stock=20,
        waste_penalty_mode="soft",
        waste_penalty_factor=0.5,
        waste_risk_threshold=50.0,  # 50% threshold
    )
    csv_layer.write_sku(sku)
    
    # Sales history
    for i in range(1, 31):
        sale = SalesRecord(
            date=date.today() - timedelta(days=30 - i + 1),
            sku="TEST_AVOID_PENALTY",
            qty_sold=3,
        )
        csv_layer.append_sales(sale)
    
    # Ledger: 40 units
    txn = Transaction(
        date=date.today() - timedelta(days=1),
        sku="TEST_AVOID_PENALTY",
        event=EventType.SNAPSHOT,
        qty=40,
    )
    csv_layer.write_transaction(txn)
    
    # Create lot: 40 units expiring in 20 days (within waste window but usable)
    # waste_horizon = 14 days, so days_left should be > min_shelf_life (14) but <= waste_horizon (14)
    # Actually, we need days_left > min_shelf_life AND <= waste_horizon
    # So: min_shelf_life=14, waste_horizon=14 → impossible to be in waste window!
    # Fix: use min_shelf_life=7, waste_horizon=14, and lot expiring in 12 days
    
    # Update SKU to have min_shelf_life=7 instead of 14
    sku = SKU(
        sku="TEST_AVOID_PENALTY",
        description="Test Penalty Avoidance",
        shelf_life_days=60,
        min_shelf_life_days=7,  # Changed from 14 to 7
        lead_time_days=7,
        review_period=7,
        safety_stock=20,
        waste_penalty_mode="soft",
        waste_penalty_factor=0.5,
        waste_risk_threshold=50.0,  # 50% threshold
    )
    csv_layer.write_sku(sku)
    
    # Sales history (before snapshot to avoid desync)
    for i in range(1, 29):  # 28 days of sales (not including today-1 and today)
        sale = SalesRecord(
            date=date.today() - timedelta(days=30 - i),
            sku="TEST_AVOID_PENALTY",
            qty_sold=3,
        )
        csv_layer.append_sales(sale)
    
    # Ledger: 40 units - set AFTER sales to avoid lots/ledger desync
    txn = Transaction(
        date=date.today(),  # Today's snapshot
        sku="TEST_AVOID_PENALTY",
        event=EventType.SNAPSHOT,
        qty=40,
    )
    csv_layer.write_transaction(txn)
    
    # Create lot: 40 units expiring in 12 days (within waste window of 14 days, but > min_shelf_life 7)
    lot_receipt_date = date.today() - timedelta(days=48)
    lot = Lot(
        lot_id="LOT_EXPIRING",
        sku="TEST_AVOID_PENALTY",
        expiry_date=lot_receipt_date + timedelta(days=60),  # Expiring in 12 days from today
        qty_on_hand=40,  # Match the snapshot
        receipt_id="REC_EXPIRING",
        receipt_date=lot_receipt_date,
    )
    csv_layer.write_lot(lot)
    
    # Generate proposal
    workflow = OrderWorkflow(csv_layer)
    
    transactions = csv_layer.read_transactions()
    sales_records = csv_layer.read_sales()
    current_stock = StockCalculator.calculate_asof(
        sku="TEST_AVOID_PENALTY",
        asof_date=date.today(),
        transactions=transactions,
        sales_records=sales_records,
    )
    
    proposal = workflow.generate_proposal(
        sku="TEST_AVOID_PENALTY",
        description="Test Penalty Avoidance",
        current_stock=current_stock,
        daily_sales_avg=3.0,
        sku_obj=sku,
    )
    
    # ASSERTIONS
    # Current waste risk should be high
    assert proposal.waste_risk_percent > 70, \
        f"Expected current waste_risk > 70%, got {proposal.waste_risk_percent}%"
    
    # Forward waste risk should be much lower (big order dilutes old stock)
    assert proposal.waste_risk_forward_percent < 50, \
        f"Expected forward waste_risk < 50%, got {proposal.waste_risk_forward_percent}%"
    
    # NO penalty should be applied (forward risk < 50% threshold)
    assert not proposal.shelf_life_penalty_applied, \
        "Penalty should NOT apply when forward risk < threshold"
    
    print(f"✓ High current waste risk ({proposal.waste_risk_percent:.1f}%) avoided penalty")
    print(f"✓ Forward waste risk: {proposal.waste_risk_forward_percent:.1f}%")


def test_forward_calculation_direct(temp_data_dir):
    """
    Direct test of ShelfLifeCalculator.calculate_forward_waste_risk method.
    """
    csv_layer = CSVLayer(data_dir=temp_data_dir)
    
    # Create scenario: 30 units expiring in 8 days
    lot1 = Lot(
        lot_id="LOT1",
        sku="TEST",
        expiry_date=date.today() + timedelta(days=8),
        qty_on_hand=30,
        receipt_id="REC1",
        receipt_date=date.today() - timedelta(days=22),
    )
    
    lots = [lot1]
    
    # Test 1: No incoming order (baseline)
    waste_risk_no_order, total_no_order, expiring_no_order = ShelfLifeCalculator.calculate_forward_waste_risk(
        lots=lots,
        current_date=date.today(),
        receipt_date=date.today() + timedelta(days=7),  # 7 days forward
        proposed_qty=0,
        sku_shelf_life_days=30,
        min_shelf_life_days=7,
        waste_horizon_days=14,
    )
    
    # After 7 days, lot will have 1 day left (< min_shelf_life=7) → unusable
    # waste_risk should be 0 (lot is unusable, not expiring_soon)
    assert total_no_order == 30, "Total should be 30 units"
    assert waste_risk_no_order == 0.0, f"Expected 0% waste risk (lot unusable), got {waste_risk_no_order}%"
    
    # Test 2: With incoming order of 120 units
    waste_risk_with_order, total_with_order, expiring_with_order = ShelfLifeCalculator.calculate_forward_waste_risk(
        lots=lots,
        current_date=date.today(),
        receipt_date=date.today() + timedelta(days=7),
        proposed_qty=120,
        sku_shelf_life_days=30,
        min_shelf_life_days=7,
        waste_horizon_days=14,
    )
    
    # Total = 30 (old unusable) + 120 (new) = 150
    # Old lot: 1 day left → unusable (not in waste window)
    # New lot: 30 days left → usable, not expiring soon
    # waste_risk ≈ 0%
    assert total_with_order == 150, f"Expected 150 total, got {total_with_order}"
    assert waste_risk_with_order == 0.0, f"Expected 0% waste risk, got {waste_risk_with_order}%"
    
    print(f"✓ Direct calculation validated")
    print(f"  No order: waste_risk={waste_risk_no_order:.1f}%, total={total_no_order}")
    print(f"  With order: waste_risk={waste_risk_with_order:.1f}%, total={total_with_order}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
