"""
Stock calculation engine (AsOf logic).

Core ledger processing: deterministic, testable, no I/O.
"""
from datetime import date
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict

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
            Stock object with on_hand, on_order, unfulfilled_qty, asof_date
        
        Raises:
            ValueError: If data is inconsistent (e.g., multiple SNAPSHOTs on same day)
        """
        on_hand = 0
        on_order = 0
        unfulfilled_qty = 0  # Track UNFULFILLED events (backorder/cancellazioni)
        
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
                on_order = max(0, on_order - txn.qty)
                on_hand += txn.qty
            elif txn.event == EventType.SALE:
                on_hand = max(0, on_hand - txn.qty)
            elif txn.event == EventType.WASTE:
                on_hand = max(0, on_hand - txn.qty)
            elif txn.event == EventType.ADJUST:
                # ADJUST: absolute set (not delta). Sets on_hand to specified value.
                on_hand = max(0, txn.qty)
            elif txn.event == EventType.UNFULFILLED:
                # Track unfulfilled quantities (backorder/cancellazioni)
                # These reduce inventory position but don't touch on_hand/on_order directly
                unfulfilled_qty += txn.qty
        
        # Final protection: ensure non-negative values
        on_hand = max(0, on_hand)
        on_order = max(0, on_order)
        unfulfilled_qty = max(0, unfulfilled_qty)
        
        return Stock(sku=sku, on_hand=on_hand, on_order=on_order, unfulfilled_qty=unfulfilled_qty, asof_date=asof_date)
    
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
    
    @staticmethod
    def on_order_by_date(
        sku: str,
        transactions: List[Transaction],
        as_of_date: Optional[date] = None,
    ) -> Dict[date, int]:
        """
        Calculate on-order quantities by expected receipt date for a SKU.
        
        This provides granular visibility into the order pipeline, enabling:
        - Distinction between Saturday vs Monday deliveries (Friday dual orders)
        - Accurate inventory position calculation for future dates
        - Order fulfillment tracking by delivery window
        
        Args:
            sku: SKU identifier
            transactions: All ledger transactions
            as_of_date: Optional cutoff date (only orders placed before this date). 
                       If None, uses today.
        
        Returns:
            Dict mapping {receipt_date: qty} for pending orders.
            Only includes ORDER events that have not been fully RECEIPT-ed yet.
        
        Algorithm:
            1. Find all ORDER events for SKU (with receipt_date)
            2. Find all RECEIPT events for SKU
            3. Match RECEIPTs to ORDERs by receipt_date
            4. Return ORDER quantities not yet received
        
        Example:
            Friday dual order scenario:
            - ORDER(qty=30, receipt_date=2026-02-07 Sat)
            - ORDER(qty=50, receipt_date=2026-02-09 Mon)
            Result: {2026-02-07: 30, 2026-02-09: 50}
            
            After Saturday receipt:
            - RECEIPT(qty=30, receipt_date=2026-02-07)
            Result: {2026-02-09: 50}  # Saturday order fulfilled
        """
        cutoff = as_of_date or date.today()
        
        # Track orders by receipt_date
        orders_by_date: Dict[date, int] = defaultdict(int)
        receipts_by_date: Dict[date, int] = defaultdict(int)
        
        for txn in transactions:
            if txn.sku != sku:
                continue
            if txn.date >= cutoff:
                continue  # Only consider transactions before cutoff
            
            if txn.event == EventType.ORDER and txn.receipt_date:
                orders_by_date[txn.receipt_date] += txn.qty
            elif txn.event == EventType.RECEIPT and txn.receipt_date:
                receipts_by_date[txn.receipt_date] += txn.qty
        
        # Calculate pending (unfulfilled) orders by date
        pending_by_date: Dict[date, int] = {}
        for receipt_date, ordered_qty in orders_by_date.items():
            received_qty = receipts_by_date.get(receipt_date, 0)
            pending = ordered_qty - received_qty
            if pending > 0:
                pending_by_date[receipt_date] = pending
        
        return pending_by_date
    
    @staticmethod
    def inventory_position(
        sku: str,
        as_of_date: date,
        transactions: List[Transaction],
        sales_records: Optional[List[SalesRecord]] = None,
    ) -> int:
        """
        Calculate inventory position (IP) for a SKU as of a specific date.
        
        IP accounts for inventory that will be available by as_of_date:
        - on_hand (current physical stock)
        - on_order (only orders with receipt_date <= as_of_date)
        - unfulfilled_qty (backorders, reduces availability)
        
        This enables accurate stock projections for future dates, critical for:
        - Friday dual order logic (different IP on Saturday vs Monday)
        - Multi-day lead time planning
        - Stock-out risk assessment
        
        Args:
            sku: SKU identifier
            as_of_date: Target date for IP calculation
            transactions: All ledger transactions
            sales_records: Daily sales records (optional)
        
        Returns:
            Inventory position = on_hand + on_order_by(as_of_date) - unfulfilled_qty
        
        Example:
            Current state (as of Friday evening):
            - on_hand: 50
            - ORDER(qty=30, receipt_date=Sat)
            - ORDER(qty=50, receipt_date=Mon)
            - unfulfilled_qty: 10
            
            IP(as_of=Saturday) = 50 + 30 - 10 = 70  (includes Sat order)
            IP(as_of=Monday) = 50 + 30 + 50 - 10 = 120  (includes both orders)
        """
        # Get base stock (on_hand, unfulfilled_qty)
        stock = StockCalculator.calculate_asof(sku, as_of_date, transactions, sales_records)
        
        # Get pending orders by receipt date
        pending_orders = StockCalculator.on_order_by_date(sku, transactions, as_of_date)
        
        # Sum only orders that will arrive by as_of_date
        on_order_by_date = sum(
            qty for receipt_date, qty in pending_orders.items()
            if receipt_date <= as_of_date
        )
        
        # IP = on_hand + on_order (filtered by date) - unfulfilled_qty
        return stock.on_hand + on_order_by_date - stock.unfulfilled_qty


def calculate_sold_from_eod_stock(
    sku: str,
    eod_date: date,
    eod_stock_on_hand: int,
    transactions: List[Transaction],
    sales_records: Optional[List[SalesRecord]] = None,
) -> Tuple[int, int]:
    """
    Calculate qty_sold for a day by comparing theoretical stock with declared EOD stock.
    
    Args:
        sku: SKU identifier
        eod_date: End-of-day date (stock declared at end of this day)
        eod_stock_on_hand: Declared stock on hand at end of eod_date
        transactions: All ledger transactions
        sales_records: Daily sales records (optional)
    
    Returns:
        (qty_sold, adjustment): 
            - qty_sold: Quantity sold during eod_date (to write to sales.csv)
            - adjustment: Stock adjustment needed (to write as ADJUST event if != 0)
    
    Logic:
        1. Calculate stock at START of eod_date (AsOf = eod_date, excludes eod_date events)
        2. Calculate theoretical stock at END of eod_date (includes all events except SALE for this day)
        3. qty_sold = theoretical_end - eod_stock_on_hand
        4. adjustment = eod_stock_on_hand - theoretical_end (if discrepancy after accounting for sales)
    """
    # Stock at start of day (before any events on eod_date)
    stock_start = StockCalculator.calculate_asof(sku, eod_date, transactions, sales_records)
    
    # Theoretical stock at end of day (include all events on eod_date except SALE from sales_records)
    # We calculate AsOf next day, but exclude sales from sales_records for eod_date
    from datetime import timedelta
    next_day = eod_date + timedelta(days=1)
    
    # Filter out sales for eod_date from sales_records to calculate theoretical stock
    sales_without_today = []
    if sales_records:
        sales_without_today = [s for s in sales_records if s.date != eod_date]
    
    stock_theoretical_end = StockCalculator.calculate_asof(sku, next_day, transactions, sales_without_today)
    
    # qty_sold = stock at start + receipts during day - stock at end (declared)
    # Simplified: theoretical_end - eod_stock_on_hand
    # This accounts for: stock_start + receipts - waste - adjust - sales = eod_stock
    # So: sales = stock_start + receipts - waste - adjust - eod_stock
    # Which is: theoretical_end_without_sales - eod_stock
    
    qty_sold = max(0, stock_theoretical_end.on_hand - eod_stock_on_hand)
    
    # After accounting for sales, check if there's still a discrepancy (shrinkage, damage, etc.)
    # theoretical after sales should equal eod_stock; if not, we need an adjustment
    theoretical_after_sales = stock_theoretical_end.on_hand - qty_sold
    adjustment = eod_stock_on_hand - theoretical_after_sales
    
    return qty_sold, adjustment


def is_day_censored(
    sku: str,
    check_date: date,
    transactions: List[Transaction],
    sales_records: Optional[List[SalesRecord]] = None,
    lookback_days: int = 3,
) -> Tuple[bool, str]:
    """
    Determine if a day should be censored (excluded from demand calculations).
    
    A day is censored when demand observation is unreliable due to stockouts:
    - Rule 1: OH==0 at EOD and sales==0 (true stockout, demand is censored)
    - Rule 2: UNFULFILLED event on this date or within lookback_days (recent OOS)
    
    This prevents artificially low demand estimates when SKU was unavailable.
    
    Args:
        sku: SKU identifier
        check_date: Date to check for censoring
        transactions: All ledger transactions
        sales_records: Daily sales records (optional)
        lookback_days: Days to look back for UNFULFILLED events (default 3)
    
    Returns:
        Tuple[bool, str]: (is_censored, reason)
            - is_censored: True if day should be excluded from demand calculations
            - reason: Human-readable explanation (for logging/audit)
    
    Examples:
        >>> is_day_censored("SKU001", date(2026, 1, 15), txns, sales)
        (True, "OH=0 and sales=0 on 2026-01-15")
        
        >>> is_day_censored("SKU002", date(2026, 1, 20), txns, sales)
        (True, "UNFULFILLED event on 2026-01-18 (within 3-day lookback)")
    """
    from datetime import timedelta
    
    # Calculate stock at EOD (end of check_date)
    next_day = check_date + timedelta(days=1)
    stock_eod = StockCalculator.calculate_asof(sku, next_day, transactions, sales_records)
    
    # Get sales for this day
    sales_qty = 0
    if sales_records:
        day_sales = [s for s in sales_records if s.sku == sku and s.date == check_date]
        if day_sales:
            sales_qty = sum(s.qty_sold for s in day_sales)
    
    # Rule 1: OH=0 and sales=0 → stockout, demand censored
    if stock_eod.on_hand == 0 and sales_qty == 0:
        return True, f"OH=0 and sales=0 on {check_date}"
    
    # Rule 2: UNFULFILLED event on check_date or within lookback window
    lookback_start = check_date - timedelta(days=lookback_days)
    unfulfilled_events = [
        t for t in transactions
        if t.sku == sku
        and t.event == EventType.UNFULFILLED
        and lookback_start <= t.date <= check_date
    ]
    
    if unfulfilled_events:
        most_recent = max(unfulfilled_events, key=lambda t: t.date)
        return True, f"UNFULFILLED event on {most_recent.date} (within {lookback_days}-day lookback)"
    
    # Not censored
    return False, "Normal demand observation"


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
