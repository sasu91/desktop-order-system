"""
Test suite for CSV persistence layer.

Tests auto-create, read/write, and data integrity.
"""
import pytest
from datetime import date
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Transaction, EventType, SalesRecord
from src.persistence.csv_layer import CSVLayer


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


class TestCSVLayerAutoCreate:
    """Test auto-creation of CSV files."""
    
    def test_all_files_created_on_init(self, temp_data_dir):
        """All CSV files should be created on initialization."""
        csv_layer = CSVLayer(data_dir=temp_data_dir)
        
        expected_files = [
            "skus.csv",
            "transactions.csv",
            "sales.csv",
            "order_logs.csv",
            "receiving_logs.csv",
        ]
        
        for filename in expected_files:
            assert (temp_data_dir / filename).exists()
    
    def test_files_have_correct_headers(self, temp_data_dir):
        """Created files should have correct headers."""
        csv_layer = CSVLayer(data_dir=temp_data_dir)
        
        # Read skus.csv
        with open(temp_data_dir / "skus.csv", "r") as f:
            headers = f.readline().strip().split(",")
        
        assert headers == ["sku", "description", "ean"]


class TestSKUOperations:
    """Test SKU read/write operations."""
    
    def test_write_and_read_sku(self, csv_layer):
        """Write SKU and read it back."""
        sku = SKU(sku="SKU001", description="Test Product", ean="5901234123457")
        csv_layer.write_sku(sku)
        
        skus = csv_layer.read_skus()
        assert len(skus) == 1
        assert skus[0].sku == "SKU001"
        assert skus[0].description == "Test Product"
        assert skus[0].ean == "5901234123457"
    
    def test_write_sku_with_empty_ean(self, csv_layer):
        """Write SKU with empty EAN."""
        sku = SKU(sku="SKU001", description="Test Product", ean=None)
        csv_layer.write_sku(sku)
        
        skus = csv_layer.read_skus()
        assert len(skus) == 1
        assert skus[0].ean is None
    
    def test_get_all_sku_ids(self, csv_layer):
        """Get all SKU identifiers."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Prod 1"))
        csv_layer.write_sku(SKU(sku="SKU002", description="Prod 2"))
        
        sku_ids = csv_layer.get_all_sku_ids()
        assert set(sku_ids) == {"SKU001", "SKU002"}


class TestTransactionOperations:
    """Test transaction read/write operations."""
    
    def test_write_and_read_transaction(self, csv_layer):
        """Write transaction and read it back."""
        txn = Transaction(
            date=date(2026, 1, 1),
            sku="SKU001",
            event=EventType.SNAPSHOT,
            qty=100,
        )
        csv_layer.write_transaction(txn)
        
        txns = csv_layer.read_transactions()
        assert len(txns) == 1
        assert txns[0].sku == "SKU001"
        assert txns[0].qty == 100
        assert txns[0].event == EventType.SNAPSHOT
    
    def test_write_transactions_batch(self, csv_layer):
        """Write multiple transactions at once."""
        txns = [
            Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
            Transaction(date=date(2026, 1, 5), sku="SKU001", event=EventType.ORDER, qty=50),
        ]
        csv_layer.write_transactions_batch(txns)
        
        read_txns = csv_layer.read_transactions()
        assert len(read_txns) == 2
        assert read_txns[0].event == EventType.SNAPSHOT
        assert read_txns[1].event == EventType.ORDER


class TestSalesOperations:
    """Test sales read/write operations."""
    
    def test_write_and_read_sales(self, csv_layer):
        """Write sales record and read it back."""
        sale = SalesRecord(
            date=date(2026, 1, 1),
            sku="SKU001",
            qty_sold=10,
        )
        csv_layer.write_sales_record(sale)
        
        sales = csv_layer.read_sales()
        assert len(sales) == 1
        assert sales[0].sku == "SKU001"
        assert sales[0].qty_sold == 10


class TestOrderLogOperations:
    """Test order log operations."""
    
    def test_write_order_log(self, csv_layer):
        """Write order log entry."""
        csv_layer.write_order_log(
            order_id="ORD001",
            date_str="2026-01-01",
            sku="SKU001",
            qty=50,
            status="PENDING",
        )
        
        logs = csv_layer.read_order_logs()
        assert len(logs) == 1
        assert logs[0]["order_id"] == "ORD001"
        assert logs[0]["qty_ordered"] == "50"
