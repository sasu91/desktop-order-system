"""
backend/dos_backend/utils/colli_utils.py
Colli <-> Pezzi conversion helpers for the REST API backend.

Canonical rule: all ledger quantities are stored as int pezzi.
User input/display for most stock quantities uses colli (decimal allowed).
Waste input remains in pezzi (no conversion needed).
Conversion rule: colli (float) * pack_size -> round half-up -> int pezzi.

Events that receive colli input (converted to pezzi before ledger write):
  on_hand (EOD)      -> ADJUST  -> colli
  adjust_qty (EOD)   -> ADJUST  -> colli
  unfulfilled_qty    -> UNFULFILLED -> colli
  ADJUST exceptions  -> colli
  UNFULFILLED exceptions -> colli

Events that receive pezzi input (no conversion):
  waste_qty (EOD)   -> WASTE   -> pezzi
  WASTE exceptions  -> pezzi
  ORDER / RECEIPT / SNAPSHOT / SALE -> internal, not from mobile input
"""
import math
from typing import Optional


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
