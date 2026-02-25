"""
GET /stock        — stock calcolato AsOf per lista SKU (paginato)
GET /stock/{sku}  — stock + ultimi eventi per singolo SKU
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.src.dependencies import get_db, verify_token
from backend.src.schemas import StockDetailResponse, StockListResponse, TransactionSummary

# Domain imports (src/ is on sys.path via pyproject.toml / editable install)
from src.repositories import LedgerRepository, SKURepository
from src.domain.ledger import StockCalculator
from src.domain.models import EventType, Transaction

router = APIRouter(tags=["stock"])


def _dict_to_transaction(row: dict) -> Transaction:
    """Convert a SQLite row dict to a domain Transaction object."""
    return Transaction(
        date=date.fromisoformat(row["date"]),
        sku=row["sku"],
        event=EventType(row["event"]),
        qty=int(row["qty"]),
        receipt_date=date.fromisoformat(row["receipt_date"]) if row.get("receipt_date") else None,
        note=row.get("note") or "",
    )


@router.get(
    "/stock",
    response_model=StockListResponse,
    summary="Stock AsOf per tutti gli SKU",
    dependencies=[Depends(verify_token)],
)
def list_stock(
    asof: date = Query(default=None, description="Data di calcolo (default: oggi)"),
    sku: list[str] = Query(default=[], description="Filtra su uno o più SKU"),
    in_assortment: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> StockListResponse:
    """
    Restituisce lo stock calcolato ledger-AsOf per tutti gli SKU (o un sottoinsieme).
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Endpoint non ancora implementato.",
    )


@router.get(
    "/stock/{sku}",
    response_model=StockDetailResponse,
    summary="Stock AsOf per singolo SKU",
    dependencies=[Depends(verify_token)],
)
def get_stock(
    sku: str,
    asof: Optional[date] = Query(default=None, description="Data di calcolo (default: oggi)"),
    recent_n: int = Query(default=10, ge=1, le=100, description="Quante transazioni recenti restituire"),
    db: sqlite3.Connection = Depends(get_db),
) -> StockDetailResponse:
    """
    Restituisce stock + ultime transazioni per un singolo SKU.

    La query è completamente spinta in SQLite (WHERE sku=? AND date < ?),
    senza caricare le transazioni degli altri SKU né troncare a 10 000 righe.
    404 se lo SKU non esiste nel master data.
    """
    asof_date = asof or date.today()

    # ── 1. Verifica esistenza SKU ────────────────────────────────────────────
    sku_repo = SKURepository(db)
    sku_row = sku_repo.get(sku)
    if sku_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SKU '{sku}' non trovato.",
        )

    # ── 2. Carica solo le transazioni rilevanti (sku=? AND date < asof) ──────
    ledger_repo = LedgerRepository(db)
    txns_dicts = ledger_repo.list_transactions_for_sku_asof(sku, asof_date)
    transactions = [_dict_to_transaction(r) for r in txns_dicts]

    # ── 3. Calcola stock AsOf ────────────────────────────────────────────────
    stock = StockCalculator.calculate_asof(sku, asof_date, transactions)

    # ── 4. Ultimi N eventi (più recenti prima) ───────────────────────────────
    recent_txns_dicts = txns_dicts[-recent_n:][::-1]  # last N, reversed (most recent first)
    recent_transactions = [
        TransactionSummary(
            transaction_id=r["transaction_id"],
            date=date.fromisoformat(r["date"]),
            event=r["event"],
            qty=int(r["qty"]),
            receipt_date=date.fromisoformat(r["receipt_date"]) if r.get("receipt_date") else None,
            note=r.get("note") or "",
        )
        for r in recent_txns_dicts
    ]

    last_event_date = (
        date.fromisoformat(txns_dicts[-1]["date"]) if txns_dicts else None
    )

    # ── 5. Risposta ──────────────────────────────────────────────────────────
    return StockDetailResponse(
        sku=sku,
        description=sku_row.get("description", ""),
        on_hand=stock.on_hand,
        on_order=stock.on_order,
        last_event_date=last_event_date,
        asof=asof_date,
        recent_transactions=recent_transactions,
    )
