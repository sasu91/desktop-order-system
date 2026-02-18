#!/usr/bin/env python3
"""
FASE 7 TASK 7.2 — Database Maintenance Tool (REINDEX & VACUUM)

Maintenance operations for optimizing database performance and reclaiming space.

⚠️  WARNING: These operations are blocking and can take significant time on large databases.
            Run during maintenance windows or when application is not in use.

Operations:
    - REINDEX: Rebuild all indices (fixes corruption, optimizes structure)
    - VACUUM: Reclaim unused space, defragment database file
    - ANALYZE: Update query optimizer statistics

Usage:
    python tools/db_reindex_vacuum.py reindex       # Rebuild all indices
    python tools/db_reindex_vacuum.py vacuum        # Reclaim space
    python tools/db_reindex_vacuum.py analyze       # Update stats
    python tools/db_reindex_vacuum.py full          # All operations (REINDEX + VACUUM + ANALYZE)
    python tools/db_reindex_vacuum.py --dry-run     # Show what would be done

Safety:
    - Automatic backup before operations
    - Integrity check before and after
    - Rollback on failure (restore from backup)

Performance Impact:
    - REINDEX: 1-5 seconds per index (blocks writes)
    - VACUUM: 2-10 seconds per 100MB (exclusive lock, blocks all access)
    - ANALYZE: 1-3 seconds (blocks writes briefly)
    
    Example: 100MB database with 30 indices = ~2 minutes total
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import sqlite3
import argparse
import time

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    open_connection,
    backup_database,
    integrity_check,
    get_database_stats,
    DB_PATH,
)


# ============================================================
# Maintenance Operations
# ============================================================

def reindex_database(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """
    Rebuild all indices in database.
    
    Purpose:
    - Fix index corruption
    - Optimize index structure
    - Improve query performance
    
    Side Effects:
    - Blocks write operations during rebuild
    - CPU and I/O intensive
    
    Returns:
        True if successful, False otherwise
    """
    cursor = conn.cursor()
    
    # Get list of indices
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='index' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    indices = [row[0] for row in cursor.fetchall()]
    
    print(f"📋 Found {len(indices)} indices to rebuild")
    
    if dry_run:
        for idx_name in indices:
            print(f"   Would rebuild: {idx_name}")
        return True
    
    print("🔧 Rebuilding indices...")
    start = time.time()
    
    try:
        # Option 1: Rebuild all at once
        cursor.execute("REINDEX")
        
        elapsed = time.time() - start
        print(f"✓ All indices rebuilt in {elapsed:.2f}s")
        return True
    
    except sqlite3.Error as e:
        print(f"✗ REINDEX failed: {e}")
        return False


def vacuum_database(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """
    Reclaim unused space and defragment database.
    
    Purpose:
    - Reclaim space from deleted rows
    - Defragment database file
    - Optimize page layout
    
    Side Effects:
    - Requires exclusive lock (blocks ALL access)
    - Requires free disk space ~= database size
    - Cannot be run inside transaction
    - CPU and I/O intensive
    
    Returns:
        True if successful, False otherwise
    """
    cursor = conn.cursor()
    
    # Get database size before
    cursor.execute("PRAGMA page_count")
    page_count_before = cursor.fetchone()[0]
    cursor.execute("PRAGMA page_size")
    page_size = cursor.fetchone()[0]
    size_before_mb = (page_count_before * page_size) / (1024 * 1024)
    
    cursor.execute("PRAGMA freelist_count")
    freelist_count = cursor.fetchone()[0]
    freelist_mb = (freelist_count * page_size) / (1024 * 1024)
    
    print(f"📊 Database size: {size_before_mb:.2f} MB")
    print(f"📊 Reclaimable space: {freelist_mb:.2f} MB ({freelist_count} pages)")
    
    if freelist_mb < 1.0:
        print("ℹ️  Less than 1 MB reclaimable, skipping VACUUM")
        return True
    
    if dry_run:
        print(f"   Would reclaim ~{freelist_mb:.2f} MB")
        return True
    
    print("🔧 Running VACUUM (this may take a while)...")
    start = time.time()
    
    try:
        # VACUUM requires special handling (cannot be in transaction)
        conn.isolation_level = None  # Autocommit mode
        cursor.execute("VACUUM")
        # isolation_level already None, no need to restore
        
        # Get size after
        cursor.execute("PRAGMA page_count")
        page_count_after = cursor.fetchone()[0]
        size_after_mb = (page_count_after * page_size) / (1024 * 1024)
        reclaimed_mb = size_before_mb - size_after_mb
        
        elapsed = time.time() - start
        print(f"✓ VACUUM completed in {elapsed:.2f}s")
        print(f"  Reclaimed: {reclaimed_mb:.2f} MB")
        print(f"  New size: {size_after_mb:.2f} MB")
        return True
    
    except sqlite3.Error as e:
        print(f"✗ VACUUM failed: {e}")
        return False


def analyze_database(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """
    Update query optimizer statistics.
    
    Purpose:
    - Update table/index statistics
    - Improve query planner decisions
    - Optimize query performance
    
    Side Effects:
    - Blocks writes briefly
    - Fast operation (1-3 seconds)
    
    Returns:
        True if successful, False otherwise
    """
    cursor = conn.cursor()
    
    if dry_run:
        print("   Would run: ANALYZE")
        return True
    
    print("🔧 Running ANALYZE...")
    start = time.time()
    
    try:
        cursor.execute("ANALYZE")
        conn.commit()
        
        elapsed = time.time() - start
        print(f"✓ ANALYZE completed in {elapsed:.2f}s")
        return True
    
    except sqlite3.Error as e:
        print(f"✗ ANALYZE failed: {e}")
        return False


def checkpoint_wal(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """
    Checkpoint WAL file (merge to main database).
    
    Purpose:
    - Merge WAL changes to main DB
    - Truncate WAL file
    - Reduce WAL file size
    
    Returns:
        True if successful, False otherwise
    """
    cursor = conn.cursor()
    
    # Check WAL size
    wal_path = Path(str(DB_PATH) + "-wal")
    if wal_path.exists():
        wal_size_mb = wal_path.stat().st_size / (1024 * 1024)
        print(f"📊 WAL file size: {wal_size_mb:.2f} MB")
    else:
        print("ℹ️  No WAL file found (already checkpointed or journal mode not WAL)")
        return True
    
    if dry_run:
        print(f"   Would checkpoint WAL")
        return True
    
    print("🔧 Checkpointing WAL...")
    start = time.time()
    
    try:
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result = cursor.fetchone()
        # result = (busy, log_pages, checkpointed_pages)
        
        elapsed = time.time() - start
        print(f"✓ WAL checkpoint completed in {elapsed:.2f}s")
        
        if wal_path.exists():
            wal_size_mb_after = wal_path.stat().st_size / (1024 * 1024)
            print(f"  WAL size after: {wal_size_mb_after:.2f} MB")
        
        return True
    
    except sqlite3.Error as e:
        print(f"✗ WAL checkpoint failed: {e}")
        return False


# ============================================================
# Main Orchestration
# ============================================================

def run_maintenance(operation: str, db_path: Path = DB_PATH, dry_run: bool = False, skip_backup: bool = False) -> int:
    """
    Run maintenance operation with safety checks.
    
    Returns:
        0 = Success
        1 = Failure
    """
    print("=" * 80)
    print("DATABASE MAINTENANCE TOOL")
    print("=" * 80)
    print(f"Database: {db_path}")
    print(f"Operation: {operation.upper()}")
    print(f"Dry run: {dry_run}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()
    
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return 1
    
    # Open connection
    try:
        conn = open_connection(db_path, track_connection=False)
    except Exception as e:
        print(f"❌ Failed to open database: {e}")
        return 1
    
    # Pre-check: integrity
    print("🔍 Pre-check: Verifying database integrity...")
    if not integrity_check(conn):
        print("❌ Database integrity check failed. Aborting maintenance.")
        print("   Action: Restore from backup or run tools/db_check.py for details")
        conn.close()
        return 1
    print()
    
    # Backup (unless dry-run or skip)
    backup_path = None
    if not dry_run and not skip_backup:
        print("💾 Creating backup before maintenance...")
        try:
            backup_path = backup_database(db_path, f"pre_{operation}")
            print()
        except Exception as e:
            print(f"❌ Backup failed: {e}")
            print("   Aborting maintenance (safety first)")
            conn.close()
            return 1
    
    # Run operation
    success = False
    
    try:
        if operation == "reindex":
            success = reindex_database(conn, dry_run)
        
        elif operation == "vacuum":
            success = vacuum_database(conn, dry_run)
        
        elif operation == "analyze":
            success = analyze_database(conn, dry_run)
        
        elif operation == "checkpoint":
            success = checkpoint_wal(conn, dry_run)
        
        elif operation == "full":
            print("🔧 Running FULL maintenance (REINDEX + VACUUM + ANALYZE + CHECKPOINT)")
            print()
            success = (
                checkpoint_wal(conn, dry_run) and
                reindex_database(conn, dry_run) and
                analyze_database(conn, dry_run) and
                vacuum_database(conn, dry_run)
            )
        
        else:
            print(f"❌ Unknown operation: {operation}")
            conn.close()
            return 1
    
    except Exception as e:
        print(f"❌ Unexpected error during {operation}: {e}")
        import traceback
        traceback.print_exc()
        success = False
    
    print()
    
    # Post-check: integrity (if not dry-run)
    if not dry_run and success:
        print("🔍 Post-check: Verifying database integrity...")
        if not integrity_check(conn):
            print("❌ Database integrity check failed after maintenance!")
            print(f"   Restoring from backup: {backup_path}")
            conn.close()
            
            if backup_path:
                import shutil
                shutil.copy2(backup_path, db_path)
                print("✓ Backup restored")
            
            return 1
        print()
    
    conn.close()
    
    # Final report
    print("=" * 80)
    if dry_run:
        print("✓ DRY RUN COMPLETE (no changes made)")
    elif success:
        print("✅ MAINTENANCE COMPLETE")
        if backup_path:
            print(f"   Backup: {backup_path}")
    else:
        print("❌ MAINTENANCE FAILED")
        if backup_path:
            print(f"   Restore from backup: cp {backup_path} {db_path}")
    print("=" * 80)
    
    return 0 if success else 1


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Database maintenance tool (REINDEX, VACUUM, ANALYZE)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "operation",
        choices=["reindex", "vacuum", "analyze", "checkpoint", "full"],
        help="Maintenance operation to perform"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--skip-backup", action="store_true", help="Skip backup (NOT RECOMMENDED)")
    parser.add_argument("--db", type=str, help=f"Database path (default: {DB_PATH})")
    
    args = parser.parse_args()
    
    # Use custom DB path if provided
    db_path = Path(args.db) if args.db else DB_PATH
    
    # Run maintenance
    exit_code = run_maintenance(
        args.operation,
        db_path=db_path,
        dry_run=args.dry_run,
        skip_backup=args.skip_backup
    )
    
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
