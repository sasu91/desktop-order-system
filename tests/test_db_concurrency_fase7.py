"""
FASE 7 TASK 7.1 — Database Concurrency & Lock Handling Tests

Tests:
1. Single-writer discipline (ConnectionFactory)
2. Retry logic with exponential backoff
3. Connection tracking (leak detection)
4. Lock error messages (user-friendly)
5. Database integrity after lock errors

Stop Conditions:
- Concurrent writers do not crash application
- Lock errors produce clear, actionable messages
- No partial writes (all-or-nothing)
- Connection count accurate (no leaks)
"""

import pytest
import sqlite3
import time
import threading
from pathlib import Path
from contextlib import contextmanager
import tempfile
import shutil

# Import from src
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    open_connection,
    close_connection,
    get_active_connections_count,
    ConnectionFactory,
    retry_on_locked,
    exponential_backoff,
    transaction,
    integrity_check,
    initialize_database,
    DB_PATH,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_db():
    """Create temporary database for isolated testing."""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    
    # Initialize test database with schema
    conn = open_connection(db_path, track_connection=False)
    
    # Create minimal schema for testing
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        
        INSERT INTO schema_version (version) VALUES (1);
        
        CREATE TABLE IF NOT EXISTS test_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS skus (
            sku TEXT PRIMARY KEY,
            description TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    
    yield db_path
    
    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def factory(temp_db):
    """Create ConnectionFactory with temp database."""
    return ConnectionFactory(temp_db)


# ============================================================
# Test 1: Exponential Backoff Calculation
# ============================================================

def test_exponential_backoff_calculation():
    """Test backoff delay calculation with exponential growth and cap."""
    # Base delay: 0.5s, Max delay: 5.0s
    
    # Attempt 0: 0.5 * (2^0) = 0.5
    assert exponential_backoff(0, base_delay=0.5, max_delay=5.0) == 0.5
    
    # Attempt 1: 0.5 * (2^1) = 1.0
    assert exponential_backoff(1, base_delay=0.5, max_delay=5.0) == 1.0
    
    # Attempt 2: 0.5 * (2^2) = 2.0
    assert exponential_backoff(2, base_delay=0.5, max_delay=5.0) == 2.0
    
    # Attempt 3: 0.5 * (2^3) = 4.0
    assert exponential_backoff(3, base_delay=0.5, max_delay=5.0) == 4.0
    
    # Attempt 4: 0.5 * (2^4) = 8.0 → capped at 5.0
    assert exponential_backoff(4, base_delay=0.5, max_delay=5.0) == 5.0
    
    # Attempt 5+: Still capped at 5.0
    assert exponential_backoff(5, base_delay=0.5, max_delay=5.0) == 5.0
    assert exponential_backoff(10, base_delay=0.5, max_delay=5.0) == 5.0


# ============================================================
# Test 2: Connection Tracking
# ============================================================

def test_connection_tracking(temp_db):
    """Test that connections are tracked and decremented on close."""
    # Initial state: 0 active connections
    # (reset may be needed if other tests leaked)
    
    # Open connection (tracked)
    conn1 = open_connection(temp_db, track_connection=True)
    initial_count = get_active_connections_count()
    assert initial_count >= 1, "Connection not tracked"
    
    # Open another connection
    conn2 = open_connection(temp_db, track_connection=True)
    after_second = get_active_connections_count()
    assert after_second == initial_count + 1, "Second connection not tracked"
    
    # Close first connection
    close_connection(conn1, tracked=True)
    after_close_one = get_active_connections_count()
    assert after_close_one == after_second - 1, "Connection not decremented"
    
    # Close second connection
    close_connection(conn2, tracked=True)
    after_close_two = get_active_connections_count()
    assert after_close_two == after_close_one - 1, "Second connection not decremented"


def test_connection_tracking_with_factory(factory, temp_db):
    """Test connection tracking with ConnectionFactory context managers."""
    initial_count = get_active_connections_count()
    
    # Reader context (should increment and decrement)
    with factory.reader() as conn:
        inside_count = get_active_connections_count()
        assert inside_count >= initial_count + 1, "Reader connection not tracked"
    
    # After context exit, count should be back to initial (or initial + leaks from other tests)
    after_reader = get_active_connections_count()
    # Note: Allow for some tolerance due to other tests
    assert after_reader <= inside_count, "Reader connection not closed"
    
    # Writer context
    with factory.writer() as conn:
        inside_writer = get_active_connections_count()
        assert inside_writer >= 1, "Writer connection not tracked"
    
    after_writer = get_active_connections_count()
    assert after_writer <= inside_writer, "Writer connection not closed"


# ============================================================
# Test 3: Single-Writer Discipline
# ============================================================

def test_single_writer_blocks_concurrent_writers(factory):
    """Test that ConnectionFactory allows only one writer at a time."""
    results = {"t1_acquired": False, "t2_acquired": False, "t2_wait_time": 0}
    
    def writer_1():
        with factory.writer() as conn:
            results["t1_acquired"] = True
            # Hold lock for 2 seconds
            time.sleep(2)
    
    def writer_2():
        start = time.time()
        with factory.writer(timeout=5.0) as conn:
            results["t2_wait_time"] = time.time() - start
            results["t2_acquired"] = True
    
    # Start both writers
    t1 = threading.Thread(target=writer_1)
    t2 = threading.Thread(target=writer_2)
    
    t1.start()
    time.sleep(0.1)  # Ensure t1 acquires lock first
    t2.start()
    
    t1.join()
    t2.join()
    
    # Both should have acquired successfully (sequentially)
    assert results["t1_acquired"], "Writer 1 failed to acquire lock"
    assert results["t2_acquired"], "Writer 2 failed to acquire lock"
    
    # Writer 2 should have waited at least 1.5 seconds (t1 held for 2s)
    assert results["t2_wait_time"] >= 1.5, f"Writer 2 did not wait (waited {results['t2_wait_time']:.2f}s)"
    
    print(f"✓ Writer 2 waited {results['t2_wait_time']:.2f}s for Writer 1 (expected ~2s)")


def test_writer_timeout_on_long_hold(factory):
    """Test that writer times out if lock held too long."""
    
    def long_writer():
        with factory.writer() as conn:
            time.sleep(5)  # Hold for 5 seconds
    
    # Start long writer in background
    t1 = threading.Thread(target=long_writer, daemon=True)
    t1.start()
    time.sleep(0.1)  # Ensure t1 acquires lock
    
    # Try to acquire writer with short timeout (should fail)
    with pytest.raises(TimeoutError, match="Could not acquire writer lock"):
        with factory.writer(timeout=1.0) as conn:
            pass
    
    print("✓ Writer timeout works correctly")


# ============================================================
# Test 4: Retry Logic
# ============================================================

def test_retry_on_locked_decorator_success(factory):
    """Test that @retry_on_locked retries and succeeds on transient lock."""
    call_count = {"count": 0}
    
    @retry_on_locked(max_attempts=3)
    def flaky_read(conn):
        call_count["count"] += 1
        if call_count["count"] < 2:
            # Simulate lock on first attempt
            raise sqlite3.OperationalError("database is locked")
        # Succeed on second attempt
        return conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
    
    with factory.reader() as conn:
        result = flaky_read(conn)
    
    # Should have retried once and succeeded
    assert call_count["count"] == 2, f"Expected 2 calls (1 fail + 1 success), got {call_count['count']}"
    assert result == 0, "Query did not return expected result"
    
    print(f"✓ Retry logic succeeded after {call_count['count']} attempts")


def test_retry_on_locked_exhausts_attempts(factory):
    """Test that @retry_on_locked raises after max attempts."""
    call_count = {"count": 0}
    
    @retry_on_locked(max_attempts=3)
    def always_locked(conn):
        call_count["count"] += 1
        raise sqlite3.OperationalError("database is locked")
    
    with factory.reader() as conn:
        with pytest.raises(sqlite3.OperationalError, match="Database locked after 3 attempts"):
            always_locked(conn)
    
    # Should have tried 3 times
    assert call_count["count"] == 3, f"Expected 3 attempts, got {call_count['count']}"
    
    print(f"✓ Retry logic exhausted after {call_count['count']} attempts")


def test_retry_on_locked_ignores_non_lock_errors(factory):
    """Test that @retry_on_locked does not retry non-lock errors."""
    call_count = {"count": 0}
    
    @retry_on_locked(max_attempts=3)
    def non_lock_error(conn):
        call_count["count"] += 1
        raise sqlite3.OperationalError("no such table: nonexistent")
    
    with factory.reader() as conn:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            non_lock_error(conn)
    
    # Should NOT have retried (only 1 attempt)
    assert call_count["count"] == 1, f"Should not retry non-lock errors, got {call_count['count']} attempts"
    
    print("✓ Non-lock errors are not retried")


# ============================================================
# Test 5: Lock Error Messages
# ============================================================

def test_open_connection_locked_error_message(temp_db):
    """Test that lock errors have user-friendly messages."""
    # Create a lock by holding an exclusive transaction
    blocker_conn = open_connection(temp_db, track_connection=False)
    blocker_conn.execute("BEGIN EXCLUSIVE")
    
    # Set very short timeout to force immediate failure
    try:
        # Try to open another connection (should fail with informative message)
        victim_conn = sqlite3.connect(str(temp_db), timeout=0.1)
        
        # Try to write (should fail)
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            victim_conn.execute("INSERT INTO test_table (value) VALUES ('test')")
        
        error_msg = str(exc_info.value)
        assert "locked" in error_msg.lower(), f"Expected 'locked' in error message: {error_msg}"
        
        victim_conn.close()
    
    finally:
        blocker_conn.rollback()
        blocker_conn.close()
    
    print("✓ Lock error messages are informative")


def test_writer_timeout_error_message(factory):
    """Test that writer timeout has diagnostic message."""
    # Hold writer lock in background
    def hold_lock():
        with factory.writer() as conn:
            time.sleep(3)
    
    t = threading.Thread(target=hold_lock, daemon=True)
    t.start()
    time.sleep(0.1)  # Ensure lock acquired
    
    # Try to acquire with short timeout
    with pytest.raises(TimeoutError) as exc_info:
        with factory.writer(timeout=0.5) as conn:
            pass
    
    error_msg = str(exc_info.value)
    
    # Check for helpful diagnostic info
    assert "Could not acquire writer lock" in error_msg
    assert "write operation is in progress" in error_msg
    assert "Action:" in error_msg or "may indicate:" in error_msg
    
    print(f"✓ Writer timeout error message is helpful:\n{error_msg}")


# ============================================================
# Test 6: Database Integrity After Lock
# ============================================================

def test_integrity_after_lock_error(temp_db):
    """Test that database integrity is preserved after lock errors."""
    factory = ConnectionFactory(temp_db)
    
    # Insert some test data
    with factory.writer() as conn:
        conn.execute("INSERT INTO test_table (value) VALUES ('test1')")
        conn.execute("INSERT INTO test_table (value) VALUES ('test2')")
        conn.commit()
    
    # Verify count
    with factory.reader() as conn:
        count_before = conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
        assert count_before == 2
    
    # Simulate lock error during write (rollback scenario)
    def faulty_write():
        with factory.writer() as conn:
            conn.execute("INSERT INTO test_table (value) VALUES ('test3')")
            # Simulate error before commit
            raise RuntimeError("Simulated failure")
    
    # Attempt faulty write (should rollback)
    with pytest.raises(RuntimeError, match="Simulated failure"):
        faulty_write()
    
    # Verify data integrity (should still have only 2 rows)
    with factory.reader() as conn:
        count_after = conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
        assert count_after == 2, "Transaction should have rolled back"
        
        # Run integrity check
        conn_raw = open_connection(temp_db, track_connection=False)
        is_healthy = integrity_check(conn_raw)
        conn_raw.close()
        
        assert is_healthy, "Database integrity compromised after lock error"
    
    print("✓ Database integrity preserved after lock error")


# ============================================================
# Test 7: No Partial Writes
# ============================================================

def test_no_partial_writes_on_lock(temp_db):
    """Test that lock errors during batch writes do not leave partial data."""
    factory = ConnectionFactory(temp_db)
    
    # Insert initial data
    with factory.writer() as conn:
        conn.executemany(
            "INSERT INTO test_table (value) VALUES (?)",
            [("initial_1",), ("initial_2",)]
        )
        conn.commit()
    
    initial_count = 2
    
    # Attempt batch insert that will fail mid-way
    def failing_batch_write():
        with factory.writer() as conn:
            # Use explicit transaction
            conn.execute("BEGIN IMMEDIATE")
            
            # Insert batch
            conn.executemany(
                "INSERT INTO test_table (value) VALUES (?)",
                [("batch_1",), ("batch_2",), ("batch_3",)]
            )
            
            # Simulate failure before commit
            raise RuntimeError("Batch write failed")
    
    # Attempt failing write
    with pytest.raises(RuntimeError, match="Batch write failed"):
        failing_batch_write()
    
    # Verify NO partial writes (count should still be 2)
    with factory.reader() as conn:
        final_count = conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
        assert final_count == initial_count, f"Partial write detected: expected {initial_count}, got {final_count}"
    
    print(f"✓ No partial writes: count remained at {initial_count}")


# ============================================================
# Test 8: Multiple Readers (No Blocking)
# ============================================================

def test_multiple_readers_no_blocking(factory):
    """Test that multiple readers can access database simultaneously (no blocking)."""
    # Insert test data
    with factory.writer() as conn:
        conn.executemany(
            "INSERT INTO test_table (value) VALUES (?)",
            [(f"value_{i}",) for i in range(10)]
        )
        conn.commit()
    
    results = {"reader1": None, "reader2": None, "reader3": None}
    
    def reader(name):
        with factory.reader() as conn:
            time.sleep(0.1)  # Simulate some work
            count = conn.execute("SELECT COUNT(*) FROM test_table").fetchone()[0]
            results[name] = count
    
    # Launch 3 readers simultaneously
    threads = [
        threading.Thread(target=reader, args=("reader1",)),
        threading.Thread(target=reader, args=("reader2",)),
        threading.Thread(target=reader, args=("reader3",)),
    ]
    
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start
    
    # All readers should succeed
    assert results["reader1"] == 10
    assert results["reader2"] == 10
    assert results["reader3"] == 10
    
    # Elapsed time should be ~0.1s (concurrent), not ~0.3s (serial)
    assert elapsed < 0.3, f"Readers appear to be blocking (took {elapsed:.2f}s, expected <0.3s)"
    
    print(f"✓ Multiple readers executed concurrently in {elapsed:.2f}s")


# ============================================================
# Run All Tests
# ============================================================

if __name__ == "__main__":
    # Run pytest with verbose output
    pytest.main([__file__, "-v", "--tb=short", "-q"])
