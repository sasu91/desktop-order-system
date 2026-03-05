-- Migration 006: Add ean_secondary column to skus table
-- Adds a secondary EAN/GTIN field per SKU (alternative barcode).
ALTER TABLE skus ADD COLUMN ean_secondary TEXT DEFAULT '';
