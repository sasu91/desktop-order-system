"""
SKU Canonical Validation Utility

Canonical SKU format: exactly 7 numeric digits, zero-padded (e.g. '0450663').
Validation is STRICT — no numeric-equivalence fallback.
A SKU like '450663' (missing leading zero) is ALWAYS an error, not silently accepted.

Usage:
    from src.utils.sku_validation import validate_sku_canonical, SkuFormatError

    validate_sku_canonical(sku, context="Document DDT-20260328")   # raises SkuFormatError on fail
"""

import re
from typing import Optional

# Canonical pattern: exactly 7 decimal digits.
_SKU_PATTERN = re.compile(r'^\d{7}$')


class SkuFormatError(ValueError):
    """Raised when a SKU string is not in canonical 7-digit format.

    Attributes:
        sku_value: The offending value (as received, not coerced).
        context:   Optional caller context to aid diagnostics (e.g. document ID, field name).
    """

    def __init__(self, sku_value: object, context: Optional[str] = None) -> None:
        self.sku_value = sku_value
        self.context = context
        ctx_str = f" [{context}]" if context else ""
        super().__init__(
            f"SKU non canonico (atteso stringa di esattamente 7 cifre numeriche): "
            f"ricevuto {sku_value!r}{ctx_str}"
        )


def validate_sku_canonical(sku: object, context: Optional[str] = None) -> str:
    """Validate that *sku* is a canonical 7-digit zero-padded string.

    Strict mode:
    - Must be a ``str`` (not int / None).
    - Must match ``^\\d{7}$`` — no leading/trailing spaces, no letters, exactly 7 digits.
    - Leading zeros are REQUIRED (e.g. '0450663' is canonical; '450663' is not).

    Args:
        sku:     Value to validate.
        context: Optional caller context for error messages (e.g. document ID, field path).

    Returns:
        The validated SKU string (unchanged).

    Raises:
        SkuFormatError: If the SKU does not satisfy the canonical format.
    """
    if not isinstance(sku, str) or not _SKU_PATTERN.match(sku):
        raise SkuFormatError(sku, context=context)
    return sku


def is_sku_canonical(sku: object) -> bool:
    """Return True iff *sku* satisfies the canonical 7-digit format (no exception variant)."""
    return isinstance(sku, str) and bool(_SKU_PATTERN.match(sku))
