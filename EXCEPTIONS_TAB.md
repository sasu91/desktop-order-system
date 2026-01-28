# Tab Eccezioni - Implementazione Completa

## Panoramica

Sistema completo per gestire eccezioni di magazzino (WASTE, ADJUST, UNFULFILLED) con form di quick entry inline, storico giornaliero filtrato per data e funzionalità di revert singolo e bulk.

## Funzionalità Implementate

### 1. **Interfaccia Grafica** ([app.py](src/gui/app.py))

#### Layout Tab Eccezioni

```
┌─ Exception Management ─────────────────────────────────────────┐
│                                                                 │
│ ┌─ Quick Entry ──────────────────────────────────────────────┐ │
│ │ Event Type: [WASTE▼]  SKU: [SKU001▼]  Quantity: [10]      │ │
│ │ Date: [2026-01-28]  Notes: [Damaged goods...]             │ │
│ │ [✓ Submit Exception] [✗ Clear Form]                       │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─ Exception History ────────────────────────────────────────┐ │
│ │ View Date: [2026-01-28] [🔄 Refresh] [📅 Today]           │ │
│ │ [🗑️ Revert Selected] [🗑️ Revert All...]                  │ │
│ ├───────────────────────────────────────────────────────────┤ │
│ │ Type    │ SKU    │ Qty │ Notes              │ Date       │ │
│ ├───────────────────────────────────────────────────────────┤ │
│ │ WASTE   │ SKU001 │ 10  │ Damaged goods      │ 2026-01-28 │ │
│ │ ADJUST  │ SKU002 │ -5  │ Count mismatch     │ 2026-01-28 │ │
│ │ UNFULFILLED │ SKU003 │ 3 │ Out of stock   │ 2026-01-28 │ │
│ └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

#### Componenti UI

##### **Quick Entry Form (Inline)**

Form integrato nella parte superiore del tab per registrazione rapida eccezioni:

**Campi:**
- **Event Type** (Dropdown, readonly): WASTE, ADJUST, UNFULFILLED
- **SKU** (Combobox): Lista di tutti gli SKU disponibili (popolata da `csv_layer.get_all_sku_ids()`)
- **Quantity** (Entry): Numero intero (signed, può essere negativo per ADJUST)
- **Date** (Entry): Formato YYYY-MM-DD (default: oggi)
- **Notes** (Entry): Testo libero opzionale

**Bottoni:**
- **✓ Submit Exception**: Registra eccezione nel ledger
- **✗ Clear Form**: Resetta tutti i campi

**Validazioni automatiche:**
- SKU non vuoto
- Quantity: intero valido
- Date: formato YYYY-MM-DD valido
- Event Type: solo WASTE/ADJUST/UNFULFILLED

##### **Exception History Table**

Treeview con storico eccezioni filtrato per data visualizzata.

**Colonne:**
- **Type**: Tipo di eccezione (WASTE, ADJUST, UNFULFILLED)
- **SKU**: Codice SKU
- **Qty**: Quantità (con segno + o - per ADJUST)
- **Notes**: Note utente (pulite dal prefix exception_key)
- **Date**: Data dell'eccezione

**Toolbar:**
- **View Date**: Campo data per filtrare eccezioni (default: oggi)
- **🔄 Refresh**: Ricarica tabella con data selezionata
- **📅 Today**: Imposta data view a oggi e ricarica
- **🗑️ Revert Selected**: Annulla eccezione selezionata (riga)
- **🗑️ Revert All...**: Apre dialog per revert bulk con filtri

---

### 2. **Workflow Backend** (già implementato in [receiving.py](src/workflows/receiving.py))

#### `ExceptionWorkflow.record_exception()`

```python
txn, already_recorded = exception_workflow.record_exception(
    event_type=EventType.WASTE,
    sku="SKU001",
    qty=10,
    event_date=date(2026, 1, 28),  # Optional, defaults to today
    notes="Damaged goods",
)
```

**Comportamento:**
- **Idempotenza**: Chiave = `{date}_{sku}_{event_type}`
  - Se già registrata oggi → `already_recorded=True`, nessuna scrittura
  - Se nuova → scrive in `transactions.csv`, `already_recorded=False`
- **Validazione**: Solo EventType.WASTE/ADJUST/UNFULFILLED (ValueError altrimenti)
- **Formato note**: `{exception_key}; {user_notes}`

#### `ExceptionWorkflow.revert_exception_day()`

```python
reverted_count = exception_workflow.revert_exception_day(
    event_date=date(2026, 1, 28),
    sku="SKU001",
    event_type=EventType.WASTE,
)
```

**Comportamento:**
- Annulla **tutte** le eccezioni di tipo specificato per SKU in una data
- Implementazione: rilegge `transactions.csv`, filtra escludendo match, riscrive
- Restituisce numero di entries annullate
- **Destructive**: modifica permanente del ledger

---

### 3. **Tipi di Eccezione**

| Tipo | Significato | Impatto Stock | Uso Tipico |
|------|------------|---------------|------------|
| **WASTE** | Scarto/danno | `on_hand -= qty` | Merci danneggiate, scadute, perse |
| **ADJUST** | Rettifica inventariale | `on_hand += qty` (qty signed) | Correzione errori di conteggio, inventario fisico |
| **UNFULFILLED** | Ordine non evaso (tracking) | Nessuno | Monitoraggio ordini non evasibili |

**Esempi:**

```python
# WASTE: 10 unità danneggiate
record_exception(EventType.WASTE, "SKU001", 10, notes="Broken packaging")
→ Stock: on_hand -= 10

# ADJUST: -5 unità (correzione in negativo)
record_exception(EventType.ADJUST, "SKU002", -5, notes="Count mismatch")
→ Stock: on_hand += (-5) = on_hand -= 5

# ADJUST: +3 unità (correzione in positivo)
record_exception(EventType.ADJUST, "SKU003", 3, notes="Found extra stock")
→ Stock: on_hand += 3

# UNFULFILLED: 2 unità non evadibili (tracking only)
record_exception(EventType.UNFULFILLED, "SKU004", 2, notes="Out of stock")
→ Stock: nessun impatto (solo tracking)
```

---

## Flussi di Lavoro

### Workflow: Registrazione Eccezione (Quick Entry)

1. Utente apre tab "Eccezioni"
2. Form quick entry già visibile in alto
3. Seleziona:
   - Event Type: `WASTE`
   - SKU: `SKU001` (dropdown auto-popolato)
   - Quantity: `10`
   - Date: `2026-01-28` (default oggi)
   - Notes: `Damaged during transport`
4. Clicca **✓ Submit Exception**
5. Validazioni automatiche:
   - ✓ SKU non vuoto
   - ✓ Quantity è intero
   - ✓ Date formato valido
6. Sistema chiama `exception_workflow.record_exception()`
7. Se `already_recorded=False`:
   - Messagebox: "Exception recorded successfully: WASTE - SKU001 - Qty: 10"
   - Form si resetta automaticamente
   - Tabella si refresh con nuovo entry
8. Se `already_recorded=True`:
   - Messagebox warning: "Exception of type WASTE for SKU 'SKU001' on 2026-01-28 was already recorded today."

### Workflow: Visualizzazione Storico Eccezioni

1. Utente seleziona data nel campo "View Date": `2026-01-20`
2. Clicca **🔄 Refresh** (o Enter nel campo data)
3. Sistema:
   - Legge tutte le transazioni da `transactions.csv`
   - Filtra per:
     - `event in [WASTE, ADJUST, UNFULFILLED]`
     - `date == 2026-01-20`
   - Ordina per data (implicitamente dal ledger)
4. Tabella mostra solo eccezioni del 20 gennaio
5. Bottone **📅 Today** riporta view a oggi

### Workflow: Revert Singolo (da Selezione Tabella)

1. Utente visualizza eccezioni di oggi
2. Seleziona riga nella tabella: `WASTE | SKU001 | 10 | Damaged goods | 2026-01-28`
3. Clicca **🗑️ Revert Selected**
4. Dialog conferma: "Revert all WASTE exceptions for SKU 'SKU001' on 2026-01-28? This action cannot be undone."
5. Utente clicca **Yes**
6. Sistema chiama `exception_workflow.revert_exception_day()`
7. Risultato: tutte le WASTE per SKU001 del 28/01 vengono rimosse dal ledger
8. Messagebox: "Reverted 1 exception(s) for WASTE - SKU001 on 2026-01-28."
9. Tabella si refresh automaticamente

**Nota**: Revert singolo in realtà annulla **tutte** le eccezioni dello stesso tipo/SKU/data (per design idempotenza → max 1 entry per tipo/sku/data)

### Workflow: Revert Bulk (con Filtri)

1. Utente clicca **🗑️ Revert All...**
2. Si apre popup dialog "Bulk Revert Exceptions" con campi:
   - Event Type: `WASTE` (dropdown)
   - SKU: `SKU001` (combobox)
   - Date: `2026-01-28` (entry, pre-popolata da view date)
3. Utente compila filtri (es. WASTE per SKU001 del 28/01)
4. Clicca **Revert**
5. Dialog conferma: "Revert ALL WASTE exceptions for SKU 'SKU001' on 2026-01-28? This action cannot be undone."
6. Utente conferma
7. Sistema reverte tutte le match
8. Messagebox: "Reverted N exception(s)."
9. Dialog si chiude, tabella principale si refresh

**Caso d'uso bulk**: Annullare tutte le WASTE per un SKU in un giorno (anche se ci sono più SKU con WASTE)

---

## Validazioni Implementate

### 1. **Validazione Form Quick Entry**

#### SKU
- **Non vuoto**: Messagebox error se non selezionato
- **Esistenza**: Dropdown popolato da SKU validi (auto-validato)

#### Quantity
- **Tipo intero**: Messagebox error se non è numero
- **Signed**: Accettato (negativo per ADJUST)
- **Esempio validi**: `10`, `-5`, `0`
- **Esempio invalidi**: `abc`, `10.5` → error

#### Date
- **Formato ISO**: YYYY-MM-DD
- **Messagebox error** se formato invalido
- **Default**: oggi (`date.today()`)

#### Event Type
- **Readonly dropdown**: Solo WASTE/ADJUST/UNFULFILLED
- **Backend validation**: ValueError se tipo invalido (safety check)

### 2. **Validazione Revert**

#### Conferma Utente
- **Messagebox askyesno** prima di ogni revert
- **Warning chiaro**: "This action cannot be undone"
- **Dettagli**: Mostra tipo, SKU, data che saranno reverted

#### Feedback
- **Success**: "Reverted N exception(s)" con numero esatto
- **No matches**: "No exceptions found to revert" (count=0)
- **Error**: Messagebox error con stack trace

---

## Decisioni Tecniche

### 1. **Form Inline (non Popup)**
- ✅ Quick entry visibile sempre in cima al tab
- ✅ UX più veloce: nessun click extra per aprire popup
- ✅ Workflow ottimizzato: submit → clear → ripeti
- Decisione utente: "1. inline"

### 2. **Date Picker Singola (no Range)**
- ✅ Campo "View Date" per filtrare tabella per giorno specifico
- ✅ Bottone "Today" per tornare rapidamente a oggi
- ✅ Default: oggi (`date.today()`)
- ⚠️ Limitation: visualizzazione solo per singola data (no range)
- Decisione utente: "2. prima opzione"

### 3. **Revert Singolo + Bulk (Entrambi)**
- ✅ **Revert Selected**: Click su riga → revert tipo/SKU/data di quella riga
- ✅ **Revert All**: Dialog con filtri manuali (tipo, SKU, data)
- Decisione utente: "3. entrambi"

### 4. **Idempotenza del Backend**
- ✅ Max 1 eccezione per `{date}_{sku}_{event_type}`
- ✅ Tentativo di re-registrazione → messagebox warning (non error)
- ✅ Revert annulla **tutte** le match (anche se tecnicamente solo 1 per idempotenza)

### 5. **Storage nel Ledger (Non Tabella Separata)**
- ✅ Eccezioni scritte direttamente in `transactions.csv`
- ✅ Single source of truth
- ✅ Calcolo stock integra automaticamente eccezioni (priority=2 per WASTE/ADJUST)
- ⚠️ Limitation: Nessun audit trail separato (cancellazione è definitiva)

---

## Integrazione con Architettura Esistente

### ✅ Rispetta Principi del Progetto

1. **Ledger-Driven Architecture**: Eccezioni sono eventi normali nel ledger, calcolati in stock AsOf
2. **Deterministic Logic**: Workflow usa date parametrica (no `datetime.now()` in business logic)
3. **Idempotency**: `record_exception()` idempotente per chiave `{date}_{sku}_{type}`
4. **CSV Auto-Create**: Nessuna modifica a schema CSV esistente (usa transactions.csv)
5. **Graceful Error Handling**: Messagebox chiari, nessun crash
6. **Priority System**: WASTE/ADJUST applicati con priority=2 (dopo SNAPSHOT/ORDER/RECEIPT, con SALE)

### 🔗 Integrazione Stock Calculation

**Da [ledger.py](src/domain/ledger.py):**

```python
# Event priority
EVENT_PRIORITY = {
    EventType.SNAPSHOT: 0,
    EventType.RECEIPT: 1,
    EventType.ORDER: 1,
    EventType.SALE: 2,
    EventType.WASTE: 2,      # Same as SALE
    EventType.ADJUST: 2,
    EventType.UNFULFILLED: 3,
}

# Application logic
if txn.event == EventType.WASTE:
    on_hand -= txn.qty
elif txn.event == EventType.ADJUST:
    on_hand += txn.qty  # qty is signed
elif txn.event == EventType.UNFULFILLED:
    pass  # Tracking only
```

**Esempio calcolo stock AsOf:**

```
Date: 2026-01-28
Initial stock: on_hand=100

Event sequence (sorted by priority):
1. SNAPSHOT (priority 0): on_hand = 100
2. RECEIPT (priority 1): +50 → on_hand = 150
3. SALE (priority 2): -20 → on_hand = 130
4. WASTE (priority 2): -10 → on_hand = 120  ← Exception
5. ADJUST (priority 2): -5 → on_hand = 115  ← Exception
6. UNFULFILLED (priority 3): tracking only  ← Exception

Final stock AsOf 2026-01-28: on_hand=115, on_order=0
```

---

## Test Manuale Rapido

### Script di Test Automatico
```bash
python test_exceptions_management.py
```

Questo script:
1. Crea 3 SKU di test
2. Registra WASTE exception
3. Verifica idempotenza (re-record → already_recorded=True)
4. Registra ADJUST con qty negativa
5. Registra UNFULFILLED
6. Filtra eccezioni per data
7. Reverte WASTE per SKU001
8. Verifica revert selettivo (solo WASTE rimossa, ADJUST rimane)
9. Cleanup automatico

### Test GUI

1. **Avvia applicazione:**
   ```bash
   python main.py
   ```

2. **Vai al tab "Eccezioni"**

3. **Test quick entry:**
   - Seleziona: Type=WASTE, SKU=SKU001, Qty=10, Notes="Test damage"
   - Clicca **✓ Submit Exception**
   - Messagebox: "Exception recorded successfully"
   - Form si resetta automaticamente
   - Tabella mostra nuova entry

4. **Test idempotenza:**
   - Re-inserisci stessi dati (WASTE, SKU001, oggi)
   - Submit → Messagebox warning: "Already recorded"

5. **Test visualizzazione storico:**
   - Cambia "View Date" a ieri
   - Refresh → tabella vuota (nessuna eccezione ieri)
   - Clicca **📅 Today** → torna a oggi, mostra eccezioni

6. **Test revert singolo:**
   - Seleziona riga WASTE nella tabella
   - Clicca **🗑️ Revert Selected**
   - Conferma → Messagebox "Reverted 1 exception(s)"
   - Tabella si aggiorna, entry rimossa

7. **Test revert bulk:**
   - Registra ADJUST per SKU002
   - Clicca **🗑️ Revert All...**
   - Popup: Type=ADJUST, SKU=SKU002, Date=oggi
   - Revert → Messagebox conferma
   - Tabella refresh, ADJUST rimosso

---

## File Modificati

### 1. [src/gui/app.py](src/gui/app.py)
- **Modificato** import: aggiunto `EventType`
- **Aggiunto** `self.exception_date = date.today()` in `__init__`
- **Riscritto** completamente `_build_exception_tab()` (~360 righe)
- **Aggiunti** 10 nuovi metodi privati:
  - `_populate_exception_sku_dropdown()`
  - `_clear_exception_form()`
  - `_submit_exception()`
  - `_refresh_exception_tab()`
  - `_set_exception_today()`
  - `_revert_selected_exception()`
  - `_revert_bulk_exceptions()`
- **Modificato** `_refresh_all()` per includere `_refresh_exception_tab()`
- **Linee aggiunte**: ~380

### 2. [test_exceptions_management.py](test_exceptions_management.py)
- **Creato** nuovo script di test manuale
- **Linee**: ~180

---

## Limitations e Future Enhancements

### Limitations Attuali

1. **Nessun Audit Trail**: Revert è distruttivo (no soft delete, no history log)
2. **Visualizzazione per Singola Data**: No range date selector
3. **Nessun Bulk Insert**: Form registra 1 eccezione alla volta
4. **No Export**: Impossibile esportare storico eccezioni a CSV/Excel
5. **No Filtro per SKU nella Tabella**: View mostra tutte le eccezioni della data (no filtro live per SKU)

### Possibili Enhancement Futuri

1. **Audit Log Eccezioni**
   - File separato `exception_audit.csv` con storico revert
   - Colonne: timestamp, user, action (record/revert), details

2. **Range Date Selector**
   - Widget calendario per selezione range
   - Tabella mostra eccezioni per intervallo

3. **Bulk Exception Entry**
   - Import da CSV: `sku,event_type,qty,notes`
   - Form multi-row per registrare più eccezioni insieme

4. **Export Functionality**
   - Export tabella eccezioni a CSV/Excel
   - Filtro avanzato: date range, tipo, SKU

5. **Live Search/Filter**
   - Search box per filtrare tabella per SKU/tipo/note
   - Client-side filtering real-time

6. **Statistiche Eccezioni**
   - Widget riassuntivo: "Oggi: 5 WASTE, 3 ADJUST, 1 UNFULFILLED"
   - Grafico trend eccezioni per tipo

7. **Undo Last Exception**
   - Bottone "Undo Last" per annullare ultima eccezione registrata
   - Più user-friendly del revert completo

---

## Note Tecniche

### Performance
- **Filtro eccezioni**: O(n) con n = numero totale transazioni
  - Per dataset grandi (>10K transazioni), può rallentare
  - Soluzione futura: index by date/event_type in CSVLayer
- **Revert operation**: Rewrite completo di `transactions.csv`
  - O(n) con n = numero totale transazioni
  - Accettabile per <100K righe

### Formato Note nel Ledger
```
{exception_key}; {user_notes}

Esempio:
"2026-01-28_SKU001_WASTE; Damaged during transport"

Parsing in UI:
notes.split(";", 1)[1].strip()  → "Damaged during transport"
```

---

## Supporto

Per problemi o domande:
1. Verifica errori sintattici: `python -m py_compile src/gui/app.py`
2. Esegui test workflow: `python test_exceptions_management.py`
3. Controlla log GUI per errori runtime (stdout/stderr)

---

**Data implementazione**: Gennaio 2026  
**Versione**: 1.0 - Tab Eccezioni Completo  
**Form type**: Inline quick entry  
**Revert modes**: Singolo + Bulk  
**File modificati**: 1 (app.py)  
**Linee aggiunte**: ~380
