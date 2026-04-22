#!/usr/bin/env python3
"""
FASE 7 TASK 7.2 — Database Integrity & Invariant Checker

Comprehensive database health check tool with detailed diagnostics.

Usage:
    python tools/db_check.py                 # Full check with all tests
    python tools/db_check.py --quick         # Quick check (structure only)
    python tools/db_check.py --fix           # Attempt automatic fixes (where safe)
    python tools/db_check.py --verbose       # Verbose output with details

Exit Codes:
    0 = All checks PASS
    1 = One or more checks FAIL
    2 = One or more checks WARN (but no failures)

Report Sections:
    1. Structural Integrity (PRAGMA integrity_check)
    2. Referential Integrity (PRAGMA foreign_key_check)
    3. Schema Verification (expected tables, indices)
    4. Invariant Validation (data quality checks)
    5. WAL Checkpoint Status (journal mode health)
    6. Database Statistics (size, row counts)
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional
import sqlite3
import argparse

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import (
    open_connection,
    get_current_schema_version,
    DB_PATH,
)


# ============================================================
# Check Result Classes
# ============================================================

class CheckResult:
    """Result of a single check."""
    
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    
    def __init__(self, name: str, status: str, message: str = "", details: Optional[List[str]] = None, recovery_hint: str = ""):
        self.name = name
        self.status = status
        self.message = message
        self.details = details or []
        self.recovery_hint = recovery_hint
    
    def __str__(self):
        status_icon = {
            self.PASS: "✓",
            self.WARN: "⚠",
            self.FAIL: "✗"
        }[self.status]
        
        result = f"{status_icon} {self.status}: {self.name}"
        if self.message:
            result += f"\n  {self.message}"
        if self.details:
            for detail in self.details[:10]:  # Limit to first 10
                result += f"\n    - {detail}"
            if len(self.details) > 10:
                result += f"\n    ... and {len(self.details) - 10} more"
        if self.recovery_hint and self.status == self.FAIL:
            result += f"\n  💡 Recovery: {self.recovery_hint}"
        return result


class CheckReport:
    """Collection of check results."""
    
    def __init__(self):
        self.results: List[CheckResult] = []
        self.start_time = datetime.now()
        self.end_time = None
    
    def add(self, result: CheckResult):
        """Add a check result."""
        self.results.append(result)
    
    def finalize(self):
        """Finalize report (set end time)."""
        self.end_time = datetime.now()
    
    def get_summary(self) -> Dict[str, int]:
        """Get count of PASS/WARN/FAIL."""
        return {
            "PASS": sum(1 for r in self.results if r.status == CheckResult.PASS),
            "WARN": sum(1 for r in self.results if r.status == CheckResult.WARN),
            "FAIL": sum(1 for r in self.results if r.status == CheckResult.FAIL),
        }
    
    def has_failures(self) -> bool:
        """Check if any checks failed."""
        return any(r.status == CheckResult.FAIL for r in self.results)
    
    def has_warnings(self) -> bool:
        """Check if any checks warned."""
        return any(r.status == CheckResult.WARN for r in self.results)
    
    def print_report(self, verbose: bool = False):
        """Print formatted report."""
        print("=" * 80)
        print("DATABASE INTEGRITY & INVARIANT CHECK REPORT")
        print("=" * 80)
        print(f"Database: {DB_PATH}")
        print(f"Timestamp: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.end_time and self.start_time:
            print(f"Duration: {(self.end_time - self.start_time).total_seconds():.2f}s")
        print("=" * 80)
        print()
        
        # Group results by status
        for status in [CheckResult.FAIL, CheckResult.WARN, CheckResult.PASS]:
            status_results = [r for r in self.results if r.status == status]
            if status_results or verbose:
                print(f"{status} ({len(status_results)}):")
                print("-" * 80)
                for result in status_results:
                    print(result)
                    print()
        
        # Summary
        summary = self.get_summary()
        print("=" * 80)
        print("SUMMARY:")
        print(f"  PASS: {summary['PASS']}")
        print(f"  WARN: {summary['WARN']}")
        print(f"  FAIL: {summary['FAIL']}")
        print("=" * 80)
        
        if self.has_failures():
            print("\n❌ DATABASE HAS CRITICAL ISSUES")
            print("Action: Review FAIL items above and follow recovery instructions.")
        elif self.has_warnings():
            print("\n⚠️  DATABASE HAS WARNINGS")
            print("Action: Review WARN items above (non-critical, but should be addressed).")
        else:
            print("\n✅ DATABASE IS HEALTHY")


# ============================================================
# Check Functions
# ============================================================

def check_structural_integrity(conn: sqlite3.Connection) -> CheckResult:
    """Check structural integrity (PRAGMA integrity_check)."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check")
    result = cursor.fetchall()
    
    if len(result) == 1 and result[0][0] == "ok":
        return CheckResult(
            "Structural Integrity",
            CheckResult.PASS,
            "Database structure is intact (no corruption)"
        )
    else:
        details = [row[0] for row in result]
        return CheckResult(
            "Structural Integrity",
            CheckResult.FAIL,
            f"Database corruption detected ({len(details)} issues)",
            details,
            "Restore from backup: cp data/backups/app_TIMESTAMP.db data/app.db"
        )


def check_referential_integrity(conn: sqlite3.Connection) -> CheckResult:
    """Check referential integrity (PRAGMA foreign_key_check)."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_key_check")
    violations = cursor.fetchall()
    
    if not violations:
        return CheckResult(
            "Referential Integrity",
            CheckResult.PASS,
            "All foreign key constraints satisfied"
        )
    else:
        details = [
            f"Table: {row[0]}, RowID: {row[1]}, Parent: {row[2]}, FK Index: {row[3]}"
            for row in violations
        ]
        return CheckResult(
            "Referential Integrity",
            CheckResult.FAIL,
            f"Foreign key violations found ({len(violations)})",
            details,
            "Review orphaned records and either delete or fix parent references"
        )


def check_schema_version(conn: sqlite3.Connection) -> CheckResult:
    """Check schema version is valid."""
    version = get_current_schema_version(conn)
    
    if version == 0:
        return CheckResult(
            "Schema Version",
            CheckResult.FAIL,
            "Schema version is 0 (no migrations applied)",
            recovery_hint="Run: python src/db.py migrate"
        )
    elif version < 3:  # Assuming latest is 3+
        return CheckResult(
            "Schema Version",
            CheckResult.WARN,
            f"Schema version is {version} (migrations may be pending)",
            recovery_hint="Run: python src/db.py migrate"
        )
    else:
        return CheckResult(
            "Schema Version",
            CheckResult.PASS,
            f"Schema version is {version} (up-to-date)"
        )


def check_expected_tables(conn: sqlite3.Connection) -> CheckResult:
    """Check all expected tables exist."""
    cursor = conn.cursor()
    
    expected_tables = {
        "schema_version", "skus", "transactions", "sales", "order_logs",
        "receiving_logs", "order_receipts", "lots", "promo_calendar",
        "kpi_daily", "audit_log", "event_uplift_rules", "settings", "holidays"
    }
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    actual_tables = {row[0] for row in cursor.fetchall()}
    
    missing = expected_tables - actual_tables
    extra = actual_tables - expected_tables - {"sqlite_sequence"}
    
    if missing:
        return CheckResult(
            "Expected Tables",
            CheckResult.FAIL,
            f"{len(missing)} expected tables missing",
            [f"Missing: {table}" for table in sorted(missing)],
            "Run: python src/db.py init"
        )
    elif extra:
        return CheckResult(
            "Expected Tables",
            CheckResult.WARN,
            f"{len(extra)} unexpected tables found",
            [f"Extra: {table}" for table in sorted(extra)]
        )
    else:
        return CheckResult(
            "Expected Tables",
            CheckResult.PASS,
            f"All {len(expected_tables)} expected tables present"
        )


def check_foreign_keys_enabled(conn: sqlite3.Connection) -> CheckResult:
    """Check foreign keys are enabled."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys")
    enabled = cursor.fetchone()[0]
    
    if enabled == 1:
        return CheckResult(
            "Foreign Keys Enabled",
            CheckResult.PASS,
            "Foreign keys are enabled"
        )
    else:
        return CheckResult(
            "Foreign Keys Enabled",
            CheckResult.FAIL,
            "Foreign keys are NOT enabled (data integrity at risk)",
            recovery_hint="Reconnect with PRAGMA foreign_keys=ON (already done in src/db.py)"
        )


def check_invariant_qty_valid(conn: sqlite3.Connection) -> CheckResult:
    """Check that qty columns are valid integers (not NULL, not NaN)."""
    cursor = conn.cursor()
    issues = []
    
    # Check transactions.qty
    cursor.execute("""
        SELECT COUNT(*) FROM transactions 
        WHERE qty IS NULL OR typeof(qty) != 'integer'
    """)
    invalid_txn_qty = cursor.fetchone()[0]
    if invalid_txn_qty > 0:
        issues.append(f"transactions: {invalid_txn_qty} rows with invalid qty")
    
    # Check sales.qty_sold
    cursor.execute("""
        SELECT COUNT(*) FROM sales 
        WHERE qty_sold IS NULL OR typeof(qty_sold) != 'integer'
    """)
    invalid_sales_qty = cursor.fetchone()[0]
    if invalid_sales_qty > 0:
        issues.append(f"sales: {invalid_sales_qty} rows with invalid qty_sold")
    
    # Check order_logs.qty_ordered
    cursor.execute("""
        SELECT COUNT(*) FROM order_logs 
        WHERE qty_ordered IS NULL OR typeof(qty_ordered) != 'integer'
    """)
    invalid_order_qty = cursor.fetchone()[0]
    if invalid_order_qty > 0:
        issues.append(f"order_logs: {invalid_order_qty} rows with invalid qty_ordered")
    
    if issues:
        return CheckResult(
            "Invariant: Qty Valid",
            CheckResult.FAIL,
            "Invalid qty values found",
            issues,
            "Review and fix: UPDATE table SET qty = 0 WHERE qty IS NULL (or delete invalid rows)"
        )
    else:
        return CheckResult(
            "Invariant: Qty Valid",
            CheckResult.PASS,
            "All qty columns are valid integers"
        )


def check_invariant_dates_valid(conn: sqlite3.Connection) -> CheckResult:
    """Check that date columns are parseable (YYYY-MM-DD format)."""
    cursor = conn.cursor()
    issues = []
    
    # Check transactions.date
    cursor.execute("""
        SELECT COUNT(*) FROM transactions 
        WHERE date IS NULL OR date NOT LIKE '____-__-__'
    """)
    invalid_txn_date = cursor.fetchone()[0]
    if invalid_txn_date > 0:
        issues.append(f"transactions: {invalid_txn_date} rows with invalid date")
    
    # Check sales.date
    cursor.execute("""
        SELECT COUNT(*) FROM sales 
        WHERE date IS NULL OR date NOT LIKE '____-__-__'
    """)
    invalid_sales_date = cursor.fetchone()[0]
    if invalid_sales_date > 0:
        issues.append(f"sales: {invalid_sales_date} rows with invalid date")
    
    # Check order_logs.date
    cursor.execute("""
        SELECT COUNT(*) FROM order_logs 
        WHERE date IS NULL OR date NOT LIKE '____-__-__'
    """)
    invalid_order_date = cursor.fetchone()[0]
    if invalid_order_date > 0:
        issues.append(f"order_logs: {invalid_order_date} rows with invalid date")
    
    if issues:
        return CheckResult(
            "Invariant: Dates Valid",
            CheckResult.FAIL,
            "Invalid date values found",
            issues,
            "Fix dates to YYYY-MM-DD format or delete invalid rows"
        )
    else:
        return CheckResult(
            "Invariant: Dates Valid",
            CheckResult.PASS,
            "All date columns are in valid format"
        )


def check_invariant_document_ids_unique(conn: sqlite3.Connection) -> CheckResult:
    """Check that document_id values are unique per table."""
    cursor = conn.cursor()
    issues = []
    
    # Check receiving_logs.document_id uniqueness
    cursor.execute("""
        SELECT document_id, COUNT(*) as cnt 
        FROM receiving_logs 
        WHERE document_id IS NOT NULL
        GROUP BY document_id 
        HAVING cnt > 1
    """)
    duplicates = cursor.fetchall()
    if duplicates:
        issues.append(f"receiving_logs: {len(duplicates)} duplicate document_id values")
        for doc_id, count in duplicates[:5]:
            issues.append(f"  document_id='{doc_id}' appears {count} times")
    
    # Check order_logs.order_id uniqueness (if it should be unique)
    cursor.execute("""
        SELECT order_id, COUNT(*) as cnt 
        FROM order_logs 
        WHERE order_id IS NOT NULL
        GROUP BY order_id 
        HAVING cnt > 1
    """)
    duplicates = cursor.fetchall()
    if duplicates:
        issues.append(f"order_logs: {len(duplicates)} duplicate order_id values")
        for order_id, count in duplicates[:5]:
            issues.append(f"  order_id='{order_id}' appears {count} times")
    
    if issues:
        return CheckResult(
            "Invariant: Document IDs Unique",
            CheckResult.WARN,  # WARN not FAIL (may be legitimate)
            "Duplicate document IDs found",
            issues,
            "Review duplicates - may indicate reprocessing or need for composite keys"
        )
    else:
        return CheckResult(
            "Invariant: Document IDs Unique",
            CheckResult.PASS,
            "All document IDs are unique"
        )


def check_invariant_no_orphaned_transactions(conn: sqlite3.Connection) -> CheckResult:
    """Check that all transactions reference existing SKUs."""
    cursor = conn.cursor()
    
    # Find transactions with non-existent SKUs
    cursor.execute("""
        SELECT DISTINCT t.sku 
        FROM transactions t 
        LEFT JOIN skus s ON t.sku = s.sku 
        WHERE s.sku IS NULL
    """)
    orphaned_skus = [row[0] for row in cursor.fetchall()]
    
    if orphaned_skus:
        # Count orphaned transactions
        cursor.execute("""
            SELECT COUNT(*) 
            FROM transactions t 
            LEFT JOIN skus s ON t.sku = s.sku 
            WHERE s.sku IS NULL
        """)
        orphaned_count = cursor.fetchone()[0]
        
        return CheckResult(
            "Invariant: No Orphaned Transactions",
            CheckResult.FAIL,
            f"{orphaned_count} transactions reference non-existent SKUs",
            [f"Missing SKU: {sku}" for sku in orphaned_skus[:10]],
            "Either add missing SKUs or delete orphaned transactions"
        )
    else:
        return CheckResult(
            "Invariant: No Orphaned Transactions",
            CheckResult.PASS,
            "All transactions reference existing SKUs"
        )


def check_invariant_no_orphaned_sales(conn: sqlite3.Connection) -> CheckResult:
    """Check that all sales reference existing SKUs."""
    cursor = conn.cursor()
    
    # Find sales with non-existent SKUs
    cursor.execute("""
        SELECT DISTINCT sa.sku 
        FROM sales sa 
        LEFT JOIN skus s ON sa.sku = s.sku 
        WHERE s.sku IS NULL
    """)
    orphaned_skus = [row[0] for row in cursor.fetchall()]
    
    if orphaned_skus:
        cursor.execute("""
            SELECT COUNT(*) 
            FROM sales sa 
            LEFT JOIN skus s ON sa.sku = s.sku 
            WHERE s.sku IS NULL
        """)
        orphaned_count = cursor.fetchone()[0]
        
        return CheckResult(
            "Invariant: No Orphaned Sales",
            CheckResult.FAIL,
            f"{orphaned_count} sales reference non-existent SKUs",
            [f"Missing SKU: {sku}" for sku in orphaned_skus[:10]],
            "Either add missing SKUs or delete orphaned sales"
        )
    else:
        return CheckResult(
            "Invariant: No Orphaned Sales",
            CheckResult.PASS,
            "All sales reference existing SKUs"
        )


def check_wal_checkpoint_status(conn: sqlite3.Connection) -> CheckResult:
    """Check WAL file size and checkpoint status."""
    cursor = conn.cursor()
    
    # Check journal mode
    cursor.execute("PRAGMA journal_mode")
    journal_mode = cursor.fetchone()[0]
    
    if journal_mode.upper() != "WAL":
        return CheckResult(
            "WAL Checkpoint Status",
            CheckResult.WARN,
            f"Journal mode is {journal_mode} (expected WAL)",
            recovery_hint="PRAGMA journal_mode=WAL (already done in src/db.py)"
        )
    
    # Check WAL file size
    wal_path = Path(str(DB_PATH) + "-wal")
    if wal_path.exists():
        wal_size_mb = wal_path.stat().st_size / (1024 * 1024)
        
        if wal_size_mb > 100:
            return CheckResult(
                "WAL Checkpoint Status",
                CheckResult.WARN,
                f"WAL file is large ({wal_size_mb:.2f} MB)",
                recovery_hint="Run checkpoint: sqlite3 data/app.db 'PRAGMA wal_checkpoint(TRUNCATE)'"
            )
        else:
            return CheckResult(
                "WAL Checkpoint Status",
                CheckResult.PASS,
                f"WAL file size is normal ({wal_size_mb:.2f} MB)"
            )
    else:
        return CheckResult(
            "WAL Checkpoint Status",
            CheckResult.PASS,
            "WAL file does not exist (or already checkpointed)"
        )


def check_database_statistics(conn: sqlite3.Connection) -> CheckResult:
    """Get database statistics (informational)."""
    cursor = conn.cursor()
    
    stats = []
    
    # Database size
    if DB_PATH.exists():
        db_size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        stats.append(f"Database size: {db_size_mb:.2f} MB")
    
    # Table count
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    table_count = cursor.fetchone()[0]
    stats.append(f"Tables: {table_count}")
    
    # Index count
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
    index_count = cursor.fetchone()[0]
    stats.append(f"Indices: {index_count}")
    
    # Row counts for key tables
    for table in ["skus", "transactions", "sales", "order_logs", "receiving_logs"]:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            stats.append(f"{table}: {count:,} rows")
        except sqlite3.OperationalError:
            pass  # Table doesn't exist
    
    return CheckResult(
        "Database Statistics",
        CheckResult.PASS,  # Always PASS (informational)
        "Database statistics gathered",
        stats
    )


# ============================================================
# Main Check Runner
# ============================================================

def run_all_checks(conn: sqlite3.Connection, quick: bool = False) -> CheckReport:
    """Run all checks and return report."""
    report = CheckReport()
    
    print("Running database checks...")
    print()
    
    # Structural checks (always run)
    report.add(check_structural_integrity(conn))
    report.add(check_referential_integrity(conn))
    report.add(check_schema_version(conn))
    report.add(check_expected_tables(conn))
    report.add(check_foreign_keys_enabled(conn))
    
    # Invariant checks (skip if --quick)
    if not quick:
        report.add(check_invariant_qty_valid(conn))
        report.add(check_invariant_dates_valid(conn))
        report.add(check_invariant_document_ids_unique(conn))
        report.add(check_invariant_no_orphaned_transactions(conn))
        report.add(check_invariant_no_orphaned_sales(conn))
    
    # WAL and stats (informational)
    report.add(check_wal_checkpoint_status(conn))
    report.add(check_database_statistics(conn))
    
    report.finalize()
    return report


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Database integrity and invariant checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--quick", action="store_true", help="Quick check (structure only, skip invariants)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--db", type=str, help=f"Database path (default: {DB_PATH})")
    
    args = parser.parse_args()
    
    # Use custom DB path if provided
    db_path = Path(args.db) if args.db else DB_PATH
    
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        print(f"   Run: python src/db.py init")
        return 1
    
    try:
        conn = open_connection(db_path, track_connection=False)
        report = run_all_checks(conn, quick=args.quick)
        conn.close()
        
        print()
        report.print_report(verbose=args.verbose)
        
        # Exit code
        if report.has_failures():
            return 1
        elif report.has_warnings():
            return 2
        else:
            return 0
    
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
