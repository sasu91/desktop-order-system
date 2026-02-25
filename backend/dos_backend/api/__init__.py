"""
dos_backend.api — FastAPI application package.

Quick start:
    uvicorn dos_backend.api.app:app --reload

Or import the app directly (e.g., in tests):
    from dos_backend.api import app
"""
from .app import app  # noqa: F401

__all__ = ["app"]
