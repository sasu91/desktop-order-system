#!/usr/bin/env python3
"""
FASE 7 TASK 7.4 — Export Debug Bundle

Export comprehensive diagnostic bundle for troubleshooting.

Purpose:
- Creates self-contained diagnostic package with all relevant data
- Useful for debugging production issues without accessing production system
- Can be shared with support/development team

Bundle Contents:
- Database backup (.db file + WAL/SHM if exists)
- Audit log export (CSV)
- Database statistics (row counts, schema version, indices)
- System information (Python version, SQLite version, OS)
- Settings file (if exists)
- README with instructions

Usage:
    # Export debug bundle
    python tools/export_debug_bundle.py

    # Export to custom location
    python tools/export_debug_bundle.py --output /path/to/output/

    # Include last N audit records
    python tools/export_debug_bundle.py --audit-limit 1000

    # Compress to ZIP
    python tools/export_debug_bundle.py --compress
"""

import sys
import os
import shutil
from pathlib import Path
from datetime import datetime
import sqlite3
import platform
import json
import csv
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    open_connection,
    get_database_stats,
    backup_database,
    integrity_check,
    get_audit_log,
    DB_PATH,
    SETTINGS_FILE,
)


# ============================================================
# Bundle Creation
# ============================================================

def export_audit_log_to_csv(conn: sqlite3.Connection, output_file: Path, limit: int = 1000):
    """Export audit log to CSV file."""
    records = get_audit_log(conn, limit=limit)
    
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        if records:
            fieldnames = records[0].keys()
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
    
    return len(records)


def collect_system_info() -> dict:
    """Collect system information for diagnostics."""
    import sqlite3
    
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "compiler": platform.python_compiler(),
        },
        "sqlite": {
            "version": sqlite3.sqlite_version,
            "version_info": sqlite3.sqlite_version_info,
        },
        "timestamp": datetime.now().isoformat(),
    }


def create_debug_readme(bundle_dir: Path, manifest: dict):
    """Create README with bundle instructions."""
    readme_content = f"""# Debug Bundle - README

**Created**: {manifest['created_at']}
**Database**: {manifest['database_path']}
**Bundle ID**: {manifest['bundle_id']}

## Contents

This debug bundle contains a complete diagnostic snapshot of the system at the time of export.

### Files Included:

1. **database_backup.db** - Full database backup (use this to restore on test system)
2. **audit_log.csv** - Last {manifest['audit_records_count']} audit log records
3. **database_stats.json** - Database statistics (row counts, schema version)
4. **system_info.json** - System information (Python, SQLite, OS versions)
5. **settings.json** - Application settings (if available)
6. **manifest.json** - Bundle metadata
7. **README.txt** - This file

### How to Use This Bundle

#### Option 1: Restore Database on Test System

```bash
# Copy database_backup.db to test system
cp database_backup.db /path/to/test/data/app.db

# Run application
python main.py
```

#### Option 2: Inspect Data Without Running Application

```bash
# Open database with SQLite CLI
sqlite3 database_backup.db

# Run queries
SELECT COUNT(*) FROM skus;
SELECT * FROM order_logs WHERE status = 'PENDING' LIMIT 10;
.quit
```

#### Option 3: Review Audit Log

```bash
# Open audit_log.csv in Excel or any CSV viewer
# Filter by SKU, operation, or run_id to trace specific operations
```

### Troubleshooting Checklist

1. **Check database stats** (database_stats.json):
   - Schema version: Should match expected version
   - Row counts: Are they reasonable?
   - Database size: Is it within expected range?

2. **Check integrity** (if issues suspected):
   ```bash
   python src/db.py verify
   ```

3. **Review recent audit log** (audit_log.csv):
   - Look for ERROR or WARNING operations
   - Check for unexpected batch operations (same run_id)
   - Verify timestamps match expected activity

4. **Check system info** (system_info.json):
   - Python version: Compatible with requirements?
   - SQLite version: All features supported?
   - OS: Any platform-specific issues?

### Common Issues

**Issue**: Database locked errors
**Solution**: Check for other processes holding database. Run with single-writer pattern.

**Issue**: Missing data after restore
**Solution**: Check audit_log.csv for deletion events. May need older backup.

**Issue**: Slow queries
**Solution**: Check database_stats.json for missing indices. Run ANALYZE.

### Support Contact

If bundle doesn't resolve issue, share entire bundle with support team.

**Bundle ID**: {manifest['bundle_id']}  
**Created**: {manifest['created_at']}

---
Generated by FASE 7 TASK 7.4 — Debug Bundle Export Tool
"""
    
    with open(bundle_dir / "README.txt", "w", encoding="utf-8") as f:
        f.write(readme_content)


def export_debug_bundle(
    db_path: Path = DB_PATH,
    output_dir: Optional[Path] = None,
    audit_limit: int = 1000,
    compress: bool = False,
) -> Path:
    """
    Export comprehensive debug bundle.
    
    Args:
        db_path: Path to database
        output_dir: Output directory (default: data/debug_bundles/)
        audit_limit: Maximum audit records to include
        compress: If True, create ZIP file
    
    Returns:
        Path to bundle directory (or ZIP file if compress=True)
    """
    print("=" * 80)
    print("EXPORT DEBUG BUNDLE")
    print("=" * 80)
    print(f"Database: {db_path}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()
    
    # Create output directory
    if output_dir is None:
        output_dir = Path("data/debug_bundles")
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create bundle directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_id = f"debug_bundle_{timestamp}"
    bundle_dir = output_dir / bundle_id
    bundle_dir.mkdir(exist_ok=True)
    
    print(f"📦 Creating bundle: {bundle_dir}")
    print()
    
    manifest = {
        "bundle_id": bundle_id,
        "created_at": datetime.now().isoformat(),
        "database_path": str(db_path),
        "audit_records_count": audit_limit,
    }
    
    # 1. Database backup
    print("1️⃣  Backing up database...")
    if db_path.exists():
        db_backup_path = bundle_dir / "database_backup.db"
        shutil.copy2(db_path, db_backup_path)
        
        # Copy WAL if exists
        wal_path = Path(str(db_path) + "-wal")
        if wal_path.exists():
            shutil.copy2(wal_path, bundle_dir / "database_backup.db-wal")
        
        # Copy SHM if exists
        shm_path = Path(str(db_path) + "-shm")
        if shm_path.exists():
            shutil.copy2(shm_path, bundle_dir / "database_backup.db-shm")
        
        manifest["database_size_mb"] = db_path.stat().st_size / (1024 * 1024)
        print(f"   ✓ Database backed up ({manifest['database_size_mb']:.2f} MB)")
    else:
        print("   ⚠ Database not found (first run?)")
        manifest["database_size_mb"] = 0
    
    print()
    
    # 2. Database statistics
    print("2️⃣  Collecting database statistics...")
    try:
        conn = open_connection(db_path, track_connection=False)
        stats = get_database_stats(conn)
        
        with open(bundle_dir / "database_stats.json", "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
        
        manifest["schema_version"] = stats.get("schema_version", 0)
        manifest["tables_count"] = stats.get("tables_count", 0)
        manifest["total_rows"] = sum(stats.get("row_counts", {}).values())
        
        print(f"   ✓ Schema version: {manifest['schema_version']}")
        print(f"   ✓ Tables: {manifest['tables_count']}")
        print(f"   ✓ Total rows: {manifest['total_rows']:,}")
    except Exception as e:
        print(f"   ⚠ Could not collect stats: {e}")
        manifest["schema_version"] = None
        conn = None
    
    print()
    
    # 3. Audit log export
    print("3️⃣  Exporting audit log...")
    if conn:
        try:
            audit_csv = bundle_dir / "audit_log.csv"
            record_count = export_audit_log_to_csv(conn, audit_csv, limit=audit_limit)
            manifest["audit_records_exported"] = record_count
            print(f"   ✓ Exported {record_count:,} audit records")
        except Exception as e:
            print(f"   ⚠ Could not export audit log: {e}")
            manifest["audit_records_exported"] = 0
    else:
        print("   ⚠ No database connection")
    
    print()
    
    # 4. System information
    print("4️⃣  Collecting system information...")
    try:
        system_info = collect_system_info()
        with open(bundle_dir / "system_info.json", "w", encoding="utf-8") as f:
            json.dump(system_info, f, indent=2)
        
        manifest["python_version"] = system_info["python"]["version"]
        manifest["sqlite_version"] = system_info["sqlite"]["version"]
        manifest["platform"] = system_info["platform"]["system"]
        
        print(f"   ✓ Python {manifest['python_version']}")
        print(f"   ✓ SQLite {manifest['sqlite_version']}")
        print(f"   ✓ Platform: {manifest['platform']}")
    except Exception as e:
        print(f"   ⚠ Could not collect system info: {e}")
    
    print()
    
    # 5. Settings file
    print("5️⃣  Copying settings...")
    if SETTINGS_FILE.exists():
        try:
            shutil.copy2(SETTINGS_FILE, bundle_dir / "settings.json")
            print(f"   ✓ Settings copied")
            manifest["settings_included"] = True
        except Exception as e:
            print(f"   ⚠ Could not copy settings: {e}")
            manifest["settings_included"] = False
    else:
        print("   ℹ No settings file found")
        manifest["settings_included"] = False
    
    print()
    
    # 6. Create manifest
    print("6️⃣  Creating manifest...")
    with open(bundle_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print("   ✓ Manifest created")
    
    print()
    
    # 7. Create README
    print("7️⃣  Creating README...")
    create_debug_readme(bundle_dir, manifest)
    print("   ✓ README created")
    
    print()
    
    # Close connection
    if conn:
        conn.close()
    
    # Optional compression
    if compress:
        print("8️⃣  Compressing bundle...")
        import zipfile
        
        zip_path = output_dir / f"{bundle_id}.zip"
        
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in bundle_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(bundle_dir.parent)
                    zf.write(file_path, arcname)
        
        # Remove uncompressed directory
        shutil.rmtree(bundle_dir)
        
        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"   ✓ Compressed to {zip_size_mb:.2f} MB")
        print()
        
        print("=" * 80)
        print("✅ DEBUG BUNDLE COMPLETE (COMPRESSED)")
        print(f"   Location: {zip_path}")
        print(f"   Size: {zip_size_mb:.2f} MB")
        print("=" * 80)
        
        return zip_path
    
    else:
        bundle_size_mb = sum(f.stat().st_size for f in bundle_dir.rglob("*") if f.is_file()) / (1024 * 1024)
        
        print("=" * 80)
        print("✅ DEBUG BUNDLE COMPLETE")
        print(f"   Location: {bundle_dir}")
        print(f"   Files: {len(list(bundle_dir.rglob('*')))} files")
        print(f"   Size: {bundle_size_mb:.2f} MB")
        print("=" * 80)
        
        return bundle_dir


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Export debug bundle for troubleshooting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--db", type=str, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--output", type=str, help="Output directory (default: data/debug_bundles/)")
    parser.add_argument("--audit-limit", type=int, default=1000, help="Max audit records to include (default: 1000)")
    parser.add_argument("--compress", action="store_true", help="Compress bundle to ZIP")
    
    args = parser.parse_args()
    
    db_path = Path(args.db) if args.db else DB_PATH
    output_dir = Path(args.output) if args.output else Path("data/debug_bundles")
    
    bundle_path = export_debug_bundle(
        db_path=db_path,
        output_dir=output_dir,
        audit_limit=args.audit_limit,
        compress=args.compress,
    )
    
    print()
    print("💡 Next steps:")
    if args.compress:
        print(f"   1. Share {bundle_path.name} with support team")
        print(f"   2. Or extract locally: unzip {bundle_path.name}")
    else:
        print(f"   1. Review README.txt in {bundle_path.name}/")
        print(f"   2. Inspect files for diagnostics")
        print(f"   3. Or compress for sharing: --compress")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
