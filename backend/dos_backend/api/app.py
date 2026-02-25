"""
dos_backend/api/app.py — FastAPI application factory.

Entry points
------------
Canonical:
    uvicorn dos_backend.api.app:app --reload

Legacy (still works — main.py delegates here):
    uvicorn dos_backend.main:app

Test isolation:
    from dos_backend.api.app import create_app
    app = create_app()   # fresh instance per test
"""
from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from .errors import register_handlers
from ..routers import health, skus, stock, exceptions, receipts


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _get_version() -> str:
    try:
        return version("dos-backend")
    except PackageNotFoundError:
        return "0.1.0"


_TITLE = "desktop-order-system API"
_DESCRIPTION = (
    "REST backend for **desktop-order-system**: stock queries, EAN lookup, "
    "exception logging, and receipt closure.\n\n"
    "All write endpoints require a `Bearer <DOS_API_TOKEN>` header.\n"
    "See [`docs/api_contract.md`]"
    "(https://github.com/sasu91/desktop-order-system/blob/main/docs/api_contract.md) "
    "for the full contract."
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    """
    Configure stdlib logging for the ``dos_backend`` namespace.

    Level is read from ``DOS_LOG_LEVEL`` (default: ``INFO``).
    Output goes to stdout so container runtimes / systemd can capture it.

    Idempotent: if handlers are already attached the function is a no-op,
    which makes it safe to call repeatedly (e.g. during testing).
    """
    level_name = os.environ.get("DOS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger("dos_backend")
    if root_logger.handlers:
        # Already configured — respect previous setup (e.g. from utils.logging_config)
        root_logger.setLevel(level)
        return

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
    # Don't propagate to the root logger; we manage our own handler.
    root_logger.propagate = False


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    Build and fully wire the FastAPI application.

    Steps performed:
    1. Configure stdlib logging for the ``dos_backend`` namespace
    2. Create FastAPI instance with title, version, and /api/docs active
    3. Register exception handlers (ErrorEnvelope wrapping)
    4. Include all routers under /api/v1 (except /health which is at root)

    Call once at module level for production, or per-test for isolation.
    """
    _configure_logging()
    logger = logging.getLogger("dos_backend.api")

    api_version = _get_version()

    app = FastAPI(
        title=_TITLE,
        version=api_version,
        description=_DESCRIPTION,
        # /api/docs and /api/redoc are always active; set docs_url=None to disable.
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # ------------------------------------------------------------------
    # Exception handlers — wrap everything in ErrorEnvelope
    # ------------------------------------------------------------------
    register_handlers(app)

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    # /health — public, no prefix (acts as liveness probe)
    app.include_router(health.router)

    # /api/v1/* — versioned, authenticated endpoints
    app.include_router(skus.router,       prefix="/api/v1")
    app.include_router(stock.router,      prefix="/api/v1")
    app.include_router(exceptions.router, prefix="/api/v1")
    app.include_router(receipts.router,   prefix="/api/v1")

    logger.info("FastAPI app created — version=%s", api_version)
    return app


# ---------------------------------------------------------------------------
# Module-level app instance
# uvicorn dos_backend.api.app:app
# ---------------------------------------------------------------------------
app = create_app()
