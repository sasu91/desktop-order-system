"""
Test suite for legacy migration.

Tests conversion from legacy snapshot CSV to ledger events.
"""
import pytest
import csv
from datetime import date
from pathlib import Path
import tempfile
import shutil

from src.domain.models import EventType
from src.domain.migration import LegacyMigration
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


@pytest.fixture
def legacy_csv_file(temp_data_dir):
    """Create a sample legacy inventory CSV file."""
    legacy_file = temp_data_dir / "legacy_inventory.csv"
    
    with open(legacy_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["sku", "description", "quantity", "ean"])
        writer.writeheader()
        writer.writerow({"sku": "SKU001", "description": "Product A", "quantity": "100", "ean": "5901234123457"})
        writer.writerow({"sku": "SKU002", "description": "Product B", "quantity": "50", "ean": ""})
        writer.writerow({"sku": "SKU003", "description": "Product C", "quantity": "200", "ean": ""})
    
    return legacy_file


class TestLegacyMigration:
    """Test legacy inventory migration."""
    
    def test_migrate_from_legacy_csv(self, csv_layer, legacy_csv_file):
        """Migrate legacy inventory to ledger."""
        migration_date = date(2026, 1, 1)
        
        result = LegacyMigration.migrate_from_legacy_csv(
            legacy_csv_path=legacy_csv_file,
            csv_layer=csv_layer,
            snapshot_date=migration_date,
        )
        
        assert result["success"] is True
        assert result["migrated_skus"] == 3
        assert len(result["errors"]) == 0
        
        # Verify transactions were created
        txns = csv_layer.read_transactions()
        assert len(txns) == 3
        assert all(t.event == EventType.SNAPSHOT for t in txns)
        
        # Verify SKUs were added
        skus = csv_layer.read_skus()
        assert len(skus) == 3
        sku_ids = [s.sku for s in skus]
        assert set(sku_ids) == {"SKU001", "SKU002", "SKU003"}
    
    def test_migrate_skip_if_ledger_already_populated(self, csv_layer, legacy_csv_file):
        """Skip migration if ledger already has events."""
        from src.domain.models import Transaction
        
        # Pre-populate ledger
        csv_layer.write_transaction(
            Transaction(date=date(2026, 1, 1), sku="EXISTING", event=EventType.SNAPSHOT, qty=10)
        )
        
        result = LegacyMigration.migrate_from_legacy_csv(
            legacy_csv_path=legacy_csv_file,
            csv_layer=csv_layer,
            snapshot_date=date(2026, 1, 1),
        )
        
        assert result["success"] is False
        assert "already populated" in result["message"].lower()
    
    def test_migrate_force_override_existing(self, csv_layer, legacy_csv_file):
        """Force migration even if ledger has events."""
        from src.domain.models import Transaction
        
        # Pre-populate ledger
        csv_layer.write_transaction(
            Transaction(date=date(2026, 1, 1), sku="EXISTING", event=EventType.SNAPSHOT, qty=10)
        )
        
        result = LegacyMigration.migrate_from_legacy_csv(
            legacy_csv_path=legacy_csv_file,
            csv_layer=csv_layer,
            snapshot_date=date(2026, 1, 1),
            force=True,
        )
        
        assert result["success"] is True
        assert result["migrated_skus"] == 3
    
    def test_migrate_missing_legacy_file(self, csv_layer, temp_data_dir):
        """Handle missing legacy file gracefully."""
        result = LegacyMigration.migrate_from_legacy_csv(
            legacy_csv_path=temp_data_dir / "nonexistent.csv",
            csv_layer=csv_layer,
            snapshot_date=date(2026, 1, 1),
        )
        
        assert result["success"] is False
        assert "not found" in result["message"].lower()
