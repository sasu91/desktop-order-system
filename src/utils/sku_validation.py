"""
SKU Canonical Validation Utility

Canonical SKU format: exactly 7 numeric digits, zero-padded (e.g. '0450663').
Validation is STRICT — no numeric-equivalence fallback.

This file is mirrored by backend/dos_backend/utils/sku_validation.py.
Keep the two files in sync when modifying the canonical format.
"""

import re
from typing import Optional

# Canonical pattern: exactly 7 decimal digits.
_SKU_PATTERN = re.compile(r'^\d{7}$')


class SkuFormatError(ValueError):
    """Raised when a SKU string is not in canonical 7-digit format."""

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

    Raises SkuFormatError if the format is not satisfied.
    Returns the validated SKU string unchanged.
    """
    if not isinstance(sku, str) or not _SKU_PATTERN.match(sku):
        raise SkuFormatError(sku, context=context)
    return sku


def is_sku_canonical(sku: object) -> bool:
    """Return True iff *sku* satisfies the canonical 7-digit format."""
    return isinstance(sku, str) and bool(_SKU_PATTERN.match(sku))
