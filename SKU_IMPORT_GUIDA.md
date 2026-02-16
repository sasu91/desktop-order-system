# Import SKU da CSV - Guida d'Uso Rapida

## Come Usare

1. **Aprire il menu**: `File → Importa SKU da CSV...`

2. **Selezionare il file CSV**: Scegliere il CSV contenente i dati SKU da importare. Il file può usare qualsiasi delimitatore (virgola, punto e virgola, ecc.) e qualsiasi encoding (UTF-8, latin-1).

3. **Anteprima e validazione**: Verrà mostrata una finestra con:
   - **Conteggio totale**: righe totali, valide, scartate
   - **Mapping colonne**: mapping automatico delle colonne riconosciute (sku, description, moq, ecc.)
   - **Tabella preview**: prime 50 righe con stato (✅ OK / ❌ ERRORE) e dettagli errori
   - **Motivo principale scarti**: se presenti righe scartate

4. **Scegliere modalità import**:
   - **UPSERT**: Aggiorna SKU esistenti + Aggiunge nuovi SKU (modalità consigliata)
   - **REPLACE**: Sostituisce completamente il file SKU con quelli importati (⚠️ rimuove SKU non presenti nel CSV)

5. **Confermare import**: 
   - Se ci sono scarti in modalità REPLACE, verrà richiesta conferma esplicita aggiuntiva
   - Il sistema crea backup automatico di `skus.csv` prima della modifica

6. **Risultato**: Messaggio di successo con conteggio importati/scartati. Se ci sono scarti, viene creato un file `import_sku_errors_*.csv` in `data/` con dettagli.

## Formato CSV Supportato

### Colonne Riconosciute (auto-mapping con alias)
- `sku` (o `code`, `item_code`, `product_code`)
- `description` (o `desc`, `name`, `product_name`)
- `ean` (o `barcode`, `gtin`, `upc`)
- `moq`, `pack_size`, `lead_time_days`, `shelf_life_days`, ecc.

**Campi critici obbligatori**: `sku` e `description` (le righe senza questi campi vengono scartate).

### Esempio CSV Minimo
```csv
sku,description,moq,lead_time_days
SKU001,Prodotto 1,10,14
SKU002,Prodotto 2,5,7
```

### Colonne Extra
Colonne non riconosciute vengono ignorate senza errore.

### Valori Mancanti
Per campi non critici vengono applicati i default del modello SKU (es. `moq=1`, `lead_time_days=7`).

## Sicurezza

- **Backup automatico**: Prima di ogni import viene creato backup timestampato (`skus.csv.backup.YYYYMMDD_HHMMSS`)
- **Scrittura atomica**: In modalità REPLACE, il file viene scritto in modo atomico (temp → rename) per evitare corruzione dati
- **Audit logging**: Ogni import viene registrato in `audit_log.csv` con modalità, file sorgente, conteggio righe e dettagli errori
- **Dettaglio scarti**: Se ci sono righe scartate, viene salvato `import_sku_errors_*.csv` con riga, SKU, errori e avvisi

## Troubleshooting

- **"Nessuna riga valida da importare"**: Verificare che il CSV abbia almeno le colonne `sku` e `description` con valori non vuoti
- **Encoding issues**: Il parser tenta automaticamente UTF-8 e poi latin-1; se usa caratteri speciali, convertire il CSV in UTF-8
- **Duplicati nel file**: Se lo stesso SKU appare più volte nel CSV, viene importata solo la prima occorrenza
- **REPLACE bloccato**: In modalità REPLACE con scarti, confermare esplicitamente che si vuole procedere (⚠️ attenzione a perdita dati)

## File Modificati/Creati

- **Modulo principale**: `src/workflows/sku_import.py` (parser, validazione, preview, UPSERT/REPLACE)
- **Integrazione GUI**: `src/gui/app.py` - menu `File → Importa SKU da CSV...` (linea ~144) e wizard preview (funzione `_import_sku_from_csv()` e `_show_import_preview_wizard()`)
- **Persistence**: `src/persistence/csv_layer.py` - helper `update_sku_object()`, `log_import_audit()`
- **Test**: `tests/test_sku_import.py` (32 test: parsing, validazione, UPSERT, REPLACE, backup, audit)

---
**Versione**: 1.0 - Febbraio 2026
