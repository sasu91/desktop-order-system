# Backend — desktop-order-system API

FastAPI REST backend che espone il database SQLite del desktop client via HTTP.  
Pensato per essere consumato dal client Android e da futuri client web.

> **Package installabile**: `pip install -e backend[api]` — tutti gli endpoint sono implementati.  
> Vedi [docs/api_contract.md](../docs/api_contract.md) per la specifica completa degli endpoint.

---

## Struttura

```
backend/
├── dos_backend/
│   ├── __init__.py
│   ├── main.py           # FastAPI app, include_router
│   ├── dependencies.py   # get_db(), verify_token()
│   ├── schemas.py        # Pydantic request/response models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── health.py     # GET /health
│   │   ├── skus.py       # GET /skus/lookup-ean/{ean}
│   │   ├── stock.py      # GET /stock, GET /stock/{sku}
│   │   ├── exceptions.py # POST /exceptions
│   │   └── receipts.py   # POST /receipts/close
│   ├── domain/           # modelli, ledger, calendar, forecast…
│   ├── persistence/      # storage_adapter, csv_layer
│   ├── workflows/        # order, receiving, exceptions, daily_close
│   └── utils/            # paths, error_formatting
├── pyproject.toml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Prerequisiti

- Python 3.12+
- Il database SQLite del desktop client già inizializzato (`python src/db.py init`)

---

## Setup

```bash
# 1. Crea e attiva un virtualenv
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\Activate.ps1     # Windows PowerShell

# 2. Installa il package in modalità editabile (include tutte le dipendenze API)
pip install -e backend[api]

# 3. Configura le variabili d'ambiente
cp backend/.env.example backend/.env
# Modifica backend/.env: imposta DOS_DB_PATH, DOS_API_TOKEN, DOS_SECRET_KEY
```

> `backend/requirements.txt` è mantenuto per compatibilità con ambienti
> che non usano `pip install -e`. Dipende da `pyproject.toml` — non modificarlo
> manualmente.

---

## Avvio

```bash
# Via script helper (legge automaticamente backend/.env, gestisce venv)
bash tools/run_backend.sh
.\tools\run_backend.ps1          # Windows

# Oppure direttamente con uvicorn
uvicorn dos_backend.api.main:app --reload --host 127.0.0.1 --port 8000

# Oppure come modulo Python (usa DOS_API_HOST / DOS_API_PORT / DOS_RELOAD)
python -m dos_backend.api.main
```

Documentazione interattiva disponibile su:  
- Swagger UI: <http://127.0.0.1:8000/api/docs>  
- ReDoc:       <http://127.0.0.1:8000/api/redoc>

---

## Variabili d'ambiente

| Variabile | Obbligatoria | Note |
|---|---|---|
| `DOS_DB_PATH` | ✓ | Percorso assoluto al file `app.db` |
| `DOS_API_TOKEN` | ✓ (produzione) | Bearer token per autenticazione |
| `DOS_SECRET_KEY` | ✓ (produzione) | Chiave per JWT/HMAC |
| `DOS_API_HOST` | — | Default `127.0.0.1` |
| `DOS_API_PORT` | — | Default `8000` |
| `DOS_LOG_LEVEL` | — | Default `INFO` |

Riferimento completo: [docs/config.md](../docs/config.md)

---

## Vedere anche

- [docs/api_contract.md](../docs/api_contract.md) — specifica endpoint, esempi JSON, idempotenza
- [docs/config.md](../docs/config.md) — tutte le variabili d'ambiente
- [docs/runbook.md](../docs/runbook.md) — startup, backup, troubleshooting
