-- migrations/001_initial_schema.sql
-- Description: Initial schema from CSV migration (Fase 1)
-- Version: 1
-- Author: Tech Lead
-- Date: 2026-02-17

-- IMPORTANT: This script should be executed with PRAGMA foreign_keys=ON
-- and in a transaction context (handled by migration runner)

BEGIN TRANSACTION;

-- ============================================================
-- 1. Schema Version Tracking
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL,
    checksum TEXT
);

-- Seed initial version (will be updated at end of script)

-- ============================================================
-- 2. Core Tables
-- ============================================================

-- 2.1 SKUs (Product Master Data)
CREATE TABLE skus (
    sku TEXT PRIMARY KEY NOT NULL,
    description TEXT NOT NULL,
    ean TEXT,
    
    -- Order parameters
    moq INTEGER NOT NULL DEFAULT 1 CHECK(moq >= 1),
    pack_size INTEGER NOT NULL DEFAULT 1 CHECK(pack_size >= 1),
    lead_time_days INTEGER NOT NULL DEFAULT 7 CHECK(lead_time_days >= 0 AND lead_time_days <= 365),
    review_period INTEGER NOT NULL DEFAULT 7 CHECK(review_period >= 0),
    safety_stock INTEGER NOT NULL DEFAULT 0 CHECK(safety_stock >= 0),
    shelf_life_days INTEGER NOT NULL DEFAULT 0 CHECK(shelf_life_days >= 0),
    
    -- Shelf life operational parameters
    min_shelf_life_days INTEGER NOT NULL DEFAULT 0 CHECK(min_shelf_life_days >= 0),
    waste_penalty_mode TEXT DEFAULT '' CHECK(waste_penalty_mode IN ('', 'soft', 'hard')),
    waste_penalty_factor REAL NOT NULL DEFAULT 0.0 CHECK(waste_penalty_factor >= 0.0 AND waste_penalty_factor <= 1.0),
    waste_risk_threshold REAL NOT NULL DEFAULT 0.0 CHECK(waste_risk_threshold >= 0.0 AND waste_risk_threshold <= 100.0),
    
    max_stock INTEGER NOT NULL DEFAULT 999 CHECK(max_stock >= 0),
    reorder_point INTEGER NOT NULL DEFAULT 10 CHECK(reorder_point >= 0),
    demand_variability TEXT NOT NULL DEFAULT 'STABLE' CHECK(demand_variability IN ('STABLE', 'LOW', 'HIGH', 'SEASONAL')),
    
    -- Hierarchical classification
    category TEXT DEFAULT '',
    department TEXT DEFAULT '',
    
    -- OOS parameters
    oos_boost_percent REAL NOT NULL DEFAULT 0.0 CHECK(oos_boost_percent >= 0.0 AND oos_boost_percent <= 100.0),
    oos_detection_mode TEXT DEFAULT '' CHECK(oos_detection_mode IN ('', 'strict', 'relaxed')),
    oos_popup_preference TEXT NOT NULL DEFAULT 'ask' CHECK(oos_popup_preference IN ('ask', 'always_yes', 'always_no')),
    
    -- Forecast method selection
    forecast_method TEXT DEFAULT '' CHECK(forecast_method IN ('', 'simple', 'monte_carlo')),
    
    -- Monte Carlo override parameters
    mc_distribution TEXT DEFAULT '' CHECK(mc_distribution IN ('', 'empirical', 'normal', 'lognormal', 'residuals')),
    mc_n_simulations INTEGER NOT NULL DEFAULT 0 CHECK(mc_n_simulations >= 0),
    mc_random_seed INTEGER NOT NULL DEFAULT 0 CHECK(mc_random_seed >= 0),
    mc_output_stat TEXT DEFAULT '' CHECK(mc_output_stat IN ('', 'mean', 'percentile')),
    mc_output_percentile INTEGER NOT NULL DEFAULT 0 CHECK(mc_output_percentile >= 0 AND mc_output_percentile <= 100),
    mc_horizon_mode TEXT DEFAULT '' CHECK(mc_horizon_mode IN ('', 'auto', 'custom')),
    mc_horizon_days INTEGER NOT NULL DEFAULT 0 CHECK(mc_horizon_days >= 0),
    
    -- Assortment status
    in_assortment INTEGER NOT NULL DEFAULT 1 CHECK(in_assortment IN (0, 1)),
    
    -- Service level override
    target_csl REAL NOT NULL DEFAULT 0.0 CHECK(target_csl >= 0.0 AND target_csl <= 0.9999),
    
    -- Audit metadata
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2.2 Transactions (Stock Ledger - Append-Only)
CREATE TABLE transactions (
    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    sku TEXT NOT NULL,
    event TEXT NOT NULL CHECK(event IN (
        'SNAPSHOT', 'ORDER', 'RECEIPT', 'SALE', 'WASTE', 'ADJUST', 'UNFULFILLED',
        'SKU_EDIT', 'EXPORT_LOG', 'ASSORTMENT_IN', 'ASSORTMENT_OUT'
    )),
    qty INTEGER NOT NULL,
    receipt_date TEXT,
    note TEXT DEFAULT '',
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE RESTRICT
);

-- 2.3 Sales (Daily Aggregated Sales)
CREATE TABLE sales (
    date TEXT NOT NULL,
    sku TEXT NOT NULL,
    qty_sold INTEGER NOT NULL CHECK(qty_sold >= 0),
    promo_flag INTEGER NOT NULL DEFAULT 0 CHECK(promo_flag IN (0, 1)),
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    PRIMARY KEY (date, sku),
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE CASCADE
);

-- 2.4 Order Logs
CREATE TABLE order_logs (
    order_id TEXT PRIMARY KEY NOT NULL,
    date TEXT NOT NULL,
    sku TEXT NOT NULL,
    qty_ordered INTEGER NOT NULL CHECK(qty_ordered > 0),
    qty_received INTEGER NOT NULL DEFAULT 0 CHECK(qty_received >= 0),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'PARTIAL', 'RECEIVED')),
    receipt_date TEXT,
    
    -- Promo prebuild metadata
    promo_prebuild_enabled INTEGER NOT NULL DEFAULT 0 CHECK(promo_prebuild_enabled IN (0, 1)),
    promo_start_date TEXT,
    target_open_qty INTEGER NOT NULL DEFAULT 0,
    projected_stock_on_promo_start INTEGER NOT NULL DEFAULT 0,
    prebuild_delta_qty INTEGER NOT NULL DEFAULT 0,
    prebuild_qty INTEGER NOT NULL DEFAULT 0,
    prebuild_coverage_days INTEGER NOT NULL DEFAULT 0,
    prebuild_distribution_note TEXT DEFAULT '',
    
    -- Event uplift metadata
    event_uplift_active INTEGER NOT NULL DEFAULT 0 CHECK(event_uplift_active IN (0, 1)),
    event_delivery_date TEXT,
    event_reason TEXT DEFAULT '',
    event_u_store_day REAL NOT NULL DEFAULT 1.0,
    event_quantile REAL NOT NULL DEFAULT 0.0 CHECK(event_quantile >= 0.0 AND event_quantile <= 1.0),
    event_fallback_level TEXT DEFAULT '',
    event_beta_i REAL NOT NULL DEFAULT 1.0,
    event_beta_fallback_level TEXT DEFAULT '',
    event_m_i REAL NOT NULL DEFAULT 1.0,
    event_explain_short TEXT DEFAULT '',
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE CASCADE,
    CHECK(qty_received <= qty_ordered)
);

-- 2.5 Receiving Logs
CREATE TABLE receiving_logs (
    document_id TEXT PRIMARY KEY NOT NULL,
    receipt_id TEXT,
    date TEXT NOT NULL,
    sku TEXT NOT NULL,
    qty_received INTEGER NOT NULL CHECK(qty_received > 0),
    receipt_date TEXT NOT NULL,
    order_ids TEXT DEFAULT '',
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE CASCADE
);

-- 2.6 Order-Receipt Junction Table (Normalized)
CREATE TABLE order_receipts (
    order_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    
    PRIMARY KEY (order_id, document_id),
    FOREIGN KEY (order_id) REFERENCES order_logs(order_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES receiving_logs(document_id) ON DELETE CASCADE
);

-- 2.7 Lots (Shelf Life Tracking)
CREATE TABLE lots (
    lot_id TEXT PRIMARY KEY NOT NULL,
    sku TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    qty_on_hand INTEGER NOT NULL CHECK(qty_on_hand >= 0),
    receipt_id TEXT,
    receipt_date TEXT NOT NULL,
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE CASCADE
);

-- 2.8 Promo Calendar
CREATE TABLE promo_calendar (
    promo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    store_id TEXT DEFAULT '',
    promo_flag INTEGER NOT NULL DEFAULT 1 CHECK(promo_flag IN (0, 1)),
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE CASCADE,
    CHECK(start_date <= end_date),
    UNIQUE(sku, start_date, end_date, store_id)
);

-- 2.9 KPI Daily
CREATE TABLE kpi_daily (
    sku TEXT NOT NULL,
    date TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('strict', 'relaxed')),
    
    oos_rate REAL CHECK(oos_rate >= 0.0 AND oos_rate <= 1.0),
    lost_sales_est REAL CHECK(lost_sales_est >= 0.0),
    wmape REAL CHECK(wmape >= 0.0),
    bias REAL,
    fill_rate REAL CHECK(fill_rate >= 0.0 AND fill_rate <= 1.0),
    otif_rate REAL CHECK(otif_rate >= 0.0 AND otif_rate <= 1.0),
    avg_delay_days REAL,
    n_periods INTEGER NOT NULL CHECK(n_periods >= 0),
    lookback_days INTEGER NOT NULL CHECK(lookback_days > 0),
    waste_rate REAL CHECK(waste_rate >= 0.0),  -- fraction waste/sales; 0.0 when no waste (never NULL)
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    PRIMARY KEY (sku, date, mode),
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE CASCADE
);

-- 2.10 Audit Log
CREATE TABLE audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    operation TEXT NOT NULL,
    sku TEXT,
    details TEXT DEFAULT '',
    user TEXT NOT NULL DEFAULT 'system',
    
    FOREIGN KEY (sku) REFERENCES skus(sku) ON DELETE SET NULL
);

-- 2.11 Event Uplift Rules
CREATE TABLE event_uplift_rules (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_date TEXT NOT NULL,
    reason TEXT NOT NULL,
    strength REAL NOT NULL CHECK(strength >= 0.0),
    scope_type TEXT NOT NULL CHECK(scope_type IN ('ALL', 'SKU', 'CATEGORY', 'DEPARTMENT')),
    scope_key TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    UNIQUE(delivery_date, scope_type, scope_key)
);

-- 2.12 Settings (JSON BLOB - Minimal Disruption)
CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    settings_json TEXT NOT NULL DEFAULT '{}',
    
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2.13 Holidays (JSON BLOB - Minimal Disruption)
CREATE TABLE holidays (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    holidays_json TEXT NOT NULL DEFAULT '{"holidays": []}',
    
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- 3. Indices
-- ============================================================

-- SKUs indices
CREATE INDEX idx_skus_in_assortment ON skus(in_assortment) WHERE in_assortment = 1;
CREATE INDEX idx_skus_category ON skus(category) WHERE category != '';
CREATE INDEX idx_skus_department ON skus(department) WHERE department != '';
CREATE INDEX idx_skus_demand_variability ON skus(demand_variability);

-- Transactions indices (CRITICAL for stock calculations)
CREATE INDEX idx_transactions_sku_date ON transactions(sku, date);
CREATE INDEX idx_transactions_event ON transactions(event);
CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_transactions_receipt_date ON transactions(receipt_date) WHERE receipt_date IS NOT NULL;

-- Sales indices (CRITICAL for forecast)
CREATE INDEX idx_sales_sku_date ON sales(sku, date);
CREATE INDEX idx_sales_date ON sales(date);
CREATE INDEX idx_sales_promo_flag ON sales(promo_flag) WHERE promo_flag = 1;

-- Order logs indices (CRITICAL for pipeline)
CREATE INDEX idx_order_logs_sku_status ON order_logs(sku, status);
CREATE INDEX idx_order_logs_date ON order_logs(date);
CREATE INDEX idx_order_logs_receipt_date ON order_logs(receipt_date) WHERE receipt_date IS NOT NULL;
CREATE INDEX idx_order_logs_status ON order_logs(status);

-- Receiving logs indices
CREATE INDEX idx_receiving_logs_sku ON receiving_logs(sku);
CREATE INDEX idx_receiving_logs_date ON receiving_logs(date);
CREATE INDEX idx_receiving_logs_receipt_date ON receiving_logs(receipt_date);

-- Order-receipts junction indices
CREATE INDEX idx_order_receipts_document_id ON order_receipts(document_id);

-- Lots indices (CRITICAL for FEFO)
CREATE INDEX idx_lots_sku_expiry ON lots(sku, expiry_date);
CREATE INDEX idx_lots_sku_qty ON lots(sku, qty_on_hand) WHERE qty_on_hand > 0;
CREATE INDEX idx_lots_expiry_date ON lots(expiry_date);
CREATE INDEX idx_lots_receipt_id ON lots(receipt_id) WHERE receipt_id IS NOT NULL;

-- Promo calendar indices
CREATE INDEX idx_promo_calendar_sku_dates ON promo_calendar(sku, start_date, end_date);
CREATE INDEX idx_promo_calendar_dates ON promo_calendar(start_date, end_date);
CREATE INDEX idx_promo_calendar_store_id ON promo_calendar(store_id) WHERE store_id != '';

-- KPI daily indices
CREATE INDEX idx_kpi_daily_sku_date ON kpi_daily(sku, date);
CREATE INDEX idx_kpi_daily_date ON kpi_daily(date);

-- Audit log indices
CREATE INDEX idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_log_operation ON audit_log(operation);
CREATE INDEX idx_audit_log_sku ON audit_log(sku) WHERE sku IS NOT NULL;
CREATE INDEX idx_audit_log_user ON audit_log(user);

-- Event uplift rules indices
CREATE INDEX idx_event_uplift_delivery_date ON event_uplift_rules(delivery_date);
CREATE INDEX idx_event_uplift_scope ON event_uplift_rules(scope_type, scope_key);

-- ============================================================
-- 4. Seed Initial Data
-- ============================================================

-- Seed settings and holidays single rows
INSERT INTO settings (id, settings_json) VALUES (1, '{}');
INSERT INTO holidays (id, holidays_json) VALUES (1, '{"holidays": []}');

-- ============================================================
-- 5. Update Schema Version
-- ============================================================

INSERT INTO schema_version (version, description) 
VALUES (1, 'Initial schema from CSV migration (Fase 1)');

COMMIT;

-- ============================================================
-- Post-Migration Verification Queries (Optional)
-- ============================================================

-- Verify all tables created
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;

-- Verify schema version
SELECT * FROM schema_version;

-- Verify foreign keys enabled (must be set in connection PRAGMA)
-- PRAGMA foreign_keys;  -- Should return 1

-- Count tables and indices
SELECT 
    (SELECT COUNT(*) FROM sqlite_master WHERE type='table') as tables_count,
    (SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%') as indices_count;
