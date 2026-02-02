"""
Example: Friday Dual Order Workflow with On-Order Pipeline

Demonstrates:
1. Creating Friday dual orders (Saturday + Monday lanes)
2. Tracking pending orders by receipt date
3. Calculating inventory position for different dates
4. Integration with calendar module
"""

from datetime import date as Date
from src.domain.models import Transaction, EventType, SKU
from src.domain.ledger import StockCalculator
from src.domain.calendar import next_receipt_date, Lane, calculate_protection_period_days


def friday_dual_order_example():
    """Complete Friday dual order workflow."""
    
    # Setup: It's Friday, Feb 9, 2024
    friday = Date(2024, 2, 9)
    
    # SKU configuration
    sku = SKU(
        sku="SKU001",
        description="Widget Pro",
        safety_stock=20,
        lead_time_days=1,
    )
    
    # Historical transactions
    transactions = [
        # Initial stock snapshot
        Transaction(
            date=Date(2024, 2, 1),
            sku=sku.sku,
            event=EventType.SNAPSHOT,
            qty=50,
            note="Initial inventory"
        ),
    ]
    
    # Daily sales average (calculated from history, not shown)
    daily_sales_avg = 10.0
    
    print("=" * 60)
    print("FRIDAY DUAL ORDER WORKFLOW")
    print("=" * 60)
    print(f"Current Date: {friday} (Friday)")
    print(f"SKU: {sku.sku} - {sku.description}")
    print(f"Safety Stock: {sku.safety_stock}")
    print(f"Daily Sales Avg: {daily_sales_avg}")
    print()
    
    # Step 1: Calculate receipt dates using calendar
    print("STEP 1: Calculate Receipt Dates")
    print("-" * 60)
    
    receipt_saturday = next_receipt_date(friday, Lane.SATURDAY)
    receipt_monday = next_receipt_date(friday, Lane.MONDAY)
    
    print(f"Saturday lane: order {friday} → delivery {receipt_saturday}")
    print(f"Monday lane: order {friday} → delivery {receipt_monday}")
    print()
    
    # Step 2: Calculate protection periods
    print("STEP 2: Calculate Protection Periods")
    print("-" * 60)
    
    P_saturday = calculate_protection_period_days(friday, Lane.SATURDAY)
    P_monday = calculate_protection_period_days(friday, Lane.MONDAY)
    
    print(f"Saturday lane protection period: {P_saturday} days")
    print(f"Monday lane protection period: {P_monday} days")
    print()
    
    # Step 3: Current inventory position (before orders)
    print("STEP 3: Current Inventory Position")
    print("-" * 60)
    
    stock_now = StockCalculator.calculate_asof(
        sku.sku, 
        friday + Date.resolution,  # End of Friday
        transactions
    )
    
    ip_now = stock_now.on_hand + stock_now.on_order - stock_now.unfulfilled_qty
    
    print(f"On Hand: {stock_now.on_hand}")
    print(f"On Order: {stock_now.on_order}")
    print(f"Inventory Position (now): {ip_now}")
    print()
    
    # Step 4: Calculate order quantities for each lane
    print("STEP 4: Calculate Order Quantities")
    print("-" * 60)
    
    # Saturday lane
    forecast_saturday = daily_sales_avg * P_saturday
    target_saturday = forecast_saturday + sku.safety_stock
    need_saturday = max(0, target_saturday - ip_now)
    
    print(f"Saturday Lane:")
    print(f"  Forecast ({P_saturday} days): {forecast_saturday:.0f}")
    print(f"  Target Stock: {target_saturday:.0f}")
    print(f"  Current IP: {ip_now}")
    print(f"  Order Needed: {need_saturday:.0f}")
    print()
    
    # Monday lane
    forecast_monday = daily_sales_avg * P_monday
    target_monday = forecast_monday + sku.safety_stock
    # For Monday lane, assume Saturday order is placed, so IP includes it
    ip_after_saturday = ip_now + need_saturday
    need_monday = max(0, target_monday - ip_after_saturday)
    
    print(f"Monday Lane:")
    print(f"  Forecast ({P_monday} days): {forecast_monday:.0f}")
    print(f"  Target Stock: {target_monday:.0f}")
    print(f"  IP after Saturday order: {ip_after_saturday:.0f}")
    print(f"  Additional Order Needed: {need_monday:.0f}")
    print()
    
    # Step 5: Create orders
    print("STEP 5: Place Orders")
    print("-" * 60)
    
    if need_saturday > 0:
        transactions.append(
            Transaction(
                date=friday,
                sku=sku.sku,
                event=EventType.ORDER,
                qty=int(need_saturday),
                receipt_date=receipt_saturday,
                note=f"Friday order - Saturday lane (P={P_saturday})"
            )
        )
        print(f"✓ ORDER placed: {int(need_saturday)} units for {receipt_saturday} (Saturday)")
    
    if need_monday > 0:
        transactions.append(
            Transaction(
                date=friday,
                sku=sku.sku,
                event=EventType.ORDER,
                qty=int(need_monday),
                receipt_date=receipt_monday,
                note=f"Friday order - Monday lane (P={P_monday})"
            )
        )
        print(f"✓ ORDER placed: {int(need_monday)} units for {receipt_monday} (Monday)")
    
    if need_saturday == 0 and need_monday == 0:
        print("✗ No orders needed (sufficient stock)")
    
    print()
    
    # Step 6: View on-order pipeline
    print("STEP 6: On-Order Pipeline by Receipt Date")
    print("-" * 60)
    
    pending_orders = StockCalculator.on_order_by_date(
        sku.sku, 
        transactions,
        as_of_date=friday + Date.resolution
    )
    
    if pending_orders:
        for receipt_date, qty in sorted(pending_orders.items()):
            print(f"  {receipt_date}: {qty} units")
    else:
        print("  No pending orders")
    print()
    
    # Step 7: Inventory position projections
    print("STEP 7: Inventory Position Projections")
    print("-" * 60)
    
    # IP as of Saturday (includes Saturday order only)
    ip_saturday = StockCalculator.inventory_position(
        sku.sku,
        receipt_saturday,
        transactions
    )
    
    # IP as of Monday (includes both orders)
    ip_monday = StockCalculator.inventory_position(
        sku.sku,
        receipt_monday,
        transactions
    )
    
    print(f"IP as of {receipt_saturday} (Saturday): {ip_saturday}")
    print(f"  → Includes Saturday order: {int(need_saturday)} units")
    print()
    
    print(f"IP as of {receipt_monday} (Monday): {ip_monday}")
    print(f"  → Includes Saturday ({int(need_saturday)}) + Monday ({int(need_monday)}) orders")
    print()
    
    # Step 8: Simulate Saturday receipt
    print("STEP 8: Simulate Saturday Receipt")
    print("-" * 60)
    
    if need_saturday > 0:
        transactions.append(
            Transaction(
                date=receipt_saturday,
                sku=sku.sku,
                event=EventType.RECEIPT,
                qty=int(need_saturday),
                receipt_date=receipt_saturday,
                note="Saturday delivery received"
            )
        )
        print(f"✓ RECEIPT: {int(need_saturday)} units received on {receipt_saturday}")
        
        # Check updated pipeline
        pending_after_saturday = StockCalculator.on_order_by_date(
            sku.sku,
            transactions,
            as_of_date=receipt_saturday + Date.resolution
        )
        
        print("\nUpdated On-Order Pipeline:")
        if pending_after_saturday:
            for receipt_date, qty in sorted(pending_after_saturday.items()):
                print(f"  {receipt_date}: {qty} units")
        else:
            print("  No pending orders")
    
    print()
    print("=" * 60)
    print("END OF WORKFLOW")
    print("=" * 60)


def multi_sku_pipeline_example():
    """Show on-order pipeline for multiple SKUs."""
    
    skus = ["SKU001", "SKU002", "SKU003"]
    
    transactions = [
        # SKU001: Two orders
        Transaction(date=Date(2024, 2, 5), sku="SKU001", event=EventType.ORDER, 
                   qty=100, receipt_date=Date(2024, 2, 10)),
        Transaction(date=Date(2024, 2, 6), sku="SKU001", event=EventType.ORDER, 
                   qty=50, receipt_date=Date(2024, 2, 12)),
        
        # SKU002: One order
        Transaction(date=Date(2024, 2, 7), sku="SKU002", event=EventType.ORDER, 
                   qty=200, receipt_date=Date(2024, 2, 11)),
        
        # SKU003: No orders
    ]
    
    print("=" * 60)
    print("MULTI-SKU ON-ORDER PIPELINE")
    print("=" * 60)
    
    for sku in skus:
        pending = StockCalculator.on_order_by_date(sku, transactions)
        
        print(f"\n{sku}:")
        if pending:
            for receipt_date, qty in sorted(pending.items()):
                print(f"  {receipt_date}: {qty} units")
        else:
            print("  No pending orders")


if __name__ == "__main__":
    print("\n")
    friday_dual_order_example()
    print("\n\n")
    multi_sku_pipeline_example()
