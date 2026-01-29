# Feature: Inserimento Stock Fine Giornata (EOD)

## Descrizione
Aggiunta la possibilità di inserire lo stock on hand a fine giornata e calcolare automaticamente il venduto conseguente.

## Componenti Implementati

### 1. **Domain Logic** (`src/domain/ledger.py`)
- **Funzione**: `calculate_sold_from_eod_stock()`
  - Calcola il venduto giornaliero confrontando stock teorico e stock dichiarato EOD
  - Parametri: `sku`, `eod_date`, `eod_stock_on_hand`, `transactions`, `sales_records`
  - Ritorna: `(qty_sold, adjustment)` tuple
  - Logica:
    1. Calcola stock teorico a inizio giornata
    2. Calcola stock teorico a fine giornata (escludendo vendite)
    3. Venduto = stock_teorico_fine - stock_dichiarato_eod
    4. Adjustment = discrepanza residua (es. shrinkage)

### 2. **Workflow** (`src/workflows/daily_close.py`)
- **Classe**: `DailyCloseWorkflow`
- **Metodi principali**:
  - `process_eod_stock()`: Processa singolo SKU
    - Scrive vendite in `sales.csv`
    - Crea evento `ADJUST` nel ledger se necessario
    - Ritorna: `(SalesRecord, Transaction, status_message)`
  - `process_bulk_eod_stock()`: Processa più SKU in batch
    - Parametri: `{sku: eod_stock}` dict
    - Ritorna: lista di messaggi status
- **Validazioni**:
  - SKU esistente
  - Stock EOD ≥ 0
  - Idempotenza (update se già registrato)

### 3. **Persistence** (`src/persistence/csv_layer.py`)
- Aggiunti metodi:
  - `append_sales(sale)`: Append singola vendita
  - `write_sales(sales_list)`: Sovrascrive intero file (per update)

### 4. **GUI** (`src/gui/app.py`)

#### Tab Stock - Modifiche:
1. **Colonna "Stock EOD 📝"** aggiunta alla treeview
2. **Edit in-place**: Doppio click sulla colonna EOD
   - Popup dialog per inserire stock
   - Riga evidenziata in giallo (tag `eod_edited`)
   - Valori staged in `self.eod_stock_edits` dict
3. **Barra ricerca** sotto tabella
   - Filtra per codice SKU o descrizione
   - Real-time filtering
4. **Pulsante "✓ Conferma Chiusura Giornaliera"**
   - Processa tutti gli EOD edits in batch
   - Mostra risultati in messagebox
   - Refresh automatico tabella e audit

#### Metodi aggiunti:
- `_on_stock_eod_double_click()`: Gestisce doppio click
- `_filter_stock_table()`: Filtra tabella con search
- `_confirm_eod_close()`: Conferma batch EOD processing

### 5. **Test** (`tests/test_workflows.py`)
Aggiunti test per `TestDailyCloseWorkflow`:
- `test_process_eod_stock_basic`: Test base calcolo venduto
- `test_process_eod_stock_with_adjustment`: Test con shrinkage
- `test_process_eod_stock_idempotency`: Test update senza duplicati
- `test_process_bulk_eod_stock`: Test batch processing
- `test_process_eod_invalid_sku`: Test validazione SKU
- `test_process_eod_negative_stock`: Test validazione stock negativo

## Workflow Utente

### Scenario: Inserimento Stock Fine Giornata

1. **Apertura Tab Stock**
   - Visualizza tabella con colonne: SKU, Descrizione, Disponibile, In Ordine, Totale, **Stock EOD**

2. **Inserimento EOD (opzionale: usa ricerca)**
   - Digita nella barra ricerca per filtrare SKU
   - **Doppio click** su colonna "Stock EOD" per SKU desiderato
   - Dialog popup: inserisci stock contato a fine giornata
   - Conferma → riga diventa gialla, valore mostrato in colonna

3. **Ripeti per più SKU**
   - Ogni edit viene staged localmente
   - Nessuna scrittura fino a conferma

4. **Conferma Chiusura**
   - Click su "✓ Conferma Chiusura Giornaliera"
   - Popup conferma: mostra numero SKU e data
   - Sistema processa:
     - Calcola venduto per ogni SKU
     - Scrive vendite in `sales.csv`
     - Crea `ADJUST` se discrepanza
   - Mostra risultati con status per ogni SKU
   - Refresh automatico tabella

### Esempio Output Status:
```
✓ SKU001 | Venduto: 15
✓ SKU002 | Venduto: 20 | Rettifica: -2
✓ SKU003 | Nessun cambiamento (stock teorico = EOD)
```

## Dati Scritti

### `data/sales.csv`
```csv
date,sku,qty_sold
2026-01-29,SKU001,15
2026-01-29,SKU002,20
```

### `data/transactions.csv` (se adjustment necessario)
```csv
date,sku,event,qty,receipt_date,note
2026-01-29,SKU002,ADJUST,78,,EOD adjustment (discrepancy: -2)
```

## Architettura

```
User Input (EOD Stock)
   ↓
GUI: _on_stock_eod_double_click() → stage in self.eod_stock_edits
   ↓
GUI: _confirm_eod_close() → batch process
   ↓
DailyCloseWorkflow.process_bulk_eod_stock()
   ↓
   ├→ calculate_sold_from_eod_stock() [domain logic]
   ├→ CSVLayer.append_sales() / write_sales()
   └→ CSVLayer.append_transaction() [ADJUST if needed]
   ↓
GUI: refresh tables & show results
```

## Vantaggi

1. **Doppia registrazione**:
   - Vendite in `sales.csv` (usato per calcolo proposte ordine)
   - ADJUST in ledger (allinea stock teorico e fisico)

2. **Idempotenza**:
   - Update invece di duplicazione se stesso SKU/data

3. **Audit trail completo**:
   - Ogni EOD genera eventi tracciabili
   - Discrepanze visibili come ADJUST

4. **UX efficiente**:
   - Edit in-place (no form popup complesso)
   - Ricerca integrata
   - Batch processing (conferma una volta per tutti)

5. **Validazione robusta**:
   - SKU esistente
   - Stock non negativo
   - Messaggi errore chiari

## Limitazioni / Note

- **Data EOD**: Usa `asof_date` dal tab Stock (non data sistema)
  - L'utente deve impostare correttamente la data AsOf prima di inserire EOD
- **No undo**: Una volta confermato, le vendite/ADJUST sono scritte
  - Reversione manuale: edit CSV o nuovo ADJUST correttivo
- **Calcolo venduto**: Assume che delta stock = vendite + shrinkage
  - Non distingue automaticamente tra vendita e perdita (tutto va in qty_sold + optional ADJUST)

## File Modificati

- `src/domain/ledger.py`: +65 righe (funzione `calculate_sold_from_eod_stock`)
- `src/workflows/daily_close.py`: +135 righe (nuovo file)
- `src/workflows/__init__.py`: +6 righe (export)
- `src/persistence/csv_layer.py`: +12 righe (metodi `append_sales`, `write_sales`)
- `src/gui/app.py`: ~100 righe (colonna, edit, ricerca, conferma)
- `tests/test_workflows.py`: +195 righe (test suite)

## Testing

Eseguire test con:
```bash
pytest tests/test_workflows.py::TestDailyCloseWorkflow -v
```

Test manuale GUI:
1. Avvia app: `python main.py`
2. Tab Stock → doppio click colonna EOD
3. Inserisci valore → conferma chiusura
4. Verifica `data/sales.csv` e `data/transactions.csv`

---
**Implementato**: 29 Gennaio 2026  
**Status**: ✅ Completato e testato
