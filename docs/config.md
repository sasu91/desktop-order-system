# Configuration Reference

All runtime parameters for **desktop-order-system** are controlled via environment
variables prefixed `DOS_`.  Both the desktop client and the FastAPI backend share
the same prefix so a single `.env` file can cover the full stack.

---

## Loading order

1. Hard-coded defaults in `dos_backend/config.py` / `utils/paths.py`
2. `data/settings.json` (`storage_backend` only, persisted by the GUI Settings tab)
3. **Environment variables** — highest priority, always win

To load a `.env` file automatically install `python-dotenv` and call `load_dotenv()`
at the top of your entry-point:

```python
from dotenv import load_dotenv
load_dotenv()   # reads .env in cwd; silent if absent
```

---

## Variables — Storage & paths

| Variable | Default | Description |
|---|---|---|
| `DOS_DATA_DIR` | `<project_root>/data` → `%APPDATA%/DesktopOrderSystem/data` | Absolute path to the data directory (DB, CSV, settings). Overrides the portable path and the `%APPDATA%` fallback. |
| `DOS_DB_PATH` | `<DOS_DATA_DIR>/app.db` | Absolute path to the SQLite database file. Shared between desktop client and API when both run on the same host. |
| `DOS_STORAGE_BACKEND` | `sqlite` | Storage engine: `sqlite` or `csv`. Overrides `data/settings.json`. |

---

## Variables — API server

| Variable | Default | Description |
|---|---|---|
| `DOS_API_HOST` | `127.0.0.1` | Interface Uvicorn binds to. Use `0.0.0.0` to expose on all interfaces (e.g. Docker). |
| `DOS_API_PORT` | `8000` | TCP port Uvicorn listens on. Must be 1–65535; invalid values fall back to `8000`. |
| `DOS_API_TOKEN` | *(empty = dev mode)* | Static Bearer token for all authenticated endpoints. When unset/blank the server runs in **dev mode** (see below). Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `DOS_CORS_ORIGINS` | *(empty = disabled)* | Comma-separated list of allowed CORS origins, e.g. `http://localhost:3000,https://app.example.com`. When empty the CORS middleware is not added. |

---

## Variables — Logging

| Variable | Default | Description |
|---|---|---|
| `DOS_LOG_LEVEL` | `INFO` | Verbosity for the `dos_backend` logger: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `DOS_LOG_DIR` | `<project_root>/logs` | Directory where rotating log files are written (desktop client). The API server logs to stdout only. |

---

## Dev mode — authentication bypass

When `DOS_API_TOKEN` is **not set** (or blank) the backend starts in **dev mode**:

- All endpoints decorated with `Depends(verify_token)` or `Depends(optional_token)`
  are accessible **without any `Authorization` header**.
- A one-time `WARNING` is printed to the server log at the first authenticated request:

  ```
  ⚠️  AUTH DEV MODE ACTIVE — DOS_API_TOKEN is not set.
       All authenticated endpoints are accessible without a token.
       Set DOS_API_TOKEN before deploying to production.
  ```

- The dependency returns the sentinel string `"__dev__"` instead of a real token;
  route handlers can check `token == "__dev__"` if they need to detect dev mode.

> **NEVER run in dev mode in production.**  Set a long random `DOS_API_TOKEN`.

---

## Sensitive variables

**Never commit** `DOS_API_TOKEN` or any credential to version control.
Only `.env.example` (with placeholder values) is committed.

```gitignore
.env
.env.local
backend/.env
backend/.env.local
```

---

## Implementation reference — `dos_backend/config.py`

| Python name | Getter | Env var read |
|---|---|---|
| `DATA_DIR` | `_resolve_data_dir()` | `DOS_DATA_DIR` |
| `DATABASE_PATH` | `_resolve_db_path()` | `DOS_DB_PATH` |
| `SETTINGS_FILE` | — | — |
| `API_HOST` | `get_api_host()` | `DOS_API_HOST` |
| `API_PORT` | `get_api_port()` | `DOS_API_PORT` |
| `API_TOKEN` | `get_api_token()` | `DOS_API_TOKEN` |
| `CORS_ORIGINS` | `get_cors_origins()` | `DOS_CORS_ORIGINS` |
| `_STORAGE_BACKEND` | `get_storage_backend()` | `DOS_STORAGE_BACKEND` |

Module-level names (`API_HOST`, `API_PORT`, …) are resolved **once at import time**.
Use the getter functions when you need env-var changes to take effect without
reloading the module (e.g. in tests that `monkeypatch.setenv`).

---

## See also

- [runbook.md](runbook.md) — startup, backup, restore, troubleshooting
- [api_contract.md](api_contract.md) — REST endpoint contract
- `backend/dos_backend/config.py` — source of truth for all defaults
- `backend/dos_backend/api/auth.py` — dev-mode implementation
