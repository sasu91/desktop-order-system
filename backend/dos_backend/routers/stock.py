"""
GET /stock        — stock calcolato AsOf per lista SKU (paginato)
GET /stock/{sku}  — stock + ultimi eventi per singolo SKU

Mode semantics
--------------
POINT_IN_TIME (default):
    asof_date è esclusiva.  Gli eventi vengono inclusi se date < asof_date.
    → stock all'inizio della giornata `asof_date`.
    Esempio: asof_date=2026-02-25 → tutti gli eventi fino al 24/02 incluso.

END_OF_DAY:
    asof_date è inclusiva.  La data viene traslata internamente di +1 giorno
    prima di chiamare StockCalculator, quindi date <= asof_date vengono inclusi.
    → stock alla fine della giornata `asof_date`.
    Esempio: asof_date=2026-02-25 → tutti gli eventi fino al 25/02 incluso.

La trasformazione avviene *qui*, nel router, non nel dominio.  Il dominio
rimane puro: StockCalculator.calculate_asof(sku, effective_asof, ...) con
semantica sempre date < effective_asof.
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..api.auth import verify_token
from ..api.deps import get_storage
from ..api.errors import NotFoundError
from ..domain.ledger import StockCalculator
from ..schemas import (
    StockDetailResponse,
    StockItem,
    StockListResponse,
    StockMode,
    TransactionSummary,
)

router = APIRouter(tags=["stock"])

# How many recent transactions to include in the detail response by default.
_DEFAULT_RECENT_N = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_asof(asof_date: Optional[date]) -> date:
    """Return asof_date or today if None."""
    return asof_date if asof_date is not None else date.today()


def _effective_asof(asof_date: date, mode: StockMode) -> date:
    """
    Translate the user-facing asof_date into the internal ``effective_asof``
    passed to ``StockCalculator.calculate_asof()``.

    StockCalculator rule:  date < effective_asof  →  event included.

    POINT_IN_TIME: effective = asof_date        (events of asof_date excluded)
    END_OF_DAY   : effective = asof_date + 1d   (events of asof_date included)
    """
    if mode == "END_OF_DAY":
        return asof_date + timedelta(days=1)
    return asof_date


# ---------------------------------------------------------------------------
# GET /stock  (list, paginated)
# ---------------------------------------------------------------------------

@router.get(
    "/stock",
    response_model=StockListResponse,
    summary="Stock AsOf per tutti gli SKU (paginato)",
    dependencies=[Depends(verify_token)],
)
def list_stock(
    asof_date: Optional[date] = Query(
        default=None,
        alias="asof_date",
        description="Data di calcolo stock (default: oggi). Semantica: date < asof_date.",
    ),
    mode: StockMode = Query(
        default="POINT_IN_TIME",
        description=(
            "POINT_IN_TIME (default) = eventi con date < asof_date. "
            "END_OF_DAY = eventi con date <= asof_date (internamente +1 giorno)."
        ),
    ),
    sku: list[str] = Query(default=[], description="Filtra su uno o più SKU"),
    in_assortment: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    storage=Depends(get_storage),
) -> StockListResponse:
    """
    Restituisce lo stock calcolato ledger-AsOf per tutti gli SKU (o sottoinsieme).
    """
    resolved = _resolve_asof(asof_date)
    effective = _effective_asof(resolved, mode)

    all_skus = storage.read_skus()
    if in_assortment:
        all_skus = [s for s in all_skus if s.in_assortment]
    if sku:
        sku_set = {s.upper() for s in sku}
        all_skus = [s for s in all_skus if s.sku.upper() in sku_set]

    transactions = storage.read_transactions()
    sales_records = storage.read_sales() if hasattr(storage, "read_sales") else []

    sku_ids = [s.sku for s in all_skus]
    stock_map = StockCalculator.calculate_all_skus(sku_ids, effective, transactions, sales_records)

    # Build description lookup
    desc_map = {s.sku: s.description for s in all_skus}

    # Determine last event date per SKU
    last_event: dict[str, date] = {}
    for txn in transactions:
        if txn.date < effective:
            if txn.sku not in last_event or txn.date > last_event[txn.sku]:
                last_event[txn.sku] = txn.date

    items: list[StockItem] = []
    for sku_id in sku_ids:
        stock = stock_map[sku_id]
        items.append(
            StockItem(
                sku=sku_id,
                description=desc_map.get(sku_id, ""),
                on_hand=stock.on_hand,
                on_order=stock.on_order,
                last_event_date=last_event.get(sku_id),
            )
        )

    # Paginate
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]

    return StockListResponse(
        asof=resolved,
        page=page,
        page_size=page_size,
        total=total,
        items=page_items,
    )


# ---------------------------------------------------------------------------
# GET /stock/{sku}
# ---------------------------------------------------------------------------

@router.get(
    "/stock/{sku}",
    response_model=StockDetailResponse,
    summary="Stock AsOf per singolo SKU + ultimi eventi",
    dependencies=[Depends(verify_token)],
)
def get_stock(
    sku: str,
    asof_date: Optional[date] = Query(
        default=None,
        alias="asof_date",
        description=(
            "Data di calcolo (default: oggi). "
            "Con mode=POINT_IN_TIME gli eventi di questa data vengono esclusi; "
            "con mode=END_OF_DAY vengono inclusi."
        ),
    ),
    mode: StockMode = Query(
        default="POINT_IN_TIME",
        description=(
            "POINT_IN_TIME (default): stock all'inizio di asof_date, cioè "
            "include tutti gli eventi con date < asof_date. "
            "END_OF_DAY: stock alla fine di asof_date, cioè include tutti "
            "gli eventi con date <= asof_date (internamente: asof_date + 1d)."
        ),
    ),
    recent_n: int = Query(
        default=_DEFAULT_RECENT_N,
        ge=0,
        le=200,
        alias="recent_n",
        description="Numero di transazioni recenti da includere nella risposta (default 20).",
    ),
    storage=Depends(get_storage),
) -> StockDetailResponse:
    """
    Restituisce stock calcolato + ultime ``recent_n`` transazioni per un singolo SKU.

    **Semantica della data**

    | mode            | Condizione interna       | Significato                        |
    |-----------------|--------------------------|-------------------------------------|
    | POINT_IN_TIME   | date < asof_date         | Stock all'apertura di asof_date     |
    | END_OF_DAY      | date < asof_date + 1     | Stock alla chiusura di asof_date    |

    **Errori**

    - 404 se lo SKU non esiste nel database.
    """
    # 1. Resolve date
    resolved = _resolve_asof(asof_date)

    # 2. Translate to effective_asof used by StockCalculator
    effective = _effective_asof(resolved, mode)

    # 3. Validate SKU exists
    all_skus = storage.read_skus()
    sku_obj = next((s for s in all_skus if s.sku == sku), None)
    if sku_obj is None:
        raise NotFoundError(f"SKU '{sku}' non trovato nel database.")

    # 4. Load ledger and calculate stock
    transactions = storage.read_transactions()
    sales_records = storage.read_sales() if hasattr(storage, "read_sales") else []
    stock = StockCalculator.calculate_asof(sku, effective, transactions, sales_records)

    # 5. Filter transactions for this SKU, date < effective_asof, sorted desc
    sku_txns = [
        t for t in transactions
        if t.sku == sku and t.date < effective
    ]
    sku_txns.sort(key=lambda t: t.date, reverse=True)

    last_event_date: Optional[date] = sku_txns[0].date if sku_txns else None

    # 6. Build recent_transactions summary (no row-id in domain model)
    recent: list[TransactionSummary] = [
        TransactionSummary(
            transaction_id=None,
            date=t.date,
            event=t.event.value,
            qty=t.qty,
            receipt_date=t.receipt_date,
            note=t.note or "",
        )
        for t in sku_txns[:recent_n]
    ]

    # 7. Assemble response
    return StockDetailResponse(
        sku=sku,
        description=sku_obj.description,
        on_hand=stock.on_hand,
        on_order=stock.on_order,
        unfulfilled_qty=stock.unfulfilled_qty,
        last_event_date=last_event_date,
        asof=resolved,        # user-facing date (not the shifted effective)
        mode=mode,
        recent_transactions=recent,
    )
