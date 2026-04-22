"""
Test on-order pipeline by receipt date (Friday dual orders scenario).

Validates:
1. on_order_by_date() correctly maps orders to receipt dates
2. inventory_position(as_of_date) filters orders by receipt_date
3. Friday dual orders (Saturday vs Monday) are tracked independently
4. Receipt events correctly reduce pending orders by date
"""
import pytest
from datetime import date as Date, timedelta

from src.domain.ledger import StockCalculator
from src.domain.models import Transaction, EventType, Stock


class TestOnOrderPipeline:
    """Test order pipeline with date granularity."""
    
    def test_on_order_by_date_empty_ledger(self):
        """Empty ledger returns empty dict."""
        transactions = []
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        assert result == {}
    
    def test_on_order_by_date_single_order(self):
        """Single order with receipt_date."""
        transactions = [
            Transaction(
                date=Date(2024, 2, 5),  # Monday
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=Date(2024, 2, 6),  # Tuesday
                note="Regular order"
            )
        ]
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        assert result == {Date(2024, 2, 6): 100}
    
    def test_on_order_by_date_friday_dual_orders(self):
        """
        Friday dual orders: one for Saturday, one for Monday.
        
        Scenario:
        - Order on Friday 2024-02-09
        - Lane 1 (Saturday): delivery 2024-02-10, qty=30
        - Lane 2 (Monday): delivery 2024-02-12, qty=50
        """
        friday = Date(2024, 2, 9)
        saturday = Date(2024, 2, 10)
        monday = Date(2024, 2, 12)
        
        transactions = [
            # Friday order: Saturday delivery
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=30,
                receipt_date=saturday,
                note="Friday -> Saturday lane"
            ),
            # Friday order: Monday delivery
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=50,
                receipt_date=monday,
                note="Friday -> Monday lane"
            ),
        ]
        
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        
        assert result == {
            saturday: 30,
            monday: 50,
        }
    
    def test_on_order_by_date_aggregates_same_date(self):
        """Multiple orders for same receipt_date are aggregated."""
        receipt_date = Date(2024, 2, 10)
        
        transactions = [
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=receipt_date,
            ),
            Transaction(
                date=Date(2024, 2, 6),
                sku="SKU001",
                event=EventType.ORDER,
                qty=50,
                receipt_date=receipt_date,
            ),
        ]
        
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        assert result == {receipt_date: 150}
    
    def test_on_order_by_date_ignores_receipts_without_date(self):
        """Orders without receipt_date are ignored."""
        transactions = [
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=None,  # No receipt_date
            ),
        ]
        
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        assert result == {}
    
    def test_on_order_by_date_filters_by_sku(self):
        """Only returns orders for requested SKU."""
        transactions = [
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=Date(2024, 2, 6),
            ),
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU002",
                event=EventType.ORDER,
                qty=200,
                receipt_date=Date(2024, 2, 6),
            ),
        ]
        
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        assert result == {Date(2024, 2, 6): 100}
    
    def test_on_order_by_date_after_receipt(self):
        """Orders fulfilled by RECEIPT events are removed from pending."""
        receipt_date = Date(2024, 2, 10)
        
        transactions = [
            # Order 100
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=receipt_date,
            ),
            # Receive 100 (fully fulfilled)
            Transaction(
                date=Date(2024, 2, 10),
                sku="SKU001",
                event=EventType.RECEIPT,
                qty=100,
                receipt_date=receipt_date,
            ),
        ]
        
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        assert result == {}  # No pending orders
    
    def test_on_order_by_date_partial_receipt(self):
        """Partial receipt reduces pending order."""
        receipt_date = Date(2024, 2, 10)
        
        transactions = [
            # Order 100
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=receipt_date,
            ),
            # Receive only 60
            Transaction(
                date=Date(2024, 2, 10),
                sku="SKU001",
                event=EventType.RECEIPT,
                qty=60,
                receipt_date=receipt_date,
            ),
        ]
        
        result = StockCalculator.on_order_by_date("SKU001", transactions)
        assert result == {receipt_date: 40}  # 100 - 60 = 40 pending
    
    def test_on_order_by_date_respects_asof_cutoff(self):
        """Only includes orders placed before as_of_date."""
        transactions = [
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=Date(2024, 2, 10),
            ),
            Transaction(
                date=Date(2024, 2, 15),  # After cutoff
                sku="SKU001",
                event=EventType.ORDER,
                qty=200,
                receipt_date=Date(2024, 2, 20),
            ),
        ]
        
        # As of Feb 10, only first order is visible
        result = StockCalculator.on_order_by_date(
            "SKU001",
            transactions,
            as_of_date=Date(2024, 2, 10)
        )
        assert result == {Date(2024, 2, 10): 100}


class TestInventoryPosition:
    """Test inventory_position(as_of_date) calculation."""
    
    def test_inventory_position_base_case(self):
        """IP = on_hand + on_order - unfulfilled."""
        transactions = [
            # Snapshot: 50 on hand
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=50,
            ),
            # Order 100 for Feb 10
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=Date(2024, 2, 10),
            ),
        ]
        
        # As of Feb 10: IP = 50 + 100 = 150
        ip = StockCalculator.inventory_position(
            "SKU001",
            Date(2024, 2, 10),
            transactions
        )
        assert ip == 150
    
    def test_inventory_position_friday_dual_orders_saturday(self):
        """
        Friday dual orders: IP on Saturday includes only Saturday order.
        
        Setup:
        - on_hand: 50 (as of Friday)
        - ORDER(qty=30, receipt_date=Saturday)
        - ORDER(qty=50, receipt_date=Monday)
        
        Expected:
        - IP(as_of=Saturday) = 50 + 30 = 80 (only Saturday order)
        - IP(as_of=Monday) = 50 + 30 + 50 = 130 (both orders)
        """
        friday = Date(2024, 2, 9)
        saturday = Date(2024, 2, 10)
        monday = Date(2024, 2, 12)
        
        transactions = [
            # Initial stock: 50
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=50,
            ),
            # Friday order: Saturday delivery (30)
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=30,
                receipt_date=saturday,
                note="Saturday lane"
            ),
            # Friday order: Monday delivery (50)
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=50,
                receipt_date=monday,
                note="Monday lane"
            ),
        ]
        
        # IP as of Saturday: should include ONLY Saturday order
        ip_saturday = StockCalculator.inventory_position(
            "SKU001",
            saturday,
            transactions
        )
        assert ip_saturday == 80  # 50 + 30
        
        # IP as of Monday: should include BOTH orders
        ip_monday = StockCalculator.inventory_position(
            "SKU001",
            monday,
            transactions
        )
        assert ip_monday == 130  # 50 + 30 + 50
    
    def test_inventory_position_friday_dual_orders_monday(self):
        """Same scenario, verify Monday IP explicitly."""
        friday = Date(2024, 2, 9)
        saturday = Date(2024, 2, 10)
        monday = Date(2024, 2, 12)
        
        transactions = [
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=50,
            ),
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=30,
                receipt_date=saturday,
            ),
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=50,
                receipt_date=monday,
            ),
        ]
        
        # IP as of Monday
        ip_monday = StockCalculator.inventory_position(
            "SKU001",
            monday,
            transactions
        )
        assert ip_monday == 130  # 50 on_hand + 30 (Sat) + 50 (Mon)
    
    def test_inventory_position_excludes_future_orders(self):
        """Orders with receipt_date > as_of_date are excluded from IP."""
        transactions = [
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=50,
            ),
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=Date(2024, 2, 20),  # Future receipt
            ),
        ]
        
        # IP as of Feb 10: should NOT include order arriving Feb 20
        ip = StockCalculator.inventory_position(
            "SKU001",
            Date(2024, 2, 10),
            transactions
        )
        assert ip == 50  # Only on_hand, no on_order yet
    
    def test_inventory_position_with_unfulfilled(self):
        """IP accounts for unfulfilled quantities (backorders)."""
        transactions = [
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=100,
            ),
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=50,
                receipt_date=Date(2024, 2, 10),
            ),
            # Backorder: 20 units cancelled/unfulfilled
            Transaction(
                date=Date(2024, 2, 6),
                sku="SKU001",
                event=EventType.UNFULFILLED,
                qty=20,
            ),
        ]
        
        # IP = on_hand + on_order - unfulfilled = 100 + 50 - 20 = 130
        ip = StockCalculator.inventory_position(
            "SKU001",
            Date(2024, 2, 10),
            transactions
        )
        assert ip == 130
    
    def test_inventory_position_after_receipt(self):
        """After receipt, on_order is reduced, on_hand increases."""
        transactions = [
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=50,
            ),
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=100,
                receipt_date=Date(2024, 2, 10),
            ),
            Transaction(
                date=Date(2024, 2, 10),
                sku="SKU001",
                event=EventType.RECEIPT,
                qty=100,
                receipt_date=Date(2024, 2, 10),
            ),
        ]
        
        # IP as of Feb 11 (after receipt): on_hand=150, on_order=0
        ip = StockCalculator.inventory_position(
            "SKU001",
            Date(2024, 2, 11),
            transactions
        )
        assert ip == 150  # 50 + 100 received
    
    def test_inventory_position_with_sales(self):
        """IP decreases with sales (on_hand reduces)."""
        from src.domain.models import SalesRecord
        
        transactions = [
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=100,
            ),
            Transaction(
                date=Date(2024, 2, 5),
                sku="SKU001",
                event=EventType.ORDER,
                qty=50,
                receipt_date=Date(2024, 2, 10),
            ),
        ]
        
        sales_records = [
            SalesRecord(date=Date(2024, 2, 7), sku="SKU001", qty_sold=30)
        ]
        
        # IP as of Feb 10: on_hand=70 (100-30), on_order=50
        ip = StockCalculator.inventory_position(
            "SKU001",
            Date(2024, 2, 10),
            transactions,
            sales_records
        )
        assert ip == 120  # 70 + 50


class TestOnOrderPipelineIntegration:
    """Integration tests for full order pipeline scenarios."""
    
    def test_friday_dual_order_full_workflow(self):
        """
        Complete Friday dual order workflow with receipts.
        
        Timeline:
        - Friday: Place 2 orders (Sat=30, Mon=50)
        - Saturday: Receive first order (30)
        - Monday: Receive second order (50)
        
        Verify on_order_by_date at each step.
        """
        friday = Date(2024, 2, 9)
        saturday = Date(2024, 2, 10)
        monday = Date(2024, 2, 12)
        
        # Step 1: Friday evening (orders placed)
        txns_friday = [
            Transaction(
                date=Date(2024, 2, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=50,
            ),
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=30,
                receipt_date=saturday,
            ),
            Transaction(
                date=friday,
                sku="SKU001",
                event=EventType.ORDER,
                qty=50,
                receipt_date=monday,
            ),
        ]
        
        # Friday: Both orders pending
        pending_friday = StockCalculator.on_order_by_date("SKU001", txns_friday)
        assert pending_friday == {saturday: 30, monday: 50}
        
        # Step 2: Saturday receipt
        txns_saturday = txns_friday + [
            Transaction(
                date=saturday,
                sku="SKU001",
                event=EventType.RECEIPT,
                qty=30,
                receipt_date=saturday,
            ),
        ]
        
        # Saturday: Only Monday order pending
        pending_saturday = StockCalculator.on_order_by_date("SKU001", txns_saturday)
        assert pending_saturday == {monday: 50}
        
        # Step 3: Monday receipt
        txns_monday = txns_saturday + [
            Transaction(
                date=monday,
                sku="SKU001",
                event=EventType.RECEIPT,
                qty=50,
                receipt_date=monday,
            ),
        ]
        
        # Monday: No pending orders
        pending_monday = StockCalculator.on_order_by_date("SKU001", txns_monday)
        assert pending_monday == {}
    
    def test_inventory_position_across_week(self):
        """
        Track IP progression across a week with daily sales and orders.
        
        Day 1 (Mon): on_hand=100, order 50 for Wed
        Day 2 (Tue): sell 20
        Day 3 (Wed): receive 50, order 30 for Fri
        Day 4 (Thu): sell 15
        Day 5 (Fri): receive 30, sell 10
        """
        from src.domain.models import SalesRecord
        
        mon = Date(2024, 2, 5)
        tue = Date(2024, 2, 6)
        wed = Date(2024, 2, 7)
        thu = Date(2024, 2, 8)
        fri = Date(2024, 2, 9)
        
        transactions = [
            # Monday: snapshot 100, order 50 for Wed
            Transaction(date=mon, sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=mon, sku="SKU001", event=EventType.ORDER, qty=50, receipt_date=wed),
            # Wednesday: receive 50, order 30 for Fri
            Transaction(date=wed, sku="SKU001", event=EventType.RECEIPT, qty=50, receipt_date=wed),
            Transaction(date=wed, sku="SKU001", event=EventType.ORDER, qty=30, receipt_date=fri),
            # Friday: receive 30
            Transaction(date=fri, sku="SKU001", event=EventType.RECEIPT, qty=30, receipt_date=fri),
        ]
        
        sales = [
            SalesRecord(date=tue, sku="SKU001", qty_sold=20),
            SalesRecord(date=thu, sku="SKU001", qty_sold=15),
            SalesRecord(date=fri, sku="SKU001", qty_sold=10),
        ]
        
        # IP progression (use next day to include transactions OF that day)
        # Note: IP(as_of_date) includes only orders with receipt_date <= as_of_date
        tue_eod = Date(2024, 2, 6)
        wed_eod = Date(2024, 2, 7)
        thu_eod = Date(2024, 2, 8)
        fri_eod = Date(2024, 2, 9)
        sat = Date(2024, 2, 10)  # Next day after Friday
        
        # Monday EOD (as of Tue): on_hand=100, order not yet arrived (receipt_date=Wed > Tue)
        ip_mon = StockCalculator.inventory_position("SKU001", tue_eod, transactions, sales)
        assert ip_mon == 100  # 100 on_hand, no orders arriving by Tue
        
        # Tuesday EOD (as of Wed): on_hand=80 (sold 20), order arriving Wed (50)
        ip_tue = StockCalculator.inventory_position("SKU001", wed_eod, transactions, sales)
        assert ip_tue == 130  # 80 + 50 (order for Wed)
        
        # Wednesday EOD (as of Thu): on_hand=130 (80+50 received), order for Fri not yet arrived
        ip_wed = StockCalculator.inventory_position("SKU001", thu_eod, transactions, sales)
        assert ip_wed == 130  # 130 on_hand, order for Fri > Thu
        
        # Thursday EOD (as of Fri): on_hand=115 (130-15), order arriving Fri (30)
        ip_thu = StockCalculator.inventory_position("SKU001", fri_eod, transactions, sales)
        assert ip_thu == 145  # 115 + 30 (order for Fri)
        
        # Friday EOD (as of Sat): on_hand=135 (115+30-10)
        ip_fri = StockCalculator.inventory_position("SKU001", sat, transactions, sales)
        assert ip_fri == 135  # 145 - 10 sold on Fri


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
