"""
POST /eod/close — chiusura giornaliera multi-SKU (End-of-Day Batch).

Permette all'app mobile di inviare in un'unica richiesta, per più SKU,
i dati di fine giornata: giacenza fisica (on_hand), sprechi (waste_qty),
rettifiche manuali (adjust_qty) e domanda inevasa (unfulfilled_qty).

Ledger mapping (per SKU, nell'ordine):
  1. waste_qty      → WASTE  event
  2. unfulfilled_qty → UNFULFILLED event
  3. adjust_qty     → ADJUST event (rettifica manuale, assoluta)
  4. on_hand        → ADJUST event (conteggio fisico, assoluto; finalizza lo stato)

Gli eventi 3 e 4 producono entrambi ADJUST: on_hand viene scritto per ultimo
così che il conteggio fisico finale sovrascriva qualsiasi ADJUST precedente.

Atomicità
---------
Tutti gli SKU vengono validati PRIMA di scrivere qualsiasi evento.
Se uno SKU non è trovato nel catalogo, la risposta è 400 con la lista
degli errori e il ledger rimane invariato.

Idempotenza
-----------
Un solo livello: **client_eod_id** (UUID obbligatorio nel payload).
Consultato e registrato nella tabella ``api_idempotency_keys`` (SQLite).
Se la stessa stringa arriva una seconda volta il server risponde 200 con
``already_posted=true`` senza toccare il ledger.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from ..api.auth import verify_token
from ..api.deps import get_db, get_storage
from ..api.errors import BadRequestError
from ..api import idempotency
from ..domain.ledger import validate_ean
from ..domain.models import EventType, Transaction
from ..schemas import (
    EodCloseRequest,
    EodCloseResponse,
    EodEntryResult,
    ErrorDetail,
)
from ..utils.colli_utils import colli_to_pezzi

logger = logging.getLogger(__name__)

router = APIRouter(tags=["eod"])

_ENDPOINT_LABEL = "POST /eod/close"

# Ordered mapping: event type → string for response
_EVENT_LABEL: dict[EventType, str] = {
    EventType.WASTE:       "WASTE",
    EventType.ADJUST:      "ADJUST",
    EventType.UNFULFILLED: "UNFULFILLED",
}


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def _validate_entries(
    body: EodCloseRequest,
    sku_map: dict,
    ean_to_sku: dict,
) -> tuple[list[ErrorDetail], list[tuple[int, str]]]:
    """
    Validate all EOD entries and resolve EAN → SKU when needed.

    Returns:
        (errors, resolved)
        errors   — list of ErrorDetail (empty if all valid)
        resolved — list of (entry_index, resolved_sku_code)
    """
    errors: list[ErrorDetail] = []
    resolved: list[tuple[int, str]] = []

    for idx, entry in enumerate(body.entries):
        prefix = f"entries[{idx}]"
        sku_code: str | None = None

        raw_sku = (entry.sku or "").strip()
        raw_ean = (entry.ean or "").strip()

        # ---- Numeric field validation ------------------------------------
        # on_hand, adjust_qty, unfulfilled_qty are in COLLI (float >= 0)
        # waste_qty is in PEZZI (int >= 1)
        if entry.on_hand is not None and entry.on_hand < 0:
            errors.append(ErrorDetail(field=f"{prefix}.on_hand", issue="Deve essere >= 0 colli."))
        if entry.waste_qty is not None and entry.waste_qty < 1:
            errors.append(ErrorDetail(field=f"{prefix}.waste_qty", issue="Deve essere >= 1 pz."))
        if entry.adjust_qty is not None and entry.adjust_qty <= 0:
            errors.append(ErrorDetail(field=f"{prefix}.adjust_qty", issue="Deve essere > 0 colli."))
        if entry.unfulfilled_qty is not None and entry.unfulfilled_qty <= 0:
            errors.append(ErrorDetail(field=f"{prefix}.unfulfilled_qty", issue="Deve essere > 0 colli."))

        if raw_sku:
            if raw_sku not in sku_map:
                errors.append(ErrorDetail(
                    field=f"{prefix}.sku",
                    issue=f"SKU '{raw_sku}' non trovato nel catalogo.",
                ))
            else:
                sku_code = raw_sku
        elif raw_ean:
            ok, err_msg = validate_ean(raw_ean)
            if not ok:
                errors.append(ErrorDetail(
                    field=f"{prefix}.ean",
                    issue=f"EAN non valido: {err_msg}",
                ))
            else:
                sku_code = ean_to_sku.get(raw_ean)
                if sku_code is None:
                    errors.append(ErrorDetail(
                        field=f"{prefix}.ean",
                        issue=f"Nessuno SKU trovato con EAN '{raw_ean}'.",
                    ))
        else:
            errors.append(ErrorDetail(
                field=f"{prefix}.sku",
                issue="Almeno uno tra 'sku' e 'ean' deve essere fornito.",
            ))

        if sku_code is not None:
            resolved.append((idx, sku_code))

    return errors, resolved


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post(
    "/eod/close",
    response_model=EodCloseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Chiusura giornaliera multi-SKU (EOD batch)",
    dependencies=[Depends(verify_token)],
    responses={
        200: {
            "description": "Già processato (already_posted=true).",
            "model": EodCloseResponse,
        },
        201: {"description": "Chiusura EOD registrata con successo."},
        400: {"description": "Errori di validazione — nessun evento scritto."},
    },
)
def close_eod(
    body: EodCloseRequest,
    db=Depends(get_db),
    storage=Depends(get_storage),
) -> JSONResponse:
    """
    Registra gli eventi EOD nel ledger per ogni SKU con almeno un campo valorizzato.

    **Campi opzionali per SKU** (tutti nullable):
    - `on_hand` (≥ 0)        → ADJUST event (conteggio fisico di fine giornata)
    - `waste_qty` (≥ 1)       → WASTE event
    - `adjust_qty` (≥ 1)      → ADJUST event (rettifica manuale)
    - `unfulfilled_qty` (≥ 1) → UNFULFILLED event

    **Atomicità**: se anche un solo SKU non è trovato nel catalogo, **nessun**
    evento viene scritto e la risposta 400 elenca tutti gli errori.

    **Idempotenza**: `client_eod_id` (UUID, obbligatorio) — duplicato → 200
    `already_posted=true`, ledger invariato.

    **Ordine scrittura per SKU**: WASTE → UNFULFILLED → ADJUST (adjust_qty) →
    ADJUST (on_hand). Il conteggio fisico è sempre lo stato finale.
    """
    # ------------------------------------------------------------------ #
    # 1. Ensure idempotency schema                                        #
    # ------------------------------------------------------------------ #
    idempotency.ensure_schema(db)

    # ------------------------------------------------------------------ #
    # 2. Fast-path: client_eod_id dedup                                   #
    # ------------------------------------------------------------------ #
    client_eod_id: str = body.client_eod_id.strip()

    _claimed_slot = False

    stored = idempotency.lookup(db, client_eod_id)
    if stored is not None:
        _, response_dict = stored
        response_dict["already_posted"] = True
        logger.info(
            "idempotency replay: client_eod_id=%r endpoint=%s",
            client_eod_id,
            _ENDPOINT_LABEL,
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=response_dict)

    if idempotency.try_claim(db, client_eod_id, _ENDPOINT_LABEL):
        _claimed_slot = True
    else:
        stored = idempotency.lookup_with_wait(db, client_eod_id)
        if stored is not None:
            stored[1]["already_posted"] = True
            logger.info(
                "idempotency concurrent replay: client_eod_id=%r endpoint=%s",
                client_eod_id,
                _ENDPOINT_LABEL,
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content=stored[1])
        _claimed_slot = True
        logger.warning(
            "idempotency: lookup_with_wait timed out for %r; processing as fresh",
            client_eod_id,
        )

    # ------------------------------------------------------------------ #
    # 3. Build SKU lookup indices                                          #
    # ------------------------------------------------------------------ #
    all_skus = storage.read_skus()
    sku_map: dict = {s.sku: s for s in all_skus}
    ean_to_sku: dict = {
        s.ean: s.sku
        for s in all_skus
        if s.ean and s.ean.strip()
    }

    # ------------------------------------------------------------------ #
    # 4. Validate ALL entries atomically                                   #
    # ------------------------------------------------------------------ #
    errors, resolved = _validate_entries(body, sku_map, ean_to_sku)

    if errors:
        raise BadRequestError(
            message="Errori di validazione EOD — nessun evento scritto.",
            details=errors,
        )

    # ------------------------------------------------------------------ #
    # 5. Write events (all-or-nothing: validated above)                   #
    # ------------------------------------------------------------------ #
    results: list[EodEntryResult] = []

    for idx, sku_code in resolved:
        entry = body.entries[idx]
        note = entry.note.strip()
        events_written: list[str] = []

        # Get pack_size for colli->pezzi conversion (on_hand, adjust_qty, unfulfilled_qty)
        sku_obj = sku_map[sku_code]
        pack_size: int = getattr(sku_obj, "pack_size", 1) or 1

        # ── WASTE ─────────────────────────────────────────────────────
        if entry.waste_qty is not None and entry.waste_qty > 0:
            storage.write_transaction(Transaction(
                date=body.date,
                sku=sku_code,
                event=EventType.WASTE,
                qty=entry.waste_qty,
                note=f"[EOD] {note}" if note else "[EOD]",
            ))
            events_written.append("WASTE")

        # ── UNFULFILLED ───────────────────────────────────────────────
        # unfulfilled_qty is in COLLI -> convert to pezzi
        if entry.unfulfilled_qty is not None and entry.unfulfilled_qty > 0:
            unfulfilled_pezzi = colli_to_pezzi(entry.unfulfilled_qty, pack_size)
            if unfulfilled_pezzi > 0:
                storage.write_transaction(Transaction(
                    date=body.date,
                    sku=sku_code,
                    event=EventType.UNFULFILLED,
                    qty=unfulfilled_pezzi,
                    note=f"[EOD] {note}" if note else "[EOD]",
                ))
                events_written.append("UNFULFILLED")

        # ── ADJUST (manual correction, written before on_hand) ────────
        # adjust_qty is in COLLI -> convert to pezzi
        if entry.adjust_qty is not None and entry.adjust_qty > 0:
            adjust_pezzi = colli_to_pezzi(entry.adjust_qty, pack_size)
            if adjust_pezzi > 0:
                storage.write_transaction(Transaction(
                    date=body.date,
                    sku=sku_code,
                    event=EventType.ADJUST,
                    qty=adjust_pezzi,
                    note=f"[EOD-ADJUST] {note}" if note else "[EOD-ADJUST]",
                ))
                events_written.append("ADJUST")

        # ── on_hand → ADJUST (physical count, always last) ────────────
        # on_hand is in COLLI -> convert to pezzi
        if entry.on_hand is not None:
            on_hand_pezzi = colli_to_pezzi(entry.on_hand, pack_size)
            storage.write_transaction(Transaction(
                date=body.date,
                sku=sku_code,
                event=EventType.ADJUST,
                qty=on_hand_pezzi,
                note=f"[EOD-ON_HAND] {note}" if note else "[EOD-ON_HAND]",
            ))
            events_written.append("ADJUST:ON_HAND")

        noop = len(events_written) == 0
        results.append(EodEntryResult(
            sku=sku_code,
            events_written=events_written,
            noop=noop,
        ))
        if events_written:
            logger.info(
                "eod events written: date=%s sku=%s events=%s",
                body.date,
                sku_code,
                events_written,
            )
        else:
            logger.debug(
                "eod noop: date=%s sku=%s (all fields null/zero)",
                body.date,
                sku_code,
            )

    # ------------------------------------------------------------------ #
    # 6. Assemble response                                                 #
    # ------------------------------------------------------------------ #
    response_obj = EodCloseResponse(
        date=body.date,
        client_eod_id=client_eod_id,
        already_posted=False,
        total_entries=len(body.entries),
        results=results,
    )
    response_dict = response_obj.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # 7. Finalize idempotency slot                                         #
    # ------------------------------------------------------------------ #
    if _claimed_slot:
        idempotency.finalize(
            conn=db,
            client_event_id=client_eod_id,
            status_code=status.HTTP_201_CREATED,
            response_data=response_dict,
        )

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=response_dict)
