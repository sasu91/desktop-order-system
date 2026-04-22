"""
Tests for projected inventory position with forecast sales and expiring lots.

Verifies that calendar-aware proposals correctly account for:
1. Forecast sales between today and target_receipt_date
2. Lots expiring between today and target_receipt_date
3. Orders arriving between today and target_receipt_date
"""
from datetime import date, timedelta
from pathlib import Path
import pytest

from src.domain.models import SKU, Transaction, EventType, Stock, SalesRecord, DemandVariability, Lot
from src.domain.ledger import StockCalculator
from src.workflows.order import OrderWorkflow
from src.persistence.csv_layer import CSVLayer


def test_projected_ip_subtracts_forecast_sales():
    """
    Test that projected_inventory_position subtracts forecast sales.
    
    Scenario:
    - Today: 2026-02-11 (Tuesday)
    - on_hand=100
    - Forecast: 10 pz/day
    - Target: Friday (2026-02-14, 3 days later)
    - No orders
    
    Expected:
    - Projected IP(Friday) = 100 - 30 (Tue+Wed+Thu sales) = 70
    """
    sku = "TEST_SKU"
    today = date(2026, 2, 11)  # Tuesday
    friday = date(2026, 2, 14)  # 3 days later
    
    transactions = [
        Transaction(date=date(2026, 2, 10), sku=sku, event=EventType.SNAPSHOT, qty=100)
    ]
    
    current_stock = Stock(sku=sku, on_hand=100, on_order=0, unfulfilled_qty=0, asof_date=today)
    
    projected_ip = StockCalculator.projected_inventory_position(
        sku=sku,
        target_date=friday,
        current_stock=current_stock,
        transactions=transactions,
        daily_sales_forecast=10.0
    )
    
    expected_ip = 100 - 30  # 100 - (3 days × 10 pz/day)
    assert projected_ip == expected_ip, f"Expected {expected_ip}, got {projected_ip}"


def test_projected_ip_adds_receipts_before_target():
    """
    Test that projected IP adds receipts arriving before target date.
    
    Scenario:
    - Today: 2026-02-11 (Tuesday)
    - on_hand=50
    - Forecast: 10 pz/day
    - Wednesday order: 30 pz (receipt_date=Wed)
    - Friday order: 50 pz (receipt_date=Fri)
    - Target: Friday
    
    Expected:
    - Projected IP(Friday) = 50 - 30 (3 days sales) + 30 (Wed received) + 50 (Fri pending) = 100
    """
    sku = "TEST_SKU"
    today = date(2026, 2, 11)  # Tuesday
    wednesday = date(2026, 2, 12)
    friday = date(2026, 2, 14)
    
    transactions = [
        Transaction(date=date(2026, 2, 10), sku=sku, event=EventType.SNAPSHOT, qty=50),
        Transaction(date=today, sku=sku, event=EventType.ORDER, qty=30, receipt_date=wednesday),
        Transaction(date=today, sku=sku, event=EventType.ORDER, qty=50, receipt_date=friday),
    ]
    
    current_stock = Stock(sku=sku, on_hand=50, on_order=80, unfulfilled_qty=0, asof_date=today)
    
    projected_ip = StockCalculator.projected_inventory_position(
        sku=sku,
        target_date=friday,
        current_stock=current_stock,
        transactions=transactions,
        daily_sales_forecast=10.0
    )
    
    # Projected on_hand = 50 - 30 (sales) + 30 (Wed receipt) = 50
    # On_order at Friday = 50 (Fri order)
    # IP = 50 + 50 = 100
    expected_ip = 100
    assert projected_ip == expected_ip, f"Expected {expected_ip}, got {projected_ip}"


def test_projected_ip_monday_vs_saturday():
    """
    Test that Monday IP is different from Saturday IP due to forecast sales.
    
    Scenario (Friday dual order):
    - Today: 2026-02-11 (Tuesday)
    - on_hand: 50
    - Forecast: 10 pz/day
    - Wednesday order: 30 pz
    - Friday order: 50 pz (to be calculated)
    
    Expected:
    - IP(Wednesday) = 50 - 20 (Tue+Wed sales) + 30 (Wed pending) = 60
    - IP(Friday) = 50 - 40 (4 days sales) + 30 (Wed received) + 50 (Fri pending) = 90
    """
    sku = "TEST_SKU"
    today = date(2026, 2, 11)  # Tuesday
    wednesday = date(2026, 2, 12)
    friday = date(2026, 2, 14)
    
    transactions = [
        Transaction(date=date(2026, 2, 10), sku=sku, event=EventType.SNAPSHOT, qty=50),
        Transaction(date=today, sku=sku, event=EventType.ORDER, qty=30, receipt_date=wednesday),
        Transaction(date=today, sku=sku, event=EventType.ORDER, qty=50, receipt_date=friday),
    ]
    
    current_stock = Stock(sku=sku, on_hand=50, on_order=80, unfulfilled_qty=0, asof_date=today)
    
    # Wednesday projection
    projected_ip_wed = StockCalculator.projected_inventory_position(
        sku=sku,
        target_date=wednesday,
        current_stock=current_stock,
        transactions=transactions,
        daily_sales_forecast=10.0
    )
    
    # Projected on_hand = 50 - 10 (Tue sales) = 40
    # On_order at Wednesday = 30 (Wed) + 50 (Fri) = 80
    # IP = 40 + 80 = 120
    expected_ip_wed = 120
    assert projected_ip_wed == expected_ip_wed, f"Wednesday IP: expected {expected_ip_wed}, got {projected_ip_wed}"
    
    # Friday projection
    projected_ip_fri = StockCalculator.projected_inventory_position(
        sku=sku,
        target_date=friday,
        current_stock=current_stock,
        transactions=transactions,
        daily_sales_forecast=10.0
    )
    
    # Projected on_hand = 50 - 30 (3 day sales) + 30 (Wed received) = 50
    # On_order at Friday = 50 (Fri)
    # IP = 50 + 50 = 100
    expected_ip_fri = 100
    assert projected_ip_fri == expected_ip_fri, f"Friday IP: expected {expected_ip_fri}, got {projected_ip_fri}"
    
    # Friday IP should be lower than Wednesday IP due to more forecast sales
    assert projected_ip_fri < projected_ip_wed, "Friday IP should be lower than Wednesday IP"


def test_proposal_with_forecast_sales_integration():
    """
    Integration test: verify that generate_proposal uses projected IP with forecast sales.
    
    Scenario:
    - Today: 2026-02-11 (Tuesday)
    - Wednesday delivery and Friday delivery
    - Friday proposal should account for weekday sales
    """
    import tempfile
    test_dir = tempfile.mkdtemp()
    
    try:
        csv_layer = CSVLayer(data_dir=Path(test_dir))
        workflow = OrderWorkflow(csv_layer=csv_layer, lead_time_days=1)
        
        sku_id = "SKU_SALES"
        sku = SKU(
            sku=sku_id,
            description="Sales Forecast Test",
            ean="",
            moq=10,
            pack_size=10,
            lead_time_days=1,
            review_period=7,
            safety_stock=120,  # Higher safety to trigger ordering
            shelf_life_days=0,
            max_stock=500,
            reorder_point=100,
            demand_variability=DemandVariability.LOW,
            in_assortment=True
        )
        csv_layer.write_sku(sku)
        
        # Initial stock: 100 pz
        transactions = [
            Transaction(date=date(2026, 2, 10), sku=sku_id, event=EventType.SNAPSHOT, qty=100)
        ]
        for txn in transactions:
            csv_layer.write_transaction(txn)
        
        # Sales history: 10 pz/day
        sales = [
            SalesRecord(date=date(2026, 2, i), sku=sku_id, qty_sold=10)
            for i in range(5, 11)
        ]
        csv_layer.write_sales(sales)
        
        stock = Stock(sku=sku_id, on_hand=100, on_order=0, unfulfilled_qty=0, asof_date=date(2026, 2, 11))
        
        # Wednesday proposal (1 day forecast)
        wednesday = date(2026, 2, 12)
        proposal_wed = workflow.generate_proposal(
            sku=sku_id,
            description="Test",
            current_stock=stock,
            daily_sales_avg=10.0,
            sku_obj=sku,
            target_receipt_date=wednesday,
            protection_period_days=1,  # Same protection period
            transactions=transactions,
            sales_records=sales
        )
        
        # IP(Wednesday) should account for Tuesday sales: 100 - 10 = 90
        expected_ip_wed = 90
        assert proposal_wed.inventory_position == expected_ip_wed, \
            f"Wednesday IP should be {expected_ip_wed}, got {proposal_wed.inventory_position}"
        
        # Friday proposal (3 days forecast)
        friday = date(2026, 2, 14)
        proposal_fri = workflow.generate_proposal(
            sku=sku_id,
            description="Test",
            current_stock=stock,
            daily_sales_avg=10.0,
            sku_obj=sku,
            target_receipt_date=friday,
            protection_period_days=1,  # Same protection period as Wednesday
            transactions=transactions,
            sales_records=sales
        )
        
        # IP(Friday) should account for Tue+Wed+Thu sales: 100 - 30 = 70
        expected_ip_fri = 70
        assert proposal_fri.inventory_position == expected_ip_fri, \
            f"Friday IP should be {expected_ip_fri}, got {proposal_fri.inventory_position}"
        
        # Friday should propose more than Wednesday due to lower IP
        assert proposal_fri.proposed_qty > proposal_wed.proposed_qty, \
            "Friday should propose more qty due to lower IP from forecast sales"
        
    finally:
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)


def test_usable_stock_at_target_date_expiring_lots():
    """
    Test that usable_stock calculation uses target_date, catching expiring lots.
    
    Scenario:
    - Today: 2026-02-11 (Tuesday)
    - Lot 1: 30 pz, expires Thursday (between Tuesday and Friday)
    - Lot 2: 70 pz, expires next week
    - Total stock: 100 pz
    - Target: Friday
    
    Expected:
    - Usable stock at Friday = 70 pz (Lot 1 expired)
    - IP should use 70, not 100
    """
    import tempfile
    test_dir = tempfile.mkdtemp()
    
    try:
        csv_layer = CSVLayer(data_dir=Path(test_dir))
        workflow = OrderWorkflow(csv_layer=csv_layer, lead_time_days=1)
        
        sku_id = "SKU_EXPIRY"
        sku = SKU(
            sku_id,
            description="Expiring Lots Test",
            ean="",
            moq=10,
            pack_size=10,
            lead_time_days=1,
            review_period=7,
            safety_stock=20,
            shelf_life_days=14,  # Enable shelf life
            min_shelf_life_days=3,
            max_stock=500,
            reorder_point=30,
            demand_variability=DemandVariability.LOW,
            in_assortment=True
        )
        csv_layer.write_sku(sku)
        
        # Stock: 100 pz total
        transactions = [
            Transaction(date=date(2026, 2, 10), sku=sku_id, event=EventType.SNAPSHOT, qty=100)
        ]
        for txn in transactions:
            csv_layer.write_transaction(txn)
        
        # Lots: one expires Thursday, one next week
        thursday = date(2026, 2, 13)
        next_week = date(2026, 2, 20)
        
        lots = [
            Lot(
                lot_id="LOT_EXPIRE_THURSDAY",
                sku=sku_id,
                qty_on_hand=30,
                receipt_id="REC_001",
                receipt_date=date(2026, 1, 30),
                expiry_date=thursday
            ),
            Lot(
                lot_id="LOT_OK",
                sku=sku_id,
                qty_on_hand=70,
                receipt_id="REC_002",
                receipt_date=date(2026, 2, 6),
                expiry_date=next_week
            )
        ]
        for lot in lots:
            csv_layer.write_lot(lot)
        
        sales = [
            SalesRecord(date=date(2026, 2, i), sku=sku_id, qty_sold=5)
            for i in range(5, 11)
        ]
        csv_layer.write_sales(sales)
        
        stock = Stock(sku=sku_id, on_hand=100, on_order=0, unfulfilled_qty=0, asof_date=date(2026, 2, 11))
        
        # Friday proposal: should see Lot 1 expired
        friday = date(2026, 2, 14)
        proposal = workflow.generate_proposal(
            sku=sku_id,
            description="Test",
            current_stock=stock,
            daily_sales_avg=5.0,
            sku_obj=sku,
            target_receipt_date=friday,
            protection_period_days=1,
            transactions=transactions,
            sales_records=sales
        )
        
        # Usable stock at Friday should be 70 (Lot_OK only)
        # Note: calculate_usable_stock with min_shelf_life=3 at Friday (2026-02-14):
        # - LOT_EXPIRE_THURSDAY: expires 2026-02-13 (before Friday) → unusable
        # - LOT_OK: expires 2026-02-20, shelf_life = 6 days > min=3 → usable
        expected_usable = 70
        assert proposal.usable_stock == expected_usable, \
            f"Usable stock at Friday should be {expected_usable}, got {proposal.usable_stock}"
        
        # IP should use usable stock (70) - forecast sales (15) = 55
        expected_ip = 70 - 15  # 70 usable - (3 days × 5 pz/day)
        assert proposal.inventory_position == expected_ip, \
            f"IP should be {expected_ip}, got {proposal.inventory_position}"
        
    finally:
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
