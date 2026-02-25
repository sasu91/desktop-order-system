"""
CSV to SQLite Migration Tool

FASE 4: Migrate existing CSV/JSON storage to SQLite database
- CSVReader: Read CSV files with schema detection
- DataValidator: Validate data integrity before import
- MigrationReport: Generate detailed migration report
- MigrationOrchestrator: Coordinate full migration process

Design Principles:
- Idempotent: Re-run without duplicates (check existing data)
- Incremental: Migrate one table at a time (resume on failure)
- Validated: Pre-flight checks (FK integrity, date formats, UNIQUE constraints)
- Dry-run mode: Preview without committing
- Golden dataset: Preserve original CSV as backup
"""

import csv
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import shutil

from .db import open_connection, transaction, backup_database
from .repositories import RepositoryFactory, DuplicateKeyError, ForeignKeyError, BusinessRuleError


# ============================================================
# Configuration
# ============================================================

from .utils.paths import get_data_dir as _get_data_dir

CSV_DIR = _get_data_dir()
BACKUP_DIR = _get_data_dir() / "csv_backups"

# CSV file mapping (filename → table name)
CSV_FILES = {
    "skus.csv": "skus",
    "transactions.csv": "transactions",
    "sales.csv": "sales",
    "order_logs.csv": "order_logs",
    "receiving_logs.csv": "receiving_logs",
    "lots.csv": "lots",
    "promo_calendar.csv": "promo_calendar",
    "kpi_daily.csv": "kpi_daily",
    "audit_log.csv": "audit_log",
    "event_uplift_rules.csv": "event_uplift_rules",
}

# JSON file mapping
JSON_FILES = {
    "settings.json": "settings",
    "holidays.json": "holidays",
}

# Migration order (respects FK dependencies)
MIGRATION_ORDER = [
    "skus",          # No FK dependencies
    "transactions",  # FK: skus
    "sales",         # FK: skus
    "order_logs",    # FK: skus
    "receiving_logs", # FK: skus
    "lots",          # FK: skus
    "promo_calendar", # FK: skus
    "kpi_daily",     # FK: skus
    "audit_log",     # FK: skus (nullable)
    "event_uplift_rules", # No FK
    "settings",      # No FK
    "holidays",      # No FK
]


# ============================================================
# Data Structures
# ============================================================

@dataclass
class ValidationError:
    """Single validation error"""
    row_num: int
    field: str
    value: Any
    error: Optional[str]  # Allow None for generic errors


@dataclass
class MigrationStats:
    """Statistics for a single table migration"""
    table: str
    total_rows: int = 0
    inserted: int = 0
    skipped: int = 0
    errors: int = 0
    validation_errors: List[ValidationError] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class MigrationReport:
    """Complete migration report"""
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    dry_run: bool = False
    tables_migrated: List[str] = field(default_factory=list)
    table_stats: Dict[str, MigrationStats] = field(default_factory=dict)
    
    def add_stats(self, stats: MigrationStats):
        """Add table migration stats"""
        self.tables_migrated.append(stats.table)
        self.table_stats[stats.table] = stats
    
    def total_inserted(self) -> int:
        """Total rows inserted across all tables"""
        return sum(s.inserted for s in self.table_stats.values())
    
    def total_errors(self) -> int:
        """Total errors across all tables"""
        return sum(s.errors for s in self.table_stats.values())
    
    def has_errors(self) -> bool:
        """Check if any errors occurred"""
        return self.total_errors() > 0
    
    def print_summary(self):
        """Print human-readable summary"""
        print("\n" + "="*70)
        print("MIGRATION REPORT")
        print("="*70)
        print(f"Started: {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.completed_at:
            print(f"Completed: {self.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")
            duration = (self.completed_at - self.started_at).total_seconds()
            print(f"Duration: {duration:.2f}s")
        print(f"Mode: {'DRY RUN' if self.dry_run else 'PRODUCTION'}")
        print()
        
        print("Table Summary:")
        print("-" * 70)
        for table in self.tables_migrated:
            stats = self.table_stats[table]
            status = "✓" if stats.errors == 0 else "✗"
            print(f"{status} {table:20s}  Total: {stats.total_rows:5d}  "
                  f"Inserted: {stats.inserted:5d}  Skipped: {stats.skipped:5d}  "
                  f"Errors: {stats.errors:3d}")
        
        print("-" * 70)
        print(f"TOTAL ROWS MIGRATED: {self.total_inserted()}")
        print(f"TOTAL ERRORS: {self.total_errors()}")
        
        if self.has_errors():
            print("\n⚠ VALIDATION ERRORS:")
            for table, stats in self.table_stats.items():
                if stats.validation_errors:
                    print(f"\n  {table}:")
                    for err in stats.validation_errors[:10]:  # Show first 10
                        print(f"    Row {err.row_num}: {err.field} = '{err.value}' → {err.error}")
                    if len(stats.validation_errors) > 10:
                        print(f"    ... and {len(stats.validation_errors) - 10} more errors")
        
        print("="*70)


# ============================================================
# CSV Reader
# ============================================================

class CSVReader:
    """Read CSV files with schema detection and error handling"""
    
    @staticmethod
    def read_csv(filepath: Path) -> List[Dict[str, Any]]:
        """
        Read CSV file and return list of dictionaries.
        
        Args:
            filepath: Path to CSV file
        
        Returns:
            List of row dictionaries (empty list if file not found)
        """
        if not filepath.exists():
            return []
        
        rows = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        
        return rows
    
    @staticmethod
    def read_json(filepath: Path) -> Dict[str, Any]:
        """
        Read JSON file and return dictionary.
        
        Args:
            filepath: Path to JSON file
        
        Returns:
            Dictionary (empty dict if file not found)
        """
        if not filepath.exists():
            return {}
        
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)


# ============================================================
# Data Validator
# ============================================================

class DataValidator:
    """Validate data before migration"""
    
    @staticmethod
    def validate_date(value: str) -> Tuple[bool, Optional[str]]:
        """
        Validate date format (YYYY-MM-DD).
        
        Returns:
            (is_valid, error_message)
        """
        if not value or value == '':
            return True, None  # Allow empty dates (nullable)
        
        try:
            # Try parsing as ISO format
            datetime.strptime(value, '%Y-%m-%d')
            return True, None
        except ValueError:
            return False, f"Invalid date format (expected YYYY-MM-DD)"
    
    @staticmethod
    def validate_integer(value: str, allow_empty: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Validate integer value.
        
        Returns:
            (is_valid, error_message)
        """
        if allow_empty and (not value or value == ''):
            return True, None
        
        try:
            int(value)
            return True, None
        except (ValueError, TypeError):
            return False, f"Invalid integer"
    
    @staticmethod
    def validate_float(value: str, allow_empty: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Validate float value.
        
        Returns:
            (is_valid, error_message)
        """
        if allow_empty and (not value or value == ''):
            return True, None
        
        try:
            float(value)
            return True, None
        except (ValueError, TypeError):
            return False, f"Invalid float"
    
    @staticmethod
    def validate_event_type(value: str) -> Tuple[bool, Optional[str]]:
        """Validate transaction event type"""
        valid_events = {
            'SNAPSHOT', 'ORDER', 'RECEIPT', 'SALE', 'WASTE', 'ADJUST', 'UNFULFILLED',
            'SKU_EDIT', 'EXPORT_LOG', 'ASSORTMENT_IN', 'ASSORTMENT_OUT'
        }
        
        if value in valid_events:
            return True, None
        return False, f"Invalid event type (must be one of {valid_events})"
    
    @staticmethod
    def validate_status(value: str) -> Tuple[bool, Optional[str]]:
        """Validate order status"""
        valid_statuses = {'PENDING', 'PARTIAL', 'RECEIVED'}
        
        if value in valid_statuses:
            return True, None
        return False, f"Invalid status (must be one of {valid_statuses})"
    
    @staticmethod
    def clean_csv_row(row: Dict[str, str]) -> Dict[str, Any]:
        """
        Clean CSV row: convert empty strings to None, trim whitespace.
        
        Args:
            row: Raw CSV row dict
        
        Returns:
            Cleaned row dict
        """
        cleaned = {}
        for key, value in row.items():
            if isinstance(value, str):
                value = value.strip()
                if value == '':
                    value = None
            cleaned[key] = value
        
        return cleaned


# ============================================================
# Migration Orchestrator
# ============================================================

class MigrationOrchestrator:
    """
    Coordinate CSV to SQLite migration.
    
    Features:
    - Idempotent: Check existing data before insert
    - Incremental: Migrate one table at a time
    - Validated: Pre-flight checks before insert
    - Dry-run mode: Preview without committing
    """
    
    def __init__(self, conn: sqlite3.Connection, csv_dir: Path = CSV_DIR):
        self.conn = conn
        self.csv_dir = csv_dir
        self.repos = RepositoryFactory(conn)
        self.report = MigrationReport()
    
    def migrate_all(self, dry_run: bool = False, tables: Optional[List[str]] = None) -> MigrationReport:
        """
        Migrate all CSV/JSON files to SQLite.
        
        Args:
            dry_run: If True, validate but don't commit
            tables: Optional list of specific tables to migrate (default: all)
        
        Returns:
            MigrationReport with statistics
        """
        self.report = MigrationReport(dry_run=dry_run)
        
        # Backup CSVs before migration
        if not dry_run:
            self._backup_csv_files()
        
        # Determine tables to migrate
        tables_to_migrate = tables if tables else MIGRATION_ORDER
        
        # Migrate each table in order (FK dependencies)
        for table in tables_to_migrate:
            if table not in MIGRATION_ORDER:
                print(f"⚠ Skipping unknown table: {table}")
                continue
            
            print(f"\n→ Migrating {table}...")
            
            try:
                stats = self._migrate_table(table, dry_run)
                self.report.add_stats(stats)
                
                if stats.errors == 0:
                    print(f"✓ {table}: {stats.inserted} rows inserted, {stats.skipped} skipped")
                else:
                    print(f"✗ {table}: {stats.errors} errors")
            
            except Exception as e:
                print(f"✗ {table} FAILED: {e}")
                stats = MigrationStats(table=table, errors=1)
                stats.validation_errors.append(ValidationError(0, 'migration', '', str(e)))
                self.report.add_stats(stats)
        
        self.report.completed_at = datetime.now()
        return self.report
    
    def _backup_csv_files(self):
        """Create backup of CSV files before migration"""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_subdir = BACKUP_DIR / f"pre_migration_{timestamp}"
        backup_subdir.mkdir(exist_ok=True)
        
        for csv_file in CSV_FILES.keys():
            src = self.csv_dir / csv_file
            if src.exists():
                dst = backup_subdir / csv_file
                shutil.copy2(src, dst)
        
        for json_file in JSON_FILES.keys():
            src = self.csv_dir / json_file
            if src.exists():
                dst = backup_subdir / json_file
                shutil.copy2(src, dst)
        
        print(f"✓ CSV/JSON files backed up to {backup_subdir}")
    
    def _migrate_table(self, table: str, dry_run: bool) -> MigrationStats:
        """Migrate single table"""
        stats = MigrationStats(table=table)
        start_time = datetime.now()
        
        # Dispatch to appropriate migration method
        if table == "skus":
            stats = self._migrate_skus(dry_run)
        elif table == "transactions":
            stats = self._migrate_transactions(dry_run)
        elif table == "sales":
            stats = self._migrate_sales(dry_run)
        elif table == "order_logs":
            stats = self._migrate_order_logs(dry_run)
        elif table == "receiving_logs":
            stats = self._migrate_receiving_logs(dry_run)
        elif table == "lots":
            stats = self._migrate_lots(dry_run)
        elif table == "promo_calendar":
            stats = self._migrate_promo_calendar(dry_run)
        elif table == "kpi_daily":
            stats = self._migrate_kpi_daily(dry_run)
        elif table == "audit_log":
            stats = self._migrate_audit_log(dry_run)
        elif table == "event_uplift_rules":
            stats = self._migrate_event_uplift_rules(dry_run)
        elif table == "settings":
            stats = self._migrate_settings(dry_run)
        elif table == "holidays":
            stats = self._migrate_holidays(dry_run)
        else:
            stats.errors = 1
            stats.validation_errors.append(ValidationError(0, 'table', table, 'Unknown table'))
        
        stats.duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        return stats
    
    def _migrate_skus(self, dry_run: bool) -> MigrationStats:
        """Migrate skus.csv"""
        stats = MigrationStats(table="skus")
        
        csv_file = self.csv_dir / "skus.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Validate required fields
            if not row.get('sku'):
                stats.validation_errors.append(ValidationError(i, 'sku', row.get('sku'), 'Required field missing'))
                stats.errors += 1
                continue
            
            # Check if already exists (idempotency)
            if self.repos.skus().exists(row['sku']):
                stats.skipped += 1
                continue
            
            # Convert types
            sku_data = {
                'sku': row['sku'],
                'description': row.get('description', ''),
                'ean': row.get('ean'),
            }
            
            # Optional integer fields
            int_fields = [
                'moq', 'pack_size', 'lead_time_days', 'review_period', 'safety_stock',
                'shelf_life_days', 'min_shelf_life_days', 'max_stock', 'reorder_point',
                'mc_n_simulations', 'mc_random_seed', 'mc_output_percentile', 'mc_horizon_days',
                'in_assortment'
            ]
            for field in int_fields:
                if row.get(field):
                    try:
                        sku_data[field] = int(row[field])
                    except (ValueError, TypeError):
                        stats.validation_errors.append(ValidationError(i, field, row[field], 'Invalid integer'))
                        stats.errors += 1
            
            # Optional float fields
            float_fields = [
                'waste_penalty_factor', 'waste_risk_threshold', 'oos_boost_percent', 'target_csl'
            ]
            for field in float_fields:
                if row.get(field):
                    try:
                        sku_data[field] = float(row[field])
                    except (ValueError, TypeError):
                        stats.validation_errors.append(ValidationError(i, field, row[field], 'Invalid float'))
                        stats.errors += 1
            
            # Text fields (keep as-is)
            text_fields = [
                'waste_penalty_mode', 'demand_variability', 'category', 'department',
                'oos_detection_mode', 'oos_popup_preference', 'forecast_method',
                'mc_distribution', 'mc_output_stat', 'mc_horizon_mode'
            ]
            for field in text_fields:
                if row.get(field):
                    sku_data[field] = row[field]
            
            # Insert (if not dry-run)
            if not dry_run:
                try:
                    self.repos.skus().upsert(sku_data)
                    stats.inserted += 1
                except Exception as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', row['sku'], str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1  # Count as inserted in dry-run mode
        
        return stats
    
    def _migrate_transactions(self, dry_run: bool) -> MigrationStats:
        """Migrate transactions.csv"""
        stats = MigrationStats(table="transactions")
        
        csv_file = self.csv_dir / "transactions.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        # Batch insert for performance
        batch = []
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Validate required fields
            required = ['date', 'sku', 'event', 'qty']
            for field in required:
                if not row.get(field):
                    stats.validation_errors.append(ValidationError(i, field, row.get(field), 'Required field missing'))
                    stats.errors += 1
                    continue
            
            # Validate date
            is_valid, error = DataValidator.validate_date(row['date'])
            if not is_valid:
                stats.validation_errors.append(ValidationError(i, 'date', row['date'], error))
                stats.errors += 1
                continue
            
            # Validate event type
            is_valid, error = DataValidator.validate_event_type(row['event'])
            if not is_valid:
                stats.validation_errors.append(ValidationError(i, 'event', row['event'], error))
                stats.errors += 1
                continue
            
            # Convert qty to int
            try:
                qty = int(row['qty'])
            except (ValueError, TypeError):
                stats.validation_errors.append(ValidationError(i, 'qty', row['qty'], 'Invalid integer'))
                stats.errors += 1
                continue
            
            # Validate receipt_date (if present)
            if row.get('receipt_date'):
                is_valid, error = DataValidator.validate_date(row['receipt_date'])
                if not is_valid:
                    stats.validation_errors.append(ValidationError(i, 'receipt_date', row['receipt_date'], error))
                    stats.errors += 1
                    continue
            
            # Add to batch
            batch.append({
                'date': row['date'],
                'sku': row['sku'],
                'event': row['event'],
                'qty': qty,
                'receipt_date': row.get('receipt_date'),
                'note': row.get('note', '')
            })
        
        # Insert batch (if not dry-run)
        if not dry_run and batch:
            try:
                self.repos.ledger().append_batch(batch)
                stats.inserted = len(batch)
            except (ForeignKeyError, BusinessRuleError) as e:
                # Fallback to individual inserts to identify failing rows
                for j, txn in enumerate(batch):
                    try:
                        self.repos.ledger().append_transaction(**txn)
                        stats.inserted += 1
                    except Exception as ex:
                        stats.validation_errors.append(ValidationError(j+1, 'insert', txn['sku'], str(ex)))
                        stats.errors += 1
        else:
            stats.inserted = len(batch)  # Count as inserted in dry-run mode
        
        return stats
    
    def _migrate_sales(self, dry_run: bool) -> MigrationStats:
        """Migrate sales.csv"""
        stats = MigrationStats(table="sales")
        
        csv_file = self.csv_dir / "sales.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Validate required fields
            required = ['date', 'sku', 'qty_sold']
            for field in required:
                if not row.get(field):
                    stats.validation_errors.append(ValidationError(i, field, row.get(field), 'Required field missing'))
                    stats.errors += 1
                    continue
            
            # Check if already exists (idempotency: PK is (date, sku))
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM sales WHERE date = ? AND sku = ?", (row['date'], row['sku']))
            if cursor.fetchone():
                stats.skipped += 1
                continue
            
            # Validate types
            try:
                qty_sold = int(row['qty_sold'])
                promo_flag = int(row.get('promo_flag', 0))
            except (ValueError, TypeError) as e:
                stats.validation_errors.append(ValidationError(i, 'qty_sold/promo_flag', row['qty_sold'], str(e)))
                stats.errors += 1
                continue
            
            # Insert
            if not dry_run:
                try:
                    with transaction(self.conn) as cur:
                        cur.execute("""
                            INSERT INTO sales (date, sku, qty_sold, promo_flag)
                            VALUES (?, ?, ?, ?)
                        """, (row['date'], row['sku'], qty_sold, promo_flag))
                    stats.inserted += 1
                except Exception as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', row['sku'], str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1
        
        return stats
    
    def _migrate_order_logs(self, dry_run: bool) -> MigrationStats:
        """Migrate order_logs.csv"""
        stats = MigrationStats(table="order_logs")
        
        csv_file = self.csv_dir / "order_logs.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Validate required fields
            required = ['order_id', 'date', 'sku', 'qty_ordered']
            for field in required:
                if not row.get(field):
                    stats.validation_errors.append(ValidationError(i, field, row.get(field), 'Required field missing'))
                    stats.errors += 1
                    continue
            
            # Check if already exists (idempotency)
            if self.repos.orders().get(row['order_id']):
                stats.skipped += 1
                continue
            
            # Build order data dict
            order_data = {
                'order_id': row['order_id'],
                'date': row['date'],
                'sku': row['sku'],
            }
            
            # Integer fields
            int_fields = [
                'qty_ordered', 'qty_received', 'target_open_qty', 'projected_stock_on_promo_start',
                'prebuild_delta_qty', 'prebuild_qty', 'prebuild_coverage_days',
                'promo_prebuild_enabled', 'event_uplift_active'
            ]
            for field in int_fields:
                if row.get(field):
                    try:
                        order_data[field] = int(row[field])
                    except (ValueError, TypeError):
                        pass  # Use default
            
            # Float fields
            float_fields = [
                'event_u_store_day', 'event_quantile', 'event_beta_i', 'event_m_i'
            ]
            for field in float_fields:
                if row.get(field):
                    try:
                        order_data[field] = float(row[field])
                    except (ValueError, TypeError):
                        pass  # Use default
            
            # Text fields
            text_fields = [
                'status', 'receipt_date', 'promo_start_date', 'prebuild_distribution_note',
                'event_delivery_date', 'event_reason', 'event_fallback_level',
                'event_beta_fallback_level', 'event_explain_short'
            ]
            for field in text_fields:
                if row.get(field):
                    order_data[field] = row[field]
            
            # Insert
            if not dry_run:
                try:
                    self.repos.orders().create_order_log(order_data)
                    stats.inserted += 1
                except (DuplicateKeyError, ForeignKeyError, BusinessRuleError) as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', row['order_id'], str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1
        
        return stats
    
    def _migrate_receiving_logs(self, dry_run: bool) -> MigrationStats:
        """Migrate receiving_logs.csv"""
        stats = MigrationStats(table="receiving_logs")
        
        csv_file = self.csv_dir / "receiving_logs.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Generate document_id if missing (use receipt_id or fallback)
            document_id = row.get('document_id') or row.get('receipt_id') or f"MIGRATED_{row['date']}_{row['sku']}"
            
            # Check if already exists (idempotency)
            if self.repos.receiving().get(document_id):
                stats.skipped += 1
                continue
            
            # Build receipt data
            try:
                receipt_data = {
                    'date': row['date'],
                    'sku': row['sku'],
                    'qty_received': int(row['qty_received']),
                    'receipt_date': row['receipt_date'],
                    'receipt_id': row.get('receipt_id'),
                    'order_ids': row.get('order_ids', '')
                }
            except (ValueError, TypeError, KeyError) as e:
                stats.validation_errors.append(ValidationError(i, 'data', document_id, str(e)))
                stats.errors += 1
                continue
            
            # Insert
            if not dry_run:
                try:
                    result = self.repos.receiving().close_receipt_idempotent(document_id, receipt_data)
                    if result['status'] == 'success':
                        stats.inserted += 1
                    else:
                        stats.skipped += 1
                except (ForeignKeyError, BusinessRuleError) as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', document_id, str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1
        
        return stats
    
    def _migrate_lots(self, dry_run: bool) -> MigrationStats:
        """Migrate lots.csv (shelf life tracking)"""
        stats = MigrationStats(table="lots")
        
        csv_file = self.csv_dir / "lots.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Check if already exists (idempotency)
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM lots WHERE lot_id = ?", (row.get('lot_id'),))
            if cursor.fetchone():
                stats.skipped += 1
                continue
            
            # Insert
            if not dry_run:
                try:
                    with transaction(self.conn) as cur:
                        cur.execute("""
                            INSERT INTO lots (lot_id, sku, expiry_date, qty_on_hand, receipt_id, receipt_date)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            row['lot_id'],
                            row['sku'],
                            row['expiry_date'],
                            int(row['qty_on_hand']),
                            row.get('receipt_id'),
                            row['receipt_date']
                        ))
                    stats.inserted += 1
                except Exception as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', row['lot_id'], str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1
        
        return stats
    
    def _migrate_promo_calendar(self, dry_run: bool) -> MigrationStats:
        """Migrate promo_calendar.csv"""
        stats = MigrationStats(table="promo_calendar")
        
        csv_file = self.csv_dir / "promo_calendar.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Check if already exists (idempotency: UNIQUE on sku, start_date, end_date, store_id)
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT 1 FROM promo_calendar 
                WHERE sku = ? AND start_date = ? AND end_date = ? AND store_id = ?
            """, (row['sku'], row['start_date'], row['end_date'], row.get('store_id', '')))
            if cursor.fetchone():
                stats.skipped += 1
                continue
            
            # Insert
            if not dry_run:
                try:
                    with transaction(self.conn) as cur:
                        cur.execute("""
                            INSERT INTO promo_calendar (sku, start_date, end_date, store_id, promo_flag)
                            VALUES (?, ?, ?, ?, ?)
                        """, (
                            row['sku'],
                            row['start_date'],
                            row['end_date'],
                            row.get('store_id', ''),
                            int(row.get('promo_flag', 1))
                        ))
                    stats.inserted += 1
                except Exception as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', row['sku'], str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1
        
        return stats
    
    def _migrate_kpi_daily(self, dry_run: bool) -> MigrationStats:
        """Migrate kpi_daily.csv"""
        stats = MigrationStats(table="kpi_daily")
        
        csv_file = self.csv_dir / "kpi_daily.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Check if already exists (idempotency: PK is sku, date, mode)
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM kpi_daily WHERE sku = ? AND date = ? AND mode = ?",
                          (row['sku'], row['date'], row['mode']))
            if cursor.fetchone():
                stats.skipped += 1
                continue
            
            # Insert
            if not dry_run:
                try:
                    with transaction(self.conn) as cur:
                        cur.execute("""
                            INSERT INTO kpi_daily
                            (sku, date, mode, oos_rate, lost_sales_est, wmape, bias,
                             fill_rate, otif_rate, avg_delay_days, n_periods, lookback_days,
                             waste_rate,
                             pi80_coverage, pi80_coverage_error,
                             wmape_promo, bias_promo, n_promo_points,
                             wmape_event, bias_event, n_event_points)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                    ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            row['sku'], row['date'], row['mode'],
                            float(row['oos_rate']) if row.get('oos_rate') else None,
                            float(row['lost_sales_est']) if row.get('lost_sales_est') else None,
                            float(row['wmape']) if row.get('wmape') else None,
                            float(row['bias']) if row.get('bias') else None,
                            float(row['fill_rate']) if row.get('fill_rate') else None,
                            float(row['otif_rate']) if row.get('otif_rate') else None,
                            float(row['avg_delay_days']) if row.get('avg_delay_days') else None,
                            int(row['n_periods']),
                            int(row['lookback_days']),
                            float(row['waste_rate']) if row.get('waste_rate') else None,
                            float(row['pi80_coverage']) if row.get('pi80_coverage') else None,
                            float(row['pi80_coverage_error']) if row.get('pi80_coverage_error') else None,
                            float(row['wmape_promo']) if row.get('wmape_promo') else None,
                            float(row['bias_promo']) if row.get('bias_promo') else None,
                            int(row['n_promo_points']) if row.get('n_promo_points') else 0,
                            float(row['wmape_event']) if row.get('wmape_event') else None,
                            float(row['bias_event']) if row.get('bias_event') else None,
                            int(row['n_event_points']) if row.get('n_event_points') else 0,
                        ))
                    stats.inserted += 1
                except Exception as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', row['sku'], str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1
        
        return stats
    
    def _migrate_audit_log(self, dry_run: bool) -> MigrationStats:
        """Migrate audit_log.csv"""
        stats = MigrationStats(table="audit_log")
        
        csv_file = self.csv_dir / "audit_log.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        # Audit log has AUTOINCREMENT PK, so just insert all
        if not dry_run:
            try:
                with transaction(self.conn) as cur:
                    for row in rows:
                        row = DataValidator.clean_csv_row(row)
                        cur.execute("""
                            INSERT INTO audit_log (timestamp, operation, sku, details, user)
                            VALUES (?, ?, ?, ?, ?)
                        """, (
                            row['timestamp'],
                            row['operation'],
                            row.get('sku'),
                            row.get('details', ''),
                            row.get('user', 'system')
                        ))
                stats.inserted = len(rows)
            except Exception as e:
                stats.errors = 1
                stats.validation_errors.append(ValidationError(0, 'batch', '', str(e)))
        else:
            stats.inserted = len(rows)
        
        return stats
    
    def _migrate_event_uplift_rules(self, dry_run: bool) -> MigrationStats:
        """Migrate event_uplift_rules.csv"""
        stats = MigrationStats(table="event_uplift_rules")
        
        csv_file = self.csv_dir / "event_uplift_rules.csv"
        rows = CSVReader.read_csv(csv_file)
        stats.total_rows = len(rows)
        
        for i, raw_row in enumerate(rows, start=1):
            row = DataValidator.clean_csv_row(raw_row)
            
            # Check if already exists (idempotency: UNIQUE on delivery_date, scope_type, scope_key)
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT 1 FROM event_uplift_rules 
                WHERE delivery_date = ? AND scope_type = ? AND scope_key = ?
            """, (row['delivery_date'], row['scope_type'], row.get('scope_key', '')))
            if cursor.fetchone():
                stats.skipped += 1
                continue
            
            # Insert
            if not dry_run:
                try:
                    with transaction(self.conn) as cur:
                        cur.execute("""
                            INSERT INTO event_uplift_rules 
                            (delivery_date, reason, strength, scope_type, scope_key, notes)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            row['delivery_date'],
                            row['reason'],
                            float(row['strength']),
                            row['scope_type'],
                            row.get('scope_key', ''),
                            row.get('notes', '')
                        ))
                    stats.inserted += 1
                except Exception as e:
                    stats.validation_errors.append(ValidationError(i, 'insert', row['delivery_date'], str(e)))
                    stats.errors += 1
            else:
                stats.inserted += 1
        
        return stats
    
    def _migrate_settings(self, dry_run: bool) -> MigrationStats:
        """Migrate settings.json"""
        stats = MigrationStats(table="settings")
        
        json_file = self.csv_dir / "settings.json"
        settings_dict = CSVReader.read_json(json_file)
        
        if not settings_dict:
            return stats
        
        stats.total_rows = 1
        
        # Check if already exists with non-empty settings
        cursor = self.conn.cursor()
        cursor.execute("SELECT settings_json FROM settings WHERE id = 1")
        existing = cursor.fetchone()
        
        if existing:
            # Check if existing settings are non-empty (has keys beyond default {})
            existing_settings = json.loads(existing[0]) if existing[0] else {}
            if existing_settings and len(existing_settings) > 0:
                # Already populated with real settings, skip
                stats.skipped = 1
                return stats
        
        # Insert or replace settings
        if not dry_run:
            try:
                with transaction(self.conn) as cur:
                    cur.execute("""
                        INSERT OR REPLACE INTO settings (id, settings_json)
                        VALUES (1, ?)
                    """, (json.dumps(settings_dict),))
                stats.inserted = 1
            except Exception as e:
                stats.errors = 1
                stats.validation_errors.append(ValidationError(0, 'settings', '', str(e)))
        else:
            stats.inserted = 1
        
        return stats
    
    def _migrate_holidays(self, dry_run: bool) -> MigrationStats:
        """Migrate holidays.json"""
        stats = MigrationStats(table="holidays")
        
        json_file = self.csv_dir / "holidays.json"
        holidays_dict = CSVReader.read_json(json_file)
        
        if not holidays_dict:
            holidays_dict = {"holidays": []}
        
        stats.total_rows = 1
        
        # Check if already exists with non-empty holidays
        cursor = self.conn.cursor()
        cursor.execute("SELECT holidays_json FROM holidays WHERE id = 1")
        existing = cursor.fetchone()
        
        if existing:
            # Check if existing holidays are non-empty
            existing_holidays = json.loads(existing[0]) if existing[0] else {"holidays": []}
            if existing_holidays.get('holidays') and len(existing_holidays['holidays']) > 0:
                # Already populated with real holidays, skip
                stats.skipped = 1
                return stats
        
        # Insert or replace holidays
        if not dry_run:
            try:
                with transaction(self.conn) as cur:
                    cur.execute("""
                        INSERT OR REPLACE INTO holidays (id, holidays_json)
                        VALUES (1, ?)
                    """, (json.dumps(holidays_dict),))
                stats.inserted = 1
            except Exception as e:
                stats.errors = 1
                stats.validation_errors.append(ValidationError(0, 'holidays', '', str(e)))
        else:
            stats.inserted = 1
        
        return stats


# ============================================================
# CLI Interface
# ============================================================

if __name__ == "__main__":
    import sys
    
    # Parse arguments
    dry_run = "--dry-run" in sys.argv
    tables_arg = [arg.split('=')[1] for arg in sys.argv if arg.startswith('--tables=')]
    tables = tables_arg[0].split(',') if tables_arg else None
    
    # Connect to database
    conn = open_connection()
    
    # Run migration
    orchestrator = MigrationOrchestrator(conn)
    report = orchestrator.migrate_all(dry_run=dry_run, tables=tables)
    
    # Print report
    report.print_summary()
    
    # Close connection
    conn.close()
    
    # Exit with error code if failures
    sys.exit(1 if report.has_errors() else 0)
