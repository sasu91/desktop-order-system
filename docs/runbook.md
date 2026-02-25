# Runbook — Desktop Order System

Procedure operative per avvio, backup, manutenzione e troubleshooting.

---

## Indice

1. [Avvio applicazione](#1-avvio-applicazione)
2. [Inizializzazione database SQLite](#2-inizializzazione-database-sqlite)
3. [Backup database](#3-backup-database)
4. [Ripristino da backup](#4-ripristino-da-backup)
5. [Migrazione CSV → SQLite](#5-migrazione-csv--sqlite)
6. [Troubleshooting: database locked](#6-troubleshooting-database-locked)
7. [Troubleshooting: SQLite corrotto](#7-troubleshooting-sqlite-corrotto)
8. [Verifica integrità DB](#8-verifica-integrità-db)
9. [Ottimizzazione (VACUUM / REINDEX)](#9-ottimizzazione-vacuum--reindex)
10. [Avvio in modalità CSV pura](#10-avvio-in-modalità-csv-pura)
11. [Export dati per debug](#11-export-dati-per-debug)

---

## 1. Avvio applicazione

### Modalità sviluppo

```bash
cd /path/to/desktop-order-system
pip install -r requirements.txt       # solo al primo avvio / dopo aggiornamenti
python main.py
```

L'app crea automaticamente `data/` con tutti i file CSV e `data/app.db` se assenti.
Non è richiesta nessuna configurazione manuale.

### Eseguibile distribuito (Windows)

1. Aprire la cartella `dist/DesktopOrderSystem/`
2. Doppio clic su `DesktopOrderSystem.exe`

I dati vengono scritti in `<cartella exe>/data/`.  
Se quella cartella non è scrivibile (es. `Program Files`), il fallback è
`%APPDATA%\DesktopOrderSystem\data\`.

### Variabile d'ambiente utile

```powershell
# Forzare backend CSV (ignora settings.json)
$env:STORAGE_BACKEND = "csv"
python main.py
```

---

## 2. Inizializzazione database SQLite

Da eseguire **una volta sola** su una nuova installazione o dopo aver cancellato `data/app.db`.

```bash
python src/db.py init
```

Output atteso:

```
✓ Database initialized at data/app.db
  Schema version: 3
  Tables: 12
```

Per verificare che le migrazioni siano aggiornate:

```bash
python src/db.py migrate --dry-run   # preview senza modifiche
python src/db.py migrate             # applica le migrazioni pending
```

---

## 3. Backup database

### Backup manuale (CLI)

```bash
python src/db.py backup manual
```

Il file viene salvato in `data/backups/manual/app_<timestamp>.db`.

### Backup automatico (avvio app)

Ad ogni avvio dell'applicazione viene creato automaticamente un backup in
`data/backups/startup/` (massimo 7 backup conservati per rotazione).

### Backup pre-migrazione

Prima di ogni `migrate` viene creato automaticamente un backup in
`data/backups/pre_migration/` (massimo 10 backup).

### Script tools

```bash
# Verifica spazio backup e lista candidati di ripristino
python tools/db_check.py --quick
```

---

## 4. Ripristino da backup

### Con script dedicato

```bash
python tools/restore_backup.py
```

Lo script elenca i backup disponibili, mostra data/dimensione e chiede conferma
prima di sovrascrivere `data/app.db`.

### Manuale

```bash
# 1. Fermare l'applicazione
# 2. Sostituire il database
cp data/backups/startup/app_20260224_120000.db data/app.db

# 3. Verificare integrità del file ripristinato
python src/db.py verify
```

### Da GUI

Nelle impostazioni dell'app (tab **⚙ Impostazioni → Database**) è disponibile il
pulsante **"Ripristina da backup"** che guida il processo.

---

## 5. Migrazione CSV → SQLite

Converte i dati esistenti da file CSV al database SQLite.  
**Operazione idempotente**: può essere rieseguita senza duplicare dati.

```bash
# Dry-run (anteprima, nessuna modifica)
python src/migrate_csv_to_sqlite.py --dry-run

# Migrazione completa
python src/migrate_csv_to_sqlite.py

# Solo tabelle specifiche
python src/migrate_csv_to_sqlite.py --tables=skus,transactions
```

Oppure dalla GUI: tab **⚙ Impostazioni → Database → "Avvia Migrazione Wizard"**.

**Prima della migrazione**:

- Assicurarsi che i CSV non siano aperti in altri programmi (es. Excel)
- Verificare spazio disco disponibile (≥ 2× dimensione totale CSV)
- Un backup dei CSV viene creato automaticamente in `data/csv_backups/`

---

## 6. Troubleshooting: database locked

**Sintomo**: errore `database is locked` all'avvio o durante operazioni.

### Causa più comune

Un'altra istanza dell'applicazione è in esecuzione, o una connessione precedente
non è stata chiusa correttamente.

### Procedura

```bash
# 1. Verificare processi attivi
# Windows:
tasklist | findstr python

# Linux/WSL:
ps aux | grep python

# 2. Terminare eventuali istanze
# Windows: Task Manager → fine attività
# Linux: kill <PID>

# 3. Verificare il journal WAL (potrebbe essere residuo)
ls data/app.db-wal data/app.db-shm
# Se presenti, sono normali — SQLite li gestisce automaticamente.
# Rimuoverli solo se il DB è inaccessibile E si ha un backup recente.

# 4. Forzare checkpoint WAL e rilascio lock
python -c "
import sqlite3
conn = sqlite3.connect('data/app.db', timeout=1)
conn.execute('PRAGMA wal_checkpoint(FULL)')
conn.close()
print('OK')
"

# 5. Riavviare l'applicazione
python main.py
```

### Impostazione timeout

In `src/db.py`, `PRAGMA_CONFIG["busy_timeout"]` è già impostato a `5000 ms`.
Se l'ambiente ha molti accessi concorrenti, aumentarlo in `config.py`.

---

## 7. Troubleshooting: SQLite corrotto

**Sintomo**: errori `malformed database`, `file is not a database`, `disk I/O error`.

### Procedura di recovery guidata

```bash
python src/db.py recover
```

Lo script esegue in sequenza:
1. Safety backup del file corrotto (se leggibile)
2. `PRAGMA integrity_check` per valutare il danno
3. Elenco dei backup disponibili per il ripristino
4. Istruzioni per il passo successivo

### Recovery manuale

```bash
# 1. Cercare il backup più recente
ls -lt data/backups/startup/

# 2. Copiare il backup
cp data/backups/startup/app_YYYYMMDD_HHMMSS.db data/app.db

# 3. Verificare
python src/db.py verify

# 4. Se nessun backup è disponibile, ricostruire da CSV
python src/db.py init --force           # ricrea schema vuoto
python src/migrate_csv_to_sqlite.py     # reimporta da CSV
```

### Prevenzione

- Attivare backup automatici (default: ON all'avvio)
- Non collocare `data/app.db` su cartelle di rete o cloud-sync attivo (OneDrive, Dropbox)
- Evitare di terminare l'app con Task Manager / `kill -9` durante scritture

---

## 8. Verifica integrità DB

```bash
# Check rapido (struttura + schema)
python tools/db_check.py --quick

# Check completo (struttura + FK + invarianti + WAL)
python tools/db_check.py

# Con dettagli verbosi
python tools/db_check.py --verbose
```

Codici di uscita:

| Codice | Significato |
|--------|-------------|
| `0` | Tutti i controlli PASS |
| `1` | Uno o più controlli FAIL |
| `2` | Uno o più WARNING (nessun FAIL) |

Verifica rapida via CLI SQLite:

```bash
sqlite3 data/app.db "PRAGMA integrity_check;"
sqlite3 data/app.db "PRAGMA foreign_key_check;"
```

---

## 9. Ottimizzazione (VACUUM / REINDEX)

Da eseguire dopo eliminazioni massive di righe o nel caso di prestazioni degradate.

```bash
python tools/db_reindex_vacuum.py
```

Operazioni eseguite:
- `PRAGMA wal_checkpoint(FULL)` — svuota il WAL
- `REINDEX` — ricostruisce tutti gli indici
- `VACUUM` — compatta il file DB

**Attenzione**: `VACUUM` crea una copia temporanea del DB — richiedere ~2× spazio
libero su disco prima di eseguirlo.

Timing consigliato: settimanale, in orario di bassa attività.

---

## 10. Avvio in modalità CSV pura

Utile per debug o ambienti senza SQLite funzionante.

### Via settings.json

```json
// data/settings.json
{
  "storage_backend": "csv"
}
```

### Via GUI

Tab **⚙ Impostazioni → Database → Backend storage** → selezionare `CSV`.

### Via codice (test / script)

```python
from src.persistence.storage_adapter import StorageAdapter

adapter = StorageAdapter(force_backend="csv")
```

L'applicazione funziona in modalità CSV completa; le funzionalità SQL-only
(es. concorrenza WAL, integrità FK) non sono disponibili.

---

## 11. Export dati per debug

### Bundle completo (log + DB + CSV)

```bash
python tools/export_debug_bundle.py
```

Crea un archivio `debug_bundle_<timestamp>.zip` contenente:
- `data/app.db` (copia)
- `data/*.csv`
- `logs/`
- output di `db_check --verbose`
- `data/settings.json`

### Snapshot stock (CSV)

```bash
python tools/export_snapshot.py
```

Esporta lo stato stock corrente (as-of oggi) in `data/stock_snapshot_<timestamp>.csv`.

### Profilo performance DB

```bash
python tools/profile_db.py
```

Mostra tempi di query per le operazioni principali — utile per diagnosticare
rallentamenti su dataset grandi.

---

## Riferimenti

| Documento | Contenuto |
|-----------|-----------|
| [README.md](../README.md) | Panoramica, prerequisiti, quick start |
| [HOLIDAY_SYSTEM.md](../HOLIDAY_SYSTEM.md) | Gestione festività e chiusure |
| [FASE_7_COMPLETE.md](../FASE_7_COMPLETE.md) | Dettagli architettura SQLite |
| `src/db.py` | Sorgente connection manager, `python src/db.py --help` |
| `tools/db_check.py` | Sorgente integrity checker |
