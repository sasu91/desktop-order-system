"""
FASE 2 Test Script: Storage Layer Validation

Tests:
1. Connection management (open, close, PRAGMA verification)
2. Transaction context manager (commit, rollback)
3. CRUD operations on core tables (skus, transactions, order_logs)
4. Foreign key constraints (enforce referential integrity)
5. UNIQUE constraints (idempotency keys)
6. CHECK constraints (business rules)
7. Idempotent operations (duplicate detection)

Run: python test_db_fase2.py
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from db import (
    open_connection, 
    transaction, 
    verify_schema, 
    integrity_check,
    get_database_stats
)
import sqlite3
from datetime import date


def test_connection():
    """Test 1: Connection management and PRAGMA verification"""
    print("\n" + "="*60)
    print("TEST 1: Connection Management")
    print("="*60)
    
    conn = open_connection()
    cursor = conn.cursor()
    
    # Verify PRAGMAs
    fk_enabled = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_enabled == 1, "Foreign keys should be enabled"
    
    journal_mode = cursor.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode == "wal", f"Journal mode should be WAL, got {journal_mode}"
    
    print("✓ Connection opened with correct PRAGMA settings")
    print(f"  - Foreign keys: ON")
    print(f"  - Journal mode: {journal_mode}")
    
    conn.close()
    return True


def test_transactions():
    """Test 2: Transaction context manager (commit/rollback)"""
    print("\n" + "="*60)
    print("TEST 2: Transaction Context Manager")
    print("="*60)
    
    conn = open_connection()
    
    # Test COMMIT on success
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO skus (sku, description, moq, pack_size) 
            VALUES (?, ?, ?, ?)
        """, ("TEST_SKU_1", "Test Product 1", 10, 1))
    
    # Verify inserted
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM skus WHERE sku = ?", ("TEST_SKU_1",))
    row = cursor.fetchone()
    assert row is not None, "SKU should be inserted after commit"
    print(f"✓ INSERT committed successfully: {row['sku']}")
    
    # Test ROLLBACK on exception
    try:
        with transaction(conn) as cur:
            cur.execute("""
                INSERT INTO skus (sku, description, moq, pack_size) 
                VALUES (?, ?, ?, ?)
            """, ("TEST_SKU_2", "Test Product 2", 10, 1))
            
            # Force exception
            raise ValueError("Simulated error")
    
    except RuntimeError as e:
        assert "Transaction failed" in str(e)
        print(f"✓ Transaction rolled back on exception (as expected)")
    
    # Verify NOT inserted
    cursor.execute("SELECT * FROM skus WHERE SKU = ?", ("TEST_SKU_2",))
    row = cursor.fetchone()
    assert row is None, "SKU should NOT exist after rollback"
    print(f"✓ ROLLBACK verified: TEST_SKU_2 not inserted")
    
    conn.close()
    return True


def test_foreign_key_constraints():
    """Test 3: Foreign key constraint enforcement"""
    print("\n" + "="*60)
    print("TEST 3: Foreign Key Constraints")
    print("="*60)
    
    conn = open_connection()
    
    # Insert parent SKU
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO skus (sku, description, moq, pack_size) 
            VALUES (?, ?, ?, ?)
        """, ("TEST_SKU_FK", "Test FK Product", 5, 1))
    
    print(f"✓ Parent SKU inserted: TEST_SKU_FK")
    
    # Insert child transaction (should succeed)
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO transactions (date, sku, event, qty) 
            VALUES (?, ?, ?, ?)
        """, ("2026-02-17", "TEST_SKU_FK", "SNAPSHOT", 100))
    
    print(f"✓ Child transaction inserted (FK valid)")
    
    # Try to insert child with non-existent SKU (should fail)
    try:
        with transaction(conn) as cur:
            cur.execute("""
                INSERT INTO transactions (date, sku, event, qty) 
                VALUES (?, ?, ?, ?)
            """, ("2026-02-17", "NONEXISTENT_SKU", "SNAPSHOT", 50))
        
        assert False, "Should have raised FK constraint error"
    
    except RuntimeError as e:
        assert "FOREIGN KEY constraint failed" in str(e)
        print(f"✓ FK constraint enforced: rejected invalid SKU")
    
    conn.close()
    return True


def test_unique_constraints():
    """Test 4: UNIQUE constraint for idempotency"""
    print("\n" + "="*60)
    print("TEST 4: UNIQUE Constraints (Idempotency)")
    print("="*60)
    
    conn = open_connection()
    
    # Insert SKU
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO skus (sku, description, moq, pack_size) 
            VALUES (?, ?, ?, ?)
        """, ("TEST_SKU_UNIQ", "Test Unique", 1, 1))
    
    # Insert receiving log with document_id
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO receiving_logs (document_id, date, sku, qty_received, receipt_date)
            VALUES (?, ?, ?, ?, ?)
        """, ("DOC_001", "2026-02-17", "TEST_SKU_UNIQ", 50, "2026-02-20"))
    
    print(f"✓ Receiving log inserted: DOC_001")
    
    # Try to insert duplicate document_id (should fail)
    try:
        with transaction(conn) as cur:
            cur.execute("""
                INSERT INTO receiving_logs (document_id, date, sku, qty_received, receipt_date)
                VALUES (?, ?, ?, ?, ?)
            """, ("DOC_001", "2026-02-18", "TEST_SKU_UNIQ", 30, "2026-02-21"))
        
        assert False, "Should have raised UNIQUE constraint error"
    
    except RuntimeError as e:
        assert "UNIQUE constraint failed" in str(e)
        print(f"✓ UNIQUE constraint enforced: duplicate document_id rejected")
    
    conn.close()
    return True


def test_check_constraints():
    """Test 5: CHECK constraints for business rules"""
    print("\n" + "="*60)
    print("TEST 5: CHECK Constraints (Business Rules)")
    print("="*60)
    
    conn = open_connection()
    
    # Test 1: Invalid event type (should fail)
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO skus (sku, description, moq, pack_size) 
            VALUES (?, ?, ?, ?)
        """, ("TEST_SKU_CHECK", "Test Check", 1, 1))
    
    try:
        with transaction(conn) as cur:
            cur.execute("""
                INSERT INTO transactions (date, sku, event, qty) 
                VALUES (?, ?, ?, ?)
            """, ("2026-02-17", "TEST_SKU_CHECK", "INVALID_EVENT", 10))
        
        assert False, "Should have raised CHECK constraint error"
    
    except RuntimeError as e:
        assert "CHECK constraint failed" in str(e)
        print(f"✓ CHECK constraint enforced: invalid event type rejected")
    
    # Test 2: qty_received > qty_ordered in order_logs (should fail)
    try:
        with transaction(conn) as cur:
            cur.execute("""
                INSERT INTO order_logs (order_id, date, sku, qty_ordered, qty_received, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("ORD_001", "2026-02-17", "TEST_SKU_CHECK", 50, 100, "RECEIVED"))
        
        assert False, "Should have raised CHECK constraint error"
    
    except RuntimeError as e:
        assert "CHECK constraint failed" in str(e)
        print(f"✓ CHECK constraint enforced: qty_received > qty_ordered rejected")
    
    # Test 3: Valid order (should succeed)
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO order_logs (order_id, date, sku, qty_ordered, qty_received, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("ORD_002", "2026-02-17", "TEST_SKU_CHECK", 50, 30, "PARTIAL"))
    
    print(f"✓ Valid order inserted: ORD_002 (qty_received <= qty_ordered)")
    
    conn.close()
    return True


def test_cascade_delete():
    """Test 6: ON DELETE RESTRICT and CASCADE behavior"""
    print("\n" + "="*60)
    print("TEST 6: ON DELETE RESTRICT and CASCADE")
    print("="*60)
    
    conn = open_connection()
    
    # Test 6a: RESTRICT for transactions (preserve ledger history)
    # Insert parent SKU
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO skus (sku, description, moq, pack_size) 
            VALUES (?, ?, ?, ?)
        """, ("TEST_SKU_RESTRICT", "Test Restrict", 1, 1))
    
    # Insert child transaction (ledger entry)
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO transactions (date, sku, event, qty) 
            VALUES (?, ?, ?, ?)
        """, ("2026-02-17", "TEST_SKU_RESTRICT", "SNAPSHOT", 100))
    
    print(f"✓ Child transaction created for TEST_SKU_RESTRICT")
    
    # Try to delete parent SKU (should FAIL due to RESTRICT)
    try:
        with transaction(conn) as cur:
            cur.execute("DELETE FROM skus WHERE sku = ?", ("TEST_SKU_RESTRICT",))
        
        assert False, "Should have raised FK RESTRICT constraint error"
    
    except RuntimeError as e:
        assert "FOREIGN KEY constraint failed" in str(e)
        print(f"✓ ON DELETE RESTRICT enforced: cannot delete SKU with transaction history")
    
    # Test 6b: CASCADE for sales (allow cleanup of derived data)
    # Insert parent SKU
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO skus (sku, description, moq, pack_size) 
            VALUES (?, ?, ?, ?)
        """, ("TEST_SKU_CASCADE", "Test Cascade", 1, 1))
    
    # Insert child sales records
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO sales (date, sku, qty_sold) 
            VALUES (?, ?, ?)
        """, ("2026-02-17", "TEST_SKU_CASCADE", 10))
        
        cur.execute("""
            INSERT INTO sales (date, sku, qty_sold) 
            VALUES (?, ?, ?)
        """, ("2026-02-18", "TEST_SKU_CASCADE", 15))
    
    # Verify 2 sales records exist
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sales WHERE sku = ?", ("TEST_SKU_CASCADE",))
    count = cursor.fetchone()[0]
    assert count == 2, f"Expected 2 sales records, got {count}"
    print(f"✓ Child sales records created: {count}")
    
    # Delete parent SKU (should CASCADE delete sales)
    with transaction(conn) as cur:
        cur.execute("DELETE FROM skus WHERE sku = ?", ("TEST_SKU_CASCADE",))
    
    print(f"✓ Parent SKU deleted: TEST_SKU_CASCADE")
    
    # Verify sales are also deleted (CASCADE)
    cursor.execute("SELECT COUNT(*) FROM sales WHERE sku = ?", ("TEST_SKU_CASCADE",))
    count = cursor.fetchone()[0]
    assert count == 0, f"Expected 0 sales after cascade, got {count}"
    print(f"✓ ON DELETE CASCADE verified: child sales records deleted")
    
    conn.close()
    return True


def test_autoincrement_keys():
    """Test 7: AUTOINCREMENT surrogate keys"""
    print("\n" + "="*60)
    print("TEST 7: AUTOINCREMENT Surrogate Keys")
    print("="*60)
    
    conn = open_connection()
    
    # Insert SKU
    with transaction(conn) as cur:
        cur.execute("""
            INSERT INTO skus (sku, description, moq, pack_size) 
            VALUES (?, ?, ?, ?)
        """, ("TEST_SKU_AUTO", "Test Auto", 1, 1))
    
    # Insert 3 transactions (transaction_id should auto-increment)
    transaction_ids = []
    
    for i in range(3):
        with transaction(conn) as cur:
            cur.execute("""
                INSERT INTO transactions (date, sku, event, qty) 
                VALUES (?, ?, ?, ?)
            """, (f"2026-02-{17+i}", "TEST_SKU_AUTO", "SALE", -5))
            
            # Get last inserted ID
            transaction_id = cur.lastrowid
            transaction_ids.append(transaction_id)
    
    print(f"✓ Transactions inserted with AUTO IDs: {transaction_ids}")
    
    # Verify IDs are sequential
    assert transaction_ids == sorted(transaction_ids), "IDs should be sequential"
    assert transaction_ids[1] == transaction_ids[0] + 1, "IDs should increment by 1"
    print(f"✓ AUTOINCREMENT verified: sequential IDs")
    
    # Delete transaction by ID (resolves Risk #1)
    with transaction(conn) as cur:
        cur.execute("DELETE FROM transactions WHERE transaction_id = ?", (transaction_ids[1],))
    
    # Verify only 2 transactions remain
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE sku = ?", ("TEST_SKU_AUTO",))
    count = cursor.fetchone()[0]
    assert count == 2, f"Expected 2 transactions after delete, got {count}"
    print(f"✓ DELETE by transaction_id successful (resolves Risk #1)")
    
    conn.close()
    return True


def test_integrity():
    """Test 8: Final integrity check"""
    print("\n" + "="*60)
    print("TEST 8: Database Integrity Check")
    print("="*60)
    
    conn = open_connection()
    
    # Schema verification
    schema_valid = verify_schema(conn)
    assert schema_valid, "Schema verification failed"
    
    # Integrity check
    integrity_valid = integrity_check(conn)
    assert integrity_valid, "Integrity check failed"
    
    # Get stats
    stats = get_database_stats(conn)
    print(f"\nFinal Database Statistics:")
    print(f"  Schema version: {stats['schema_version']}")
    print(f"  Total rows across all tables: {sum(stats['row_counts'].values())}")
    
    conn.close()
    return True


def run_all_tests():
    """Run all tests sequentially"""
    tests = [
        ("Connection Management", test_connection),
        ("Transaction Context Manager", test_transactions),
        ("Foreign Key Constraints", test_foreign_key_constraints),
        ("UNIQUE Constraints", test_unique_constraints),
        ("CHECK Constraints", test_check_constraints),
        ("ON DELETE CASCADE", test_cascade_delete),
        ("AUTOINCREMENT Keys", test_autoincrement_keys),
        ("Database Integrity", test_integrity),
    ]
    
    print("\n" + "#"*60)
    print("# FASE 2 - Storage Layer Validation Tests")
    print("#"*60)
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
        except AssertionError as e:
            print(f"\n✗ TEST FAILED: {test_name}")
            print(f"  Error: {e}")
            failed += 1
        except Exception as e:
            print(f"\n✗ TEST ERROR: {test_name}")
            print(f"  Unexpected error: {e}")
            failed += 1
    
    print("\n" + "#"*60)
    print(f"# Test Summary: {passed} passed, {failed} failed")
    print("#"*60)
    
    if failed == 0:
        print("\n✓ ALL TESTS PASSED - FASE 2 COMPLETE")
        return 0
    else:
        print(f"\n✗ {failed} TESTS FAILED - REVIEW REQUIRED")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
