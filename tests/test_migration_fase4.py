"""
Test Suite for FASE 4: CSV to SQLite Migration Tool

Tests:
1. CSVReader - CSV/JSON reading
2. DataValidator - data validation logic
3. MigrationOrchestrator - full migration flow
4. Idempotency - re-run without duplicates
5. Error handling - invalid data scenarios
"""

import pytest
import sqlite3
import json
import csv
from pathlib import Path
from datetime import datetime
import tempfile
import shutil

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from migrate_csv_to_sqlite import (
    CSVReader, DataValidator, MigrationOrchestrator, MigrationReport,
    MigrationStats, ValidationError
)
from db import open_connection, apply_migrations, transaction
from repositories import RepositoryFactory


# ============================================================
# Test Fixtures
# ============================================================

@pytest.fixture
def temp_csv_dir():
    """Create temporary directory for test CSV files"""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def test_db():
    """Create test database"""
    db_file = Path(tempfile.mktemp(suffix=".db"))
    conn = open_connection(db_path=db_file)
    apply_migrations(conn)  # Apply schema
    yield conn
    conn.close()
    if db_file.exists():
        db_file.unlink()


@pytest.fixture
def sample_skus_csv(temp_csv_dir):
    """Create sample skus.csv"""
    csv_file = temp_csv_dir / "skus.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['sku', 'description', 'ean', 'moq', 'lead_time_days'])
        writer.writeheader()
        writer.writerow({
            'sku': 'SKU001',
            'description': 'Test Product 1',
            'ean': '1234567890123',
            'moq': '10',
            'lead_time_days': '7'
        })
        writer.writerow({
            'sku': 'SKU002',
            'description': 'Test Product 2',
            'ean': '9876543210987',
            'moq': '5',
            'lead_time_days': '14'
        })
    return csv_file


@pytest.fixture
def sample_transactions_csv(temp_csv_dir):
    """Create sample transactions.csv"""
    csv_file = temp_csv_dir / "transactions.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'sku', 'event', 'qty', 'receipt_date', 'note'])
        writer.writeheader()
        writer.writerow({
            'date': '2024-01-01',
            'sku': 'SKU001',
            'event': 'SNAPSHOT',
            'qty': '100',
            'receipt_date': '',
            'note': 'Initial inventory'
        })
        writer.writerow({
            'date': '2024-01-02',
            'sku': 'SKU001',
            'event': 'SALE',
            'qty': '-10',
            'receipt_date': '',
            'note': ''
        })
        writer.writerow({
            'date': '2024-01-03',
            'sku': 'SKU002',
            'event': 'ORDER',
            'qty': '50',
            'receipt_date': '2024-01-10',
            'note': ''
        })
    return csv_file


@pytest.fixture
def sample_sales_csv(temp_csv_dir):
    """Create sample sales.csv"""
    csv_file = temp_csv_dir / "sales.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'sku', 'qty_sold', 'promo_flag'])
        writer.writeheader()
        writer.writerow({'date': '2024-01-01', 'sku': 'SKU001', 'qty_sold': '10', 'promo_flag': '0'})
        writer.writerow({'date': '2024-01-02', 'sku': 'SKU001', 'qty_sold': '15', 'promo_flag': '0'})
        writer.writerow({'date': '2024-01-03', 'sku': 'SKU002', 'qty_sold': '5', 'promo_flag': '1'})
    return csv_file


@pytest.fixture
def sample_settings_json(temp_csv_dir):
    """Create sample settings.json"""
    json_file = temp_csv_dir / "settings.json"
    settings = {
        "default_lead_time": 7,
        "default_moq": 10,
        "target_csl": 0.95,
        "warehouse_id": "WH001"
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)
    return json_file


@pytest.fixture
def sample_holidays_json(temp_csv_dir):
    """Create sample holidays.json"""
    json_file = temp_csv_dir / "holidays.json"
    holidays = {
        "holidays": [
            {"date": "2024-01-01", "name": "New Year"},
            {"date": "2024-12-25", "name": "Christmas"}
        ]
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(holidays, f, indent=2)
    return json_file


@pytest.fixture
def invalid_transactions_csv(temp_csv_dir):
    """Create transactions.csv with invalid data"""
    csv_file = temp_csv_dir / "transactions_invalid.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'sku', 'event', 'qty', 'receipt_date', 'note'])
        writer.writeheader()
        # Invalid date format
        writer.writerow({
            'date': '01/01/2024',
            'sku': 'SKU001',
            'event': 'SNAPSHOT',
            'qty': '100',
            'receipt_date': '',
            'note': ''
        })
        # Invalid event type
        writer.writerow({
            'date': '2024-01-02',
            'sku': 'SKU001',
            'event': 'INVALID_EVENT',
            'qty': '10',
            'receipt_date': '',
            'note': ''
        })
        # Invalid qty (not a number)
        writer.writerow({
            'date': '2024-01-03',
            'sku': 'SKU001',
            'event': 'SALE',
            'qty': 'invalid',
            'receipt_date': '',
            'note': ''
        })
        # Missing required field
        writer.writerow({
            'date': '2024-01-04',
            'sku': '',
            'event': 'SALE',
            'qty': '5',
            'receipt_date': '',
            'note': ''
        })
    return csv_file


# ============================================================
# Test CSVReader
# ============================================================

def test_csv_reader_read_csv(sample_skus_csv):
    """Test CSVReader.read_csv()"""
    rows = CSVReader.read_csv(sample_skus_csv)
    
    assert len(rows) == 2
    assert rows[0]['sku'] == 'SKU001'
    assert rows[0]['description'] == 'Test Product 1'
    assert rows[1]['sku'] == 'SKU002'
    assert rows[1]['moq'] == '5'


def test_csv_reader_missing_file(temp_csv_dir):
    """Test CSVReader with non-existent file"""
    missing_file = temp_csv_dir / "missing.csv"
    rows = CSVReader.read_csv(missing_file)
    
    assert rows == []


def test_csv_reader_read_json(sample_settings_json):
    """Test CSVReader.read_json()"""
    data = CSVReader.read_json(sample_settings_json)
    
    assert 'default_lead_time' in data
    assert data['default_lead_time'] == 7
    assert data['warehouse_id'] == 'WH001'


def test_csv_reader_missing_json(temp_csv_dir):
    """Test CSVReader with non-existent JSON file"""
    missing_file = temp_csv_dir / "missing.json"
    data = CSVReader.read_json(missing_file)
    
    assert data == {}


# ============================================================
# Test DataValidator
# ============================================================

def test_validator_date_valid():
    """Test date validation with valid dates"""
    is_valid, error = DataValidator.validate_date('2024-01-15')
    assert is_valid
    assert error is None


def test_validator_date_invalid_format():
    """Test date validation with invalid format"""
    is_valid, error = DataValidator.validate_date('01/15/2024')
    assert not is_valid
    assert 'Invalid date format' in error


def test_validator_date_empty():
    """Test date validation with empty string (nullable)"""
    is_valid, error = DataValidator.validate_date('')
    assert is_valid
    assert error is None


def test_validator_integer_valid():
    """Test integer validation"""
    is_valid, error = DataValidator.validate_integer('42')
    assert is_valid
    assert error is None
    
    is_valid, error = DataValidator.validate_integer('-10')
    assert is_valid


def test_validator_integer_invalid():
    """Test integer validation with invalid value"""
    is_valid, error = DataValidator.validate_integer('not_a_number')
    assert not is_valid
    assert 'Invalid integer' in error


def test_validator_float_valid():
    """Test float validation"""
    is_valid, error = DataValidator.validate_float('3.14')
    assert is_valid
    assert error is None


def test_validator_float_invalid():
    """Test float validation with invalid value"""
    is_valid, error = DataValidator.validate_float('abc')
    assert not is_valid
    assert 'Invalid float' in error


def test_validator_event_type_valid():
    """Test event type validation"""
    valid_events = ['SNAPSHOT', 'ORDER', 'RECEIPT', 'SALE', 'WASTE', 'ADJUST', 'UNFULFILLED']
    
    for event in valid_events:
        is_valid, error = DataValidator.validate_event_type(event)
        assert is_valid, f"Event {event} should be valid"
        assert error is None


def test_validator_event_type_invalid():
    """Test event type validation with invalid event"""
    is_valid, error = DataValidator.validate_event_type('INVALID_EVENT')
    assert not is_valid
    assert 'Invalid event type' in error


def test_validator_status_valid():
    """Test order status validation"""
    for status in ['PENDING', 'PARTIAL', 'RECEIVED']:
        is_valid, error = DataValidator.validate_status(status)
        assert is_valid
        assert error is None


def test_validator_status_invalid():
    """Test order status validation with invalid status"""
    is_valid, error = DataValidator.validate_status('UNKNOWN')
    assert not is_valid
    assert 'Invalid status' in error


def test_validator_clean_csv_row():
    """Test CSV row cleaning"""
    raw_row = {
        'sku': '  SKU001  ',
        'description': 'Test',
        'moq': '',
        'lead_time': '  10  '
    }
    
    cleaned = DataValidator.clean_csv_row(raw_row)
    
    assert cleaned['sku'] == 'SKU001'
    assert cleaned['description'] == 'Test'
    assert cleaned['moq'] is None  # Empty string converted to None
    assert cleaned['lead_time'] == '10'


# ============================================================
# Test MigrationOrchestrator - SKUs
# ============================================================

def test_migrate_skus_success(test_db, temp_csv_dir, sample_skus_csv):
    """Test migrating skus.csv"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    report = orchestrator.migrate_all(dry_run=False, tables=['skus'])
    
    assert report.total_inserted() == 2
    assert report.total_errors() == 0
    
    # Verify SKUs in database
    repos = RepositoryFactory(test_db)
    sku1 = repos.skus().get('SKU001')
    assert sku1 is not None
    assert sku1['description'] == 'Test Product 1'
    assert sku1['moq'] == 10
    assert sku1['lead_time_days'] == 7
    
    sku2 = repos.skus().get('SKU002')
    assert sku2['moq'] == 5
    assert sku2['lead_time_days'] == 14


def test_migrate_skus_idempotent(test_db, temp_csv_dir, sample_skus_csv):
    """Test SKU migration is idempotent (no duplicates on re-run)"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    # First run
    report1 = orchestrator.migrate_all(dry_run=False, tables=['skus'])
    assert report1.total_inserted() == 2
    
    # Second run (should skip existing SKUs)
    report2 = orchestrator.migrate_all(dry_run=False, tables=['skus'])
    assert report2.table_stats['skus'].inserted == 0
    assert report2.table_stats['skus'].skipped == 2
    
    # Verify no duplicates
    cursor = test_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM skus")
    count = cursor.fetchone()[0]
    assert count == 2


def test_migrate_skus_dry_run(test_db, temp_csv_dir, sample_skus_csv):
    """Test dry-run mode doesn't insert data"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    report = orchestrator.migrate_all(dry_run=True, tables=['skus'])
    
    assert report.dry_run is True
    assert report.total_inserted() == 2  # Counted, but not actually inserted
    
    # Verify database is empty
    cursor = test_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM skus")
    count = cursor.fetchone()[0]
    assert count == 0


# ============================================================
# Test MigrationOrchestrator - Transactions
# ============================================================

def test_migrate_transactions_success(test_db, temp_csv_dir, sample_skus_csv, sample_transactions_csv):
    """Test migrating transactions.csv (requires SKUs first)"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    # Migrate SKUs first (FK dependency)
    orchestrator.migrate_all(dry_run=False, tables=['skus'])
    
    # Then migrate transactions
    report = orchestrator.migrate_all(dry_run=False, tables=['transactions'])
    
    assert report.total_inserted() == 3
    assert report.total_errors() == 0
    
    # Verify transactions in database
    repos = RepositoryFactory(test_db)
    txns = repos.ledger().list_transactions('SKU001', limit=10)
    assert len(txns) == 2  # SNAPSHOT + SALE
    
    assert txns[0]['event'] == 'SNAPSHOT'
    assert txns[0]['qty'] == 100
    assert txns[1]['event'] == 'SALE'
    assert txns[1]['qty'] == -10


def test_migrate_transactions_fk_violation(test_db, temp_csv_dir, sample_transactions_csv):
    """Test transactions migration fails with FK violation (SKU not found)"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    # Don't migrate SKUs first → FK violation
    report = orchestrator.migrate_all(dry_run=False, tables=['transactions'])
    
    # Should have errors (foreign key violations)
    assert report.total_errors() > 0


def test_migrate_transactions_invalid_data(test_db, temp_csv_dir, sample_skus_csv, invalid_transactions_csv):
    """Test transactions migration with invalid data"""
    # Move invalid CSV to expected location
    shutil.copy(invalid_transactions_csv, temp_csv_dir / "transactions.csv")
    
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    # Migrate SKUs first
    orchestrator.migrate_all(dry_run=False, tables=['skus'])
    
    # Migrate transactions (should have validation errors)
    report = orchestrator.migrate_all(dry_run=False, tables=['transactions'])
    
    stats = report.table_stats['transactions']
    assert stats.errors > 0
    assert len(stats.validation_errors) > 0
    
    # Check that errors were detected (any validation error is acceptable)
    # Could be date format, event type, qty format, or FK violation
    assert stats.errors >= 1


# ============================================================
# Test MigrationOrchestrator - Sales
# ============================================================

def test_migrate_sales_success(test_db, temp_csv_dir, sample_skus_csv, sample_sales_csv):
    """Test migrating sales.csv"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    # Migrate SKUs first
    orchestrator.migrate_all(dry_run=False, tables=['skus'])
    
    # Migrate sales
    report = orchestrator.migrate_all(dry_run=False, tables=['sales'])
    
    assert report.total_inserted() == 3
    assert report.total_errors() == 0
    
    # Verify sales in database
    cursor = test_db.cursor()
    cursor.execute("SELECT * FROM sales WHERE sku = 'SKU001' ORDER BY date")
    rows = cursor.fetchall()
    
    assert len(rows) == 2
    assert rows[0][2] == 10  # qty_sold
    assert rows[1][2] == 15


def test_migrate_sales_idempotent(test_db, temp_csv_dir, sample_skus_csv, sample_sales_csv):
    """Test sales migration is idempotent (PK: date, sku)"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    orchestrator.migrate_all(dry_run=False, tables=['skus'])
    
    # First run
    report1 = orchestrator.migrate_all(dry_run=False, tables=['sales'])
    assert report1.total_inserted() == 3
    
    # Second run (should skip existing)
    report2 = orchestrator.migrate_all(dry_run=False, tables=['sales'])
    assert report2.table_stats['sales'].inserted == 0
    assert report2.table_stats['sales'].skipped == 3


# ============================================================
# Test MigrationOrchestrator - Settings & Holidays (JSON)
# ============================================================

def test_migrate_settings_json(test_db, temp_csv_dir, sample_settings_json):
    """Test migrating settings.json"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    report = orchestrator.migrate_all(dry_run=False, tables=['settings'])
    
    assert report.total_inserted() == 1
    assert report.total_errors() == 0
    
    # Verify settings in database
    cursor = test_db.cursor()
    cursor.execute("SELECT settings_json FROM settings WHERE id = 1")
    row = cursor.fetchone()
    
    assert row is not None
    settings = json.loads(row[0])
    assert settings['default_lead_time'] == 7
    assert settings['warehouse_id'] == 'WH001'


def test_migrate_holidays_json(test_db, temp_csv_dir, sample_holidays_json):
    """Test migrating holidays.json"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    report = orchestrator.migrate_all(dry_run=False, tables=['holidays'])
    
    assert report.total_inserted() == 1
    assert report.total_errors() == 0
    
    # Verify holidays in database
    cursor = test_db.cursor()
    cursor.execute("SELECT holidays_json FROM holidays WHERE id = 1")
    row = cursor.fetchone()
    
    assert row is not None
    holidays = json.loads(row[0])
    assert len(holidays['holidays']) == 2
    assert holidays['holidays'][0]['name'] == 'New Year'


# ============================================================
# Test Full Migration
# ============================================================

def test_full_migration_order(test_db, temp_csv_dir, sample_skus_csv, sample_transactions_csv, sample_sales_csv):
    """Test full migration respects FK dependency order"""
    orchestrator = MigrationOrchestrator(test_db, csv_dir=temp_csv_dir)
    
    # Migrate in correct order
    report = orchestrator.migrate_all(dry_run=False, tables=['skus', 'transactions', 'sales'])
    
    assert report.total_errors() == 0
    assert 'skus' in report.tables_migrated
    assert 'transactions' in report.tables_migrated
    assert 'sales' in report.tables_migrated
    
    # Verify data
    repos = RepositoryFactory(test_db)
    assert repos.skus().get('SKU001') is not None
    
    cursor = test_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM transactions")
    assert cursor.fetchone()[0] == 3
    
    cursor.execute("SELECT COUNT(*) FROM sales")
    assert cursor.fetchone()[0] == 3


def test_migration_report_structure():
    """Test MigrationReport data structure"""
    report = MigrationReport(dry_run=True)
    
    stats1 = MigrationStats(table='skus', total_rows=10, inserted=10, skipped=0, errors=0)
    stats2 = MigrationStats(table='transactions', total_rows=50, inserted=45, skipped=5, errors=0)
    
    report.add_stats(stats1)
    report.add_stats(stats2)
    report.completed_at = datetime.now()
    
    assert report.total_inserted() == 55
    assert report.total_errors() == 0
    assert not report.has_errors()
    assert 'skus' in report.tables_migrated
    assert len(report.table_stats) == 2


def test_migration_report_with_errors():
    """Test MigrationReport with validation errors"""
    report = MigrationReport()
    
    stats = MigrationStats(table='transactions', total_rows=10, inserted=8, errors=2)
    stats.validation_errors.append(ValidationError(3, 'date', '01/01/2024', 'Invalid date format'))
    stats.validation_errors.append(ValidationError(7, 'event', 'INVALID', 'Invalid event type'))
    
    report.add_stats(stats)
    
    assert report.total_errors() == 2
    assert report.has_errors()
    assert len(report.table_stats['transactions'].validation_errors) == 2


# ============================================================
# Run Tests
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
