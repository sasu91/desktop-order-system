"""
Test Suite for FASE 5: StorageAdapter

Tests:
1. Adapter initialization (CSV and SQLite modes)
2. Backend routing (write/read operations)
3. Fallback behavior (SQLite → CSV on error)
4. Dual-mode compatibility
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import date

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.persistence.storage_adapter import StorageAdapter
from src.domain.models import SKU, Transaction, EventType, SalesRecord, DemandVariability


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory"""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def csv_adapter(temp_data_dir):
    """Create CSV-mode adapter"""
    adapter = StorageAdapter(data_dir=temp_data_dir, force_backend='csv')
    yield adapter
    adapter.close()


class TestAdapterInitialization:
    """Test adapter initialization and backend detection"""
    
    def test_csv_mode_initialization(self, temp_data_dir):
        """Test adapter initializes in CSV mode"""
        adapter = StorageAdapter(data_dir=temp_data_dir, force_backend='csv')
        
        assert adapter.get_backend() == 'csv'
        assert not adapter.is_sqlite_mode()
        
        adapter.close()
    
    def test_backend_fallback_when_sqlite_unavailable(self,temp_data_dir):
        """Test automatic fallback to CSV when SQLite not available"""
        # Force SQLite mode, but database doesn't exist
        adapter = StorageAdapter(data_dir=temp_data_dir, force_backend='sqlite')
        
        # Should fallback to CSV
        assert adapter.get_backend() == 'csv'
        
        adapter.close()


class TestSKUOperations:
    """Test SKU read/write operations"""
    
    def test_write_and_read_sku_csv_mode(self, csv_adapter):
        """Test write and read SKU in CSV mode"""
        sku = SKU(
            sku='TEST001',
            description='Test Product',
            ean='1234567890123',
            moq=10,
            lead_time_days=7,
        )
        
        # Write SKU
        csv_adapter.write_sku(sku)
        
        # Read SKUs
        skus = csv_adapter.read_skus()
        
        assert len(skus) == 1
        assert skus[0].sku == 'TEST001'
        assert skus[0].description == 'Test Product'
        assert skus[0].moq == 10
    
    def test_sku_exists_check(self, csv_adapter):
        """Test SKU existence check"""
        sku = SKU(sku='TEST002', description='Test Product 2')
        csv_adapter.write_sku(sku)
        
        assert csv_adapter.sku_exists('TEST002')
        assert not csv_adapter.sku_exists('NONEXISTENT')
    
    def test_get_all_sku_ids(self, csv_adapter):
        """Test get all SKU IDs"""
        csv_adapter.write_sku(SKU(sku='SKU001', description='Product 1'))
        csv_adapter.write_sku(SKU(sku='SKU002', description='Product 2'))
        csv_adapter.write_sku(SKU(sku='SKU003', description='Product 3'))
        
        sku_ids = csv_adapter.get_all_sku_ids()
        
        assert len(sku_ids) == 3
        assert 'SKU001' in sku_ids
        assert 'SKU002' in sku_ids
        assert 'SKU003' in sku_ids
    
    def test_delete_sku(self, csv_adapter):
        """Test SKU deletion"""
        csv_adapter.write_sku(SKU(sku='DELETE_ME', description='To be deleted'))
        
        assert csv_adapter.sku_exists('DELETE_ME')
        
        success = csv_adapter.delete_sku('DELETE_ME')
        
        assert success
        assert not csv_adapter.sku_exists('DELETE_ME')


class TestTransactionOperations:
    """Test transaction read/write operations"""
    
    def test_write_and_read_transaction(self, csv_adapter):
        """Test write and read transaction"""
        # First create SKU (required for FK)
        csv_adapter.write_sku(SKU(sku='TXN001', description='Product'))
        
        # Write transaction
        txn = Transaction(
            date=date(2024, 1, 15),
            sku='TXN001',
            event=EventType.SNAPSHOT,
            qty=100,
        )
        csv_adapter.write_transaction(txn)
        
        # Read transactions
        txns = csv_adapter.read_transactions()
        
        assert len(txns) >= 1
        assert any(t.sku == 'TXN001' and t.event == EventType.SNAPSHOT for t in txns)
    
    def test_write_transactions_batch(self, csv_adapter):
        """Test batch transaction write"""
        csv_adapter.write_sku(SKU(sku='BATCH001', description='Product'))
        
        txns = [
            Transaction(date=date(2024, 1, 1), sku='BATCH001', event=EventType.SNAPSHOT, qty=50),
            Transaction(date=date(2024, 1, 2), sku='BATCH001', event=EventType.SALE, qty=-10),
            Transaction(date=date(2024, 1, 3), sku='BATCH001', event=EventType.ORDER, qty=20, receipt_date=date(2024, 1, 10)),
        ]
        
        csv_adapter.write_transactions_batch(txns)
        
        all_txns = csv_adapter.read_transactions()
        batch_txns = [t for t in all_txns if t.sku == 'BATCH001']
        
        assert len(batch_txns) == 3


class TestSalesOperations:
    """Test sales read/write operations"""
    
    def test_write_and_read_sales(self, csv_adapter):
        """Test write and read sales record"""
        csv_adapter.write_sku(SKU(sku='SALES001', description='Product'))
        
        sale = SalesRecord(
            date=date(2024, 1, 15),
            sku='SALES001',
            qty_sold=25,
            promo_flag=0,
        )
        
        csv_adapter.write_sales_record(sale)
        
        sales = csv_adapter.read_sales()
        
        assert len(sales) >= 1
        assert any(s.sku == 'SALES001' and s.qty_sold == 25 for s in sales)
    
    def test_append_sales_alias(self, csv_adapter):
        """Test append_sales (alias for write_sales_record)"""
        csv_adapter.write_sku(SKU(sku='SALES002', description='Product'))
        
        sale = SalesRecord(
            date=date(2024, 1, 16),
            sku='SALES002',
            qty_sold=30,
        )
        
        csv_adapter.append_sales(sale)
        
        sales = csv_adapter.read_sales()
        
        assert any(s.sku == 'SALES002' for s in sales)


class TestSettingsOperations:
    """Test settings read/write operations"""
    
    def test_read_write_settings(self, csv_adapter):
        """Test read and write settings"""
        settings = {
            'default_lead_time': 7,
            'target_csl': 0.95,
            'warehouse_id': 'WH001',
        }
        
        csv_adapter.write_settings(settings)
        
        loaded_settings = csv_adapter.read_settings()
        
        assert loaded_settings['default_lead_time'] == 7
        assert loaded_settings['target_csl'] == 0.95
        assert loaded_settings['warehouse_id'] == 'WH001'
    
    def test_get_default_sku_params(self, csv_adapter):
        """Test get default SKU parameters"""
        defaults = csv_adapter.get_default_sku_params()
        
        # Should have some default values
        assert 'lead_time_days' in defaults or 'default_lead_time' in defaults


class TestHolidaysOperations:
    """Test holidays read/write operations"""
    
    def test_read_write_holidays(self, csv_adapter):
        """Test read and write holidays"""
        holidays = [
            {'date': '2024-01-01', 'name': 'New Year'},
            {'date': '2024-12-25', 'name': 'Christmas'},
        ]
        
        csv_adapter.write_holidays(holidays)
        
        loaded_holidays = csv_adapter.read_holidays()
        
        assert len(loaded_holidays) == 2
        assert any(h['name'] == 'New Year' for h in loaded_holidays)
    
    def test_add_holiday(self, csv_adapter):
        """Test add single holiday"""
        csv_adapter.write_holidays([])  # Start with empty
        
        csv_adapter.add_holiday({'date': '2024-06-01', 'name': 'Test Holiday'})
        
        holidays = csv_adapter.read_holidays()
        
        assert len(holidays) == 1
        assert holidays[0]['name'] == 'Test Holiday'


class TestDomainModelConversions:
    """Test domain model conversions (SKU, Transaction)"""
    
    def test_sku_to_dict_conversion(self):
        """Test SKU to dict conversion"""
        sku = SKU(
            sku='CONV001',
            description='Conversion Test',
            moq=5,
            lead_time_days=10,
            demand_variability=DemandVariability.HIGH,
        )
        
        sku_dict = StorageAdapter._sku_to_dict(sku)
        
        assert sku_dict['sku'] == 'CONV001'
        assert sku_dict['moq'] == 5
        assert sku_dict['lead_time_days'] == 10
        assert sku_dict['demand_variability'] == 'HIGH'
    
    def test_dict_to_sku_conversion(self):
        """Test dict to SKU conversion"""
        sku_dict = {
            'sku': 'CONV002',
            'description': 'Reverse Conversion',
            'moq': 8,
            'lead_time_days': 14,
            'demand_variability': 'LOW',
        }
        
        sku = StorageAdapter._dict_to_sku(sku_dict)
        
        assert sku.sku == 'CONV002'
        assert sku.moq == 8
        assert sku.lead_time_days == 14
        assert sku.demand_variability == DemandVariability.LOW
    
    def test_dict_to_transaction_conversion(self):
        """Test dict to Transaction conversion"""
        txn_dict = {
            'date': '2024-01-15',
            'sku': 'TXN_CONV',
            'event': 'SALE',
            'qty': -10,
            'receipt_date': None,
            'note': 'Test transaction',
        }
        
        txn = StorageAdapter._dict_to_transaction(txn_dict)
        
        assert txn.date == date(2024, 1, 15)
        assert txn.sku == 'TXN_CONV'
        assert txn.event == EventType.SALE
        assert txn.qty == -10
        assert txn.note == 'Test transaction'


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
