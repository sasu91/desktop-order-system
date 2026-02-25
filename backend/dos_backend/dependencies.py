"""
backend/src/dependencies.py — FastAPI dependency-injection helpers.

Provides:
  - get_db()       : yields a read-only SQLite connection pointing at DOS_DB_PATH
  - verify_token() : validates the Bearer token against DOS_API_TOKEN
"""
import os
import sqlite3
from typing import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a SQLite connection for the request lifetime.

    Reads DOS_DB_PATH from the environment (set by tools/run_backend.sh or .env).
    Raises RuntimeError at startup if the variable is not set.
    """
    db_path = os.environ.get("DOS_DB_PATH")
    if not db_path:
        raise RuntimeError(
            "DOS_DB_PATH non impostata. "
            "Imposta la variabile d'ambiente prima di avviare il backend."
        )

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=True)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """
    Validate the Bearer token against DOS_API_TOKEN.

    Returns the token string on success.
    Raises 401 if the token is missing, empty, or doesn't match.
    """
    expected = os.environ.get("DOS_API_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DOS_API_TOKEN non configurato sul server.",
        )
    if credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
