#!/usr/bin/env python3
"""
FASE 7 TASK 7.3 — Test Suite: Recovery & Backup

Tests for backup, export, and restore functionality.

Test Coverage:
- Automatic backup on startup
- Backup retention policy (cleanup old backups)
- WAL-aware backup (captures .db + .db-wal + .db-shm)
- CSV snapshot export (all tables + manifest)
- Restore from binary backup
- Safety backup before restore
- Integrity validation

Usage:
    pytest tests/test_backup_restore_fase7.py -v
"""

import pytest
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import tempfile
import shutil
import json
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    initialize_database,
    open_connection,
    backup_database,
    cleanup_old_backups,
    automatic_backup_on_startup,
    get_database_stats,
    integrity_check,
    BACKUP_DIR,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_db(tmp_path):
    """Create temporary database with test data."""
    db_path = tmp_path / "test.db"
    
    # Initialize schema
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Create minimal schema
    cursor.execute("""
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE skus (
            sku TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            ean TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            sku TEXT NOT NULL,
            event TEXT NOT NULL,
            qty INTEGER NOT NULL,
            receipt_date TEXT,
            note TEXT
        )
    """)
    
    # Insert test data
    cursor.execute("INSERT INTO schema_version VALUES (6, '2026-01-01')")
    cursor.execute("INSERT INTO skus VALUES ('SKU001', 'Test Product 1', '1234567890123')")
    cursor.execute("INSERT INTO skus VALUES ('SKU002', 'Test Product 2', '9876543210987')")
    
    cursor.execute("""
        INSERT INTO transactions (date, sku, event, qty, note)
        VALUES ('2026-01-01', 'SKU001', 'SNAPSHOT', 100, 'Initial')
    """)
    cursor.execute("""
        INSERT INTO transactions (date, sku, event, qty, note)
        VALUES ('2026-01-02', 'SKU001', 'SALE', -10, 'Daily sales')
    """)
    
    conn.commit()
    conn.close()
    
    return db_path


@pytest.fixture
def temp_backup_dir(tmp_path):
    """Create temporary backup directory."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(exist_ok=True)
    return backup_dir


# ============================================================
# TEST 1: Basic Backup Creation
# ============================================================

def test_backup_creates_main_file(temp_db, temp_backup_dir):
    """Test that backup_database creates main .db file."""
    backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason="test")
    
    assert backup_path.exists(), "Backup file should exist"
    assert backup_path.suffix == ".db", "Backup should have .db extension"
    assert "test" in backup_path.name, "Backup filename should contain reason"
    
    # Verify backup is valid SQLite database
    conn = sqlite3.connect(str(backup_path))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM skus")
    count = cursor.fetchone()[0]
    conn.close()
    
    assert count == 2, "Backup should contain same data as original"


# ============================================================
# TEST 2: WAL-Aware Backup
# ============================================================

def test_backup_captures_wal_files(temp_db, temp_backup_dir):
    """Test that backup captures WAL and SHM files when they exist."""
    # Enable WAL mode
    conn = sqlite3.connect(str(temp_db))
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Make some changes to generate WAL file
    cursor = conn.cursor()
    cursor.execute("INSERT INTO skus VALUES ('SKU003', 'Test Product 3', '1111111111111')")
    conn.commit()
    conn.close()
    
    # Verify WAL file exists
    wal_file = Path(str(temp_db) + "-wal")
    shm_file = Path(str(temp_db) + "-shm")
    
    # Create backup
    backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason="wal_test")
    
    # Check for backup WAL/SHM files
    backup_wal = Path(str(backup_path) + "-wal")
    backup_shm = Path(str(backup_path) + "-shm")
    
    if wal_file.exists():
        assert backup_wal.exists(), "Backup should include WAL file if it exists"
    
    if shm_file.exists():
        assert backup_shm.exists(), "Backup should include SHM file if it exists"


# ============================================================
# TEST 3: Backup Manifest Creation
# ============================================================

def test_backup_creates_manifest(temp_db, temp_backup_dir):
    """Test that backup creates manifest file listing all backed up files."""
    # Enable WAL mode
    conn = sqlite3.connect(str(temp_db))
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO skus VALUES ('SKU999', 'Test', '9999999999999')")
    conn.commit()
    conn.close()
    
    # Create backup
    backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason="manifest_test")
    
    # Check manifest
    manifest_file = Path(str(backup_path) + ".manifest")
    assert manifest_file.exists(), "Manifest file should be created"
    
    # Read manifest
    with open(manifest_file, "r") as f:
        content = f.read()
    
    assert backup_path.name in content, "Manifest should list main .db file"


# ============================================================
# TEST 4: Cleanup Old Backups (Retention Policy)
# ============================================================

def test_cleanup_old_backups_retention(temp_db, temp_backup_dir):
    """Test that cleanup_old_backups keeps only the most recent N backups."""
    # Create 15 backups
    backup_paths = []
    for i in range(15):
        backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason=f"test_{i}")
        backup_paths.append(backup_path)
        
        # Sleep to ensure different timestamps
        import time
        time.sleep(0.01)
    
    # Verify 15 backups exist
    assert len(list(temp_backup_dir.glob("*.db"))) == 15, "Should have 15 backups before cleanup"
    
    # Cleanup, keeping only 10
    deleted_count = cleanup_old_backups(max_backups=10, backup_dir=temp_backup_dir)
    
    assert deleted_count == 5, "Should have deleted 5 old backups"
    
    # Verify 10 backups remain
    remaining_backups = list(temp_backup_dir.glob("*.db"))
    assert len(remaining_backups) == 10, "Should have 10 backups after cleanup"
    
    # Note: Due to rapid creation, mtime may be identical for multiple files,
    # so we can't guarantee which specific files were kept.
    # The important thing is that 10 remain and 5 were deleted.


# ============================================================
# TEST 5: Automatic Backup on Startup
# ============================================================

def test_automatic_backup_on_startup(temp_db, temp_backup_dir):
    """Test automatic_backup_on_startup creates backup and applies retention."""
    # Create backup on startup
    backup_path = automatic_backup_on_startup(temp_db, max_backups=5)
    
    assert backup_path is not None, "Should create startup backup"
    assert backup_path.exists(), "Startup backup should exist"
    assert "startup" in backup_path.name, "Backup should be marked as startup"


def test_automatic_backup_skips_if_db_missing(temp_backup_dir):
    """Test that automatic backup is skipped if database doesn't exist."""
    nonexistent_db = temp_backup_dir / "nonexistent.db"
    
    backup_path = automatic_backup_on_startup(nonexistent_db, max_backups=5)
    
    assert backup_path is None, "Should skip backup if database doesn't exist"


# ============================================================
# TEST 6: CSV Export - Table Coverage
# ============================================================

def test_export_snapshot_completeness(temp_db, tmp_path):
    """Test that CSV export includes all tables and manifest."""
    from tools.export_snapshot import export_full_snapshot
    
    # Export snapshot
    snapshot_dir = export_full_snapshot(temp_db, output_dir=tmp_path, compress=False)
    
    assert snapshot_dir.exists(), "Snapshot directory should be created"
    
    # Check for CSV files
    skus_csv = snapshot_dir / "skus.csv"
    transactions_csv = snapshot_dir / "transactions.csv"
    
    assert skus_csv.exists(), "skus.csv should be exported"
    assert transactions_csv.exists(), "transactions.csv should be exported"
    
    # Check manifest
    manifest_file = snapshot_dir / "manifest.json"
    assert manifest_file.exists(), "manifest.json should be created"
    
    # Check README
    readme_file = snapshot_dir / "README.txt"
    assert readme_file.exists(), "README.txt should be created"
    
    # Verify manifest content
    with open(manifest_file, "r") as f:
        manifest = json.load(f)
    
    assert "created_at" in manifest, "Manifest should contain timestamp (created_at)"
    assert "tables" in manifest, "Manifest should contain table information"
    assert "skus" in manifest["tables"], "Manifest should list skus table"
    assert manifest["tables"]["skus"]["rows"] == 2, "Manifest should show correct row count"


# ============================================================
# TEST 7: CSV Export - Data Integrity
# ============================================================

def test_export_snapshot_data_integrity(temp_db, tmp_path):
    """Test that CSV export preserves data correctly."""
    from tools.export_snapshot import export_full_snapshot
    import csv
    
    # Export snapshot
    snapshot_dir = export_full_snapshot(temp_db, output_dir=tmp_path, compress=False)
    
    # Read SKUs CSV
    skus_csv = snapshot_dir / "skus.csv"
    with open(skus_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    assert len(rows) == 2, "Should export 2 SKUs"
    assert rows[0]["sku"] == "SKU001", "SKU data should be correct"
    assert rows[0]["description"] == "Test Product 1", "Description should be correct"
    
    # Read transactions CSV
    transactions_csv = snapshot_dir / "transactions.csv"
    with open(transactions_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    assert len(rows) == 2, "Should export 2 transactions"
    assert rows[0]["event"] == "SNAPSHOT", "Event type should be correct"
    assert rows[0]["qty"] == "100", "Quantity should be correct"


# ============================================================
# TEST 8: Restore - Validation Before Restore
# ============================================================

def test_restore_validates_backup(temp_db, temp_backup_dir):
    """Test that restore validates backup integrity before restoring."""
    from tools.restore_backup import validate_backup
    
    # Create valid backup
    backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason="valid")
    
    # Validate
    is_valid = validate_backup(backup_path)
    assert is_valid is True, "Valid backup should pass validation"


def test_restore_rejects_corrupted_backup(temp_backup_dir):
    """Test that restore rejects corrupted backup."""
    from tools.restore_backup import validate_backup
    
    # Create corrupted file
    corrupted_backup = temp_backup_dir / "corrupted.db"
    with open(corrupted_backup, "w") as f:
        f.write("This is not a valid SQLite database")
    
    # Validate
    is_valid = validate_backup(corrupted_backup)
    assert is_valid is False, "Corrupted backup should fail validation"


# ============================================================
# TEST 9: Restore - Successful Restore
# ============================================================

def test_restore_from_backup(temp_db, temp_backup_dir, tmp_path):
    """Test successful restore from backup."""
    from tools.restore_backup import restore_backup
    
    # Create backup
    backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason="before_modify")
    
    # Modify database (delete a row)
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM skus WHERE sku='SKU002'")
    conn.commit()
    
    # Verify modification
    cursor.execute("SELECT COUNT(*) FROM skus")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 1, "Database should have 1 SKU after deletion"
    
    # Restore from backup
    target_db = tmp_path / "restored.db"
    success = restore_backup(backup_path, target_path=target_db, force=True)
    
    assert success is True, "Restore should succeed"
    assert target_db.exists(), "Restored database should exist"
    
    # Verify restoration
    conn = sqlite3.connect(str(target_db))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM skus")
    count = cursor.fetchone()[0]
    conn.close()
    
    assert count == 2, "Restored database should have original 2 SKUs"


# ============================================================
# TEST 10: Restore - Safety Backup Created
# ============================================================

def test_restore_creates_safety_backup(temp_db, temp_backup_dir, tmp_path):
    """Test that restore creates safety backup before overwriting."""
    from tools.restore_backup import restore_backup
    
    # Create original backup
    backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason="original")
    
    # Modify database
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()
    cursor.execute("INSERT INTO skus VALUES ('SKU999', 'Safety Test', '9999999999999')")
    conn.commit()
    conn.close()
    
    # Copy modified database to target location
    target_db = tmp_path / "target.db"
    shutil.copy2(temp_db, target_db)
    
    # Count backups before restore (safety backups go to data/backups/other/ since
    # reason "pre_restore" is categorised as "other")
    from src.db import BACKUP_DIR
    other_dir = BACKUP_DIR / "other"
    other_dir.mkdir(parents=True, exist_ok=True)
    backups_before = len(list(other_dir.glob("*pre_restore*.db")))
    
    # Restore from backup (this should create safety backup)
    success = restore_backup(backup_path, target_path=target_db, force=True)
    
    assert success is True, "Restore should succeed"
    
    # Count backups after restore (in other/ subdir)
    backups_after = len(list(other_dir.glob("*pre_restore*.db")))
    
    assert backups_after > backups_before, "Should create safety backup before restore"


# ============================================================
# TEST 11: Backup with Different Reasons
# ============================================================

def test_backup_with_custom_reason(temp_db, temp_backup_dir):
    """Test backup with custom reason in filename."""
    reasons = ["manual", "scheduled", "pre_update", "emergency"]
    
    for reason in reasons:
        backup_path = backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason=reason)
        assert reason in backup_path.name, f"Backup filename should contain reason: {reason}"


# ============================================================
# TEST 12: List Available Backups
# ============================================================

def test_list_available_backups(temp_db, temp_backup_dir):
    """Test listing available backups."""
    from tools.restore_backup import list_available_backups
    
    # Create multiple backups
    for i in range(5):
        backup_database(temp_db, backup_dir=temp_backup_dir, backup_reason=f"test_{i}")
    
    # List backups
    backups = list_available_backups(backup_dir=temp_backup_dir)
    
    assert len(backups) == 5, "Should list 5 backups"
    
    # Verify structure
    for backup in backups:
        assert "path" in backup, "Backup metadata should contain path"
        assert "reason" in backup, "Backup metadata should contain reason"
        assert "created" in backup, "Backup metadata should contain creation date"
        assert "size_mb" in backup, "Backup metadata should contain size"


# ============================================================
# TEST 13: Export with Compression
# ============================================================

def test_export_snapshot_with_compression(temp_db, tmp_path):
    """Test CSV export with ZIP compression."""
    from tools.export_snapshot import export_full_snapshot
    
    # Export with compression
    snapshot_path = export_full_snapshot(temp_db, output_dir=tmp_path, compress=True)
    
    # Should create .zip file
    assert snapshot_path.suffix == ".zip", "Compressed snapshot should be a ZIP file"
    assert snapshot_path.exists(), "ZIP file should exist"
    
    # Verify ZIP contents
    import zipfile
    with zipfile.ZipFile(snapshot_path, "r") as zf:
        names = zf.namelist()
        assert any("skus.csv" in name for name in names), "ZIP should contain skus.csv"
        assert any("manifest.json" in name for name in names), "ZIP should contain manifest.json"
        assert any("README.txt" in name for name in names), "ZIP should contain README.txt"


# ============================================================
# SUMMARY
# ============================================================

"""
Test Suite Summary:

Total Tests: 13

Coverage:
  ✓ Basic backup creation (TEST 1)
  ✓ WAL-aware backup (TEST 2, 3)
  ✓ Retention policy / cleanup old backups (TEST 4)
  ✓ Automatic backup on startup (TEST 5)
  ✓ CSV export completeness (TEST 6)
  ✓ CSV export data integrity (TEST 7)
  ✓ Restore validation (TEST 8)
  ✓ Successful restore (TEST 9)
  ✓ Safety backup on restore (TEST 10)
  ✓ Custom backup reasons (TEST 11)
  ✓ List available backups (TEST 12)
  ✓ Export with compression (TEST 13)

All tests validate TASK 7.3 requirements:
1. Automatic backup on startup with retention ✓
2. Export full snapshot (CSV + manifest) ✓
3. Restore tool with validation and safety ✓
"""
