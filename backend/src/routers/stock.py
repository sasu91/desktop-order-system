"""
GET /stock        — stock calcolato AsOf per lista SKU (paginato)
GET /stock/{sku}  — stock + ultimi eventi per singolo SKU
"""
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.src.dependencies import get_db, verify_token
from backend.src.schemas import StockDetailResponse, StockListResponse

router = APIRouter(tags=["stock"])


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

    La logica di calcolo richiama StockCalculator.calculate_all_skus() dal dominio.
    """
    # TODO: implementare calcolo AsOf via src.domain.ledger.StockCalculator
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
    asof: date = Query(default=None, description="Data di calcolo (default: oggi)"),
    db: sqlite3.Connection = Depends(get_db),
) -> StockDetailResponse:
    """
    Restituisce stock + ultime transazioni per un singolo SKU.
    404 se lo SKU non esiste.
    """
    # TODO: implementare lookup SKU + calcolo AsOf + ultimi N eventi
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Endpoint non ancora implementato.",
    )
