"""
Order workflow: proposal, confirmation, and logging.
"""
from datetime import date, timedelta
from typing import List, Optional, Tuple

from ..domain.models import Stock, OrderProposal, OrderConfirmation, Transaction, EventType, SKU


class OrderWorkflow:
    """Order processing: proposal generation and confirmation."""
    
    def __init__(self, csv_layer: CSVLayer, lead_time_days: int = 7):
        """
        Initialize order workflow.
        
        Args:
            csv_layer: CSV persistence layer
            lead_time_days: Default lead time for orders (days) - used only if SKU doesn't specify
        """
        self.csv_layer = csv_layer
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
        
        Args:
            sku: SKU identifier
            description: SKU description
            current_stock: Current stock state (on_hand, on_order)
            daily_sales_avg: Average daily sales (from historical data)
            min_stock: Minimum stock threshold (global default or SKU-specific reorder_point)
            days_cover: Days of sales to cover with on_order
            sku_obj: SKU object (for MOQ, lead_time_days, reorder_point)
        
        Returns:
            OrderProposal with suggested quantity (adjusted for MOQ)
        
        Logic:
            target = reorder_point + (daily_sales_avg * days_cover)
            available = current_stock.on_hand + current_stock.on_order
            proposed_qty_raw = max(0, target - available)
            proposed_qty = round up to nearest MOQ multiple
        """
        # Use SKU-specific parameters if available
        moq = sku_obj.moq if sku_obj else 1
        lead_time = sku_obj.lead_time_days if sku_obj else self.lead_time_days
        reorder_point = sku_obj.reorder_point if sku_obj else min_stock
        
        target_inventory = reorder_point + int(daily_sales_avg * days_cover)
        available_inventory = current_stock.on_hand + current_stock.on_order
        proposed_qty_raw = max(0, target_inventory - available_inventory)
        
        # Apply MOQ: round up to nearest multiple
        if proposed_qty_raw > 0 and moq > 1:
            proposed_qty = ((proposed_qty_raw + moq - 1) // moq) * moq
        else:
            proposed_qty = proposed_qty_raw
        
        receipt_date = date.today() + timedelta(days=lead_time)
        
        notes = f"Target: {target_inventory} units, Available: {available_inventory}, MOQ: {moq}, Lead time: {lead_time}d"
        
        return OrderProposal(
            sku=sku,
            description=description,
            current_on_hand=current_stock.on_hand,
            current_on_order=current_stock.on_order,
            daily_sales_avg=daily_sales_avg,
            proposed_qty=proposed_qty,
            receipt_date=receipt_date,
            notes=notes,
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
) -> float:
    """
    Calculate average daily sales for a SKU from sales records.
    
    Args:
        sales_records: List of SalesRecord objects
        sku: SKU identifier
        days_lookback: Number of days to look back
    
    Returns:
        Average daily sales qty
    """
    sku_sales = [s for s in sales_records if s.sku == sku]
    
    if not sku_sales:
        return 0.0
    
    total_qty = sum(s.qty_sold for s in sku_sales[-days_lookback:])
    days = min(len(sku_sales), days_lookback)
    
    return total_qty / days if days > 0 else 0.0
