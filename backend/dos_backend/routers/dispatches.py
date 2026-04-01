"""
Order Dispatch API — invia proposte d'ordine confermate al terminale Android.

Endpoints
---------
POST   /api/v1/order-dispatches         — crea nuovo dispatch (snapshot ordine)
GET    /api/v1/order-dispatches         — lista ultimi 10 dispatch (desc)
GET    /api/v1/order-dispatches/{id}    — dettaglio con linee
DELETE /api/v1/order-dispatches/{id}    — elimina un dispatch
DELETE /api/v1/order-dispatches         — elimina tutti i dispatch

Regole
------
- Ogni dispatch viene salvato come snapshot immutabile (header + linee).
- Al massimo 10 dispatch vengono restituiti dalla listagem (LIFO).
- Le linee con EAN non valido vengono accettate: il campo ean viene conservato
  as-is (validazione semantica a carico del cliente Android).
- La conferma dell'ordine non viene mai bloccata se il backend è offline
  (il pulsante desktop gestisce l'errore in modo non-fatale).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, status

from ..api.auth import verify_token
from ..api.deps import get_storage
from ..api.errors import NotFoundError
from ..schemas import (
    OrderDispatchCreateRequest,
    OrderDispatchDeleteResponse,
    OrderDispatchResponse,
    OrderDispatchSummary,
    OrderDispatchLineResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dispatches"])

_MAX_HISTORY = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dispatch_id() -> str:
    """Generate a unique, sortable dispatch ID."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid4().hex[:6]
    return f"DSP_{ts}_{suffix}"


def _build_summary(row: dict) -> OrderDispatchSummary:
    return OrderDispatchSummary(
        dispatch_id=row["dispatch_id"],
        sent_at=row["sent_at"],
        line_count=int(row.get("line_count", 0)),
        note=row.get("note", ""),
    )


def _build_line(row: dict) -> OrderDispatchLineResponse:
    return OrderDispatchLineResponse(
        sku=row["sku"],
        description=row.get("description", ""),
        qty_ordered=int(row.get("qty_ordered", 0)),
        ean=row.get("ean") or None,
        order_id=row.get("order_id", ""),
        receipt_date=row.get("receipt_date") or None,
    )


# ---------------------------------------------------------------------------
# POST /order-dispatches
# ---------------------------------------------------------------------------

@router.post(
    "/order-dispatches",
    status_code=status.HTTP_201_CREATED,
    response_model=OrderDispatchResponse,
    dependencies=[Depends(verify_token)],
)
def create_order_dispatch(
    body: OrderDispatchCreateRequest,
    storage=Depends(get_storage),
):
    dispatch_id = _make_dispatch_id()
    sent_at = datetime.now(timezone.utc).isoformat()

    storage.write_order_dispatch(
        dispatch_id=dispatch_id,
        sent_at=sent_at,
        line_count=len(body.lines),
        note=body.note,
    )

    line_dicts = [
        {
            "dispatch_id": dispatch_id,
            "sku": line.sku,
            "description": line.description,
            "qty_ordered": line.qty_ordered,
            "ean": line.ean or "",
            "order_id": line.order_id,
            "receipt_date": str(line.receipt_date) if line.receipt_date else "",
        }
        for line in body.lines
    ]
    storage.write_order_dispatch_lines_batch(line_dicts)

    logger.info("Created order dispatch %s with %d lines", dispatch_id, len(body.lines))

    return OrderDispatchResponse(
        dispatch_id=dispatch_id,
        sent_at=sent_at,
        line_count=len(body.lines),
        note=body.note,
        lines=[
            OrderDispatchLineResponse(
                sku=line.sku,
                description=line.description,
                qty_ordered=line.qty_ordered,
                ean=line.ean or None,
                order_id=line.order_id,
                receipt_date=line.receipt_date,
            )
            for line in body.lines
        ],
    )


# ---------------------------------------------------------------------------
# GET /order-dispatches
# ---------------------------------------------------------------------------

@router.get(
    "/order-dispatches",
    response_model=list[OrderDispatchSummary],
    dependencies=[Depends(verify_token)],
)
def list_order_dispatches(storage=Depends(get_storage)):
    """Return the last _MAX_HISTORY dispatches, newest first."""
    rows = storage.read_order_dispatches()  # already sorted desc by csv_layer
    return [_build_summary(r) for r in rows[:_MAX_HISTORY]]


# ---------------------------------------------------------------------------
# GET /order-dispatches/{dispatch_id}
# ---------------------------------------------------------------------------

@router.get(
    "/order-dispatches/{dispatch_id}",
    response_model=OrderDispatchResponse,
    dependencies=[Depends(verify_token)],
)
def get_order_dispatch(dispatch_id: str, storage=Depends(get_storage)):
    rows = storage.read_order_dispatches()
    header = next((r for r in rows if r["dispatch_id"] == dispatch_id), None)
    if header is None:
        raise NotFoundError(f"Dispatch {dispatch_id!r} not found")

    line_rows = storage.read_order_dispatch_lines(dispatch_id)
    return OrderDispatchResponse(
        **_build_summary(header).model_dump(),
        lines=[_build_line(lr) for lr in line_rows],
    )


# ---------------------------------------------------------------------------
# DELETE /order-dispatches/{dispatch_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/order-dispatches/{dispatch_id}",
    response_model=OrderDispatchDeleteResponse,
    dependencies=[Depends(verify_token)],
)
def delete_order_dispatch(dispatch_id: str, storage=Depends(get_storage)):
    deleted = storage.delete_order_dispatch(dispatch_id)
    if not deleted:
        raise NotFoundError(f"Dispatch {dispatch_id!r} not found")
    return OrderDispatchDeleteResponse(
        dispatch_id=dispatch_id,
        deleted=True,
        message=f"Dispatch {dispatch_id} deleted successfully",
    )


# ---------------------------------------------------------------------------
# DELETE /order-dispatches  (delete all)
# ---------------------------------------------------------------------------

@router.delete(
    "/order-dispatches",
    response_model=OrderDispatchDeleteResponse,
    dependencies=[Depends(verify_token)],
)
def delete_all_order_dispatches(storage=Depends(get_storage)):
    count = storage.delete_all_order_dispatches()
    return OrderDispatchDeleteResponse(
        dispatch_id="*",
        deleted=True,
        message=f"Deleted {count} dispatch(es)",
    )
