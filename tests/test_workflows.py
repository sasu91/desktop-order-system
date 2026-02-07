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
from src.workflows.daily_close import DailyCloseWorkflow


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
        """Generate basic order proposal using new formula."""
        workflow = OrderWorkflow(csv_layer, lead_time_days=7)
        
        # Test scenario: low stock, need to reorder
        current_stock = Stock(sku="SKU001", on_hand=20, on_order=0)
        proposal = workflow.generate_proposal(
            sku="SKU001",
            description="Test Product",
            current_stock=current_stock,
            daily_sales_avg=5.0,
            min_stock=20,
            days_cover=30,  # DEPRECATED, formula now uses lead_time + review_period
        )
        
        # NEW FORMULA: S = forecast × (lead_time + review_period) + safety_stock
        # forecast_period = 7 + 7 = 14 days
        # forecast_qty = 5.0 × 14 = 70
        # safety_stock = 0 (default)
        # S = 70 + 0 = 70
        # IP = 20 + 0 = 20
        # proposed = max(0, 70 - 20) = 50
        assert proposal.proposed_qty == 50
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
        """Calculate basic daily sales average using calendar-based approach."""
        # Create sales for 3 consecutive days
        today = date.today()
        sales = [
            SalesRecord(date=today - timedelta(days=2), sku="SKU001", qty_sold=10),
            SalesRecord(date=today - timedelta(days=1), sku="SKU001", qty_sold=15),
            SalesRecord(date=today, sku="SKU001", qty_sold=20),
        ]
        
        # Calendar-based: 45 total sales over 30 days → avg = 45/30 = 1.5
        avg, oos_count = calculate_daily_sales_average(sales, "SKU001", days_lookback=30)
        assert avg == 1.5  # (10 + 15 + 20) / 30 calendar days
        assert oos_count == 0  # No OOS days (transactions not provided)
    
    def test_daily_sales_avg_no_data(self):
        """Daily sales avg with no data should be 0.0."""
        avg, oos_count = calculate_daily_sales_average([], "SKU001")
        assert avg == 0.0
        assert oos_count == 0  # No OOS days


class TestDailyCloseWorkflow:
    """Test daily closing workflow for EOD stock entry and sales calculation."""
    
    def test_process_eod_stock_basic(self, csv_layer):
        """Process basic EOD stock entry with sales calculation."""
        # Setup: Add a SKU
        from src.domain.models import SKU, DemandVariability
        csv_layer.write_sku(SKU(
            sku="SKU001",
            description="Test Product",
            ean="",
            moq=1,
            lead_time_days=7,
            max_stock=999,
            reorder_point=10,
            demand_variability=DemandVariability.STABLE,
        ))
        
        # Add initial snapshot
        csv_layer.write_transaction(Transaction(
            date=date(2026, 1, 1),
            sku="SKU001",
            event=EventType.SNAPSHOT,
            qty=100,
        ))
        
        # Process EOD: stock at end of day 2026-01-02 is 80 (so 20 sold)
        workflow = DailyCloseWorkflow(csv_layer)
        sale, adjust, status = workflow.process_eod_stock(
            sku="SKU001",
            eod_date=date(2026, 1, 2),
            eod_stock_on_hand=80,
        )
        
        # Should record sale of 20
        assert sale is not None
        assert sale.qty_sold == 20
        assert sale.date == date(2026, 1, 2)
        
        # No adjustment needed (theoretical = EOD)
        assert adjust is None
        
        # Verify sale written to sales.csv
        sales = csv_layer.read_sales()
        assert len(sales) == 1
        assert sales[0].sku == "SKU001"
        assert sales[0].qty_sold == 20
    
    def test_process_eod_stock_with_adjustment(self, csv_layer):
        """Process EOD with stock discrepancy (shrinkage)."""
        from src.domain.models import SKU, DemandVariability
        csv_layer.write_sku(SKU(
            sku="SKU001",
            description="Test Product",
            ean="",
            moq=1,
            lead_time_days=7,
            max_stock=999,
            reorder_point=10,
            demand_variability=DemandVariability.STABLE,
        ))
        
        # Initial stock: 100
        csv_layer.write_transaction(Transaction(
            date=date(2026, 1, 1),
            sku="SKU001",
            event=EventType.SNAPSHOT,
            qty=100,
        ))
        
        # EOD stock is 75 (expected 100, so 25 missing)
        # This could be 20 sold + 5 shrinkage
        workflow = DailyCloseWorkflow(csv_layer)
        sale, adjust, status = workflow.process_eod_stock(
            sku="SKU001",
            eod_date=date(2026, 1, 2),
            eod_stock_on_hand=75,
        )
        
        # Should record sale of 25
        assert sale is not None
        assert sale.qty_sold == 25
        
        # No adjustment because we accounted for all discrepancy with sales
        # (In real scenario, if there was additional shrinkage beyond sales, adjust would be non-None)
        assert adjust is None
    
    def test_process_eod_stock_idempotency(self, csv_layer):
        """Process same EOD twice should update, not duplicate."""
        from src.domain.models import SKU, DemandVariability
        csv_layer.write_sku(SKU(
            sku="SKU001",
            description="Test Product",
            ean="",
            moq=1,
            lead_time_days=7,
            max_stock=999,
            reorder_point=10,
            demand_variability=DemandVariability.STABLE,
        ))
        
        csv_layer.write_transaction(Transaction(
            date=date(2026, 1, 1),
            sku="SKU001",
            event=EventType.SNAPSHOT,
            qty=100,
        ))
        
        workflow = DailyCloseWorkflow(csv_layer)
        
        # First EOD entry
        workflow.process_eod_stock("SKU001", date(2026, 1, 2), 80)
        sales_1 = csv_layer.read_sales()
        assert len(sales_1) == 1
        
        # Second EOD entry (same date, different value)
        workflow.process_eod_stock("SKU001", date(2026, 1, 2), 85)
        sales_2 = csv_layer.read_sales()
        
        # Should still have 1 sale record (updated, not duplicated)
        assert len(sales_2) == 1
        assert sales_2[0].qty_sold == 15  # Updated value
    
    def test_process_bulk_eod_stock(self, csv_layer):
        """Process multiple SKUs at once."""
        from src.domain.models import SKU, DemandVariability
        
        # Add multiple SKUs
        for i in range(3):
            csv_layer.write_sku(SKU(
                sku=f"SKU00{i+1}",
                description=f"Product {i+1}",
                ean="",
                moq=1,
                lead_time_days=7,
                max_stock=999,
                reorder_point=10,
                demand_variability=DemandVariability.STABLE,
            ))
            csv_layer.write_transaction(Transaction(
                date=date(2026, 1, 1),
                sku=f"SKU00{i+1}",
                event=EventType.SNAPSHOT,
                qty=100,
            ))
        
        # Bulk EOD
        workflow = DailyCloseWorkflow(csv_layer)
        results = workflow.process_bulk_eod_stock(
            eod_entries={
                "SKU001": 90,
                "SKU002": 85,
                "SKU003": 95,
            },
            eod_date=date(2026, 1, 2),
        )
        
        # All 3 should succeed
        assert len(results) == 3
        assert all("✓" in r for r in results)
        
        # Verify sales
        sales = csv_layer.read_sales()
        assert len(sales) == 3
        
    def test_process_eod_invalid_sku(self, csv_layer):
        """Process EOD for non-existent SKU should raise error."""
        workflow = DailyCloseWorkflow(csv_layer)
        
        with pytest.raises(ValueError, match="does not exist"):
            workflow.process_eod_stock("INVALID", date(2026, 1, 2), 50)
    
    def test_process_eod_negative_stock(self, csv_layer):
        """Process EOD with negative stock should raise error."""
        from src.domain.models import SKU, DemandVariability
        csv_layer.write_sku(SKU(
            sku="SKU001",
            description="Test",
            ean="",
            moq=1,
            lead_time_days=7,
            max_stock=999,
            reorder_point=10,
            demand_variability=DemandVariability.STABLE,
        ))
        
        workflow = DailyCloseWorkflow(csv_layer)
        
        with pytest.raises(ValueError, match="cannot be negative"):
            workflow.process_eod_stock("SKU001", date(2026, 1, 2), -10)

