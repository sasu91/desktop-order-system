"""
Stock projection chart series builder.

Pure function that produces the data series rendered by the detail sidebar
chart in the GUI. Extracted here so it is testable without any Tkinter or
matplotlib dependency.

Semantic rules
--------------
- Historical series: real ledger values (as-of each past day), NOT a synthetic
  extrapolation. Matches the Storico/Audit timeline.
- Future series: policy-aware demand driver.
    * When the proposal's forecast method is Monte Carlo (primary method),
      the daily demand used in the chart is derived from
      ``forecast_qty / forecast_period_days`` so the projection reflects the
      MC driver even when the plain SMA is zero.
    * Otherwise the SMA ``daily_sales_avg`` is used.
- Pipeline receipts (already confirmed ORDER events in the ledger) are added
  on their expected receipt day; the current proposal's receipt (not yet in
  the ledger) is added at ``receipt_offset`` if positive.

No business-logic formulas (Monte Carlo, safety stock, policy) are changed
here: the function only consumes the values already attached to the
``OrderProposal`` by the workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

from src.domain.ledger import StockCalculator
from src.domain.models import SalesRecord, Transaction


@dataclass
class ProjectionSeries:
    """Series data for the Stock Projection chart."""
    past_x: List[int]
    past_stock: List[float]
    future_x: List[int]
    future_stock: List[float]
    pipeline_by_day: Dict[int, int]
    pipeline_unresolved: int
    receipt_offset: Optional[int]
    daily_demand: float
    demand_source: str  # "monte_carlo" | "sma"
    past_days: int
    future_end: int


def _select_demand_driver(proposal) -> tuple[float, str]:
    """Return (daily_demand, source_tag) applying the policy-aware rule.

    Rule: if the proposal's forecast method is Monte Carlo AND a positive
    forecast has been produced over a positive horizon, use the MC-derived
    daily demand. Otherwise fall back to the SMA ``daily_sales_avg``.
    """
    fm = (getattr(proposal, "forecast_method", "") or "").lower()
    mc_used = (getattr(proposal, "mc_method_used", "") or "").lower()
    is_mc_primary = fm == "monte_carlo" or mc_used == "monte_carlo"
    horizon = int(getattr(proposal, "forecast_period_days", 0) or 0)
    fq = int(getattr(proposal, "forecast_qty", 0) or 0)
    if is_mc_primary and horizon > 0 and fq > 0:
        return fq / horizon, "monte_carlo"
    return max(float(proposal.daily_sales_avg or 0.0), 0.0), "sma"


def build_projection_series(
    proposal,
    today: date,
    transactions: Sequence[Transaction],
    sales_records: Optional[Sequence[SalesRecord]] = None,
    past_days: int = 7,
    future_extra_days: int = 5,
) -> ProjectionSeries:
    """Build the data series for the Stock Projection chart.

    Args:
        proposal: An ``OrderProposal`` (duck-typed; any object with the
            expected attributes works).
        today: Reference "today" date (no ``date.today()`` inside so tests
            are deterministic).
        transactions: Ledger transactions used for the real historical
            series and for the pipeline receipts.
        sales_records: Optional daily sales records for the historical
            as-of calculation (same semantics as ``StockCalculator``).
        past_days: Number of past days to render (default 7).
        future_extra_days: Days added beyond the receipt window.
    """
    daily_demand, demand_source = _select_demand_driver(proposal)

    # --- Past: real ledger as-of per day -------------------------------------
    # calculate_asof applies events with date < asof_date, so to capture the
    # state at end-of-day D we query with asof_date = D + 1.
    past_x: List[int] = list(range(-past_days, 1))
    past_stock: List[float] = []
    txns_list = list(transactions)
    sales_list = list(sales_records) if sales_records is not None else None
    for d in past_x:
        target = today + timedelta(days=d)
        try:
            stock = StockCalculator.calculate_asof(
                proposal.sku, target + timedelta(days=1), txns_list, sales_list,
            )
            past_stock.append(float(stock.on_hand))
        except Exception:
            # On any ledger error, fall back to current_on_hand so the
            # chart still renders something reasonable.
            past_stock.append(float(proposal.current_on_hand))

    # --- Pipeline receipts ---------------------------------------------------
    pipeline_by_day: Dict[int, int] = {}
    try:
        pending = StockCalculator.on_order_by_date(
            proposal.sku, txns_list, as_of_date=today + timedelta(days=1),
        )
        for rd, qty in pending.items():
            offset = (rd - today).days
            if offset > 0:
                pipeline_by_day[offset] = pipeline_by_day.get(offset, 0) + qty
    except Exception:
        pipeline_by_day = {}

    pipeline_resolved = sum(pipeline_by_day.values())
    pipeline_unresolved = max(0, int(proposal.current_on_order) - pipeline_resolved)

    # --- Receipt offset for the current proposal -----------------------------
    receipt_offset: Optional[int] = None
    if getattr(proposal, "receipt_date", None):
        receipt_offset = (proposal.receipt_date - today).days

    # --- Future series -------------------------------------------------------
    base_future_end = (receipt_offset if receipt_offset is not None else 14)
    future_end = max(base_future_end + future_extra_days, 10)
    if pipeline_by_day:
        future_end = max(future_end, max(pipeline_by_day.keys()) + future_extra_days)

    future_x: List[int] = []
    future_stock: List[float] = []
    for d in range(0, future_end + 1):
        if d == 0:
            s = float(proposal.current_on_hand)
        else:
            s = future_stock[-1] - daily_demand
            if d in pipeline_by_day:
                s += pipeline_by_day[d]
            if (receipt_offset is not None
                    and d == receipt_offset
                    and proposal.proposed_qty > 0):
                s += proposal.proposed_qty
            s = max(s, 0.0)
        future_x.append(d)
        future_stock.append(s)

    return ProjectionSeries(
        past_x=past_x,
        past_stock=past_stock,
        future_x=future_x,
        future_stock=future_stock,
        pipeline_by_day=pipeline_by_day,
        pipeline_unresolved=pipeline_unresolved,
        receipt_offset=receipt_offset,
        daily_demand=daily_demand,
        demand_source=demand_source,
        past_days=past_days,
        future_end=future_end,
    )
