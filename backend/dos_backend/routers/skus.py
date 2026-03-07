"""
GET /skus/by-ean/{ean} — SKU lookup by EAN/GTIN barcode.

Errors
------
400 BAD_REQUEST   EAN contains non-digit characters or has invalid length.
404 NOT_FOUND     EAN format is valid but no SKU with that code exists.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from ..api.auth import verify_token
from ..api.deps import get_storage
from ..api.errors import BadRequestError, ConflictError, NotFoundError
from ..domain.ledger import StockCalculator, normalize_ean_13, validate_ean
from ..schemas import (
    BindSecondaryEanRequest,
    BindSecondaryEanResponse,
    SKUResponse,
    ScannerPreloadItem,
    SkuSearchResponse,
    SkuSearchResult,
)

router = APIRouter(tags=["skus"])


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

        if primary:
            result.append(ScannerPreloadItem(
                ean=primary,
                sku=sku_obj.sku,
                description=sku_obj.description,
                pack_size=getattr(sku_obj, "pack_size", 1) or 1,
                on_hand=on_hand,
                on_order=on_order,
            ))

        if secondary and secondary != primary:
            result.append(ScannerPreloadItem(
                ean=secondary,
                sku=sku_obj.sku,
                description=sku_obj.description,
                pack_size=getattr(sku_obj, "pack_size", 1) or 1,
                on_hand=on_hand,
                on_order=on_order,
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
