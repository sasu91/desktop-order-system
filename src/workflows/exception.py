"""
Exception workflow: WASTE, ADJUST, UNFULFILLED handling.
"""
from datetime import date
from typing import Tuple

from ..domain.models import Transaction, EventType
from ..persistence.csv_layer import CSVLayer

class ExceptionWorkflow:
    """Exception workflow for WASTE, ADJUST, UNFULFILLED handling."""
    
    def __init__(self, csv_layer: CSVLayer):
        """
        Initialize the workflow.
        
        Args:
            csv_layer: CSV layer for data persistence
        """
        self.csv_layer = csv_layer
    
    def record_exception(self, event_type: EventType, sku: str, qty: int, event_date: date, notes: str) -> Tuple[Transaction, bool]:
        """
        Record an exception.
        
        Args:
            event_type: Type of exception (WASTE, ADJUST, UNFULFILLED)
            sku: SKU code
            qty: Quantity
            event_date: Date of the event
            notes: Notes
            
        Returns:
            Tuple[Transaction, bool]: (Transaction object, True if already recorded today)
        """
        # Check if already recorded today
        today = date.today()
        existing_txns = self.csv_layer.read_transactions()
        for txn in existing_txns:
            if txn.event == event_type and txn.sku == sku and txn.date == today:
                return txn, True
        
        # Create new transaction
        txn = Transaction(
            event=event_type,
            sku=sku,
            qty=qty,
            date=event_date,
            notes=notes,
        )
        self.csv_layer.write_transaction(txn)
        
        return txn, False
    
    def revert_exception_day(self, event_date: date, sku: str, event_type: EventType) -> int:
        """
        Revert all exceptions of a specific type for a SKU on a specific day.
        
        Args:
            event_date: Date of the event
            sku: SKU code
            event_type: Type of exception
            
        Returns:
            int: Number of exceptions reverted
        """
        # Read all transactions
        all_txns = self.csv_layer.read_transactions()
        reverted_count = 0
        for txn in all_txns:
            if txn.event == event_type and txn.sku == sku and txn.date == event_date:
                self.csv_layer.delete_transaction(txn)
                reverted_count += 1
        
        return reverted_count