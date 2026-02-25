"""
GET /skus/by-ean/{ean} — SKU lookup by EAN/GTIN barcode.

Errors
------
400 BAD_REQUEST   EAN contains non-digit characters or has invalid length.
404 NOT_FOUND     EAN format is valid but no SKU with that code exists.
"""
from fastapi import APIRouter, Depends

from ..api.auth import verify_token
from ..api.deps import get_storage
from ..api.errors import BadRequestError, NotFoundError
from ..domain.ledger import validate_ean
from ..schemas import SKUResponse

router = APIRouter(tags=["skus"])


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
    # Normalise both sides: strip whitespace, compare as strings.
    skus = storage.read_skus()
    hit = next((s for s in skus if (s.ean or "").strip() == ean), None)

    if hit is None:
        raise NotFoundError(f"Nessuno SKU trovato con EAN '{ean}'.")

    # --- 3. Re-validate the stored EAN to set ean_valid flag ---
    stored_ean_ok, _ = validate_ean(hit.ean)

    return SKUResponse(
        sku=hit.sku,
        description=hit.description,
        ean=hit.ean,
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
