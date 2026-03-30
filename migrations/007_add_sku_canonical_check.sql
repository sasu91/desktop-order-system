-- Migration 007: Rebuild tables to add missing columns:
--   skus: category, in_assortment, created_at, updated_at
--   transactions: run_id (new col, DEFAULT '')
--   receiving_logs: created_at
-- Also fixes column alignment after migrations 001-006.
--
-- NOTE: DB-level CHECK constraints for canonical SKU format are intentionally
-- omitted — existing production data contains non-canonical SKU codes
-- (e.g. 'BIRRA_LAGER', 'ACQUA_FRIZZANTE'). Format enforcement is handled
-- at the application layer (ReceiptLine.sku_must_be_canonical in schemas.py).
--
-- SQLite does not support ADD CONSTRAINT on existing tables. We use CREATE TABLE ...
-- with INSERT INTO ... SELECT ... + rename approach.
-- IMPORTANT: Foreign key checks are disabled during migration to avoid ordering issues.

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- ============================================================
-- 1. skus
-- ============================================================
CREATE TABLE IF NOT EXISTS skus_new (
    sku                         TEXT PRIMARY KEY NOT NULL,
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
    department                  TEXT    NOT NULL DEFAULT '',
    category                    TEXT    NOT NULL DEFAULT '',
    in_assortment               INTEGER NOT NULL DEFAULT 1,
    created_at                  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at                  TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO skus_new (
    sku, description, ean, ean_secondary, moq, pack_size, lead_time_days,
    review_period, safety_stock, shelf_life_days, min_shelf_life_days,
    max_stock, reorder_point, waste_penalty_factor, waste_penalty_mode,
    waste_risk_threshold, oos_boost_percent, oos_detection_mode,
    oos_popup_preference, target_csl, demand_variability, forecast_method,
    mc_n_simulations, mc_random_seed, mc_output_stat, mc_output_percentile,
    mc_distribution, mc_horizon_days, mc_horizon_mode, has_expiry_label,
    department, category, in_assortment, created_at, updated_at
)
SELECT
    sku, description, ean, ean_secondary, moq, pack_size, lead_time_days,
    review_period, safety_stock, shelf_life_days, min_shelf_life_days,
    max_stock, reorder_point, waste_penalty_factor, waste_penalty_mode,
    waste_risk_threshold, oos_boost_percent, oos_detection_mode,
    oos_popup_preference, target_csl, demand_variability, forecast_method,
    mc_n_simulations, mc_random_seed, mc_output_stat, mc_output_percentile,
    mc_distribution, mc_horizon_days, mc_horizon_mode, has_expiry_label,
    department, category, in_assortment, created_at, updated_at
FROM skus;

DROP TABLE skus;
ALTER TABLE skus_new RENAME TO skus;

-- ============================================================
-- 2. transactions
-- ============================================================
CREATE TABLE IF NOT EXISTS transactions_new (
    transaction_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    sku             TEXT    NOT NULL,
    event           TEXT    NOT NULL,
    qty             INTEGER NOT NULL,
    receipt_date    TEXT    NOT NULL DEFAULT '',
    note            TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    run_id          TEXT    NOT NULL DEFAULT '',
    FOREIGN KEY (sku) REFERENCES skus(sku)
);

INSERT INTO transactions_new (
    transaction_id, date, sku, event, qty, receipt_date, note, created_at
)
SELECT
    transaction_id, date, sku, event, qty, receipt_date, note, created_at
FROM transactions;

DROP TABLE transactions;
ALTER TABLE transactions_new RENAME TO transactions;

-- ============================================================
-- 3. order_logs
-- ============================================================
CREATE TABLE IF NOT EXISTS order_logs_new (
    order_id                    TEXT    NOT NULL,
    date                        TEXT    NOT NULL,
    sku                         TEXT    NOT NULL,
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
    created_at                  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at                  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (order_id),
    FOREIGN KEY (sku) REFERENCES skus(sku)
);

INSERT INTO order_logs_new (
    order_id, date, sku, qty_ordered, qty_received, status, receipt_date,
    promo_prebuild_enabled, promo_start_date, target_open_qty,
    projected_stock_on_promo_start, prebuild_delta_qty, prebuild_qty,
    prebuild_coverage_days, prebuild_distribution_note, event_uplift_active,
    event_delivery_date, event_reason, event_u_store_day, event_quantile,
    event_fallback_level, event_beta_i, event_beta_fallback_level,
    event_m_i, event_explain_short, created_at, updated_at
)
SELECT
    order_id, date, sku, qty_ordered, qty_received, status, receipt_date,
    promo_prebuild_enabled, promo_start_date, target_open_qty,
    projected_stock_on_promo_start, prebuild_delta_qty, prebuild_qty,
    prebuild_coverage_days, prebuild_distribution_note, event_uplift_active,
    event_delivery_date, event_reason, event_u_store_day, event_quantile,
    event_fallback_level, event_beta_i, event_beta_fallback_level,
    event_m_i, event_explain_short, created_at, updated_at
FROM order_logs;

DROP TABLE order_logs;
ALTER TABLE order_logs_new RENAME TO order_logs;

-- ============================================================
-- 4. receiving_logs
-- ============================================================
CREATE TABLE IF NOT EXISTS receiving_logs_new (
    document_id     TEXT    NOT NULL,
    receipt_id      TEXT,
    date            TEXT    NOT NULL,
    sku             TEXT    NOT NULL,
    qty_received    INTEGER NOT NULL DEFAULT 0,
    receipt_date    TEXT    NOT NULL DEFAULT '',
    order_ids       TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (document_id),
    FOREIGN KEY (sku) REFERENCES skus(sku)
);

INSERT INTO receiving_logs_new (
    document_id, receipt_id, date, sku, qty_received, receipt_date, order_ids, created_at
)
SELECT
    document_id, receipt_id, date, sku, qty_received, receipt_date, order_ids, created_at
FROM receiving_logs;

DROP TABLE receiving_logs;
ALTER TABLE receiving_logs_new RENAME TO receiving_logs;

COMMIT;
PRAGMA foreign_keys = ON;
