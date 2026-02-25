"""
POST /exceptions — registra evento di eccezione nel ledger (WASTE, ADJUST, UNFULFILLED).

Idempotency
-----------
Due livelli di protezione contro le scritture duplicate:

1. **client_event_id** (UUID opzionale nel payload)
   Consultato/registrato nella tabella ``api_idempotency_keys`` (SQLite).
   Se la stessa stringa arriva una seconda volta il server risponde 200 con
   ``already_recorded=true`` **senza toccare il ledger**.

2. **date + sku + event** (legacy fallback, senza client_event_id)
   Se la tripletta è già presente nel ledger → 409 Conflict.
   Si applica anche quando client_event_id è presente ma la chiave non è
   ancora nel registro (es. prima chiamata con un uuid già "usato" lato
   ledger da una richiesta precedente senza uuid).

Workflow
--------
1. Validate body (Pydantic + campo obbligatorio sku exists).
2. Se client_event_id → lookup idempotency table.
3. Se trovato → replay stored response con already_recorded=True (HTTP 200).
4. Controlla date+sku+event nel ledger → ConflictError(409) se già presente.
5. Scrive Transaction nel ledger via StorageAdapter.
6. Se client_event_id → record nella tabella idempotency.
7. Risponde 201 Created.
"""
from __future__ import annotations

import logging
from datetime import date as date_type

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from ..api.auth import verify_token
from ..api.deps import get_db, get_storage
from ..api.errors import ConflictError, NotFoundError
from ..api import idempotency
from ..domain.models import EventType, Transaction
from ..schemas import ExceptionRequest, ExceptionResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["exceptions"])

_ENDPOINT_LABEL = "POST /exceptions"

# Mapping from payload event string → domain EventType
_EVENT_MAP: dict[str, EventType] = {
    "WASTE": EventType.WASTE,
    "ADJUST": EventType.ADJUST,
    "UNFULFILLED": EventType.UNFULFILLED,
}


def _idempotency_key(event_date: date_type, sku: str, event: str) -> str:
    """Legacy date+sku+event idempotency key (used for ledger duplicate check)."""
    return f"{event_date.isoformat()}:{sku}:{event}"


@router.post(
    "/exceptions",
    response_model=ExceptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registra eccezione (WASTE / ADJUST / UNFULFILLED)",
    dependencies=[Depends(verify_token)],
    responses={
        200: {
            "description": "Già registrato — risposta replay (client_event_id duplicato)",
            "model": ExceptionResponse,
        },
        201: {"description": "Eccezione registrata con successo"},
        400: {"description": "Input non valido"},
        404: {"description": "SKU non trovato"},
        409: {"description": "Eccezione già presente (date+sku+event)"},
    },
)
def create_exception(
    body: ExceptionRequest,
    db=Depends(get_db),
    storage=Depends(get_storage),
) -> JSONResponse:
    """
    Scrive un evento WASTE, ADJUST o UNFULFILLED nel ledger.

    **Idempotenza con client_event_id**

    Inserisci nel payload un UUID univoco per ogni evento logico:

    ```json
    { "date": "2026-02-25", "sku": "PRD-0042", "event": "WASTE",
      "qty": 3, "client_event_id": "550e8400-e29b-41d4-a716-446655440000" }
    ```

    Se la stessa richiesta arriva due volte (es. retry di rete), il server
    risponde **200** con `already_recorded=true` — il ledger non viene
    toccato una seconda volta.

    **Senza client_event_id**: la tripletta `date+sku+event` viene usata come
    chiave di idempotenza legacy. Duplicato → **409 Conflict**.
    """
    # ------------------------------------------------------------------ #
    # 1. Ensure idempotency schema exists (no-op when migration 005 ran)  #
    # ------------------------------------------------------------------ #
    idempotency.ensure_schema(db)

    # ------------------------------------------------------------------ #
    # 2. client_event_id fast-path: replay if already processed           #
    # ------------------------------------------------------------------ #
    client_event_id: str | None = (body.client_event_id or "").strip() or None

    if client_event_id:
        stored = idempotency.lookup(db, client_event_id)
        if stored is not None:
            _, response_dict = stored
            # Mark replay: override already_recorded flag
            response_dict["already_recorded"] = True
            logger.info(
                "idempotency replay: client_event_id=%r endpoint=%s",
                client_event_id,
                _ENDPOINT_LABEL,
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content=response_dict)

    # ------------------------------------------------------------------ #
    # 3. Validate SKU exists                                              #
    # ------------------------------------------------------------------ #
    all_skus = storage.read_skus()
    sku_obj = next((s for s in all_skus if s.sku == body.sku), None)
    if sku_obj is None:
        raise NotFoundError(f"SKU '{body.sku}' non trovato nel database.")

    # ------------------------------------------------------------------ #
    # 4. Map event type (Pydantic already validated the literal value)    #
    # ------------------------------------------------------------------ #
    event_type: EventType = _EVENT_MAP[body.event]

    # ------------------------------------------------------------------ #
    # 5. Legacy idempotency: date+sku+event already in ledger?            #
    # ------------------------------------------------------------------ #
    idem_key = _idempotency_key(body.date, body.sku, body.event)
    existing_txns = storage.read_transactions()
    duplicate = any(
        t.date == body.date and t.sku == body.sku and t.event == event_type
        for t in existing_txns
    )
    if duplicate:
        raise ConflictError(
            f"Eccezione già registrata per la tripletta "
            f"date={body.date} / sku='{body.sku}' / event={body.event}. "
            f"Idempotency key: '{idem_key}'."
        )

    # ------------------------------------------------------------------ #
    # 6. Build and write transaction                                      #
    # ------------------------------------------------------------------ #
    # Compose note: prefix with idempotency key so the ledger is self-describing.
    full_note = f"{idem_key}"
    if body.note:
        full_note = f"{idem_key}; {body.note}"

    txn = Transaction(
        date=body.date,
        sku=body.sku,
        event=event_type,
        qty=body.qty,
        note=full_note,
    )
    storage.write_transaction(txn)
    logger.info(
        "exception recorded: date=%s sku=%s event=%s qty=%d client_event_id=%r",
        body.date,
        body.sku,
        body.event,
        body.qty,
        client_event_id,
    )

    # ------------------------------------------------------------------ #
    # 7. Assemble response                                                #
    # ------------------------------------------------------------------ #
    response_obj = ExceptionResponse(
        transaction_id=None,  # StorageAdapter.write_transaction() returns nothing
        date=body.date,
        sku=body.sku,
        event=body.event,
        qty=body.qty,
        note=full_note,
        idempotency_key=idem_key,
        already_recorded=False,
        client_event_id=client_event_id,
    )
    response_dict = response_obj.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # 8. Record in idempotency table (only when client provided a uuid)   #
    # ------------------------------------------------------------------ #
    if client_event_id:
        idempotency.record(
            conn=db,
            client_event_id=client_event_id,
            endpoint=_ENDPOINT_LABEL,
            status_code=status.HTTP_201_CREATED,
            response_data=response_dict,
        )

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=response_dict)

