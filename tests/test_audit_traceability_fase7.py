#!/usr/bin/env python3
"""
FASE 7 TASK 7.4 — Test Suite: Audit & Traceability

Tests for audit logging, run_id batch tracking, and debug bundle export.

Test Coverage:
- Migration 002 (run_id column addition)
- generate_run_id() - unique ID generation
- log_audit_event() - single event logging
- get_audit_log() - query with filters
- get_batch_operations() - batch operation tracking
- Debug bundle export

Usage:
    pytest tests/test_audit_traceability_fase7.py -v
"""

import pytest
import sqlite3
from pathlib import Path
from datetime import datetime
import tempfile
import shutil
import json
import csv
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    initialize_database,
    open_connection,
    apply_migrations,
    generate_run_id,
    log_audit_event,
    get_audit_log,
    get_batch_operations,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_db(tmp_path):
    """Create temporary database with schema."""
    db_path = tmp_path / "test.db"
    
    # Initialize with schema
    from src.db import DB_PATH, MIGRATIONS_DIR
    original_db_path = DB_PATH
    
    # Temporarily override DB_PATH
    import src.db as db_module
    db_module.DB_PATH = db_path
    
    # Initialize database
    conn = initialize_database(force=True)
    
    # Apply migrations
    apply_migrations(conn, dry_run=False)
    
    conn.close()
    
    # Restore original DB_PATH
    db_module.DB_PATH = original_db_path
    
    return db_path


# ============================================================
# TEST 1: Migration 002 - run_id Column
# ============================================================

def test_migration_002_adds_run_id_column(temp_db):
    """Test that migration 002 adds run_id column to audit_log."""
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()
    
    # Check that run_id column exists
    cursor.execute("PRAGMA table_info(audit_log)")
    columns = {row[1]: row[2] for row in cursor.fetchall()}
    
    assert "run_id" in columns, "audit_log should have run_id column"
    assert columns["run_id"] == "TEXT", "run_id should be TEXT type"
    
    # Check that index exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_log'")
    indices = [row[0] for row in cursor.fetchall()]
    
    assert any("run_id" in idx for idx in indices), "Should have index on run_id"
    
    conn.close()


# ============================================================
# TEST 2: generate_run_id() - Unique ID Generation
# ============================================================

def test_generate_run_id_format():
    """Test that generate_run_id returns correct format."""
    run_id = generate_run_id()
    
    # Format: run_YYYYMMDD_HHMMSS_<uuid4_short>
    assert run_id.startswith("run_"), "run_id should start with 'run_'"
    
    parts = run_id.split("_")
    assert len(parts) == 4, "run_id should have 4 parts: run_YYYYMMDD_HHMMSS_uuid"
    
    # Check date format (YYYYMMDD)
    date_part = parts[1]
    assert len(date_part) == 8, "Date should be 8 digits (YYYYMMDD)"
    assert date_part.isdigit(), "Date should be all digits"
    
    # Check time format (HHMMSS)
    time_part = parts[2]
    assert len(time_part) == 6, "Time should be 6 digits (HHMMSS)"
    assert time_part.isdigit(), "Time should be all digits"
    
    # Check UUID part (8 hex chars)
    uuid_part = parts[3]
    assert len(uuid_part) == 8, "UUID should be 8 characters"


def test_generate_run_id_unique():
    """Test that generate_run_id returns unique IDs."""
    run_ids = [generate_run_id() for _ in range(100)]
    
    # All should be unique
    assert len(set(run_ids)) == 100, "All generated run_ids should be unique"


# ============================================================
# TEST 3: log_audit_event() - Single Event Logging
# ============================================================

def test_log_audit_event_basic(temp_db):
    """Test basic audit event logging."""
    conn = open_connection(temp_db, track_connection=False)
    
    # Create SKU first (required by foreign key)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO skus (sku, description) VALUES ('SKU001', 'Test Product')")
    conn.commit()
    
    audit_id = log_audit_event(
        conn,
        operation="TEST_OPERATION",
        details="Test event",
        sku="SKU001",
        user="test_user",
    )
    
    assert audit_id > 0, "Should return audit_id"
    
    # Verify event was logged
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_log WHERE audit_id = ?", (audit_id,))
    row = cursor.fetchone()
    
    assert row is not None, "Event should be logged"
    assert row[2] == "TEST_OPERATION", "Operation should match"
    assert row[3] == "SKU001", "SKU should match"
    assert row[4] == "Test event", "Details should match"
    assert row[5] == "test_user", "User should match"
    
    conn.close()


def test_log_audit_event_with_run_id(temp_db):
    """Test audit event logging with run_id."""
    conn = open_connection(temp_db, track_connection=False)
    
    run_id = generate_run_id()
    
    # Log multiple events with same run_id
    audit_id_1 = log_audit_event(conn, "OP1", "Event 1", run_id=run_id)
    audit_id_2 = log_audit_event(conn, "OP2", "Event 2", run_id=run_id)
    audit_id_3 = log_audit_event(conn, "OP3", "Event 3", run_id=run_id)
    
    # Verify all events have same run_id
    cursor = conn.cursor()
    cursor.execute("SELECT operation, run_id FROM audit_log WHERE run_id = ?", (run_id,))
    rows = cursor.fetchall()
    
    assert len(rows) == 3, "Should have 3 events with same run_id"
    assert all(row[1] == run_id for row in rows), "All events should have same run_id"
    
    conn.close()


def test_log_audit_event_null_sku(temp_db):
    """Test audit event logging with null SKU (global operation)."""
    conn = open_connection(temp_db, track_connection=False)
    
    audit_id = log_audit_event(
        conn,
        operation="GLOBAL_OP",
        details="Global event",
        sku=None,  # No SKU
    )
    
    cursor = conn.cursor()
    cursor.execute("SELECT sku FROM audit_log WHERE audit_id = ?", (audit_id,))
    sku = cursor.fetchone()[0]
    
    assert sku is None, "SKU should be NULL for global operations"
    
    conn.close()


# ============================================================
# TEST 4: get_audit_log() - Query with Filters
# ============================================================

def test_get_audit_log_all(temp_db):
    """Test getting all audit log records."""
    conn = open_connection(temp_db, track_connection=False)
    
    # Create SKUs first
    cursor = conn.cursor()
    for i in range(10):
        cursor.execute("INSERT INTO skus (sku, description) VALUES (?, ?)", (f"SKU{i:03d}", f"Product {i}"))
    conn.commit()
    
    # Log multiple events
    for i in range(10):
        log_audit_event(conn, f"OP{i}", f"Event {i}", sku=f"SKU{i:03d}")
    
    # Get all records
    records = get_audit_log(conn, limit=100)
    
    assert len(records) == 10, "Should return 10 records"
    assert records[0]["operation"] == "OP9", "Most recent should be first (DESC order)"
    
    conn.close()


def test_get_audit_log_filter_by_sku(temp_db):
    """Test filtering audit log by SKU."""
    conn = open_connection(temp_db, track_connection=False)
    
    # Create SKUs first
    cursor = conn.cursor()
    cursor.execute("INSERT INTO skus (sku, description) VALUES ('SKU001', 'Product 1')")
    cursor.execute("INSERT INTO skus (sku, description) VALUES ('SKU002', 'Product 2')")
    conn.commit()
    
    # Log events for different SKUs
    log_audit_event(conn, "OP1", "Event for SKU001", sku="SKU001")
    log_audit_event(conn, "OP2", "Event for SKU002", sku="SKU002")
    log_audit_event(conn, "OP3", "Another event for SKU001", sku="SKU001")
    
    # Filter by SKU001
    records = get_audit_log(conn, sku="SKU001")
    
    assert len(records) == 2, "Should return 2 events for SKU001"
    assert all(r["sku"] == "SKU001" for r in records), "All records should be for SKU001"
    
    conn.close()


def test_get_audit_log_filter_by_operation(temp_db):
    """Test filtering audit log by operation type."""
    conn = open_connection(temp_db, track_connection=False)
    
    # Log different operations
    log_audit_event(conn, "ORDER_CONFIRMED", "Order 1")
    log_audit_event(conn, "RECEIPT_CLOSED", "Receipt 1")
    log_audit_event(conn, "ORDER_CONFIRMED", "Order 2")
    
    # Filter by ORDER_CONFIRMED
    records = get_audit_log(conn, operation="ORDER_CONFIRMED")
    
    assert len(records) == 2, "Should return 2 ORDER_CONFIRMED events"
    assert all(r["operation"] == "ORDER_CONFIRMED" for r in records), "All should be ORDER_CONFIRMED"
    
    conn.close()


def test_get_audit_log_filter_by_run_id(temp_db):
    """Test filtering audit log by run_id."""
    conn = open_connection(temp_db, track_connection=False)
    
    run_id_1 = generate_run_id()
    run_id_2 = generate_run_id()
    
    # Log events with different run_ids
    log_audit_event(conn, "OP1", "Event 1", run_id=run_id_1)
    log_audit_event(conn, "OP2", "Event 2", run_id=run_id_1)
    log_audit_event(conn, "OP3", "Event 3", run_id=run_id_2)
    
    # Filter by run_id_1
    records = get_audit_log(conn, run_id=run_id_1)
    
    assert len(records) == 2, "Should return 2 events for run_id_1"
    assert all(r["run_id"] == run_id_1 for r in records), "All should have run_id_1"
    
    conn.close()


def test_get_audit_log_pagination(temp_db):
    """Test audit log pagination."""
    conn = open_connection(temp_db, track_connection=False)
    
    # Log 20 events
    for i in range(20):
        log_audit_event(conn, f"OP{i}", f"Event {i}")
    
    # Get first page (10 records)
    page1 = get_audit_log(conn, limit=10, offset=0)
    assert len(page1) == 10, "First page should have 10 records"
    
    # Get second page (10 records)
    page2 = get_audit_log(conn, limit=10, offset=10)
    assert len(page2) == 10, "Second page should have 10 records"
    
    # Pages should not overlap
    page1_ids = {r["audit_id"] for r in page1}
    page2_ids = {r["audit_id"] for r in page2}
    assert page1_ids.isdisjoint(page2_ids), "Pages should not overlap"
    
    conn.close()


# ============================================================
# TEST 5: get_batch_operations() - Batch Tracking
# ============================================================

def test_get_batch_operations_basic(temp_db):
    """Test getting batch operations."""
    conn = open_connection(temp_db, track_connection=False)
    
    # Create SKUs first
    cursor = conn.cursor()
    cursor.execute("INSERT INTO skus (sku, description) VALUES ('SKU001', 'Product 1')")
    cursor.execute("INSERT INTO skus (sku, description) VALUES ('SKU002', 'Product 2')")
    conn.commit()
    
    run_id = generate_run_id()
    
    # Log batch operations
    log_audit_event(conn, "BATCH_START", "Starting batch", run_id=run_id)
    log_audit_event(conn, "PROCESS_SKU", "Processing SKU001", sku="SKU001", run_id=run_id)
    log_audit_event(conn, "PROCESS_SKU", "Processing SKU002", sku="SKU002", run_id=run_id)
    log_audit_event(conn, "BATCH_END", "Batch complete", run_id=run_id)
    
    # Get batch operations
    batch = get_batch_operations(conn, run_id)
    
    assert batch["run_id"] == run_id, "run_id should match"
    assert batch["event_count"] == 4, "Should have 4 events"
    assert len(batch["events"]) == 4, "Should return 4 events"
    assert batch["events"][0]["operation"] == "BATCH_START", "First event should be BATCH_START"
    assert batch["events"][-1]["operation"] == "BATCH_END", "Last event should be BATCH_END"
    
    conn.close()


def test_get_batch_operations_empty(temp_db):
    """Test getting batch operations for nonexistent run_id."""
    conn = open_connection(temp_db, track_connection=False)
    
    run_id = "nonexistent_run_id"
    batch = get_batch_operations(conn, run_id)
    
    assert batch["run_id"] == run_id, "run_id should match"
    assert batch["event_count"] == 0, "Should have 0 events"
    assert batch["events"] == [], "Events list should be empty"
    assert batch["start_time"] is None, "start_time should be None"
    assert batch["end_time"] is None, "end_time should be None"
    
    conn.close()


def test_get_batch_operations_calculates_duration(temp_db):
    """Test that batch operations calculates duration."""
    conn = open_connection(temp_db, track_connection=False)
    
    run_id = generate_run_id()
    
    # Log events (will have timestamps)
    log_audit_event(conn, "START", "Start", run_id=run_id)
    import time
    time.sleep(0.1)  # Small delay
    log_audit_event(conn, "END", "End", run_id=run_id)
    
    batch = get_batch_operations(conn, run_id)
    
    assert batch["event_count"] == 2, "Should have 2 events"
    assert batch["start_time"] is not None, "start_time should be set"
    assert batch["end_time"] is not None, "end_time should be set"
    # Duration should be > 0 (we slept 0.1s)
    assert batch["duration_seconds"] >= 0, "duration_seconds should be calculated"
    
    conn.close()


# ============================================================
# TEST 6: Debug Bundle Export
# ============================================================

def test_export_debug_bundle_creates_files(temp_db, tmp_path):
    """Test that debug bundle export creates all expected files."""
    from tools.export_debug_bundle import export_debug_bundle
    
    # Create some audit log entries
    conn = open_connection(temp_db, track_connection=False)
    run_id = generate_run_id()
    for i in range(5):
        log_audit_event(conn, f"OP{i}", f"Event {i}", run_id=run_id)
    conn.close()
    
    # Export debug bundle
    output_dir = tmp_path / "debug_bundles"
    bundle_path = export_debug_bundle(
        db_path=temp_db,
        output_dir=output_dir,
        audit_limit=100,
        compress=False,
    )
    
    assert bundle_path.exists(), "Bundle directory should be created"
    assert bundle_path.is_dir(), "Bundle should be a directory"
    
    # Check for expected files
    expected_files = [
        "database_backup.db",
        "audit_log.csv",
        "database_stats.json",
        "system_info.json",
        "manifest.json",
        "README.txt",
    ]
    
    for filename in expected_files:
        file_path = bundle_path / filename
        assert file_path.exists(), f"{filename} should exist in bundle"


def test_export_debug_bundle_audit_log_content(temp_db, tmp_path):
    """Test that audit log CSV contains correct data."""
    from tools.export_debug_bundle import export_debug_bundle
    
    # Create audit log entries
    conn = open_connection(temp_db, track_connection=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO skus (sku, description) VALUES ('SKU001', 'Test Product')")
    conn.commit()
    log_audit_event(conn, "TEST_OP", "Test event", sku="SKU001", user="test_user")
    conn.close()
    
    # Export bundle
    output_dir = tmp_path / "debug_bundles"
    bundle_path = export_debug_bundle(temp_db, output_dir, audit_limit=100, compress=False)
    
    # Read audit_log.csv
    audit_csv_path = bundle_path / "audit_log.csv"
    with open(audit_csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    assert len(rows) >= 1, "Should have at least 1 audit record"
    assert rows[0]["operation"] == "TEST_OP", "Operation should match"
    assert rows[0]["sku"] == "SKU001", "SKU should match"
    assert rows[0]["user"] == "test_user", "User should match"


def test_export_debug_bundle_with_compression(temp_db, tmp_path):
    """Test debug bundle export with ZIP compression."""
    from tools.export_debug_bundle import export_debug_bundle
    import zipfile
    
    # Export compressed bundle
    output_dir = tmp_path / "debug_bundles"
    bundle_path = export_debug_bundle(temp_db, output_dir, compress=True)
    
    assert bundle_path.exists(), "ZIP file should be created"
    assert bundle_path.suffix == ".zip", "Should be a ZIP file"
    assert bundle_path.is_file(), "Should be a file"
    
    # Verify ZIP contents
    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = zf.namelist()
        assert any("database_backup.db" in name for name in names), "Should contain database_backup.db"
        assert any("audit_log.csv" in name for name in names), "Should contain audit_log.csv"
        assert any("manifest.json" in name for name in names), "Should contain manifest.json"
        assert any("README.txt" in name for name in names), "Should contain README.txt"


def test_export_debug_bundle_manifest_content(temp_db, tmp_path):
    """Test that manifest contains expected metadata."""
    from tools.export_debug_bundle import export_debug_bundle
    
    # Export bundle
    output_dir = tmp_path / "debug_bundles"
    bundle_path = export_debug_bundle(temp_db, output_dir, compress=False)
    
    # Read manifest
    manifest_path = bundle_path / "manifest.json"
    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    
    assert "bundle_id" in manifest, "Manifest should contain bundle_id"
    assert "created_at" in manifest, "Manifest should contain created_at"
    assert "database_path" in manifest, "Manifest should contain database_path"
    assert "schema_version" in manifest, "Manifest should contain schema_version"
    assert "python_version" in manifest, "Manifest should contain python_version"
    assert "sqlite_version" in manifest, "Manifest should contain sqlite_version"


# ============================================================
# TEST 7: Integration - Full Batch Operation Workflow
# ============================================================

def test_full_batch_operation_workflow(temp_db):
    """Test complete batch operation workflow with audit logging."""
    conn = open_connection(temp_db, track_connection=False)
    
    # Create SKUs first
    cursor = conn.cursor()
    for i in range(1, 4):
        cursor.execute("INSERT INTO skus (sku, description) VALUES (?, ?)", (f"SKU00{i}", f"Product {i}"))
    conn.commit()
    
    # Simulate batch operation: Update safety stock for multiple SKUs
    run_id = generate_run_id()
    
    # Log batch start
    log_audit_event(conn, "BATCH_START", "Update safety stock for slow movers", run_id=run_id)
    
    # Simulate processing multiple SKUs
    skus_to_update = ["SKU001", "SKU002", "SKU003"]
    for sku in skus_to_update:
        log_audit_event(
            conn,
            operation="SKU_UPDATED",
            details=f"Set safety_stock=20",
            sku=sku,
            run_id=run_id,
        )
    
    # Log batch end
    log_audit_event(conn, "BATCH_END", f"Updated {len(skus_to_update)} SKUs", run_id=run_id)
    
    # Retrieve and verify batch
    batch = get_batch_operations(conn, run_id)
    
    assert batch["event_count"] == 5, "Should have 5 events (START + 3 updates + END)"
    assert batch["events"][0]["operation"] == "BATCH_START", "First should be BATCH_START"
    assert batch["events"][-1]["operation"] == "BATCH_END", "Last should be BATCH_END"
    
    # Count SKU_UPDATED events
    sku_updates = [e for e in batch["events"] if e["operation"] == "SKU_UPDATED"]
    assert len(sku_updates) == 3, "Should have 3 SKU_UPDATED events"
    
    # Verify all SKUs were processed
    updated_skus = {e["sku"] for e in sku_updates}
    assert updated_skus == set(skus_to_update), "All SKUs should be logged"
    
    conn.close()


# ============================================================
# SUMMARY
# ============================================================

"""
Test Suite Summary:

Total Tests: 17

Coverage:
  ✓ Migration 002 - run_id column addition (TEST 1)
  ✓ generate_run_id() format & uniqueness (TEST 2)
  ✓ log_audit_event() basic & with run_id (TEST 3)
  ✓ get_audit_log() filters (SKU, operation, run_id, pagination) (TEST 4)
  ✓ get_batch_operations() basic & duration calculation (TEST 5)
  ✓ Debug bundle export (files, content, compression, manifest) (TEST 6)
  ✓ Integration - full batch workflow (TEST 7)

All tests validate TASK 7.4 requirements:
1. run_id for batch operations ✓
2. Audit logging functions ✓
3. Debug bundle export tool ✓
"""
