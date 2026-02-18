#!/usr/bin/env python3
"""
FASE 7 TASK 7.3 — Restore Database from Backup

Restore database from backup file with safety checks and user confirmation.

Purpose:
- Restore database from binary backup (.db file)
- Safety: Creates backup before restore (can rollback)
- Explicit user confirmation required
- Validates backup integrity before restore
- Supports WAL mode backups (.db + .db-wal + .db-shm)

Usage:
    # List available backups
    python tools/restore_backup.py --list

    # Restore from specific backup (with confirmation)
    python tools/restore_backup.py data/backups/app_20260217_143000_startup.db

    # Restore without confirmation (DANGEROUS - for scripts only)
    python tools/restore_backup.py <backup_file> --force

    # Dry-run (show what would be done)
    python tools/restore_backup.py <backup_file> --dry-run

Safety Features:
    - Creates safety backup before restore (can rollback)
    - Validates backup integrity (PRAGMA integrity_check)
    - Requires explicit --yes confirmation (or interactive prompt)
    - Shows diff summary (what will change)
    - Rollback on failure

Workflow:
    1. List backups or select backup file
    2. Validate backup integrity
    3. Create safety backup of current database
    4. Show what will change (row counts diff)
    5. Request user confirmation
    6. Restore backup (copy files)
    7. Verify restored database
    8. Success or rollback
"""

import sys
import os
import shutil
from pathlib import Path
from datetime import datetime
import sqlite3
import argparse

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    open_connection,
    get_current_schema_version,
    get_database_stats,
    integrity_check,
    backup_database,
    DB_PATH,
    BACKUP_DIR,
)


# ============================================================
# Backup Discovery
# ============================================================

def list_available_backups(backup_dir: Path = BACKUP_DIR) -> list:
    """
    List all available backups with metadata.
    
    Returns:
        List of tuples: (backup_path, manifest_data)
    """
    if not backup_dir.exists():
        return []
    
    backups = []
    
    # Find all backup files (*.db, excluding -wal and -shm)
    for backup_file in sorted(backup_dir.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True):
        if backup_file.name.endswith(("-wal", "-shm")):
            continue
        
        # Parse filename: app_YYYYMMDD_HHMMSS_reason.db
        parts = backup_file.stem.split("_")
        
        manifest = {
            "path": backup_file,
            "size_mb": backup_file.stat().st_size / (1024 * 1024),
            "created": datetime.fromtimestamp(backup_file.stat().st_mtime),
        }
        
        # Extract reason (everything after timestamp)
        if len(parts) >= 4:
            manifest["reason"] = "_".join(parts[3:])
        else:
            manifest["reason"] = "unknown"
        
        # Check for associated files
        wal_file = Path(str(backup_file) + "-wal")
        shm_file = Path(str(backup_file) + "-shm")
        manifest_file = Path(str(backup_file) + ".manifest")
        
        manifest["has_wal"] = wal_file.exists()
        manifest["has_shm"] = shm_file.exists()
        manifest["has_manifest"] = manifest_file.exists()
        
        # Read manifest if exists
        if manifest["has_manifest"]:
            try:
                with open(manifest_file, "r") as f:
                    lines = [l.strip() for l in f.readlines() if not l.startswith("#")]
                    manifest["files"] = [l for l in lines if l]
            except:
                pass
        
        backups.append(manifest)
    
    return backups


def print_backups_table(backups: list):
    """Print backups in formatted table."""
    if not backups:
        print("No backups found.")
        return
    
    print("=" * 100)
    print("AVAILABLE BACKUPS")
    print("=" * 100)
    print(f"{'#':<4} {'Date/Time':<20} {'Reason':<20} {'Size (MB)':<12} {'WAL':<6} {'Path'}")
    print("-" * 100)
    
    for i, backup in enumerate(backups, 1):
        date_str = backup["created"].strftime("%Y-%m-%d %H:%M:%S")
        reason = backup["reason"][:18] + ".." if len(backup["reason"]) > 20 else backup["reason"]
        size_str = f"{backup['size_mb']:.2f}"
        wal_str = "Yes" if backup["has_wal"] else "No"
        path_str = backup["path"].name
        
        print(f"{i:<4} {date_str:<20} {reason:<20} {size_str:<12} {wal_str:<6} {path_str}")
    
    print("=" * 100)
    print(f"Total: {len(backups)} backups")
    print()


# ============================================================
# Validation
# ============================================================

def validate_backup(backup_path: Path) -> bool:
    """
    Validate backup integrity.
    
    Args:
        backup_path: Path to backup file
    
    Returns:
        True if backup is valid, False otherwise
    """
    print("🔍 Validating backup integrity...")
    
    if not backup_path.exists():
        print(f"   ✗ Backup file not found: {backup_path}")
        return False
    
    try:
        # Try to open and check integrity
        conn = sqlite3.connect(str(backup_path))
        cursor = conn.cursor()
        
        # Check integrity
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchall()
        
        if len(result) == 1 and result[0][0] == "ok":
            print("   ✓ Backup integrity OK")
        else:
            print(f"   ✗ Backup is corrupted: {result}")
            conn.close()
            return False
        
        # Get schema version
        try:
            cursor.execute("SELECT MAX(version) FROM schema_version")
            version = cursor.fetchone()[0]
            print(f"   ✓ Schema version: {version}")
        except:
            print("   ⚠ Warning: Could not determine schema version")
        
        # Get row counts
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cursor.fetchall()]
        
        total_rows = 0
        for table in tables[:5]:  # First 5 tables
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            total_rows += count
        
        print(f"   ✓ Found {len(tables)} tables, {total_rows:,}+ rows")
        
        conn.close()
        return True
    
    except Exception as e:
        print(f"   ✗ Validation failed: {e}")
        return False


def get_database_diff(current_db: Path, backup_db: Path) -> dict:
    """
    Get difference between current database and backup.
    
    Returns:
        Dictionary with diff information
    """
    diff = {
        "current_exists": current_db.exists(),
        "backup_exists": backup_db.exists(),
        "tables": {}
    }
    
    if not diff["current_exists"] or not diff["backup_exists"]:
        return diff
    
    try:
        # Open both databases
        current_conn = sqlite3.connect(str(current_db))
        backup_conn = sqlite3.connect(str(backup_db))
        
        # Get current schema version
        try:
            cursor = current_conn.cursor()
            cursor.execute("SELECT MAX(version) FROM schema_version")
            diff["current_schema_version"] = cursor.fetchone()[0]
        except:
            diff["current_schema_version"] = 0
        
        # Get backup schema version
        try:
            cursor = backup_conn.cursor()
            cursor.execute("SELECT MAX(version) FROM schema_version")
            diff["backup_schema_version"] = cursor.fetchone()[0]
        except:
            diff["backup_schema_version"] = 0
        
        # Get tables from current
        cursor = current_conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        current_tables = {row[0] for row in cursor.fetchall()}
        
        # Get tables from backup
        cursor = backup_conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        backup_tables = {row[0] for row in cursor.fetchall()}
        
        # Compare row counts
        for table in current_tables & backup_tables:
            cursor = current_conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            current_count = cursor.fetchone()[0]
            
            cursor = backup_conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            backup_count = cursor.fetchone()[0]
            
            diff["tables"][table] = {
                "current_rows": current_count,
                "backup_rows": backup_count,
                "diff": backup_count - current_count
            }
        
        # Tables only in current
        for table in current_tables - backup_tables:
            cursor = current_conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            diff["tables"][table] = {
                "current_rows": cursor.fetchone()[0],
                "backup_rows": 0,
                "diff": -cursor.fetchone()[0],
                "status": "will_be_removed"
            }
        
        # Tables only in backup
        for table in backup_tables - current_tables:
            cursor = backup_conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            diff["tables"][table] = {
                "current_rows": 0,
                "backup_rows": cursor.fetchone()[0],
                "diff": cursor.fetchone()[0],
                "status": "will_be_added"
            }
        
        current_conn.close()
        backup_conn.close()
    
    except Exception as e:
        diff["error"] = str(e)
    
    return diff


def print_diff_summary(diff: dict):
    """Print diff summary."""
    print()
    print("=" * 80)
    print("RESTORE PREVIEW (What Will Change)")
    print("=" * 80)
    
    if not diff["current_exists"]:
        print("⚠ Current database does not exist (will be created)")
        return
    
    print(f"Schema Version: {diff.get('current_schema_version', '?')} → {diff.get('backup_schema_version', '?')}")
    print()
    
    if not diff["tables"]:
        print("(No table information available)")
        return
    
    print(f"{'Table':<30} {'Current Rows':<15} {'Backup Rows':<15} {'Difference'}")
    print("-" * 80)
    
    for table, info in sorted(diff["tables"].items()):
        current_str = f"{info['current_rows']:,}"
        backup_str = f"{info['backup_rows']:,}"
        diff_val = info['diff']
        diff_str = f"{diff_val:+,}" if diff_val != 0 else "0"
        
        if diff_val > 0:
            diff_str = f"⬆ {diff_str}"
        elif diff_val < 0:
            diff_str = f"⬇ {diff_str}"
        
        print(f"{table:<30} {current_str:<15} {backup_str:<15} {diff_str}")
    
    print("=" * 80)


# ============================================================
# Restore Operations
# ============================================================

def restore_backup(
    backup_path: Path,
    target_path: Path = DB_PATH,
    dry_run: bool = False,
    force: bool = False
) -> bool:
    """
    Restore database from backup.
    
    Args:
        backup_path: Path to backup file
        target_path: Path to target database (default: data/app.db)
        dry_run: If True, don't actually restore
        force: If True, skip confirmation
    
    Returns:
        True if restore successful, False otherwise
    """
    print("=" * 80)
    print("RESTORE DATABASE FROM BACKUP")
    print("=" * 80)
    print(f"Backup: {backup_path}")
    print(f"Target: {target_path}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()
    
    # Validate backup
    if not validate_backup(backup_path):
        print("❌ Backup validation failed. Aborting.")
        return False
    
    print()
    
    # Get diff
    diff = get_database_diff(target_path, backup_path)
    print_diff_summary(diff)
    
    print()
    
    if dry_run:
        print("🔍 DRY RUN: No changes will be made.")
        return True
    
    # Request confirmation
    if not force:
        print("⚠️  WARNING: This will replace the current database with the backup.")
        print("   A safety backup of the current database will be created first.")
        print()
        response = input("Continue with restore? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("❌ Restore cancelled by user.")
            return False
        print()
    
    # Create safety backup
    if target_path.exists():
        print("💾 Creating safety backup of current database...")
        try:
            safety_backup = backup_database(target_path, backup_reason="pre_restore")
            print(f"   Safety backup: {safety_backup}")
            print()
        except Exception as e:
            print(f"❌ Failed to create safety backup: {e}")
            return False
    
    # Restore backup
    print("🔧 Restoring from backup...")
    
    try:
        # Copy main database file
        shutil.copy2(backup_path, target_path)
        print(f"   ✓ Copied {backup_path.name}")
        
        # Copy WAL file if exists
        wal_backup = Path(str(backup_path) + "-wal")
        if wal_backup.exists():
            wal_target = Path(str(target_path) + "-wal")
            shutil.copy2(wal_backup, wal_target)
            print(f"   ✓ Copied {wal_backup.name}")
        
        # Copy SHM file if exists
        shm_backup = Path(str(backup_path) + "-shm")
        if shm_backup.exists():
            shm_target = Path(str(target_path) + "-shm")
            shutil.copy2(shm_backup, shm_target)
            print(f"   ✓ Copied {shm_backup.name}")
        
        print()
        
        # Verify restored database
        print("🔍 Verifying restored database...")
        conn = open_connection(target_path, track_connection=False)
        
        if not integrity_check(conn):
            print("❌ Restored database failed integrity check!")
            print("   Rolling back to safety backup...")
            if "safety_backup" in locals():
                shutil.copy2(safety_backup, target_path)
                print("   ✓ Rollback complete")
            conn.close()
            return False
        
        conn.close()
        print("   ✓ Integrity check passed")
        print()
        
        print("=" * 80)
        print("✅ RESTORE COMPLETE")
        print(f"   Database restored from: {backup_path.name}")
        if "safety_backup" in locals():
            print(f"   Safety backup saved: {safety_backup}")
        print("=" * 80)
        
        return True
    
    except Exception as e:
        print(f"❌ Restore failed: {e}")
        
        # Attempt rollback
        if "safety_backup" in locals() and safety_backup.exists():
            print("   Attempting rollback...")
            try:
                shutil.copy2(safety_backup, target_path)
                print("   ✓ Rollback successful")
            except Exception as rollback_error:
                print(f"   ✗ Rollback failed: {rollback_error}")
        
        return False


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Restore database from backup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("backup", nargs="?", help="Path to backup file (or use --list)")
    parser.add_argument("--list", action="store_true", help="List available backups")
    parser.add_argument("--target", type=str, help=f"Target database path (default: {DB_PATH})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without restoring")
    parser.add_argument("--force", action="store_true", help="Skip confirmation (DANGEROUS)")
    
    args = parser.parse_args()
    
    # List backups
    if args.list:
        backups = list_available_backups()
        print_backups_table(backups)
        
        if backups:
            print("\nTo restore a backup:")
            print(f"  python {sys.argv[0]} <backup_file>")
        
        return 0
    
    # Restore backup
    if not args.backup:
        print("Error: No backup file specified. Use --list to see available backups.")
        print(f"Usage: python {sys.argv[0]} <backup_file>")
        return 1
    
    backup_path = Path(args.backup)
    if not backup_path.exists():
        print(f"❌ Backup file not found: {backup_path}")
        return 1
    
    target_path = Path(args.target) if args.target else DB_PATH
    
    success = restore_backup(
        backup_path=backup_path,
        target_path=target_path,
        dry_run=args.dry_run,
        force=args.force
    )
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
