"""
Receiving workflow: closure of received orders and idempotent updates.
"""
from datetime import date
from typing import List, Dict, Tuple
import hashlib

from ..domain.models import Transaction, EventType
from ..persistence.csv_layer import CSVLayer


class ReceivingWorkflow:
    """Receiving management: close receipts idempotently."""
    
    def __init__(self, csv_layer: CSVLayer):
        """
        Initialize receiving workflow.
        
        Args:
            csv_layer: CSV persistence layer
        """
        self.csv_layer = csv_layer
    
    @staticmethod
    def generate_receipt_id(receipt_date: date, origin: str, sku: str) -> str:
        """
        Generate deterministic receipt_id.
        
        Format: {receipt_date}_{origin_hash}_{sku}
        
        Args:
            receipt_date: Date of receipt
            origin: Supplier/origin identifier
            sku: SKU identifier
        
        Returns:
            Deterministic receipt ID
        """
        origin_hash = hashlib.md5(origin.encode()).hexdigest()[:8]
        return f"{receipt_date.isoformat()}_{origin_hash}_{sku}"
    
    def close_receipt(
        self,
        receipt_id: str,
        receipt_date: date,
        sku_quantities: Dict[str, int],  # {sku: qty_received}
        notes: str = "",
    ) -> Tuple[List[Transaction], bool]:
        """
        Close a receipt and update ledger idempotently.
        
        NEW BEHAVIOR: Auto-creates UNFULFILLED events for unshipped quantities.
        - Compares qty_received vs qty_ordered (from order_logs)
        - If qty_received < qty_ordered: creates UNFULFILLED event for difference
        - UNFULFILLED reduces on_order (preventing indefinite "in order" status)
        
        Idempotency strategy:
        - Check if receipt_id already exists in receiving_logs.csv
        - If yes: skip (receipt already processed) → return empty txns, True
        - If no: create RECEIPT events (+ UNFULFILLED if needed), write logs → return txns, False
        
        Args:
            receipt_id: Unique receipt identifier
            receipt_date: Date of receipt
            sku_quantities: Dict {sku: qty_received}
            notes: Optional notes
        
        Returns:
            (transactions_to_write, already_processed)
        """
        # Check if receipt already processed
        existing_logs = self.csv_layer.read_receiving_logs()
        already_received = any(
            log.get("receipt_id") == receipt_id
            for log in existing_logs
        )
        
        if already_received:
            # Receipt already processed; idempotent return
            return [], True
        
        today = date.today()
        transactions = []
        
        # Read order_logs to get qty_ordered for each SKU
        order_logs = self.csv_layer.read_order_logs()
        qty_ordered_map = {}  # {sku: total_qty_ordered}
        
        for log in order_logs:
            # Filter orders matching this receipt (same date range or explicit link)
            # For simplicity: sum all "pending" orders for each SKU
            sku_log = log.get("sku", "")
            qty_log = int(log.get("qty_ordered", 0))
            status = log.get("status", "")
            
            if status == "pending":  # Only consider pending orders
                qty_ordered_map[sku_log] = qty_ordered_map.get(sku_log, 0) + qty_log
        
        # Create RECEIPT events + UNFULFILLED events for each SKU
        for sku, qty_received in sku_quantities.items():
            # RECEIPT event
            txn_receipt = Transaction(
                date=today,
                sku=sku,
                event=EventType.RECEIPT,
                qty=qty_received,
                receipt_date=receipt_date,
                note=f"Receipt {receipt_id}; {notes}".strip(),
            )
            transactions.append(txn_receipt)
            
            # Check for unfulfilled quantity
            qty_ordered = qty_ordered_map.get(sku, 0)
            qty_unfulfilled = qty_ordered - qty_received
            
            # Only create UNFULFILLED if:
            # 1. There was an actual order (qty_ordered > 0)
            # 2. Received less than ordered (qty_unfulfilled > 0)
            # 3. Protection: never create UNFULFILLED > qty_ordered
            if qty_ordered > 0 and qty_unfulfilled > 0:
                # Create UNFULFILLED event
                txn_unfulfilled = Transaction(
                    date=today,
                    sku=sku,
                    event=EventType.UNFULFILLED,
                    qty=min(qty_unfulfilled, qty_ordered),  # Safety cap
                    note=f"Auto-generated for receipt {receipt_id}; qty_ordered={qty_ordered}, qty_received={qty_received}",
                )
                transactions.append(txn_unfulfilled)
        
        # Write transactions to ledger
        if transactions:
            self.csv_layer.write_transactions_batch(transactions)
        
        # Write to receiving_logs for idempotency tracking
        for sku, qty_received in sku_quantities.items():
            self.csv_layer.write_receiving_log(
                receipt_id=receipt_id,
                date_str=today.isoformat(),
                sku=sku,
                qty=qty_received,
                receipt_date=receipt_date.isoformat(),
            )
        
        return transactions, False


class ExceptionWorkflow:
    """Quick entry for exceptions: WASTE, ADJUST, UNFULFILLED."""
    
    def __init__(self, csv_layer: CSVLayer):
        """
        Initialize exception workflow.
        
        Args:
            csv_layer: CSV persistence layer
        """
        self.csv_layer = csv_layer
    
    @staticmethod
    def generate_exception_key(event_date: date, sku: str, event_type: EventType) -> str:
        """
        Generate idempotency key for exception.
        
        Args:
            event_date: Date of exception
            sku: SKU identifier
            event_type: Exception event type
        
        Returns:
            Idempotency key
        """
        return f"{event_date.isoformat()}_{sku}_{event_type.value}"
    
    def record_exception(
        self,
        event_type: EventType,
        sku: str,
        qty: int,
        event_date: date = None,
        notes: str = "",
    ) -> Tuple[Transaction, bool]:
        """
        Record an exception (WASTE, ADJUST, UNFULFILLED).
        
        Idempotency:
        - Check if exception key already exists
        - If yes: skip (already recorded) → return txn, True
        - If no: write transaction → return txn, False
        
        Args:
            event_type: EventType (WASTE, ADJUST, UNFULFILLED)
            sku: SKU identifier
            qty: Quantity (signed for ADJUST)
            event_date: Event date (defaults to today)
            notes: Optional notes
        
        Returns:
            (transaction, already_recorded)
        """
        if event_type not in [EventType.WASTE, EventType.ADJUST, EventType.UNFULFILLED]:
            raise ValueError(f"Invalid exception type: {event_type}")
        
        event_date = event_date or date.today()
        exception_key = self.generate_exception_key(event_date, sku, event_type)
        
        # Check if already recorded
        existing_txns = self.csv_layer.read_transactions()
        already_recorded = any(
            t.date == event_date and t.sku == sku and t.event == event_type
            for t in existing_txns
        )
        
        txn = Transaction(
            date=event_date,
            sku=sku,
            event=event_type,
            qty=qty,
            note=f"{exception_key}; {notes}".strip(),
        )
        
        if not already_recorded:
            self.csv_layer.write_transaction(txn)
        
        return txn, already_recorded
    
    def revert_exception_day(
        self,
        event_date: date,
        sku: str,
        event_type: EventType,
    ) -> int:
        """
        Revert all exceptions of a type for a SKU on a specific date.
        
        Implementation: re-write transactions.csv excluding matching entries.
        
        Args:
            event_date: Date to target
            sku: SKU identifier
            event_type: Exception type to revert
        
        Returns:
            Number of reverted entries
        """
        existing_txns = self.csv_layer.read_transactions()
        
        # Filter: keep all except matching exceptions
        filtered_txns = [
            t for t in existing_txns
            if not (t.date == event_date and t.sku == sku and t.event == event_type)
        ]
        
        reverted_count = len(existing_txns) - len(filtered_txns)
        
        if reverted_count > 0:
            self.csv_layer.write_transactions_batch(filtered_txns)
        
        return reverted_count
