"""
POST /receipts/close — chiude una ricezione merce scrivendo eventi RECEIPT nel ledger.

Idempotency key: receipt_id (fornito dal chiamante)
Atomica: se anche una riga fallisce validazione, nessuna riga viene scritta.
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import get_db, verify_token
from ..schemas import ReceiptsCloseRequest, ReceiptsCloseResponse

router = APIRouter(tags=["receipts"])


@router.post(
    "/receipts/close",
    response_model=ReceiptsCloseResponse,
    summary="Chiudi ricezione merce",
    dependencies=[Depends(verify_token)],
)
def close_receipt(
    body: ReceiptsCloseRequest,
    db: sqlite3.Connection = Depends(get_db),
) -> ReceiptsCloseResponse:
    """
    Registra gli eventi RECEIPT nel ledger per ogni riga dell'ordine ricevuto.

    - Se `receipt_id` è già presente in receiving_logs → 200 con already_processed=True.
    - Se è la prima elaborazione → 201 con already_processed=False.
    - Validazione per riga (SKU inesistente, qty < 1) raccolta in un unico 400
      se anche una sola riga è invalida (atomicità: nessuna riga scritta).
    """
    # TODO: controllare receipt_id in receiving_logs (idempotenza)
    # TODO: validare tutte le righe → raccogliere errori → 400 se presenti
    # TODO: INSERT transazioni RECEIPT in modo atomico (sqlite transaction)
    # TODO: INSERT in receiving_logs
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Endpoint non ancora implementato.",
    )
