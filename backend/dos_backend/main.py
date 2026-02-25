"""
dos_backend/main.py — FastAPI application entry-point.

Start with uvicorn:
    uvicorn dos_backend.main:app --reload

Or via the helper script:
    bash tools/run_backend.sh
    .\\tools\\run_backend.ps1
"""
from fastapi import FastAPI

from dos_backend.routers import health, skus, stock, exceptions, receipts

app = FastAPI(
    title="desktop-order-system API",
    description="REST backend for desktop-order-system (stock, EAN lookup, exceptions, receipts).",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(health.router)
app.include_router(skus.router,       prefix="/api/v1")
app.include_router(stock.router,      prefix="/api/v1")
app.include_router(exceptions.router, prefix="/api/v1")
app.include_router(receipts.router,   prefix="/api/v1")
