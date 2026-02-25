"""
dos_backend/config.py — Centralised configuration for the dos_backend package.

Path resolution priority (highest → lowest):
  1. Environment variable  (see table below)
  2. settings.json         (storage_backend only)
  3. utils/paths.py        (frozen-aware defaults; same logic as project-root config.py)

Environment variables recognised
---------------------------------
# Storage / paths
DOS_DATA_DIR          Absolute path that overrides the default data directory.
DOS_DB_PATH           Absolute path that overrides the SQLite database file.
DOS_STORAGE_BACKEND   'csv' or 'sqlite' — overrides settings.json value.
DOS_LOG_LEVEL         'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' (read externally).

# API server
DOS_API_HOST          Interface Uvicorn binds to (default: 127.0.0.1).
DOS_API_PORT          TCP port Uvicorn listens on (default: 8000).
DOS_API_TOKEN         Static Bearer token for all authenticated endpoints.
                      When empty/unset the server runs in *dev mode*: all
                      authenticated requests are allowed and a warning is logged.
DOS_CORS_ORIGINS      Comma-separated list of allowed CORS origins.
                      Default: empty (CORS middleware not added).
                      Example: http://localhost:3000,https://app.example.com

Backward-compatibility guarantee
---------------------------------
When no environment variable is set the module produces *exactly* the same
paths and behaviour as the project-root ``config.py``.  All public names
exported here (DATA_DIR, DATABASE_PATH, SETTINGS_FILE,
get_storage_backend, set_storage_backend, is_sqlite_available) have the
same signature and semantics as in the root file.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Literal

from .utils.paths import get_data_dir as _paths_get_data_dir


# ---------------------------------------------------------------------------
# 1. DATA_DIR
#    DOS_DATA_DIR env var → absolute override
#    otherwise            → paths.get_data_dir()  (portable / %APPDATA% fallback)
# ---------------------------------------------------------------------------

def _resolve_data_dir() -> Path:
    raw = os.environ.get("DOS_DATA_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return _paths_get_data_dir()


DATA_DIR: Path = _resolve_data_dir()

# ---------------------------------------------------------------------------
# 2. DATABASE_PATH
#    DOS_DB_PATH env var → absolute override
#    otherwise           → DATA_DIR / "app.db"
# ---------------------------------------------------------------------------

def _resolve_db_path() -> Path:
    raw = os.environ.get("DOS_DB_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
        # Ensure parent directory exists (user may reference a new location)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return DATA_DIR / "app.db"


DATABASE_PATH: Path = _resolve_db_path()

# ---------------------------------------------------------------------------
# 3. SETTINGS_FILE  (always inside DATA_DIR — not overridable separately)
# ---------------------------------------------------------------------------

SETTINGS_FILE: Path = DATA_DIR / "settings.json"

# ---------------------------------------------------------------------------
# 4. Storage-backend management
#    Priority: DOS_STORAGE_BACKEND env > settings.json > 'sqlite' (default)
# ---------------------------------------------------------------------------

# Module-level mutable; updated by set_storage_backend()
_STORAGE_BACKEND: Literal["csv", "sqlite"] = "sqlite"


def get_storage_backend() -> Literal["csv", "sqlite"]:
    """
    Return the active storage backend.

    Resolution order:
      1. ``DOS_STORAGE_BACKEND`` environment variable (if set to 'csv' or 'sqlite')
      2. ``settings.json`` key ``storage_backend``
      3. Module default: ``'sqlite'``

    The returned value also updates the module-level ``_STORAGE_BACKEND``.
    """
    global _STORAGE_BACKEND

    # 1. Env var override
    env_val = os.environ.get("DOS_STORAGE_BACKEND", "").strip().lower()
    if env_val in ("csv", "sqlite"):
        _STORAGE_BACKEND = env_val  # type: ignore[assignment]
        return _STORAGE_BACKEND

    # 2. settings.json
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                settings = json.load(fh)
                backend = settings.get("storage_backend", "")
                if backend in ("csv", "sqlite"):
                    _STORAGE_BACKEND = backend  # type: ignore[assignment]
                    return _STORAGE_BACKEND
        except (json.JSONDecodeError, OSError):
            pass  # Fall through to default

    return _STORAGE_BACKEND


def set_storage_backend(backend: Literal["csv", "sqlite"]) -> bool:
    """
    Persist the storage backend choice to ``settings.json``.

    Returns ``True`` on success, ``False`` if *backend* is invalid or the
    file cannot be written.
    """
    global _STORAGE_BACKEND

    if backend not in ("csv", "sqlite"):
        return False

    # Load existing settings (preserve unrelated keys)
    settings: dict = {}
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                settings = json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass

    settings["storage_backend"] = backend

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        _STORAGE_BACKEND = backend
        return True
    except OSError:
        return False


def is_sqlite_available() -> bool:
    """
    Return ``True`` if the SQLite database exists and has been initialised
    (schema_version table is present).
    """
    if not DATABASE_PATH.exists():
        return False
    try:
        conn = sqlite3.connect(str(DATABASE_PATH), timeout=1.0)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        has_schema = cur.fetchone() is not None
        conn.close()
        return has_schema
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Initialise _STORAGE_BACKEND at import time (mirrors root config.py behaviour)
# ---------------------------------------------------------------------------
_STORAGE_BACKEND = get_storage_backend()


# ---------------------------------------------------------------------------
# 5. API server settings
#    All read lazily at call-time so tests can patch env vars freely.
# ---------------------------------------------------------------------------

def get_api_host() -> str:
    """Return the interface Uvicorn should bind to (default: ``127.0.0.1``)."""
    return os.environ.get("DOS_API_HOST", "127.0.0.1").strip() or "127.0.0.1"


def get_api_port() -> int:
    """Return the TCP port Uvicorn should listen on (default: ``8000``).

    Invalid values (non-numeric or out of range 1-65535) are silently replaced
    by the default.
    """
    raw = os.environ.get("DOS_API_PORT", "").strip()
    try:
        port = int(raw)
        if 1 <= port <= 65535:
            return port
    except (ValueError, TypeError):
        pass
    return 8000


def get_api_token() -> str:
    """Return the configured Bearer token.

    Returns an empty string when ``DOS_API_TOKEN`` is unset or blank, which
    signals *dev mode* to ``auth.py``.
    """
    return os.environ.get("DOS_API_TOKEN", "").strip()


def is_dev_mode() -> bool:
    """Return ``True`` when no Bearer token is configured.

    In dev mode all authenticated API endpoints are accessible without a token
    and a startup warning is emitted.  **Never run in dev mode in production.**
    """
    return get_api_token() == ""


def get_cors_origins() -> list[str]:
    """Return the list of allowed CORS origins.

    Reads ``DOS_CORS_ORIGINS`` (comma-separated, e.g.
    ``http://localhost:3000,https://app.example.com``).
    Returns an empty list when the variable is unset or blank — the CORS
    middleware should not be added in that case.
    """
    raw = os.environ.get("DOS_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# Convenience module-level snapshots (resolved at import time).
# Use the getter functions above when you need env-var changes to take effect
# without reloading the module (e.g. inside tests).
API_HOST: str = get_api_host()
API_PORT: int = get_api_port()
API_TOKEN: str = get_api_token()
CORS_ORIGINS: list[str] = get_cors_origins()
_STORAGE_BACKEND = get_storage_backend()
