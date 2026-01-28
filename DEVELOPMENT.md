# Desktop Order System - Development Notes

## Architettura Critica

### Ledger come Fonte di Verità

**Principio**: Lo stato dello stock NON è mai memorizzato. È sempre **calcolato** dal ledger (transactions.csv).

```python
# ✅ CORRETTO
stock = StockCalculator.calculate_asof(sku, asof_date, transactions)

# ❌ SBAGLIATO
stock = read_from_inventory_csv()  # No! Crea inconsistenza
```

### Evento → Impatto su Stock

| Evento | date | receipt_date | Impatto |
|--------|------|--------------|---------|
| SNAPSHOT | T | - | on_hand := qty; on_order := 0 |
| ORDER | T | D+7 (default) | on_order += qty |
| RECEIPT | T | D (receipt happened) | on_order -= qty; on_hand += qty |
| SALE | T | - | on_hand -= qty (from sales.csv) |
| WASTE | T | - | on_hand -= qty |
| ADJUST | T | - | on_hand ± qty |
| UNFULFILLED | T | - | No impact (tracking only) |

### AsOf Rule (Critica)

**Regola deterministica**: Solo eventi con `date < AsOf_date` influenzano il calcolo.

```python
# AsOf = 2026-01-28
# ✅ Incluso: evento del 2026-01-27
# ❌ Escluso: evento del 2026-01-28 o successivo

for txn in transactions:
    if txn.date < asof_date:
        apply_event(txn)
```

Conseguenza: Stock del giorno D è **finale** dal giorno D+1 in poi.

### Priorità Evento (Stessa Giornata)

Se più eventi stesso giorno e stesso SKU:

1. **SNAPSHOT** (priority 0) - Reset base
2. **ORDER/RECEIPT** (priority 1) - Transiti in/out ordine
3. **SALE/WASTE/ADJUST** (priority 2) - Variazioni on_hand
4. **UNFULFILLED** (priority 3) - Tracking only

```python
sorted_txns = sorted(transactions, key=lambda t: (t.date, EVENT_PRIORITY[t.event]))
```

## Idempotenza (Critica per Ricevimenti + Eccezioni)

### Ricevimento (ReceivingWorkflow.close_receipt)

**Problema**: Utente clicca "Chiudi ricevimento" due volte → dupliche gli effetti?

**Soluzione**: Idempotency key = receipt_id

```python
receipt_id = f"{receipt_date}_{origin_hash}_{sku}"

# Primo close
close_receipt(receipt_id="2026-01-15_a1b2c3d4_SKU001", qty=50)
# → Crea RECEIPT event, scrive su receiving_logs.csv

# Secondo close (stessa receipt_id)
close_receipt(receipt_id="2026-01-15_a1b2c3d4_SKU001", qty=50)
# → Check: receipt_id già in receiving_logs? YES
# → Return (already_processed=True), no new events
```

### Eccezione (ExceptionWorkflow.record_exception)

**Problema**: Registri WASTE due volte stesso giorno → dupliche?

**Soluzione**: Idempotency key = date + sku + event_type

```python
# Primo record
record_exception(date=2026-01-20, sku="SKU001", event_type=WASTE, qty=10)
# → Crea evento, scrive su ledger

# Secondo record (stesso giorno, sku, tipo)
record_exception(date=2026-01-20, sku="SKU001", event_type=WASTE, qty=10)
# → Check: già presente? YES
# → Return (already_recorded=True), no new events
```

### Revert Eccezione (ExceptionWorkflow.revert_exception_day)

**Cosa serve**: Annulla TUTTE le eccezioni di tipo X per SKU in data D.

```python
# Scenario: Giorno 2026-01-20, SKU001
# - WASTE di 5
# - ADJUST di -3
# - ORDER di 50 (non eccezione)

revert_exception_day(date=2026-01-20, sku="SKU001", event_type=WASTE)
# → Rimuove WASTE, mantiene ADJUST e ORDER
```

## Flusso Ordine (Proposal → Confirmation → Receipt)

### 1. Proposal (OrderWorkflow.generate_proposal)

```
Input: 
  - current_stock (on_hand, on_order)
  - daily_sales_avg (ultimi 30 giorni)
  - min_stock, days_cover (configurabili)

Logica:
  target = min_stock + (daily_sales_avg * days_cover)
  proposed_qty = max(0, target - (on_hand + on_order))
  receipt_date = today + lead_time_days

Output: OrderProposal(sku, description, proposed_qty, receipt_date)
```

### 2. Confirmation (OrderWorkflow.confirm_order)

```
Input: List[OrderProposal], optional confirmed_qtys

Azioni:
  1. Generate order_id (deterministic: date + index)
  2. Create ORDER events in ledger (today, sku, qty, receipt_date)
  3. Write to order_logs.csv (for tracking)
  4. Return confirmations + transactions

Output: List[OrderConfirmation], List[Transaction]
```

### 3. Receipt (ReceivingWorkflow.close_receipt)

```
Input: receipt_id, receipt_date, sku_quantities

Azioni:
  1. Check if receipt_id already exists (idempotency)
  2. If no: create RECEIPT events (today, sku, qty, receipt_date)
  3. Write to ledger + receiving_logs.csv
  4. Return transactions + already_processed flag

Effetto Ledger:
  RECEIPT: on_order -= qty, on_hand += qty
  Data: TODAY (quando registriamo la chiusura)
  receipt_date: Quando è arrivato effettivamente
  
Visibilità Stock:
  - OGGI: Calcolo AsOf today excludes oggi stesso
  - DOMANI: receipt_date < asof_date → included
```

## Migrazione Legacy (One-Time Init)

### Scenario

```
Legacy inventory CSV (old system):
  sku,description,quantity,ean
  SKU001,Product A,100,5901234123457
  SKU002,Product B,50,
```

### Processo (LegacyMigration.migrate_from_legacy_csv)

```
Check:
  1. Is ledger already populated? → Skip (avoid duplication)
  2. Does legacy file exist? → Error if not

Migrate:
  1. Read legacy CSV
  2. For each SKU: create SNAPSHOT event (snapshot_date, qty)
  3. Add SKU to skus.csv
  4. Write all to transactions.csv

Validate:
  Stock AsOf (snapshot_date + 1 day) deve corrispondere
```

## Validazioni + Error Handling

### EAN Validation

```python
is_valid, error = validate_ean(ean_string)

if not is_valid:
    # Log warning, display message
    print(f"Invalid EAN: {error}")
    # Don't crash; skip barcode render
else:
    render_barcode(ean_string)
```

### Date Validation

```python
# ❌ Sbagliato (hardcoded now)
today = datetime.datetime.now()

# ✅ Corretto (passe as parameter)
def calculate_stock(sku, asof_date, transactions):
    # Use asof_date explicitly
```

### SKU Existence Check

```python
sku_ids = csv_layer.get_all_sku_ids()
if sku_id not in sku_ids:
    raise ValueError(f"Unknown SKU: {sku_id}")
```

## Testing Strategy

### 1. Unit Tests (Domain)

```python
# Pure functions, no I/O
test_calculate_stock_asof()
test_event_priority_same_day()
test_sales_integration()
test_validate_ean()
```

### 2. Integration Tests (Persistence + Domain)

```python
# CSV layer + calculation
test_write_read_transactions()
test_stock_calculation_with_csv()
```

### 3. Workflow Tests

```python
# High-level operations
test_confirm_order_creates_ledger_entry()
test_receiving_idempotent()
test_exception_revertible()
```

### 4. Regression Tests (Fixed Datasets)

```python
# Known scenarios
test_legacy_migration_preserves_stock()
test_receiving_twice_no_duplicate()
```

## Common Gotchas

### 1. Date Comparison Bug

```python
# ❌ Sbagliato
if txn.date >= asof_date:
    skip

# ✅ Corretto
if txn.date >= asof_date:
    skip  # Includi solo date < asof_date
```

### 2. Ledger Overwrite

```python
# ❌ Sbagliato (sovrascrive intero ledger)
write_transactions(batch_new_txns)

# ✅ Corretto (leggi, aggiungi, scrivi)
existing = read_transactions()
existing.extend(new_txns)
write_transactions_batch(existing)
```

### 3. Missing SKU in Proposal

```python
# ❌ Sbagliato
proposal = generate_proposal(sku="UNKNOWN", ...)

# ✅ Corretto
if sku not in csv_layer.get_all_sku_ids():
    raise ValueError(f"Unknown SKU: {sku}")
```

## GUI Patterns (Tkinter)

### Tab: Stock Calcolato

```python
# Quando ledger cambia (ordine/ricevimento)
def _refresh_stock_tab():
    asof_date = date.fromisoformat(self.asof_date_var.get())
    stocks = StockCalculator.calculate_all_skus(...)
    # Populate treeview
```

### Tab: Ordini

```python
# 1. Generazione Proposta
proposals = [order_workflow.generate_proposal(...) for sku in skus]
# Mostra tabella proposta

# 2. Conferma
confirmations, txns = order_workflow.confirm_order(proposals)
# Crea ORDER events in ledger
# Show confirmation window (5 items/page, barcode)
```

### Tab: Ricevimenti

```python
# 1. Inserisci receipt_id, receipt_date, qty per SKU
# 2. Chiudi ricevimento
receipt_txns, already_processed = receiving_workflow.close_receipt(...)
# Se already_processed: show warning
# Altrimenti: show receipt_txns created
```

### Tab: Eccezioni

```python
# Rapido entry: WASTE, ADJUST, UNFULFILLED
txn, already_recorded = exception_workflow.record_exception(...)
# Se already_recorded: show warning

# Revert button
reverted_count = exception_workflow.revert_exception_day(...)
```

## Debugging Checklist

- [ ] Ledger contiene gli eventi attesi?
- [ ] AsOf date è < event date? (should exclude)
- [ ] Event priority è corretta? (sort per day)
- [ ] Receipt_id è unique + deterministic?
- [ ] CSV files auto-created con headers?
- [ ] EAN invalid → messaggio, no crash?
- [ ] Idempotenza verified (2x run = same result)?
