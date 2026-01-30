"""
Order workflow: proposal generation and confirmation.
"""
from datetime import date, timedelta
from typing import List, Tuple, Optional

from ..domain.models import Stock, OrderProposal, OrderConfirmation, Transaction, EventType, SKU
from ..persistence.csv_layer import CSVLayer

class OrderWorkflow:
    """Order processing: proposal generation and confirmation."""
    
    def __init__(self, csv_layer: CSVLayer, lead_time_days: int = None):
        """
        Initialize order workflow.
        
        Args:
            csv_layer: CSV persistence layer
            lead_time_days: Default lead time for orders (days). If None, reads from settings.
        """
        self.csv_layer = csv_layer
        
        # Read lead_time from settings if not provided
        if lead_time_days is None:
            settings = csv_layer.read_settings()
            self.lead_time_days = settings.get("reorder_engine", {}).get("lead_time_days", {}).get("value", 7)
        else:
            self.lead_time_days = lead_time_days
    
    def generate_proposal(
        self,
        sku: str,
        description: str,
        current_stock: Stock,
        daily_sales_avg: float,
        min_stock: int = 10,
        days_cover: int = 30,
        sku_obj: Optional[SKU] = None,
    ) -> OrderProposal:
        """
        Generate order proposal based on stock and sales history.
        
        NEW FORMULA (2026-01-29):
        S = forecast × (lead_time + review_period) + safety_stock
        proposed = max(0, S − (on_hand + on_order))
        Then: apply pack_size rounding → MOQ rounding → cap at max_stock
        
        Args:
            sku: SKU identifier
            description: SKU description
            current_stock: Current stock state (on_hand, on_order)
            daily_sales_avg: Average daily sales (from historical data)
            min_stock: Minimum stock threshold (global default, overridden by SKU reorder_point)
            days_cover: Days of sales to cover (DEPRECATED, now uses lead_time + review_period)
            sku_obj: SKU object (for pack_size, MOQ, lead_time, review_period, safety_stock, max_stock)
        
        Returns:
            OrderProposal with suggested quantity (adjusted for pack_size, MOQ, and max_stock cap)
        """
        # Use SKU-specific parameters if available
        pack_size = sku_obj.pack_size if sku_obj else 1
        moq = sku_obj.moq if sku_obj else 1
        lead_time = sku_obj.lead_time_days if sku_obj else self.lead_time_days
        review_period = sku_obj.review_period if sku_obj else 7
        safety_stock = sku_obj.safety_stock if sku_obj else 0
        shelf_life_days = sku_obj.shelf_life_days if sku_obj else 0
        max_stock = sku_obj.max_stock if sku_obj else 999
        
        # NEW FORMULA: S = forecast × (lead_time + review_period) + safety_stock
        forecast_period = lead_time + review_period
        S = int(daily_sales_avg * forecast_period) + safety_stock
        
        # Check shelf life warning (if shelf_life_days > 0)
        shelf_life_warning = False
        if shelf_life_days > 0 and daily_sales_avg > 0:
            shelf_life_capacity = int(daily_sales_avg * shelf_life_days)
            if S > shelf_life_capacity:
                shelf_life_warning = True
        
        # proposed = max(0, S − (on_hand + on_order))
        available_inventory = current_stock.on_hand + current_stock.on_order
        proposed_qty_raw = max(0, S - available_inventory)
        
        # Apply pack_size rounding (round up to nearest pack_size multiple)
        if proposed_qty_raw > 0 and pack_size > 1:
            proposed_qty = ((proposed_qty_raw + pack_size - 1) // pack_size) * pack_size
        else:
            proposed_qty = proposed_qty_raw
        
        # Apply MOQ rounding (round up to nearest MOQ multiple)
        if proposed_qty > 0 and moq > 1:
            proposed_qty = ((proposed_qty + moq - 1) // moq) * moq
        
        # Cap at max_stock
        if available_inventory + proposed_qty > max_stock:
            proposed_qty = max(0, max_stock - available_inventory)
            # Re-apply pack_size and MOQ constraints after capping
            if proposed_qty > 0 and pack_size > 1:
                proposed_qty = (proposed_qty // pack_size) * pack_size  # Round down
            if proposed_qty > 0 and moq > 1 and proposed_qty < moq:
                proposed_qty = 0  # Can't meet MOQ without exceeding max_stock
        
        receipt_date = date.today() + timedelta(days=lead_time)
        
        notes = f"S={S} (forecast={int(daily_sales_avg * forecast_period)}+safety={safety_stock}), Available={available_inventory}, Pack={pack_size}, MOQ={moq}, Max={max_stock}"
        if shelf_life_warning:
            notes += f" ⚠️ SHELF LIFE: Target S={S} exceeds {shelf_life_days}d capacity"
        
        return OrderProposal(
            sku=sku,
            description=description,
            current_on_hand=current_stock.on_hand,
            current_on_order=current_stock.on_order,
            daily_sales_avg=daily_sales_avg,
            proposed_qty=proposed_qty,
            receipt_date=receipt_date,
            notes=notes,
            shelf_life_warning=shelf_life_warning,
        )
    
    def confirm_order(
        self,
        proposals: List[OrderProposal],
        confirmed_qtys: Optional[List[int]] = None,
    ) -> Tuple[List[OrderConfirmation], List[Transaction]]:
        """
        Confirm order(s) and generate ORDER events.
        
        Args:
            proposals: List of order proposals
            confirmed_qtys: Confirmed quantities (if None, use proposal qty)
        
        Returns:
            (order_confirmations, transactions_to_write)
        """
        confirmed_qtys = confirmed_qtys or [p.proposed_qty for p in proposals]
        
        if len(proposals) != len(confirmed_qtys):
            raise ValueError("Number of proposals and confirmed quantities must match")
        
        today = date.today()
        order_id_base = today.isoformat().replace("-", "")
        
        confirmations = []
        transactions = []
        
        for idx, (proposal, qty) in enumerate(zip(proposals, confirmed_qtys)):
            if qty <= 0:
                continue
            
            order_id = f"{order_id_base}_{idx:03d}"
            
            confirmation = OrderConfirmation(
                order_id=order_id,
                date=today,
                sku=proposal.sku,
                qty_ordered=qty,
                receipt_date=proposal.receipt_date or today + timedelta(days=self.lead_time_days),
                status="PENDING",
            )
            confirmations.append(confirmation)
            
            # Create ORDER event in ledger
            txn = Transaction(
                date=today,
                sku=proposal.sku,
                event=EventType.ORDER,
                qty=qty,
                receipt_date=confirmation.receipt_date,
                note=f"Order {order_id}",
            )
            transactions.append(txn)
        
        # Write to ledger and logs
        if transactions:
            self.csv_layer.write_transactions_batch(transactions)
        
        for confirmation in confirmations:
            self.csv_layer.write_order_log(
                order_id=confirmation.order_id,
                date_str=confirmation.date.isoformat(),
                sku=confirmation.sku,
                qty=confirmation.qty_ordered,
                status=confirmation.status,
            )
        
        return confirmations, transactions


def calculate_daily_sales_average(
    sales_records,
    sku: str,
    days_lookback: int = 30,
    transactions=None,
    asof_date: date = None,
) -> float:
    """
    Calculate average daily sales for a SKU using calendar-based approach.
    
    NEW BEHAVIOR (2026-01-29):
    - Uses real calendar days (30 days = 30 data points, including zeros)
    - Excludes days when SKU was out-of-stock (on_hand + on_order == 0)
    - More accurate forecast for irregular sales patterns
    
    Args:
        sales_records: List of SalesRecord objects
        sku: SKU identifier
        days_lookback: Number of calendar days to look back (default: 30)
        transactions: List of Transaction objects (for OOS detection)
        asof_date: As-of date for calculation (defaults to today)
    
    Returns:
        Average daily sales qty (excluding OOS days)
    
    Example:
        If last 30 days have 10 days with sales, 15 days zero, 5 days OOS:
        avg = sum(sales_10_days) / 25  (excludes 5 OOS days)
    """
    from ..domain.ledger import StockCalculator
    
    if asof_date is None:
        asof_date = date.today()
    
    # Build sales map: {date: qty_sold}
    sku_sales_map = {}
    for s in sales_records:
        if s.sku == sku:
            sku_sales_map[s.date] = sku_sales_map.get(s.date, 0) + s.qty_sold
    
    # Generate calendar days range
    start_date = asof_date - timedelta(days=days_lookback - 1)
    calendar_days = [start_date + timedelta(days=i) for i in range(days_lookback)]
    
    # Detect OOS days (if transactions provided)
    oos_days = set()
    if transactions:
        for day in calendar_days:
            stock = StockCalculator.calculate_asof(sku, day, transactions, sales_records)
            if stock.on_hand + stock.on_order == 0:
                oos_days.add(day)
    
    # Calculate average excluding OOS days
    total_sales = 0
    valid_days = 0
    
    for day in calendar_days:
        if day in oos_days:
            continue  # Skip OOS days
        
        # Include day with sales qty (or zero if no sales)
        total_sales += sku_sales_map.get(day, 0)
        valid_days += 1
    
    return total_sales / valid_days if valid_days > 0 else 0.0

