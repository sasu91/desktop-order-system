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
# Stock mode
# ---------------------------------------------------------------------------

StockMode = Literal["POINT_IN_TIME", "END_OF_DAY"]
"""
Controlla la semantica della data AsOf nel calcolo stock.

- **POINT_IN_TIME** (default): stock all'inizio di `asof_date`.
  Vengono inclusi solo gli eventi con `date < asof_date`.
  Esempio: asof_date=2026-02-25 → eventi fino al 24 febbraio incluso.

- **END_OF_DAY**: stock alla fine di `asof_date`.
  Vengono inclusi tutti gli eventi con `date <= asof_date`.
  Internamente la data viene traslata a `asof_date + 1 giorno`
  prima di richiamare ``StockCalculator.calculate_asof()``.
  Esempio: asof_date=2026-02-25 → eventi fino al 25 febbraio incluso.
"""


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
    storage_backend: str          # 'sqlite' | 'csv'
    dev_mode: bool                # True when DOS_API_TOKEN is not set
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
    transaction_id: Optional[int] = None   # None for CSV backend (no row-id)
    date: date
    event: str
    qty: int
    receipt_date: Optional[date] = None
    note: str = ""


class StockDetailResponse(StockItem):
    asof: date
    """Effective AsOf date used in the calculation (already adjusted for END_OF_DAY mode)."""
    mode: StockMode = "POINT_IN_TIME"
    unfulfilled_qty: int = 0
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
    client_event_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "Optional UUID supplied by the client for idempotency. "
            "If the same client_event_id is received a second time the server "
            "replays the stored response with already_recorded=true without "
            "writing to the ledger again."
        ),
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class ExceptionResponse(BaseModel):
    transaction_id: Optional[int] = None  # None for CSV backend (no row-id)
    date: date
    sku: str
    event: str
    qty: int
    note: str
    idempotency_key: Optional[str] = None  # populated only when client_event_id was provided
    already_recorded: bool = False
    client_event_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Receipts close
# ---------------------------------------------------------------------------

class ReceiptLine(BaseModel):
    """
    Una riga di una ricezione merce.

    Risoluzione SKU (in ordine di precedenza):
    1. ``sku``  — codice SKU esatto (case-sensitive).
    2. ``ean``  — EAN-12/13; il server risolve l'EAN → SKU prima della validazione.
    3. Se entrambi mancano → errore 400 su quella riga.
    Se entrambi presenti, ``sku`` ha la precedenza; ``ean`` viene ignorato.
    """
    sku: Optional[str] = Field(
        default=None,
        description="Codice SKU (case-sensitive). Obbligatorio se ean non è fornito.",
    )
    ean: Optional[str] = Field(
        default=None,
        description="EAN-12 o EAN-13. Alternativa a sku (lookup server-side).",
    )
    qty_received: int = Field(
        ...,
        ge=0,
        description=(
            "Quantità ricevuta. 0 = nessun articolo ricevuto (riga accettata "
            "ma nessun evento RECEIPT scritto nel ledger)."
        ),
    )
    expiry_date: Optional[date] = Field(
        default=None,
        description=(
            "Data di scadenza del lotto. Obbligatoria se lo SKU ha "
            "has_expiry_label=true (etichetta scadenza manuale)."
        ),
    )
    note: str = Field("", max_length=200)


class ReceiptsCloseRequest(BaseModel):
    receipt_id: str = Field(
        ...,
        max_length=100,
        description=(
            "Chiave di idempotenza legacy (date+supplier+ref). "
            "Se già presente nei receiving_logs → 200 already_posted."
        ),
    )
    receipt_date: date
    lines: list[ReceiptLine] = Field(..., min_length=1)
    client_receipt_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "UUID opzionale del client per idempotenza forte. "
            "Se la stessa stringa arriva una seconda volta il server "
            "risponde already_posted=true senza riscrivere il ledger."
        ),
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class ReceiptLineResult(BaseModel):
    line_index: int
    sku: str
    ean: Optional[str] = None
    qty_received: int
    expiry_date: Optional[date] = None
    status: Literal["ok", "already_received", "skipped"]
    """
    ok             — RECEIPT scritto nel ledger.
    already_received — risposta replay (already_posted=True).
    skipped        — qty_received=0: riga accettata ma nessun RECEIPT scritto.
    """


class ReceiptsCloseResponse(BaseModel):
    receipt_id: str
    receipt_date: date
    already_posted: bool = False
    client_receipt_id: Optional[str] = None
    lines: list[ReceiptLineResult]
