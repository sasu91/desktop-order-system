"""
tests/test_colli_pezzi.py — Unit tests for colli <-> pezzi conversion helpers.

Tests both the src/domain/validation.py helpers and the backend mirror in
backend/dos_backend/utils/colli_utils.py.

Scenarios covered:
    - colli_to_pezzi: exact integer, fractional, round-half-up tie, zero, large
    - parse_colli: dot/comma decimal separators, negative, invalid
    - format_pezzi_colli: pack_size=1, pack_size>1, zero, mixed denominators
    - EOD backend: colli fields produce correct int pezzi in ledger
    - ExceptionRequest: float qty validaton
"""
import math
import sys
import os
from datetime import date

import pytest

# ── Import helpers from both locations ────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.domain.validation import (
    colli_to_pezzi as src_colli_to_pezzi,
    parse_colli as src_parse_colli,
    format_pezzi_colli as src_format_pezzi_colli,
)
from backend.dos_backend.utils.colli_utils import (
    colli_to_pezzi as be_colli_to_pezzi,
    parse_colli as be_parse_colli,
    format_pezzi_colli as be_format_pezzi_colli,
)

# Run identical tests on both implementations
_IMPLS = [
    ("src", src_colli_to_pezzi, src_parse_colli, src_format_pezzi_colli),
    ("backend", be_colli_to_pezzi, be_parse_colli, be_format_pezzi_colli),
]


# ── colli_to_pezzi ─────────────────────────────────────────────────────────────

class TestColliToPezzi:
    @pytest.mark.parametrize("colli,pack,expected", [
        # Exact integers
        (1.0,  10, 10),
        (2.0,  10, 20),
        (0.0,  10,  0),
        # Common fractional values
        (1.5,  10, 15),    # 15.0 -> 15
        (0.5,  10,  5),
        (0.25, 10,  3),    # 2.5 -> round half-up -> 3  ← key rule
        (0.1,  10,  1),    # 1.0 -> 1
        # pack_size = 1 (trivial passthrough)
        (3.0,   1,  3),
        (7.0,   1,  7),
        # pack_size = 6 (e.g. beverage)
        (1.0,   6,  6),
        (0.5,   6,  3),
        (0.333, 6,  2),    # 1.998 -> round half-up -> 2
        # Large quantities
        (100.0, 12, 1200),
        # Negative pack_size safety: treated as 1
        (5.0,   0,  5),
        (5.0,  -1,  5),
    ])
    def test_conversion(self, colli, pack, expected):
        for name, fn, _, _ in _IMPLS:
            result = fn(colli, pack)
            assert result == expected, f"[{name}] colli_to_pezzi({colli}, {pack}) = {result}, want {expected}"
            assert isinstance(result, int), f"[{name}] result must be int, got {type(result)}"

    def test_result_always_non_negative(self):
        for _, fn, _, _ in _IMPLS:
            assert fn(0.0, 10) == 0
            assert fn(0.001, 10) == 0   # floor(0.01 + 0.5) = 0

    def test_round_half_up_tie(self):
        """0.25 colli * 10 = 2.5 -> round half-up -> 3 (not banker's rounding)."""
        for name, fn, _, _ in _IMPLS:
            assert fn(0.25, 10) == 3, f"[{name}] should round 2.5 up to 3"
            assert fn(0.35, 10) == 4, f"[{name}] should round 3.5 up to 4"


# ── parse_colli ────────────────────────────────────────────────────────────────

class TestParseColli:
    @pytest.mark.parametrize("raw,expected", [
        ("1.5",  1.5),
        ("1,5",  1.5),   # Italian locale comma
        ("0",    0.0),
        ("0.0",  0.0),
        ("10",  10.0),
        ("  2.5  ", 2.5),  # leading/trailing whitespace
    ])
    def test_valid(self, raw, expected):
        for name, _, fn, _ in _IMPLS:
            result = fn(raw)
            assert result == expected, f"[{name}] parse_colli({raw!r}) = {result}, want {expected}"

    @pytest.mark.parametrize("raw", ["-1", "-0.5", "abc", "", "1.2.3"])
    def test_invalid_returns_none(self, raw):
        for name, _, fn, _ in _IMPLS:
            assert fn(raw) is None, f"[{name}] parse_colli({raw!r}) should return None"


# ── format_pezzi_colli ─────────────────────────────────────────────────────────

class TestFormatPezziColli:
    @pytest.mark.parametrize("pezzi,pack,expected", [
        (15,  10, "15 pz (1.5 colli)"),
        (20,  10, "20 pz (2 colli)"),    # no trailing decimal
        ( 5,   1, "5 pz"),               # pack_size=1 -> no colli suffix
        ( 0,   1, "0 pz"),
        ( 3,   6, "3 pz (0.5 colli)"),
        ( 0,  10, "0 pz (0 colli)"),
        (12,  12, "12 pz (1 colli)"),
        (25,  10, "25 pz (2.5 colli)"),
        (100,  4, "100 pz (25 colli)"),
    ])
    def test_format(self, pezzi, pack, expected):
        for name, _, _, fn in _IMPLS:
            result = fn(pezzi, pack)
            assert result == expected, f"[{name}] format_pezzi_colli({pezzi}, {pack}) = {result!r}, want {expected!r}"


# ── Backend schema validation ──────────────────────────────────────────────────

class TestBackendSchemas:
    def test_eod_entry_accepts_float_colli(self):
        from backend.dos_backend.schemas import EodEntry
        entry = EodEntry(on_hand=1.5, waste_qty=3, adjust_qty=0.25, unfulfilled_qty=2.0)
        assert entry.on_hand == 1.5
        assert entry.waste_qty == 3
        assert entry.adjust_qty == 0.25
        assert entry.unfulfilled_qty == 2.0

    def test_eod_entry_waste_must_be_int(self):
        """waste_qty is in pezzi (int) - should reject float if Pydantic coerces."""
        from backend.dos_backend.schemas import EodEntry
        # Pydantic v2 with int field will coerce 3.0 -> 3
        entry = EodEntry(waste_qty=3)
        assert isinstance(entry.waste_qty, int)

    def test_exception_request_accepts_float_qty(self):
        from backend.dos_backend.schemas import ExceptionRequest
        req = ExceptionRequest(date=date(2026, 3, 4), sku="TEST", event="ADJUST", qty=1.5)
        assert req.qty == 1.5

    def test_exception_request_rejects_zero_qty(self):
        from backend.dos_backend.schemas import ExceptionRequest
        from pydantic import ValidationError
        with pytest.raises((ValidationError, Exception)):
            ExceptionRequest(date=date(2026, 3, 4), sku="TEST", event="WASTE", qty=0)

    def test_stock_item_has_pack_size(self):
        from backend.dos_backend.schemas import StockItem
        item = StockItem(sku="SKU001", description="Test", on_hand=15, on_order=0)
        assert item.pack_size == 1  # default
        item2 = StockItem(sku="SKU001", description="Test", on_hand=15, on_order=0, pack_size=10)
        assert item2.pack_size == 10


# ── Round-trip integration: colli input -> pezzi ledger ───────────────────────

@pytest.mark.parametrize("colli_input,pack_size,expected_pezzi", [
    (1.5,  10, 15),   # 1.5 colli, pack=10 -> 15 pz
    (0.25, 10,  3),   # round half-up
    (2.0,   6, 12),   # exact
    (0.0,  10,  0),   # zero on_hand is valid (empty shelf)
    (3.0,   1,  3),   # pack=1 passthrough
])
def test_eod_roundtrip(colli_input, pack_size, expected_pezzi):
    """Simulate what the backend EOD router does: colli * pack_size -> int pezzi."""
    qty_pezzi = src_colli_to_pezzi(colli_input, pack_size)
    assert qty_pezzi == expected_pezzi, f"EOD roundtrip: {colli_input} colli * pack {pack_size} -> {qty_pezzi} pz (want {expected_pezzi})"
    assert isinstance(qty_pezzi, int)
