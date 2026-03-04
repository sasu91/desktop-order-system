"""
Centralized validation rules for domain models.

Provides validation functions for quantities, dates, and business rules.
Includes colli<->pezzi conversion helpers (colli = display/input unit, pezzi = ledger unit).
"""
import math
from datetime import date
from typing import Optional, Tuple


def validate_quantity(qty: int, allow_negative: bool = False, min_val: Optional[int] = None, max_val: Optional[int] = None) -> Tuple[bool, str]:
    """
    Validate quantity value.
    
    Args:
        qty: Quantity to validate
        allow_negative: Whether negative values are allowed
        min_val: Minimum allowed value (inclusive)
        max_val: Maximum allowed value (inclusive)
    
    Returns:
        (is_valid, error_message)
    """
    if not isinstance(qty, int):
        return False, "La quantità deve essere un numero intero"
    
    if not allow_negative and qty < 0:
        return False, "La quantità non può essere negativa"
    
    if min_val is not None and qty < min_val:
        return False, f"La quantità deve essere almeno {min_val}"
    
    if max_val is not None and qty > max_val:
        return False, f"La quantità non può superare {max_val}"
    
    return True, ""


def validate_sku_code(sku: str) -> Tuple[bool, str]:
    """
    Validate SKU code format.
    
    Args:
        sku: SKU code to validate
    
    Returns:
        (is_valid, error_message)
    """
    if not sku or not sku.strip():
        return False, "Il codice SKU non può essere vuoto"
    
    if len(sku) > 50:
        return False, "Il codice SKU non può superare 50 caratteri"
    
    # Basic format check: alphanumeric + underscore/hyphen
    if not all(c.isalnum() or c in ['_', '-'] for c in sku):
        return False, "Il codice SKU può contenere solo caratteri alfanumerici, '_' e '-'"
    
    return True, ""


def validate_date_range(start_date: date, end_date: date, allow_future: bool = True) -> Tuple[bool, str]:
    """
    Validate date range.
    
    Args:
        start_date: Start date
        end_date: End date
        allow_future: Whether future dates are allowed
    
    Returns:
        (is_valid, error_message)
    """
    if start_date > end_date:
        return False, "La data di inizio non può essere successiva alla data di fine"
    
    if not allow_future:
        today = date.today()
        if end_date > today:
            return False, "Le date non possono essere nel futuro"
    
    return True, ""


def validate_stock_level(on_hand: int, on_order: int) -> Tuple[bool, str]:
    """
    Validate stock levels.
    
    Args:
        on_hand: On-hand stock quantity
        on_order: On-order stock quantity
    
    Returns:
        (is_valid, error_message)
    """
    if on_hand < 0:
        return False, "Lo stock disponibile non può essere negativo"
    
    if on_order < 0:
        return False, "Lo stock in ordine non può essere negativo"
    
    return True, ""


def validate_order_parameters(min_qty: int, max_qty: int, reorder_point: int) -> Tuple[bool, str]:
    """
    Validate order parameters.
    
    Args:
        min_qty: Minimum order quantity
        max_qty: Maximum order quantity
        reorder_point: Reorder point threshold
    
    Returns:
        (is_valid, error_message)
    """
    if min_qty <= 0:
        return False, "La quantità minima deve essere maggiore di 0"
    
    if max_qty < min_qty:
        return False, "La quantità massima deve essere >= quantità minima"
    
    if reorder_point < 0:
        return False, "Il punto di riordino non può essere negativo"
    
    return True, ""


# ---------------------------------------------------------------------------
# Colli <-> Pezzi conversion helpers
# ---------------------------------------------------------------------------
# Canonical rule: all ledger quantities are stored as int pezzi.
# User input/display for most stock quantities uses colli (decimal allowed).
# Waste input remains in pezzi (no conversion needed).
# Conversion rule: colli (float) * pack_size -> round half-up -> int pezzi
# ---------------------------------------------------------------------------

def colli_to_pezzi(colli: float, pack_size: int) -> int:
    """
    Convert colli (decimal) to pezzi (integer) using round-half-up.

    Args:
        colli:     Quantity in colli (fractional allowed, e.g. 1.5)
        pack_size: Pieces per collo (SKU.pack_size; defaults to 1 if <= 0)

    Returns:
        Quantity in pezzi (integer, always >= 0)

    Examples::

        colli_to_pezzi(1.5, 10)   -> 15   # 1.5 x 10 = 15.0 -> 15
        colli_to_pezzi(0.25, 10)  -> 3    # 0.25 x 10 = 2.5 -> round half-up -> 3
        colli_to_pezzi(1.0, 6)    -> 6
        colli_to_pezzi(0.0, 10)   -> 0
    """
    if pack_size <= 0:
        pack_size = 1
    result = math.floor(colli * pack_size + 0.5)
    return max(0, result)


def parse_colli(raw: str) -> Optional[float]:
    """
    Parse a colli string to float, accepting '.' or ',' as decimal separator.

    Returns:
        Parsed value (>= 0.0) or None on parse error / negative value.
    """
    cleaned = raw.strip().replace(",", ".")
    try:
        val = float(cleaned)
        return val if val >= 0.0 else None
    except ValueError:
        return None


def format_pezzi_colli(pezzi: int, pack_size: int) -> str:
    """
    Format an integer pezzi quantity as "N pz (M colli)" for display.

    If pack_size <= 1 (trivial conversion), returns plain "N pz".

    Args:
        pezzi:     Quantity in pezzi (integer)
        pack_size: Pieces per collo

    Examples::

        format_pezzi_colli(15, 10)  -> "15 pz (1.5 colli)"
        format_pezzi_colli(5, 1)    -> "5 pz"
        format_pezzi_colli(3, 6)    -> "3 pz (0.5 colli)"
        format_pezzi_colli(0, 10)   -> "0 pz"
    """
    if pack_size <= 1 or pezzi == 0:
        return f"{pezzi} pz"
    colli = pezzi / pack_size
    # Round to 1 decimal place; use :g to drop trailing zero (1.0 → 1)
    colli_str = f"{round(colli, 1):g}"
    return f"{pezzi} pz ({colli_str} colli)"
