"""
Tests for lot tracking and FEFO consumption.
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile

from src.domain.models import Lot, Transaction, EventType, SKU
from src.persistence.csv_layer import CSVLayer
from src.workflows.receiving_v2 import ReceivingWorkflow
from src.domain.ledger import LotConsumptionManager


class TestLotModel:
    """Test Lot domain model."""
    
    def test_lot_creation_valid(self):
        """Test valid lot creation."""
        lot = Lot(
            lot_id="LOT-001",
            sku="SKU001",
            expiry_date=date(2026, 12, 31),
            qty_on_hand=100,
            receipt_id="REC-001",
            receipt_date=date(2026, 1, 1),
        )
        
        assert lot.lot_id == "LOT-001"
        assert lot.qty_on_hand == 100
        assert lot.expiry_date == date(2026, 12, 31)
    
    def test_lot_no_expiry(self):
        """Test lot without expiry date (non-perishable)."""
        lot = Lot(
            lot_id="LOT-002",
            sku="SKU002",
            expiry_date=None,
            qty_on_hand=50,
            receipt_id="REC-002",
            receipt_date=date(2026, 1, 1),
        )
        
        assert lot.expiry_date is None
        assert not lot.is_expired(date(2030, 1, 1))
        assert lot.days_until_expiry(date.today()) is None
    
    def test_lot_is_expired(self):
        """Test expiry check."""
        lot = Lot(
            lot_id="LOT-003",
            sku="SKU003",
            expiry_date=date(2026, 1, 1),
            qty_on_hand=10,
            receipt_id="REC-003",
            receipt_date=date(2025, 12, 1),
        )
        
        assert lot.is_expired(date(2026, 1, 2))
        assert not lot.is_expired(date(2025, 12, 31))
    
    def test_lot_days_until_expiry(self):
        """Test days until expiry calculation."""
        lot = Lot(
            lot_id="LOT-004",
            sku="SKU004",
            expiry_date=date(2026, 3, 1),
            qty_on_hand=20,
            receipt_id="REC-004",
            receipt_date=date(2026, 2, 1),
        )
        
        days = lot.days_until_expiry(date(2026, 2, 10))
        assert days == 19  # 1 Mar - 10 Feb = 19 days
    
    def test_lot_invalid_empty_id(self):
        """Test lot with empty ID raises error."""
        with pytest.raises(ValueError, match="Lot ID cannot be empty"):
            Lot(
                lot_id="",
                sku="SKU001",
                expiry_date=None,
                qty_on_hand=10,
                receipt_id="REC-001",
                receipt_date=date.today(),
            )
    
    def test_lot_invalid_negative_qty(self):
        """Test lot with negative qty raises error."""
        with pytest.raises(ValueError, match="Lot quantity cannot be negative"):
            Lot(
                lot_id="LOT-001",
                sku="SKU001",
                expiry_date=None,
                qty_on_hand=-5,
                receipt_id="REC-001",
                receipt_date=date.today(),
            )
    
    def test_lot_expiry_before_receipt(self):
        """Test expiry date before receipt date raises error."""
        with pytest.raises(ValueError, match="Expiry date cannot be before receipt date"):
            Lot(
                lot_id="LOT-001",
                sku="SKU001",
                expiry_date=date(2026, 1, 1),
                qty_on_hand=10,
                receipt_id="REC-001",
                receipt_date=date(2026, 2, 1),
            )


class TestLotPersistence:
    """Test lot CSV persistence."""
    
    @pytest.fixture
    def temp_csv_layer(self):
        """Create temporary CSV layer for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_layer = CSVLayer(data_dir=Path(tmpdir))
            yield csv_layer
    
    def test_write_and_read_lot(self, temp_csv_layer):
        """Test writing and reading lots."""
        lot = Lot(
            lot_id="LOT-TEST-001",
            sku="SKU001",
            expiry_date=date(2026, 12, 31),
            qty_on_hand=100,
            receipt_id="REC-001",
            receipt_date=date(2026, 2, 1),
        )
        
        temp_csv_layer.write_lot(lot)
        lots = temp_csv_layer.read_lots()
        
        assert len(lots) == 1
        assert lots[0].lot_id == "LOT-TEST-001"
        assert lots[0].qty_on_hand == 100
        assert lots[0].expiry_date == date(2026, 12, 31)
    
    def test_update_lot_quantity(self, temp_csv_layer):
        """Test updating lot quantity."""
        lot = Lot(
            lot_id="LOT-002",
            sku="SKU002",
            expiry_date=date(2026, 6, 30),
            qty_on_hand=50,
            receipt_id="REC-002",
            receipt_date=date(2026, 2, 1),
        )
        
        temp_csv_layer.write_lot(lot)
        temp_csv_layer.update_lot_quantity("LOT-002", 30)
        
        lots = temp_csv_layer.read_lots()
        assert len(lots) == 1
        assert lots[0].qty_on_hand == 30
    
    def test_update_lot_to_zero_removes(self, temp_csv_layer):
        """Test updating lot to 0 removes it."""
        lot = Lot(
            lot_id="LOT-003",
            sku="SKU003",
            expiry_date=date(2026, 3, 15),
            qty_on_hand=20,
            receipt_id="REC-003",
            receipt_date=date(2026, 2, 1),
        )
        
        temp_csv_layer.write_lot(lot)
        temp_csv_layer.update_lot_quantity("LOT-003", 0)
        
        lots = temp_csv_layer.read_lots()
        assert len(lots) == 0
    
    def test_get_lots_by_sku_fefo_order(self, temp_csv_layer):
        """Test getting lots by SKU in FEFO order."""
        lot1 = Lot("LOT-A", "SKU001", date(2026, 6, 1), 10, "REC-A", date(2026, 1, 1))
        lot2 = Lot("LOT-B", "SKU001", date(2026, 3, 1), 20, "REC-B", date(2026, 1, 2))
        lot3 = Lot("LOT-C", "SKU001", None, 30, "REC-C", date(2026, 1, 3))
        
        temp_csv_layer.write_lot(lot1)
        temp_csv_layer.write_lot(lot2)
        temp_csv_layer.write_lot(lot3)
        
        lots = temp_csv_layer.get_lots_by_sku("SKU001", sort_by_expiry=True)
        
        assert len(lots) == 3
        assert lots[0].lot_id == "LOT-B"  # Earliest expiry first
        assert lots[1].lot_id == "LOT-A"
        assert lots[2].lot_id == "LOT-C"  # No expiry last
    
    def test_get_expiring_lots(self, temp_csv_layer):
        """Test getting expiring lots within threshold."""
        today = date.today()
        
        lot_soon = Lot("LOT-SOON", "SKU001", today + timedelta(days=5), 10, "REC-1", today)
        lot_later = Lot("LOT-LATER", "SKU002", today + timedelta(days=40), 20, "REC-2", today)
        lot_no_expiry = Lot("LOT-NONE", "SKU003", None, 30, "REC-3", today)
        
        temp_csv_layer.write_lot(lot_soon)
        temp_csv_layer.write_lot(lot_later)
        temp_csv_layer.write_lot(lot_no_expiry)
        
        expiring = temp_csv_layer.get_expiring_lots(days_threshold=7)
        
        assert len(expiring) == 1
        assert expiring[0].lot_id == "LOT-SOON"
    
    def test_get_expired_lots(self, temp_csv_layer):
        """Test getting expired lots."""
        today = date.today()
        
        lot_expired = Lot("LOT-EXP", "SKU001", today - timedelta(days=5), 10, "REC-1", today - timedelta(days=30))
        lot_valid = Lot("LOT-OK", "SKU002", today + timedelta(days=10), 20, "REC-2", today)
        
        temp_csv_layer.write_lot(lot_expired)
        temp_csv_layer.write_lot(lot_valid)
        
        expired = temp_csv_layer.get_expired_lots()
        
        assert len(expired) == 1
        assert expired[0].lot_id == "LOT-EXP"


class TestFEFOConsumption:
    """Test FEFO consumption logic."""
    
    @pytest.fixture
    def temp_csv_layer(self):
        """Create temporary CSV layer for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_layer = CSVLayer(data_dir=Path(tmpdir))
            # Add test SKU
            sku = SKU(sku="SKU001", description="Test Product")
            csv_layer.write_sku(sku)
            yield csv_layer
    
    def test_fefo_consume_from_earliest_expiry(self, temp_csv_layer):
        """Test FEFO consumes from lot with earliest expiry first."""
        today = date.today()
        
        lot1 = Lot("LOT-A", "SKU001", today + timedelta(days=10), 50, "REC-A", today)
        lot2 = Lot("LOT-B", "SKU001", today + timedelta(days=5), 30, "REC-B", today)
        
        temp_csv_layer.write_lot(lot1)
        temp_csv_layer.write_lot(lot2)
        
        lots = temp_csv_layer.get_lots_by_sku("SKU001", sort_by_expiry=True)
        consumption = LotConsumptionManager.consume_from_lots("SKU001", 20, lots, temp_csv_layer)
        
        assert len(consumption) == 1
        assert consumption[0]["lot_id"] == "LOT-B"  # Earliest expiry
        assert consumption[0]["qty_consumed"] == 20
        
        # Verify lot updated
        updated_lots = temp_csv_layer.read_lots()
        lot_b = next(l for l in updated_lots if l.lot_id == "LOT-B")
        assert lot_b.qty_on_hand == 10  # 30 - 20
    
    def test_fefo_consume_across_multiple_lots(self, temp_csv_layer):
        """Test FEFO consumption across multiple lots."""
        today = date.today()
        
        lot1 = Lot("LOT-A", "SKU001", today + timedelta(days=5), 20, "REC-A", today)
        lot2 = Lot("LOT-B", "SKU001", today + timedelta(days=10), 30, "REC-B", today)
        
        temp_csv_layer.write_lot(lot1)
        temp_csv_layer.write_lot(lot2)
        
        lots = temp_csv_layer.get_lots_by_sku("SKU001", sort_by_expiry=True)
        consumption = LotConsumptionManager.consume_from_lots("SKU001", 35, lots, temp_csv_layer)
        
        assert len(consumption) == 2
        assert consumption[0]["lot_id"] == "LOT-A"
        assert consumption[0]["qty_consumed"] == 20
        assert consumption[1]["lot_id"] == "LOT-B"
        assert consumption[1]["qty_consumed"] == 15
        
        # Verify lots updated
        updated_lots = temp_csv_layer.read_lots()
        assert len(updated_lots) == 1  # LOT-A depleted (qty=0 removed)
        lot_b = updated_lots[0]
        assert lot_b.lot_id == "LOT-B"
        assert lot_b.qty_on_hand == 15
    
    def test_fefo_insufficient_stock_raises_error(self, temp_csv_layer):
        """Test FEFO raises error when insufficient stock."""
        today = date.today()
        
        lot1 = Lot("LOT-A", "SKU001", today + timedelta(days=5), 10, "REC-A", today)
        temp_csv_layer.write_lot(lot1)
        
        lots = temp_csv_layer.get_lots_by_sku("SKU001", sort_by_expiry=True)
        
        with pytest.raises(ValueError, match="Insufficient stock in lots"):
            LotConsumptionManager.consume_from_lots("SKU001", 50, lots, temp_csv_layer)


class TestReceivingWithLots:
    """Test receiving workflow with lot creation."""
    
    @pytest.fixture
    def temp_workflow(self):
        """Create temporary receiving workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_layer = CSVLayer(data_dir=Path(tmpdir))
            # Add test SKU with has_expiry_label=True so manual expiry date is honoured
            sku = SKU(sku="SKU001", description="Test Product", has_expiry_label=True)
            csv_layer.write_sku(sku)
            workflow = ReceivingWorkflow(csv_layer)
            yield workflow, csv_layer
    
    def test_receiving_creates_lot_with_expiry(self, temp_workflow):
        """Test receiving creates lot when expiry_date provided."""
        workflow, csv_layer = temp_workflow
        
        # Create an order first
        csv_layer.write_order_log(
            order_id="ORD-001",
            date_str=date.today().isoformat(),
            sku="SKU001",
            qty=100,
            status="PENDING",
            receipt_date=date.today().isoformat(),
        )
        
        items = [{
            "sku": "SKU001",
            "qty_received": 100,
            "order_ids": [],
            "expiry_date": "2026-12-31",
        }]
        
        txns, skip, updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-001",
            receipt_date=date.today(),
            items=items,
        )
        
        # Verify lot created
        lots = csv_layer.read_lots()
        assert len(lots) == 1
        assert lots[0].lot_id  # auto-generated, non-empty
        assert lots[0].qty_on_hand == 100
        assert lots[0].expiry_date == date(2026, 12, 31)
    
    def test_receiving_auto_generates_lot_id(self, temp_workflow):
        """Test receiving auto-generates lot_id if not provided."""
        workflow, csv_layer = temp_workflow
        
        # Create an order first
        csv_layer.write_order_log(
            order_id="ORD-002",
            date_str=date.today().isoformat(),
            sku="SKU001",
            qty=50,
            status="PENDING",
            receipt_date=date.today().isoformat(),
        )
        
        items = [{
            "sku": "SKU001",
            "qty_received": 50,
            "order_ids": [],
            "expiry_date": "2026-06-30",
        }]
        
        txns, skip, updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-002",
            receipt_date=date.today(),
            items=items,
        )
        
        # Verify lot created with auto-generated ID
        lots = csv_layer.read_lots()
        assert len(lots) == 1
        assert lots[0].lot_id.startswith(date.today().isoformat())
        assert lots[0].qty_on_hand == 50
    
    def test_receiving_no_lot_without_expiry(self, temp_workflow):
        """Test receiving skips lot creation if no expiry_date or lot_id."""
        workflow, csv_layer = temp_workflow
        
        items = [{
            "sku": "SKU001",
            "qty_received": 30,
            "order_ids": [],
            "expiry_date": "",
        }]
        
        txns, skip, updates = workflow.close_receipt_by_document(
            document_id="DDT-TEST-003",
            receipt_date=date.today(),
            items=items,
        )
        
        # Verify no lot created
        lots = csv_layer.read_lots()
        assert len(lots) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
