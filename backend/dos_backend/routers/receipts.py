"""
POST /receipts/close — chiude una ricezione merce scrivendo eventi RECEIPT nel ledger.

Atomicità
---------
Tutte le righe vengono validate **prima** di scrivere qualsiasi cosa.
Se anche una sola riga fallisce, vengono restituiti gli errori per riga
(con indice + campo + motivo) e il ledger rimane invariato.

Idempotenza (due livelli)
-------------------------
1. **client_receipt_id** (UUID opzionale nel payload)
   Chave nella tabella ``api_idempotency_keys``.  Se già presente → 200
   con ``already_posted=true`` e risposta esatta del primo tentativo.
   Non viene toccato il ledger.

2. **receipt_id** (chiave legacy nei receiving_logs)
   Se ``receipt_id`` è già nei receiving_logs (es. scrittura fatta da un
   client senza UUID, o dall'app desktop) → 200 ``already_posted=true``
   con risposta ricostruita dinamenicamente dal payload corrente.

Validazione per riga
--------------------
Per ogni ``lines[i]``:
- ``sku`` o ``ean`` deve essere presente (almeno uno).
- Se viene fornito ``ean``, viene validato il formato (EAN-12/13, solo cifre)
  e risolto in SKU tramite lookup nel catalogo.
- Se viene fornito ``sku``, deve esistere nel catalogo.
- ``qty_received >= 0`` (0 = nessun articolo fisicamente ricevuto).
- Se lo SKU ha ``has_expiry_label=True``, ``expiry_date`` è obbligatoria.

Tutti gli errori vengono raccolti in un unico 400 prima di scrivere nulla.
"""
from __future__ import annotations

import logging
from datetime import date as date_type

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from ..api.auth import verify_token
from ..api.deps import get_db, get_storage
from ..api.errors import BadRequestError
from ..api import idempotency
from ..domain.ledger import validate_ean
from ..domain.models import EventType, Transaction
from ..schemas import (
    ErrorDetail,
    ReceiptLineResult,
    ReceiptsCloseRequest,
    ReceiptsCloseResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["receipts"])

_ENDPOINT_LABEL = "POST /receipts/close"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_lines(
    body: ReceiptsCloseRequest,
    sku_map: dict,        # {sku_code: SKU}
    ean_to_sku: dict,     # {ean: sku_code}
) -> tuple[list[ErrorDetail], list[tuple[int, str, object]]]:
    """
    Validate all receipt lines.

    Returns:
        (errors, resolved)
        errors   — list of ErrorDetail (empty if all valid)
        resolved — list of (line_index, resolved_sku_code, sku_obj)
                   parallel to body.lines, only populated when errors=[].
    """
    errors: list[ErrorDetail] = []
    resolved: list[tuple[int, str, object]] = []

    for idx, line in enumerate(body.lines):
        prefix = f"lines[{idx}]"
        sku_code: str | None = None
        sku_obj = None

        # ---- 1. Resolve SKU ------------------------------------------------
        raw_sku = (line.sku or "").strip()
        raw_ean = (line.ean or "").strip()

        if raw_sku:
            # sku takes priority — canonical format already enforced by schema validator,
            # but guard here too in case the model is called without Pydantic (e.g. tests).
            import re as _re
            if not _re.fullmatch(r'\d{7}', raw_sku):
                errors.append(ErrorDetail(
                    field=f"{prefix}.sku",
                    issue=(
                        f"SKU non canonico (atteso stringa di esattamente 7 cifre numeriche): "
                        f"ricevuto {raw_sku!r}"
                    ),
                ))
                continue
            sku_code = raw_sku
            sku_obj = sku_map.get(sku_code)
            if sku_obj is None:
                errors.append(ErrorDetail(
                    field=f"{prefix}.sku",
                    issue=f"SKU '{sku_code}' non trovato nel catalogo.",
                ))
        elif raw_ean:
            # EAN-based lookup
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
                    sku_obj = sku_map[sku_code]
        else:
            errors.append(ErrorDetail(
                field=f"{prefix}.sku",
                issue="Almeno uno tra 'sku' e 'ean' deve essere fornito.",
            ))

        # ---- 2. Expiry date (only when SKU resolved) -----------------------
        if sku_obj is not None and getattr(sku_obj, "has_expiry_label", False):
            if line.expiry_date is None:
                errors.append(ErrorDetail(
                    field=f"{prefix}.expiry_date",
                    issue=(
                        f"Lo SKU '{sku_code}' richiede la data di scadenza "
                        f"(has_expiry_label=true) ma expiry_date non è stata fornita."
                    ),
                ))

        # ---- 3. qty_received is already enforced >=0 by Pydantic -----------
        #         No additional check needed here.

        # Accumulate resolved pairs only when there are no errors for this line
        if sku_code and sku_obj is not None:
            resolved.append((idx, sku_code, sku_obj))

    return errors, resolved


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post(
    "/receipts/close",
    response_model=ReceiptsCloseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Chiudi ricezione merce",
    dependencies=[Depends(verify_token)],
    responses={
        200: {
            "description": "Già processato (already_posted=true).",
            "model": ReceiptsCloseResponse,
        },
        201: {"description": "Ricezione registrata con successo."},
        400: {"description": "Errori di validazione per riga — nessuna riga scritta."},
    },
)
def close_receipt(
    body: ReceiptsCloseRequest,
    db=Depends(get_db),
    storage=Depends(get_storage),
) -> JSONResponse:
    """
    Registra gli eventi RECEIPT nel ledger per ogni riga ricevuta.

    **Atomicità**: se anche una sola riga fallisce validazione, **nessuna**
    riga viene scritta e la risposta 400 elenca tutti gli errori con indice
    riga, campo e motivo.

    **Idempotenza**:
    - ``client_receipt_id`` (UUID) → lookup in ``api_idempotency_keys``.
      Duplicato → 200 ``already_posted=true``, ledger invariato.
    - ``receipt_id`` (legacy) → lookup in receiving_logs.
      Già presente → 200 ``already_posted=true``.

    **Risoluzione SKU**:
    - ``sku`` esplicito → ricerca diretta.
    - ``ean`` → validazione formato + lookup catalogo → sku.
    - Se lo SKU ha ``has_expiry_label=true`` → ``expiry_date`` obbligatoria.
    """
    # ---------------------------------------------------------------------- #
    # 1. Ensure idempotency schema                                            #
    # ---------------------------------------------------------------------- #
    idempotency.ensure_schema(db)

    # ---------------------------------------------------------------------- #
    # 2. Fast-path: client_receipt_id dedup                                   #
    # ---------------------------------------------------------------------- #
    client_receipt_id: str | None = (body.client_receipt_id or "").strip() or None

    # Track whether this request owns the idempotency slot.
    _claimed_slot: bool = False

    if client_receipt_id:
        stored = idempotency.lookup(db, client_receipt_id)
        if stored is not None:
            _, response_dict = stored
            response_dict["already_posted"] = True
            logger.info(
                "idempotency replay: client_receipt_id=%r endpoint=%s",
                client_receipt_id,
                _ENDPOINT_LABEL,
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content=response_dict)

        if idempotency.try_claim(db, client_receipt_id, _ENDPOINT_LABEL):
            _claimed_slot = True
        else:
            stored = idempotency.lookup_with_wait(db, client_receipt_id)
            if stored is not None:
                stored[1]["already_posted"] = True
                logger.info(
                    "idempotency concurrent replay: client_receipt_id=%r endpoint=%s",
                    client_receipt_id,
                    _ENDPOINT_LABEL,
                )
                return JSONResponse(
                    status_code=status.HTTP_200_OK, content=stored[1]
                )
            _claimed_slot = True
            logger.warning(
                "idempotency: lookup_with_wait timed out for %r; processing as fresh",
                client_receipt_id,
            )

    # ---------------------------------------------------------------------- #
    # 3. Legacy idempotency: receipt_id already in receiving_logs?           #
    # ---------------------------------------------------------------------- #
    existing_logs = storage.read_receiving_logs()
    if any(log.get("receipt_id") == body.receipt_id for log in existing_logs):
        logger.info(
            "receipt already processed (legacy receipt_id=%r)", body.receipt_id
        )
        # Reconstruct a synthetic response from the current request payload.
        synthetic_lines = [
            ReceiptLineResult(
                line_index=i,
                sku=(line.sku or "").strip() or (line.ean or ""),
                ean=(line.ean or "").strip() or None,
                qty_received=line.qty_received,
                expiry_date=line.expiry_date,
                status="already_received",
            )
            for i, line in enumerate(body.lines)
        ]
        resp = ReceiptsCloseResponse(
            receipt_id=body.receipt_id,
            receipt_date=body.receipt_date,
            already_posted=True,
            client_receipt_id=client_receipt_id,
            lines=synthetic_lines,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=resp.model_dump(mode="json"),
        )

    # ---------------------------------------------------------------------- #
    # 4. Build lookup indices                                                 #
    # ---------------------------------------------------------------------- #
    all_skus = storage.read_skus()
    sku_map: dict = {s.sku: s for s in all_skus}
    ean_to_sku: dict = {
        s.ean: s.sku
        for s in all_skus
        if s.ean and s.ean.strip()
    }

    # ---------------------------------------------------------------------- #
    # 5. Validate ALL lines (collect errors atomically)                       #
    # ---------------------------------------------------------------------- #
    errors, resolved = _validate_lines(body, sku_map, ean_to_sku)

    if errors:
        raise BadRequestError(
            message=(
                f"{len(errors)} errore/i di validazione — "
                "nessuna riga è stata scritta nel ledger."
            ),
            details=errors,
        )

    # ---------------------------------------------------------------------- #
    # 6. Build Transaction objects (only for qty_received > 0)               #
    # ---------------------------------------------------------------------- #
    txns_to_write: list[Transaction] = []
    result_lines: list[ReceiptLineResult] = []

    for idx, sku_code, sku_obj in resolved:
        line = body.lines[idx]
        raw_ean = (line.ean or "").strip() or None

        note_parts = [f"receipt_id={body.receipt_id}"]
        if line.note:
            note_parts.append(line.note)
        if line.expiry_date:
            note_parts.append(f"expiry={line.expiry_date.isoformat()}")
        note = "; ".join(note_parts)

        if line.qty_received > 0:
            txns_to_write.append(
                Transaction(
                    date=body.receipt_date,
                    sku=sku_code,
                    event=EventType.RECEIPT,
                    qty=line.qty_received,
                    receipt_date=body.receipt_date,
                    note=note,
                )
            )
            line_status = "ok"
        else:
            # qty_received == 0: acknowledged, no RECEIPT event
            line_status = "skipped"
            logger.info(
                "receipt line skipped (qty=0): receipt_id=%r sku=%s idx=%d",
                body.receipt_id,
                sku_code,
                idx,
            )

        result_lines.append(
            ReceiptLineResult(
                line_index=idx,
                sku=sku_code,
                ean=raw_ean,
                qty_received=line.qty_received,
                expiry_date=line.expiry_date,
                status=line_status,
            )
        )

    # ---------------------------------------------------------------------- #
    # 7. Write — single atomic batch                                          #
    # ---------------------------------------------------------------------- #
    if txns_to_write:
        storage.write_transactions_batch(txns_to_write)
        logger.info(
            "receipt closed: receipt_id=%r lines=%d txns=%d",
            body.receipt_id,
            len(body.lines),
            len(txns_to_write),
        )

    # Write receiving_log entries for all lines (even qty=0) so receipt_id
    # idempotency check covers the full document next time.
    today_str = date_type.today().isoformat()
    for idx, sku_code, _ in resolved:
        line = body.lines[idx]
        storage.write_receiving_log(
            document_id=body.receipt_id,
            receipt_id=body.receipt_id,
            date_str=today_str,
            sku=sku_code,
            qty=line.qty_received,
            receipt_date=body.receipt_date.isoformat(),
        )

    # ---------------------------------------------------------------------- #
    # 8. Assemble response and record idempotency                             #
    # ---------------------------------------------------------------------- #
    response_obj = ReceiptsCloseResponse(
        receipt_id=body.receipt_id,
        receipt_date=body.receipt_date,
        already_posted=False,
        client_receipt_id=client_receipt_id,
        lines=result_lines,
    )
    response_dict = response_obj.model_dump(mode="json")

    if _claimed_slot:
        idempotency.finalize(
            conn=db,
            client_event_id=client_receipt_id,  # type: ignore[arg-type]
            status_code=status.HTTP_201_CREATED,
            response_data=response_dict,
        )

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=response_dict)

