"""
Test suite for stock calculation engine (ledger.py).

Tests core ledger processing, AsOf logic, and deterministic ordering.
"""
import pytest
from datetime import date

from src.domain.models import Transaction, EventType, Stock, SalesRecord
from src.domain.ledger import StockCalculator, validate_ean


class TestStockCalculatorBasic:
    """Test basic stock calculation scenarios."""
    
    def test_empty_ledger(self):
        """Empty ledger should yield zero stock."""
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 28),
            transactions=[],
        )
        assert stock.on_hand == 0
        assert stock.on_order == 0
        assert stock.sku == "SKU001"
    
    def test_snapshot_only(self):
        """Single SNAPSHOT event should set on_hand."""
        txns = [
            Transaction(
                date=date(2026, 1, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=100,
            )
        ]
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 28),
            transactions=txns,
        )
        assert stock.on_hand == 100
        assert stock.on_order == 0
    
    def test_snapshot_followed_by_order(self):
        """SNAPSHOT + ORDER should increase on_order."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=date(2026, 1, 5), sku="SKU001", event=EventType.ORDER, qty=50),
        ]
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 28),
            transactions=txns,
        )
        assert stock.on_hand == 100
        assert stock.on_order == 50
    
    def test_receipt_moves_stock(self):
        """RECEIPT should move qty from on_order to on_hand."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=date(2026, 1, 5), sku="SKU001", event=EventType.ORDER, qty=50),
            Transaction(date=date(2026, 1, 10), sku="SKU001", event=EventType.RECEIPT, qty=50),
        ]
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 28),
            transactions=txns,
        )
        assert stock.on_hand == 150  # 100 + 50 received
        assert stock.on_order == 0
    
    def test_sale_reduces_on_hand(self):
        """SALE event should reduce on_hand."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=date(2026, 1, 10), sku="SKU001", event=EventType.SALE, qty=20),
        ]
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 28),
            transactions=txns,
        )
        assert stock.on_hand == 80
        assert stock.on_order == 0


class TestStockCalculatorAsOfDate:
    """Test AsOf date boundary logic."""
    
    def test_asof_excludes_future_events(self):
        """Events on or after AsOf date should be excluded."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=date(2026, 1, 5), sku="SKU001", event=EventType.ORDER, qty=50),
            Transaction(date=date(2026, 1, 28), sku="SKU001", event=EventType.ORDER, qty=30),  # On AsOf date
            Transaction(date=date(2026, 1, 29), sku="SKU001", event=EventType.ORDER, qty=20),  # After AsOf date
        ]
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 28),
            transactions=txns,
        )
        # Should only include first two events
        assert stock.on_hand == 100
        assert stock.on_order == 50


class TestStockCalculatorEventOrdering:
    """Test deterministic event ordering within same day."""
    
    def test_event_priority_same_day(self):
        """
        Events on same day should be applied in priority order:
        SNAPSHOT → ORDER/RECEIPT → SALE/WASTE/ADJUST
        """
        txns = [
            # All on same day, but in reverse priority order
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.ADJUST, qty=10),
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.ORDER, qty=50),
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
        ]
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 2),
            transactions=txns,
        )
        # Order should be: SNAPSHOT (100) → ORDER (no effect) → ADJUST (+10)
        assert stock.on_hand == 110
        assert stock.on_order == 50


class TestSalesIntegration:
    """Test integration of sales records as implicit SALE events."""
    
    def test_sales_reduce_on_hand(self):
        """Sales records should reduce on_hand like SALE events."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
        ]
        sales = [
            SalesRecord(date=date(2026, 1, 5), sku="SKU001", qty_sold=10),
            SalesRecord(date=date(2026, 1, 10), sku="SKU001", qty_sold=15),
        ]
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 28),
            transactions=txns,
            sales_records=sales,
        )
        assert stock.on_hand == 75  # 100 - 10 - 15


class TestMultipleSKUs:
    """Test stock calculation for multiple SKUs."""
    
    def test_calculate_all_skus(self):
        """Calculate stock for multiple SKUs in parallel."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=date(2026, 1, 1), sku="SKU002", event=EventType.SNAPSHOT, qty=50),
            Transaction(date=date(2026, 1, 5), sku="SKU001", event=EventType.ORDER, qty=30),
        ]
        stocks = StockCalculator.calculate_all_skus(
            all_skus=["SKU001", "SKU002"],
            asof_date=date(2026, 1, 28),
            transactions=txns,
        )
        assert stocks["SKU001"].on_hand == 100
        assert stocks["SKU001"].on_order == 30
        assert stocks["SKU002"].on_hand == 50
        assert stocks["SKU002"].on_order == 0


class TestEANValidation:
    """Test EAN validation function."""
    
    def test_valid_ean_13(self):
        """Valid 13-digit EAN should pass."""
        is_valid, error = validate_ean("5901234123457")
        assert is_valid is True
        assert error is None
    
    def test_valid_ean_12(self):
        """Valid 12-digit EAN (UPC-A) should pass."""
        is_valid, error = validate_ean("590123412345")
        assert is_valid is True
        assert error is None
    
    def test_empty_ean_is_valid(self):
        """Empty or None EAN should be valid."""
        assert validate_ean(None)[0] is True
        assert validate_ean("")[0] is True
        assert validate_ean("   ")[0] is True
    
    def test_invalid_ean_non_digit(self):
        """Non-digit EAN should fail."""
        is_valid, error = validate_ean("590123ABC357")
        assert is_valid is False
        assert "digits" in error.lower()
    
    def test_invalid_ean_wrong_length(self):
        """EAN with wrong length should fail."""
        is_valid, error = validate_ean("59012341234")  # 11 digits
        assert is_valid is False
        assert "digits" in error.lower()


class TestIdempotency:
    """Test that recalculating same date twice yields same result."""
    
    def test_recalculate_same_date_is_idempotent(self):
        """Calculating stock twice for same date should yield identical result."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=date(2026, 1, 5), sku="SKU001", event=EventType.ORDER, qty=50),
            Transaction(date=date(2026, 1, 10), sku="SKU001", event=EventType.RECEIPT, qty=50),
            Transaction(date=date(2026, 1, 15), sku="SKU001", event=EventType.SALE, qty=20),
        ]
        
        stock1 = StockCalculator.calculate_asof("SKU001", date(2026, 1, 28), txns)
        stock2 = StockCalculator.calculate_asof("SKU001", date(2026, 1, 28), txns)
        
        assert stock1.on_hand == stock2.on_hand
        assert stock1.on_order == stock2.on_order
