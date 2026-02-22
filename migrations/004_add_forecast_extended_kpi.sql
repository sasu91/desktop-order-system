-- Migration 004: Add extended forecast KPIs to kpi_daily
--
-- Adds probabilistic forecast quality (PI80 coverage) and
-- promo/event-segmented forecast accuracy metrics.
-- All new columns are nullable (NULL = not yet computed / insufficient data).

ALTER TABLE kpi_daily
    ADD COLUMN pi80_coverage       REAL CHECK(pi80_coverage >= 0.0 AND pi80_coverage <= 1.0);

ALTER TABLE kpi_daily
    ADD COLUMN pi80_coverage_error REAL;  -- signed: > 0 over-cautious, < 0 under-confident

ALTER TABLE kpi_daily
    ADD COLUMN wmape_promo         REAL CHECK(wmape_promo >= 0.0);

ALTER TABLE kpi_daily
    ADD COLUMN bias_promo          REAL;

ALTER TABLE kpi_daily
    ADD COLUMN n_promo_points      INTEGER NOT NULL DEFAULT 0;

ALTER TABLE kpi_daily
    ADD COLUMN wmape_event         REAL CHECK(wmape_event >= 0.0);

ALTER TABLE kpi_daily
    ADD COLUMN bias_event          REAL;

ALTER TABLE kpi_daily
    ADD COLUMN n_event_points      INTEGER NOT NULL DEFAULT 0;

-- Update schema version
INSERT INTO schema_version (version, description, checksum)
VALUES (
    4,
    'Add extended forecast KPIs (PI80 coverage, promo/event segmented accuracy)',
    'sha256:004_add_forecast_extended_kpi'
);
