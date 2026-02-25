# Configuration Reference

All runtime parameters for **desktop-order-system** are controlled via environment
variables prefixed `DOS_`.  Both the desktop client and the (forthcoming) FastAPI
backend share the same prefix so a single `.env` file can cover the full stack.

---

## Loading order

1. Hard-coded defaults in `config.py` / `src/utils/paths.py`
2. `data/settings.json` (GUI-persisted overrides, desktop only)
3. **Environment variables** — highest priority, always win

To load a `.env` file automatically install `python-dotenv` and call
`load_dotenv()` at the very top of your entry-point (`main.py` / `backend/app/main.py`):

```python
from dotenv import load_dotenv
load_dotenv()          # reads .env in cwd; silent if absent
```

---

## Variables — Desktop client

| Variable | Default | Description |
|---|---|---|
| `DOS_DATA_DIR` | `<project_root>/data` → `%APPDATA%/DesktopOrderSystem/data` | Absolute path to the data directory (DB, CSV, settings). Overrides both the portable path and the `%APPDATA%` fallback. |
| `DOS_DB_PATH` | `<DOS_DATA_DIR>/app.db` | Absolute path to the SQLite database file. |
| `DOS_STORAGE_BACKEND` | `sqlite` | Storage engine: `sqlite` or `csv`. Overrides `data/settings.json`. |
| `DOS_LOG_DIR` | `<project_root>/logs` → `%APPDATA%/DesktopOrderSystem/logs` | Directory where rotating log files are written. |
| `DOS_LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

---

## Variables — Backend API (FastAPI)

| Variable | Default | Description |
|---|---|---|
| `DOS_DB_PATH` | *(required)* | Absolute path to the SQLite file the API reads. Should point to the same file as the desktop client when running on the same host. |
| `DOS_API_HOST` | `127.0.0.1` | Interface the Uvicorn server binds to. Use `0.0.0.0` to expose on all interfaces. |
| `DOS_API_PORT` | `8000` | TCP port the API listens on. |
| `DOS_API_TOKEN` | *(required in production)* | Static bearer token for API authentication. Set a long random string (≥ 32 chars). |
| `DOS_SECRET_KEY` | *(required in production)* | Secret used for JWT signing or HMAC. Generate with `openssl rand -hex 32`. |
| `DOS_CORS_ORIGINS` | `http://localhost` | Comma-separated list of allowed CORS origins, e.g. `http://localhost,https://myapp.example.com`. |
| `DOS_LOG_LEVEL` | `INFO` | Same semantics as the desktop variable. |
| `DOS_LOG_DIR` | `backend/logs` | Log directory for the API process. |

---

## Sensitive variables

**Never commit** `DOS_API_TOKEN`, `DOS_SECRET_KEY`, or any credential to version control.
Add `.env` to `.gitignore` — only `.env.example` (with placeholder values) is committed.

```gitignore
.env
.env.local
backend/.env
backend/.env.local
```

---

## Integration with `config.py`

`config.py` should be updated to read env vars before falling back to defaults.
Pattern to adopt in `config.py`:

```python
import os
from pathlib import Path

# DB path: env var wins, then computed default
_db_env = os.environ.get("DOS_DB_PATH")
DATABASE_PATH = Path(_db_env) if _db_env else DATA_DIR / "app.db"

# Storage backend: env var wins, then settings.json, then module default
_backend_env = os.environ.get("DOS_STORAGE_BACKEND", "").lower()
if _backend_env in ("csv", "sqlite"):
    STORAGE_BACKEND = _backend_env   # type: ignore[assignment]

# Log level
LOG_LEVEL = os.environ.get("DOS_LOG_LEVEL", "INFO").upper()
```

---

## See also

- [runbook.md](runbook.md) — startup, backup, restore, troubleshooting
- `config.py` — source of truth for defaults and settings.json parsing
- `src/utils/paths.py` — frozen-aware path resolution
