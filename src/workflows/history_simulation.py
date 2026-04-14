"""
History Simulation Workflow.

Generates synthetic historical data for a SKU by simulating daily sales
(SALE events), automatic reorders (ORDER events), and receipts (RECEIPT events)
using the real order engine and receiving workflow — exactly as the normal
operational flow would produce them.

Design rules:
- All events are tagged with a simulation prefix (SIM_HIST) for idempotent reruns.
- Stock starts at 0 on the first day of the period; the engine creates orders
  automatically from that day forward.
- Random qty per day is sampled from a uniform integer range [qty_min, qty_max].
- Rerun is idempotent: before generating, all previous SIM_HIST events for the
  SKU in the same period are removed; sales.csv rows for the SKU+period are
  replaced wholesale.
- No datetime.now() or date.today() in core logic; dates are passed as params.
"""
from datetime import date, timedelta
from typing import List, Tuple, Optional
import random
import logging

from ..domain.models import (
    Transaction, EventType, Stock, SalesRecord, SKU, OrderProposal,
)
from ..domain.ledger import StockCalculator
from ..persistence.csv_layer import CSVLayer
from ..utils.sku_validation import validate_sku_canonical

logger = logging.getLogger(__name__)

# Tag embedded in the `note` field of every simulated event.
SIM_TAG = "SIM_HIST"


class HistorySimulationResult:
    """Summary of a simulation run."""

    def __init__(self):
        self.days_generated: int = 0
        self.total_sales_qty: int = 0
        self.orders_created: int = 0
        self.receipts_created: int = 0
        self.warnings: List[str] = []

    def __repr__(self) -> str:
        return (
            f"<HistorySimulation days={self.days_generated} "
            f"sales={self.total_sales_qty} "
            f"orders={self.orders_created} "
            f"receipts={self.receipts_created} "
            f"warnings={len(self.warnings)}>"
        )


class HistorySimulationWorkflow:
    """
    Orchestrates synthetic history generation for a single SKU.

    Each call to :meth:`run_for_sku` produces:
    - One SALE event per day in the period (qty sampled uniformly in [qty_min, qty_max]).
    - One SalesRecord per day written to sales.csv.
    - ORDER events when stock would run out (using a simplified reorder rule based
      on SKU lead_time + reorder_point to avoid re-running the full heavy proposal
      engine in a tight chronological loop).
    - RECEIPT events lead_time_days after each ORDER.

    Each simulation note embeds ``SIM_HIST`` so reruns can cleanly overwrite data.
    """

    # Prefix used in order_id and document_id to identify simulation artefacts.
    SIM_ORDER_ID_PREFIX = "SIMHIST"
    SIM_DOC_ID_PREFIX = "SIMHIST_DOC"

    def __init__(self, csv_layer: CSVLayer):
        self.csv_layer = csv_layer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_for_sku(
        self,
        sku_code: str,
        qty_min: int,
        qty_max: int,
        n_days: int,
        end_date: date,
        random_seed: Optional[int] = None,
    ) -> HistorySimulationResult:
        """
        Generate n_days of synthetic history ending on end_date (inclusive).

        Args:
            sku_code: Canonical SKU code.
            qty_min: Min daily sales qty (inclusive).
            qty_max: Max daily sales qty (inclusive).
            n_days: Number of calendar days to simulate.
            end_date: Last day of the simulated period (normally today).
            random_seed: Optional seed for reproducibility (deterministic when set).

        Returns:
            HistorySimulationResult summary.

        Raises:
            ValueError: If input validation fails.
        """
        # --- Validation ---
        validate_sku_canonical(sku_code, context="simulazione storico")
        if qty_min < 0:
            raise ValueError("qty_min non può essere negativo")
        if qty_max < qty_min:
            raise ValueError("qty_max deve essere >= qty_min")
        if n_days < 1:
            raise ValueError("n_days deve essere >= 1")

        # Check SKU exists
        skus = self.csv_layer.read_skus()
        sku_obj: Optional[SKU] = next((s for s in skus if s.sku == sku_code), None)
        if sku_obj is None:
            raise ValueError(f"SKU '{sku_code}' non trovato nel sistema")

        start_date = end_date - timedelta(days=n_days - 1)
        result = HistorySimulationResult()

        logger.info(
            f"[SIM_HIST] Starting for SKU={sku_code} period={start_date}..{end_date} "
            f"qty_range=[{qty_min},{qty_max}] seed={random_seed}"
        )
        self.csv_layer.log_audit(
            operation="SIM_HIST_START",
            details=(
                f"Simulazione storico: SKU={sku_code} "
                f"periodo={start_date}..{end_date} "
                f"qty_range=[{qty_min},{qty_max}] "
                f"seed={random_seed}"
            ),
            sku=sku_code,
        )

        # --- Step 1: Purge previous simulation data for this SKU+period ---
        self._purge_previous_simulation(sku_code, start_date, end_date)

        # --- Step 2: Run chronological simulation ---
        rng = random.Random(random_seed)
        lead_time = sku_obj.lead_time_days if sku_obj.lead_time_days > 0 else 7
        reorder_point = sku_obj.reorder_point if sku_obj.reorder_point >= 0 else 10

        # In-memory ledger accumulator (not persisted yet; we batch-write at end).
        sim_transactions: List[Transaction] = []
        sim_sales: List[SalesRecord] = []

        # Pending orders: list of (order_date, receipt_date, order_id, qty)
        pending_orders: List[Tuple[date, date, str, int]] = []
        order_counter = 0

        # Simulated on_hand and on_order tracked in memory
        on_hand = 0
        on_order = 0

        for day_offset in range(n_days):
            current_day = start_date + timedelta(days=day_offset)

            # Receive any orders due today
            for (o_date, r_date, o_id, o_qty) in list(pending_orders):
                if r_date == current_day:
                    receipt_txn = Transaction(
                        date=current_day,
                        sku=sku_code,
                        event=EventType.RECEIPT,
                        qty=o_qty,
                        receipt_date=current_day,
                        note=f"{SIM_TAG}|doc={self.SIM_DOC_ID_PREFIX}_{o_id}|order={o_id}",
                    )
                    sim_transactions.append(receipt_txn)
                    on_order = max(0, on_order - o_qty)
                    on_hand += o_qty
                    pending_orders.remove((o_date, r_date, o_id, o_qty))
                    result.receipts_created += 1
                    logger.debug(
                        f"[SIM_HIST] {current_day}: RECEIPT qty={o_qty} "
                        f"on_hand={on_hand} on_order={on_order}"
                    )

            # Daily sales
            qty_sold = rng.randint(qty_min, qty_max)
            actual_sold = min(qty_sold, on_hand)  # never sell more than on_hand
            if actual_sold < qty_sold:
                result.warnings.append(
                    f"{current_day}: OOS parziale — richiesti {qty_sold}, "
                    f"disponibili {actual_sold}"
                )
            on_hand = max(0, on_hand - actual_sold)

            if actual_sold > 0:
                sale_txn = Transaction(
                    date=current_day,
                    sku=sku_code,
                    event=EventType.SALE,
                    qty=actual_sold,
                    note=f"{SIM_TAG}|vendita giornaliera simulata",
                )
                sim_transactions.append(sale_txn)
                sim_sales.append(SalesRecord(date=current_day, sku=sku_code, qty_sold=actual_sold))
                result.total_sales_qty += actual_sold

            result.days_generated += 1

            # Inventory Position = on_hand + on_order
            ip = on_hand + on_order

            # Reorder check: place order if IP <= reorder_point and no order already in flight
            # covering future needs (simple policy to avoid excessive orders in simulation).
            if ip <= reorder_point and not pending_orders:
                # Order enough to cover lead_time + review_period days at avg_daily
                avg_daily = (qty_min + qty_max) / 2.0
                review_period = sku_obj.review_period if sku_obj.review_period > 0 else 7
                target_s = avg_daily * (lead_time + review_period) + sku_obj.safety_stock
                order_qty = max(1, int(target_s - ip))

                # Apply MOQ and pack_size
                pack_size = sku_obj.pack_size if sku_obj.pack_size >= 1 else 1
                moq = sku_obj.moq if sku_obj.moq >= 1 else 1
                if pack_size > 1:
                    order_qty = max(pack_size, ((order_qty + pack_size - 1) // pack_size) * pack_size)
                order_qty = max(order_qty, moq)
                order_qty = min(order_qty, sku_obj.max_stock)

                receipt_date = current_day + timedelta(days=lead_time)
                order_counter += 1
                order_id = f"{self.SIM_ORDER_ID_PREFIX}_{sku_code}_{current_day.isoformat()}_{order_counter:03d}"

                order_txn = Transaction(
                    date=current_day,
                    sku=sku_code,
                    event=EventType.ORDER,
                    qty=order_qty,
                    receipt_date=receipt_date,
                    note=f"{SIM_TAG}|order_id={order_id}",
                )
                sim_transactions.append(order_txn)
                pending_orders.append((current_day, receipt_date, order_id, order_qty))
                on_order += order_qty
                result.orders_created += 1
                logger.debug(
                    f"[SIM_HIST] {current_day}: ORDER qty={order_qty} "
                    f"receipt={receipt_date} ip_before={ip}"
                )

        # --- Step 3: Write order_logs for traceability ---
        # We write minimal order log entries so order pipeline and receiving_logs stay consistent.
        self._write_sim_order_logs(sku_code, sim_transactions, start_date, end_date)

        # --- Step 4: Write receiving_logs for simulated receipts ---
        self._write_sim_receiving_logs(sku_code, sim_transactions)

        # --- Step 5: Persist transactions to ledger (batch append) ---
        if sim_transactions:
            self.csv_layer.write_transactions_batch(sim_transactions)
        logger.info(f"[SIM_HIST] Wrote {len(sim_transactions)} transactions to ledger")

        # --- Step 6: Replace sales.csv rows for this SKU+period ---
        self._write_sim_sales(sku_code, start_date, end_date, sim_sales)

        self.csv_layer.log_audit(
            operation="SIM_HIST_DONE",
            details=(
                f"Simulazione storico completata: SKU={sku_code} "
                f"giorni={result.days_generated} "
                f"vendite_tot={result.total_sales_qty} "
                f"ordini={result.orders_created} "
                f"ricezioni={result.receipts_created} "
                f"warning={len(result.warnings)}"
            ),
            sku=sku_code,
        )
        logger.info(f"[SIM_HIST] Done: {result}")
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _purge_previous_simulation(
        self, sku_code: str, start_date: date, end_date: date
    ) -> None:
        """
        Remove all previous SIM_HIST transactions for sku_code within [start_date, end_date].
        Also removes associated order_log and receiving_log entries (by SIM prefix).
        """
        # --- Transactions ---
        all_txns = self.csv_layer.read_transactions()
        kept_txns = []
        purged = 0
        for txn in all_txns:
            is_sim = txn.note and SIM_TAG in txn.note
            in_period = start_date <= txn.date <= end_date
            if txn.sku == sku_code and is_sim and in_period:
                purged += 1
            else:
                kept_txns.append(txn)
        if purged:
            self.csv_layer.overwrite_transactions(kept_txns)
            logger.info(f"[SIM_HIST] Purged {purged} previous simulation transactions for {sku_code}")

        # --- Order logs ---
        order_logs = self.csv_layer.read_order_logs()
        kept_orders = [
            row for row in order_logs
            if not (
                row.get("sku") == sku_code
                and str(row.get("order_id", "")).startswith(self.SIM_ORDER_ID_PREFIX)
                and self._log_date_in_period(row.get("date", ""), start_date, end_date)
            )
        ]
        if len(kept_orders) < len(order_logs):
            # Rewrite order_logs without purged entries (use internal CSV write)
            self._rewrite_order_logs(kept_orders)

        # --- Receiving logs ---
        recv_logs = self.csv_layer.read_receiving_logs()
        kept_recv = [
            row for row in recv_logs
            if not (
                row.get("sku") == sku_code
                and str(row.get("document_id", "")).startswith(self.SIM_DOC_ID_PREFIX)
                and self._log_date_in_period(row.get("receipt_date", ""), start_date, end_date)
            )
        ]
        if len(kept_recv) < len(recv_logs):
            self._rewrite_receiving_logs(kept_recv)

    def _log_date_in_period(self, date_str: str, start_date: date, end_date: date) -> bool:
        """Return True if date_str parses to a date within [start_date, end_date]."""
        try:
            d = date.fromisoformat(date_str)
            return start_date <= d <= end_date
        except (ValueError, TypeError):
            return False

    def _write_sim_order_logs(
        self,
        sku_code: str,
        sim_transactions: List[Transaction],
        start_date: date,
        end_date: date,
    ) -> None:
        """Write minimal order_log rows for simulated ORDER events."""
        for txn in sim_transactions:
            if txn.event != EventType.ORDER:
                continue
            if not (txn.note and SIM_TAG in txn.note):
                continue
            # Extract order_id from note field
            order_id = self._extract_note_value(txn.note, "order_id")
            if not order_id:
                continue
            self.csv_layer.write_order_log(
                order_id=order_id,
                date_str=txn.date.isoformat(),
                sku=sku_code,
                qty=txn.qty,
                status="RECEIVED",  # Mark as already received (simulation is complete past data)
                receipt_date=txn.receipt_date.isoformat() if txn.receipt_date else None,
                qty_received=txn.qty,
            )

    def _write_sim_receiving_logs(
        self,
        sku_code: str,
        sim_transactions: List[Transaction],
    ) -> None:
        """Write receiving_log rows for simulated RECEIPT events."""
        for txn in sim_transactions:
            if txn.event != EventType.RECEIPT:
                continue
            if not (txn.note and SIM_TAG in txn.note):
                continue
            doc_id = self._extract_note_value(txn.note, "doc")
            order_id = self._extract_note_value(txn.note, "order")
            if not doc_id:
                continue
            self.csv_layer.write_receiving_log(
                document_id=doc_id,
                date_str=txn.date.isoformat(),
                sku=sku_code,
                qty=txn.qty,
                receipt_date=txn.date.isoformat(),
                order_ids=order_id or "",
            )

    def _write_sim_sales(
        self,
        sku_code: str,
        start_date: date,
        end_date: date,
        sim_sales: List[SalesRecord],
    ) -> None:
        """
        Replace all sales.csv rows for sku_code in [start_date, end_date]
        with simulated data, preserving rows outside the period.
        """
        existing_sales = self.csv_layer.read_sales()
        # Keep sales outside the period for this SKU and all other SKUs
        kept = [
            s for s in existing_sales
            if not (s.sku == sku_code and start_date <= s.date <= end_date)
        ]
        merged = kept + sim_sales
        # Sort chronologically for readability
        merged.sort(key=lambda s: (s.date, s.sku))
        self.csv_layer.write_sales(merged)
        logger.info(
            f"[SIM_HIST] Replaced {len(sim_sales)} sales records for "
            f"{sku_code} in period {start_date}..{end_date}"
        )

    @staticmethod
    def _extract_note_value(note: str, key: str) -> str:
        """
        Extract a key=value pair from a pipe-separated note string.

        Example note: "SIM_HIST|order_id=SIMHIST_0001_001|other"
        _extract_note_value(note, "order_id") -> "SIMHIST_0001_001"
        """
        for part in note.split("|"):
            part = part.strip()
            if part.startswith(f"{key}="):
                return part[len(f"{key}="):]
        return ""

    def _rewrite_order_logs(self, rows: list) -> None:
        """Overwrite order_logs.csv with given rows via CSVLayer internal write."""
        # CSVLayer exposes _write_csv for internal use; use it atomically.
        schema_keys = self.csv_layer.SCHEMAS.get("order_logs.csv", [])
        if not schema_keys:
            # fallback: derive from first row
            schema_keys = list(rows[0].keys()) if rows else []
        self.csv_layer._write_csv("order_logs.csv", rows)

    def _rewrite_receiving_logs(self, rows: list) -> None:
        """Overwrite receiving_logs.csv with given rows via CSVLayer internal write."""
        self.csv_layer._write_csv("receiving_logs.csv", rows)
