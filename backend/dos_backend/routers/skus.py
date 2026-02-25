"""
GET /skus/lookup-ean/{ean} — SKU lookup by EAN/GTIN barcode.
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import get_db, verify_token
from ..schemas import SKUResponse

router = APIRouter(tags=["skus"])


@router.get(
    "/skus/lookup-ean/{ean}",
    response_model=SKUResponse,
    summary="Cerca SKU per EAN",
    dependencies=[Depends(verify_token)],
)
def lookup_ean(
    ean: str,
    db: sqlite3.Connection = Depends(get_db),
) -> SKUResponse:
    """
    Cerca uno SKU tramite codice EAN-8, EAN-13 o GTIN-14.

    - EAN non valido (check digit errato o lunghezza sbagliata) → 422
    - EAN non trovato → 404
    - EAN malformato già presente nel DB (legacy) → restituito con ean_valid=False
    """
    # TODO: implementare EAN validation (check digit)
    # TODO: query su tabella skus WHERE ean = ?
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Endpoint non ancora implementato.",
    )
