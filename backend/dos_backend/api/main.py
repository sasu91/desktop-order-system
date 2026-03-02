"""
dos_backend/api/main.py — single canonical entry-point for the API server.

Uvicorn (recommended):
    uvicorn dos_backend.api.main:app --reload --host 127.0.0.1 --port 8000

Programmatic (e.g. in a start script):
    python -m dos_backend.api.main

Helper scripts (handle venv + env vars automatically):
    bash tools/run_backend.sh
    .\\tools\\run_backend.ps1

Test isolation — never import this module; use create_app() directly:
    from dos_backend.api.app import create_app
    app = create_app()   # fresh instance per test
"""
from __future__ import annotations

import os

from dos_backend.api.app import create_app

# ---------------------------------------------------------------------------
# Module-level app instance  ← single place where this lives
# ---------------------------------------------------------------------------
app = create_app()


# ---------------------------------------------------------------------------
# Programmatic entry-point  (python -m dos_backend.api.main)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    from dos_backend.config import get_api_host, get_api_port

    host = get_api_host()
    port = get_api_port()
    log_level = os.environ.get("DOS_LOG_LEVEL", "INFO").lower()
    reload = os.environ.get("DOS_RELOAD", "").lower() in ("1", "true", "yes")

    uvicorn.run(
        "dos_backend.api.main:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
        timeout_keep_alive=5,    # OkHttp pool idle=3 s → scade prima; mai connessioni stale
    )
