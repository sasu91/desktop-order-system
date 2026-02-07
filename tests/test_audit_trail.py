"""
Test Audit Trail functionality.

Verify audit logging for SKU operations and exports.
"""
import pytest
from datetime import date, datetime
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, EventType, AuditLog
from src.persistence.csv_layer import CSVLayer


class TestAuditTrail:
    """Test audit trail logging and retrieval."""
    
    @pytest.fixture
    def temp_csv_layer(self):
        """Create a temporary CSV layer for testing."""
        temp_dir = Path(tempfile.mkdtemp())
        csv_layer = CSVLayer(data_dir=temp_dir)
        yield csv_layer
        shutil.rmtree(temp_dir)
    
    def test_log_audit_creates_entry(self, temp_csv_layer):
        """Test that logging an audit entry creates a record."""
        # Log an audit entry
        temp_csv_layer.log_audit(
            operation="SKU_CREATE",
            details="Created SKU: Test Product",
            sku="TEST001",
            user="admin",
        )
        
        # Retrieve audit log
        logs = temp_csv_layer.read_audit_log()
        
        assert len(logs) == 1
        assert logs[0].operation == "SKU_CREATE"
        assert logs[0].sku == "TEST001"
        assert logs[0].details == "Created SKU: Test Product"
        assert logs[0].user == "admin"
        assert logs[0].timestamp  # Should have a timestamp
    
    def test_log_audit_filter_by_sku(self, temp_csv_layer):
        """Test filtering audit log by SKU."""
        # Log multiple entries
        temp_csv_layer.log_audit("SKU_CREATE", "Created SKU A", sku="SKU_A")
        temp_csv_layer.log_audit("SKU_EDIT", "Updated SKU A", sku="SKU_A")
        temp_csv_layer.log_audit("SKU_CREATE", "Created SKU B", sku="SKU_B")
        temp_csv_layer.log_audit("EXPORT", "Exported data", sku=None)
        
        # Filter by SKU_A
        logs_a = temp_csv_layer.read_audit_log(sku="SKU_A")
        assert len(logs_a) == 2
        assert all(log.sku == "SKU_A" for log in logs_a)
        
        # Filter by SKU_B
        logs_b = temp_csv_layer.read_audit_log(sku="SKU_B")
        assert len(logs_b) == 1
        assert logs_b[0].sku == "SKU_B"
        
        # No filter (all logs)
        all_logs = temp_csv_layer.read_audit_log()
        assert len(all_logs) == 4
    
    def test_log_audit_sorted_by_timestamp_desc(self, temp_csv_layer):
        """Test that audit logs are sorted by timestamp descending (most recent first)."""
        import time
        
        # Log entries with slight delay to ensure different timestamps
        temp_csv_layer.log_audit("OP1", "First operation")
        time.sleep(0.01)  # Small delay (timestamp has microsecond precision)
        temp_csv_layer.log_audit("OP2", "Second operation")
        time.sleep(0.01)
        temp_csv_layer.log_audit("OP3", "Third operation")
        
        # Retrieve logs
        logs = temp_csv_layer.read_audit_log()
        
        # Should be in reverse order (most recent first)
        assert logs[0].operation == "OP3"
        assert logs[1].operation == "OP2"
        assert logs[2].operation == "OP1"
    
    def test_log_audit_limit(self, temp_csv_layer):
        """Test limiting number of audit log entries returned."""
        import time
        
        # Log 10 entries with small delay to ensure different timestamps
        for i in range(10):
            temp_csv_layer.log_audit(f"OP{i}", f"Operation {i}")
            if i < 9:  # No need to sleep after last entry
                time.sleep(0.01)  # Timestamp has microsecond precision
        
        # Get only last 5
        logs = temp_csv_layer.read_audit_log(limit=5)
        assert len(logs) == 5
        
        # Should be most recent 5
        assert logs[0].operation == "OP9"
        assert logs[4].operation == "OP5"
    
    def test_audit_log_empty_sku(self, temp_csv_layer):
        """Test audit log with None SKU (for exports, etc.)."""
        temp_csv_layer.log_audit(
            operation="EXPORT",
            details="Exported stock snapshot",
            sku=None,
        )
        
        logs = temp_csv_layer.read_audit_log()
        assert len(logs) == 1
        assert logs[0].sku is None
        assert logs[0].operation == "EXPORT"
    
    def test_audit_log_timestamp_format(self, temp_csv_layer):
        """Test that timestamp is in correct ISO format."""
        temp_csv_layer.log_audit("TEST", "Test operation")
        
        logs = temp_csv_layer.read_audit_log()
        timestamp = logs[0].timestamp
        
        # Should be parseable as datetime
        dt = datetime.fromisoformat(timestamp)
        assert isinstance(dt, datetime)
        
        # Should be recent (within last minute)
        now = datetime.now()
        diff = (now - dt).total_seconds()
        assert 0 <= diff < 60  # Should be within last minute


class TestEventTypeExtensions:
    """Test new event types for audit trail."""
    
    def test_sku_edit_event_exists(self):
        """Test that SKU_EDIT event type exists."""
        assert hasattr(EventType, "SKU_EDIT")
        assert EventType.SKU_EDIT.value == "SKU_EDIT"
    
    def test_export_log_event_exists(self):
        """Test that EXPORT_LOG event type exists."""
        assert hasattr(EventType, "EXPORT_LOG")
        assert EventType.EXPORT_LOG.value == "EXPORT_LOG"


class TestAuditLogModel:
    """Test AuditLog dataclass."""
    
    def test_audit_log_creation(self):
        """Test creating AuditLog instance."""
        log = AuditLog(
            timestamp="2026-01-28 10:30:00",
            operation="SKU_EDIT",
            sku="TEST001",
            details="Updated description",
            user="admin",
        )
        
        assert log.timestamp == "2026-01-28 10:30:00"
        assert log.operation == "SKU_EDIT"
        assert log.sku == "TEST001"
        assert log.details == "Updated description"
        assert log.user == "admin"
    
    def test_audit_log_immutable(self):
        """Test that AuditLog is immutable (frozen)."""
        log = AuditLog(
            timestamp="2026-01-28 10:30:00",
            operation="TEST",
            sku=None,
            details="Test",
        )
        
        with pytest.raises(Exception):  # FrozenInstanceError
            log.operation = "MODIFIED"
    
    def test_audit_log_default_user(self):
        """Test that default user is 'system'."""
        log = AuditLog(
            timestamp="2026-01-28 10:30:00",
            operation="TEST",
            sku=None,
            details="Test",
        )
        
        assert log.user == "system"
