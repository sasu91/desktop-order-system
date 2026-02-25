"""
dos_backend/main.py — Backward-compatible entry-point.

Delegates to ``dos_backend.api.app`` so both uvicorn targets keep working:

    uvicorn dos_backend.main:app        # legacy / current tools/run_backend.*
    uvicorn dos_backend.api.app:app     # canonical (new)
"""
from dos_backend.api.app import app  # noqa: F401

__all__ = ["app"]
