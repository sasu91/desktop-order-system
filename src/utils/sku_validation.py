"""
SKU Validation Utility

A SKU is valid when it is a non-empty string with no leading/trailing whitespace
and no embedded whitespace characters.  This allows both legacy alphanumeric codes
(e.g. 'LATTE_UHT', 'BIRRA_LAGER') and zero-padded numeric codes (e.g. '0450663').

The key guarantee is anti-coercion: a SKU is always stored/compared as a str,
so a code like '0123456' can never silently become 123456 by an int conversion.

Usage:
    from src.utils.sku_validation import validate_sku_canonical, SkuFormatError

    validate_sku_canonical(sku, context="Document DDT-20260328")   # raises SkuFormatError on fail
"""

import re
from typing import Optional

# A SKU is any non-empty string that contains no whitespace.
_SKU_PATTERN = re.compile(r'^\S+$')


class SkuFormatError(ValueError):
    """Raised when a SKU string is empty, None, or contains whitespace.

    Attributes:
        sku_value: The offending value (as received, not coerced).
        context:   Optional caller context to aid diagnostics (e.g. document ID, field name).
    """

    def __init__(self, sku_value: object, context: Optional[str] = None) -> None:
        self.sku_value = sku_value
        self.context = context
        ctx_str = f" [{context}]" if context else ""
        super().__init__(
            f"SKU non valido (atteso stringa non vuota senza spazi): "
            f"ricevuto {sku_value!r}{ctx_str}"
        )


def validate_sku_canonical(sku: object, context: Optional[str] = None) -> str:
    """Validate that *sku* is a non-empty string containing no whitespace.

    The check ensures:
    - ``sku`` is a ``str`` (not int / None — prevents silent leading-zero loss).
    - Not empty and contains no whitespace characters.

    Alphanumeric legacy codes ('LATTE_UHT', 'BIRRA_LAGER') and zero-padded
    numeric codes ('0450663') are both valid.

    Args:
        sku:     Value to validate.
        context: Optional caller context for error messages.

    Returns:
        The validated SKU string (unchanged).

    Raises:
        SkuFormatError: If the SKU is not a non-empty, whitespace-free string.
    """
    if not isinstance(sku, str) or not _SKU_PATTERN.match(sku):
        raise SkuFormatError(sku, context=context)
    return sku


def is_sku_canonical(sku: object) -> bool:
    """Return True iff *sku* is a non-empty, whitespace-free string (no exception variant)."""
    return isinstance(sku, str) and bool(_SKU_PATTERN.match(sku))
