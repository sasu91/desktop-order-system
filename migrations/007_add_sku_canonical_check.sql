-- Migration 007: Add CHECK constraints to enforce canonical SKU format (exactly 7 numeric digits)
-- on all tables where sku is an operational column.
--
-- Canonical format: string matching ^\d{7}$ (e.g. '0450663').
-- CHECK constraints apply to new rows only (existing rows are NOT re-validated by SQLite
-- when the constraint is added via ADD COLUMN or table rebuild).
--
-- Strategy: recreate each table with the CHECK constraint added.
-- Existing rows that violate the constraint will cause the migration to fail,
-- so run scripts/audit_sku_canonical.py first and remediate non-canonical rows.
--
-- SQLite does not support ADD CONSTRAINT on existing tables. We use CREATE TABLE ...
-- with the new constraint + INSERT INTO ... SELECT ... + rename approach.
-- IMPORTANT: Foreign key checks are disabled during migration to avoid ordering issues.

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- ============================================================
-- 1. skus
-- ============================================================
CREATE TABLE IF NOT EXISTS skus_new (
    sku                         TEXT PRIMARY KEY NOT NULL
                                    CHECK(sku GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    description                 TEXT NOT NULL DEFAULT '',
    ean                         TEXT NOT NULL DEFAULT '',
    ean_secondary               TEXT NOT NULL DEFAULT '',
    moq                         INTEGER NOT NULL DEFAULT 1,
    pack_size                   INTEGER NOT NULL DEFAULT 1,
    lead_time_days              INTEGER NOT NULL DEFAULT 7,
    review_period               INTEGER NOT NULL DEFAULT 7,
    safety_stock                INTEGER NOT NULL DEFAULT 0,
    shelf_life_days             INTEGER NOT NULL DEFAULT 0,
    min_shelf_life_days         INTEGER NOT NULL DEFAULT 0,
    max_stock                   INTEGER NOT NULL DEFAULT 999999,
    reorder_point               INTEGER NOT NULL DEFAULT 0,
    waste_penalty_factor        REAL    NOT NULL DEFAULT 0.0,
    waste_penalty_mode          TEXT    NOT NULL DEFAULT '',
    waste_risk_threshold        REAL    NOT NULL DEFAULT 0.0,
    oos_boost_percent           REAL    NOT NULL DEFAULT 0.0,
    oos_detection_mode          TEXT    NOT NULL DEFAULT '',
    oos_popup_preference        TEXT    NOT NULL DEFAULT 'ask',
    target_csl                  REAL    NOT NULL DEFAULT 0.95,
    demand_variability          TEXT    NOT NULL DEFAULT 'MEDIUM',
    forecast_method             TEXT    NOT NULL DEFAULT '',
    mc_n_simulations            INTEGER NOT NULL DEFAULT 1000,
    mc_random_seed              INTEGER NOT NULL DEFAULT 42,
    mc_output_stat              TEXT    NOT NULL DEFAULT 'mean',
    mc_output_percentile        INTEGER NOT NULL DEFAULT 95,
    mc_distribution             TEXT    NOT NULL DEFAULT 'empirical',
    mc_horizon_days             INTEGER NOT NULL DEFAULT 0,
    mc_horizon_mode             TEXT    NOT NULL DEFAULT 'auto',
    has_expiry_label            INTEGER NOT NULL DEFAULT 0,
    department                  TEXT    NOT NULL DEFAULT ''
);

INSERT INTO skus_new SELECT * FROM skus;

DROP TABLE skus;
ALTER TABLE skus_new RENAME TO skus;

-- ============================================================
-- 2. transactions
-- ============================================================
CREATE TABLE IF NOT EXISTS transactions_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    sku             TEXT    NOT NULL
                        CHECK(sku GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    event           TEXT    NOT NULL,
    qty             INTEGER NOT NULL,
    receipt_date    TEXT    NOT NULL DEFAULT '',
    note            TEXT    NOT NULL DEFAULT '',
    run_id          TEXT    NOT NULL DEFAULT '',
    FOREIGN KEY (sku) REFERENCES skus(sku)
);

INSERT INTO transactions_new SELECT * FROM transactions;

DROP TABLE transactions;
ALTER TABLE transactions_new RENAME TO transactions;

-- ============================================================
-- 3. order_logs
-- ============================================================
CREATE TABLE IF NOT EXISTS order_logs_new (
    order_id                    TEXT    NOT NULL,
    date                        TEXT    NOT NULL,
    sku                         TEXT    NOT NULL
                                    CHECK(sku GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    qty_ordered                 INTEGER NOT NULL DEFAULT 0,
    qty_received                INTEGER NOT NULL DEFAULT 0,
    status                      TEXT    NOT NULL DEFAULT 'PENDING',
    receipt_date                TEXT    NOT NULL DEFAULT '',
    promo_prebuild_enabled      INTEGER NOT NULL DEFAULT 0,
    promo_start_date            TEXT    NOT NULL DEFAULT '',
    target_open_qty             INTEGER NOT NULL DEFAULT 0,
    projected_stock_on_promo_start INTEGER NOT NULL DEFAULT 0,
    prebuild_delta_qty          INTEGER NOT NULL DEFAULT 0,
    prebuild_qty                INTEGER NOT NULL DEFAULT 0,
    prebuild_coverage_days      INTEGER NOT NULL DEFAULT 0,
    prebuild_distribution_note  TEXT    NOT NULL DEFAULT '',
    event_uplift_active         INTEGER NOT NULL DEFAULT 0,
    event_delivery_date         TEXT    NOT NULL DEFAULT '',
    event_reason                TEXT    NOT NULL DEFAULT '',
    event_u_store_day           REAL    NOT NULL DEFAULT 1.0,
    event_quantile              REAL    NOT NULL DEFAULT 0.0,
    event_fallback_level        TEXT    NOT NULL DEFAULT '',
    event_beta_i                REAL    NOT NULL DEFAULT 1.0,
    event_beta_fallback_level   TEXT    NOT NULL DEFAULT '',
    event_m_i                   REAL    NOT NULL DEFAULT 1.0,
    event_explain_short         TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (order_id),
    FOREIGN KEY (sku) REFERENCES skus(sku)
);

INSERT INTO order_logs_new SELECT * FROM order_logs;

DROP TABLE order_logs;
ALTER TABLE order_logs_new RENAME TO order_logs;

-- ============================================================
-- 4. receiving_logs
-- ============================================================
CREATE TABLE IF NOT EXISTS receiving_logs_new (
    receipt_id      TEXT    NOT NULL,
    document_id     TEXT    NOT NULL DEFAULT '',
    date            TEXT    NOT NULL,
    sku             TEXT    NOT NULL
                        CHECK(sku GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    qty_received    INTEGER NOT NULL DEFAULT 0,
    receipt_date    TEXT    NOT NULL DEFAULT '',
    order_ids       TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (receipt_id),
    FOREIGN KEY (sku) REFERENCES skus(sku)
);

INSERT INTO receiving_logs_new SELECT * FROM receiving_logs;

DROP TABLE receiving_logs;
ALTER TABLE receiving_logs_new RENAME TO receiving_logs;

COMMIT;
PRAGMA foreign_keys = ON;
