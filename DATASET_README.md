# Dataset Demo - Desktop Order System

Dataset per test con 2 SKU e 1 mese di storico completo (2026-01-01 to 2026-01-31).
Include vendite giornaliere, ordini, ricevimenti, eccezioni, lotti e audit log.

## Caratteristiche

- Periodo storico: 2026-01-01 to 2026-01-31 (31 giorni)
- SKU totali: 2
- Lead time: 1 giorno
- Ordini e ricevimenti: presenti con status RECEIVED
- Eccezioni: WASTE, ADJUST, UNFULFILLED

## SKU inclusi

### 1) SKU_FRESH (HIGH)
- Descrizione: Fresh Dairy 1L
- Domanda: variabile
- Shelf life: 14 giorni
- EAN: 4006381333931

### 2) SKU_PANTRY (STABLE)
- Descrizione: Pantry Pasta 500g
- Domanda: stabile (8 unita/giorno)
- Shelf life: 180 giorni
- EAN: 5901234123457

## File dataset

- skus.csv: 2 SKU con parametri completi
- sales.csv: 62 righe (31 giorni x 2 SKU)
- transactions.csv: snapshot, order, receipt, waste, adjust, unfulfilled
- order_logs.csv: ordini con qty_received e status
- receiving_logs.csv: ricevimenti con document_id e order_ids
- lots.csv: lotti con date di scadenza
- audit_log.csv: eventi principali

## Note

- Le vendite sono giornaliere per entrambe le SKU.
- Le date dei ricevimenti rispettano lead time 1 giorno.
- I lotti hanno expiry_date coerenti con la shelf life.
