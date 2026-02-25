"""
dos_backend.api — FastAPI application package.

Canonical entry-point:
    uvicorn dos_backend.api.main:app --reload

Re-exports the single module-level app instance for convenience:
    from dos_backend.api import app
"""
from .main import app  # noqa: F401

__all__ = ["app"]
