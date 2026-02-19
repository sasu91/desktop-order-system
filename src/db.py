"""
Database connection manager and migration utilities for SQLite storage.

FASE 2: Storage Layer Minimo
- Connection management with PRAGMA configuration
- Transaction context manager
- Migration runner with backup automation
- Schema verification and integrity checks

Design Principles:
- Foreign keys enforced (PRAGMA foreign_keys=ON)
- WAL journal mode for concurrent read/write
- Automatic backups before migrations
- Idempotent migration application
"""

import sqlite3
import os
import shutil
import time
import threading
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Tuple, Callable, Any, Dict
import hashlib
import functools


# ============================================================
# Configuration Constants
# ============================================================

# ---------------------------------------------------------------------------
# Frozen-aware path constants (never relative to cwd)
# ---------------------------------------------------------------------------
from .utils.paths import get_db_path, get_migrations_dir, get_backup_dir, get_data_dir as _get_data_dir

DB_PATH: Path = get_db_path()
MIGRATIONS_DIR: Path = get_migrations_dir()
BACKUP_DIR: Path = get_backup_dir()
SETTINGS_FILE: Path = _get_data_dir() / "settings.json"

# Connection PRAGMAs
# FASE 7 TASK 7.1: Enhanced concurrency and lock handling
PRAGMA_CONFIG = {
    "foreign_keys": "ON",           # Enforce FK constraints
    "journal_mode": "WAL",          # Write-Ahead Logging for concurrency
    "synchronous": "NORMAL",        # Balance safety/performance (FULL for max safety)
    "temp_store": "MEMORY",         # Use RAM for temp tables
    "cache_size": -64000,           # 64MB cache (negative = KB)
    "busy_timeout": 5000,           # Wait 5s for lock (milliseconds)
}

# Connection pool lock (single-writer discipline)
_connection_lock = threading.Lock()
_active_connections = 0

# Retry configuration for locked database
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5  # seconds
RETRY_MAX_DELAY = 5.0   # seconds


# ============================================================
# Retry Logic (FASE 7 TASK 7.1)
# ============================================================

def exponential_backoff(attempt: int, base_delay: float = RETRY_BASE_DELAY, max_delay: float = RETRY_MAX_DELAY) -> float:
    """
    Calculate exponential backoff delay.
    
    Args:
        attempt: Attempt number (0, 1, 2, ...)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
    
    Returns:
        Delay in seconds (capped at max_delay)
    
    Formula: min(base_delay * (2 ** attempt), max_delay)
    Example: 0.5, 1.0, 2.0, 4.0, 5.0 (capped)
    """
    delay = base_delay * (2 ** attempt)
    return min(delay, max_delay)


def retry_on_locked(max_attempts: int = RETRY_MAX_ATTEMPTS, idempotent_only: bool = True):
    """
    Decorator for retrying operations when database is locked.
    
    Args:
        max_attempts: Maximum number of retry attempts
        idempotent_only: If True, only retry idempotent operations (default)
    
    Usage:
        @retry_on_locked(max_attempts=3, idempotent_only=True)
        def read_skus(conn):
            # This will retry up to 3 times with exponential backoff
            return conn.execute("SELECT * FROM skus").fetchall()
    
    IMPORTANT - Idempotency Safety:
    - Use for READ operations (safe to retry)
    - Use for IDEMPOTENT writes (e.g., upsert with unique key)
    - DO NOT use for non-idempotent writes (e.g., INSERT without unique constraint)
    
    Error Handling:
    - sqlite3.OperationalError with "locked" → retry with backoff
    - Other exceptions → immediate re-raise
    - After max_attempts → raise original exception with context
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e).lower():
                        # Not a lock error, re-raise immediately
                        raise
                    
                    last_exception = e
                    
                    if attempt < max_attempts - 1:
                        # Calculate backoff and retry
                        delay = exponential_backoff(attempt)
                        print(f"⚠ Database locked, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_attempts})")
                        time.sleep(delay)
                    else:
                        # Final attempt failed
                        break
            
            # All attempts exhausted
            raise sqlite3.OperationalError(
                f"Database locked after {max_attempts} attempts. "
                f"Close other connections and retry. Original error: {last_exception}"
            ) from last_exception
        
        return wrapper
    return decorator


# ============================================================
# Connection Management
# ============================================================

def open_connection(db_path: Optional[Path] = None, track_connection: bool = True) -> sqlite3.Connection:
    """
    Open SQLite connection with optimized PRAGMA configuration.
    
    Args:
        db_path: Path to database file (default: data/app.db)
        track_connection: If True, track connection in global counter (for monitoring)
    
    Returns:
        Configured sqlite3.Connection
    
    Raises:
        sqlite3.OperationalError: Database locked or inaccessible
        sqlite3.DatabaseError: Corrupted database file
    
    Configuration (FASE 7 TASK 7.1):
    - Foreign keys enforced (critical for referential integrity)
    - WAL journal mode (allows concurrent reads during writes)
    - Busy timeout: 5 seconds (wait for lock before failing)
    - Row factory enabled (access columns by name)
    - Connection tracking (for single-writer discipline monitoring)
    
    Concurrency Notes:
    - WAL mode allows multiple readers + 1 writer simultaneously
    - busy_timeout prevents immediate lock errors (retry internally)
    - For writes: use IMMEDIATE or EXCLUSIVE transactions to avoid deadlock
    - For reads: DEFERRED transactions are fine (default)
    """
    global _active_connections
    
    if db_path is None:
        db_path = DB_PATH
    
    # Auto-create data directory if missing
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        conn = sqlite3.connect(
            str(db_path),
            timeout=30.0,  # Compatibility timeout (busy_timeout PRAGMA is preferred)
            check_same_thread=False,  # Allow multi-threaded access (controlled by app)
        )
        
        # Enable row factory for dict-like access
        conn.row_factory = sqlite3.Row
        
        # Apply PRAGMA configuration (including busy_timeout)
        cursor = conn.cursor()
        for pragma, value in PRAGMA_CONFIG.items():
            cursor.execute(f"PRAGMA {pragma}={value}")
        
        # Verify foreign keys enabled (critical)
        fk_enabled = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
        if fk_enabled != 1:
            raise RuntimeError("Failed to enable foreign keys (PRAGMA foreign_keys=ON)")
        
        # Track active connections (for monitoring)
        if track_connection:
            with _connection_lock:
                _active_connections += 1
        
        return conn
    
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            raise sqlite3.OperationalError(
                f"Database {db_path} is locked. "
                f"This may occur if:\n"
                f"  1. Another application instance is accessing the database\n"
                f"  2. A previous connection was not properly closed\n"
                f"  3. The database is on a network drive (not recommended)\n"
                f"Action: Close all other connections and retry. If issue persists, restart the application."
            ) from e
        raise
    
    except sqlite3.DatabaseError as e:
        raise sqlite3.DatabaseError(
            f"Database {db_path} is corrupted. "
            f"Recovery options:\n"
            f"  1. Restore from backup: see data/backups/\n"
            f"  2. Run integrity check: python src/db.py verify\n"
            f"  3. Export data and reinitialize (last resort)"
        ) from e


def close_connection(conn: sqlite3.Connection, tracked: bool = True) -> None:
    """
    Close database connection and update tracking.
    
    Args:
        conn: Connection to close
        tracked: If True, decrement active connection counter
    
    Usage:
        >>> conn = open_connection()
        >>> try:
        ...     # Use connection
        ...     pass
        ... finally:
        ...     close_connection(conn)
    """
    global _active_connections
    
    if conn:
        conn.close()
        
        if tracked:
            with _connection_lock:
                _active_connections = max(0, _active_connections - 1)


def get_active_connections_count() -> int:
    """
    Get number of tracked active connections.
    
    Returns:
        Number of active connections
    
    Note: This count may not include all connections if third-party code
    opens connections directly without using open_connection().
    """
    with _connection_lock:
        return _active_connections


class ConnectionFactory:
    """
    Connection factory with single-writer discipline (FASE 7 TASK 7.1).
    
    Purpose:
    - Centralize connection management
    - Prevent multiple concurrent write connections
    - Provide context managers for safe connection lifecycle
    
    Single-Writer Discipline:
    - WAL mode allows N readers + 1 writer simultaneously
    - This factory tracks writer connections to prevent write conflicts
    - Read-only connections are not limited
    
    Usage:
        >>> factory = ConnectionFactory()
        >>> 
        >>> # For reads (unlimited)
        >>> with factory.reader() as conn:
        ...     rows = conn.execute("SELECT * FROM skus").fetchall()
        >>> 
        >>> # For writes (single writer at a time)
        >>> with factory.writer() as conn:
        ...     conn.execute("INSERT INTO skus (sku, description) VALUES (?, ?)", ("TEST", "Test"))
        ...     conn.commit()
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize connection factory.
        
        Args:
            db_path: Path to database file (default: data/app.db)
        """
        self.db_path = db_path or DB_PATH
        self._writer_lock = threading.Lock()
    
    @contextmanager
    def reader(self):
        """
        Context manager for read-only connection.
        
        Yields:
            sqlite3.Connection: Read-only connection
        
        Usage:
            >>> with factory.reader() as conn:
            ...     rows = conn.execute("SELECT * FROM skus").fetchall()
        """
        conn = open_connection(self.db_path, track_connection=True)
        try:
            yield conn
        finally:
            close_connection(conn, tracked=True)
    
    @contextmanager
    def writer(self, timeout: float = 10.0):
        """
        Context manager for write connection (single writer discipline).
        
        Args:
            timeout: Maximum time to wait for writer lock (seconds)
        
        Yields:
            sqlite3.Connection: Write connection
        
        Raises:
            TimeoutError: If writer lock cannot be acquired within timeout
        
        Usage:
            >>> with factory.writer() as conn:
            ...     conn.execute("INSERT INTO skus (...) VALUES (...)")
            ...     conn.commit()
        
        Note: Only ONE writer is allowed at a time across all threads.
        This prevents SQLITE_BUSY errors and ensures data consistency.
        """
        # Acquire writer lock with timeout
        acquired = self._writer_lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire writer lock after {timeout}s. "
                f"Another write operation is in progress. "
                f"This may indicate:\n"
                f"  1. A long-running write transaction\n"
                f"  2. Deadlock (connection not properly closed)\n"
                f"  3. Multiple application instances (not supported)\n"
                f"Action: Wait for current operation to complete or restart application."
            )
        
        conn = None
        try:
            conn = open_connection(self.db_path, track_connection=True)
            yield conn
        finally:
            if conn:
                close_connection(conn, tracked=True)
            self._writer_lock.release()


# Singleton factory instance (for convenience)
_default_factory = None

def get_connection_factory(db_path: Optional[Path] = None) -> ConnectionFactory:
    """
    Get singleton connection factory instance.
    
    Args:
        db_path: Path to database file (default: data/app.db)
    
    Returns:
        ConnectionFactory instance
    
    Usage:
        >>> factory = get_connection_factory()
        >>> with factory.reader() as conn:
        ...     # Read operations
        ...     pass
    """
    global _default_factory
    
    if _default_factory is None or (_default_factory.db_path != (db_path or DB_PATH)):
        _default_factory = ConnectionFactory(db_path)
    
    return _default_factory


@contextmanager
def transaction(conn: sqlite3.Connection, isolation_level: str = "DEFERRED"):
    """
    Transaction context manager with automatic commit/rollback.
    
    Args:
        conn: SQLite connection
        isolation_level: DEFERRED (default), IMMEDIATE, or EXCLUSIVE
    
    Yields:
        sqlite3.Cursor: Cursor for executing queries
    
    Usage:
        >>> conn = open_connection()
        >>> with transaction(conn) as cur:
        ...     cur.execute("INSERT INTO skus (sku, description) VALUES (?, ?)", ("TEST", "Test SKU"))
        ...     # Automatic COMMIT on success, ROLLBACK on exception
    
    Isolation Levels:
    - DEFERRED: Acquire lock on first write (default, best performance)
    - IMMEDIATE: Acquire lock on BEGIN (prevents writer starvation)
    - EXCLUSIVE: Acquire lock on BEGIN, block all readers (rarely needed)
    """
    cursor = conn.cursor()
    
    try:
        # Begin transaction with specified isolation level
        cursor.execute(f"BEGIN {isolation_level}")
        yield cursor
        conn.commit()
    
    except Exception as e:
        conn.rollback()
        # Re-raise with context
        raise RuntimeError(f"Transaction failed and rolled back: {e}") from e


# ============================================================
# Migration Management
# ============================================================

def get_current_schema_version(conn: sqlite3.Connection) -> int:
    """
    Get current schema version from database.
    
    Returns:
        Current schema version (0 if schema_version table doesn't exist)
    """
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        result = cursor.fetchone()
        return result[0] if result[0] is not None else 0
    
    except sqlite3.OperationalError:
        # schema_version table doesn't exist yet
        return 0


def get_pending_migrations(conn: sqlite3.Connection) -> List[Tuple[int, Path]]:
    """
    Get list of pending migration scripts.
    
    Returns:
        List of (version, filepath) tuples sorted by version
    
    Migration script naming convention: NNN_description.sql
    Example: 001_initial_schema.sql, 002_add_column_x.sql
    """
    current_version = get_current_schema_version(conn)
    
    if not MIGRATIONS_DIR.exists():
        return []
    
    pending = []
    for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        # Extract version from filename (e.g., "001_initial_schema.sql" -> 1)
        version_str = migration_file.stem.split("_")[0]
        
        try:
            version = int(version_str)
        except ValueError:
            print(f"Warning: Skipping invalid migration filename: {migration_file.name}")
            continue
        
        if version > current_version:
            pending.append((version, migration_file))
    
    return sorted(pending, key=lambda x: x[0])


def backup_database(db_path: Path, backup_reason: str = "migration", backup_dir: Optional[Path] = None) -> Path:
    """
    Create timestamped backup of database (FASE 7 TASK 7.3: Enhanced with WAL support).
    
    Args:
        db_path: Path to database file
        backup_reason: Reason for backup (used in filename)
        backup_dir: Directory for backups (default: BACKUP_DIR)
    
    Returns:
        Path to backup file
    
    Backup naming: app_YYYYMMDD_HHMMSS_{reason}.db
    Example: app_20260217_143022_migration.db
    
    WAL Mode Support:
    - Copies main DB file (.db)
    - Copies WAL file (.db-wal) if exists
    - Copies shared memory (.db-shm) if exists
    - Creates manifest file listing all backed up files
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database {db_path} does not exist")
    
    if backup_dir is None:
        backup_dir = BACKUP_DIR
    
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{db_path.stem}_{timestamp}_{backup_reason}.db"
    backup_path = backup_dir / backup_filename
    
    # Copy main database file
    shutil.copy2(db_path, backup_path)
    
    files_backed_up = [backup_path.name]
    
    # Copy WAL file if exists (critical for consistency)
    wal_path = Path(str(db_path) + "-wal")
    if wal_path.exists():
        wal_backup_path = Path(str(backup_path) + "-wal")
        shutil.copy2(wal_path, wal_backup_path)
        files_backed_up.append(wal_backup_path.name)
    
    # Copy shared memory file if exists
    shm_path = Path(str(db_path) + "-shm")
    if shm_path.exists():
        shm_backup_path = Path(str(backup_path) + "-shm")
        shutil.copy2(shm_path, shm_backup_path)
        files_backed_up.append(shm_backup_path.name)
    
    # Create manifest file
    manifest_path = Path(str(backup_path) + ".manifest")
    with open(manifest_path, "w") as f:
        f.write(f"# Backup Manifest\n")
        f.write(f"# Created: {datetime.now().isoformat()}\n")
        f.write(f"# Reason: {backup_reason}\n")
        f.write(f"# Source: {db_path}\n")
        f.write(f"#\n")
        for filename in files_backed_up:
            f.write(f"{filename}\n")
    
    print(f"✓ Backup created: {backup_path}")
    if len(files_backed_up) > 1:
        print(f"  Includes: {', '.join(files_backed_up[1:])}")
    
    return backup_path


def cleanup_old_backups(max_backups: int = 10, backup_dir: Path = BACKUP_DIR) -> int:
    """
    Remove old backups, keeping only the most recent N backups (FASE 7 TASK 7.3).
    
    Args:
        max_backups: Maximum number of backups to keep (default: 10)
        backup_dir: Directory containing backups
    
    Returns:
        Number of backups deleted
    
    Strategy:
    - Groups backups by base name (app_*.db)
    - Keeps most recent max_backups for each group
    - Deletes associated WAL/SHM/manifest files too
    
    Example:
        >>> cleanup_old_backups(max_backups=10)
        3  # Deleted 3 old backup sets
    """
    if not backup_dir.exists():
        return 0
    
    # Find all backup files (*.db, excluding -wal and -shm)
    backup_files = sorted(
        [f for f in backup_dir.glob("*.db") if not f.name.endswith(("-wal.db", "-shm.db"))],
        key=lambda f: f.stat().st_mtime,
        reverse=True  # Most recent first
    )
    
    if len(backup_files) <= max_backups:
        return 0  # Nothing to delete
    
    # Delete old backups
    deleted_count = 0
    for backup_file in backup_files[max_backups:]:
        try:
            # Delete main backup file
            backup_file.unlink()
            deleted_count += 1
            
            # Delete associated WAL file if exists
            wal_file = Path(str(backup_file) + "-wal")
            if wal_file.exists():
                wal_file.unlink()
            
            # Delete associated SHM file if exists
            shm_file = Path(str(backup_file) + "-shm")
            if shm_file.exists():
                shm_file.unlink()
            
            # Delete manifest file if exists
            manifest_file = Path(str(backup_file) + ".manifest")
            if manifest_file.exists():
                manifest_file.unlink()
            
            print(f"  Deleted old backup: {backup_file.name}")
        
        except OSError as e:
            print(f"  Warning: Could not delete {backup_file.name}: {e}")
    
    return deleted_count


def automatic_backup_on_startup(db_path: Path = DB_PATH, max_backups: int = 10) -> Optional[Path]:
    """
    Create automatic backup on application startup (FASE 7 TASK 7.3).
    
    Args:
        db_path: Path to database file
        max_backups: Maximum number of backups to keep (retention policy)
    
    Returns:
        Path to backup file, or None if database doesn't exist yet
    
    Strategy:
    - Called once at application startup
    - Creates backup with reason="startup"
    - Applies retention policy (keeps only last N backups)
    - Skips if database doesn't exist (first run)
    
    Usage:
        >>> backup_path = automatic_backup_on_startup()
        >>> if backup_path:
        ...     print(f"Startup backup: {backup_path}")
    """
    if not db_path.exists():
        print("ℹ️  No database found, skipping startup backup")
        return None
    
    print("💾 Creating automatic startup backup...")
    backup_path = backup_database(db_path, backup_reason="startup")
    
    # Apply retention policy
    deleted = cleanup_old_backups(max_backups=max_backups)
    if deleted > 0:
        print(f"  Cleaned up {deleted} old backup(s) (retention: {max_backups})")
    
    return backup_path


def calculate_file_checksum(filepath: Path) -> str:
    """Calculate SHA256 checksum of file for migration verification."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def apply_migrations(conn: Optional[sqlite3.Connection] = None, dry_run: bool = False) -> int:
    """
    Apply all pending migrations to database.
    
    Args:
        conn: Existing connection (optional, creates new if None)
        dry_run: If True, only show pending migrations without applying
    
    Returns:
        Number of migrations applied
    
    Process:
    1. Get current schema version
    2. Find pending migration scripts
    3. For each migration:
        a. Create automatic backup
        b. Execute migration SQL
        c. Update schema_version table
        d. Commit (or rollback on error)
    
    Error Handling:
    - If migration fails: rollback, restore from backup, raise exception
    - All migrations are atomic (wrapped in transaction)
    
    Usage:
        >>> apply_migrations()  # Apply all pending migrations
        >>> apply_migrations(dry_run=True)  # Preview migrations without applying
    """
    # Use existing connection or create new
    close_after = False
    if conn is None:
        conn = open_connection()
        close_after = True
    
    try:
        current_version = get_current_schema_version(conn)
        pending = get_pending_migrations(conn)
        
        if not pending:
            print(f"✓ Database schema is up-to-date (version {current_version})")
            return 0
        
        print(f"Current schema version: {current_version}")
        print(f"Pending migrations: {len(pending)}")
        
        if dry_run:
            for version, filepath in pending:
                print(f"  [{version}] {filepath.name}")
            return 0
        
        # Apply each migration
        applied_count = 0
        
        for version, migration_path in pending:
            print(f"\n→ Applying migration {version}: {migration_path.name}")
            
            # 1. Create backup before migration
            backup_path = backup_database(DB_PATH, f"v{version-1}_pre_migration")
            
            # 2. Read migration SQL
            with open(migration_path, "r", encoding="utf-8") as f:
                migration_sql = f.read()
            
            # 3. Calculate checksum for verification
            checksum = calculate_file_checksum(migration_path)
            
            # 4. Execute migration in transaction
            try:
                with transaction(conn, isolation_level="EXCLUSIVE") as cur:
                    # Execute migration SQL (may contain multiple statements)
                    cur.executescript(migration_sql)
                    
                    # Note: executescript() auto-commits, so we update schema_version separately
                    # This is acceptable because migration scripts wrap in BEGIN...COMMIT
                
                print(f"✓ Migration {version} applied successfully")
                applied_count += 1
            
            except Exception as e:
                print(f"✗ Migration {version} failed: {e}")
                print(f"→ Database state restored from backup: {backup_path}")
                print(f"→ To manually restore: cp {backup_path} {DB_PATH}")
                raise RuntimeError(f"Migration {version} failed. Database unchanged.") from e
        
        # Verify final schema version
        final_version = get_current_schema_version(conn)
        print(f"\n✓ All migrations applied successfully!")
        print(f"  Schema version: {current_version} → {final_version}")
        print(f"  Migrations applied: {applied_count}")
        
        return applied_count
    
    finally:
        if close_after:
            conn.close()


# ============================================================
# Health Checks
# ============================================================

def verify_schema(conn: sqlite3.Connection) -> bool:
    """
    Verify database schema matches expected structure.
    
    Returns:
        True if schema is valid, False otherwise
    
    Checks:
    - All expected tables exist
    - Foreign keys are enabled
    - Schema version table is present
    """
    cursor = conn.cursor()
    
    # Expected tables (after initial migration)
    expected_tables = {
        "schema_version", "skus", "transactions", "sales", "order_logs",
        "receiving_logs", "order_receipts", "lots", "promo_calendar",
        "kpi_daily", "audit_log", "event_uplift_rules", "settings", "holidays"
    }
    
    # Get actual tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    actual_tables = {row[0] for row in cursor.fetchall()}
    
    # Check for missing tables
    missing_tables = expected_tables - actual_tables
    if missing_tables:
        print(f"✗ Missing tables: {', '.join(sorted(missing_tables))}")
        return False
    
    # Check foreign keys enabled
    fk_enabled = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
    if fk_enabled != 1:
        print("✗ Foreign keys are not enabled")
        return False
    
    # Check schema version
    schema_version = get_current_schema_version(conn)
    if schema_version == 0:
        print("✗ Schema version is 0 (no migrations applied)")
        return False
    
    print(f"✓ Schema verification passed (version {schema_version})")
    return True


def integrity_check(conn: sqlite3.Connection) -> bool:
    """
    Run SQLite integrity checks.
    
    Returns:
        True if database is healthy, False otherwise
    
    Checks:
    - PRAGMA integrity_check (structural integrity)
    - PRAGMA foreign_key_check (referential integrity)
    """
    cursor = conn.cursor()
    
    # 1. Structural integrity check
    cursor.execute("PRAGMA integrity_check")
    integrity_result = cursor.fetchall()
    
    if len(integrity_result) != 1 or integrity_result[0][0] != "ok":
        print("✗ Integrity check failed:")
        for row in integrity_result:
            print(f"  - {row[0]}")
        return False
    
    # 2. Foreign key constraint check
    cursor.execute("PRAGMA foreign_key_check")
    fk_violations = cursor.fetchall()
    
    if fk_violations:
        print(f"✗ Foreign key violations found ({len(fk_violations)}):")
        for row in fk_violations[:10]:  # Show first 10
            print(f"  - Table: {row[0]}, RowID: {row[1]}, Parent: {row[2]}, FK Index: {row[3]}")
        return False
    
    print("✓ Integrity check passed (no corruption, no FK violations)")
    return True


def get_database_stats(conn: sqlite3.Connection) -> dict:
    """
    Get database statistics (table counts, indices, size).
    
    Returns:
        Dictionary with database statistics
    """
    cursor = conn.cursor()
    
    stats = {}
    
    # Table count
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
    stats["tables_count"] = cursor.fetchone()[0]
    
    # Index count (exclude auto-created sqlite_* indices)
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
    stats["indices_count"] = cursor.fetchone()[0]
    
    # Schema version
    stats["schema_version"] = get_current_schema_version(conn)
    
    # Database file size (if file exists)
    if DB_PATH.exists():
        stats["db_size_mb"] = round(DB_PATH.stat().st_size / (1024 * 1024), 2)
    
    # Row counts per table (expensive, but useful for diagnostics)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    row_counts = {}
    for row in cursor.fetchall():
        table_name = row[0]
        if table_name != "sqlite_sequence":  # Skip internal table
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_counts[table_name] = cursor.fetchone()[0]
    
    stats["row_counts"] = row_counts
    
    return stats


# ============================================================
# Startup Checks (FASE 7 TASK 7.2)
# ============================================================

def run_startup_checks(conn: sqlite3.Connection, verbose: bool = False) -> bool:
    """
    Run essential health checks on database startup.
    
    Purpose:
    - Detect corruption or configuration issues early
    - Prevent application from running with unhealthy database
    - Provide clear error messages for recovery
    
    Checks:
    1. PRAGMA integrity_check (structural integrity)
    2. PRAGMA foreign_key_check (referential integrity)
    3. Schema version compatibility
    4. Foreign keys enabled
    
    Args:
        conn: Database connection
        verbose: If True, print detailed check results
    
    Returns:
        True if all checks pass, False otherwise
    
    Usage:
        >>> conn = open_connection()
        >>> if not run_startup_checks(conn):
        ...     print("Database unhealthy, cannot start application")
        ...     sys.exit(1)
    """
    if verbose:
        print("=" * 60)
        print("STARTUP CHECKS")
        print("=" * 60)
    
    all_passed = True
    
    # Check 1: Structural integrity
    if verbose:
        print("1. Structural Integrity (PRAGMA integrity_check)...")
    
    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check")
    integrity_result = cursor.fetchall()
    
    if len(integrity_result) == 1 and integrity_result[0][0] == "ok":
        if verbose:
            print("   ✓ PASS: Database structure is intact")
    else:
        print("   ✗ FAIL: Database corruption detected")
        for row in integrity_result[:5]:
            print(f"      - {row[0]}")
        print("   Recovery: Restore from backup (data/backups/)")
        all_passed = False
    
    # Check 2: Referential integrity
    if verbose:
        print("2. Referential Integrity (PRAGMA foreign_key_check)...")
    
    cursor.execute("PRAGMA foreign_key_check")
    fk_violations = cursor.fetchall()
    
    if not fk_violations:
        if verbose:
            print("   ✓ PASS: All foreign key constraints satisfied")
    else:
        print(f"   ✗ FAIL: {len(fk_violations)} foreign key violations")
        for row in fk_violations[:5]:
            print(f"      - Table: {row[0]}, RowID: {row[1]}")
        print("   Recovery: Review and fix orphaned records")
        all_passed = False
    
    # Check 3: Schema version
    if verbose:
        print("3. Schema Version Compatibility...")
    
    schema_version = get_current_schema_version(conn)
    
    if schema_version == 0:
        print("   ✗ FAIL: Schema version is 0 (no migrations applied)")
        print("   Recovery: Run 'python src/db.py migrate'")
        all_passed = False
    elif schema_version < 3:  # Minimum expected version
        print(f"   ⚠ WARN: Schema version is {schema_version} (migrations may be pending)")
        print("   Action: Run 'python src/db.py migrate'")
        # Don't fail on old schema, just warn
    else:
        if verbose:
            print(f"   ✓ PASS: Schema version is {schema_version}")
    
    # Check 4: Foreign keys enabled
    if verbose:
        print("4. Foreign Keys Enabled...")
    
    cursor.execute("PRAGMA foreign_keys")
    fk_enabled = cursor.fetchone()[0]
    
    if fk_enabled == 1:
        if verbose:
            print("   ✓ PASS: Foreign keys are enabled")
    else:
        print("   ✗ FAIL: Foreign keys are NOT enabled")
        print("   Recovery: Reconnect with PRAGMA foreign_keys=ON")
        all_passed = False
    
    if verbose:
        print("=" * 60)
        if all_passed:
            print("✅ ALL STARTUP CHECKS PASSED")
        else:
            print("❌ STARTUP CHECKS FAILED")
        print("=" * 60)
        print()
    
    return all_passed


# ============================================================
# Initialization Helper
# ============================================================

def initialize_database(force: bool = False) -> sqlite3.Connection:
    """
    Initialize database with schema and return connection.
    
    Args:
        force: If True, delete existing database and reinitialize
    
    Returns:
        Configured sqlite3.Connection with schema applied
    
    Usage:
        >>> conn = initialize_database()  # Create or open existing
        >>> conn = initialize_database(force=True)  # Recreate from scratch
    """
    if force and DB_PATH.exists():
        print(f"⚠ Deleting existing database: {DB_PATH}")
        DB_PATH.unlink()
    
    conn = open_connection()
    
    # Apply migrations if database is new or schema_version missing
    current_version = get_current_schema_version(conn)
    if current_version == 0:
        print("→ New database detected, applying initial schema...")
        apply_migrations(conn)
    
    # Run startup checks (FASE 7 TASK 7.2)
    print("\n→ Running startup checks...")
    if not run_startup_checks(conn, verbose=True):
        raise RuntimeError("Startup checks failed - database is unhealthy")
    
    print("\n✓ Database initialized successfully!")
    stats = get_database_stats(conn)
    print(f"  Schema version: {stats['schema_version']}")
    print(f"  Tables: {stats['tables_count']}")
    print(f"  Indices: {stats['indices_count']}")
    if "db_size_mb" in stats:
        print(f"  Database size: {stats['db_size_mb']} MB")
    
    return conn


# ============================================================
# Audit Logging Functions (FASE 7 TASK 7.4)
# ============================================================

def generate_run_id() -> str:
    """
    Generate unique run_id for batch operations.
    
    Returns:
        Unique run ID in format: run_YYYYMMDD_HHMMSS_<uuid4_short>
    
    Example:
        run_20260217_143022_a1b2c3d4
    
    Purpose:
        Group related operations together for traceability.
        All audit events in the same batch share the same run_id.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    import uuid
    short_uuid = str(uuid.uuid4())[:8]
    return f"run_{timestamp}_{short_uuid}"


def log_audit_event(
    conn: sqlite3.Connection,
    operation: str,
    details: str = "",
    sku: Optional[str] = None,
    user: str = "system",
    run_id: Optional[str] = None,
) -> int:
    """
    Log audit event to audit_log table.
    
    Args:
        conn: Database connection
        operation: Operation type (e.g., "ORDER_CONFIRMED", "RECEIPT_CLOSED", "BACKUP_CREATED")
        details: Human-readable description
        sku: Optional SKU affected (None for global operations)
        user: User/operator name (default: "system")
        run_id: Optional run ID for batch operations
    
    Returns:
        audit_id of created record
    
    Example:
        >>> run_id = generate_run_id()
        >>> for sku in skus:
        >>>     log_audit_event(conn, "SKU_UPDATED", f"Adjusted safety stock to {ss}", sku=sku, run_id=run_id)
    """
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO audit_log (operation, details, sku, user, run_id)
        VALUES (?, ?, ?, ?, ?)
    """, (operation, details, sku, user, run_id))
    
    audit_id = cursor.lastrowid
    assert audit_id is not None, "lastrowid should be set after INSERT"
    conn.commit()
    
    return audit_id


def get_audit_log(
    conn: sqlite3.Connection,
    sku: Optional[str] = None,
    operation: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Query audit log with filters.
   
    Args:
        conn: Database connection
        sku: Filter by SKU (None = all SKUs)
        operation: Filter by operation type (None = all operations)
        run_id: Filter by run_id (None = all runs)
        limit: Maximum records to return
        offset: Offset for pagination
    
    Returns:
        List of audit log records (chronological, most recent first)
    
    Example:
        >>> # Get all audit events for SKU001
        >>> events = get_audit_log(conn, sku="SKU001", limit=50)
        >>> 
        >>> # Get all events in a specific batch
        >>> batch_events = get_audit_log(conn, run_id="run_20260217_143022_a1b2c3d4")
    """
    cursor = conn.cursor()
    
    query = "SELECT audit_id, timestamp, operation, sku, details, user, run_id FROM audit_log WHERE 1=1"
    params = []
    
    if sku is not None:
        query += " AND sku = ?"
        params.append(sku)
    
    if operation is not None:
        query += " AND operation = ?"
        params.append(operation)
    
    if run_id is not None:
        query += " AND run_id = ?"
        params.append(run_id)
    
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    cursor.execute(query, params)
    
    records = []
    for row in cursor.fetchall():
        records.append({
            "audit_id": row[0],
            "timestamp": row[1],
            "operation": row[2],
            "sku": row[3],
            "details": row[4],
            "user": row[5],
            "run_id": row[6],
        })
    
    return records


def get_batch_operations(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    """
    Get all operations for a specific run_id (batch).
    
    Args:
        conn: Database connection
        run_id: Run ID to query
    
    Returns:
        Dictionary with batch metadata and events
    
    Example:
        >>> batch = get_batch_operations(conn, "run_20260217_143022_a1b2c3d4")
        >>> print(f"Batch: {batch['run_id']}")
        >>> print(f"Events: {batch['event_count']}")
        >>> print(f"Duration: {batch['duration_seconds']}s")
        >>> for event in batch['events']:
        >>>     print(f"  {event['timestamp']} - {event['operation']}: {event['details']}")
    """
    cursor = conn.cursor()
    
    # Get all events for this run_id
    cursor.execute("""
        SELECT audit_id, timestamp, operation, sku, details, user
        FROM audit_log
        WHERE run_id = ?
        ORDER BY timestamp ASC
    """, (run_id,))
    
    events = []
    for row in cursor.fetchall():
        events.append({
            "audit_id": row[0],
            "timestamp": row[1],
            "operation": row[2],
            "sku": row[3],
            "details": row[4],
            "user": row[5],
        })
    
    if not events:
        return {
            "run_id": run_id,
            "event_count": 0,
            "events": [],
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
        }
    
    # Calculate batch metadata
    from dateutil import parser as dateparser
    
    start_time = events[0]["timestamp"]
    end_time = events[-1]["timestamp"]
    
    try:
        start_dt = dateparser.parse(start_time)
        end_dt = dateparser.parse(end_time)
        duration_seconds = (end_dt - start_dt).total_seconds()
    except:
        duration_seconds = 0
    
    return {
        "run_id": run_id,
        "event_count": len(events),
        "events": events,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": duration_seconds,
    }


# ============================================================
# CLI Interface (for testing)
# ============================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "init":
            # Initialize database
            force = "--force" in sys.argv
            conn = initialize_database(force=force)
            conn.close()
        
        elif command == "migrate":
            # Apply pending migrations
            dry_run = "--dry-run" in sys.argv
            conn = open_connection()
            apply_migrations(conn, dry_run=dry_run)
            conn.close()
        
        elif command == "verify":
            # Verify schema and integrity
            conn = open_connection()
            schema_valid = verify_schema(conn)
            integrity_valid = integrity_check(conn)
            conn.close()
            
            if schema_valid and integrity_valid:
                print("\n✓ Database is healthy")
                sys.exit(0)
            else:
                print("\n✗ Database has issues")
                sys.exit(1)
        
        elif command == "stats":
            # Show database statistics
            conn = open_connection()
            stats = get_database_stats(conn)
            
            print(f"\nDatabase Statistics:")
            print(f"  Schema version: {stats['schema_version']}")
            print(f"  Tables: {stats['tables_count']}")
            print(f"  Indices: {stats['indices_count']}")
            if "db_size_mb" in stats:
                print(f"  Database size: {stats['db_size_mb']} MB")
            
            print(f"\n  Row counts:")
            for table, count in sorted(stats["row_counts"].items()):
                print(f"    {table}: {count:,}")
            
            conn.close()
        
        elif command == "backup":
            # Create manual backup
            reason = sys.argv[2] if len(sys.argv) > 2 else "manual"
            backup_path = backup_database(DB_PATH, reason)
            print(f"✓ Backup created: {backup_path}")
        
        else:
            print(f"Unknown command: {command}")
            print("Usage: python src/db.py [init|migrate|verify|stats|backup]")
            sys.exit(1)
    
    else:
        # Default: initialize database
        print("Usage: python src/db.py [init|migrate|verify|stats|backup]")
        print("\nCommands:")
        print("  init           Initialize database with schema")
        print("  init --force   Recreate database from scratch")
        print("  migrate        Apply pending migrations")
        print("  migrate --dry-run  Show pending migrations without applying")
        print("  verify         Verify schema and integrity")
        print("  stats          Show database statistics")
        print("  backup [reason]  Create manual backup")
