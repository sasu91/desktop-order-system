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

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from ..api.auth import verify_token
from ..api.deps import get_db, get_storage
from ..api.errors import NotFoundError
from ..api import idempotency
from ..domain.models import EventType, Transaction
from ..schemas import (
    ExceptionRequest,
    ExceptionResponse,
    DailyUpsertRequest,
    DailyUpsertResponse,
)

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

    # Track whether THIS request owns the idempotency slot and must finalize.
    _claimed_slot: bool = False

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

        # Atomically claim the idempotency slot BEFORE touching the ledger.
        # This eliminates the TOCTOU race in the lookup → write window.
        if idempotency.try_claim(db, client_event_id, _ENDPOINT_LABEL):
            _claimed_slot = True
        else:
            # Lost the INSERT race — wait for the winner to finalize the row.
            stored = idempotency.lookup_with_wait(db, client_event_id)
            if stored is not None:
                stored[1]["already_recorded"] = True
                logger.info(
                    "idempotency concurrent replay: client_event_id=%r endpoint=%s",
                    client_event_id,
                    _ENDPOINT_LABEL,
                )
                return JSONResponse(
                    status_code=status.HTTP_200_OK, content=stored[1]
                )
            # lookup_with_wait timed out (edge case): fall through and process
            # as if we own the slot to avoid silently dropping the request.
            _claimed_slot = True
            logger.warning(
                "idempotency: lookup_with_wait timed out for %r; processing as fresh",
                client_event_id,
            )

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
    if _claimed_slot:
        idempotency.finalize(
            conn=db,
            client_event_id=client_event_id,  # type: ignore[arg-type]  # always str if _claimed_slot
            status_code=status.HTTP_201_CREATED,
            response_data=response_dict,
        )

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=response_dict)


# ===========================================================================
# POST /exceptions/daily-upsert
# ===========================================================================
# Contrast with POST /exceptions:
#
#   /exceptions           — always APPENDs a new ledger event; two calls on the
#                           same day both land in the ledger (intentional for
#                           multiple spoilage events, spot adjustments, …).
#
#   /exceptions/daily-upsert — manages a SINGLE logical total per (sku, date,
#                           event).  Modes:
#
#     "replace" (default) Idempotent: set total to exactly qty.
#                         If current == qty → no-op (noop=true).
#                         Otherwise replaces all existing rows for the
#                         triplet with a single corrected row.
#                         Designed for ERP/POS end-of-day imports.
#
#     "sum"               Appends a delta row, returns new running total.
#                         Useful for incremental event streams that must not
#                         lose individual write timestamps.
#
# This endpoint carries NO idempotency-key mechanism; "replace" mode is
# inherently idempotent (same qty → noop), and "sum" mode is additive by
# design so duplicate-detection would need business-level deduplication.
# ===========================================================================

_UPSERT_ENDPOINT_LABEL = "POST /exceptions/daily-upsert"


@router.post(
    "/exceptions/daily-upsert",
    response_model=DailyUpsertResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert totale giornaliero (WASTE / ADJUST / UNFULFILLED)",
    dependencies=[Depends(verify_token)],
    responses={
        200: {"description": "Upsert eseguito (o noop se totale già corretto)"},
        400: {"description": "Input non valido"},
        404: {"description": "SKU non trovato"},
    },
)
def daily_upsert_exception(
    body: DailyUpsertRequest,
    storage=Depends(get_storage),
) -> JSONResponse:
    """
    Mantieni un **unico totale giornaliero** per la tripletta `(sku, date, event)`.

    ## Modalità

    | `mode`      | Semantica |
    |-------------|--------------------------------------------|
    | `"replace"` | Imposta il totale a esattamente `qty`. Idempotente: se il totale corrente è già `qty`, risponde `noop=true` senza toccare il ledger. |
    | `"sum"`     | Aggiunge `qty` come delta al totale corrente. Ritorna il nuovo totale. |

    ## Differenza con `POST /exceptions`

    `POST /exceptions` **aggiunge sempre** una nuova riga al ledger;
    due chiamate nella stessa giornata producono due eventi separati
    (corretto per scarti multipli, movimenti distinti, ecc.).

    `POST /exceptions/daily-upsert` è progettato per flussi che inviano il
    **totale cumulativo** della giornata (es. integrazione ERP, fine giornata
    POS) dove duplicare le righe produrrebbe un doppio conteggio.
    """
    # ------------------------------------------------------------------ #
    # 1. Validate SKU                                                     #
    # ------------------------------------------------------------------ #
    all_skus = storage.read_skus()
    if not any(s.sku == body.sku for s in all_skus):
        raise NotFoundError(f"SKU '{body.sku}' non trovato nel database.")

    # ------------------------------------------------------------------ #
    # 2. Read current state for this triplet                              #
    # ------------------------------------------------------------------ #
    event_type: EventType = _EVENT_MAP[body.event]
    all_txns = storage.read_transactions()

    def _matches(t: Transaction) -> bool:
        return t.sku == body.sku and t.date == body.date and t.event == event_type

    current_qty: int = sum(t.qty for t in all_txns if _matches(t))

    # ------------------------------------------------------------------ #
    # 3. Apply mode logic                                                 #
    # ------------------------------------------------------------------ #
    note = body.note

    if body.mode == "replace":
        if current_qty == body.qty:
            # Already at the requested total — no ledger write needed.
            logger.info(
                "daily-upsert noop: sku=%s date=%s event=%s qty=%d",
                body.sku, body.date, body.event, body.qty,
            )
            resp = DailyUpsertResponse(
                date=body.date, sku=body.sku, event=body.event,
                mode="replace", qty_delta=0, qty_total=current_qty,
                note=note, noop=True,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=resp.model_dump(mode="json"),
            )

        # Build a new ledger list: keep all non-matching rows, add one corrected row.
        non_matching = [t for t in all_txns if not _matches(t)]
        new_txn = Transaction(
            date=body.date, sku=body.sku, event=event_type,
            qty=body.qty, note=note,
        )
        storage.overwrite_transactions(non_matching + [new_txn])
        qty_delta = body.qty - current_qty
        qty_total = body.qty
        logger.info(
            "daily-upsert replace: sku=%s date=%s event=%s old=%d new=%d delta=%+d",
            body.sku, body.date, body.event, current_qty, body.qty, qty_delta,
        )

    else:  # sum
        txn = Transaction(
            date=body.date, sku=body.sku, event=event_type,
            qty=body.qty, note=note,
        )
        storage.write_transaction(txn)
        qty_delta = body.qty
        qty_total = current_qty + body.qty
        logger.info(
            "daily-upsert sum: sku=%s date=%s event=%s delta=%d total=%d",
            body.sku, body.date, body.event, qty_delta, qty_total,
        )

    # ------------------------------------------------------------------ #
    # 4. Respond                                                          #
    # ------------------------------------------------------------------ #
    resp = DailyUpsertResponse(
        date=body.date, sku=body.sku, event=body.event,
        mode=body.mode, qty_delta=qty_delta, qty_total=qty_total,
        note=note, noop=False,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=resp.model_dump(mode="json"),
    )

