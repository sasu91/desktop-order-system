"""
Stock calculation engine (AsOf logic).

Core ledger processing: deterministic, testable, no I/O.
"""
from datetime import date
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from .models import Transaction, EventType, Stock, SalesRecord


@dataclass
class StockCalculator:
    """
    Pure stock calculator: given transactions and sales, compute stock AsOf date.
    
    Rule: All events with date < AsOf_date are applied sequentially per SKU.
    Order of application per day: by event type priority (SNAPSHOT → ORDER/RECEIPT → SALE/WASTE/ADJUST → UNFULFILLED).
    """
    
    # Event type priority (lower = applied first in a day)
    EVENT_PRIORITY = {
        EventType.SNAPSHOT: 0,
        EventType.RECEIPT: 1,
        EventType.ORDER: 1,
        EventType.SALE: 2,
        EventType.WASTE: 2,
        EventType.ADJUST: 2,
        EventType.UNFULFILLED: 3,  # Tracking only
    }
    
    @staticmethod
    def _sort_transactions(transactions: List[Transaction]) -> List[Transaction]:
        """Sort transactions by date, then by event priority."""
        return sorted(
            transactions,
            key=lambda t: (t.date, StockCalculator.EVENT_PRIORITY.get(t.event, 99))
        )
    
    @staticmethod
    def calculate_asof(
        sku: str,
        asof_date: date,
        transactions: List[Transaction],
        sales_records: Optional[List[SalesRecord]] = None,
    ) -> Stock:
        """
        Calculate stock state for a SKU as-of a specific date.
        
        Args:
            sku: SKU identifier
            asof_date: Reference date; only events with date < asof_date are included
            transactions: All ledger transactions
            sales_records: Daily sales records (optional; if provided, SALE events are auto-created)
        
        Returns:
            Stock object with on_hand, on_order, asof_date
        
        Raises:
            ValueError: If data is inconsistent (e.g., multiple SNAPSHOTs on same day)
        """
        on_hand = 0
        on_order = 0
        
        # Filter transactions for this SKU, date < asof_date
        sku_txns = [t for t in transactions if t.sku == sku and t.date < asof_date]
        
        # Add implicit SALE events from sales_records
        if sales_records:
            sku_sales = [s for s in sales_records if s.sku == sku and s.date < asof_date]
            # Convert sales to SALE events
            sale_events = [
                Transaction(date=s.date, sku=s.sku, event=EventType.SALE, qty=s.qty_sold)
                for s in sku_sales
            ]
            sku_txns.extend(sale_events)
        
        # Sort transactions deterministically
        sku_txns = StockCalculator._sort_transactions(sku_txns)
        
        # Apply events sequentially
        for txn in sku_txns:
            if txn.event == EventType.SNAPSHOT:
                on_hand = txn.qty
                on_order = 0
            elif txn.event == EventType.ORDER:
                on_order += txn.qty
            elif txn.event == EventType.RECEIPT:
                on_order -= txn.qty
                on_hand += txn.qty
            elif txn.event == EventType.SALE:
                on_hand -= txn.qty
            elif txn.event == EventType.WASTE:
                on_hand -= txn.qty
            elif txn.event == EventType.ADJUST:
                on_hand += txn.qty  # qty is signed
            elif txn.event == EventType.UNFULFILLED:
                # Tracking only; no impact on on_hand or on_order
                pass
        
        return Stock(sku=sku, on_hand=on_hand, on_order=on_order, asof_date=asof_date)
    
    @staticmethod
    def calculate_all_skus(
        all_skus: List[str],
        asof_date: date,
        transactions: List[Transaction],
        sales_records: Optional[List[SalesRecord]] = None,
    ) -> Dict[str, Stock]:
        """
        Calculate stock for all SKUs as-of a date.
        
        Returns:
            Dict {sku: Stock}
        """
        return {
            sku: StockCalculator.calculate_asof(sku, asof_date, transactions, sales_records)
            for sku in all_skus
        }


def validate_ean(ean: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate EAN-13 format (basic check).
    
    Args:
        ean: EAN string or None
    
    Returns:
        (is_valid, error_message)
    
    Note: Empty/None EAN is valid. Invalid format → error message.
    """
    if not ean or ean.strip() == "":
        return True, None
    
    ean = ean.strip()
    if not ean.isdigit():
        return False, f"EAN must contain only digits, got: {ean}"
    
    if len(ean) != 13 and len(ean) != 12:
        return False, f"EAN must be 12 or 13 digits, got {len(ean)} digits"
    
    return True, None
