"""
dos_backend/api/app.py — FastAPI application factory.

This module only contains ``create_app()``; it never instantiates the app
at import time.  The single module-level ``app`` instance lives in
``dos_backend.api.main`` (the canonical entry-point).

Entry point:
    uvicorn dos_backend.api.main:app --reload

Test isolation:
    from dos_backend.api.app import create_app
    app = create_app()   # fresh instance per test
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import AsyncGenerator

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import register_handlers
from ..routers import health, skus, stock, exceptions, receipts


# ---------------------------------------------------------------------------
# Keep-alive guard — pure ASGI middleware (no BaseHTTPMiddleware)
# ---------------------------------------------------------------------------
class _ConnectionCloseMiddleware:
    """Force ``Connection: close`` on every HTTP response.

    Implemented as a raw ASGI middleware to avoid the well-known Starlette
    ``BaseHTTPMiddleware`` bug: when the client closes the TCP connection
    (because of *this very header*), the BaseHTTPMiddleware background task
    tries to send on a dead socket, raises an asyncio error, and leaves the
    uvicorn worker in a broken state where it ignores all future requests.

    This pure-ASGI version intercepts only the ``http.response.start`` ASGI
    message and injects the header without creating any extra tasks.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _inject_close(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Strip any existing Connection header then add ours.
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"connection"
                ]
                headers.append((b"connection", b"close"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _inject_close)


# ---------------------------------------------------------------------------
# SQLite startup — runs ONCE at server start via lifespan
# ---------------------------------------------------------------------------
def _run_sqlite_startup() -> None:
    """Backup + migrate DB; called on a thread-pool thread at lifespan startup.

    Running this before uvicorn starts accepting requests means:
    - The first HTTP request never has to wait for I/O-heavy operations.
    - No per-request locking/deadlock risk.
    - The per-request guard in StorageAdapter acts only as a safety net.
    """
    _log = logging.getLogger("dos_backend.startup")
    try:
        from ..config import get_storage_backend, DATABASE_PATH, is_sqlite_available
        if get_storage_backend() != "sqlite" or not is_sqlite_available():
            return

        from ..db import open_connection, apply_migrations, automatic_backup_on_startup
        from ..persistence import storage_adapter as _sa

        with _sa._sqlite_startup_lock:
            if _sa._sqlite_startup_done:
                return  # already done (shouldn't happen in lifespan, but safe)
            _log.info("SQLite startup: backup + migration…")
            automatic_backup_on_startup(max_backups=10)
            conn = open_connection(DATABASE_PATH)
            apply_migrations(conn)
            conn.close()
            _sa._sqlite_startup_done = True
            _log.info("SQLite startup complete.")
    except Exception as exc:
        _log.warning("SQLite startup task failed (degraded mode): %s", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Run heavy startup work before accepting requests, clean up on shutdown."""
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="dos-startup"
    ) as pool:
        await loop.run_in_executor(pool, _run_sqlite_startup)
    yield  # server is live here


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
        lifespan=_lifespan,
    )

    # ------------------------------------------------------------------
    # Middleware — pure ASGI, no BaseHTTPMiddleware
    # ------------------------------------------------------------------
    # Force Connection: close header; client discards socket after each
    # response, preventing stale-connection retries on the next scan.
    app.add_middleware(_ConnectionCloseMiddleware)

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


# Module-level app is intentionally absent from this file.
# See dos_backend.api.main for the single canonical app instance.
