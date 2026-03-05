-- Migration 006: Add ean_secondary column to skus table
-- Adds a secondary EAN/GTIN field per SKU (alternative barcode).
ALTER TABLE skus ADD COLUMN ean_secondary TEXT DEFAULT '';

-- Update schema version
INSERT INTO schema_version (version, description, checksum)
VALUES (
    6,
    'Add ean_secondary to skus for secondary EAN/GTIN barcode binding',
    'sha256:006_add_ean_secondary_to_skus'
);
