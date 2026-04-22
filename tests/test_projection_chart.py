"""Unit tests for the Stock Projection series builder.

Validates that the helper used by the detail-sidebar chart produces:
- real ledger-based historical series (not synthetic),
- policy-aware future demand (Monte Carlo when MC is the primary method),
- correctly positioned pipeline receipts and current-proposal receipt,
- robust behaviour when forecast data is missing.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, List

from src.domain.models import EventType, Transaction
from src.workflows.projection import build_projection_series


@dataclass
class _FakeProposal:
    """Minimal stand-in for ``OrderProposal`` (duck-typed)."""
    sku: str = "X"
    description: str = ""
    current_on_hand: int = 0
    current_on_order: int = 0
    daily_sales_avg: float = 0.0
    proposed_qty: int = 0
    receipt_date: Optional[date] = None
    safety_stock: int = 0
    max_stock: int = 0
    forecast_method: str = ""
    forecast_qty: int = 0
    forecast_period_days: int = 0
    mc_method_used: str = ""


TODAY = date(2026, 4, 22)


def _snapshot(d: date, sku: str, qty: int) -> Transaction:
    return Transaction(date=d, sku=sku, event=EventType.SNAPSHOT, qty=qty)


def _sale(d: date, sku: str, qty: int) -> Transaction:
    return Transaction(date=d, sku=sku, event=EventType.SALE, qty=qty)


def _order(d: date, sku: str, qty: int, receipt: date) -> Transaction:
    return Transaction(
        date=d, sku=sku, event=EventType.ORDER, qty=qty, receipt_date=receipt
    )


# ---------------------------------------------------------------------------
# Policy-aware demand
# ---------------------------------------------------------------------------

def test_mc_primary_uses_forecast_qty_over_horizon():
    """When forecast_method=monte_carlo and SMA=0, MC drives the slope."""
    prop = _FakeProposal(
        sku="LATTE",
        current_on_hand=92,
        daily_sales_avg=0.0,         # SMA=0 as in LATTE_UHT case
        forecast_method="monte_carlo",
        forecast_qty=16,             # MC forecast over 1-day horizon => 16 pz/g
        forecast_period_days=1,
        mc_method_used="monte_carlo",
    )
    series = build_projection_series(prop, TODAY, [])
    assert series.demand_source == "monte_carlo"
    assert abs(series.daily_demand - 16.0) < 1e-9
    # Stock should decline by ~16/day until clamped at 0
    # Day 0 = 92, Day 1 = 76, Day 2 = 60, Day 3 = 44, Day 4 = 28, Day 5 = 12, Day 6 = 0
    assert series.future_stock[0] == 92.0
    assert series.future_stock[1] == 76.0
    assert series.future_stock[5] == 12.0
    assert series.future_stock[6] == 0.0


def test_simple_method_falls_back_to_sma():
    prop = _FakeProposal(
        current_on_hand=50,
        daily_sales_avg=5.0,
        forecast_method="simple",
        forecast_qty=0,
        forecast_period_days=7,
    )
    series = build_projection_series(prop, TODAY, [])
    assert series.demand_source == "sma"
    assert series.daily_demand == 5.0


def test_mc_flagged_but_missing_forecast_falls_back_to_sma():
    """Defensive: if MC is declared but forecast_qty=0, use SMA."""
    prop = _FakeProposal(
        current_on_hand=10,
        daily_sales_avg=2.0,
        forecast_method="monte_carlo",
        forecast_qty=0,
        forecast_period_days=0,
    )
    series = build_projection_series(prop, TODAY, [])
    assert series.demand_source == "sma"
    assert series.daily_demand == 2.0


# ---------------------------------------------------------------------------
# Historical series comes from real ledger
# ---------------------------------------------------------------------------

def test_history_reflects_real_ledger_not_synthetic():
    """Past 7 days must come from ledger as-of calculation, not formulas."""
    sku = "S"
    # SNAPSHOT at day -10, no sales: stock stays constant 100
    transactions = [
        _snapshot(TODAY - timedelta(days=10), sku, 100),
    ]
    prop = _FakeProposal(sku=sku, current_on_hand=100, daily_sales_avg=999.0)
    series = build_projection_series(prop, TODAY, transactions)
    # All past days must be 100 (real ledger), NOT proportional to daily_sales_avg.
    # Old synthetic formula would yield 100 + 999*abs(d) values.
    assert all(v == 100.0 for v in series.past_stock), series.past_stock


def test_history_follows_sales_drawdown():
    """Past series should drop on sale days, matching the audit timeline."""
    sku = "S"
    transactions = [
        _snapshot(TODAY - timedelta(days=10), sku, 100),
        _sale(TODAY - timedelta(days=3), sku, 20),
        _sale(TODAY - timedelta(days=1), sku, 10),
    ]
    prop = _FakeProposal(sku=sku, current_on_hand=70, daily_sales_avg=0.0)
    series = build_projection_series(prop, TODAY, transactions)
    # past_x = [-7..0]; index 0 -> d=-7, index 7 -> d=0
    # End of day -4 (before -3 sale) = 100
    assert series.past_stock[3] == 100.0    # d=-4
    assert series.past_stock[4] == 80.0     # d=-3 (after sale of 20)
    assert series.past_stock[5] == 80.0     # d=-2
    assert series.past_stock[6] == 70.0     # d=-1 (after sale of 10)
    assert series.past_stock[7] == 70.0     # d=0 = today


# ---------------------------------------------------------------------------
# Pipeline receipts
# ---------------------------------------------------------------------------

def test_pipeline_receipt_adds_to_future_on_its_day():
    sku = "S"
    receipt = TODAY + timedelta(days=3)
    transactions = [
        _snapshot(TODAY - timedelta(days=10), sku, 10),
        _order(TODAY - timedelta(days=1), sku, 50, receipt),
    ]
    prop = _FakeProposal(
        sku=sku, current_on_hand=10, current_on_order=50, daily_sales_avg=2.0,
    )
    series = build_projection_series(prop, TODAY, transactions)
    assert series.pipeline_by_day == {3: 50}
    # Day 2: 10 - 2*2 = 6 ; Day 3: 6 - 2 + 50 = 54
    assert series.future_stock[2] == 6.0
    assert series.future_stock[3] == 54.0


def test_unresolved_pipeline_when_on_order_gt_resolved():
    """On-order qty without a pipeline receipt_date is flagged."""
    sku = "S"
    transactions = [_snapshot(TODAY - timedelta(days=10), sku, 5)]
    prop = _FakeProposal(
        sku=sku, current_on_hand=5, current_on_order=30, daily_sales_avg=0.0,
    )
    series = build_projection_series(prop, TODAY, transactions)
    assert series.pipeline_by_day == {}
    assert series.pipeline_unresolved == 30


# ---------------------------------------------------------------------------
# Current proposal receipt
# ---------------------------------------------------------------------------

def test_current_proposal_receipt_adds_proposed_qty():
    sku = "S"
    receipt = TODAY + timedelta(days=4)
    transactions = [_snapshot(TODAY - timedelta(days=10), sku, 20)]
    prop = _FakeProposal(
        sku=sku, current_on_hand=20, daily_sales_avg=1.0,
        proposed_qty=30, receipt_date=receipt,
    )
    series = build_projection_series(prop, TODAY, transactions)
    assert series.receipt_offset == 4
    # Day 3: 20 - 3*1 = 17 ; Day 4: 17 - 1 + 30 = 46
    assert series.future_stock[3] == 17.0
    assert series.future_stock[4] == 46.0


# ---------------------------------------------------------------------------
# LATTE_UHT regression scenario
# ---------------------------------------------------------------------------

def test_latte_uht_like_scenario_not_flat_when_mc_positive():
    """Regression for the reported LATTE_UHT case: flat line with MC=16."""
    sku = "LATTE_UHT"
    transactions = [_snapshot(TODAY - timedelta(days=1), sku, 92)]
    prop = _FakeProposal(
        sku=sku,
        current_on_hand=92,
        daily_sales_avg=0.0,
        forecast_method="monte_carlo",
        forecast_qty=16,
        forecast_period_days=1,
        mc_method_used="monte_carlo",
        safety_stock=8,
        max_stock=300,
    )
    series = build_projection_series(prop, TODAY, transactions)
    # Must NOT be flat: day 1 strictly less than day 0
    assert series.future_stock[1] < series.future_stock[0]
    # MC drove the slope
    assert series.demand_source == "monte_carlo"
