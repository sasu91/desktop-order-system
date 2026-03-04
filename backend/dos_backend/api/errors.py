"""
dos_backend/api/errors.py — Custom exception hierarchy + FastAPI exception handlers.

Usage (inside create_app):
    from .errors import register_handlers
    register_handlers(app)

Exception hierarchy
-------------------
DosApiError (base)
  ├── NotFoundError          → 404  NOT_FOUND
  ├── ConflictError          → 409  CONFLICT
  ├── UnprocessableError     → 422  VALIDATION_ERROR
  └── ServiceUnavailableError→ 503  SERVICE_UNAVAILABLE

Handlers registered
-------------------
DosApiError          → status_code from exc, wrapped in ErrorEnvelope
HTTPException        → pass-through, wrapped in ErrorEnvelope
RequestValidationError → 422 with per-field details
Exception            → 500, full traceback logged (detail not leaked to client)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..schemas import ErrorDetail, ErrorEnvelope, ErrorResponse

logger = logging.getLogger("dos_backend.api.errors")


# ---------------------------------------------------------------------------
# Domain exception hierarchy
# ---------------------------------------------------------------------------

class DosApiError(Exception):
    """Base class for all dos_backend API-layer errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: list[ErrorDetail] = details or []


class NotFoundError(DosApiError):
    """Resource not found."""
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class BadRequestError(DosApiError):
    """Malformed or semantically invalid input that cannot be processed.

    Use for client-supplied values that fail validation *before* hitting
    business logic — e.g. an EAN string with wrong length or non-digit chars.
    Distinct from ``UnprocessableError`` (422) which is for Pydantic schema
    errors raised by RequestValidationError.
    """
    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"


class ConflictError(DosApiError):
    """Duplicate key or conflicting state (e.g. receipt already processed)."""
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class UnprocessableError(DosApiError):
    """Business-rule validation failure (separate from Pydantic schema errors)."""
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_ERROR"


class ServiceUnavailableError(DosApiError):
    """Dependency (DB, external service) temporarily unavailable."""
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_body(code: str, message: str, details: list[ErrorDetail]) -> dict[str, Any]:
    """Serialise an ErrorResponse to a plain dict for JSONResponse."""
    return ErrorResponse(
        error=ErrorEnvelope(code=code, message=message, details=details)
    ).model_dump()


_HTTP_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

async def _handle_dos_api_error(request: Request, exc: DosApiError) -> JSONResponse:
    logger.warning(
        "DosApiError [%s] on %s %s: %s",
        exc.code, request.method, request.url.path, exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_make_body(exc.code, exc.message, exc.details),
    )


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Wrap FastAPI/Starlette HTTPException into the standard ErrorEnvelope.
    Existing ``raise HTTPException(...)`` calls in routers keep working unchanged.
    """
    code = _HTTP_CODE_MAP.get(exc.status_code, "HTTP_ERROR")
    detail_str = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_make_body(code, detail_str, []),
        headers=getattr(exc, "headers", None) or {},
    )


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic ``RequestValidationError`` → 422 with per-field error details."""
    details = [
        ErrorDetail(
            field=".".join(str(loc) for loc in err["loc"]),
            issue=err["msg"],
        )
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_make_body("VALIDATION_ERROR", "Request validation failed.", details),
    )


async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected exceptions — logs full traceback, returns generic 500.

    In dev mode (DOS_API_TOKEN unset) the response body includes the exception
    type and message to aid debugging.  In production the message is generic.
    """
    import traceback as _tb
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    # In dev mode expose the exception details so they show up on the phone screen.
    from ..config import is_dev_mode
    if is_dev_mode():
        detail = f"{type(exc).__name__}: {exc}"
        tb_lines = _tb.format_exc().splitlines()
        tb_short = " | ".join(l.strip() for l in tb_lines[-6:] if l.strip())
        message = f"[DEV] {detail}"
        details = [ErrorDetail(field="traceback", issue=tb_short[:400])]
    else:
        message = "An unexpected error occurred."
        details = []
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_make_body("INTERNAL_ERROR", message, details),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to *app*.  Call once inside create_app()."""
    app.add_exception_handler(DosApiError, _handle_dos_api_error)          # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, _handle_http_exception)        # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unhandled)                 # type: ignore[arg-type]
