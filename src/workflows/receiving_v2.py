"""
Enhanced receiving workflow with order-document traceability.

Key improvements:
- Document-based idempotency (DDT/Invoice numbers)
- Granular order-receipt mapping
- Accurate unfulfilled tracking per order
- Atomic writes with auto-backup
"""
from datetime import date
from typing import List, Dict, Tuple, Optional, Any
import hashlib
import logging

from ..domain.models import Transaction, EventType
from ..persistence.csv_layer import CSVLayer

logger = logging.getLogger(__name__)


class ReceivingWorkflow:
    """Receiving management with enhanced traceability."""
    
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
        Generate deterministic receipt_id (legacy compatibility).
        
        NOTE: Prefer using document_id (DDT/Invoice number) for new implementations.
        
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
    
    def close_receipt_by_document(
        self,
        document_id: str,
        receipt_date: date,
        items: List[Dict[str, Any]],  # [{sku, qty_received, order_ids: Optional[List[str]]}]
        notes: str = "",
    ) -> Tuple[List[Transaction], bool, Dict[str, Dict]]:
        """
        Close receipt using document ID (DDT/Invoice) for idempotency.
        
        NEW BEHAVIOR:
        - Document-based idempotency: prevents duplicate processing of same DDT/Invoice
        - Order-level tracking: links received quantities to specific orders
        - Automatic FIFO allocation: if order_ids not specified, allocates to oldest PENDING orders
        - Partial fulfillment: updates order status to PARTIAL if incomplete
        - Unfulfilled generation: creates UNFULFILLED events for residuals
        
        Args:
            document_id: Document identifier (e.g., "DDT-2026-001", "INV-12345")
            receipt_date: Date of receipt
            items: List of items received:
                - sku: SKU identifier
                - qty_received: Quantity received
                - order_ids: (Optional) Specific orders to allocate to. If None, uses FIFO.
            notes: Optional notes
        
        Returns:
            (transactions, already_processed, order_updates)
            - transactions: List of RECEIPT/UNFULFILLED events created
            - already_processed: True if document already processed (idempotent skip)
            - order_updates: {order_id: {qty_received_total, new_status, sku}}
        
        Example:
            items = [
                {"sku": "SKU001", "qty_received": 50, "order_ids": ["20260201_001"]},
                {"sku": "SKU002", "qty_received": 30},  # FIFO allocation
            ]
            txns, skip, updates = workflow.close_receipt_by_document(
                "DDT-2026-012", date(2026, 2, 10), items
            )
        """
        # 1. Check idempotency: document already processed?
        existing_logs = self.csv_layer.read_receiving_logs()
        document_exists = any(
            log.get("document_id") == document_id or log.get("receipt_id") == document_id
            for log in existing_logs
        )
        
        if document_exists:
            logger.info(f"Document {document_id} already processed (idempotent skip)")
            return [], True, {}
        
        # 2. Read current order state
        order_logs = self.csv_layer.read_order_logs()
        
        transactions = []
        order_updates = {}  # {order_id: {qty_received_total, new_status, sku}}
        
        # 3. Process each item
        for item in items:
            sku = item["sku"]
            qty_received = item["qty_received"]
            specified_order_ids = item.get("order_ids", [])
            
            # Get PENDING orders for this SKU (sorted by date for FIFO)
            sku_orders = [
                log for log in order_logs
                if log.get("sku") == sku and log.get("status") in ["PENDING", "PARTIAL"]
            ]
            sku_orders.sort(key=lambda x: x.get("date", ""))
            
            # Determine which orders to allocate to
            if specified_order_ids:
                # Use specified orders
                target_orders = [o for o in sku_orders if o.get("order_id") in specified_order_ids]
                if not target_orders:
                    logger.warning(f"Document {document_id}: specified order_ids {specified_order_ids} not found for {sku}")
                    # Fallback to FIFO
                    target_orders = sku_orders
            else:
                # FIFO allocation
                target_orders = sku_orders
            
            if not target_orders:
                logger.warning(f"Document {document_id}: No PENDING/PARTIAL orders found for {sku}, qty_received={qty_received}")
                # Still create RECEIPT event (might be manual stock in)
                txn_receipt = Transaction(
                    date=receipt_date,
                    sku=sku,
                    event=EventType.RECEIPT,
                    qty=qty_received,
                    receipt_date=receipt_date,
                    note=f"Document {document_id} (no matching orders); {notes}".strip(),
                )
                transactions.append(txn_receipt)
                
                # Log to receiving_logs without order linkage
                self.csv_layer.write_receiving_log(
                    document_id=document_id,
                    date_str=date.today().isoformat(),
                    sku=sku,
                    qty=qty_received,
                    receipt_date=receipt_date.isoformat(),
                    order_ids="",
                )
                continue
            
            # Allocate qty_received to orders (FIFO)
            qty_remaining = qty_received
            allocated_order_ids = []
            
            for order in target_orders:
                if qty_remaining <= 0:
                    break
                
                order_id = order.get("order_id", "")
                qty_ordered = int(order.get("qty_ordered", 0))
                qty_already_received = int(order.get("qty_received", 0))
                qty_still_needed = qty_ordered - qty_already_received
                
                if qty_still_needed <= 0:
                    continue  # Order already fully received
                
                # Allocate up to qty_still_needed
                qty_to_allocate = min(qty_remaining, qty_still_needed)
                new_qty_received_total = qty_already_received + qty_to_allocate
                
                # Determine new status
                if new_qty_received_total >= qty_ordered:
                    new_status = "RECEIVED"
                elif new_qty_received_total > 0:
                    new_status = "PARTIAL"
                else:
                    new_status = "PENDING"
                
                # Update order state
                order_updates[order_id] = {
                    "qty_received_total": new_qty_received_total,
                    "new_status": new_status,
                    "sku": sku,
                    "qty_ordered": qty_ordered,
                }
                
                allocated_order_ids.append(order_id)
                qty_remaining -= qty_to_allocate
                
                logger.info(
                    f"Document {document_id}: Allocated {qty_to_allocate} of {sku} to order {order_id} "
                    f"(total received: {new_qty_received_total}/{qty_ordered}, status: {new_status})"
                )
            
            # Create RECEIPT transaction
            txn_receipt = Transaction(
                date=receipt_date,
                sku=sku,
                event=EventType.RECEIPT,
                qty=qty_received,
                receipt_date=receipt_date,
                note=f"Document {document_id}, Orders: {','.join(allocated_order_ids)}; {notes}".strip(),
            )
            transactions.append(txn_receipt)
            
            # If qty_remaining > 0 after allocation, log warning
            if qty_remaining > 0:
                logger.warning(
                    f"Document {document_id}: Received {qty_remaining} extra units of {sku} "
                    f"beyond pending orders (may be overstock or unplanned)"
                )
            
            # Check for unfulfilled residuals (orders closed without full receipt)
            for order_id, update in order_updates.items():
                if update["new_status"] == "RECEIVED" and update["qty_received_total"] < update["qty_ordered"]:
                    qty_unfulfilled = update["qty_ordered"] - update["qty_received_total"]
                    
                    txn_unfulfilled = Transaction(
                        date=receipt_date,
                        sku=sku,
                        event=EventType.UNFULFILLED,
                        qty=qty_unfulfilled,
                        note=f"Auto-generated for order {order_id} closed by document {document_id}; "
                             f"ordered={update['qty_ordered']}, received={update['qty_received_total']}",
                    )
                    transactions.append(txn_unfulfilled)
                    logger.warning(
                        f"Order {order_id}: Closed with unfulfilled qty={qty_unfulfilled} "
                        f"(ordered={update['qty_ordered']}, received={update['qty_received_total']})"
                    )
            
            # Write to receiving_logs
            self.csv_layer.write_receiving_log(
                document_id=document_id,
                date_str=date.today().isoformat(),
                sku=sku,
                qty=qty_received,
                receipt_date=receipt_date.isoformat(),
                order_ids=",".join(allocated_order_ids),
            )
        
        # 4. Write transactions to ledger (batch)
        if transactions:
            self.csv_layer.write_transactions_batch(transactions)
        
        # 5. Update order_logs with new qty_received and status
        for order_id, update in order_updates.items():
            self.csv_layer.update_order_received_qty(
                order_id=order_id,
                qty_received=update["qty_received_total"],
                status=update["new_status"],
            )
        
        logger.info(
            f"Document {document_id} processed: {len(transactions)} transactions, "
            f"{len(order_updates)} orders updated"
        )
        
        return transactions, False, order_updates
    
    def close_receipt(
        self,
        receipt_id: str,
        receipt_date: date,
        sku_quantities: Dict[str, int],
        notes: str = "",
    ) -> Tuple[List[Transaction], bool]:
        """
        Legacy receipt closure method (DEPRECATED).
        
        WARNING: This method uses aggregated SKU-level logic without order traceability.
        Use close_receipt_by_document() for better tracking.
        
        Kept for backward compatibility only.
        """
        logger.warning(
            f"close_receipt() is deprecated. Use close_receipt_by_document() "
            f"for better order traceability (receipt_id={receipt_id})"
        )
        
        # Convert to new format
        items = [
            {"sku": sku, "qty_received": qty}
            for sku, qty in sku_quantities.items()
        ]
        
        txns, already_processed, _ = self.close_receipt_by_document(
            document_id=receipt_id,
            receipt_date=receipt_date,
            items=items,
            notes=notes,
        )
        
        return txns, already_processed


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
        event_date: Optional[date] = None,
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
            logger.info(f"Exception recorded: {event_type.value} for {sku}, qty={qty}, date={event_date}")
        else:
            logger.info(f"Exception already recorded (idempotent skip): {exception_key}")
        
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
            self.csv_layer.overwrite_transactions(filtered_txns)
            logger.info(
                f"Reverted {reverted_count} {event_type.value} exception(s) for {sku} on {event_date}"
            )
        else:
            logger.info(f"No exceptions found to revert for {sku} on {event_date}")
        
        return reverted_count
