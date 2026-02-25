"""
POST /exceptions — registra evento di eccezione nel ledger (WASTE, ADJUST, UNFULFILLED).

Idempotency
-----------
Un solo livello di protezione contro le scritture duplicate:

**client_event_id** (UUID opzionale nel payload)
   Consultato/registrato nella tabella ``api_idempotency_keys`` (SQLite).
   Se la stessa stringa arriva una seconda volta il server risponde 200 con
   ``already_recorded=true`` **senza toccare il ledger**.

Se ``client_event_id`` non è fornito, ogni richiesta viene accettata
indipendentemente — più eventi WASTE/ADJUST/UNFULFILLED sullo stesso
SKU nello stesso giorno sono legittimi (es. due scarti separati).

Workflow
--------
1. Validate body (Pydantic + campo obbligatorio sku exists).
2. Se client_event_id → lookup idempotency table.
3. Se trovato → replay stored response con already_recorded=True (HTTP 200).
4. Scrive Transaction nel ledger via StorageAdapter.
5. Se client_event_id → record nella tabella idempotency.
6. Risponde 201 Created.
"""
from __future__ import annotations

import logging
from datetime import date as date_type

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from ..api.auth import verify_token
from ..api.deps import get_db, get_storage
from ..api.errors import NotFoundError
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

    **Senza client_event_id**: ogni chiamata viene accettata — non si usa
    la tripletta ``date+sku+event`` come chiave legacy (niente 409).
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
    # 5. Build and write transaction                                      #
    # ------------------------------------------------------------------ #
    full_note = body.note

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
    # 6. Assemble response                                                #
    # ------------------------------------------------------------------ #
    response_obj = ExceptionResponse(
        transaction_id=None,  # StorageAdapter.write_transaction() returns nothing
        date=body.date,
        sku=body.sku,
        event=body.event,
        qty=body.qty,
        note=full_note,
        idempotency_key=client_event_id,
        already_recorded=False,
        client_event_id=client_event_id,
    )
    response_dict = response_obj.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # 7. Record in idempotency table (only when client provided a uuid)   #
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

