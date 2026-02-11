"""
Stock calculation engine (AsOf logic).

Core ledger processing: deterministic, testable, no I/O.
"""
from datetime import date, timedelta
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


@dataclass
class UsableStockResult:
    """Result of usable stock calculation."""
    total_on_hand: int
    usable_qty: int  # Qty with sufficient residual shelf life
    unusable_qty: int  # Qty expiring too soon (< min_shelf_life_days)
    expiring_soon_qty: int  # Qty in waste risk window
    waste_risk_percent: float  # % of stock at risk of waste


class ShelfLifeCalculator:
    """
    Shelf life-aware stock calculations for reorder engine integration.
    
    Calculates:
    - Usable stock (lots with shelf life >= min_shelf_life_days)
    - Waste risk (probability/quantity of expiring stock)
    - Shelf life constraints for order proposals
    """
    
    @staticmethod
    def calculate_usable_stock(
        lots: List['Lot'],  # Forward reference (imported in csv_layer)
        check_date: date,
        min_shelf_life_days: int = 0,
        waste_horizon_days: int = 14,
    ) -> UsableStockResult:
        """
        Calculate usable stock considering shelf life constraints.
        
        Args:
            lots: List of Lot objects for a SKU
            check_date: Reference date (typically today)
            min_shelf_life_days: Minimum residual shelf life required for sale
            waste_horizon_days: Lookahead window for waste risk calculation
        
        Returns:
            UsableStockResult with breakdown of stock by shelf life status
        """
        from .models import Lot  # Import here to avoid circular dependency
        
        total_on_hand = sum(lot.qty_on_hand for lot in lots if lot.qty_on_hand > 0)
        
        if min_shelf_life_days == 0:
            # No constraint: all non-expired stock is usable
            usable_qty = total_on_hand
            unusable_qty = 0
            expiring_soon_qty = 0
            waste_risk_percent = 0.0
            return UsableStockResult(
                total_on_hand=total_on_hand,
                usable_qty=usable_qty,
                unusable_qty=unusable_qty,
                expiring_soon_qty=expiring_soon_qty,
                waste_risk_percent=waste_risk_percent
            )
        
        usable_qty = 0
        unusable_qty = 0
        expiring_soon_qty = 0
        
        for lot in lots:
            if lot.qty_on_hand <= 0:
                continue
            
            if lot.expiry_date is None:
                # No expiry date = infinite shelf life
                usable_qty += lot.qty_on_hand
                continue
            
            days_left = lot.days_until_expiry(check_date)
            
            if days_left is None or days_left < 0:
                # Already expired
                unusable_qty += lot.qty_on_hand
            elif days_left < min_shelf_life_days:
                # Not enough residual shelf life for sale
                unusable_qty += lot.qty_on_hand
            elif days_left <= waste_horizon_days:
                # Usable but expiring soon (waste risk window)
                usable_qty += lot.qty_on_hand
                expiring_soon_qty += lot.qty_on_hand
            else:
                # Usable with good shelf life
                usable_qty += lot.qty_on_hand
        
        # Waste risk: % of total stock expiring within horizon
        waste_risk_percent = (expiring_soon_qty / total_on_hand * 100) if total_on_hand > 0 else 0.0
        
        return UsableStockResult(
            total_on_hand=total_on_hand,
            usable_qty=usable_qty,
            unusable_qty=unusable_qty,
            expiring_soon_qty=expiring_soon_qty,
            waste_risk_percent=waste_risk_percent
        )
    
    @staticmethod
    def apply_shelf_life_penalty(
        proposed_qty: int,
        waste_risk_percent: float,
        waste_risk_threshold: float,
        penalty_mode: str,
        penalty_factor: float,
    ) -> Tuple[int, str]:
        """
        Apply shelf life penalty to reorder quantity.
        
        Args:
            proposed_qty: Original proposed order quantity
            waste_risk_percent: Current waste risk % (from calculate_usable_stock)
            waste_risk_threshold: Threshold to trigger penalty
            penalty_mode: "soft" (reduce qty) or "hard" (block order)
            penalty_factor: Reduction factor for soft penalty (0.0-1.0)
        
        Returns:
            (adjusted_qty, reason_message)
        """
        if waste_risk_percent < waste_risk_threshold:
            # No penalty needed
            return proposed_qty, ""
        
        if penalty_mode == "hard":
            # Hard cap: block order
            return 0, f"❌ BLOCKED: Waste risk {waste_risk_percent:.1f}% > {waste_risk_threshold}% (hard mode)"
        
        elif penalty_mode == "soft":
            # Soft penalty: reduce quantity
            reduction_pct = penalty_factor * 100
            adjusted_qty = int(proposed_qty * (1.0 - penalty_factor))
            reason = f"⚠️ Reduced by {reduction_pct:.0f}% (waste risk {waste_risk_percent:.1f}%)"
            return adjusted_qty, reason
        
        else:
            # Unknown mode: no penalty
            return proposed_qty, ""
    
    @staticmethod
    def calculate_forward_waste_risk(
        lots: List['Lot'],
        current_date: date,
        receipt_date: date,
        proposed_qty: int,
        sku_shelf_life_days: int,
        min_shelf_life_days: int,
        waste_horizon_days: int,
    ) -> Tuple[float, int, int]:
        """
        Calculate waste risk projected to receipt_date including incoming order.
        
        This provides a more realistic waste risk assessment by considering:
        1. Existing lots aged forward by lead_time days
        2. Incoming order as a virtual lot with full shelf life
        
        Args:
            lots: Current lots for the SKU
            current_date: Today's date
            receipt_date: Expected receipt date of new order
            proposed_qty: Quantity being ordered
            sku_shelf_life_days: Total shelf life for this SKU (for new lot)
            min_shelf_life_days: Minimum shelf life threshold
            waste_horizon_days: Waste risk window
        
        Returns:
            (waste_risk_percent, total_stock_at_receipt, expiring_soon_at_receipt)
        """
        from .models import Lot
        
        if proposed_qty <= 0:
            # No order: just calculate current waste risk aged forward
            aged_result = ShelfLifeCalculator.calculate_usable_stock(
                lots=lots,
                check_date=receipt_date,
                min_shelf_life_days=min_shelf_life_days,
                waste_horizon_days=waste_horizon_days
            )
            return aged_result.waste_risk_percent, aged_result.total_on_hand, aged_result.expiring_soon_qty
        
        # Create virtual lot for incoming order
        incoming_expiry = receipt_date + timedelta(days=sku_shelf_life_days) if sku_shelf_life_days > 0 else None
        incoming_lot = Lot(
            lot_id="VIRTUAL_INCOMING",
            sku="VIRTUAL",
            expiry_date=incoming_expiry,
            qty_on_hand=proposed_qty,
            receipt_id="VIRTUAL",
            receipt_date=receipt_date
        )
        
        # Combine existing + incoming lots
        combined_lots = list(lots) + [incoming_lot]
        
        # Calculate waste risk at receipt_date
        forward_result = ShelfLifeCalculator.calculate_usable_stock(
            lots=combined_lots,
            check_date=receipt_date,
            min_shelf_life_days=min_shelf_life_days,
            waste_horizon_days=waste_horizon_days
        )
        
        return (
            forward_result.waste_risk_percent,
            forward_result.total_on_hand,
            forward_result.expiring_soon_qty
        )
    
    @staticmethod
    def calculate_forward_waste_risk_demand_adjusted(
        lots: List,
        receipt_date: date,
        proposed_qty: int,
        sku_shelf_life_days: int,
        min_shelf_life_days: int,
        waste_horizon_days: int,
        forecast_daily_demand: float,
    ) -> Tuple[float, int, int, int]:
        """
        Calculate forward-looking waste risk adjusted for expected demand consumption.
        
        This method improves upon calculate_forward_waste_risk by accounting for the fact
        that stock "expiring soon" will be partially consumed by normal sales before expiry.
        
        Algorithm:
        1. Age lots to receipt_date and add virtual incoming lot
        2. Identify stock expiring within waste_horizon (expiring_soon_qty)
        3. For each expiring-soon lot, calculate expected demand until its expiry
        4. Subtract expected demand from expiring_soon_qty
        5. Calculate adjusted waste risk = max(0, expiring_soon - demand) / total_stock
        
        Args:
            lots: Current lot inventory
            receipt_date: Date when order will be received
            proposed_qty: Quantity of incoming order
            sku_shelf_life_days: Total shelf life of SKU (for virtual incoming lot)
            min_shelf_life_days: Minimum residual shelf life for sale
            waste_horizon_days: Lookahead window for waste risk
            forecast_daily_demand: Expected daily demand rate (units/day)
        
        Returns:
            Tuple[float, int, int, int]:
                - waste_risk_adjusted_percent: Demand-adjusted waste risk %
                - total_on_hand: Total stock at receipt (existing + incoming)
                - expiring_soon_qty: Raw qty expiring within horizon (before demand adjustment)
                - expected_waste_qty: Estimated actual waste after demand consumption
        
        Example:
            Receipt date: 2026-02-15
            Existing lots at receipt: 30 units expiring 2026-02-17 (2 days after receipt)
            Incoming order: 40 units (expiry 2026-04-16)
            Forecast: 10 units/day
            
            Traditional forward risk: 30/70 = 42.9%
            Demand-adjusted:
                - Expected demand in 2 days = 10 * 2 = 20
                - Expected waste = max(0, 30 - 20) = 10
                - Adjusted risk = 10/70 = 14.3%
        """
        from .models import Lot
        
        if proposed_qty <= 0:
            # No order: calculate waste risk at receipt without incoming lot
            aged_result = ShelfLifeCalculator.calculate_usable_stock(
                lots=lots,
                check_date=receipt_date,
                min_shelf_life_days=min_shelf_life_days,
                waste_horizon_days=waste_horizon_days
            )
            
            # Adjust for demand even without new order
            expected_waste = ShelfLifeCalculator._calculate_expected_waste(
                lots=lots,
                check_date=receipt_date,
                min_shelf_life_days=min_shelf_life_days,
                waste_horizon_days=waste_horizon_days,
                forecast_daily_demand=forecast_daily_demand
            )
            
            total_stock = aged_result.total_on_hand
            adjusted_risk = (expected_waste / total_stock * 100) if total_stock > 0 else 0.0
            
            return (
                adjusted_risk,
                total_stock,
                aged_result.expiring_soon_qty,
                expected_waste
            )
        
        # Create virtual lot for incoming order
        incoming_expiry = receipt_date + timedelta(days=sku_shelf_life_days) if sku_shelf_life_days > 0 else None
        incoming_lot = Lot(
            lot_id="VIRTUAL_INCOMING",
            sku="VIRTUAL",
            expiry_date=incoming_expiry,
            qty_on_hand=proposed_qty,
            receipt_id="VIRTUAL",
            receipt_date=receipt_date
        )
        
        # Combine existing + incoming lots
        combined_lots = list(lots) + [incoming_lot]
        
        # Calculate traditional forward waste risk
        forward_result = ShelfLifeCalculator.calculate_usable_stock(
            lots=combined_lots,
            check_date=receipt_date,
            min_shelf_life_days=min_shelf_life_days,
            waste_horizon_days=waste_horizon_days
        )
        
        # Calculate demand-adjusted expected waste
        expected_waste = ShelfLifeCalculator._calculate_expected_waste(
            lots=combined_lots,
            check_date=receipt_date,
            min_shelf_life_days=min_shelf_life_days,
            waste_horizon_days=waste_horizon_days,
            forecast_daily_demand=forecast_daily_demand
        )
        
        total_stock = forward_result.total_on_hand
        adjusted_risk = (expected_waste / total_stock * 100) if total_stock > 0 else 0.0
        
        return (
            adjusted_risk,
            total_stock,
            forward_result.expiring_soon_qty,
            expected_waste
        )
    
    @staticmethod
    def _calculate_expected_waste(
        lots: List,
        check_date: date,
        min_shelf_life_days: int,
        waste_horizon_days: int,
        forecast_daily_demand: float,
    ) -> int:
        """
        Calculate expected waste accounting for demand consumption.
        
        For each lot expiring within waste_horizon:
        1. Calculate days until its expiry
        2. Estimate demand that will be consumed before expiry
        3. Residual qty after demand = potential waste
        
        Args:
            lots: Lot inventory at check_date
            check_date: Reference date
            min_shelf_life_days: Minimum shelf life threshold
            waste_horizon_days: Waste risk window
            forecast_daily_demand: Daily demand rate
        
        Returns:
            Expected waste quantity (integer)
        """
        from .models import Lot
        
        if forecast_daily_demand <= 0:
            # No demand forecast: fallback to traditional risk (all expiring-soon = waste)
            expiring_soon = 0
            for lot in lots:
                if lot.qty_on_hand <= 0:
                    continue
                if lot.expiry_date is None:
                    continue
                
                days_left = lot.days_until_expiry(check_date)
                if days_left is None or days_left < min_shelf_life_days:
                    continue
                if days_left <= waste_horizon_days:
                    expiring_soon += lot.qty_on_hand
            
            return expiring_soon
        
        # Collect expiring-soon lots with their expiry dates
        expiring_lots = []
        for lot in lots:
            if lot.qty_on_hand <= 0:
                continue
            if lot.expiry_date is None:
                continue
            
            days_left = lot.days_until_expiry(check_date)
            if days_left is None or days_left < min_shelf_life_days:
                continue
            if days_left <= waste_horizon_days:
                expiring_lots.append({
                    'qty': lot.qty_on_hand,
                    'days_until_expiry': days_left
                })
        
        if not expiring_lots:
            return 0
        
        # Sort by expiry (earliest first) for FEFO simulation
        expiring_lots.sort(key=lambda x: x['days_until_expiry'])
        
        # Simulate FEFO consumption with forecasted demand
        total_expected_waste = 0
        cumulative_demand_days = 0
        
        for lot_info in expiring_lots:
            qty = lot_info['qty']
            days_until_expiry = lot_info['days_until_expiry']
            
            # Demand window: from cumulative_demand_days to expiry
            demand_window_days = max(0, days_until_expiry - cumulative_demand_days)
            expected_demand_in_window = forecast_daily_demand * demand_window_days
            
            # Waste = qty that exceeds demand in its lifespan
            waste_from_lot = max(0, qty - expected_demand_in_window)
            total_expected_waste += int(waste_from_lot)
            
            # Update cumulative demand consumed
            consumed_from_lot = min(qty, expected_demand_in_window)
            if consumed_from_lot > 0:
                cumulative_demand_days += consumed_from_lot / forecast_daily_demand
        
        return total_expected_waste


class LotConsumptionManager:
    """
    FEFO (First Expired First Out) lot consumption logic.
    
    When stock is consumed (SALE, WASTE), automatically deducts from lots
    with nearest expiry date first, then from non-expiry lots.
    """
    
    @staticmethod
    def consume_from_lots(sku: str, qty_to_consume: int, lots: List, csv_layer) -> List[Dict]:
        """
        Consume quantity from lots using FEFO logic.
        
        Args:
            sku: SKU identifier
            qty_to_consume: Total quantity to consume
            lots: List of Lot objects for the SKU (should be sorted by expiry)
            csv_layer: CSV layer for updating lot quantities
        
        Returns:
            List of consumption records: [{"lot_id": str, "qty_consumed": int, "expiry_date": date}, ...]
        
        Raises:
            ValueError: If insufficient stock in lots
        """
        from ..domain.models import Lot
        
        # Get lots for this SKU, sorted FEFO (earliest expiry first)
        sku_lots = csv_layer.get_lots_by_sku(sku, sort_by_expiry=True)
        
        if not sku_lots:
            # No lot tracking for this SKU, skip FEFO logic
            return []
        
        total_available = sum(lot.qty_on_hand for lot in sku_lots)
        if total_available < qty_to_consume:
            raise ValueError(
                f"Insufficient stock in lots for {sku}: "
                f"need {qty_to_consume}, available {total_available}"
            )
        
        consumption_records = []
        remaining_to_consume = qty_to_consume
        
        for lot in sku_lots:
            if remaining_to_consume <= 0:
                break
            
            # Consume from this lot
            qty_from_lot = min(lot.qty_on_hand, remaining_to_consume)
            new_qty = lot.qty_on_hand - qty_from_lot
            
            # Update lot quantity
            csv_layer.update_lot_quantity(lot.lot_id, new_qty)
            
            consumption_records.append({
                "lot_id": lot.lot_id,
                "qty_consumed": qty_from_lot,
                "expiry_date": lot.expiry_date,
                "qty_remaining": new_qty,
            })
            
            remaining_to_consume -= qty_from_lot
        
        return consumption_records
    
    @staticmethod
    def add_fefo_note_to_transaction(txn: Transaction, consumption_records: List[Dict]) -> Transaction:
        """
        Add FEFO consumption details to transaction note.
        
        Args:
            txn: Original transaction
            consumption_records: Output from consume_from_lots()
        
        Returns:
            New transaction with enhanced note
        """
        if not consumption_records:
            return txn
        
        lot_details = []
        for record in consumption_records:
            lot_id = record["lot_id"]
            qty = record["qty_consumed"]
            exp = record["expiry_date"]
            exp_str = exp.isoformat() if exp else "no expiry"
            lot_details.append(f"{lot_id}:{qty}pz(exp:{exp_str})")
        
        fefo_note = f"FEFO: {', '.join(lot_details)}"
        original_note = txn.note or ""
        combined_note = f"{original_note}; {fefo_note}".strip("; ")
        
        # Create new transaction with updated note
        from ..domain.models import Transaction
        return Transaction(
            date=txn.date,
            sku=txn.sku,
            event=txn.event,
            qty=txn.qty,
            receipt_date=txn.receipt_date,
            note=combined_note,
        )

