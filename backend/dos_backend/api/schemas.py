"""
dos_backend/api/schemas.py — API-layer Pydantic schema home.

Re-exports every model from ``dos_backend.schemas`` so route modules can import
from a single canonical location::

    from ..api.schemas import ExceptionRequest, HealthResponse

New endpoint-specific schemas that don't belong in the shared domain schemas
module should be added here.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Re-export all shared schemas (alphabetical)
# ---------------------------------------------------------------------------
from ..schemas import (  # noqa: F401
    ErrorDetail,
    ErrorEnvelope,
    ErrorResponse,
    ExceptionEventType,
    ExceptionRequest,
    ExceptionResponse,
    HealthResponse,
    ReceiptLine,
    ReceiptLineResult,
    ReceiptsCloseRequest,
    ReceiptsCloseResponse,
    SKUResponse,
    StockDetailResponse,
    StockItem,
    StockListResponse,
    TransactionSummary,
)


# ---------------------------------------------------------------------------
# API-layer-specific schemas
# ---------------------------------------------------------------------------

class PaginationParams(BaseModel):
    """
    Common pagination query parameters.

    Intended for use as a FastAPI Query dependency::

        from fastapi import Depends, Query
        from ..api.schemas import PaginationParams

        @router.get("/items")
        def list_items(
            page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=500),
        ): ...
    """

    page: int = Field(1, ge=1, description="Numero pagina (1-based)")
    page_size: int = Field(50, ge=1, le=500, description="Elementi per pagina")


class MessageResponse(BaseModel):
    """Generic single-message response used by write endpoints."""
    message: str
