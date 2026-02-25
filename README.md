# Desktop Order System

Stock reordering management system — ledger-driven, multi-client architecture.

> **Status**: desktop client operational · backend API (planned) · Android client (planned)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        backend/                             │
│                                                             │
│   REST API  (Python · FastAPI — planned)                    │
│   ┌─────────────┐   ┌──────────────┐   ┌────────────────┐  │
│   │  domain/    │   │ persistence/ │   │  workflows/    │  │
│   │  models     │   │  SQLite      │   │  order         │  │
│   │  ledger     │   │  CSV fallback│   │  receiving     │  │
│   │  calendar   │   │              │   │  replenishment │  │
│   └─────────────┘   └──────────────┘   └────────────────┘  │
└────────────────────────────┬────────────────────────────────┘
                             │  HTTP/JSON  (future)
           ┌─────────────────┼─────────────────┐
           │                 │                 │
    ┌──────▼──────┐         N/A         ┌──────▼──────┐
    │  desktop/   │                     │  android/   │
    │             │                     │             │
    │  Python 3.12│                     │  Kotlin +   │
    │  Tkinter    │                     │  Jetpack    │
    │  Windows    │                     │  Compose    │
    │  (current)  │                     │  (planned)  │
    └─────────────┘                     └─────────────┘
```

**Ledger-driven design** — stock state is never stored; it is always *calculated*
from a transaction ledger (`transactions.csv` / SQLite) as-of a given date.

### Layer responsibilities

| Layer | Path | Responsibility |
|-------|------|----------------|
| Domain | `src/domain/` | Pure business logic — no I/O, fully testable |
| Persistence | `src/persistence/` | CSV auto-create + SQLite adapter (transparent routing) |
| Workflows | `src/workflows/` | Order, receiving, replenishment, daily close |
| GUI | `src/gui/` | Tkinter desktop UI (tabs: Stock, Orders, Receiving, Exceptions, …) |
| Analytics | `src/analytics/` | KPI, scoring, service level, closed-loop |
| Backend API | `backend/` | FastAPI REST — **planned** |
| Android | `android/` | Kotlin mobile client — **planned** |

---

## Prerequisites

### Desktop client (current)

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12 | 3.11 may work but untested |
| OS | Windows 10/11 | Tkinter is the UI toolkit |
| pip packages | see `requirements.txt` | install once |

Optional visual features (graceful degradation if absent):

- `matplotlib` — dashboard charts
- `pillow` + `python-barcode` — barcode rendering in receipt view
- `tkcalendar` — date-picker widget

### Backend API (planned)

| Requirement | Version |
|-------------|---------|
| Python | 3.12 |
| FastAPI | ≥ 0.110 |
| SQLite | 3.x (stdlib) |

### Android client (planned)

| Requirement | Version |
|-------------|---------|
| Android Studio | Hedgehog 2023.1+ |
| Kotlin | 1.9+ |
| Min SDK | API 26 (Android 8.0) |
| Jetpack Compose | 1.6+ |

---

## Quick start

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run desktop client

```bash
python main.py
```

All required data files (`data/*.csv`, `data/app.db`) are created automatically on
first run. No manual setup needed.

### Initialize / migrate database

```bash
# First-time SQLite init
python src/db.py init

# Apply pending schema migrations
python src/db.py migrate

# Verify integrity
python src/db.py verify
```

### Start backend API

```bash
# Installa il package (una tantum)
pip install -e backend[api]

# Configura il percorso al database
export DOS_DB_PATH=/path/to/app.db   # Linux/macOS
# $env:DOS_DB_PATH = "C:\path\to\app.db"  # Windows PowerShell

# Avvio via script helper (legge backend/.env automaticamente)
bash tools/run_backend.sh
.\tools\run_backend.ps1     # Windows

# Oppure direttamente
python -m uvicorn dos_backend.api.main:app --reload --host 127.0.0.1 --port 8000
```

API docs: <http://127.0.0.1:8000/api/docs>

### Build Android APK  _(placeholder — not yet implemented)_

```bash
# cd android/
# ./gradlew assembleDebug
```

### Run tests

```bash
# Full test suite
python -m pytest tests/

# With coverage
python -m pytest tests/ --cov=src

# Specific module
python -m pytest tests/test_stock_calculation.py -v
```

### Build Windows executable

```powershell
# Onedir (recommended)
pyinstaller DesktopOrderSystem.spec --clean --noconfirm

# Single-file variant
pyinstaller DesktopOrderSystem-onefile.spec --clean --noconfirm
```

---

## Project structure

```
desktop-order-system/
├── backend/                  # future REST API (FastAPI)
├── desktop/                  # future: desktop client moved here
├── android/                  # future Android client (Kotlin)
├── docs/                     # runbooks, ADRs, operational guides
│   └── runbook.md
├── src/                      # current desktop application source
│   ├── domain/               # models, ledger, calendar, holidays, …
│   ├── persistence/          # csv_layer.py, storage_adapter.py
│   ├── workflows/            # order, receiving, replenishment, …
│   ├── gui/                  # Tkinter app, widgets, migration wizard
│   ├── analytics/            # KPI, scoring, service level
│   ├── utils/                # paths, logging, error formatting
│   ├── db.py                 # SQLite connection + migration runner
│   ├── repositories.py       # DAL — SQLite repositories
│   ├── migrate_csv_to_sqlite.py
│   ├── forecast.py
│   ├── replenishment_policy.py
│   └── uncertainty.py
├── tests/                    # pytest suite (mirrors src/)
├── tools/                    # CLI utilities (db_check, export, …)
├── migrations/               # SQL migration files (001_initial…)
├── data/                     # runtime data — auto-created, git-ignored
├── main.py                   # application entry point
├── config.py                 # storage backend, paths, constants
├── requirements.txt
├── pytest.ini
├── DesktopOrderSystem.spec   # PyInstaller — onedir
└── DesktopOrderSystem-onefile.spec
```

---

## Key concepts

### Stock calculation (AsOf logic)

Stock is **never stored** — it is recalculated on demand:

```python
from src.domain.ledger import StockCalculator
from datetime import date

stock = StockCalculator.calculate_asof(
    sku="SKU001",
    asof_date=date.today(),
    transactions=[...],
    sales_records=[...]
)
# Returns: Stock(sku="SKU001", on_hand=100, on_order=50)
```

### Ledger event types

| Event | on_hand | on_order |
|-------|---------|----------|
| `SNAPSHOT` | := qty | — |
| `ORDER` | — | += qty |
| `RECEIPT` | += qty | -= qty |
| `SALE` | -= qty | — |
| `WASTE` | -= qty | — |
| `ADJUST` | := qty | — |
| `UNFULFILLED` | — | — (tracking only) |

### Storage backend

Configurable in `data/settings.json` (`storage_backend: "sqlite"` or `"csv"`).
`StorageAdapter` routes transparently — callers never need to know which backend
is active. SQLite is the default; CSV is the fallback.

### Holiday system

Effect-aware closures in `data/holidays.json` — `no_order`, `no_receipt`, or `both`.
`next_receipt_date()` skips affected days automatically.
Full docs: [HOLIDAY_SYSTEM.md](HOLIDAY_SYSTEM.md)

---

## Design decisions

1. **Ledger as single source of truth** — stock state is calculated, not stored
2. **Idempotent operations** — receiving, exceptions use deterministic idempotency keys
3. **No `datetime.now()` in domain logic** — date always passed as parameter
4. **Transparent storage routing** — `StorageAdapter` wraps both CSV and SQLite backends
5. **Frozen-aware paths** — `src/utils/paths.py` works both in dev and PyInstaller `.exe`

---

## Operations

See [docs/runbook.md](docs/runbook.md) for startup procedures, DB backup, and
troubleshooting.