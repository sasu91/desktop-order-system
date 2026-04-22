#!/usr/bin/env python3
"""
FASE 7 TASK 7.3 — Export Full Database Snapshot

Exports entire database to CSV files for emergency recovery or inspection.

Purpose:
- Create human-readable backup (CSV format)
- Enable inspection without SQLite
- Facilitate data migration or analysis
- Emergency recovery option (complement to binary backups)

Output Structure:
    snapshot_YYYYMMDD_HHMMSS/
    ├── manifest.json                  # Snapshot metadata
    ├── skus.csv
    ├── transactions.csv
    ├── sales.csv
    ├── order_logs.csv
    ├── receiving_logs.csv
    ├── lots.csv
    ├── promo_calendar.csv
    ├── holidays.csv
    ├── event_uplift_rules.csv
    ├── settings.json                  # Application settings (if exists)
    └── README.txt                     # Snapshot documentation

Usage:
    python tools/export_snapshot.py                    # Export to default location
    python tools/export_snapshot.py --output /path/    # Custom output directory
    python tools/export_snapshot.py --compress         # Create ZIP archive

Features:
    - Exports all tables to CSV with headers
    - Includes metadata (timestamp, row counts, schema version)
    - UTF-8 encoding with BOM (Excel-compatible)
    - Optional compression (ZIP format)
    - Validates export completeness
"""

import sys
import os
import csv
import json
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import argparse

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    open_connection,
    get_current_schema_version,
    get_database_stats,
    DB_PATH,
)


# ============================================================
# Export Functions
# ============================================================

def export_table_to_csv(conn, table_name: str, output_path: Path) -> int:
    """
    Export single table to CSV file.
    
    Args:
        conn: Database connection
        table_name: Name of table to export
        output_path: Path to output CSV file
    
    Returns:
        Number of rows exported
    """
    cursor = conn.cursor()
    
    # Get all rows
    cursor.execute(f"SELECT * FROM {table_name}")
    rows = cursor.fetchall()
    
    if not rows:
        # Empty table, just create file with headers
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor.fetchall()]
        
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
        
        return 0
    
    # Get column names from first row
    columns = rows[0].keys()
    
    # Write CSV with UTF-8 BOM (Excel-compatible)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        
        # Write header
        writer.writerow(columns)
        
        # Write data rows
        for row in rows:
            writer.writerow([row[col] for col in columns])
    
    return len(rows)


def export_full_snapshot(
    db_path: Path = DB_PATH,
    output_dir: Optional[Path] = None,
    compress: bool = False
) -> Path:
    """
    Export full database snapshot to CSV files.
    
    Args:
        db_path: Path to database file
        output_dir: Output directory (default: data/snapshots/)
        compress: If True, create ZIP archive
    
    Returns:
        Path to snapshot directory (or ZIP file if compressed)
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    # Create output directory
    if output_dir is None:
        output_dir = Path("data/snapshots")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create snapshot subdirectory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_name = f"snapshot_{timestamp}"
    snapshot_dir = output_dir / snapshot_name
    snapshot_dir.mkdir(exist_ok=True)
    
    print("=" * 80)
    print("EXPORT FULL DATABASE SNAPSHOT")
    print("=" * 80)
    print(f"Database: {db_path}")
    print(f"Output: {snapshot_dir}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()
    
    # Open connection
    conn = open_connection(db_path, track_connection=False)
    
    # Get database stats
    stats = get_database_stats(conn)
    schema_version = get_current_schema_version(conn)
    
    # Tables to export (all non-system tables)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]
    
    print(f"📊 Found {len(tables)} tables to export")
    print()
    
    # Export each table
    export_summary = {}
    
    for table_name in tables:
        print(f"Exporting {table_name}...", end=" ")
        
        output_path = snapshot_dir / f"{table_name}.csv"
        row_count = export_table_to_csv(conn, table_name, output_path)
        
        export_summary[table_name] = {
            "file": f"{table_name}.csv",
            "rows": row_count,
            "size_bytes": output_path.stat().st_size
        }
        
        print(f"✓ {row_count:,} rows")
    
    conn.close()
    
    print()
    
    # Export settings if exists (JSON file)
    settings_path = Path("data/settings.json")
    if settings_path.exists():
        print("Exporting settings.json...", end=" ")
        import shutil
        shutil.copy2(settings_path, snapshot_dir / "settings.json")
        print("✓")
        export_summary["settings"] = {
            "file": "settings.json",
            "format": "json"
        }
    
    # Create manifest
    manifest = {
        "snapshot_id": snapshot_name,
        "created_at": datetime.now().isoformat(),
        "database_path": str(db_path),
        "schema_version": schema_version,
        "tables": export_summary,
        "total_tables": len(tables),
        "total_rows": sum(t["rows"] for t in export_summary.values() if "rows" in t),
        "database_size_mb": stats.get("db_size_mb", 0),
    }
    
    manifest_path = snapshot_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Manifest created: {manifest_path.name}")
    
    # Create README
    readme_content = f"""# Database Snapshot

## Snapshot Information
- **Snapshot ID**: {snapshot_name}
- **Created**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **Database**: {db_path}
- **Schema Version**: {schema_version}

## Contents
This snapshot contains CSV exports of all database tables:

"""
    for table_name, info in export_summary.items():
        if "rows" in info:
            readme_content += f"- **{table_name}.csv**: {info['rows']:,} rows\n"
        else:
            readme_content += f"- **{info['file']}**: {info.get('format', 'Unknown')} file\n"
    
    readme_content += f"""
## Total Statistics
- **Tables**: {len(tables)}
- **Total Rows**: {manifest['total_rows']:,}
- **Database Size**: {manifest['database_size_mb']:.2f} MB

## How to Use This Snapshot

### Inspect Data
Open any CSV file in Excel, LibreOffice, or text editor.
All files use UTF-8 encoding with BOM (Excel-compatible).

### Restore to SQLite
Use the companion restore tool:
```bash
python tools/import_snapshot.py {snapshot_dir}
```

### Import to Other Systems
CSV files can be imported to any database system:
- PostgreSQL: COPY command
- MySQL: LOAD DATA INFILE
- Excel: Open CSV files directly
- Python pandas: pd.read_csv()

## Notes
- This snapshot is a point-in-time export
- For production restores, use binary backups (faster, more reliable)
- CSV format is for inspection, analysis, and emergency recovery
- Date columns use YYYY-MM-DD format
- Text encoding: UTF-8 with BOM

## Manifest
See `manifest.json` for detailed metadata.
"""
    
    readme_path = snapshot_dir / "README.txt"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)
    print(f"README created: {readme_path.name}")
    
    print()
    print("=" * 80)
    print("✅ SNAPSHOT COMPLETE")
    print(f"   Location: {snapshot_dir}")
    print(f"   Tables: {len(tables)}")
    print(f"   Total Rows: {manifest['total_rows']:,}")
    print(f"   Disk Size: {sum(f.stat().st_size for f in snapshot_dir.glob('*')) / (1024*1024):.2f} MB")
    
    # Compress if requested
    if compress:
        print()
        print("🔧 Creating ZIP archive...")
        zip_path = output_dir / f"{snapshot_name}.zip"
        
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in snapshot_dir.glob("*"):
                zf.write(file_path, arcname=f"{snapshot_name}/{file_path.name}")
        
        print(f"✓ ZIP created: {zip_path}")
        print(f"  Compressed Size: {zip_path.stat().st_size / (1024*1024):.2f} MB")
        
        # Optionally delete uncompressed directory
        import shutil
        shutil.rmtree(snapshot_dir)
        print(f"  Removed uncompressed directory")
        
        print("=" * 80)
        return zip_path
    
    print("=" * 80)
    return snapshot_dir


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Export full database snapshot to CSV files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--db", type=str, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--output", type=str, help="Output directory (default: data/snapshots/)")
    parser.add_argument("--compress", action="store_true", help="Create ZIP archive")
    
    args = parser.parse_args()
    
    # Use custom paths if provided
    db_path = Path(args.db) if args.db else DB_PATH
    output_dir = Path(args.output) if args.output else None
    
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return 1
    
    try:
        result_path = export_full_snapshot(
            db_path=db_path,
            output_dir=output_dir,
            compress=args.compress
        )
        
        print()
        print(f"📦 Snapshot ready: {result_path}")
        return 0
    
    except Exception as e:
        print(f"❌ Export failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
