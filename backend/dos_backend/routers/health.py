"""
GET /health — liveness / readiness probe (no authentication required).

Returns:
  200 ok       — DB reachable, service healthy
  200 degraded — DB unreachable or not initialised (service started but not fully ready)

The endpoint never raises an error; degraded state is expressed in the payload
so that load-balancers / uptime monitors always receive a parseable response.
"""
import sqlite3
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as pkg_version

from fastapi import APIRouter

from ..config import DATABASE_PATH, get_storage_backend, is_dev_mode
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


def _get_version() -> str:
    try:
        return pkg_version("dos-backend")
    except PackageNotFoundError:
        return "0.1.0"


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


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness / readiness check",
)
def health() -> HealthResponse:
    """
    Returns service status and DB reachability.

    This endpoint is **public** — no Bearer token required.

    - `status: "ok"` — DB reachable and schema initialised.
    - `status: "degraded"` — service is running but DB is not reachable or not
      yet initialised (e.g. first boot before migration runs).
    - `dev_mode: true` — `DOS_API_TOKEN` is not set; all authenticated endpoints
      are accessible without a token.  **Do not run in production.**
    """
    # Path: DOS_DB_PATH env wins, then dos_backend.config.DATABASE_PATH
    db_path = str(DATABASE_PATH)
    db_reachable = _check_db(db_path)

    return HealthResponse(
        status="ok" if db_reachable else "degraded",
        version=_get_version(),
        db_path=db_path,
        db_reachable=db_reachable,
        storage_backend=get_storage_backend(),
        dev_mode=is_dev_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
