"""
SKU Validation Utility (backend mirror)

SKU format: qualsiasi stringa non vuota composta da lettere, cifre, underscore
o trattino (es. '0450663', 'LATTE_UHT', 'BIRRA-LAGER').

Regola per SKU numerici: se uno SKU è composto solo da cifre e inizia con zero,
lo zero iniziale deve essere SEMPRE preservato — non fare mai int(sku).

This is the backend-package copy of src/utils/sku_validation.py.
Keep the two files in sync when modifying the format.
"""

import re
from typing import Optional

# Valid SKU: 1+ chars, letters / digits / underscore / hyphen, no whitespace.
_SKU_PATTERN = re.compile(r'^[A-Za-z0-9_\-]+$')


class SkuFormatError(ValueError):
    """Raised when a SKU string is not in a valid format."""

    def __init__(self, sku_value: object, context: Optional[str] = None) -> None:
        self.sku_value = sku_value
        self.context = context
        ctx_str = f" [{context}]" if context else ""
        super().__init__(
            f"SKU non valido (deve essere una stringa non vuota di lettere/cifre/underscore/trattino, "
            f"es. '0450663' o 'LATTE_UHT'): "
            f"ricevuto {sku_value!r}{ctx_str}"
        )


def validate_sku_canonical(sku: object, context: Optional[str] = None) -> str:
    """Validate that *sku* is a non-empty string of letters/digits/underscore/hyphen.

    Numeric SKUs with leading zeros (e.g. '0450663') are accepted as-is;
    the caller must never convert them to int.

    Raises SkuFormatError if the format is not satisfied.
    Returns the validated SKU string unchanged.
    """
    if not isinstance(sku, str) or not _SKU_PATTERN.match(sku):
        raise SkuFormatError(sku, context=context)
    return sku


def is_sku_canonical(sku: object) -> bool:
    """Return True iff *sku* is a valid SKU string."""
    return isinstance(sku, str) and bool(_SKU_PATTERN.match(sku))
