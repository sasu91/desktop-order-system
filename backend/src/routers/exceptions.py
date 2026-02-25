"""
POST /exceptions — registra evento di eccezione nel ledger (WASTE, ADJUST, UNFULFILLED).

Idempotency key: date + sku + event
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from backend.src.dependencies import get_db, verify_token
from backend.src.schemas import ExceptionRequest, ExceptionResponse

router = APIRouter(tags=["exceptions"])


@router.post(
    "/exceptions",
    response_model=ExceptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registra eccezione (WASTE / ADJUST / UNFULFILLED)",
    dependencies=[Depends(verify_token)],
)
def create_exception(
    body: ExceptionRequest,
    db: sqlite3.Connection = Depends(get_db),
) -> ExceptionResponse:
    """
    Scrive un evento WASTE, ADJUST o UNFULFILLED nel ledger.

    - Idempotency key: `date + sku + event`
      Se la tripletta esiste già → 409 Conflict.
    - SKU inesistente → 404 Not Found.
    - Validazione fallisce → 400 Bad Request con dettagli per campo.
    """
    # TODO: verificare esistenza SKU
    # TODO: controllare idempotency key (SELECT from transactions)
    # TODO: INSERT into transactions + return transaction_id
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Endpoint non ancora implementato.",
    )
