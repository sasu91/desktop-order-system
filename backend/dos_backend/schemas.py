"""
backend/src/schemas.py — Pydantic request/response models.

Mirrors the shapes documented in docs/api_contract.md.
All models are placeholders; fields will be populated during implementation.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / error envelope
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    field: str
    issue: str


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


class ErrorResponse(BaseModel):
    error: ErrorEnvelope


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_path: str
    db_reachable: bool
    timestamp: str


# ---------------------------------------------------------------------------
# SKU / EAN lookup
# ---------------------------------------------------------------------------

class SKUResponse(BaseModel):
    sku: str
    description: str
    ean: Optional[str] = None
    ean_valid: bool = True
    moq: int = 1
    pack_size: int = 1
    lead_time_days: int = 7
    safety_stock: int = 0
    shelf_life_days: int = 0
    in_assortment: bool = True
    category: str = ""
    department: str = ""


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

class StockItem(BaseModel):
    sku: str
    description: str
    on_hand: int
    on_order: int
    last_event_date: Optional[date] = None


class StockListResponse(BaseModel):
    asof: date
    page: int
    page_size: int
    total: int
    items: list[StockItem]


class TransactionSummary(BaseModel):
    transaction_id: int
    date: date
    event: str
    qty: int
    receipt_date: Optional[date] = None
    note: str = ""


class StockDetailResponse(StockItem):
    asof: date
    recent_transactions: list[TransactionSummary] = []


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

ExceptionEventType = Literal["WASTE", "ADJUST", "UNFULFILLED"]


class ExceptionRequest(BaseModel):
    date: date
    sku: str
    event: ExceptionEventType
    qty: int = Field(..., ge=1)
    note: str = Field("", max_length=500)


class ExceptionResponse(BaseModel):
    transaction_id: int
    date: date
    sku: str
    event: str
    qty: int
    note: str
    idempotency_key: str


# ---------------------------------------------------------------------------
# Receipts close
# ---------------------------------------------------------------------------

class ReceiptLine(BaseModel):
    sku: str
    qty_received: int = Field(..., ge=1)
    note: str = Field("", max_length=200)


class ReceiptsCloseRequest(BaseModel):
    receipt_id: str = Field(..., max_length=100)
    receipt_date: date
    lines: list[ReceiptLine] = Field(..., min_length=1)


class ReceiptLineResult(BaseModel):
    sku: str
    qty_received: int
    transaction_id: Optional[int] = None
    status: Literal["ok", "already_received"]


class ReceiptsCloseResponse(BaseModel):
    receipt_id: str
    receipt_date: date
    already_processed: bool
    lines: list[ReceiptLineResult]
