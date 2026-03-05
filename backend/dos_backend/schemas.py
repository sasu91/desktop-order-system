"""
backend/dos_backend/schemas.py — Pydantic request/response models.

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
    ean_secondary: Optional[str] = None
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
# Scanner preload (bulk offline cache)
# ---------------------------------------------------------------------------

class ScannerPreloadItem(BaseModel):
    """Single EAN→SKU+stock row for the Android offline scanner cache.

    One row per barcode alias: if a SKU has both *ean* and *ean_secondary*,
    two rows are returned (same sku/stock data, different ``ean`` value).
    """
    ean: str
    sku: str
    description: str
    pack_size: int = 1
    on_hand: int = 0
    on_order: int = 0


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

class StockItem(BaseModel):
    sku: str
    description: str
    on_hand: int
    on_order: int
    pack_size: int = 1
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
    qty: float = Field(..., gt=0, description="Quantità: in colli (ADJUST/UNFULFILLED) o pezzi (WASTE). Conversione colli->pezzi avviene server-side.")
    note: str = Field(default="", max_length=500)
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
# Daily-upsert exception
# ---------------------------------------------------------------------------
# Semantic contract:
#   POST /exceptions         — appends a NEW transaction each call; quantity is
#                              a discrete event (e.g. one spoilage incident);
#                              multiple calls on the same day are intentional.
#
#   POST /exceptions/daily-upsert — maintains a SINGLE running total per
#                              (sku, date, event).  Two modes:
#
#                 "replace"  Set the total to exactly `qty`.  Idempotent: if
#                             the current total already equals `qty` the call
#                             is a no-op (noop=true).  Useful for ERP imports
#                             that send the end-of-day cumulative figure.
#
#                 "sum"      Append `qty` as an additional delta to the current
#                             total.  Functionally equivalent to /exceptions but
#                             the response includes the new running total so the
#                             caller can verify the accumulation.
# ---------------------------------------------------------------------------

DailyUpsertMode = Literal["sum", "replace"]


class DailyUpsertRequest(BaseModel):
    date: date
    sku: str
    event: ExceptionEventType
    qty: int = Field(..., ge=1, description="Units to set (replace) or add (sum).")
    mode: DailyUpsertMode = Field(
        "replace",
        description=(
            "\"replace\": set the daily total to exactly qty (idempotent). "
            "\"sum\": append qty as an additional delta."
        ),
    )
    note: str = Field("", max_length=500)


class DailyUpsertResponse(BaseModel):
    date: date
    sku: str
    event: str
    mode: DailyUpsertMode
    qty_delta: int = Field(
        description="Units actually written to the ledger (0 if noop; negative if replace reduced the total).",
    )
    qty_total: int = Field(description="Final running total for (sku, date, event) after this call.")
    note: str
    noop: bool = Field(
        description="True when replace mode is called with a qty that already matches the current total.",
    )


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


# ---------------------------------------------------------------------------
# EOD (End-of-Day) batch close
# ---------------------------------------------------------------------------
# Semantics per entry:
#   on_hand       → ADJUST event  (absolute inventory count; on_hand := qty)
#   waste_qty     → WASTE  event  (units wasted/spoiled this day)
#   unfulfilled_qty → UNFULFILLED event (demand not served this day)
#   adjust_qty    → ADJUST event  (manual adjustment / correction)
#
# on_hand and adjust_qty both map to ADJUST; if both are provided the backend
# writes them in declaration order (adjust_qty first, on_hand second) so that
# the end-of-day physical count is always the final state in the ledger.
#
# All entry fields are optional: an entry with every field null/0 is silently
# skipped (noop=true for that SKU).
# ---------------------------------------------------------------------------

class EodEntry(BaseModel):
    """Single SKU block inside an EOD close request."""
    sku: Optional[str] = None
    ean: Optional[str] = None
    # Colli fields (decimal): server converts colli->pezzi using SKU.pack_size
    # None = not provided / skip; all >= 0 validated in router
    on_hand: Optional[float] = Field(default=None, description="Giacenza fisica EOD in COLLI (decimale) -> ADJUST")
    waste_qty: Optional[int] = Field(default=None, description="Scarto in PEZZI (intero) -> WASTE")
    adjust_qty: Optional[float] = Field(default=None, description="Rettifica in COLLI (decimale) -> ADJUST")
    unfulfilled_qty: Optional[float] = Field(default=None, description="Non evaso in COLLI (decimale) -> UNFULFILLED")
    note: str = Field(default="", max_length=500)


class EodCloseRequest(BaseModel):
    date: date
    client_eod_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="UUID client per idempotenza.",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    entries: list[EodEntry] = Field(..., min_length=1)


class EodEntryResult(BaseModel):
    sku: str
    events_written: list[str] = []
    noop: bool = False
    skip_reason: Optional[str] = None


class EodCloseResponse(BaseModel):
    date: date
    client_eod_id: str
    already_posted: bool = False
    total_entries: int
    results: list[EodEntryResult] = []
