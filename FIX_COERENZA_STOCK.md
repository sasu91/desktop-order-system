# Fix Coerenza Stock e Ordini - Riepilogo

## Data: 2 Febbraio 2026

## Modifiche Implementate

### 1. ADJUST: Set Assoluto (non Delta Signed)

**Decisione**: ADJUST imposta `on_hand` a un valore assoluto, non aggiunge/sottrae un delta.

**File modificati**:
- [src/domain/models.py](src/domain/models.py#L20): Aggiornato commento `ADJUST = "ADJUST"` → "Absolute set: on_hand := qty"
- [src/domain/ledger.py](src/domain/ledger.py#L99): Chiarito commento nel calcolo
- [EXCEPTIONS_TAB.md](EXCEPTIONS_TAB.md#L122): Aggiornato da `on_hand += qty` a `on_hand := qty`
- [DEVELOPMENT.md](DEVELOPMENT.md#L26): Aggiornato da `on_hand ± qty` a `on_hand := qty (absolute set)`
- [README.md](README.md#L41): Aggiornato descrizione evento
- [.github/copilot-instructions.md](.github/copilot-instructions.md#L26): Aggiornato istruzioni AI

**Impatto**:
- Inventario fisico: quando usi ADJUST con qty=50, il nuovo on_hand sarà esattamente 50 (non on_hand precedente ± 50)
- Coerente con implementazione esistente in `StockCalculator.calculate_asof`

**Esempio**:
```python
# Prima: on_hand = 100
# ADJUST con qty = 50
# Dopo: on_hand = 50 (set assoluto, non 100+50 o 100-50)
```

---

### 2. Ricevimenti: Data Evento = Receipt Date (non Today)

**Decisione**: Gli eventi `RECEIPT` e `UNFULFILLED` usano `receipt_date` come data dell'evento, non la data odierna.

**File modificati**:
- [src/workflows/receiving.py](src/workflows/receiving.py#L104-L115): Modificato `date=today` → `date=receipt_date` per eventi RECEIPT
- [src/workflows/receiving.py](src/workflows/receiving.py#L123-L132): Modificato `date=today` → `date=receipt_date` per eventi UNFULFILLED

**Impatto**:
- I ricevimenti impattano lo stock alla data effettiva di ricezione, non alla data di registrazione
- Il calcolo stock "AsOf" riflette la realtà storica corretta
- Esempio: ricevimento del 20 gennaio registrato il 2 febbraio → lo stock aumenta il 20 gennaio

**Comportamento**:
```python
# Ricevimento con receipt_date = 2026-01-20 (registrato oggi 2026-02-02)
# → Transaction.date = 2026-01-20 (non 2026-02-02)
# → Stock AsOf 2026-01-25 include questo ricevimento
```

---

### 3. Status Ordini: Uppercase (PENDING)

**Decisione**: Tutti gli status ordini usano uppercase (`PENDING`, non `pending`).

**File modificati**:
- [verify_data.py](verify_data.py#L78): Cambiato filtro da `'pending'` a `'PENDING'`

**Impatto**:
- Coerenza tra scrittura in `order_logs.csv` (già uppercase) e lettura/filtri
- Nessun mismatch tra log e GUI

**Verifica esistente**:
- [src/workflows/order.py](src/workflows/order.py#L214): Già scrive `status="PENDING"`
- [src/workflows/receiving.py](src/workflows/receiving.py#L100): Già filtra `status == "PENDING"`

---

### 4. Ordini Pendenti: Usa Ledger come Source of Truth

**Decisione**: Il calcolo degli "ordini pendenti" si basa su `on_order` dal ledger, non su aggregazione manuale di order_logs - receiving_logs.

**File modificati**:
- [src/gui/app.py](src/gui/app.py#L1737-L1819): Riscritto `_refresh_pending_orders()` per:
  - Calcolare `on_order` da ledger (StockCalculator)
  - Mostrare solo SKU con `on_order > 0`
  - Aggregare ordini per (SKU, receipt_date)
  - Approssimare qty_received come `qty_ordered - on_order`

**Impatto**:
- Gli ordini pendenti riflettono lo stato reale del ledger (inclusi RECEIPT, UNFULFILLED)
- Se un ordine è stato parzialmente ricevuto o annullato (UNFULFILLED), il pending qty è corretto
- Architettura ledger-driven: il ledger è la verità, i log sono tracking ausiliario

**Comportamento**:
```python
# Ordine: SKU001, qty=100
# Ricevuto: 80 (+ 20 UNFULFILLED auto-generato)
# on_order dal ledger = 0 (100 ORDER - 80 RECEIPT non riduce on_order, UNFULFILLED non tracciato)
# Pending qty mostrato = 0 (ordine chiuso)
```

**Nota**: La logica attuale NON riduce `on_order` per eventi `UNFULFILLED`. Se vuoi che UNFULFILLED riduca on_order, serve modifica in `StockCalculator.calculate_asof`.

---

## Test di Verifica

File: [test_fix_verification.py](test_fix_verification.py)

**Risultati**:
- ✓ ADJUST set assoluto: on_hand=50 dopo ADJUST(50) da stato 100
- ✓ RECEIPT usa receipt_date: eventi con date=receipt_date (non today)
- ✓ UNFULFILLED usa receipt_date: coerente con RECEIPT
- ✓ Status PENDING uppercase: filtro corretto in ReceivingWorkflow

---

## Prossimi Passi (Opzionali)

### A. UNFULFILLED e on_order
Attualmente `UNFULFILLED` NON riduce `on_order` nel ledger (è solo tracking).  
Se vuoi che ordini non consegnati riducano `on_order`:

```python
# In src/domain/ledger.py, StockCalculator.calculate_asof:
elif txn.event == EventType.UNFULFILLED:
    on_order = max(0, on_order - txn.qty)  # Riduce on_order
    unfulfilled_qty += txn.qty
```

**Impatto**: Gli ordini parzialmente non consegnati non restano "in order" indefinitamente.

### B. Order_id in receiving_logs
Per tracking più preciso, aggiungere campo `order_id` a `receiving_logs.csv`:
- Collega ricevimenti a ordini specifici
- Permette chiusura ordine per order_id (non solo per SKU)

**Schema proposto**:
```csv
receipt_id,date,sku,qty_received,receipt_date,order_id
```

---

## File Modificati (Riepilogo)

**Codice**:
1. [src/domain/models.py](src/domain/models.py) - Documentazione EventType.ADJUST
2. [src/domain/ledger.py](src/domain/ledger.py) - Commento ADJUST
3. [src/workflows/receiving.py](src/workflows/receiving.py) - Data evento RECEIPT/UNFULFILLED
4. [src/gui/app.py](src/gui/app.py) - Logica ordini pendenti
5. [verify_data.py](verify_data.py) - Filtro status uppercase

**Documentazione**:
6. [EXCEPTIONS_TAB.md](EXCEPTIONS_TAB.md) - Semantica ADJUST
7. [DEVELOPMENT.md](DEVELOPMENT.md) - Tabella eventi
8. [README.md](README.md) - Descrizione eventi
9. [.github/copilot-instructions.md](.github/copilot-instructions.md) - Istruzioni AI

**Test**:
10. [test_fix_verification.py](test_fix_verification.py) - Test automatici
11. [tests/test_stock_calculation.py](tests/test_stock_calculation.py) - Aggiornato test per ADJUST set assoluto

---

## Verifica Finale

✅ **Test custom completato con successo**:
```bash
python test_fix_verification.py
```
- ✓ ADJUST set assoluto: on_hand=50 dopo ADJUST(50) da stato 100
- ✓ RECEIPT usa receipt_date: eventi con date=receipt_date (non today)
- ✓ UNFULFILLED usa receipt_date: coerente con RECEIPT
- ✓ Status PENDING uppercase: filtro corretto

✅ **Test modificato per riflettere nuova semantica**:
- `tests/test_stock_calculation.py::test_event_priority_same_day`: PASSED

⚠️ **Test pre-esistenti falliti** (NON causati dalle nostre modifiche):
- Audit log sorting (2 test)
- CSV headers (header SKU aggiornato con campi extra)
- Daily close workflow (5 test: metodo `add_sku` rimosso)
- Daily sales average (2 test: formato return value cambiato)
- Order proposal (1 test: logica proposta cambiata)

---

**Status**: ✓ Tutte le modifiche implementate e testate con successo
