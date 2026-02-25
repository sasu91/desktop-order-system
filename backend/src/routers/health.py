"""
GET /health — liveness check (no authentication required).
"""
import os
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter

from backend.src.schemas import HealthResponse

router = APIRouter(tags=["health"])

_VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
def health() -> HealthResponse:
    """
    Returns service status and DB reachability.
    This endpoint is public — no Bearer token required.
    """
    db_path = os.environ.get("DOS_DB_PATH", "")
    db_reachable = _check_db(db_path)

    return HealthResponse(
        status="ok" if db_reachable else "degraded",
        version=_VERSION,
        db_path=db_path,
        db_reachable=db_reachable,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _check_db(db_path: str) -> bool:
    """Return True if the SQLite file is reachable and has a schema_version table."""
    if not db_path:
        return False
    try:
        conn = sqlite3.connect(db_path, timeout=1.0)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        result = cur.fetchone() is not None
        conn.close()
        return result
    except Exception:
        return False
