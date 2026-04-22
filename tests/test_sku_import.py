"""
Tests for SKU CSV import workflow.

Tests:
- CSV parsing with delimiter detection and encoding fallback
- Auto-mapping columns with aliases
- Validation (critical fields, types, ranges, enums, duplicates)
- Preview generation with valid/discarded counts
- UPSERT mode (update + insert)
- REPLACE mode (overwrite all)
- Backup and atomic write
- Audit logging
- Dirty CSV handling (malformed, missing columns, duplicates)
"""

import csv
import pytest
from pathlib import Path
from datetime import date
from src.workflows.sku_import import SKUImporter, ImportRow, ImportPreview, COLUMN_ALIASES
from src.persistence.csv_layer import CSVLayer
from src.domain.models import SKU, DemandVariability


@pytest.fixture
def temp_data_dir(tmp_path):
    """Create temporary data directory for testing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def csv_layer(temp_data_dir):
    """Create CSVLayer instance with temporary directory."""
    return CSVLayer(data_dir=temp_data_dir)


@pytest.fixture
def importer(csv_layer):
    """Create SKUImporter instance."""
    return SKUImporter(csv_layer)


@pytest.fixture
def sample_csv(tmp_path):
    """Create sample CSV file for testing."""
    csv_file = tmp_path / "sample_skus.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description", "ean", "moq", "pack_size", "lead_time_days"])
        writer.writerow(["SKU001", "Product 1", "1234567890123", "10", "5", "14"])
        writer.writerow(["SKU002", "Product 2", "9876543210987", "5", "1", "7"])
        writer.writerow(["SKU003", "Product 3", "", "1", "1", "10"])
    return csv_file


@pytest.fixture
def dirty_csv(tmp_path):
    """Create CSV with validation errors."""
    csv_file = tmp_path / "dirty_skus.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["code", "name", "moq", "lead_time"])
        writer.writerow(["", "Missing SKU", "10", "7"])  # Missing critical field
        writer.writerow(["SKU001", "", "5", "14"])  # Missing description
        writer.writerow(["SKU002", "Valid Product", "10", "7"])  # Valid
        writer.writerow(["SKU003", "Invalid MOQ", "-5", "7"])  # Invalid MOQ
        writer.writerow(["SKU002", "Duplicate", "10", "7"])  # Duplicate in file
    return csv_file


@pytest.fixture
def csv_with_aliases(tmp_path):
    """Create CSV with column aliases."""
    csv_file = tmp_path / "alias_skus.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["item_code", "product_name", "barcode", "min_order_qty", "delivery_days"])
        writer.writerow(["SKU001", "Product 1", "1234567890123", "10", "14"])
        writer.writerow(["SKU002", "Product 2", "", "5", "7"])
    return csv_file


# === Parsing Tests ===

def test_auto_detect_delimiter_comma(importer, tmp_path):
    """Test delimiter detection with comma."""
    csv_file = tmp_path / "comma.csv"
    with open(csv_file, 'w', encoding='utf-8') as f:
        f.write("sku,description\n")
        f.write("SKU001,Product 1\n")
    
    delimiter = importer.auto_detect_delimiter(csv_file)
    assert delimiter == ','


def test_auto_detect_delimiter_semicolon(importer, tmp_path):
    """Test delimiter detection with semicolon."""
    csv_file = tmp_path / "semicolon.csv"
    with open(csv_file, 'w', encoding='utf-8') as f:
        f.write("sku;description\n")
        f.write("SKU001;Product 1\n")
    
    delimiter = importer.auto_detect_delimiter(csv_file)
    assert delimiter == ';'


def test_auto_map_columns_exact_match(importer):
    """Test auto-mapping with exact column names."""
    headers = ["sku", "description", "moq", "lead_time_days"]
    mapping = importer.auto_map_columns(headers)
    
    assert mapping["sku"] == "sku"
    assert mapping["description"] == "description"
    assert mapping["moq"] == "moq"
    assert mapping["lead_time_days"] == "lead_time_days"


def test_auto_map_columns_aliases(importer):
    """Test auto-mapping with column aliases."""
    headers = ["item_code", "product_name", "min_order_qty", "delivery_days"]
    mapping = importer.auto_map_columns(headers)
    
    assert mapping["item_code"] == "sku"
    assert mapping["product_name"] == "description"
    assert mapping["min_order_qty"] == "moq"
    assert mapping["delivery_days"] == "lead_time_days"


def test_parse_csv_basic(importer, sample_csv):
    """Test basic CSV parsing with valid data."""
    preview = importer.parse_csv_with_preview(sample_csv)
    
    assert preview.total_rows == 3
    assert preview.valid_rows == 3
    assert preview.discarded_rows == 0
    assert len(preview.rows) == 3
    assert all(row.is_valid for row in preview.rows)


def test_parse_csv_with_aliases(importer, csv_with_aliases):
    """Test CSV parsing with column aliases."""
    preview = importer.parse_csv_with_preview(csv_with_aliases)
    
    assert preview.total_rows == 2
    assert preview.valid_rows == 2
    assert preview.column_mapping["item_code"] == "sku"
    assert preview.column_mapping["product_name"] == "description"
    assert preview.column_mapping["min_order_qty"] == "moq"
    assert preview.column_mapping["delivery_days"] == "lead_time_days"


def test_parse_csv_encoding_fallback(importer, tmp_path):
    """Test encoding fallback from UTF-8 to latin-1."""
    csv_file = tmp_path / "latin1.csv"
    with open(csv_file, 'w', encoding='latin-1') as f:
        f.write("sku,description\n")
        f.write("SKU001,Prodotto à\n")
    
    preview = importer.parse_csv_with_preview(csv_file)
    assert preview.total_rows == 1
    assert preview.valid_rows == 1


# === Validation Tests ===

def test_validate_critical_fields(importer, csv_layer):
    """Test validation of critical fields (SKU, description)."""
    existing_skus = set()
    seen_skus = set()
    
    # Missing SKU
    mapped_data = {"sku": "", "description": "Product"}
    errors, warnings = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("Missing critical field: sku" in e for e in errors)
    
    # Missing description
    mapped_data = {"sku": "SKU001", "description": ""}
    errors, warnings = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("Missing critical field: description" in e for e in errors)
    
    # Both present
    mapped_data = {"sku": "SKU001", "description": "Product 1"}
    errors, warnings = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert len(errors) == 0


def test_validate_integer_ranges(importer, csv_layer):
    """Test validation of integer fields with range checks."""
    existing_skus = set()
    seen_skus = set()
    
    # Valid MOQ
    mapped_data = {"sku": "SKU001", "description": "Product", "moq": "10"}
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert not any("moq" in e for e in errors)
    
    # Invalid MOQ (< 1)
    mapped_data = {"sku": "SKU001", "description": "Product", "moq": "0"}
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("moq must be >= 1" in e for e in errors)
    
    # Invalid MOQ (non-integer)
    mapped_data = {"sku": "SKU001", "description": "Product", "moq": "abc"}
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("moq must be an integer" in e for e in errors)
    
    # Valid lead_time_days
    mapped_data = {"sku": "SKU001", "description": "Product", "lead_time_days": "14"}
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert not any("lead_time_days" in e for e in errors)
    
    # Invalid lead_time_days (> 365)
    mapped_data = {"sku": "SKU001", "description": "Product", "lead_time_days": "400"}
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("lead_time_days must be <= 365" in e for e in errors)


def test_validate_float_ranges(importer, csv_layer):
    """Test validation of float fields with range checks."""
    existing_skus = set()
    seen_skus = set()
    
    # Valid waste_penalty_factor
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "waste_penalty_factor": "0.5"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert not any("waste_penalty_factor" in e for e in errors)
    
    # Invalid waste_penalty_factor (> 1.0)
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "waste_penalty_factor": "1.5"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("waste_penalty_factor must be in range" in e for e in errors)


def test_validate_enums(importer, csv_layer):
    """Test validation of enum fields."""
    existing_skus = set()
    seen_skus = set()
    
    # Valid demand_variability
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "demand_variability": "STABLE"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert not any("demand_variability" in e for e in errors)
    
    # Invalid demand_variability
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "demand_variability": "INVALID"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("demand_variability must be one of" in e for e in errors)
    
    # Valid forecast_method
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "forecast_method": "monte_carlo"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert not any("forecast_method" in e for e in errors)
    
    # Invalid forecast_method
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "forecast_method": "invalid_method"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("forecast_method must be" in e for e in errors)


def test_validate_cross_field_shelf_life(importer, csv_layer):
    """Test cross-field validation for shelf_life_days and min_shelf_life_days."""
    existing_skus = set()
    seen_skus = set()
    
    # Valid: min_shelf_life < shelf_life
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "shelf_life_days": "30",
        "min_shelf_life_days": "10"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert not any("shelf_life" in e for e in errors)
    
    # Invalid: min_shelf_life > shelf_life
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "shelf_life_days": "30",
        "min_shelf_life_days": "40"
    }
    errors, _ = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert any("min_shelf_life_days" in e and "cannot exceed" in e for e in errors)


def test_validate_ean_warning(importer, csv_layer):
    """Test EAN validation produces warning (not error)."""
    existing_skus = set()
    seen_skus = set()
    
    # Invalid EAN (warning only)
    mapped_data = {
        "sku": "SKU001",
        "description": "Product",
        "ean": "invalid_ean"
    }
    errors, warnings = importer._validate_row(mapped_data, existing_skus, seen_skus)
    assert len(errors) == 0  # No blocking errors
    assert any("Invalid EAN" in w for w in warnings)


def test_validate_duplicates_in_file(importer, dirty_csv):
    """Test detection of duplicate SKUs within the import file."""
    preview = importer.parse_csv_with_preview(dirty_csv)
    
    # SKU002 appears twice in the file
    assert "SKU002" in preview.duplicate_skus
    
    # Check that duplicate rows have error
    sku002_rows = [row for row in preview.rows if row.mapped_data.get("sku") == "SKU002"]
    # First occurrence should be valid, second should have duplicate error
    duplicate_rows = [row for row in sku002_rows if any("Duplicate SKU in file" in e for e in row.errors)]
    assert len(duplicate_rows) > 0


# === Preview Tests ===

def test_preview_counts(importer, dirty_csv):
    """Test preview generates correct counts of valid/discarded rows."""
    preview = importer.parse_csv_with_preview(dirty_csv)
    
    assert preview.total_rows == 5
    # Only SKU002 (first occurrence) should be valid
    assert preview.valid_rows >= 1
    assert preview.discarded_rows >= 4
    assert preview.discarded_rows + preview.valid_rows == preview.total_rows


def test_preview_primary_discard_reason(importer, dirty_csv):
    """Test preview identifies primary discard reason."""
    preview = importer.parse_csv_with_preview(dirty_csv)
    
    assert preview.primary_discard_reason != ""
    assert "Missing critical field" in preview.primary_discard_reason


def test_preview_limit(importer, tmp_path):
    """Test parser validates all rows even when preview_limit is provided."""
    # Create CSV with 100 rows
    csv_file = tmp_path / "large.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description"])
        for i in range(100):
            writer.writerow([f"SKU{i:03d}", f"Product {i}"])
    
    preview = importer.parse_csv_with_preview(csv_file, preview_limit=10)
    
    # Parser must validate all rows; GUI is responsible for showing first N
    assert len(preview.rows) == 100
    assert preview.total_rows == 100
    assert preview.valid_rows == 100
    assert preview.discarded_rows == 0


# === UPSERT Mode Tests ===

def test_upsert_mode_add_new(importer, csv_layer, sample_csv):
    """Test UPSERT mode adds new SKUs."""
    preview = importer.parse_csv_with_preview(sample_csv)
    result = importer.execute_import(preview, mode="UPSERT")
    
    assert result["success"] is True
    assert result["added"] == 3
    assert result["updated"] == 0
    assert result["imported"] == 3
    
    # Verify SKUs were added
    skus = csv_layer.read_skus()
    assert len(skus) == 3
    assert any(sku.sku == "SKU001" for sku in skus)


def test_upsert_mode_update_existing(importer, csv_layer, tmp_path):
    """Test UPSERT mode updates existing SKUs."""
    # Add initial SKU
    sku1 = SKU(sku="SKU001", description="Original Product", moq=5)
    csv_layer.write_sku(sku1)
    
    # Create CSV with updated SKU
    csv_file = tmp_path / "update.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description", "moq"])
        writer.writerow(["SKU001", "Updated Product", "10"])
    
    preview = importer.parse_csv_with_preview(csv_file)
    result = importer.execute_import(preview, mode="UPSERT")
    
    assert result["success"] is True
    assert result["updated"] == 1
    assert result["added"] == 0
    assert result["imported"] == 1
    
    # Verify SKU was updated
    skus = csv_layer.read_skus()
    sku_updated = next(sku for sku in skus if sku.sku == "SKU001")
    assert sku_updated.description == "Updated Product"
    assert sku_updated.moq == 10


def test_upsert_mode_mixed(importer, csv_layer, tmp_path):
    """Test UPSERT mode with both new and existing SKUs."""
    # Add initial SKU
    sku1 = SKU(sku="SKU001", description="Existing Product")
    csv_layer.write_sku(sku1)
    
    # Create CSV with 1 update + 1 new
    csv_file = tmp_path / "mixed.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description"])
        writer.writerow(["SKU001", "Updated Product"])
        writer.writerow(["SKU002", "New Product"])
    
    preview = importer.parse_csv_with_preview(csv_file)
    result = importer.execute_import(preview, mode="UPSERT")
    
    assert result["success"] is True
    assert result["updated"] == 1
    assert result["added"] == 1
    assert result["imported"] == 2
    
    # Verify both SKUs exist
    skus = csv_layer.read_skus()
    assert len(skus) == 2


# === REPLACE Mode Tests ===

def test_replace_mode_overwrites_all(importer, csv_layer, sample_csv):
    """Test REPLACE mode overwrites entire SKU file."""
    # Add initial SKU
    sku_old = SKU(sku="SKU_OLD", description="Old Product")
    csv_layer.write_sku(sku_old)
    
    # Import replaces with new SKUs
    preview = importer.parse_csv_with_preview(sample_csv)
    result = importer.execute_import(preview, mode="REPLACE", require_confirmation_on_discards=False)
    
    assert result["success"] is True
    assert result["imported"] == 3
    
    # Verify old SKU is gone
    skus = csv_layer.read_skus()
    assert len(skus) == 3
    assert not any(sku.sku == "SKU_OLD" for sku in skus)
    assert any(sku.sku == "SKU001" for sku in skus)


def test_replace_mode_confirmation_required_on_discards(importer, csv_layer, dirty_csv):
    """Test REPLACE mode requires confirmation if there are discarded rows."""
    preview = importer.parse_csv_with_preview(dirty_csv)
    result = importer.execute_import(preview, mode="REPLACE", require_confirmation_on_discards=True)
    
    # Should require confirmation, not execute
    assert result["confirmation_required"] is True
    assert result["success"] is False


def test_replace_mode_no_confirmation_if_all_valid(importer, csv_layer, sample_csv):
    """Test REPLACE mode proceeds without extra confirmation if all rows valid."""
    preview = importer.parse_csv_with_preview(sample_csv)
    result = importer.execute_import(preview, mode="REPLACE", require_confirmation_on_discards=True)
    
    # Should execute without requiring confirmation
    assert result["success"] is True
    assert result.get("confirmation_required", False) is False


# === Backup and Atomic Write Tests ===

def test_backup_created_on_replace(importer, csv_layer, sample_csv, temp_data_dir):
    """Test backup file is created before REPLACE import."""
    # Add initial SKU
    sku_old = SKU(sku="SKU_OLD", description="Old Product")
    csv_layer.write_sku(sku_old)
    
    # Import with REPLACE
    preview = importer.parse_csv_with_preview(sample_csv)
    importer.execute_import(preview, mode="REPLACE", require_confirmation_on_discards=False)
    
    # Check backup exists
    import glob
    backups = glob.glob(str(temp_data_dir / "skus.csv.backup.*"))
    assert len(backups) >= 1


def test_atomic_write_on_replace(importer, csv_layer, sample_csv, temp_data_dir):
    """Test atomic write is used for REPLACE mode."""
    preview = importer.parse_csv_with_preview(sample_csv)
    result = importer.execute_import(preview, mode="REPLACE", require_confirmation_on_discards=False)
    
    assert result["success"] is True
    
    # Verify file exists and is valid
    skus_file = temp_data_dir / "skus.csv"
    assert skus_file.exists()
    
    # Verify content is correct
    skus = csv_layer.read_skus()
    assert len(skus) == 3


# === Audit Logging Tests ===

def test_audit_log_created(importer, csv_layer, sample_csv):
    """Test audit log entry is created after import."""
    preview = importer.parse_csv_with_preview(sample_csv)
    importer.execute_import(preview, mode="UPSERT")
    
    # Manually log (GUI would call this)
    csv_layer.log_import_audit(
        source_file=sample_csv.name,
        mode="UPSERT",
        total_rows=preview.total_rows,
        imported=preview.valid_rows,
        discarded=preview.discarded_rows,
        user="test"
    )
    
    # Verify audit log
    audit_logs = csv_layer.read_audit_log()
    import_logs = [log for log in audit_logs if log.operation == "SKU_IMPORT"]
    assert len(import_logs) >= 1
    
    latest_log = import_logs[0]
    assert "UPSERT" in latest_log.details
    assert sample_csv.name in latest_log.details


def test_export_discard_details(importer, csv_layer, dirty_csv, tmp_path):
    """Test export of discarded row details to CSV."""
    preview = importer.parse_csv_with_preview(dirty_csv)
    
    output_file = tmp_path / "errors.csv"
    importer.export_discard_details(preview, output_file)
    
    assert output_file.exists()
    
    # Verify content
    with open(output_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
        
        assert len(rows) > 1  # Header + at least 1 error row
        assert rows[0] == ["Row Number", "SKU", "Description", "Errors", "Warnings"]


# === Dirty CSV Tests ===

def test_missing_columns_handled_gracefully(importer, tmp_path):
    """Test CSV with missing columns doesn't crash."""
    csv_file = tmp_path / "missing_cols.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description"])  # Only 2 columns
        writer.writerow(["SKU001", "Product 1"])
    
    preview = importer.parse_csv_with_preview(csv_file)
    
    # Should parse successfully with defaults for missing columns
    assert preview.total_rows == 1
    assert preview.valid_rows == 1


def test_extra_columns_ignored(importer, tmp_path):
    """Test CSV with extra columns are ignored."""
    csv_file = tmp_path / "extra_cols.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description", "extra1", "extra2"])
        writer.writerow(["SKU001", "Product 1", "ignored", "also ignored"])
    
    preview = importer.parse_csv_with_preview(csv_file)
    
    assert preview.total_rows == 1
    assert preview.valid_rows == 1
    
    # Verify SKU created without extra columns
    sku_obj = preview.rows[0].sku_object
    assert sku_obj.sku == "SKU001"
    assert sku_obj.description == "Product 1"


def test_empty_csv_handled(importer, tmp_path):
    """Test empty CSV file doesn't crash."""
    csv_file = tmp_path / "empty.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description"])  # Header only
    
    preview = importer.parse_csv_with_preview(csv_file)
    
    assert preview.total_rows == 0
    assert preview.valid_rows == 0
    assert preview.discarded_rows == 0


def test_boolean_parsing_in_assortment(importer, tmp_path):
    """Test boolean parsing for in_assortment field."""
    csv_file = tmp_path / "bool_test.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description", "in_assortment"])
        writer.writerow(["SKU001", "Active", "true"])
        writer.writerow(["SKU002", "Inactive", "false"])
        writer.writerow(["SKU003", "OneValue", "1"])
        writer.writerow(["SKU004", "ZeroValue", "0"])
    
    preview = importer.parse_csv_with_preview(csv_file)
    
    assert preview.valid_rows == 4
    
    sku1 = next(row.sku_object for row in preview.rows if row.mapped_data["sku"] == "SKU001")
    assert sku1.in_assortment is True
    
    sku2 = next(row.sku_object for row in preview.rows if row.mapped_data["sku"] == "SKU002")
    assert sku2.in_assortment is False
    
    sku3 = next(row.sku_object for row in preview.rows if row.mapped_data["sku"] == "SKU003")
    assert sku3.in_assortment is True
    
    sku4 = next(row.sku_object for row in preview.rows if row.mapped_data["sku"] == "SKU004")
    assert sku4.in_assortment is False


def test_no_valid_rows_import_fails(importer, csv_layer, tmp_path):
    """Test import fails gracefully when no valid rows exist."""
    csv_file = tmp_path / "all_invalid.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description"])
        writer.writerow(["", "Missing SKU"])  # Invalid
    
    preview = importer.parse_csv_with_preview(csv_file)
    result = importer.execute_import(preview, mode="UPSERT")
    
    assert result["success"] is False
    assert "No valid SKUs to import" in result["errors"]


def test_auto_map_columns_famiglia_aliases(importer):
    """famiglia/sotto_famiglia column names must map to department/category."""
    mapping = importer.auto_map_columns(["sku", "description", "famiglia", "sotto_famiglia"])
    assert mapping["famiglia"] == "department"
    assert mapping["sotto_famiglia"] == "category"


def test_auto_map_columns_family_aliases(importer):
    """family/sub_family (English) aliases must also resolve."""
    mapping = importer.auto_map_columns(["sku", "description", "family", "sub_family"])
    assert mapping["family"] == "department"
    assert mapping["sub_family"] == "category"


def test_import_csv_with_famiglia_columns(importer, csv_layer, tmp_path):
    """CSV files using famiglia/sotto_famiglia headers must populate department/category."""
    csv_file = tmp_path / "classificazione.csv"
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "description", "famiglia", "sotto_famiglia"])
        writer.writerow(["VERD001", "Radicchio Palla", "Verdura", "Radicchio"])
        writer.writerow(["LATT001", "Parmigiano", "Latticini", "Formaggi"])
    
    preview = importer.parse_csv_with_preview(csv_file)
    assert preview.valid_rows == 2
    
    result = importer.execute_import(preview, mode="UPSERT")
    assert result["success"] is True
    
    skus = {s.sku: s for s in csv_layer.read_skus()}
    assert skus["VERD001"].department == "Verdura"
    assert skus["VERD001"].category == "Radicchio"
    assert skus["LATT001"].department == "Latticini"
    assert skus["LATT001"].category == "Formaggi"
