# Patch: Tracciabilità Ordini-Ricevimenti

## Obiettivo
Migliorare robustezza del sistema ordini/ricevimenti con tracciabilità granulare, idempotenza documento-based e calcolo accurato inevasi.

## Problemi Risolti
1. **Aggregazione SKU**: ricevimenti non tracciavano ordini specifici
2. **Consegne parziali**: impossibili da gestire correttamente su più documenti
3. **Idempotenza debole**: basata solo su `receipt_id` auto-generato
4. **Inevasi imprecisi**: calcolo grossolano per SKU totale

## Modifiche Implementate

### 1. Schema Dati (backward compatible)

**order_logs.csv**:
```
PRIMA: order_id,date,sku,qty_ordered,status,receipt_date
DOPO:  order_id,date,sku,qty_ordered,qty_received,status,receipt_date
```
- `qty_received`: traccia quanto ricevuto per questo ordine (default 0)
- Migrazione automatica: colonna mancante → valore 0

**receiving_logs.csv**:
```
PRIMA: receipt_id,date,sku,qty_received,receipt_date  
DOPO:  document_id,date,sku,qty_received,receipt_date,order_ids
```
- `document_id`: numero DDT/fattura (idempotenza documento)
- `order_ids`: lista comma-separated degli ordini chiusi (parzialmente/totalmente)

### 2. Workflow Ricevimento

#### Nuovo metodo `ReceivingWorkflow.close_receipt_by_document()`

**Signature**:
```python
def close_receipt_by_document(
    document_id: str,              # DDT-12345, INV-67890
    receipt_date: date,            # Data ricezione
    items: List[Dict],             # [{sku, qty_received, order_ids}]
    notes: str = "",
) -> Tuple[List[Transaction], bool, Dict]:
```

**Logica**:
1. **Idempotenza**: verifica se `document_id` già processato → skip
2. **Assegnazione ordini**:
   - Se `order_ids` specificati: assegna qty a quegli ordini
   - Se vuoti: assegna FIFO agli ordini PENDING più vecchi
3. **Aggiornamento stato**:
   - Incrementa `qty_received` in `order_logs.csv`
   - Aggiorna `status`: PENDING → RECEIVED (se completo) o PARTIAL
4. **Eventi ledger**:
   - RECEIPT per qty ricevuta
   - UNFULFILLED per residui non consegnati (se ordine chiuso parzialmente)
5. **Log ricevimento**: scrive `document_id` + `order_ids` in `receiving_logs.csv`

**Return**:
- `transactions`: eventi creati
- `already_processed`: True se idempotente
- `order_updates`: {order_id: {qty_received_total, new_status}}

#### Metodo legacy `close_receipt()` → **DEPRECATED** (mantenuto per compatibilità)
- Warning log: "Use close_receipt_by_document for better traceability"

### 3. Query Inevasi

#### Nuovo metodo `CSVLayer.get_unfulfilled_orders()`

```python
def get_unfulfilled_orders(self, sku: Optional[str] = None) -> List[Dict]:
    """
    Returns orders with qty_received < qty_ordered.
    
    Returns: [{order_id, sku, qty_ordered, qty_received, qty_unfulfilled, status, receipt_date}]
    """
```

### 4. Persistenza Robusta

**Atomic writes**:
```python
def _append_csv_atomic(file, data):
    temp_file = file + ".tmp"
    # 1. Read existing
    # 2. Append new row
    # 3. Write to temp
    # 4. Atomic rename
```

**Auto-backup**:
```python
def _backup_file(file):
    if os.path.exists(file):
        backup = f"{file}.backup.{timestamp}"
        shutil.copy2(file, backup)
        # Keep only last 5 backups
```

### 5. Logging

**Sostituiti tutti `print()`** con:
```python
logger.info()   # Operazioni normali
logger.warning() # Situazioni anomale (qty > ordered, no pending orders)
logger.error()   # Errori (file I/O, validazione)
```

## File Modificati

1. **src/workflows/receiving.py** (~350 righe, +100):
   - `close_receipt_by_document()` nuovo metodo
   - `close_receipt()` deprecated wrapper
   - Logica assegnazione ordini FIFO
   - Gestione stati PENDING/PARTIAL/RECEIVED

2. **src/persistence/csv_layer.py** (~800 righe, +80):
   - `write_order_log()` con `qty_received`
   - `update_order_received_qty()` aggiorna ordini esistenti
   - `write_receiving_log()` con `document_id` + `order_ids`
   - `get_unfulfilled_orders()` query
   - `_append_csv_atomic()` + `_backup_file()`

3. **src/gui/app.py** (~5000 righe, modifiche minime):
   - Tab Ricevimento: campo "Numero Documento" invece di auto-generato
   - Visualizzazione inevasi per ordine (non aggregato SKU)
   - Chiamata a `close_receipt_by_document()` invece di `close_receipt()`

4. **tests/test_receiving_traceability.py** (nuovo, ~300 righe):
   - Scenario: 2 ordini stesso SKU
   - Consegna parziale doc1, completa doc2
   - Residuo non consegnato → UNFULFILLED
   - Idempotenza: ripeti doc1 → no duplicati

## Esecuzione Test

```bash
# Test specifico tracciabilità
python -m pytest tests/test_receiving_traceability.py -v

# Test completo sistema
python -m pytest tests/ -v

# Test manuale scenario
python examples/receiving_traceability_demo.py
```

## Backward Compatibility

✅ **100% compatibile**:
- CSV esistenti funzionano (colonne opzionali hanno default)
- Metodo `close_receipt()` ancora disponibile (deprecated)
- UI mostra warning per migrazione ma continua a funzionare

## Migration Path

1. **Automatica**: al primo avvio, colonne mancanti auto-create con default
2. **Manuale (opzionale)**: script `migrate_order_logs.py` per popolare `qty_received` da `receiving_logs` esistenti

## Rischi Mitigati

- **Duplicazione**: `document_id` previene ricevimenti ripetuti
- **Perdita dati**: backup automatico prima di ogni modifica critica
- **Inconsistenze**: atomic writes garantiscono stati validi
- **Regressione**: test coprono scenario legacy + nuovi

