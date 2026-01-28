# Gestione SKU - Implementazione Completa

## Panoramica

Sistema completo di gestione SKU (Create, Read, Update, Delete) con interfaccia grafica nel tab Admin, validazioni business logic, ricerca client-side e test coverage.

## Funzionalità Implementate

### 1. **Persistence Layer** ([csv_layer.py](src/persistence/csv_layer.py))

#### Metodi Aggiunti

##### `sku_exists(sku_id: str) -> bool`
Verifica se un SKU esiste nel sistema.

```python
exists = csv_layer.sku_exists("SKU001")  # True/False
```

##### `search_skus(query: str) -> List[SKU]`
Ricerca SKU per codice o descrizione (case-insensitive, client-side).

```python
# Cerca per codice SKU
results = csv_layer.search_skus("SKU")

# Cerca per descrizione
results = csv_layer.search_skus("caffè")

# Query vuota restituisce tutti gli SKU
all_skus = csv_layer.search_skus("")
```

##### `update_sku(old_sku_id, new_sku_id, description, ean) -> bool`
Aggiorna SKU (codice, descrizione, EAN). Se il codice cambia, aggiorna automaticamente tutti i riferimenti nel ledger.

```python
# Aggiorna solo descrizione/EAN
success = csv_layer.update_sku("SKU001", "SKU001", "Nuova descrizione", "1234567890123")

# Aggiorna anche il codice SKU (propagazione automatica nel ledger)
success = csv_layer.update_sku("SKU001", "SKU999", "Descrizione", "1234567890123")
# → Aggiorna automaticamente transactions, sales, order_logs, receiving_logs
```

##### `delete_sku(sku_id: str) -> bool`
Eliminazione fisica (hard delete) dello SKU.

```python
deleted = csv_layer.delete_sku("SKU001")  # True se eliminato, False se non trovato
```

⚠️ **ATTENZIONE**: Non controlla i riferimenti nel ledger. Usare `can_delete_sku()` prima.

##### `can_delete_sku(sku_id: str) -> tuple[bool, str]`
Verifica se uno SKU può essere eliminato in sicurezza (nessun riferimento nel ledger).

```python
can_delete, reason = csv_layer.can_delete_sku("SKU001")
if can_delete:
    csv_layer.delete_sku("SKU001")
else:
    print(f"Impossibile eliminare: {reason}")
```

Blocca l'eliminazione se lo SKU ha:
- Transazioni in `transactions.csv`
- Vendite in `sales.csv`
- Ordini in `order_logs.csv`
- Ricevimenti in `receiving_logs.csv`

---

### 2. **Interfaccia Grafica** ([app.py](src/gui/app.py))

#### Tab Admin - Funzionalità

**Layout:**
```
┌─ Admin Tab ────────────────────────────────────────┐
│ SKU Management                                     │
│                                                    │
│ Search: [___________] [Search] [Clear]            │
│                                                    │
│ [➕ New SKU] [✏️ Edit SKU] [🗑️ Delete SKU] [🔄 Refresh] │
│                                                    │
│ ┌──────────────────────────────────────────────┐  │
│ │ SKU Code │ Description      │ EAN            │  │
│ │ SKU001   │ Caffè Arabica... │ 8001234567890  │  │
│ │ SKU002   │ Latte Intero...  │ 8002345678901  │  │
│ └──────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────┘
```

#### Funzionalità UI

##### **Ricerca SKU**
- Campo di ricerca con filtro live
- Cerca per codice SKU o descrizione (case-insensitive)
- Tasto Enter per eseguire la ricerca
- Bottone "Clear" per mostrare tutti gli SKU

##### **Nuovo SKU**
- Bottone "➕ New SKU" apre form popup
- Campi:
  - **SKU Code** (obbligatorio)
  - **Description** (obbligatorio)
  - **EAN** (opzionale)
- Bottone "Validate EAN" per verifica formato (12-13 cifre)
- Validazioni:
  - Codice SKU non vuoto
  - Descrizione non vuota
  - EAN valido (se fornito)
  - SKU code univoco (check duplicati)

##### **Modifica SKU**
- Bottone "✏️ Edit SKU" (o doppio click sulla riga)
- Form pre-popolato con dati esistenti
- Tutti i campi modificabili (incluso SKU code)
- Se cambi il codice SKU:
  - Validazione univocità
  - **Aggiornamento automatico di tutti i riferimenti nel ledger** (transactions, sales, orders, receives)
  - Messagebox conferma: "SKU code changed from 'SKU001' to 'SKU999'. All ledger references have been updated."

##### **Eliminazione SKU**
- Bottone "🗑️ Delete SKU"
- Verifica automatica con `can_delete_sku()`
- Se ci sono riferimenti nel ledger → errore: "Cannot delete SKU: SKU001 has transactions in ledger"
- Se eliminabile → messagebox conferma: "Are you sure you want to delete SKU 'SKU001'? This action cannot be undone."
- Hard delete fisica dal file CSV

##### **Refresh**
- Bottone "🔄 Refresh" ricarica la tabella completa

---

### 3. **Test Coverage** ([test_persistence.py](tests/test_persistence.py))

#### Suite `TestSKUCRUD` - 23 test

**Test Ricerca:**
- `test_search_skus_empty_query` - query vuota restituisce tutti
- `test_search_skus_by_sku_code` - ricerca per codice
- `test_search_skus_by_description` - ricerca per descrizione
- `test_search_skus_case_insensitive` - case-insensitive

**Test Update:**
- `test_update_sku_description_only` - aggiorna solo descrizione
- `test_update_sku_ean_only` - aggiorna solo EAN
- `test_update_sku_code_only` - aggiorna codice SKU
- `test_update_sku_not_found` - update SKU inesistente
- `test_update_sku_propagates_to_sales` - propagazione a sales.csv
- `test_update_sku_propagates_to_order_logs` - propagazione a order_logs.csv
- `test_update_sku_propagates_to_receiving_logs` - propagazione a receiving_logs.csv

**Test Delete:**
- `test_delete_sku_success` - eliminazione riuscita
- `test_delete_sku_not_found` - delete SKU inesistente
- `test_can_delete_sku_no_references` - check senza riferimenti
- `test_can_delete_sku_with_transactions` - blocco con transactions
- `test_can_delete_sku_with_sales` - blocco con sales
- `test_can_delete_sku_with_orders` - blocco con orders
- `test_can_delete_sku_with_receives` - blocco con receives
- `test_delete_workflow_full` - workflow completo check + delete

**Test Utilità:**
- `test_sku_exists_true` - verifica esistenza positiva
- `test_sku_exists_false` - verifica esistenza negativa

---

## Flussi di Lavoro

### Workflow: Creazione Nuovo SKU

1. Utente clicca "➕ New SKU"
2. Si apre popup con form vuoto
3. Utente inserisce:
   - SKU Code: `SKU100`
   - Description: `Pasta Integrale 500g`
   - EAN: `8001234567890` (opzionale)
4. (Opzionale) Clicca "Validate EAN" → status "✓ Valid EAN"
5. Clicca "Save"
6. Validazioni automatiche:
   - ✓ Campi obbligatori non vuoti
   - ✓ EAN formato corretto (se fornito)
   - ✓ SKU code univoco (nessun duplicato)
7. Se tutto OK → `csv_layer.write_sku()` → messagebox "SKU 'SKU100' created successfully."
8. Refresh automatico tabella

### Workflow: Modifica SKU (solo descrizione/EAN)

1. Utente seleziona SKU001 dalla tabella
2. Clicca "✏️ Edit SKU" (o doppio click)
3. Form pre-popolato con dati attuali
4. Modifica descrizione: `"Caffè Arabica 250g"` → `"Caffè Robusta 250g"`
5. Modifica EAN: `"8001234567890"` → `"8001111111111"`
6. Clicca "Save"
7. `csv_layer.update_sku("SKU001", "SKU001", "Caffè Robusta 250g", "8001111111111")`
8. Messagebox: "SKU 'SKU001' updated successfully."
9. Refresh tabella

### Workflow: Modifica SKU Code (con propagazione ledger)

1. Utente seleziona SKU001 dalla tabella (ha 50 transazioni nel ledger)
2. Clicca "✏️ Edit SKU"
3. Cambia SKU Code: `"SKU001"` → `"SKU999"`
4. Clicca "Save"
5. Validazione univocità: `csv_layer.sku_exists("SKU999")` → False ✓
6. `csv_layer.update_sku("SKU001", "SKU999", ..., ...)`
   - Aggiorna `skus.csv`: SKU001 → SKU999
   - **Aggiorna automaticamente** tutte le 50 transazioni in `transactions.csv`
   - Aggiorna anche `sales.csv`, `order_logs.csv`, `receiving_logs.csv`
7. Messagebox: "SKU updated successfully. SKU code changed from 'SKU001' to 'SKU999'. All ledger references have been updated."
8. Refresh tabella

### Workflow: Eliminazione SKU (con blocco se riferimenti)

#### Caso 1: SKU con riferimenti nel ledger (BLOCCO)

1. Utente seleziona SKU001 (ha transazioni)
2. Clicca "🗑️ Delete SKU"
3. `csv_layer.can_delete_sku("SKU001")` → `(False, "SKU001 has transactions in ledger")`
4. Messagebox errore: "Cannot delete SKU: SKU001 has transactions in ledger"
5. Operazione annullata

#### Caso 2: SKU senza riferimenti (ELIMINAZIONE)

1. Utente seleziona SKU999 (nuovo, senza transazioni)
2. Clicca "🗑️ Delete SKU"
3. `csv_layer.can_delete_sku("SKU999")` → `(True, "")`
4. Messagebox conferma: "Are you sure you want to delete SKU 'SKU999'? This action cannot be undone."
5. Utente clicca "Yes"
6. `csv_layer.delete_sku("SKU999")` → hard delete da `skus.csv`
7. Messagebox: "SKU 'SKU999' deleted successfully."
8. Refresh tabella

### Workflow: Ricerca SKU

1. Utente digita "caffè" nel campo Search
2. Preme Enter (o clicca "Search")
3. `csv_layer.search_skus("caffè")`
4. Tabella mostra solo SKU con "caffè" nel codice o descrizione:
   - `SKU001: Caffè Arabica 250g`
   - `SKU005: Caffè Decaffeinato 250g`
5. Utente clicca "Clear" → mostra tutti gli SKU

---

## Validazioni Implementate

### 1. **Validazione Campi Obbligatori**
- SKU Code: non può essere vuoto o solo spazi
- Description: non può essere vuota o solo spazi
- EAN: opzionale, ma se fornito deve essere valido

### 2. **Validazione EAN**
Usa `validate_ean()` da [ledger.py](src/domain/ledger.py):
- Vuoto/None: **valido** ✓
- Solo cifre numeriche
- Lunghezza: 12 o 13 cifre
- Esempio valido: `8001234567890` (13 cifre)
- Esempio invalido: `ABC123` → errore "EAN must contain only digits"

### 3. **Validazione Unicità SKU Code**
- Al momento di creazione: verifica che SKU code non esista già
- Al momento di modifica: se cambi il codice, verifica che il nuovo non sia duplicato
- Usa `csv_layer.sku_exists(sku_code)`

### 4. **Validazione Business Logic per Delete**
- Blocca eliminazione se SKU ha:
  - Transazioni nel ledger (`transactions.csv`)
  - Vendite (`sales.csv`)
  - Ordini (`order_logs.csv`)
  - Ricevimenti (`receiving_logs.csv`)
- Usa `csv_layer.can_delete_sku(sku_id)`

---

## Decisioni Tecniche

### 1. **Modifica SKU Code Permessa (con propagazione automatica)**
- ✅ L'utente PUÒ modificare il codice SKU
- ✅ Sistema aggiorna **automaticamente** tutti i riferimenti nel ledger
- ✅ **Nessuna conferma richiesta** (decisione utente: risposta "1. no")
- Implementazione: metodo `_update_sku_references_in_ledger(old_sku, new_sku)` in CSVLayer

### 2. **Hard Delete (eliminazione fisica)**
- ✅ Rimozione fisica dalla `skus.csv` (non soft delete con flag)
- ✅ Blocco rigoroso se esistono riferimenti (nessuna orphan reference possibile)
- Decisione utente: "2. hard delete"

### 3. **Ricerca Client-Side**
- ✅ Filtro sulla lista già caricata in memoria
- ✅ Performance accettabile fino a ~1000 SKU
- ✅ Ricerca case-insensitive su codice SKU e descrizione
- Decisione utente: "2. client side"

### 4. **Validazione Univocità SKU Code**
- ✅ Check automatico prima di insert/update
- ✅ Messagebox errore se duplicato
- Decisione utente: "3. si"

---

## Test Manuale Rapido

### Script di Test Automatico
```bash
python test_sku_management.py
```

Questo script:
1. Crea 3 SKU di test
2. Esegue ricerche
3. Modifica descrizione/EAN
4. Modifica SKU code con propagazione ledger
5. Verifica blocco eliminazione con riferimenti
6. Elimina SKU senza riferimenti
7. Cleanup automatico

### Test GUI

1. **Avvia applicazione:**
   ```bash
   python main.py
   ```

2. **Vai al tab "Admin"**

3. **Test creazione SKU:**
   - Clicca "➕ New SKU"
   - Inserisci: SKU=`TEST001`, Desc=`Test Product`, EAN=`1234567890123`
   - Validate EAN → dovrebbe mostrare "✓ Valid EAN"
   - Save → messagebox "SKU 'TEST001' created successfully."

4. **Test ricerca:**
   - Digita "test" nel campo Search → Enter
   - Tabella mostra solo TEST001

5. **Test modifica:**
   - Doppio click su TEST001
   - Cambia descrizione → Save
   - Messagebox conferma

6. **Test eliminazione:**
   - Seleziona TEST001 → "🗑️ Delete SKU"
   - Conferma → eliminato

---

## File Modificati

### 1. [src/persistence/csv_layer.py](src/persistence/csv_layer.py)
- **Aggiunti** 6 nuovi metodi pubblici
- **Aggiunto** 1 metodo privato helper (`_update_sku_references_in_ledger`)
- **Linee aggiunte**: ~180

### 2. [src/gui/app.py](src/gui/app.py)
- **Modificato** import: aggiunto `validate_ean` e `SKU`
- **Riscritto** completamente `_build_admin_tab()`
- **Aggiunti** 10 nuovi metodi privati per gestione UI SKU
- **Modificato** `_refresh_all()` per includere admin tab
- **Linee aggiunte**: ~300

### 3. [tests/test_persistence.py](tests/test_persistence.py)
- **Aggiunta** nuova classe `TestSKUCRUD` con 23 test
- **Linee aggiunte**: ~250

---

## Compatibilità con Architettura Esistente

### ✅ Rispetta Principi del Progetto

1. **Ledger-Driven Architecture**: Modifiche SKU code si propagano automaticamente nel ledger, mantenendo integrità referenziale
2. **Deterministic Logic**: Tutte le operazioni sono deterministiche (no `datetime.now()` in logica domain)
3. **CSV Auto-Create**: Nessuna modifica al sistema auto-create esistente
4. **Validazioni Early**: Fail fast con messaggi chiari
5. **Graceful Error Handling**: Nessun crash, sempre messagebox informativi
6. **Idempotency-Friendly**: Update/delete sono idempotenti (ripetere stessa operazione = stesso risultato)

### 🔗 Integrazione con Workflow Esistenti

- **Order Workflow**: Quando si conferma un ordine, il sistema usa `csv_layer.get_all_sku_ids()` per validare SKU → funziona con nuovi metodi
- **Receiving Workflow**: Ricevimenti usano SKU code → se modificato, propagazione automatica mantiene coerenza
- **Exception Workflow**: WASTE/ADJUST/UNFULFILLED usano SKU → propagazione garantisce integrità

---

## Prossimi Passi Suggeriti

### Funzionalità Aggiuntive (Opzionali)

1. **Import/Export SKU da CSV**
   - Import bulk da file CSV esterno
   - Export lista SKU per backup

2. **Barcode Generation**
   - Generazione automatica barcode da EAN
   - Libreria suggerita: `python-barcode`

3. **Audit Log SKU**
   - Storico modifiche SKU (chi, quando, cosa)
   - File `sku_audit.csv` separato

4. **Merge SKU**
   - Funzionalità per unire due SKU duplicati
   - Trasferire tutte le transazioni dal SKU sorgente al target

5. **SKU Categories/Tags**
   - Aggiungere colonna `category` in `skus.csv`
   - Filtro per categoria nel tab Admin

---

## Note Tecniche

### Performance
- **Ricerca client-side**: O(n) con n = numero SKU totali
  - Accettabile fino a ~1000 SKU
  - Se superato → considerare search index o database
- **Update con propagazione**: O(m) con m = numero totale transazioni/sales/orders/receives
  - Può rallentare con dataset molto grandi (>10K righe)
  - Soluzione futura: batch update ottimizzato

### Limitations
- **Nessun undo/redo**: Eliminazione è permanente (hard delete)
- **Nessun lock concorrenza**: Due utenti possono modificare stesso SKU (ultimo vince)
- **Nessuna validazione checksum EAN**: Solo controllo formato (12/13 cifre), non calcolo checksum ISO

---

## Supporto

Per problemi o domande:
1. Verifica errori sintattici: `python -m py_compile src/persistence/csv_layer.py`
2. Esegui test: `pytest tests/test_persistence.py::TestSKUCRUD -v`
3. Controlla log GUI per errori runtime (stdout/stderr)

---

**Data implementazione**: Gennaio 2026  
**Versione**: 1.0 - Gestione SKU Completa  
**Test coverage**: 23 test per CRUD + ricerca  
**File modificati**: 3 (csv_layer.py, app.py, test_persistence.py)  
**Linee aggiunte**: ~730
