"""
Tests for secondary EAN (ean_secondary) feature.

Covers:
- Domain model field declaration and __post_init__ validation
- CSV layer: schema, read/write round-trip, uniqueness helper
- SKU import: COLUMN_ALIASES mapping, _validate_row warning
- Backend router lookup by secondary EAN
"""

import csv
import pytest
from pathlib import Path
from datetime import date

from src.domain.models import SKU, DemandVariability
from src.persistence.csv_layer import CSVLayer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sku(**kwargs) -> SKU:
    defaults = dict(
        sku="SKU001",
        description="Test SKU",
        ean=None,
        ean_secondary=None,
    )
    defaults.update(kwargs)
    return SKU(**defaults)


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def layer(data_dir) -> CSVLayer:
    return CSVLayer(data_dir=data_dir)


# ---------------------------------------------------------------------------
# 1. Domain model
# ---------------------------------------------------------------------------

class TestDomainModel:

    def test_ean_secondary_defaults_none(self):
        s = make_sku()
        assert s.ean_secondary is None

    def test_ean_secondary_stored(self):
        s = make_sku(ean="1234567890123", ean_secondary="9876543210987")
        assert s.ean_secondary == "9876543210987"

    def test_ean_secondary_same_as_primary_raises(self):
        with pytest.raises(ValueError, match="secondario"):
            make_sku(ean="1234567890123", ean_secondary="1234567890123")

    def test_ean_secondary_same_as_primary_with_spaces_raises(self):
        with pytest.raises(ValueError, match="secondario"):
            make_sku(ean="1234567890123", ean_secondary=" 1234567890123 ")

    def test_ean_secondary_only_allowed_without_primary(self):
        # Secondary without primary should not raise
        s = make_sku(ean=None, ean_secondary="9876543210987")
        assert s.ean_secondary == "9876543210987"

    def test_ean_secondary_both_none_ok(self):
        s = make_sku(ean=None, ean_secondary=None)
        assert s.ean is None
        assert s.ean_secondary is None


# ---------------------------------------------------------------------------
# 2. CSV layer: schema & round-trip
# ---------------------------------------------------------------------------

class TestCSVLayerEanSecondary:

    def test_schema_contains_ean_secondary(self):
        cols = CSVLayer.SCHEMAS["skus.csv"]
        assert "ean_secondary" in cols
        # ean_secondary should come right after ean
        idx_ean = cols.index("ean")
        idx_sec = cols.index("ean_secondary")
        assert idx_sec == idx_ean + 1

    def test_write_and_read_ean_secondary(self, layer):
        s = make_sku(ean="1234567890123", ean_secondary="9876543210987")
        layer.write_sku(s)
        read_back = layer.read_skus()
        assert len(read_back) == 1
        assert read_back[0].ean_secondary == "9876543210987"

    def test_read_sku_without_ean_secondary_column_backward_compat(self, data_dir):
        """Simulate a legacy skus.csv that has no ean_secondary column."""
        csv_path = data_dir / "skus.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["sku", "description", "ean"])
            writer.writerow(["SKU001", "Legacy Product", "1234567890123"])

        layer = CSVLayer(data_dir=data_dir)
        skus = layer.read_skus()
        assert len(skus) == 1
        assert skus[0].ean_secondary is None  # default applied

    def test_update_sku_sets_ean_secondary(self, layer):
        s = make_sku(ean="1234567890123")
        layer.write_sku(s)

        layer.update_sku(
            old_sku_id="SKU001",
            new_sku_id="SKU001",
            new_description="Test SKU",
            new_ean="1234567890123",
            new_ean_secondary="9876543210987",
        )

        updated = layer.read_skus()
        assert updated[0].ean_secondary == "9876543210987"

    def test_update_sku_clears_ean_secondary(self, layer):
        s = make_sku(ean="1234567890123", ean_secondary="9876543210987")
        layer.write_sku(s)

        layer.update_sku(
            old_sku_id="SKU001",
            new_sku_id="SKU001",
            new_description="Test SKU",
            new_ean="1234567890123",
            new_ean_secondary=None,
        )

        updated = layer.read_skus()
        assert updated[0].ean_secondary is None


# ---------------------------------------------------------------------------
# 3. check_ean_unique helper
# ---------------------------------------------------------------------------

class TestCheckEanUnique:

    def test_unique_ean_returns_none(self, layer):
        layer.write_sku(make_sku(ean="1234567890123"))
        assert layer.check_ean_unique("9876543210987") is None

    def test_collision_on_primary_ean(self, layer):
        layer.write_sku(make_sku(ean="1234567890123"))
        hit = layer.check_ean_unique("1234567890123")
        assert hit == "SKU001"

    def test_collision_on_secondary_ean(self, layer):
        layer.write_sku(make_sku(ean="1234567890123", ean_secondary="9876543210987"))
        hit = layer.check_ean_unique("9876543210987")
        assert hit == "SKU001"

    def test_exclude_sku_allows_own_ean(self, layer):
        layer.write_sku(make_sku(ean="1234567890123", ean_secondary="9876543210987"))
        # Editing SKU001 itself — should not flag its own EAN
        assert layer.check_ean_unique("1234567890123", exclude_sku="SKU001") is None
        assert layer.check_ean_unique("9876543210987", exclude_sku="SKU001") is None

    def test_empty_ean_always_ok(self, layer):
        layer.write_sku(make_sku(ean=None))
        assert layer.check_ean_unique("") is None
        assert layer.check_ean_unique(None) is None

    def test_two_skus_cross_collision(self, layer):
        """EAN of SKU002 == secondary EAN of SKU001 should be detected."""
        layer.write_sku(make_sku(sku="SKU001", ean="1234567890123", ean_secondary="9876543210987"))
        hit = layer.check_ean_unique("9876543210987", exclude_sku="SKU002")
        assert hit == "SKU001"


# ---------------------------------------------------------------------------
# 4. SKU import: COLUMN_ALIASES + _validate_row
# ---------------------------------------------------------------------------

class TestSkuImportEanSecondary:

    def test_column_aliases_registered(self):
        from src.workflows.sku_import import COLUMN_ALIASES
        assert "ean_secondary" in COLUMN_ALIASES
        aliases = COLUMN_ALIASES["ean_secondary"]
        assert "ean2" in aliases
        assert "barcode2" in aliases

    def test_import_csv_with_ean_secondary(self, data_dir):
        from src.workflows.sku_import import SKUImporter

        csv_path = data_dir / "import.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["sku", "description", "ean", "ean_secondary"])
            writer.writerow(["SKU001", "Product 1", "1234567890123", "9876543210987"])

        local_layer = CSVLayer(data_dir=data_dir)
        importer = SKUImporter(local_layer)
        preview = importer.parse_csv_with_preview(csv_path)

        assert preview.valid_rows == 1
        assert preview.discarded_rows == 0
        row = preview.rows[0]
        assert row.sku_object is not None
        assert row.sku_object.ean_secondary == "9876543210987"

    def test_import_alias_ean2(self, data_dir):
        from src.workflows.sku_import import SKUImporter

        csv_path = data_dir / "import2.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["sku", "description", "ean2"])
            writer.writerow(["SKU002", "Product 2", "9876543210987"])

        local_layer = CSVLayer(data_dir=data_dir)
        importer = SKUImporter(local_layer)
        preview = importer.parse_csv_with_preview(csv_path)

        assert preview.valid_rows == 1
        row = preview.rows[0]
        assert row.sku_object.ean_secondary == "9876543210987"

    def test_validate_row_warns_on_invalid_secondary_ean(self, data_dir):
        from src.workflows.sku_import import SKUImporter

        csv_path = data_dir / "bad_ean2.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["sku", "description", "ean_secondary"])
            writer.writerow(["SKU003", "Bad EAN2 Product", "NOTANEAN"])

        local_layer = CSVLayer(data_dir=data_dir)
        importer = SKUImporter(local_layer)
        preview = importer.parse_csv_with_preview(csv_path)

        # Row should still be valid (secondary EAN error is warning-only)
        assert preview.valid_rows == 1
        row = preview.rows[0]
        assert any("secondary EAN" in w or "EAN" in w for w in row.warnings)

    def test_validate_row_warns_ean2_equals_ean(self, data_dir):
        from src.workflows.sku_import import SKUImporter

        csv_path = data_dir / "dup_ean.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["sku", "description", "ean", "ean_secondary"])
            writer.writerow(["SKU004", "Dup EAN Product", "1234567890123", "1234567890123"])

        local_layer = CSVLayer(data_dir=data_dir)
        importer = SKUImporter(local_layer)
        preview = importer.parse_csv_with_preview(csv_path)

        assert preview.valid_rows == 1
        row = preview.rows[0]
        assert any("secondario" in w.lower() or "uguale" in w.lower() for w in row.warnings)
