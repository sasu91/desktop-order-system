"""
dos_backend/api/deps.py — FastAPI dependency-injection providers.

All symbols are safe to use with ``Depends()`` in route functions.

Exported
--------
get_db          Yields a raw ``sqlite3.Connection`` (row_factory=sqlite3.Row).
                Resolves path from DOS_DB_PATH env → dos_backend.config.DATABASE_PATH.

get_storage     Yields a ``StorageAdapter`` (csv or sqlite per dos_backend.config).
                Backend selection follows: DOS_STORAGE_BACKEND → settings.json → default.

verify_token    Re-exported from .auth — required Bearer-token dependency.
optional_token  Re-exported from .auth — optional Bearer-token dependency.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Generator

from .auth import optional_token, verify_token  # re-export for convenience
from ..config import DATABASE_PATH

__all__ = ["get_db", "get_storage", "verify_token", "optional_token"]


# ---------------------------------------------------------------------------
# SQLite connection
# ---------------------------------------------------------------------------

def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a ``sqlite3.Connection`` scoped to the current HTTP request.

    Path resolution order:
      1. ``DOS_DB_PATH`` environment variable (absolute path)
      2. ``dos_backend.config.DATABASE_PATH`` (env-aware, respects DOS_DATA_DIR)

    The connection is always closed in the ``finally`` block regardless of errors.
    ``row_factory`` is set to ``sqlite3.Row`` for dict-like column access.
    """
    db_path = os.environ.get("DOS_DB_PATH", "").strip() or str(DATABASE_PATH)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# StorageAdapter
# ---------------------------------------------------------------------------

def get_storage() -> Generator:
    """
    Yield a ``StorageAdapter`` scoped to the current HTTP request.

    The backend (``csv`` / ``sqlite``) is resolved by ``dos_backend.config``:
      1. ``DOS_STORAGE_BACKEND`` env var
      2. ``settings.json`` key ``storage_backend``
      3. Compile-time default (``sqlite``)

    ``adapter.close()`` is called in the ``finally`` block to release the
    underlying SQLite connection when the backend is ``sqlite``.
    """
    # Lazy import avoids pulling in heavy SQLite setup at module load time.
    from ..persistence.storage_adapter import StorageAdapter  # noqa: PLC0415

    adapter = StorageAdapter()
    try:
        yield adapter
    finally:
        adapter.close()
