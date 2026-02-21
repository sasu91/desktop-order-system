"""
Parallel OOS (Out-of-Stock) metrics computation.

Provides CPU-parallel processing of the OOS analysis phase that precedes the
bulk OOS popup.  The hot loop — `calculate_daily_sales_average` per SKU — is
embarrassingly parallel: each SKU is fully independent and has no side effects.

Architecture
------------
* Top-level (module-scope) functions only — required for pickle support when
  using ProcessPoolExecutor with the "spawn" start method (Windows default /
  PyInstaller).
* **Primitive serialization** (key performance decision): Transaction and
  SalesRecord dataclasses are converted to plain tuples of primitives
  (str / int) *before* being pickled.  Tuple-of-primitives pickle ~10× faster
  than typed dataclasses with Enum members, which was the dominant bottleneck
  when pickling hundreds of transactions per SKU across many SKUs.
  Workers reconstruct the objects after unpickling.
* `run_oos_parallel()` is called from a background daemon *thread* in the GUI.
  It orchestrates the ProcessPoolExecutor and calls `on_progress(n_done)` on
  each chunk completion — the GUI thread polls via `root.after()`.
"""

from __future__ import annotations

import logging
import math
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── Primitive serialization helpers ──────────────────────────────────────────
# Tuples of primitives (str/int/None) pickle much faster than typed dataclasses
# with Enum members.  We convert before chunking and reconstruct in the worker.

def _txn_to_tuple(t) -> tuple:
    """Transaction → (date_iso, sku, event_value, qty, receipt_iso|None, note|None)"""
    return (
        t.date.isoformat(),
        t.sku,
        t.event.value,          # EventType enum → str value
        t.qty,
        t.receipt_date.isoformat() if t.receipt_date is not None else None,
        t.note,
    )


def _sale_to_tuple(s) -> tuple:
    """SalesRecord → (date_iso, sku, qty_sold, promo_flag)"""
    return (s.date.isoformat(), s.sku, s.qty_sold, s.promo_flag)


def _serialize_sku_items(sku_items: list) -> list:
    """
    Convert a list of sku_item dicts so that ``sku_transactions`` and
    ``sku_sales`` values are lists of primitive tuples rather than dataclasses.
    Returns a new list; original items are unchanged.
    """
    out = []
    for item in sku_items:
        out.append({
            "sku_id": item["sku_id"],
            "oos_detection_mode": item["oos_detection_mode"],
            "sku_transactions": [_txn_to_tuple(t) for t in item["sku_transactions"]],
            "sku_sales": [_sale_to_tuple(s) for s in item["sku_sales"]],
        })
    return out


# ── Worker (runs in a spawned subprocess) ────────────────────────────────────

def _oos_chunk_worker(chunk_args: dict) -> dict:
    """
    Compute OOS metrics for one chunk of SKUs.

    Called in a spawned subprocess — must not touch Tkinter, SQLite, or any
    shared mutable state.

    ``chunk_args`` keys
    -------------------
    project_root : str
        Absolute path to the project root (added to sys.path so that
        ``src.*`` imports resolve correctly in spawned processes).
    asof_date_iso : str
        ISO date string (YYYY-MM-DD).
    days_lookback : int
    skus : list[dict]
        Each entry has keys:
        ``sku_id``             (str)
        ``oos_detection_mode`` (str) "strict" | "relaxed"
        ``sku_transactions``   (list[tuple]) primitive-serialized Transactions
        ``sku_sales``          (list[tuple]) primitive-serialized SalesRecords

    Returns
    -------
    dict  {sku_id: {"daily_sales", "oos_days_count", "oos_days_list",
                    "out_of_assortment_days"}}
    """
    project_root: str = chunk_args["project_root"]
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Local imports — only resolved after sys.path is patched in the subprocess
    from src.domain.models import Transaction, SalesRecord, EventType  # noqa: PLC0415
    from src.workflows.order import calculate_daily_sales_average       # noqa: PLC0415

    asof = date.fromisoformat(chunk_args["asof_date_iso"])
    days_lookback: int = chunk_args["days_lookback"]
    results: dict = {}

    for item in chunk_args["skus"]:
        sku_id: str = item["sku_id"]
        try:
            # Reconstruct typed objects from primitive tuples
            sku_transactions = [
                Transaction(
                    date=date.fromisoformat(t[0]),
                    sku=t[1],
                    event=EventType(t[2]),
                    qty=t[3],
                    receipt_date=date.fromisoformat(t[4]) if t[4] is not None else None,
                    note=t[5],
                )
                for t in item["sku_transactions"]
            ]
            sku_sales = [
                SalesRecord(
                    date=date.fromisoformat(s[0]),
                    sku=s[1],
                    qty_sold=s[2],
                    promo_flag=s[3],
                )
                for s in item["sku_sales"]
            ]

            daily_sales, oos_days_count, oos_days_list, ooa_days = (
                calculate_daily_sales_average(
                    sales_records=[],
                    sku=sku_id,
                    days_lookback=days_lookback,
                    transactions=[],
                    asof_date=asof,
                    oos_detection_mode=item["oos_detection_mode"],
                    return_details=True,
                    sku_transactions=sku_transactions,
                    sku_sales=sku_sales,
                )
            )
        except Exception as exc:
            # Never crash a chunk on one bad SKU — return zeroed metrics and log
            logger.warning("OOS worker error for SKU %s: %s", sku_id, exc)
            daily_sales, oos_days_count, oos_days_list, ooa_days = 0.0, 0, [], []

        results[sku_id] = {
            "daily_sales": daily_sales,
            "oos_days_count": oos_days_count,
            "oos_days_list": oos_days_list,
            "out_of_assortment_days": ooa_days,
        }

    return results


# ── Orchestrator (runs in a background daemon thread) ────────────────────────

def run_oos_parallel(
    sku_items: list,
    days_lookback: int,
    asof_date: date,
    n_workers: int,
    project_root: str,
    on_progress: Optional[Callable[[int], None]] = None,
) -> dict:
    """
    Run OOS metrics for all ``sku_items`` in parallel using
    ``ProcessPoolExecutor``.

    Parameters
    ----------
    sku_items : list[dict]
        Each entry: ``{"sku_id", "oos_detection_mode", "sku_transactions",
        "sku_sales"}``.  ``sku_transactions`` and ``sku_sales`` may contain
        either typed dataclasses *or* primitive tuples — serialization is
        performed here before chunking.
    days_lookback : int
    asof_date : date
    n_workers : int
        Number of worker processes.  Caller is responsible for leaving at
        least 1 CPU free (pass ``max(1, os.cpu_count() - 1)``).
    project_root : str
        Absolute path passed to each worker for sys.path bootstrapping.
    on_progress : callable(n_done: int) | None
        Called after each chunk completes with the cumulative number of SKUs
        processed.  Safe to call from a worker thread; GUI thread should
        register updates via ``root.after(0, ...)``.

    Returns
    -------
    dict  {sku_id: metrics_dict}  — same order-stable dict as sequential path.
    """
    n = len(sku_items)
    if n == 0:
        return {}

    # Convert dataclasses → primitive tuples before pickling (key optimisation)
    serialized_items = _serialize_sku_items(sku_items)

    # Chunk size: distribute evenly across workers
    chunk_size = max(1, math.ceil(n / n_workers))
    chunks = [serialized_items[i : i + chunk_size] for i in range(0, n, chunk_size)]

    asof_iso = asof_date.isoformat()
    all_results: dict = {}
    done_count = 0

    try:
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            future_map = {
                executor.submit(
                    _oos_chunk_worker,
                    {
                        "project_root": project_root,
                        "asof_date_iso": asof_iso,
                        "days_lookback": days_lookback,
                        "skus": chunk,
                    },
                ): chunk
                for chunk in chunks
            }

            for future in as_completed(future_map):
                try:
                    chunk_result = future.result()
                    all_results.update(chunk_result)
                except Exception as exc:
                    # Chunk failed: fill with zeros so the GUI can continue
                    for item in future_map[future]:
                        all_results[item["sku_id"]] = {
                            "daily_sales": 0.0,
                            "oos_days_count": 0,
                            "oos_days_list": [],
                            "out_of_assortment_days": [],
                        }
                    logger.error("OOS parallel chunk failed: %s", exc)

                done_count += len(future_map[future])
                if on_progress:
                    try:
                        on_progress(done_count)
                    except Exception:
                        pass  # never let a progress callback crash the worker

    except Exception as exc:
        logger.error("ProcessPoolExecutor failed: %s. Results may be partial.", exc)
        raise

    return all_results
