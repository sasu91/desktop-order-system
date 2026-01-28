# Desktop Order System - Verifica Rapida

## Avviamento Veloce

### Prerequisiti
```bash
python --version  # Deve essere Python 3.12+
pip install -r requirements.txt
```

### Esecuzione Test
```bash
# Esegui tutti i test
python -m pytest tests/ -v

# Esegui test singolo
python -m pytest tests/test_stock_calculation.py -v

# Con copertura
python -m pytest tests/ --cov=src
```

### Avviamento GUI
```bash
python main.py
```

L'app creerà automaticamente i file CSV nella cartella `data/` al primo avvio.

## Test Critico: Logica Stock CalcolatA

I test principali verificano:
1. ✅ Calcolo stock AsOf date (events con date < AsOf_date)
2. ✅ Priorità eventi stessa data (SNAPSHOT → ORDER/RECEIPT → SALE/WASTE)
3. ✅ Integrazione vendite da sales.csv
4. ✅ Idempotenza ricevimenti (stessa receipt_id due volte = no duplicazione)
5. ✅ Eccezioni revertibili (WASTE, ADJUST, UNFULFILLED)

## Struttura Verificata

```
src/domain/
  ├── models.py (SKU, Transaction, Stock, EventType)
  ├── ledger.py (StockCalculator, validate_ean)
  └── migration.py (LegacyMigration)

src/persistence/
  └── csv_layer.py (Auto-create con headers)

src/workflows/
  ├── order.py (Proposal, Confirmation, generate_receipt_id)
  └── receiving.py (Receiving closure idempotente, Exception handling)

src/gui/
  └── app.py (Tkinter UI: Stock tab, Order tab, Exception tab, Admin tab)

tests/
  ├── test_stock_calculation.py (22 test cases)
  ├── test_workflows.py (Workflow tests)
  ├── test_persistence.py (CSV layer tests)
  └── test_migration.py (Legacy migration tests)
```

## File CSV Creati Automaticamente

| File | Scopo | Creato da |
|------|-------|----------|
| data/skus.csv | Master SKU | CSVLayer.__init__ |
| data/transactions.csv | **Ledger (fonte di verità)** | CSVLayer.__init__ |
| data/sales.csv | Vendite giornaliere | CSVLayer.__init__ |
| data/order_logs.csv | Log ordini | OrderWorkflow |
| data/receiving_logs.csv | Log ricevimenti | ReceivingWorkflow |

## Validazioni Implementate

✅ **EAN**: Formato 12-13 digits; None/vuoto permesso; invalid → messaggio, no crash  
✅ **Date**: YYYY-MM-DD; no future dates in domain logic  
✅ **SKU**: Cannot be empty; unique per transaction  
✅ **Events**: SNAPSHOT/ORDER/RECEIPT/SALE/WASTE/ADJUST/UNFULFILLED  
✅ **Ledger**: Fonte di verità; stock calcolato, non memorizzato  

## Design Verificato

✅ **Determinismo**: Stessi input → sempre stesso output (idempotenza)  
✅ **Isolamento**: Domain logic puro (no I/O); testabile senza file  
✅ **Layers**: Dominio → Persistenza → Workflows → GUI  
✅ **Graceful**: Errori EAN/CSV → warning, continua senza crash  
✅ **Idempotenza**: Ricevimento e Eccezioni sono riscrivibili senza duplicazione  

## Prossimi Step (se necessario)

1. Aggiungi tab "Ordini" con interfaccia proposta + conferma
2. Implementa barcode/EAN rendering in finestra post-conferma
3. Aggiungi tab "Ricevimenti" con UI chiusura + log
4. Aggiungi tab "Eccezioni" con WASTE/ADJUST/UNFULFILLED
5. Migrazione legacy da snapshot CSV (command-line o UI)
