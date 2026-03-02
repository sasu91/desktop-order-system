"""
dos_backend/api/auth.py — Bearer-token authentication dependencies.

Exported FastAPI dependencies
------------------------------
verify_token(credentials)   In *production mode* (DOS_API_TOKEN set):
                              • Raises HTTP 401 if the supplied token is wrong.
                              • Returns the validated token string.
                            In *dev mode* (DOS_API_TOKEN empty/unset):
                              • Allows every request unconditionally.
                              • Logs a one-time WARNING at first call.
                              • Returns the sentinel string ``"__dev__"``.

optional_token(credentials) Like verify_token but returns None when no
                            Authorization header is provided at all.
                            In dev mode, always returns ``"__dev__"``.

Dev mode vs. production mode
------------------------------
When ``DOS_API_TOKEN`` is **not set** (or blank) the server starts in dev mode:
  • Authentication is skipped for all ``verify_token`` / ``optional_token`` calls.
  • A WARNING is emitted once at first call: visible in uvicorn logs.
  • NEVER deploy with dev mode active; set a long random token in production.

Environment variable
---------------------
DOS_API_TOKEN   Shared secret sent by clients as ``Bearer <token>``.
                Leave unset (or blank) for dev mode.
                Generate a secure value: ``python -c "import secrets; print(secrets.token_hex(32))"``
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import get_api_token, is_dev_mode

logger = logging.getLogger("dos_backend.api.auth")

# Sentinel returned in dev mode so callers can detect it if needed.
_DEV_SENTINEL = "__dev__"

# Two schemes: one that auto-errors on missing header (required),
# one that doesn't so optional_token can return None.
_bearer_required = HTTPBearer(auto_error=True)
_bearer_optional = HTTPBearer(auto_error=False)

# Guards the one-time dev-mode log so it isn't spammed on every request.
_dev_warning_emitted = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _emit_dev_warning() -> None:
    """Log the dev-mode warning exactly once per process lifetime."""
    global _dev_warning_emitted
    if not _dev_warning_emitted:
        logger.warning(
            "\n"
            "  ⚠️  AUTH DEV MODE ACTIVE — DOS_API_TOKEN is not set.\n"
            "     All authenticated endpoints are accessible without a token.\n"
            "     Set DOS_API_TOKEN before deploying to production."
        )
        _dev_warning_emitted = True


def _validate(credentials: HTTPAuthorizationCredentials) -> str:
    """
    Compare *credentials* against ``DOS_API_TOKEN``.

    In dev mode the comparison is skipped and ``_DEV_SENTINEL`` is returned.

    :raises HTTPException 401: when the supplied token does not match (production only).
    :returns: Validated token string, or ``"__dev__"`` in dev mode.
    """
    if is_dev_mode():
        _emit_dev_warning()
        return _DEV_SENTINEL

    expected = get_api_token()   # re-read so patching env in tests works
    if credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_optional),
) -> str:
    """
    FastAPI dependency — validates the required Bearer token.

    In *dev mode* (``DOS_API_TOKEN`` unset) any request is accepted; no
    ``Authorization`` header is required.  Using ``_bearer_optional`` here
    prevents FastAPI from raising 403 before the function body runs when the
    client (e.g. Android app with no token configured) omits the header.

    In *production mode* the ``Authorization: Bearer <token>`` header is
    mandatory and the token must match ``DOS_API_TOKEN`` exactly;
    missing or wrong header → HTTP 401.

    Use as a router-level or endpoint-level dependency::

        router = APIRouter(dependencies=[Depends(verify_token)])
        # or
        @router.get("/resource", dependencies=[Depends(verify_token)])
    """
    if is_dev_mode():
        _emit_dev_warning()
        return _DEV_SENTINEL
    # Production mode: header is required
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token mancante. Inviare: Authorization: Bearer <DOS_API_TOKEN>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _validate(credentials)


def optional_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_optional),
) -> Optional[str]:
    """
    FastAPI dependency — validates the Bearer token only if one is supplied.

    • Dev mode  → always returns ``"__dev__"`` (and logs once).
    • Production, no header  → returns ``None``.
    • Production, wrong token → raises HTTP 401.
    • Production, correct token → returns the token string.
    """
    if is_dev_mode():
        _emit_dev_warning()
        return _DEV_SENTINEL
    if credentials is None:
        return None
    return _validate(credentials)
