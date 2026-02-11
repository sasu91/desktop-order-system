"""
Tests for calendar-aware order proposals with receipt-date filtering.

These tests verify that:
1. inventory_position filters on_order by receipt_date when target_receipt_date is provided
2. Monday proposals don't count Saturday orders that arrive after Monday
3. Protection period replaces lead_time + review_period when calendar is used
4. Manual receipt date override works correctly
"""
from datetime import date, timedelta
from pathlib import Path
import pytest

from src.domain.models import SKU, Transaction, EventType, Stock, SalesRecord, DemandVariability
from src.domain.ledger import StockCalculator
from src.domain.calendar import Lane, next_receipt_date, calculate_protection_period_days
from src.workflows.order import OrderWorkflow
from src.persistence.csv_layer import CSVLayer


def test_inventory_position_filters_by_receipt_date():
    """
    Test that inventory_position filters on_order by receipt_date.
    
    Scenario:
    - On hand: 50 pz
    - Order 1: 30 pz, receipt_date = Saturday (2026-02-07)
    - Order 2: 50 pz, receipt_date = Monday (2026-02-09)
    
    Expected:
    - IP(as_of=Saturday) = 50 + 30 = 80 (only Saturday order counted)
    - IP(as_of=Monday) = 50 + 30 + 50 = 130 (both orders counted)
    - IP(as_of=Friday) = 50 (no orders arrived yet)
    """
    sku = "TEST_SKU"
    
    transactions = [
        # Initial snapshot
        Transaction(date=date(2026, 2, 1), sku=sku, event=EventType.SNAPSHOT, qty=50),
        
        # Friday dual order
        Transaction(
            date=date(2026, 2, 6),  # Friday
            sku=sku,
            event=EventType.ORDER,
            qty=30,
            receipt_date=date(2026, 2, 7)  # Saturday delivery
        ),
        Transaction(
            date=date(2026, 2, 6),  # Friday
            sku=sku,
            event=EventType.ORDER,
            qty=50,
            receipt_date=date(2026, 2, 9)  # Monday delivery
        ),
    ]
    
    # Test IP at different dates
    ip_friday = StockCalculator.inventory_position(
        sku=sku,
        as_of_date=date(2026, 2, 6),  # Friday evening (before any deliveries)
        transactions=transactions,
        sales_records=None
    )
    assert ip_friday == 50, "Friday IP should only include on_hand (no orders arrived yet)"
    
    ip_saturday = StockCalculator.inventory_position(
        sku=sku,
        as_of_date=date(2026, 2, 7),  # Saturday
        transactions=transactions,
        sales_records=None
    )
    assert ip_saturday == 80, "Saturday IP should include on_hand + Saturday order"
    
    ip_monday = StockCalculator.inventory_position(
        sku=sku,
        as_of_date=date(2026, 2, 9),  # Monday
        transactions=transactions,
        sales_records=None
    )
    assert ip_monday == 130, "Monday IP should include on_hand + both orders"


def test_monday_proposal_ignores_saturday_order():
    """
    Test that Monday order proposal doesn't count Saturday delivery in on_order.
    
    Scenario (Friday dual order):
    1. Generate proposal for Saturday delivery (Lane.SATURDAY)
       → Should count current on_hand only (no future orders)
    2. Confirm Saturday order
    3. Generate proposal for Monday delivery (Lane.MONDAY)
       → Should count on_hand + Saturday order in pipeline
    
    This simulates the "doppio ordine venerdì" workflow.
    """
    # Setup test data directory (in-memory)
    import tempfile
    import os
    test_dir = tempfile.mkdtemp()
    
    try:
        csv_layer = CSVLayer(data_dir=Path(test_dir))
        workflow = OrderWorkflow(csv_layer=csv_layer, lead_time_days=1)
        
        sku_id = "SKU_DUAL"
        
        # Create SKU
        sku = SKU(
            sku=sku_id,
            description="Dual Order Test",
            ean="",
            moq=10,
            pack_size=10,
            lead_time_days=1,
            review_period=7,
            safety_stock=20,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=30,
            demand_variability=DemandVariability.LOW,
            in_assortment=True
        )
        csv_layer.write_sku(sku)
        
        # Initial stock
        transactions = [
            Transaction(date=date(2026, 2, 5), sku=sku_id, event=EventType.SNAPSHOT, qty=50)
        ]
        for txn in transactions:
            csv_layer.write_transaction(txn)
        
        # Sales history: 10 pz/day
        sales = [
            SalesRecord(date=date(2026, 2, i), sku=sku_id, qty_sold=10)
            for i in range(1, 6)  # 5 days history
        ]
        csv_layer.write_sales(sales)
        
        # Calculate current stock
        stock = StockCalculator.calculate_asof(
            sku=sku_id,
            asof_date=date(2026, 2, 6),  # Friday
            transactions=transactions,
            sales_records=sales
        )
        
        # === FRIDAY: Generate Saturday proposal ===
        friday = date(2026, 2, 6)
        saturday_receipt = next_receipt_date(friday, Lane.SATURDAY)
        saturday_protection = calculate_protection_period_days(friday, Lane.SATURDAY)
        
        proposal_saturday = workflow.generate_proposal(
            sku=sku_id,
            description="Dual Order Test",
            current_stock=stock,
            daily_sales_avg=10.0,
            sku_obj=sku,
            target_receipt_date=saturday_receipt,
            protection_period_days=saturday_protection,
            transactions=transactions,
            sales_records=sales
        )
        
        # Saturday proposal should use projected IP accounting for Friday→Saturday sales
        # Stock calculation: SNAPSHOT(50 on Feb 5) - SALE(10 on Feb 5) = 40 on Feb 6
        # Projected IP = 40 - 10 (forecast Friday→Saturday) = 30
        expected_ip_saturday = 30  # on_hand=40, forecast_sales=10, no on_order yet
        assert proposal_saturday.inventory_position == expected_ip_saturday, \
            f"Saturday IP should be {expected_ip_saturday}, got {proposal_saturday.inventory_position}"
        
        # Confirm Saturday order
        saturday_order_qty = proposal_saturday.proposed_qty
        if saturday_order_qty > 0:
            saturday_order_txn = Transaction(
                date=friday,
                sku=sku_id,
                event=EventType.ORDER,
                qty=saturday_order_qty,
                receipt_date=saturday_receipt
            )
            csv_layer.write_transaction(saturday_order_txn)
            transactions.append(saturday_order_txn)
        
        # === FRIDAY: Generate Monday proposal ===
        monday_receipt = next_receipt_date(friday, Lane.MONDAY)
        monday_protection = calculate_protection_period_days(friday, Lane.MONDAY)
        
        proposal_monday = workflow.generate_proposal(
            sku=sku_id,
            description="Dual Order Test",
            current_stock=stock,  # Same base stock
            daily_sales_avg=10.0,
            sku_obj=sku,
            target_receipt_date=monday_receipt,
            protection_period_days=monday_protection,
            transactions=transactions,
            sales_records=sales
        )
        
        # Monday proposal should count Saturday order and subtract forecast sales
        # Projected IP(Monday) = on_hand - forecast(Fri→Mon) + receipts_before_Monday
        # = 40 - 30 (3 days × 10) + 20 (Saturday receipt) = 30
        # Stock base: 40 (on_hand on Feb 6)
        # Forecast sales: 30 (Friday→Monday = 3 days)
        # Saturday receipt before Monday: +20
        expected_ip_monday = 40 - 30 + saturday_order_qty if saturday_order_qty > 0 else 40 - 30
        assert proposal_monday.inventory_position == expected_ip_monday, \
            f"Monday IP should be {expected_ip_monday} (base - forecast + Saturday receipt), got {proposal_monday.inventory_position}"
        
        # Verify protection periods are calendar-driven (not lead+review)
        # Saturday: Sat → Tue (next order Mon) = 3 days
        # Monday: Mon → Tue (next order Mon) = 1 day
        assert proposal_saturday.forecast_period_days == 3, \
            f"Saturday protection should be 3 days, got {proposal_saturday.forecast_period_days}"
        assert proposal_monday.forecast_period_days == 1, \
            f"Monday protection should be 1 day, got {proposal_monday.forecast_period_days}"
        
    finally:
        # Cleanup
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)


def test_protection_period_replaces_lead_review():
    """
    Test that protection_period_days replaces lead_time + review_period in forecast.
    
    Traditional formula: forecast_period = lead_time + review_period
    Calendar-aware: forecast_period = protection_period (from calendar)
    """
    import tempfile
    test_dir = tempfile.mkdtemp()
    
    try:
        csv_layer = CSVLayer(data_dir=Path(test_dir))
        workflow = OrderWorkflow(csv_layer=csv_layer, lead_time_days=1)
        
        sku_id = "TEST_PROTECTION"
        sku = SKU(
            sku=sku_id,
            description="Protection Period Test",
            ean="",
            moq=1,
            pack_size=1,
            lead_time_days=1,
            review_period=7,  # Traditional: 1 + 7 = 8 days
            safety_stock=10,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=30,
            demand_variability=DemandVariability.LOW,
            in_assortment=True
        )
        csv_layer.write_sku(sku)
        
        # Setup stock
        transactions = [
            Transaction(date=date(2026, 2, 5), sku=sku_id, event=EventType.SNAPSHOT, qty=100)
        ]
        for txn in transactions:
            csv_layer.write_transaction(txn)
        
        stock = Stock(sku=sku_id, on_hand=100, on_order=0, unfulfilled_qty=0, asof_date=date(2026, 2, 6))
        
        # Traditional proposal (no protection_period)
        proposal_traditional = workflow.generate_proposal(
            sku=sku_id,
            description="Test",
            current_stock=stock,
            daily_sales_avg=5.0,
            sku_obj=sku,
        )
        
        expected_traditional_period = 1 + 7  # lead_time + review_period
        assert proposal_traditional.forecast_period_days == expected_traditional_period, \
            f"Traditional forecast_period should be {expected_traditional_period}, got {proposal_traditional.forecast_period_days}"
        
        # Calendar-aware proposal with custom protection_period
        custom_protection = 3  # e.g., Friday Saturday lane
        custom_receipt_date = date(2026, 2, 9)
        
        proposal_calendar = workflow.generate_proposal(
            sku=sku_id,
            description="Test",
            current_stock=stock,
            daily_sales_avg=5.0,
            sku_obj=sku,
            target_receipt_date=custom_receipt_date,
            protection_period_days=custom_protection,
            transactions=transactions,
        )
        
        assert proposal_calendar.forecast_period_days == custom_protection, \
            f"Calendar-aware forecast_period should be {custom_protection}, got {proposal_calendar.forecast_period_days}"
        
        # Receipt date should match target
        assert proposal_calendar.receipt_date == custom_receipt_date, \
            f"Receipt date should be {custom_receipt_date}, got {proposal_calendar.receipt_date}"
        
    finally:
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)


def test_manual_receipt_date_override():
    """
    Test that manual receipt_date override works and uses derived protection period.
    
    When user provides manual receipt_date:
    - Receipt date should be used as-is
    - Protection period should be calculated as (target - today)
    """
    import tempfile
    test_dir = tempfile.mkdtemp()
    
    try:
        csv_layer = CSVLayer(data_dir=Path(test_dir))
        workflow = OrderWorkflow(csv_layer=csv_layer, lead_time_days=1)
        
        sku_id = "TEST_MANUAL"
        sku = SKU(
            sku=sku_id,
            description="Manual Override Test",
            ean="",
            moq=1,
            pack_size=1,
            lead_time_days=1,
            review_period=7,
            safety_stock=10,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=30,
            demand_variability=DemandVariability.LOW,
            in_assortment=True
        )
        csv_layer.write_sku(sku)
        
        transactions = [
            Transaction(date=date(2026, 2, 5), sku=sku_id, event=EventType.SNAPSHOT, qty=100)
        ]
        for txn in transactions:
            csv_layer.write_transaction(txn)
        
        stock = Stock(sku=sku_id, on_hand=100, on_order=0, unfulfilled_qty=0, asof_date=date(2026, 2, 6))
        
        # Manual receipt date: 5 days from today
        manual_receipt_date = date(2026, 2, 11)  # Friday 2026-02-06 + 5 days
        expected_protection = 5
        
        proposal = workflow.generate_proposal(
            sku=sku_id,
            description="Test",
            current_stock=stock,
            daily_sales_avg=5.0,
            sku_obj=sku,
            target_receipt_date=manual_receipt_date,
            protection_period_days=expected_protection,  # Derived by UI from (target - today)
            transactions=transactions,
        )
        
        assert proposal.receipt_date == manual_receipt_date, \
            f"Receipt date should be {manual_receipt_date}, got {proposal.receipt_date}"
        
        assert proposal.forecast_period_days == expected_protection, \
            f"Protection period should be {expected_protection}, got {proposal.forecast_period_days}"
        
    finally:
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
