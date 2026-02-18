# FASE 0 — RICOGNIZIONE (Inventory e Punti di Scrittura)

**Data ricognizione**: 2026-02-17  
**Repository**: desktop-order-system (branch main)  
**Storage primario attuale**: CSV/JSON in cartella `/data`  
**Obiettivo migrazione**: SQLite in `data/app.db`

---

## 1. FILE DI STORAGE IDENTIFICATI

### CSV Files (10 file attivi)

| File | # Colonne | # Righe (sample) | Descrizione | Status |
|------|-----------|------------------|-------------|--------|
| `skus.csv` | 30 | ~3 | Anagrafica SKU (prodotti) | **CORE** |
| `transactions.csv` | 6 | ~17 | Ledger eventi stock (append-only) | **CORE** |
| `sales.csv` | 4 | ~61 | Vendite giornaliere aggregate | **CORE** |
| `order_logs.csv` | 24 | ~5 | Log ordini con metadati estesi | **CORE** |
| `receiving_logs.csv` | 7 | ~5 | Log ricevimenti (idempotenza document_id) | **CORE** |
| `lots.csv` | 6 | ~5 | Lotti con scadenza (shelf life tracking) | **CORE** |
| `promo_calendar.csv` | 5 | ~4 | Calendario promozionale | **ACTIVE** |
| `kpi_daily.csv` | 12 | ~1 | KPI giornalieri (OOS, waste, forecast) | **ACTIVE** |
| `audit_log.csv` | 5 | ~5 | Audit trail operazioni utente | **ACTIVE** |
| `event_uplift_rules.csv` | 6 | ~1 | Regole uplift domanda eventi (holidays, etc.) | **ACTIVE** |

### JSON Files (2 file configurazione)

| File | Struttura | Descrizione | Status |
|------|-----------|-------------|--------|
| `settings.json` | Mega-dict gerarchico (~20 sezioni) | Configurazione globale app (parametri ordini, MC, promo, CSL, etc.) | **CORE** |
| `holidays.json` | `{"holidays": []}` | Calendario festività (date ripetibili, scope, effect) | **ACTIVE** |

---

## 2. MAPPING DETTAGLIATO FILE → COLONNE → OPERAZIONI

### 2.1 `skus.csv` — Anagrafica SKU

**Colonne (30):**
```
sku, description, ean, moq, pack_size, lead_time_days, review_period, safety_stock, 
shelf_life_days, min_shelf_life_days, waste_penalty_mode, waste_penalty_factor, 
waste_risk_threshold, max_stock, reorder_point, demand_variability, category, department,
oos_boost_percent, oos_detection_mode, oos_popup_preference, forecast_method, 
mc_distribution, mc_n_simulations, mc_random_seed, mc_output_stat, mc_output_percentile,
mc_horizon_mode, mc_horizon_days, in_assortment, target_csl
```

**Chiave primaria candidata:** `sku` (UNIQUE, non-null)

**Letture:**
- `CSVLayer.read_skus()` → parsing completo con validazione
- `CSVLayer.sku_exists(sku)` → check esistenza
- GUI: tutte le tab per lookup SKU

**Scritture:**
- `CSVLayer.write_sku(sku)` → INSERT nuovo SKU (con auto-classificazione demand_variability)
- `CSVLayer.update_sku_object(sku, new_sku_obj)` → UPDATE completo (overwrite riga)
- `CSVLayer.delete_sku_purge(sku)` → DELETE hard (rimozione fisica riga)
- `CSVLayer.normalize_skus()` → BATCH UPDATE colonne normalizzate
- `SkuImportWorkflow` → BULK INSERT/UPDATE/REPLACE da CSV import utente
- GUI: tab "SKU Management", form modifica SKU

**Tipi attesi:**
- `sku`: TEXT (required, unique)
- `description`: TEXT (required)
- `ean`: TEXT (nullable, può essere invalido)
- `moq, pack_size, lead_time_days, review_period, safety_stock, shelf_life_days`: INTEGER ≥ 0
- `waste_penalty_factor, waste_risk_threshold, oos_boost_percent, target_csl`: FLOAT/REAL
- `demand_variability`: ENUM TEXT (STABLE, LOW, HIGH, SEASONAL)
- `forecast_method`: TEXT (simple, monte_carlo, empty)
- `in_assortment`: BOOLEAN (true/false come TEXT)

**Vincoli di unicità:**
- `sku` deve essere UNIQUE (PK naturale)
- Nessun altro vincolo UNIQUE dichiarato

**Note/Rischi:**
- Header CSV è cresciuto nel tempo (schema migrations con backup)
- File legacy (`skus_backup*.csv`) hanno colonne ridotte → migrazione schema automatica esistente
- Auto-classificazione `demand_variability` attivabile da settings → potenziale race condition se disabilitato durante import

---

### 2.2 `transactions.csv` — Ledger Eventi Stock (Append-Only)

**Colonne (6):**
```
date, sku, event, qty, receipt_date, note
```

**Chiave primaria candidata:** **NESSUNA esplicita** (append-only log, nessun transaction_id auto-generato)

**Possibile chiave surrogata:** `rowid` implicito (SQLite) o `transaction_id INTEGER PRIMARY KEY AUTOINCREMENT`

**Letture:**
- `CSVLayer.read_transactions()` → parsing completo con validazione EventType
- `StockCalculator.calculate_asof(sku, asof_date, transactions)` → filtraggio + aggregazione per AsOf
- `StockCalculator.projected_inventory_position(sku, target_date, ...)` → filtering avanzato su receipt_date
- GUI: tab "Audit Trail", analisi stock, pipeline ordini

**Scritture:**
- `CSVLayer.write_transaction(txn)` → APPEND singolo evento (con auto-FEFO per SALE/WASTE)
- `CSVLayer.write_transactions_batch(txns)` → APPEND batch (atomico con backup)
- `CSVLayer.overwrite_transactions(txns)` → REPLACE completo file (usato solo in migration/repair)
- Workflows:
  - `OrderWorkflow.confirm_order()` → genera ORDER events
  - `ReceivingWorkflow.close_receipt_by_document()` → genera RECEIPT + UNFULFILLED events
  - `DailyCloseWorkflow.close_eod()` → genera ADJUST events (FEFO lots)
  - `ExceptionManagement.revert_exception_day()` → DELETE eventi specifici (overwrite filtered)

**Tipi attesi:**
- `date`: DATE (ISO8601 TEXT "YYYY-MM-DD")
- `sku`: TEXT (FK → skus.sku)
- `event`: TEXT ENUM (SNAPSHOT, ORDER, RECEIPT, SALE, WASTE, ADJUST, UNFULFILLED, SKU_EDIT, EXPORT_LOG, ASSORTMENT_IN, ASSORTMENT_OUT)
- `qty`: INTEGER (può essere negativo per WASTE/SALE in rappresentazione positiva qty consumata)
- `receipt_date`: DATE (ISO8601 TEXT, nullable, usato solo per ORDER/RECEIPT)
- `note`: TEXT (nullable)

**Vincoli di unicità:**
- **NESSUNO** (append-only, duplicati permessi per eventi genuini)
- Idempotenza garantita solo a livello applicativo (es. receiving, non nel ledger stesso)

**Note/Rischi:**
- **Manca transaction_id**: impossibile referenziare singolo evento direttamente (es. per audit o revert puntuale)
- Revert exceptions usa match esatto su (date, sku, event, qty, note) → fragilità se note cambiano
- Auto-FEFO applicato su SALE/WASTE modifica qty lots → side-effect implicito in `write_transaction()`
- Migrazioni schema usano backup automatico prima di overwrite

---

### 2.3 `sales.csv` — Vendite Giornaliere Aggregate

**Colonne (4):**
```
date, sku, qty_sold, promo_flag
```

**Chiave primaria candidata:** `(date, sku)` UNIQUE (una riga per SKU per giorno)

**Letture:**
- `CSVLayer.read_sales()` → parsing completo con validazione
- Forecast: `monte_carlo_forecast()`, `promo_adjusted_forecast()`, fitting modelli
- Order: calcolo `daily_sales_avg` per proposal
- GUI: analisi vendite, KPI, grafici

**Scritture:**
- `CSVLayer.write_sales_record(sale)` → APPEND singola riga
- `CSVLayer.append_sales(sale)` → alias write_sales_record
- `CSVLayer.write_sales(sales_list)` → OVERWRITE completo (bulk update)
- Workflows:
  - `DailyCloseWorkflow.close_eod()` → crea/aggiorna record giornaliero (check idempotenza applicativo)
  - `enrich_sales_with_promo_flags()` → UPDATE batch promo_flag (in-place overwrite)
  - GUI: entry manuale vendite, EOD

**Tipi attesi:**
- `date`: DATE (ISO8601 TEXT)
- `sku`: TEXT (FK → skus.sku)
- `qty_sold`: INTEGER ≥ 0
- `promo_flag`: BOOLEAN (0/1 o true/false come TEXT, nullable con fallback "0")

**Vincoli di unicità:**
- `(date, sku)` dovrebbe essere UNIQUE (ma non forzato a livello CSV)
- Idempotenza daily_close: check `any(s.date == eod_date and s.sku == sku)` prima di scrivere

**Note/Rischi:**
- File legacy può mancare colonna `promo_flag` → backward compatibility con default "0"
- Overwrite completo file (`write_sales`) usato per enrichment promo → rischio corruzione se interrotto
- Nessuna validazione formale duplicati (date, sku) → possibili doppioni se daily_close eseguito 2 volte

---

### 2.4 `order_logs.csv` — Log Ordini (Esteso con Metadati)

**Colonne (24, dopo espansione 2026-02-16):**
```
order_id, date, sku, qty_ordered, qty_received, status, receipt_date,
promo_prebuild_enabled, promo_start_date, target_open_qty, projected_stock_on_promo_start,
prebuild_delta_qty, prebuild_qty, prebuild_coverage_days, prebuild_distribution_note,
event_uplift_active, event_delivery_date, event_reason, event_u_store_day, event_quantile,
event_fallback_level, event_beta_i, event_beta_fallback_level, event_m_i, event_explain_short
```

**Chiave primaria candidata:** `order_id` (UNIQUE, TEXT, formato `YYYYMMDD_###`)

**Letture:**
- `CSVLayer.read_order_logs()` → lista dict raw
- `CSVLayer.get_unfulfilled_orders(sku)` → filtraggio `qty_received < qty_ordered`
- `build_open_pipeline()` → estrazione ordini PENDING/PARTIAL per CSL policy
- GUI: tab "Ordini", tracking pipeline, KPI OTIF

**Scritture:**
- `CSVLayer.write_order_log(...)` → APPEND singolo ordine (24 parametri)
- `CSVLayer.update_order_received_qty(order_id, qty_received, status)` → UPDATE atomico con backup (rewrite completo file)
- Workflows:
  - `OrderWorkflow.confirm_order()` → genera order_id, scrive log
  - `ReceivingWorkflow.close_receipt_by_document()` → aggiorna qty_received, status

**Generazione `order_id`:**
```python
order_id_base = today.isoformat().replace("-", "")  # "YYYYMMDD"
order_id = f"{order_id_base}_{idx:03d}"  # "YYYYMMDD_001", "YYYYMMDD_002", ...
```
**Determinismo**: basato su data + indice sequenziale nel batch, **non globalmente univoco** se conferme multiple nello stesso giorno con indici sovrapposti

**Tipi attesi:**
- `order_id`: TEXT (PK naturale, formato YYYYMMDD_NNN)
- `date, receipt_date, promo_start_date, event_delivery_date`: DATE (ISO8601 TEXT, nullable)
- `sku`: TEXT (FK → skus.sku)
- `qty_ordered, qty_received, target_open_qty, projected_stock_on_promo_start, prebuild_delta_qty, prebuild_qty, prebuild_coverage_days`: INTEGER
- `status`: TEXT ENUM (PENDING, PARTIAL, RECEIVED)
- `promo_prebuild_enabled, event_uplift_active`: BOOLEAN (true/false TEXT)
- `event_u_store_day, event_quantile, event_beta_i, event_m_i`: FLOAT/REAL
- `event_fallback_level, event_beta_fallback_level, event_reason, event_explain_short, prebuild_distribution_note`: TEXT (nullable)

**Vincoli di unicità:**
- `order_id` deve essere UNIQUE (PK naturale)
- Nessuna validazione referenziale (date, sku) → ordini per SKU cancellati possono esistere

**Note/Rischi:**
- Header espanso da 7 a 24 colonne (migration automatica con backup)
- Update `qty_received` atomico ma **rewrites intero file** → lento con molti ordini, rischio lock CSV
- `order_id` collision possibile se conferme concorrenti (race condition, mitigato da indice batch)

---

### 2.5 `receiving_logs.csv` — Log Ricevimenti (Idempotenza Document-Based)

**Colonne (7):**
```
document_id, receipt_id, date, sku, qty_received, receipt_date, order_ids
```

**Chiave primaria candidata:** `document_id` (UNIQUE, idempotenza chiave)

**Backward compatibility:** `receipt_id` = legacy identifier (ora alias di `document_id`)

**Letture:**
- `CSVLayer.read_receiving_logs()` → lista dict raw
- `ReceivingWorkflow.close_receipt_by_document()` → check idempotenza su `document_id`
- GUI: tab "Receiving", storico ricevimenti, KPI OTIF

**Scritture:**
- `CSVLayer.write_receiving_log(document_id, ...)` → APPEND singolo record
- Workflows:
  - `ReceivingWorkflow.close_receipt_by_document()` → genera receiving log (dopo check idempotenza)
  - **Idempotenza garantita**: `if document_exists: return ([], True, {})`

**Idempotenza meccanismo:**
```python
document_exists = any(
    log.get("document_id") == document_id or log.get("receipt_id") == document_id
    for log in existing_logs
)
```
**Match su entrambi `document_id` e `receipt_id` per backward compat**

**Tipi attesi:**
- `document_id`: TEXT (PK naturale, es. "DDT-2026-001", "INV-12345")
- `receipt_id`: TEXT (legacy, nullable, ora duplicato di document_id)
- `date, receipt_date`: DATE (ISO8601 TEXT)
- `sku`: TEXT (FK → skus.sku)
- `qty_received`: INTEGER ≥ 0
- `order_ids`: TEXT (CSV embedded, es. "20260201_001,20260201_002", nullable)

**Vincoli di unicità:**
- `document_id` deve essere UNIQUE (chiave idempotenza)
- Nessuna validazione formale → duplicati possibili se check saltato

**Note/Rischi:**
- **Idempotenza critica**: chiusura stesso document_id 2 volte NON duplica stock
- `order_ids` è stringa CSV embedded → parsing fragile (`split(",")`)
- Nessun vincolo referenziale su order_ids → link può puntare a ordini inesistenti

---

### 2.6 `lots.csv` — Lotti con Scadenza (Shelf Life Tracking)

**Colonne (6):**
```
lot_id, sku, expiry_date, qty_on_hand, receipt_id, receipt_date
```

**Chiave primaria candidata:** `lot_id` (UNIQUE, TEXT, auto-generato come hash)

**Letture:**
- `CSVLayer.read_lots()` → parsing completo Lot objects
- `CSVLayer.get_lots_by_sku(sku, sort_by_expiry)` → filtraggio + ordinamento FEFO
- `ShelfLifeCalculator.calculate_usable_stock()` → calcolo stock utilizzabile vs scadente
- `ShelfLifeCalculator.apply_fefo_to_event()` → consume lotti in ordine FEFO
- GUI: tab "Stock", dettagli lotti, waste risk

**Scritture:**
- `CSVLayer.write_lot(lot)` → UPSERT (update se lot_id esiste, altrimenti insert)
- `CSVLayer.apply_fefo_to_lots()` → UPDATE batch qty_on_hand (consumo FEFO, atomic rewrite)
- Workflows:
  - `ReceivingWorkflow.close_receipt_by_document()` → crea nuovi lotti da ricevimento
  - `DailyCloseWorkflow.close_eod()` → consume lotti FEFO per vendite (ADJUST ledger + update lots)
  - Auto-FEFO su `write_transaction(SALE/WASTE)` → side effect implicito

**Generazione `lot_id`:**
```python
lot_id = f"{receipt_id}_{sku}_{expiry_date.isoformat()}"
```
**Determinismo**: receipt_id + sku + expiry_date univoca (se receipt_id univoco)

**Tipi attesi:**
- `lot_id`: TEXT (PK naturale, formato composito)
- `sku`: TEXT (FK → skus.sku)
- `expiry_date, receipt_date`: DATE (ISO8601 TEXT)
- `qty_on_hand`: INTEGER ≥ 0
- `receipt_id`: TEXT (FK logico → receiving_logs.document_id, nullable)

**Vincoli di unicità:**
- `lot_id` deve essere UNIQUE (PK naturale)
- Possibile multipli lotti stesso (sku, expiry_date) se receipt_id diversi → raggruppamento logico necessario

**Note/Rischi:**
- **Discrepancy tracking lots vs ledger**: fallback a ledger se `lots_total < ledger_stock - threshold`
- FEFO consume atomico ma **rewrites intero file** → performance issue con molti lotti
- `receipt_id` può essere `None` per lotti manuali → complicazione FK

---

### 2.7 `promo_calendar.csv` — Calendario Promozionale

**Colonne (5):**
```
sku, start_date, end_date, store_id, promo_flag
```

**Chiave primaria candidata:** `(sku, start_date, end_date, store_id)` UNIQUE composita

**Letture:**
- `CSVLayer.read_promo_calendar()` → lista PromoWindow objects
- `promo_adjusted_forecast()` → matching finestre promo per uplift forecast
- `is_in_post_promo_window()` → check post-promo guardrail
- GUI: tab "Promo Calendar", visualizzazione finestre

**Scritture:**
- `CSVLayer.write_promo_window(window)` → APPEND singola finestra
- `CSVLayer.write_promo_calendar(windows)` → OVERWRITE completo (bulk replace)
- Workflows:
  - GUI: add/edit/delete promo windows
  - Import CSV promo → bulk overwrite

**Tipi attesi:**
- `sku`: TEXT (FK → skus.sku)
- `start_date, end_date`: DATE (ISO8601 TEXT)
- `store_id`: TEXT (nullable, `None` = global promo)
- `promo_flag`: BOOLEAN (0/1 o true/false TEXT, sempre "1" per attive)

**Vincoli di unicità:**
- `(sku, start_date, end_date, store_id)` dovrebbe essere UNIQUE (non forzato)
- Overlapping windows permessi per stesso SKU (ma semanticamente problematici)

**Note/Rischi:**
- Nessuna validazione `start_date < end_date`
- `store_id = None` rappresentato come empty string in CSV → ambiguità

---

### 2.8 `kpi_daily.csv` — KPI Giornalieri

**Colonne (12):**
```
sku, date, oos_rate, lost_sales_est, wmape, bias, fill_rate, 
otif_rate, avg_delay_days, n_periods, lookback_days, mode
```

**Chiave primaria candidata:** `(sku, date, mode)` UNIQUE composita

**Letture:**
- `CSVLayer.read_kpi_daily()` → filtraggio per sku/date/mode
- `load_latest_kpi_metrics(sku)` → ultime metriche per closed-loop tuning
- GUI: tab "Dashboard", grafici KPI, trend

**Scritture:**
- `CSVLayer.write_kpi_daily_batch(kpi_snapshots)` → OVERWRITE completo (bulk replace)
- `CSVLayer.update_or_append_kpi_daily(kpi_entry)` → UPSERT (update se match chiave, altrimenti append)
- Workflows:
  - GUI: refresh KPI button → calcolo batch + write
  - Closed-loop: periodico update KPI per tuning

**Tipi attesi:**
- `sku`: TEXT (FK → skus.sku)
- `date`: DATE (ISO8601 TEXT)
- `mode`: TEXT (es. "strict", "relaxed" per OOS detection mode)
- `oos_rate, lost_sales_est, wmape, bias, fill_rate, otif_rate, avg_delay_days`: FLOAT/REAL (nullable, NaN possibile)
- `n_periods, lookback_days`: INTEGER

**Vincoli di unicità:**
- `(sku, date, mode)` dovrebbe essere UNIQUE (non forzato)
- Duplicati possibili se refresh multipli

**Note/Rischi:**
- Overwrite completo file per batch update → lento, rischio perdita dati se interrotto
- NaN/Null handling inconsistente (CSV scrive `""` per None, parsing può fallire)

---

### 2.9 `audit_log.csv` — Audit Trail Operazioni

**Colonne (5):**
```
timestamp, operation, sku, details, user
```

**Chiave primaria candidata:** **NESSUNA esplicita** (append-only log, no audit_id)

**Possibile chiave surrogata:** `audit_id INTEGER PRIMARY KEY AUTOINCREMENT`

**Letture:**
- `CSVLayer.read_audit_log(filters)` → filtraggio per operation, sku, date range
- GUI: tab "Audit Trail", visualizzazione storico

**Scritture:**
- `CSVLayer.log_audit(operation, details, sku, user)` → APPEND singolo record
- Chiamato da:
  - GUI: ogni modifica SKU, conferma ordine, receiving, delete
  - Workflows: operazioni critiche (confirm, close_receipt, revert)

**Tipi attesi:**
- `timestamp`: DATETIME (ISO8601 TEXT "YYYY-MM-DD HH:MM:SS")
- `operation`: TEXT (es. "sku_created", "order_confirmed", "receipt_closed", "sku_deleted")
- `sku`: TEXT (nullable, FK logico → skus.sku)
- `details`: TEXT (JSON embedded o plain text)
- `user`: TEXT (default "system", oppure username)

**Vincoli di unicità:**
- **NESSUNO** (append-only, duplicati permessi)

**Note/Rischi:**
- Manca audit_id → impossibile referenziare evento specifico
- `details` può essere JSON stringified → parsing necessario per analisi strutturata

---

### 2.10 `event_uplift_rules.csv` — Regole Uplift Eventi

**Colonne (6):**
```
delivery_date, reason, strength, scope_type, scope_key, notes
```

**Chiave primaria candidata:** `(delivery_date, scope_type, scope_key)` UNIQUE composita (ragionevole, non garantito)

**Letture:**
- `CSVLayer.read_event_uplift_rules()` → lista EventUpliftRule objects, ordinata per delivery_date
- `apply_event_uplift_to_forecast()` → matching regole per data consegna
- GUI: tab "Event Uplift", gestione regole

**Scritture:**
- `CSVLayer.write_event_uplift_rule(rule)` → APPEND singola regola
- `CSVLayer.write_event_uplift_rules(rules)` → OVERWRITE completo (bulk replace)
- GUI: add/edit/delete regole

**Tipi attesi:**
- `delivery_date`: DATE (ISO8601 TEXT)
- `reason`: TEXT (es. "holiday", "weather", "strike")
- `strength`: FLOAT/REAL (uplift factor, es. 1.5 = +50%)
- `scope_type`: TEXT ENUM (ALL, SKU, CATEGORY, DEPARTMENT)
- `scope_key`: TEXT (nullable, valore chiave per scope_type != ALL)
- `notes`: TEXT (nullable)

**Vincoli di unicità:**
- `(delivery_date, scope_type, scope_key)` dovrebbe essere UNIQUE (non forzato)
- Duplicati possibili → ambiguità quale regola applicare

**Note/Rischi:**
- `strength` può essere sia percentuale (150) che fattore (1.5) → normalizzazione necessaria
- Sorting esplicito per `delivery_date` → importante per match logico

---

### 2.11 `settings.json` — Configurazione Globale

**Struttura:**
Mega-dizionario gerarchico con ~20 sezioni:
- `reorder_engine`: parametri ordini (lead_time, moq, pack_size, review_period, safety_stock, max_stock, reorder_point, demand_variability, oos_boost_percent, oos_lookback_days, oos_detection_mode, forecast_method, policy_mode)
- `monte_carlo`: parametri simulazione MC (distribution, n_simulations, random_seed, output_stat, output_percentile, horizon_mode, horizon_days, show_comparison)
- `dashboard`: parametri UI (stock_unit_price)
- `promo_uplift`: parametri calcolo uplift promo (min/max_uplift, min_events, winsorize, confidence thresholds)
- `promo_adjustment`: enable/disable promo adjustment, smoothing
- `event_uplift`: enable/disable event uplift, default_quantile, min/max_factor, apply_to, perishables_policy
- `promo_prebuild`: enable/disable prebuild, coverage_days, safety_component
- `post_promo_guardrail`: enable/disable anti-overstock, window_days, cooldown_factor, qty_cap, dip estimation
- `promo_cannibalization`: enable/disable downlift, downlift_min/max, substitute_groups
- `service_level`: CSL params (metric, default_csl, fill_rate_target, lookback_days, oos_mode, cluster_csl_*)
- `closed_loop`: enable/disable KPI-driven tuning, review_frequency, alpha_step, thresholds
- `calendar`: order_days validi
- `shelf_life_policy`: enable/disable, min_shelf_life_global, waste_horizon_days, category_overrides, waste_realization_factor
- `auto_variability`: enable/disable auto-classification, min_sample, cv_thresholds

**Formato:**
Ogni parametro: `{"value": X, "auto_apply_to_new_sku": bool, "description": str, "min": Y, "max": Z, "choices": [...]}`

**Letture:**
- `CSVLayer.read_settings()` → parsing JSON con merge defaults per chiavi mancanti
- `CSVLayer.get_default_sku_params()` → estrazione defaults per nuovo SKU
- Tutte le workflows/forecast per config-driven behavior

**Scritture:**
- `CSVLayer.write_settings(settings)` → OVERWRITE completo JSON (indent 2, ensure_ascii=False)
- GUI: tab "Settings", modifica parametri
- Auto-merge: se chiavi mancanti, crea + scrive (auto-persist)

**Chiave primaria:** N/A (singolo file, non tabulare)

**Note/Rischi:**
- File gigantesco (~1500 linee JSON) → merge complesso, rischio corruzione
- Auto-persist può sovrascrivere modifiche concorrenti (no locking)
- Default values hard-coded in `read_settings()` → duplicazione logica

---

### 2.12 `holidays.json` — Calendario Festività

**Struttura:**
```json
{
  "holidays": [
    {
      "name": "Natale",
      "scope": {...},
      "effect": {...},
      "type": "fixed_date" | "recurring_date" | "relative_date",
      "params": {...}
    }
  ]
}
```

**Letture:**
- `CSVLayer.read_holidays()` → lista holidays dict
- `HolidayCalendar.load_from_json()` → build calendario con date espanse
- `next_receipt_date()`, `calculate_protection_period()` → calendar-aware date calcs

**Scritture:**
- `CSVLayer.write_holidays(holidays)` → OVERWRITE completo JSON
- `CSVLayer.add_holiday(holiday)` → READ + APPEND + WRITE
- `CSVLayer.update_holiday(index, holiday)` → READ + REPLACE[index] + WRITE
- `CSVLayer.delete_holiday(index)` → READ + POP[index] + WRITE
- GUI: tab "Holiday Management", add/edit/delete holidays

**Chiave primaria:** Index-based (fragile)

**Note/Rischi:**
- Index-based edit/delete → race condition se modifiche concorrenti
- Possibili duplicati (name, type, params) → nessuna validazione unicità

---

## 3. CHIAVI E VINCOLI DI UNICITÀ (Candidate Key Analysis)

| File | Primary Key Candidata | Unique Constraints | Foreign Keys (logici) | Enforcement |
|------|----------------------|-------------------|----------------------|-------------|
| `skus.csv` | `sku` | `sku` | — | Applicativo |
| `transactions.csv` | **NONE** (suggerito: `transaction_id AUTOINCREMENT`) | NONE | `sku` → skus.sku | NONE |
| `sales.csv` | `(date, sku)` composita | `(date, sku)` | `sku` → skus.sku | Applicativo |
| `order_logs.csv` | `order_id` | `order_id` | `sku` → skus.sku | Applicativo |
| `receiving_logs.csv` | `document_id` | `document_id` | `sku` → skus.sku, `order_ids` → order_logs.order_id (CSV embedded) | Applicativo (idempotenza) |
| `lots.csv` | `lot_id` | `lot_id` | `sku` → skus.sku, `receipt_id` → receiving_logs.document_id | Applicativo |
| `promo_calendar.csv` | `(sku, start_date, end_date, store_id)` | Nessuna esplicita | `sku` → skus.sku | NONE |
| `kpi_daily.csv` | `(sku, date, mode)` composita | Nessuna esplicita | `sku` → skus.sku | NONE |
| `audit_log.csv` | **NONE** (suggerito: `audit_id AUTOINCREMENT`) | NONE | `sku` → skus.sku (nullable) | NONE |
| `event_uplift_rules.csv` | `(delivery_date, scope_type, scope_key)` composita (ragionevole) | Nessuna esplicita | NONE | NONE |

**Nota critica:** NESSUN vincolo formale forzato a livello storage (CSV plain text). Unicità garantita solo da logica applicativa (check before insert, idempotency guards).

---

## 4. FLUSSI CRITICI (Operazioni Multi-File con Atomicità Richiesta)

### 4.1 **Ledger Append (Transazioni Stock)**
**Workflow:** Qualsiasi evento che modifica stock (order, receipt, waste, adjust, sale EOD)

**File coinvolti:**
1. `transactions.csv` ← APPEND nuovo evento
2. `lots.csv` ← UPDATE qty_on_hand se SALE/WASTE (auto-FEFO)
3. `audit_log.csv` ← APPEND log operazione

**Atomicità:**
- CSV Layer usa `_write_csv_atomic()` con temp file + backup
- **NO TRANSAZIONE MULTI-FILE**: ledger append e lots update sono operazioni separate
- **Rischio**: crash tra `append_transaction()` e `apply_fefo_to_lots()` → inconsistenza

**Idempotenza:**
- NONE (ledger è append-only, duplicati possibili se retry)
- Receiving idempotenza protegge contro duplicati document-based

**Punti di scrittura:**
- `CSVLayer.write_transaction()` → append singolo
- `CSVLayer.write_transactions_batch()` → append batch (atomic per batch, non multi-file)

---

### 4.2 **Conferma Ordini (Order Confirmation)**
**Workflow:** `OrderWorkflow.confirm_order(proposals, confirmed_qtys)`

**File coinvolti:**
1. `transactions.csv` ← BATCH APPEND eventi ORDER (uno per SKU)
2. `order_logs.csv` ← BATCH APPEND log ordini (order_id generato)
3. `audit_log.csv` ← APPEND log conferma

**Meccanismo:**
```python
# OrderWorkflow.confirm_order()
transactions = []
for proposal, qty in zip(proposals, confirmed_qtys):
    order_id = f"{order_date_base}_{idx:03d}"  # Generato qui
    txn = Transaction(event=ORDER, qty=qty, receipt_date=proposal.receipt_date, note=f"Order {order_id}")
    transactions.append(txn)
    confirmations.append(OrderConfirmation(order_id=order_id, ...))

# Scritture atomiche PER FILE (non cross-file)
csv_layer.write_transactions_batch(transactions)  # Atomic per transactions.csv
for confirmation in confirmations:
    csv_layer.write_order_log(order_id, ...)  # Atomic per order_logs.csv (append singolo)
```

**Atomicità:**
- `write_transactions_batch()` atomico per `transactions.csv` (backup + temp + replace)
- `write_order_log()` append singoli **non atomici rispetto a transactions.csv**
- **Rischio**: crash dopo transactions ma prima order_logs → eventi ORDER senza log tracciabilità

**Idempotenza:**
- NONE (nessun check duplicate order_id)
- Possibile collision `order_id` se conferme concorrenti stesso giorno

**Punti di scrittura:**
- `src/workflows/order.py:1488` → `csv_layer.write_transactions_batch(transactions)`
- `src/workflows/order.py:1516` → `csv_layer.write_order_log(...)`

---

### 4.3 **Chiusura Ricevimento (Receiving Closure)**
**Workflow:** `ReceivingWorkflow.close_receipt_by_document(document_id, receipt_date, items)`

**File coinvolti:**
1. `receiving_logs.csv` ← CHECK idempotenza su `document_id` (read)
2. `receiving_logs.csv` ← APPEND log ricevimento (se not exists)
3. `transactions.csv` ← BATCH APPEND eventi RECEIPT + UNFULFILLED
4. `lots.csv` ← BATCH APPEND nuovi lotti (uno per item se shelf_life > 0)
5. `order_logs.csv` ← UPDATE `qty_received` e `status` per order_ids allocation (atomic rewrite)
6. `audit_log.csv` ← APPEND log chiusura

**Meccanismo receiving_v2 (document-based):**
```python
# 1. Check idempotenza
existing_logs = csv_layer.read_receiving_logs()
if any(log.get("document_id") == document_id for log in existing_logs):
    return [], True, {}  # Already processed

# 2. Process items (generate transactions, lots, order_updates)
transactions = []
lots_to_create = []
order_updates = {}
for item in items:
    # Allocate qty_received to orders (FIFO or specified order_ids)
    # Create RECEIPT event, optional UNFULFILLED if residual
    # Create Lot if shelf_life > 0
    pass

# 3. Write (non-atomic cross-file!)
csv_layer.write_receiving_log(document_id, ...)  # APPEND
csv_layer.write_transactions_batch(transactions)  # ATOMIC per transactions.csv
for lot in lots_to_create:
    csv_layer.write_lot(lot)  # UPSERT per lot (atomic rewrite lots.csv)
for order_id, update_info in order_updates.items():
    csv_layer.update_order_received_qty(order_id, qty_received, status)  # ATOMIC rewrite order_logs.csv
```

**Atomicità:**
- Ogni file operazione atomica **per singolo file** (backup + temp + replace)
- **NO TRANSAZIONE MULTI-FILE**: crash tra `write_receiving_log()` e `write_transactions_batch()` → inconsistenza
- **Rischio massimo**: receiving_log scritto ma transactions/lots mancanti → stock non aggiornato, ma idempotenza previene retry

**Idempotenza:**
- **CRITICA**: check `document_id` esiste in `receiving_logs.csv` PRIMA di qualsiasi scrittura
- **Garanzia**: stesso `document_id` 2 volte → seconda chiamata skip completo (nessun side effect)
- **Fragilità**: se check idempotenza salta (bug) → duplicazione RECEIPT events e lotti

**Punti di scrittura:**
- `src/workflows/receiving_v2.py:150` → `csv_layer.write_receiving_log(...)`
- `src/workflows/receiving_v2.py:243` → `csv_layer.write_receiving_log(...)` (secondo pattern)
- `src/workflows/receiving_v2.py:281` → `csv_layer.write_lot(lot)`
- `src/workflows/receiving_v2.py:286` → `csv_layer.write_transactions_batch(transactions)`
- `src/workflows/receiving_v2.py:290` → `csv_layer.update_order_received_qty(...)`

---

### 4.4 **Revert Eccezioni (Exception Day Revert)**
**Workflow:** `ExceptionManagement.revert_exception_day(sku, exception_date, event_types)`

**File coinvolti:**
1. `transactions.csv` ← READ + FILTER + OVERWRITE (rimozione eventi specifici)
2. `audit_log.csv` ← APPEND log revert

**Meccanismo:**
```python
# Read all transactions
existing_txns = csv_layer.read_transactions()

# Filter out events matching (sku, date, event_types)
filtered_txns = [
    txn for txn in existing_txns
    if not (txn.sku == sku and txn.date == exception_date and txn.event in event_types)
]

# Overwrite (atomic)
csv_layer.overwrite_transactions(filtered_txns)
```

**Atomicità:**
- `overwrite_transactions()` atomico per `transactions.csv` (backup + temp + replace)
- **Rischio**: matching basato su (sku, date, event, qty, note) → se note cambiano, manca match
- **Limite**: nessun transaction_id → impossibile revert singolo evento preciso

**Idempotenza:**
- Ripetere revert STESSO (sku, date, event_type) → idempotente (già rimossi)

**Punti di scrittura:**
- `src/workflows/receiving_v2.py:451` → `csv_layer.overwrite_transactions(filtered_txns)`
- `src/workflows/receiving.py:265` → `csv_layer.overwrite_transactions(filtered_txns)`

---

### 4.5 **Migrazioni Schema CSV (Auto-Migration Legacy Files)**
**Workflow:** `CSVLayer._ensure_file_exists()` su startup

**File coinvolti:**
- Qualsiasi CSV con schema changed (es. `skus.csv`, `order_logs.csv`)

**Meccanismo:**
```python
# Read current header
with open(filepath, "r") as f:
    current_columns = csv.DictReader(f).fieldnames

# Compare with expected schema
if set(current_columns) != set(expected_columns):
    # Backup
    backup_path = f"{filepath.stem}.pre_migration.{timestamp}{filepath.suffix}"
    shutil.copy2(filepath, backup_path)
    
    # Read all rows
    old_rows = list(csv.DictReader(open(filepath)))
    
    # Rewrite with new schema (missing columns filled with "")
    with open(filepath, "w") as f:
        writer = csv.DictWriter(f, fieldnames=expected_columns)
        writer.writeheader()
        for row in old_rows:
            migrated_row = {col: row.get(col, "") for col in expected_columns}
            writer.writerow(migrated_row)
```

**Atomicità:**
- **NO**: backup + overwrite **non atomico**
- **Rischio**: crash durante migration → file corrotto (backup esiste, ma recovery manuale)

**Idempotenza:**
- **NO**: ogni startup check schema, se changes → re-migra (con nuovo backup timestamp)

**Punti di scrittura:**
- `src/persistence/csv_layer.py:100-114` (metodo `_ensure_file_exists()`)

---

## 5. RISCHI E INVARIANTI CRITICHE

### 5.1 Rischi Identificati

| # | Rischio | File Coinvolti | Impatto | Mitigazione Proposta SQLite |
|---|---------|---------------|---------|----------------------------|
| 1 | **Mancanza transaction_id** | `transactions.csv`, `audit_log.csv` | Impossibilità revert puntuale, audit trail incompleto | `transaction_id INTEGER PRIMARY KEY AUTOINCREMENT` |
| 2 | **Operazioni multi-file NON atomiche** | Tutti i workflows critici | Inconsistenza dati se crash mid-operation | Transaction DB `BEGIN...COMMIT` |
| 3 | **Idempotenza receiving fragile** | `receiving_logs.csv` | Duplicati se check saltato | `UNIQUE(document_id)` + UPSERT O INSERT IGNORE |
| 4 | **order_id collision** | `order_logs.csv` | Duplicati se conferme concorrenti | `UNIQUE(order_id)` + sequential generation con lock |
| 5 | **CSV rewrite completo per update** | `order_logs.csv`, `lots.csv`, `kpi_daily.csv` | Lento, lock file, rischio corruzione | UPDATE singola riga con WHERE clause |
| 6 | **Embedded CSV in order_ids** | `receiving_logs.csv` | Parsing fragile, no FK enforcement | Tabella junction `order_receipts(order_id, document_id)` |
| 7 | **Nessuna validazione FK** | Tutti | SKU cancellati con dati orfani | `FOREIGN KEY(sku) REFERENCES skus(sku) ON DELETE CASCADE` (o RESTRICT) |
| 8 | **Discrepancy lots vs ledger** | `lots.csv` vs `transactions.csv` | Fallback a ledger disabilita shelf life | Sincronizzazione automatica o trigger |
| 9 | **settings.json mega-file** | `settings.json` | Merge complesso, corruzione facile | Tabella `settings(section, key, value_json, type)` o multi-colonna |
| 10 | **Index-based edit holidays** | `holidays.json` | Race condition, edit wrong entry | Chiave `holiday_id` generata + UNIQUE name |

---

### 5.2 Invarianti Critiche da Preservare Post-Migrazione

1. **Stock AsOf Determinism**: `calculate_asof(sku, asof_date)` deve restituire STESSO risultato pre/post-migrazione su STESSO dataset
   - **Test regressione**: AsOf calculation golden test

2. **Receiving Idempotency**: `close_receipt_by_document(document_id)` chiamato 2 volte → secondo skip, nessun side effect
   - **Test regressione**: duplicate document_id → single RECEIPT event

3. **Order ID Uniqueness**: Nessun `order_id` duplicato in `order_logs`
   - **Test regressione**: concurrent order confirmations → distinct order_ids

4. **FEFO Lot Consumption**: SALE/WASTE consuma lotti in ordine earliest expiry_date first
   - **Test regressione**: FEFO application su multi-lot scenario

5. **Event Type Semantics**: Ogni event type applica impatto stock corretto:
   - SNAPSHOT → on_hand := qty
   - ORDER → on_order += qty
   - RECEIPT → on_order -= qty, on_hand += qty
   - SALE/WASTE → on_hand -= qty
   - ADJUST → on_hand := qty
   - UNFULFILLED → tracking only (no stock impact)
   - **Test regressione**: event application su ledger vuoto

6. **Promo Adjustment Forecast Neutrality**: Disabling promo_adjustment fallback a baseline → no crash
   - **Test regressione**: toggle promo_adjustment enabled → same qty if no promo active

7. **Settings Merge Idempotency**: `read_settings()` + `write_settings()` ripetuti → nessuna perdita dati
   - **Test regressione**: merge defaults + persist → stable structure

---

## 6. PUNTI DI SCRITTURA SUMMARY (Centralizzati in CSVLayer)

### Metodi di Scrittura (Pattern rilevati)

| Metodo | Tipo Operazione | Atomicità | Uso Primario |
|--------|----------------|-----------|--------------|
| `_write_csv(filename, rows)` | OVERWRITE completo | NO (direct write) | Legacy, piccoli file |
| `_write_csv_atomic(filename, rows)` | OVERWRITE completo | SÌ (temp + backup + replace) | File critici, update batch |
| `_append_csv(filename, row)` | APPEND singola riga | NO (direct append) | Log append-only (transactions, events) |
| `write_sku(sku)` | INSERT/UPDATE | NO (read + modify + overwrite) | Nuovo SKU o update parametri |
| `write_transaction(txn)` | APPEND + side effects | PARZIALE (auto-FEFO side effect su lots) | Singolo evento ledger |
| `write_transactions_batch(txns)` | APPEND batch | SÌ (per transactions.csv) | Conferma ordini, receiving batch |
| `overwrite_transactions(txns)` | REPLACE completo | SÌ (atomic rewrite) | Revert eccezioni, repair |
| `write_order_log(...)` | APPEND singolo | NO (direct append) | Log ordine singolo |
| `update_order_received_qty(order_id, qty, status)` | UPDATE singolo | SÌ (read + modify + atomic rewrite) | Receiving closure |
| `write_receiving_log(document_id, ...)` | APPEND singolo | NO (direct append) | Ricevimento idempotente |
| `write_lot(lot)` | UPSERT | SÌ (read + modify + atomic rewrite) | Nuovo lotto o update qty |
| `write_sales_record(sale)` | APPEND singolo | NO (direct append) | Daily close, entry manuale |
| `write_sales(sales_list)` | OVERWRITE completo | NO (direct overwrite) | Bulk update promo flags |
| `write_kpi_daily_batch(kpis)` | OVERWRITE completo | SÌ (atomic rewrite) | Refresh KPI dashboard |
| `write_settings(settings)` | OVERWRITE completo JSON | NO (direct write) | Salvataggio configurazione |
| `write_holidays(holidays)` | OVERWRITE completo JSON | NO (direct write) | Update calendario festività |

---

## 7. CONCLUSIONI E NEXT STEPS (STOP CONDITIONS VERIFICATE)

### ✅ STOP CONDITIONS FASE 0 — VERIFICATE

1. **Tutti i punti di scrittura identificati**: ✅ 85 match `write_|append_` + 16 metodi CSVLayer documentati
2. **Tutte le strutture dati persistenti mappate**: ✅ 10 CSV + 2 JSON + header completi + tipi
3. **Idempotenza ricevimento chiara**: ✅ `document_id` UNIQUE check in `close_receipt_by_document()`
4. **Generazione ID documentata**:
   - `order_id`: ✅ `f"{YYYYMMDD}_{idx:03d}"` (non globalmente univoco, collision possibile)
   - `lot_id`: ✅ `f"{receipt_id}_{sku}_{expiry_date}"` (deterministic)
   - `transaction_id`: ❌ **NON ESISTE** (chiave surrogata necessaria per migrazione)

---

### DELIVERABLE FASE 0

**File prodotto**: `FASE0_RICOGNIZIONE_STORAGE.md` (questo documento)

**Contenuto**:
- ✅ Tabella File → Colonne → Letture → Scritture → Chiavi/Unicità → Note/Rischi (sezione 2)
- ✅ Mappa flussi critici (ledger, order, receiving, revert, schema migration) (sezione 4)
- ✅ Rischi identificati con impatto e mitigazioni proposte (sezione 5.1)
- ✅ Invarianti critiche da preservare (sezione 5.2)
- ✅ Punti di scrittura centralizzati (sezione 6)

---

### READY FOR FASE 1 — SCHEMA SQLITE (DDL + VINCOLI + INDICI)

**Input per Fase 1**:
- 10 CSV files con 102 colonne totali documentate
- 2 JSON files con strutture complesse
- Chiavi candidate identificate (7 PK naturali + 2 PK surrogati da creare)
- 10 rischi prioritari da mitigare con vincoli DB
- 7 invarianti critiche da testare post-migrazione

**Proposta iniziale tabelle SQLite** (da raffinare in Fase 1):
1. `skus` (PK: sku)
2. `transactions` (PK: transaction_id AUTOINCREMENT, FK: sku)
3. `sales` (PK composita: date, sku)
4. `order_logs` (PK: order_id, FK: sku)
5. `receiving_logs` (PK: document_id, FK: sku)
6. `order_receipts` (junction: order_id, document_id) ← NEW per embedded CSV
7. `lots` (PK: lot_id, FK: sku, receipt_id)
8. `promo_calendar` (PK: promo_id AUTOINCREMENT o composita)
9. `kpi_daily` (PK composita: sku, date, mode)
10. `audit_log` (PK: audit_id AUTOINCREMENT, FK: sku nullable)
11. `event_uplift_rules` (PK: rule_id AUTOINCREMENT o composita)
12. `settings` (PK: section, key o table con key-value rows) ← da decidere struttura
13. `holidays` (PK: holiday_id AUTOINCREMENT)

**Next action**: Iniziare Fase 1 con DDL completo + rationale vincoli.

---

**End of FASE 0 RICOGNIZIONE**
