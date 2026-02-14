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
        
        expected_headers = [
            "sku", "description", "ean", "moq", "pack_size", "lead_time_days",
            "review_period", "safety_stock", "shelf_life_days", "min_shelf_life_days",
            "waste_penalty_mode", "waste_penalty_factor", "waste_risk_threshold",
            "max_stock", "reorder_point", "demand_variability", "category", "department",
            "oos_boost_percent", "oos_detection_mode", "oos_popup_preference",
            "forecast_method", "mc_distribution", "mc_n_simulations", "mc_random_seed",
            "mc_output_stat", "mc_output_percentile", "mc_horizon_mode", "mc_horizon_days",
            "in_assortment", "target_csl",
        ]
        assert headers == expected_headers


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
    
    def test_target_csl_roundtrip(self, csv_layer):
        """Write SKU with target_csl and read it back (roundtrip preservation)."""
        sku = SKU(
            sku="SKU_CSL",
            description="SKU with CSL override",
            target_csl=0.95
        )
        csv_layer.write_sku(sku)
        
        skus = csv_layer.read_skus()
        assert len(skus) == 1
        assert skus[0].target_csl == 0.95, f"Expected target_csl=0.95, got {skus[0].target_csl}"
    
    def test_read_skus_legacy_no_target_csl(self, temp_data_dir):
        """Test backward compatibility: read legacy skus.csv without target_csl column."""
        import csv
        from pathlib import Path
        
        # Create legacy skus.csv without target_csl column
        skus_file = temp_data_dir / "skus.csv"
        with open(skus_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "sku", "description", "ean", "moq", "pack_size", "lead_time_days",
                "review_period", "safety_stock", "shelf_life_days", "min_shelf_life_days",
                "waste_penalty_mode", "waste_penalty_factor", "waste_risk_threshold",
                "max_stock", "reorder_point", "demand_variability", "category", "department",
                "oos_boost_percent", "oos_detection_mode", "oos_popup_preference",
                "forecast_method", "mc_distribution", "mc_n_simulations", "mc_random_seed",
                "mc_output_stat", "mc_output_percentile", "mc_horizon_mode", "mc_horizon_days",
                "in_assortment"
                # Note: NO target_csl column
            ])
            writer.writeheader()
            writer.writerow({
                "sku": "LEGACY001",
                "description": "Legacy SKU",
                "ean": "",
                "moq": "1",
                "pack_size": "1",
                "lead_time_days": "7",
                "review_period": "7",
                "safety_stock": "0",
                "shelf_life_days": "0",
                "min_shelf_life_days": "0",
                "waste_penalty_mode": "",
                "waste_penalty_factor": "0",
                "waste_risk_threshold": "0",
                "max_stock": "999",
                "reorder_point": "10",
                "demand_variability": "STABLE",
                "category": "",
                "department": "",
                "oos_boost_percent": "0",
                "oos_detection_mode": "",
                "oos_popup_preference": "ask",
                "forecast_method": "",
                "mc_distribution": "",
                "mc_n_simulations": "0",
                "mc_random_seed": "0",
                "mc_output_stat": "",
                "mc_output_percentile": "0",
                "mc_horizon_mode": "",
                "mc_horizon_days": "0",
                "in_assortment": "true",
            })
        
        # Read using csv_layer (should not crash, should default target_csl to 0.0)
        from src.persistence.csv_layer import CSVLayer
        csv_layer_legacy = CSVLayer(temp_data_dir)
        
        skus = csv_layer_legacy.read_skus()
        assert len(skus) == 1
        assert skus[0].sku == "LEGACY001"
        assert skus[0].target_csl == 0.0, f"Legacy SKU should default target_csl to 0.0, got {skus[0].target_csl}"


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


class TestSKUCRUD:
    """Test SKU CRUD operations (Create, Read, Update, Delete)."""
    
    def test_sku_exists_true(self, csv_layer):
        """Test sku_exists returns True for existing SKU."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product 1"))
        assert csv_layer.sku_exists("SKU001") is True
    
    def test_sku_exists_false(self, csv_layer):
        """Test sku_exists returns False for non-existent SKU."""
        assert csv_layer.sku_exists("UNKNOWN") is False
    
    def test_search_skus_empty_query(self, csv_layer):
        """Search with empty query returns all SKUs."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product 1"))
        csv_layer.write_sku(SKU(sku="SKU002", description="Product 2"))
        
        results = csv_layer.search_skus("")
        assert len(results) == 2
    
    def test_search_skus_by_sku_code(self, csv_layer):
        """Search SKUs by SKU code."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Coffee"))
        csv_layer.write_sku(SKU(sku="SKU002", description="Tea"))
        csv_layer.write_sku(SKU(sku="ABC123", description="Milk"))
        
        results = csv_layer.search_skus("SKU")
        assert len(results) == 2
        assert all("SKU" in r.sku for r in results)
    
    def test_search_skus_by_description(self, csv_layer):
        """Search SKUs by description."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Arabica Coffee"))
        csv_layer.write_sku(SKU(sku="SKU002", description="Green Tea"))
        csv_layer.write_sku(SKU(sku="SKU003", description="Coffee Beans"))
        
        results = csv_layer.search_skus("coffee")
        assert len(results) == 2
        assert all("coffee" in r.description.lower() for r in results)
    
    def test_search_skus_case_insensitive(self, csv_layer):
        """Search is case-insensitive."""
        csv_layer.write_sku(SKU(sku="sku001", description="Product"))
        
        results = csv_layer.search_skus("SKU001")
        assert len(results) == 1
        
        results = csv_layer.search_skus("product")
        assert len(results) == 1
    
    def test_update_sku_description_only(self, csv_layer):
        """Update SKU description without changing code."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Old Description", ean="1234567890123"))
        
        success = csv_layer.update_sku("SKU001", "SKU001", "New Description", "1234567890123")
        assert success is True
        
        skus = csv_layer.read_skus()
        assert len(skus) == 1
        assert skus[0].sku == "SKU001"
        assert skus[0].description == "New Description"
        assert skus[0].ean == "1234567890123"
    
    def test_update_sku_ean_only(self, csv_layer):
        """Update SKU EAN without changing code."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product", ean="1234567890123"))
        
        success = csv_layer.update_sku("SKU001", "SKU001", "Product", "9876543210987")
        assert success is True
        
        skus = csv_layer.read_skus()
        assert skus[0].ean == "9876543210987"
    
    def test_update_sku_code_only(self, csv_layer):
        """Update SKU code (should update ledger references)."""
        # Create SKU and add transaction
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_transaction(
            Transaction(
                date=date(2026, 1, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=100,
            )
        )
        
        # Update SKU code
        success = csv_layer.update_sku("SKU001", "SKU999", "Product", None)
        assert success is True
        
        # Verify SKU updated
        skus = csv_layer.read_skus()
        assert len(skus) == 1
        assert skus[0].sku == "SKU999"
        
        # Verify transaction references updated
        txns = csv_layer.read_transactions()
        assert len(txns) == 1
        assert txns[0].sku == "SKU999"
    
    def test_update_sku_not_found(self, csv_layer):
        """Update non-existent SKU returns False."""
        success = csv_layer.update_sku("UNKNOWN", "NEW", "Desc", None)
        assert success is False
    
    def test_update_sku_propagates_to_sales(self, csv_layer):
        """Update SKU code propagates to sales records."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_sales_record(
            SalesRecord(date=date(2026, 1, 1), sku="SKU001", qty_sold=10)
        )
        
        csv_layer.update_sku("SKU001", "SKU999", "Product", None)
        
        sales = csv_layer.read_sales()
        assert sales[0].sku == "SKU999"
    
    def test_update_sku_propagates_to_order_logs(self, csv_layer):
        """Update SKU code propagates to order logs."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_order_log("ORD001", "2026-01-01", "SKU001", 50, "PENDING")
        
        csv_layer.update_sku("SKU001", "SKU999", "Product", None)
        
        orders = csv_layer.read_order_logs()
        assert orders[0]["sku"] == "SKU999"
    
    def test_update_sku_propagates_to_receiving_logs(self, csv_layer):
        """Update SKU code propagates to receiving logs."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_receiving_log("REC001", "2026-01-01", "SKU001", 50, "2026-01-05")
        
        csv_layer.update_sku("SKU001", "SKU999", "Product", None)
        
        receives = csv_layer.read_receiving_logs()
        assert receives[0]["sku"] == "SKU999"
    
    def test_delete_sku_success(self, csv_layer):
        """Delete SKU with no ledger references."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product 1"))
        csv_layer.write_sku(SKU(sku="SKU002", description="Product 2"))
        
        deleted = csv_layer.delete_sku("SKU001")
        assert deleted is True
        
        skus = csv_layer.read_skus()
        assert len(skus) == 1
        assert skus[0].sku == "SKU002"
    
    def test_delete_sku_not_found(self, csv_layer):
        """Delete non-existent SKU returns False."""
        deleted = csv_layer.delete_sku("UNKNOWN")
        assert deleted is False
    
    def test_can_delete_sku_no_references(self, csv_layer):
        """Can delete SKU with no references."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        
        can_delete, reason = csv_layer.can_delete_sku("SKU001")
        assert can_delete is True
        assert reason == ""
    
    def test_can_delete_sku_with_transactions(self, csv_layer):
        """Cannot delete SKU with transactions in ledger."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_transaction(
            Transaction(
                date=date(2026, 1, 1),
                sku="SKU001",
                event=EventType.SNAPSHOT,
                qty=100,
            )
        )
        
        can_delete, reason = csv_layer.can_delete_sku("SKU001")
        assert can_delete is False
        assert "transactions" in reason.lower()
    
    def test_can_delete_sku_with_sales(self, csv_layer):
        """Cannot delete SKU with sales records."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_sales_record(
            SalesRecord(date=date(2026, 1, 1), sku="SKU001", qty_sold=10)
        )
        
        can_delete, reason = csv_layer.can_delete_sku("SKU001")
        assert can_delete is False
        assert "sales" in reason.lower()
    
    def test_can_delete_sku_with_orders(self, csv_layer):
        """Cannot delete SKU with order history."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_order_log("ORD001", "2026-01-01", "SKU001", 50, "PENDING")
        
        can_delete, reason = csv_layer.can_delete_sku("SKU001")
        assert can_delete is False
        assert "order" in reason.lower()
    
    def test_can_delete_sku_with_receives(self, csv_layer):
        """Cannot delete SKU with receiving history."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        csv_layer.write_receiving_log("REC001", "2026-01-01", "SKU001", 50, "2026-01-05")
        
        can_delete, reason = csv_layer.can_delete_sku("SKU001")
        assert can_delete is False
        assert "receiving" in reason.lower()
    
    def test_delete_workflow_full(self, csv_layer):
        """Full delete workflow: check, then delete."""
        csv_layer.write_sku(SKU(sku="SKU001", description="Product"))
        
        # Check first
        can_delete, reason = csv_layer.can_delete_sku("SKU001")
        assert can_delete is True
        
        # Then delete
        deleted = csv_layer.delete_sku("SKU001")
        assert deleted is True
        
        # Verify gone
        assert csv_layer.sku_exists("SKU001") is False

