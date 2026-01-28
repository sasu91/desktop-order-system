"""
Test suite for order and receiving workflows.

Tests proposal, confirmation, receipt, and idempotency.
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import Stock, Transaction, EventType, SalesRecord
from src.persistence.csv_layer import CSVLayer
from src.workflows.order import OrderWorkflow, calculate_daily_sales_average
from src.workflows.receiving import ReceivingWorkflow, ExceptionWorkflow


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for tests."""
    tmpdir = tempfile.mkdtemp()
    yield Path(tmpdir)
    shutil.rmtree(tmpdir)


@pytest.fixture
def csv_layer(temp_data_dir):
    """Create CSV layer with temp directory."""
    return CSVLayer(data_dir=temp_data_dir)


class TestOrderWorkflow:
    """Test order proposal and confirmation."""
    
    def test_generate_proposal_basic(self, csv_layer):
        """Generate basic order proposal."""
        workflow = OrderWorkflow(csv_layer, lead_time_days=7)
        
        current_stock = Stock(sku="SKU001", on_hand=50, on_order=30)
        proposal = workflow.generate_proposal(
            sku="SKU001",
            description="Test Product",
            current_stock=current_stock,
            daily_sales_avg=5.0,
            min_stock=20,
            days_cover=30,
        )
        
        # target = 20 + (5 * 30) = 170
        # available = 50 + 30 = 80
        # proposed = max(0, 170 - 80) = 90
        assert proposal.proposed_qty == 90
        assert proposal.sku == "SKU001"
        assert proposal.receipt_date == date.today() + timedelta(days=7)
    
    def test_generate_proposal_zero_qty(self, csv_layer):
        """Proposal should be zero if stock is already high."""
        workflow = OrderWorkflow(csv_layer)
        
        current_stock = Stock(sku="SKU001", on_hand=200, on_order=100)
        proposal = workflow.generate_proposal(
            sku="SKU001",
            description="Test Product",
            current_stock=current_stock,
            daily_sales_avg=5.0,
            min_stock=20,
            days_cover=30,
        )
        
        # target = 20 + (5 * 30) = 170
        # available = 200 + 100 = 300
        # proposed = max(0, 170 - 300) = 0
        assert proposal.proposed_qty == 0
    
    def test_confirm_order_single_sku(self, csv_layer):
        """Confirm order and verify ledger entry."""
        workflow = OrderWorkflow(csv_layer)
        
        current_stock = Stock(sku="SKU001", on_hand=50, on_order=30)
        proposal = workflow.generate_proposal(
            sku="SKU001",
            description="Test Product",
            current_stock=current_stock,
            daily_sales_avg=5.0,
        )
        
        confirmations, txns = workflow.confirm_order([proposal], [90])
        
        assert len(confirmations) == 1
        assert confirmations[0].qty_ordered == 90
        assert confirmations[0].sku == "SKU001"
        assert confirmations[0].status == "PENDING"
        
        assert len(txns) == 1
        assert txns[0].event == EventType.ORDER
        assert txns[0].qty == 90
        
        # Verify ledger was written
        ledger_txns = csv_layer.read_transactions()
        assert len(ledger_txns) == 1
        assert ledger_txns[0].event == EventType.ORDER


class TestReceivingWorkflow:
    """Test receiving closure and idempotency."""
    
    def test_close_receipt_first_time(self, csv_layer):
        """First receipt closure should succeed."""
        workflow = ReceivingWorkflow(csv_layer)
        
        receipt_id = "REC001"
        receipt_date = date(2026, 1, 15)
        sku_quantities = {"SKU001": 50, "SKU002": 30}
        
        txns, already_processed = workflow.close_receipt(
            receipt_id=receipt_id,
            receipt_date=receipt_date,
            sku_quantities=sku_quantities,
        )
        
        assert already_processed is False
        assert len(txns) == 2
        assert all(t.event == EventType.RECEIPT for t in txns)
        
        # Verify ledger was written
        ledger_txns = csv_layer.read_transactions()
        assert len(ledger_txns) == 2
    
    def test_close_receipt_idempotent(self, csv_layer):
        """Closing same receipt twice should be idempotent."""
        workflow = ReceivingWorkflow(csv_layer)
        
        receipt_id = "REC001"
        receipt_date = date(2026, 1, 15)
        sku_quantities = {"SKU001": 50}
        
        # First close
        txns1, already1 = workflow.close_receipt(
            receipt_id=receipt_id,
            receipt_date=receipt_date,
            sku_quantities=sku_quantities,
        )
        assert already1 is False
        assert len(txns1) == 1
        
        # Second close (should be skipped)
        txns2, already2 = workflow.close_receipt(
            receipt_id=receipt_id,
            receipt_date=receipt_date,
            sku_quantities=sku_quantities,
        )
        assert already2 is True
        assert len(txns2) == 0
        
        # Verify ledger has only one entry
        ledger_txns = csv_layer.read_transactions()
        assert len(ledger_txns) == 1


class TestExceptionWorkflow:
    """Test exception recording (WASTE, ADJUST, UNFULFILLED)."""
    
    def test_record_waste_exception(self, csv_layer):
        """Record a WASTE exception."""
        workflow = ExceptionWorkflow(csv_layer)
        
        txn, already_recorded = workflow.record_exception(
            event_type=EventType.WASTE,
            sku="SKU001",
            qty=10,
            event_date=date(2026, 1, 20),
            notes="Damaged goods",
        )
        
        assert already_recorded is False
        assert txn.event == EventType.WASTE
        assert txn.qty == 10
        
        # Verify ledger was written
        ledger_txns = csv_layer.read_transactions()
        assert len(ledger_txns) == 1
    
    def test_record_adjust_exception(self, csv_layer):
        """Record an ADJUST exception (signed qty)."""
        workflow = ExceptionWorkflow(csv_layer)
        
        txn, already_recorded = workflow.record_exception(
            event_type=EventType.ADJUST,
            sku="SKU001",
            qty=-5,  # Negative adjustment
            event_date=date(2026, 1, 20),
        )
        
        assert already_recorded is False
        assert txn.event == EventType.ADJUST
        assert txn.qty == -5
    
    def test_exception_idempotency(self, csv_layer):
        """Same exception recorded twice should be idempotent."""
        workflow = ExceptionWorkflow(csv_layer)
        
        event_date = date(2026, 1, 20)
        
        # First record
        txn1, already1 = workflow.record_exception(
            event_type=EventType.WASTE,
            sku="SKU001",
            qty=10,
            event_date=event_date,
        )
        assert already1 is False
        
        # Second record (same day, sku, type)
        txn2, already2 = workflow.record_exception(
            event_type=EventType.WASTE,
            sku="SKU001",
            qty=10,
            event_date=event_date,
        )
        assert already2 is True
        
        # Verify only one entry in ledger
        ledger_txns = csv_layer.read_transactions()
        assert len(ledger_txns) == 1
    
    def test_revert_exception_day(self, csv_layer):
        """Revert all exceptions of a type for a SKU on a date."""
        workflow = ExceptionWorkflow(csv_layer)
        
        event_date = date(2026, 1, 20)
        
        # Record multiple exceptions same day
        workflow.record_exception(EventType.WASTE, "SKU001", 5, event_date)
        workflow.record_exception(EventType.ADJUST, "SKU001", -3, event_date)
        
        # Verify 2 entries exist
        ledger_txns = csv_layer.read_transactions()
        assert len(ledger_txns) == 2
        
        # Revert WASTE exceptions
        reverted_count = workflow.revert_exception_day(event_date, "SKU001", EventType.WASTE)
        assert reverted_count == 1
        
        # Verify only ADJUST remains
        ledger_txns = csv_layer.read_transactions()
        assert len(ledger_txns) == 1
        assert ledger_txns[0].event == EventType.ADJUST


class TestDailySalesAverage:
    """Test calculation of daily sales average."""
    
    def test_daily_sales_avg_basic(self):
        """Calculate basic daily sales average."""
        sales = [
            SalesRecord(date=date(2026, 1, 1), sku="SKU001", qty_sold=10),
            SalesRecord(date=date(2026, 1, 2), sku="SKU001", qty_sold=15),
            SalesRecord(date=date(2026, 1, 3), sku="SKU001", qty_sold=20),
        ]
        
        avg = calculate_daily_sales_average(sales, "SKU001", days_lookback=30)
        assert avg == 15.0  # (10 + 15 + 20) / 3
    
    def test_daily_sales_avg_no_data(self):
        """Daily sales avg with no data should be 0.0."""
        avg = calculate_daily_sales_average([], "SKU001")
        assert avg == 0.0
