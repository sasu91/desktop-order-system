"""
GET /skus/by-ean/{ean} — SKU lookup by EAN/GTIN barcode.

Errors
------
400 BAD_REQUEST   EAN contains non-digit characters or has invalid length.
404 NOT_FOUND     EAN format is valid but no SKU with that code exists.
"""
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from ..api.auth import verify_token
from ..api import idempotency
from ..api.deps import get_db, get_storage
from ..api.errors import BadRequestError, ConflictError, NotFoundError
from ..domain.ledger import StockCalculator, normalize_ean_13, validate_ean
from ..domain.models import SKU
from ..schemas import (
    AddArticleRequest,
    AddArticleResponse,
    BindSecondaryEanRequest,
    BindSecondaryEanResponse,
    SKUResponse,
    ScannerPreloadItem,
    SkuSearchResponse,
    SkuSearchResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skus"])

_CREATE_ENDPOINT_LABEL = "POST /skus"


# ---------------------------------------------------------------------------
# GET /skus/scanner-preload
# ---------------------------------------------------------------------------

@router.get(
    "/skus/scanner-preload",
    response_model=list[ScannerPreloadItem],
    summary="Pre-carica catalogo barcode per scanner offline",
    dependencies=[Depends(verify_token)],
)
def get_scanner_preload(
    storage=Depends(get_storage),
) -> list[ScannerPreloadItem]:
    """
    Restituisce tutti gli SKU *in assortimento* con barcode e stock corrente
    (END_OF_DAY di oggi).

    Se uno SKU ha sia EAN primario che EAN secondario, vengono emesse due righe
    con lo stesso sku/stock ma EAN diverso — l'app li inserisce come alias
    separati nella cache Room.

    Usato dall'app Android per pre-popolare la cache offline prima della prima
    scansione, senza richiedere connessione al momento della scansione.
    """
    today = date.today()
    # END_OF_DAY: include events of today → effective = today + 1
    effective = today + timedelta(days=1)

    all_skus = [s for s in storage.read_skus() if s.in_assortment]

    transactions = storage.read_transactions()
    sales_records = storage.read_sales() if hasattr(storage, "read_sales") else []

    sku_ids = [s.sku for s in all_skus]
    stock_map = StockCalculator.calculate_all_skus(
        sku_ids, effective, transactions, sales_records
    )

    result: list[ScannerPreloadItem] = []
    for sku_obj in all_skus:
        primary = normalize_ean_13((sku_obj.ean or "").strip())
        secondary = normalize_ean_13((sku_obj.ean_secondary or "").strip())

        if not primary and not secondary:
            continue  # SKU senza nessun barcode — salta

        stock = stock_map.get(sku_obj.sku)
        on_hand = stock.on_hand if stock else 0
        on_order = stock.on_order if stock else 0

        expiry_flag = getattr(sku_obj, "has_expiry_label", False) or False

        if primary:
            result.append(ScannerPreloadItem(
                ean=primary,
                sku=sku_obj.sku,
                description=sku_obj.description,
                pack_size=getattr(sku_obj, "pack_size", 1) or 1,
                on_hand=on_hand,
                on_order=on_order,
                has_expiry_label=expiry_flag,
            ))

        if secondary and secondary != primary:
            result.append(ScannerPreloadItem(
                ean=secondary,
                sku=sku_obj.sku,
                description=sku_obj.description,
                pack_size=getattr(sku_obj, "pack_size", 1) or 1,
                on_hand=on_hand,
                on_order=on_order,
                has_expiry_label=expiry_flag,
            ))

    return result


@router.get(
    "/skus/by-ean/{ean}",
    response_model=SKUResponse,
    summary="Cerca SKU per EAN",
    dependencies=[Depends(verify_token)],
)
def get_sku_by_ean(
    ean: str,
    storage=Depends(get_storage),
) -> SKUResponse:
    """
    Cerca uno SKU tramite codice EAN-12 o EAN-13.

    - EAN non valido (caratteri non numerici o lunghezza diversa da 12/13 cifre) → **400**
    - EAN valido ma non presente nel catalogo → **404**
    - EAN valido e trovato, ma il valore nel DB ha formato irregolare → **200**
      con `ean_valid: false` (dato legacy accettato senza crash)
    """
    ean = ean.strip()

    # --- 1. Validate EAN format (digits only, length 8/12/13) ---
    is_valid, err_msg = validate_ean(ean)
    if not is_valid:
        raise BadRequestError(
            f"EAN non valido: {err_msg}",
        )
    if not ean:
        # validate_ean treats empty as valid; we reject it here as a path param
        raise BadRequestError("EAN non può essere vuoto.")

    # --- 2. Lookup: linear scan over all SKUs (no EAN index yet) ---
    # Normalise both sides to canonical 13-digit form before comparison.
    # This handles the common case where the stored EAN is 12 digits but ML Kit
    # returns 13 digits (barcode encodes check digit not stored in the CSV).
    ean_canonical = normalize_ean_13(ean)
    skus = storage.read_skus()
    hit = next(
        (s for s in skus
         if normalize_ean_13((s.ean or "").strip()) == ean_canonical
         or normalize_ean_13((s.ean_secondary or "").strip()) == ean_canonical),
        None,
    )

    if hit is None:
        raise NotFoundError(f"Nessuno SKU trovato con EAN '{ean}'.")

    # --- 3. Re-validate the stored EAN to set ean_valid flag ---
    stored_ean_ok, _ = validate_ean(hit.ean)

    return SKUResponse(
        sku=hit.sku,
        description=hit.description,
        ean=hit.ean,
        ean_secondary=hit.ean_secondary,
        ean_valid=stored_ean_ok,
        moq=hit.moq,
        pack_size=hit.pack_size,
        lead_time_days=hit.lead_time_days,
        safety_stock=hit.safety_stock,
        shelf_life_days=hit.shelf_life_days,
        has_expiry_label=getattr(hit, "has_expiry_label", False) or False,
        in_assortment=hit.in_assortment,
        category=hit.category or "",
        department=hit.department or "",
    )


# ---------------------------------------------------------------------------
# GET /skus/search   — autocomplete / SKU picker for Android bind tab
# ---------------------------------------------------------------------------

@router.get(
    "/skus/search",
    response_model=SkuSearchResponse,
    summary="Cerca SKU per codice o descrizione (autocomplete)",
    dependencies=[Depends(verify_token)],
)
def search_skus(
    q: str = Query(default="", description="Stringa di ricerca (SKU code o descrizione)"),
    limit: int = Query(default=20, ge=1, le=200, description="Numero massimo risultati"),
    storage=Depends(get_storage),
) -> SkuSearchResponse:
    """
    Ricerca full-text lato server su codice SKU e descrizione.

    - `q` vuota → restituisce i primi `limit` SKU ordinati per código.
    - La ricerca è case-insensitive e cerca per sottostringa.
    - Usato dall'app Android per l'autocomplete nella tab Abbinamento EAN.
    """
    all_skus = storage.search_skus(q.strip()) if q.strip() else storage.read_skus()
    # Sort: perfect-prefix match first, then alphabetical
    q_lower = q.strip().lower()
    if q_lower:
        all_skus = sorted(
            all_skus,
            key=lambda s: (
                0 if s.sku.lower().startswith(q_lower) else
                1 if s.description.lower().startswith(q_lower) else 2,
                s.sku.lower(),
            ),
        )
    else:
        all_skus = sorted(all_skus, key=lambda s: s.sku.lower())

    results = [
        SkuSearchResult(
            sku=s.sku,
            description=s.description,
            ean=s.ean,
            ean_secondary=s.ean_secondary,
            in_assortment=s.in_assortment,
        )
        for s in all_skus[:limit]
    ]
    return SkuSearchResponse(query=q, results=results)


# ---------------------------------------------------------------------------
# PATCH /skus/{sku}/bind-secondary-ean
# ---------------------------------------------------------------------------

@router.patch(
    "/skus/{sku}/bind-secondary-ean",
    response_model=BindSecondaryEanResponse,
    summary="Associa EAN secondario a uno SKU",
    dependencies=[Depends(verify_token)],
)
def bind_secondary_ean(
    sku: str,
    body: BindSecondaryEanRequest,
    storage=Depends(get_storage),
) -> BindSecondaryEanResponse:
    """
    Associa (o rimuove) un EAN secondario a uno SKU esistente.

    Regole di business:
    - `sku` deve esistere nel catalogo → **404** se non trovato.
    - `ean_secondary` vuoto → rimuove l'EAN secondario corrente (clear).
    - `ean_secondary` non vuoto → validato come EAN (8/12/13 cifre) → **400** se non valido.
    - `ean_secondary` uguale all'EAN primario dello stesso SKU → **409** (conflitto).
    - `ean_secondary` già usato come primario o secondario da un *altro* SKU → **409**.

    In caso di successo aggiorna `skus.csv` (campo ``ean_secondary``) e restituisce
    i valori aggiornati.  Il preload scanner dell'app Android includerà il nuovo
    alias dalla prossima chiamata a ``GET /skus/scanner-preload``.
    """
    sku = sku.strip()
    new_ean = body.ean_secondary.strip()

    # --- 1. SKU must exist ---
    all_skus = storage.read_skus()
    target = next((s for s in all_skus if s.sku == sku), None)
    if target is None:
        raise NotFoundError(f"SKU '{sku}' non trovato nel catalogo.")

    # --- 2. If non-empty, validate EAN format ---
    if new_ean:
        ok, err_msg = validate_ean(new_ean)
        if not ok:
            raise BadRequestError(f"EAN non valido: {err_msg}")

        # --- 3. Conflict: same as primary EAN of this SKU ---
        if (target.ean or "").strip() == new_ean:
            raise ConflictError(
                f"L'EAN '{new_ean}' è già l'EAN primario di questo SKU."
            )

        # --- 4. Conflict: already in use by another SKU ---
        conflict = next(
            (
                s for s in all_skus
                if s.sku != sku and (
                    (s.ean or "").strip() == new_ean
                    or (s.ean_secondary or "").strip() == new_ean
                )
            ),
            None,
        )
        if conflict is not None:
            raise ConflictError(
                f"L'EAN '{new_ean}' è già associato allo SKU '{conflict.sku}'."
            )

    # --- 5. Persist ---
    updated = storage.bind_ean_secondary(sku, new_ean or None)
    if not updated:
        raise NotFoundError(f"SKU '{sku}' non trovato durante il salvataggio.")

    stored_ean = new_ean if new_ean else None
    msg = (
        f"EAN secondario '{stored_ean}' associato a SKU '{sku}'."
        if stored_ean
        else f"EAN secondario rimosso da SKU '{sku}'."
    )
    return BindSecondaryEanResponse(sku=sku, ean_secondary=stored_ean, message=msg)


# ---------------------------------------------------------------------------
# POST /skus — create article (offline-first, idempotent)
# ---------------------------------------------------------------------------

@router.post(
    "/skus",
    response_model=AddArticleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crea nuovo articolo nel catalogo SKU",
    dependencies=[Depends(verify_token)],
    responses={
        200: {
            "description": "Già creato — risposta replay (client_add_id duplicato)",
            "model": AddArticleResponse,
        },
        201: {"description": "Articolo creato con successo"},
        400: {"description": "Input non valido (descrizione vuota, EAN non valido)"},
        409: {"description": "Conflitto: EAN già associato ad altro SKU, o codice SKU già esistente"},
    },
)
def create_article(
    body: AddArticleRequest,
    db=Depends(get_db),
    storage=Depends(get_storage),
) -> JSONResponse:
    """
    Crea un nuovo articolo nel catalogo SKU.

    **Idempotenza con client_add_id**

    Il client Android genera un UUID stabile prima dell'invio.  Se la stessa
    richiesta arriva due volte (es. retry di rete) il server risponde **200**
    con ``already_created=True`` senza duplicare l'articolo.

    **Gestione SKU provvisorio**

    Il client può inviare un codice SKU provvisorio nel formato ``TMP-<epoch>-<4hex>``.
    Il server accetta il codice proposto se non è già in uso.  La risposta
    riporta il codice confermato nel campo ``sku``; il client Android usa
    questo valore per aggiornare la cache locale (riconciliazione SKU).

    **Validazioni**

    - ``description`` è obbligatoria e non può essere vuota.
    - ``ean_primary`` / ``ean_secondary`` sono opzionali; se presenti, devono
      essere stringhe di 8, 12 o 13 cifre numeriche.
    - Se un EAN è già associato a un *altro* SKU esistente → **409**.
    - Se il codice SKU è già in uso da un *altro* articolo → **409**.
    """
    # ------------------------------------------------------------------ #
    # 1. Ensure idempotency schema exists                                 #
    # ------------------------------------------------------------------ #
    idempotency.ensure_schema(db)

    # ------------------------------------------------------------------ #
    # 2. client_add_id fast-path: replay if already processed             #
    # ------------------------------------------------------------------ #
    client_add_id: str = body.client_add_id.strip()
    _claimed_slot: bool = False

    stored = idempotency.lookup(db, client_add_id)
    if stored is not None:
        _, response_dict = stored
        response_dict["already_created"] = True
        logger.info("idempotency replay: client_add_id=%r endpoint=%s", client_add_id, _CREATE_ENDPOINT_LABEL)
        return JSONResponse(status_code=status.HTTP_200_OK, content=response_dict)

    if idempotency.try_claim(db, client_add_id, _CREATE_ENDPOINT_LABEL):
        _claimed_slot = True
    else:
        # Lost the INSERT race — wait for the winner to finalize.
        stored = idempotency.lookup_with_wait(db, client_add_id)
        if stored is not None:
            stored[1]["already_created"] = True
            logger.info(
                "idempotency concurrent replay: client_add_id=%r endpoint=%s",
                client_add_id, _CREATE_ENDPOINT_LABEL,
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content=stored[1])
        # lookup_with_wait timed out: fall through and process as fresh.
        _claimed_slot = True
        logger.warning("idempotency: lookup_with_wait timed out for %r; processing as fresh", client_add_id)

    # ------------------------------------------------------------------ #
    # 3. Validate description                                             #
    # ------------------------------------------------------------------ #
    description = body.description.strip()
    if not description:
        raise BadRequestError("Il campo 'description' non può essere vuoto.")

    # ------------------------------------------------------------------ #
    # 4. Validate and normalise EAN fields                               #
    # ------------------------------------------------------------------ #
    def _validate_and_normalise(ean_raw: str | None, field_name: str) -> str | None:
        if not ean_raw:
            return None
        ean = ean_raw.strip()
        if not ean:
            return None
        ok, err_msg = validate_ean(ean)
        if not ok:
            raise BadRequestError(f"'{field_name}' non valido: {err_msg}")
        return normalize_ean_13(ean) or ean

    ean_primary = _validate_and_normalise(body.ean_primary, "ean_primary")
    ean_secondary = _validate_and_normalise(body.ean_secondary, "ean_secondary")

    if ean_primary and ean_secondary and ean_primary == ean_secondary:
        raise BadRequestError("'ean_primary' e 'ean_secondary' non possono essere identici.")

    # ------------------------------------------------------------------ #
    # 5. EAN conflict check (against existing catalogue)                 #
    # ------------------------------------------------------------------ #
    all_skus = storage.read_skus()
    proposed_sku = (body.sku or "").strip() or None

    for candidate_ean in filter(None, [ean_primary, ean_secondary]):
        conflict = next(
            (
                s for s in all_skus
                if s.sku != proposed_sku and (
                    normalize_ean_13((s.ean or "").strip()) == candidate_ean
                    or normalize_ean_13((s.ean_secondary or "").strip()) == candidate_ean
                )
            ),
            None,
        )
        if conflict is not None:
            raise ConflictError(
                f"L'EAN '{candidate_ean}' è già associato allo SKU '{conflict.sku}'."
            )

    # ------------------------------------------------------------------ #
    # 6. SKU conflict check                                              #
    # ------------------------------------------------------------------ #
    if proposed_sku and storage.sku_exists(proposed_sku):
        # SKU already exists — 409 unless it belongs to the same idempotency
        # replay (already handled above).  The client should use a different
        # SKU code or omit it to let the server generate one.
        raise ConflictError(
            f"Il codice SKU '{proposed_sku}' è già in uso nel catalogo."
        )

    # ------------------------------------------------------------------ #
    # 7. Assign SKU code (use proposed or keep TMP as canonical)         #
    # ------------------------------------------------------------------ #
    # Policy: accept the client-proposed code verbatim.  If the client did not
    # provide a code, generate a server-side provisional id from the
    # client_add_id (deterministic, no counter state needed).
    import hashlib
    if proposed_sku:
        confirmed_sku = proposed_sku
    else:
        short = hashlib.sha1(client_add_id.encode()).hexdigest()[:8].upper()
        confirmed_sku = f"TMP-{short}"

    # ------------------------------------------------------------------ #
    # 8. Persist SKU                                                     #
    # ------------------------------------------------------------------ #
    new_sku = SKU(
        sku=confirmed_sku,
        description=description,
        ean=ean_primary,
        ean_secondary=ean_secondary,
    )
    storage.write_sku(new_sku)
    logger.info(
        "article created: sku=%s description=%r ean_primary=%s ean_secondary=%s client_add_id=%r",
        confirmed_sku, description, ean_primary, ean_secondary, client_add_id,
    )

    # ------------------------------------------------------------------ #
    # 9. Assemble response                                               #
    # ------------------------------------------------------------------ #
    response_obj = AddArticleResponse(
        sku=confirmed_sku,
        description=description,
        ean_primary=ean_primary,
        ean_secondary=ean_secondary,
        client_add_id=client_add_id,
        already_created=False,
    )
    response_dict = response_obj.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # 10. Finalize idempotency slot                                      #
    # ------------------------------------------------------------------ #
    if _claimed_slot:
        idempotency.finalize(
            conn=db,
            client_event_id=client_add_id,
            status_code=status.HTTP_201_CREATED,
            response_data=response_dict,
        )

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=response_dict)

