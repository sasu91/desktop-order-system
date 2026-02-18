# TASK 7.1 — Database Concurrency & Lock Handling

## FASE 7: Production Hardening - Operational Notes

### ⚙️ Configuration Summary

**PRAGMA Settings** (applied at every connection):
```python
PRAGMA foreign_keys = ON          # Enforce referential integrity
PRAGMA journal_mode = WAL         # Write-Ahead Logging (concurrent reads)
PRAGMA synchronous = NORMAL       # Balance safety/performance
PRAGMA temp_store = MEMORY        # Use RAM for temporary tables
PRAGMA cache_size = -64000        # 64MB cache
PRAGMA busy_timeout = 5000        # Wait 5 seconds for lock before failing
```

**Connection Timeout**:
- `timeout=30.0` seconds in `sqlite3.connect()`
- `busy_timeout=5000` milliseconds (5 seconds) via PRAGMA
- **Total wait time**: Up to 30 seconds before raising OperationalError

---

## 🔒 Concurrency Model (WAL Mode)

### WAL (Write-Ahead Logging) Characteristics

**Allowed Concurrency**:
- ✅ **Multiple readers** (unlimited) can read simultaneously
- ✅ **1 writer + N readers** can operate simultaneously
- ❌ **Multiple writers** CANNOT write simultaneously (serialized by SQLite)

**How WAL Works**:
1. **Writes** go to separate WAL file (`app.db-wal`)
2. **Reads** see consistent snapshot from WAL + main DB
3. **Checkpoint**: Periodically merges WAL → main DB (automatic)
4. **Shared memory**: Uses `app.db-shm` for coordination

**Files on Disk**:
```
data/
├── app.db        # Main database file
├── app.db-wal    # Write-Ahead Log (active writes)
└── app.db-shm    # Shared memory (coordination)
```

**Important**: When backing up, copy **all three files** to preserve consistency.

---

## 🚦 Single-Writer Discipline

### ConnectionFactory Pattern

**Implementation**:
```python
from src.db import get_connection_factory

factory = get_connection_factory()

# For reads (unlimited)
with factory.reader() as conn:
    rows = conn.execute("SELECT * FROM skus").fetchall()

# For writes (single writer at a time)
with factory.writer() as conn:
    conn.execute("INSERT INTO skus (...) VALUES (...)")
    conn.commit()
```

**Guarantees**:
- Only **one writer context** can be active at a time
- Writer lock uses Python `threading.Lock()` with timeout
- Default timeout: 10 seconds (configurable via `writer(timeout=X)`)
- If lock cannot be acquired → raises `TimeoutError` with diagnostic message

**Why This Matters**:
- Prevents SQLITE_BUSY errors (multiple concurrent writes)
- Ensures data consistency (serializes modifications)
- Provides clear error messages when write conflicts occur

---

## 🔁 Retry Logic with Exponential Backoff

### @retry_on_locked Decorator

**Purpose**: Automatically retry operations when database is temporarily locked.

**Usage**:
```python
from src.db import retry_on_locked

@retry_on_locked(max_attempts=3, idempotent_only=True)
def load_skus(conn):
    return conn.execute("SELECT * FROM skus").fetchall()
```

**Configuration**:
- `max_attempts=3` (default): Retry up to 3 times
- `RETRY_BASE_DELAY=0.5` seconds (doubles each attempt)
- `RETRY_MAX_DELAY=5.0` seconds (cap)

**Backoff Schedule**:
| Attempt | Delay     |
|---------|-----------|
| 1       | 0.5s      |
| 2       | 1.0s      |
| 3       | 2.0s      |
| 4       | 4.0s      |
| 5+      | 5.0s (cap)|

**Safety - Idempotency**:
- ✅ **USE for** READ operations (always safe to retry)
- ✅ **USE for** UPSERT with unique key (idempotent)
- ❌ **DO NOT USE for** INSERT without unique constraint (duplicates)
- ❌ **DO NOT USE for** non-idempotent updates (e.g., `qty = qty + 1` without WHERE)

**Error Handling**:
- If `sqlite3.OperationalError` contains "locked" → retry with backoff
- Other exceptions → immediate re-raise (no retry)
- After max attempts → raise with diagnostic message

---

## ⚠️ Multiple Application Instances (NOT SUPPORTED)

### Scenario: Two Desktop App Instances

**What Happens**:
1. **First instance** opens `data/app.db` → acquires connection
2. **Second instance** opens same `data/app.db`:
   - ✅ **Reads work** (WAL mode allows concurrent reads)
   - ⚠️ **Writes MAY conflict** if both try to write simultaneously

**Observed Behavior**:
- **Best case**: SQLite serializes writes (one waits for the other)
- **Common case**: `SQLITE_BUSY` error after timeout
- **Worst case**: Write fails, user sees error dialog

**User Experience**:
```
Error: Database is locked

This may occur if:
  1. Another application instance is accessing the database
  2. A previous connection was not properly closed
  3. The database is on a network drive (not recommended)

Action: Close all other connections and retry. If issue persists, restart the application.
```

### Recommendations

**Option A - Single Instance Enforcement** (RECOMMENDED):
```python
import fcntl  # Unix/Linux
import msvcrt  # Windows

# Create lock file on app startup
LOCK_FILE = Path("data/app.lock")

def acquire_app_lock():
    """
    Acquire exclusive lock to prevent multiple instances.
    Raise RuntimeError if lock already held.
    """
    # Implementation varies by OS (see Single Instance pattern)
    pass
```

**Option B - Multiple Instances with Warning** (CURRENT):
- Allow multiple instances
- Show warning dialog if lock errors occur
- Provide "Force Quit Other Instances" button (advanced)

**Option C - Read-Only Mode** (FUTURE):
- Second instance detects existing connection
- Opens in read-only mode (viewing only, no writes)
- Show banner: "Read-only mode (another instance is writing)"

---

## 🛠️ Troubleshooting Lock Errors

### Common Error: "Database is locked"

**Diagnosis Steps**:

1. **Check active connections**:
   ```python
   from src.db import get_active_connections_count
   print(f"Active connections: {get_active_connections_count()}")
   ```

2. **Check for orphaned connections**:
   - Did a previous operation crash without closing connection?
   - Restart application to release all handles

3. **Check WAL checkpoint**:
   ```bash
   python src/db.py stats
   # Look for large app.db-wal file (>100 MB)
   ```

4. **Check file permissions** (Linux/Unix):
   ```bash
   ls -l data/app.db*
   # Ensure user has read/write permissions
   ```

5. **Check network drive** (NOT RECOMMENDED):
   - SQLite on network drives (SMB, NFS) can cause lock issues
   - **Solution**: Move database to local disk

### Recovery Actions

**Action 1: Restart Application**
```bash
# Close all instances
# Restart once
# SQLite will recover automatically
```

**Action 2: Manual Checkpoint** (WAL file is large):
```python
import sqlite3
conn = sqlite3.connect("data/app.db")
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.close()
```

**Action 3: Restore from Backup**:
```bash
# List available backups
ls -lh data/backups/

# Restore (replace TIMESTAMP with actual value)
cp data/backups/app_TIMESTAMP_manual.db data/app.db
```

**Action 4: Integrity Check**:
```bash
python src/db.py verify
# If FAIL: database may be corrupted, restore from backup
```

---

## 📊 Monitoring & Diagnostics

### Connection Tracking

**Check Active Connections**:
```python
from src.db import get_active_connections_count
count = get_active_connections_count()
print(f"Active connections: {count}")

# Expected: 1-2 for single-threaded app
# Warning if > 5: possible connection leak
```

**Connection Leak Detection**:
- If `get_active_connections_count()` keeps increasing → leak
- **Root cause**: Connections not closed in finally block
- **Fix**: Use context managers (`with factory.reader()`)

### Database Statistics

**Get Database Stats**:
```bash
python src/db.py stats
```

**Output**:
```
Database Statistics:
  Schema version: 3
  Tables: 13
  Indices: 34
  Database size: 12.45 MB

  Row counts:
    skus: 1,250
    transactions: 45,678
    sales: 123,456
    ...
```

### Lock Contention Monitoring

**Symptoms of High Contention**:
- Frequent "Database is locked" errors
- Operations taking longer than usual
- Retry attempts logged in console

**Mitigation**:
1. **Reduce write frequency**: Batch operations where possible
2. **Use IMMEDIATE transactions**: Acquire lock earlier
3. **Optimize queries**: Add missing indices (see TASK 7.5)
4. **Connection pooling**: Reuse connections instead of open/close

---

## 🧪 Testing Lock Behavior

### Simulated Lock Test

**Test 1: Concurrent Writers** (should serialize or error):
```python
import threading
from src.db import get_connection_factory

factory = get_connection_factory()

def writer_task(sku_id):
    with factory.writer() as conn:
        conn.execute("INSERT INTO skus (sku, description) VALUES (?, ?)", (sku_id, "Test"))
        conn.commit()
        time.sleep(2)  # Hold connection for 2 seconds

# Launch 2 writers simultaneously
t1 = threading.Thread(target=writer_task, args=("SKU1",))
t2 = threading.Thread(target=writer_task, args=("SKU2",))

t1.start()
t2.start()
t1.join()
t2.join()

# Expected: t2 waits for t1 to complete (single-writer discipline)
# If timeout: TimeoutError after 10 seconds
```

**Test 2: Forced Lock** (external process):
```bash
# Terminal 1: Hold lock for 30 seconds
sqlite3 data/app.db "BEGIN EXCLUSIVE; SELECT 1; -- (wait 30s before closing)"

# Terminal 2: Try to write (should fail after timeout)
python -c "from src.db import open_connection; conn = open_connection(); conn.execute('INSERT INTO skus (sku) VALUES (\"TEST\")')"

# Expected: OperationalError after ~5 seconds (busy_timeout)
```

**Test 3: Retry Logic**:
```python
from src.db import retry_on_locked, open_connection

@retry_on_locked(max_attempts=3)
def read_skus():
    conn = open_connection()
    return conn.execute("SELECT COUNT(*) FROM skus").fetchone()[0]

# While another process holds exclusive lock:
count = read_skus()
# Expected: Retries 3 times with backoff, then raises OperationalError
```

---

## 📝 Best Practices

### DO ✅

1. **Use ConnectionFactory** for all database access
   ```python
   with factory.reader() as conn:
       # Read operations
   
   with factory.writer() as conn:
       # Write operations
       conn.commit()
   ```

2. **Always use context managers** (automatic cleanup)
   ```python
   with factory.writer() as conn:
       # Connection automatically closed on exit
   ```

3. **Use transactions explicitly** for writes
   ```python
   with factory.writer() as conn:
       conn.execute("BEGIN IMMEDIATE")  # Acquire lock early
       # ... multiple writes ...
       conn.commit()
   ```

4. **Check integrity periodically**
   ```bash
   python src/db.py verify
   ```

5. **Backup before risky operations**
   ```bash
   python src/db.py backup manual_before_bulk_delete
   ```

### DON'T ❌

1. **Don't open multiple connections unnecessarily**
   ```python
   # BAD: Opens N connections
   for sku in skus:
       conn = open_connection()  # ❌ New connection every loop
       conn.execute("INSERT INTO skus (...) VALUES (...)")
       conn.close()
   
   # GOOD: Reuse connection
   with factory.writer() as conn:
       for sku in skus:
           conn.execute("INSERT INTO skus (...) VALUES (...)")  # ✅ Single connection
       conn.commit()
   ```

2. **Don't use @retry_on_locked for non-idempotent writes**
   ```python
   # BAD: May insert duplicates on retry
   @retry_on_locked(max_attempts=3)
   def add_sale(sku, qty):
       conn.execute("INSERT INTO sales (sku, qty) VALUES (?, ?)", (sku, qty))  # ❌
   
   # GOOD: Idempotent with unique constraint
   @retry_on_locked(max_attempts=3)
   def record_sale(date, sku, qty):
       conn.execute(
           "INSERT INTO sales (date, sku, qty) VALUES (?, ?, ?) "
           "ON CONFLICT(date, sku) DO UPDATE SET qty = qty + excluded.qty",
           (date, sku, qty)
       )  # ✅ Idempotent
   ```

3. **Don't put database on network drive**
   ```
   ❌ \\network\share\app.db  (SMB/CIFS)
   ❌ /mnt/nfs/app.db         (NFS)
   ✅ C:\app\data\app.db      (Local disk)
   ✅ /home/user/app/data/app.db  (Local disk)
   ```

4. **Don't ignore lock errors** (investigate root cause)
   ```python
   try:
       with factory.writer() as conn:
           conn.execute("INSERT INTO ...")
   except sqlite3.OperationalError:
       pass  # ❌ Silent failure
   
   # GOOD: Log and inform user
   except sqlite3.OperationalError as e:
       logger.error(f"Database locked: {e}", exc_info=True)
       show_error_dialog("Database Locked", "Close other instances and retry.")
   ```

5. **Don't run long-running operations in writer context**
   ```python
   # BAD: Holds writer lock for entire duration
   with factory.writer() as conn:
       for sku in skus:  # 10,000 iterations
           conn.execute("INSERT INTO ...")
           time.sleep(0.1)  # Network call, etc.  ❌
   
   # GOOD: Batch quickly, release lock
   with factory.writer() as conn:
       conn.executemany("INSERT INTO ...", batch)  # Fast bulk insert  ✅
       conn.commit()
   ```

---

## 🎯 STOP CONDITIONS (TASK 7.1)

### ✅ Test 1: Simulated Lock (Two Writers)

**Setup**:
- Start Test 1 above (two concurrent writers)

**Expected**:
- ✅ No application crash
- ✅ Second writer waits for first (or times out with clear message)
- ✅ No partial writes (all-or-nothing)
- ✅ User sees informative error if timeout

### ✅ Test 2: External Lock (sqlite3 CLI)

**Setup**:
- Open exclusive lock with `sqlite3` CLI (30 seconds)
- Try to write from application

**Expected**:
- ✅ Application retries with backoff (if @retry_on_locked used)
- ✅ User sees "Database locked" dialog after timeout
- ✅ No crash, no data corruption

### ✅ Test 3: Connection Tracking

**Setup**:
- Monitor `get_active_connections_count()` during operations

**Expected**:
- ✅ Count increases when connections open
- ✅ Count decreases when connections close
- ✅ No leaks (count returns to 0 after operations complete)

### ✅ Test 4: Integrity After Lock

**Setup**:
- Force lock error during write operation
- Verify database integrity after recovery

**Expected**:
- ✅ `PRAGMA integrity_check` returns "ok"
- ✅ `PRAGMA foreign_key_check` returns no violations
- ✅ No orphaned or partial records

---

## 📚 References

- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
- [SQLite Locking](https://www.sqlite.org/lockingv3.html)
- [PRAGMA Statements](https://www.sqlite.org/pragma.html)
- [Python sqlite3 Module](https://docs.python.org/3/library/sqlite3.html)

---

**Document Version**: 1.0  
**Last Updated**: 2026-02-17 (FASE 7 TASK 7.1)  
**Status**: Implementation Complete — Testing Pending
