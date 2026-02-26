"""
dos_backend.api — FastAPI application package.

Canonical entry-point:
    uvicorn dos_backend.api.main:app --reload

Re-exports the single module-level app instance for convenience:
    from dos_backend.api import app

The import of ``app`` is intentionally **lazy** (via ``__getattr__``) so that
importing the package itself does not eagerly call ``create_app()``.
Tools that only need a fresh app instance (e.g. export_openapi.py) can
import ``create_app`` from the sub-module without triggering the server
singleton as a side-effect.
"""
from __future__ import annotations

__all__ = ["app"]


def __getattr__(name: str) -> object:
    if name == "app":
        from .main import app  # noqa: PLC0415
        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
