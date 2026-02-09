#!/usr/bin/env python
"""Test assortment status exclusion from sales average calculation."""

from datetime import date, timedelta
from src.domain.models import SKU, Transaction, EventType, SalesRecord
from src.workflows.order import calculate_daily_sales_average

# Test scenario: SKU out of assortment for 10 days, then back in
print("\n" + "="*70)
print("TEST: Sales Average Exclusion for Out-of-Assortment Periods")
print("="*70)

# Create test data
sku_id = "TEST_SKU"
asof = date(2026, 2, 8)

# Sales: 10 units/day for 30 days
sales_records = []
for i in range(30):
    sales_date = asof - timedelta(days=29-i)
    sales_records.append(SalesRecord(
        date=sales_date,
        sku=sku_id,
        qty_sold=10
    ))

# Scenario 1: No assortment changes
print("\n--- Scenario 1: Always IN assortment ---")
avg1, oos1 = calculate_daily_sales_average(
    sales_records, sku_id, days_lookback=30, 
    transactions=[], asof_date=asof
)
print(f"Average sales: {avg1:.2f} units/day")
print(f"OOS days: {oos1}")
print(f"Expected: 10.00 units/day (no exclusions)")

# Scenario 2: OUT for days 10-20 (10 days)
print("\n--- Scenario 2: OUT of assortment days 10-20 (10 days) ---")
day_10 = asof - timedelta(days=20)  # Day 10 from start
day_21 = asof - timedelta(days=9)   # Day 21 from start

# Add SNAPSHOT to give stock (prevents all days being marked as OOS)
day_0 = asof - timedelta(days=30)
transactions = [
    Transaction(
        date=day_0,
        sku=sku_id,
        event=EventType.SNAPSHOT,
        qty=1000,  # Starting stock
        receipt_date=None,
        note="Initial stock"
    ),
    Transaction(
        date=day_10,
        sku=sku_id,
        event=EventType.ASSORTMENT_OUT,
        qty=0,
        receipt_date=None,
        note="Test: going out of assortment"
    ),
    Transaction(
        date=day_21,
        sku=sku_id,
        event=EventType.ASSORTMENT_IN,
        qty=0,
        receipt_date=None,
        note="Test: back in assortment"
    )
]

avg2, oos2 = calculate_daily_sales_average(
    sales_records, sku_id, days_lookback=30,
    transactions=transactions, asof_date=asof
)
print(f"Average sales: {avg2:.2f} units/day")
print(f"OOS days: {oos2}")
print(f"Expected: 10.00 units/day (20 valid days with 10 units each = 200/20 = 10)")
print(f"Days excluded: 10 (out-of-assortment)")

# Scenario 3: OUT for first 15 days, then IN
print("\n--- Scenario 3: OUT for first 15 days, then IN ---")
day_1 = asof - timedelta(days=29)   # Day 1
day_16 = asof - timedelta(days=14)  # Day 16

# Create transactions showing initial OUT state
transactions3 = [
    Transaction(
        date=day_0,
        sku=sku_id,
        event=EventType.SNAPSHOT,
        qty=1000,
        receipt_date=None,
        note="Initial stock"
    ),
    # Implicit: SKU was OUT before our lookback period
    # First event is ASSORTMENT_IN, so calculate_daily_sales_average assumes OUT from start
    Transaction(
        date=day_16,
        sku=sku_id,
        event=EventType.ASSORTMENT_IN,
        qty=0,
        receipt_date=None,
        note="Test: coming back in assortment"
    )
]

avg3, oos3 = calculate_daily_sales_average(
    sales_records, sku_id, days_lookback=30,
    transactions=transactions3, asof_date=asof
)
print(f"Average sales: {avg3:.2f} units/day")
print(f"OOS days: {oos3}")
print(f"Expected: 10.00 units/day (15 valid days with 10 units each = 150/15 = 10)")
print(f"Days excluded: 15 (out-of-assortment)")

# Scenario 4: Currently OUT (never came back IN)
print("\n--- Scenario 4: OUT from day 20 to present (still OUT) ---")
day_20 = asof - timedelta(days=10)  # Day 20 from start

transactions4 = [
    Transaction(
        date=day_0,
        sku=sku_id,
        event=EventType.SNAPSHOT,
        qty=1000,
        receipt_date=None,
        note="Initial stock"
    ),
    Transaction(
        date=day_20,
        sku=sku_id,
        event=EventType.ASSORTMENT_OUT,
        qty=0,
        receipt_date=None,
        note="Test: going out of assortment (stays out)"
    )
]

avg4, oos4 = calculate_daily_sales_average(
    sales_records, sku_id, days_lookback=30,
    transactions=transactions4, asof_date=asof
)
print(f"Average sales: {avg4:.2f} units/day")
print(f"OOS days: {oos4}")
print(f"Expected: 10.00 units/day (19 valid days with 10 units each = 190/19 = 10)")
print(f"Days excluded: 11 (out-of-assortment, including today)")

# Scenario 5: Different sales during OUT period (contamination test)
print("\n--- Scenario 5: Clearance sales (20 units/day) during OUT period ---")
# Modify sales: 20 units/day during OUT period (days 10-20)
sales_records5 = []
for i in range(30):
    sales_date = asof - timedelta(days=29-i)
    day_num = i + 1
    
    # Days 10-20: 20 units (clearance)
    # Other days: 10 units (normal)
    qty = 20 if 10 <= day_num <= 20 else 10
    
    sales_records5.append(SalesRecord(
        date=sales_date,
        sku=sku_id,
        qty_sold=qty
    ))

# Use same OUT/IN events as Scenario 2 (but add SNAPSHOT to transactions list)
transactions5 = [
    Transaction(
        date=day_0,
        sku=sku_id,
        event=EventType.SNAPSHOT,
        qty=1000,
        receipt_date=None,
        note="Initial stock"
    ),
    Transaction(
        date=day_10,
        sku=sku_id,
        event=EventType.ASSORTMENT_OUT,
        qty=0,
        receipt_date=None,
        note="Test: going out of assortment"
    ),
    Transaction(
        date=day_21,
        sku=sku_id,
        event=EventType.ASSORTMENT_IN,
        qty=0,
        receipt_date=None,
        note="Test: back in assortment"
    )
]

avg5_with_exclusion, oos5 = calculate_daily_sales_average(
    sales_records5, sku_id, days_lookback=30,
    transactions=transactions5, asof_date=asof
)

avg5_without_exclusion, _ = calculate_daily_sales_average(
    sales_records5, sku_id, days_lookback=30,
    transactions=[], asof_date=asof  # No transactions = no exclusions
)

print(f"Average WITH exclusion: {avg5_with_exclusion:.2f} units/day")
print(f"Average WITHOUT exclusion: {avg5_without_exclusion:.2f} units/day")
print(f"Expected WITH: 10.00 (excludes clearance period)")
print(f"Expected WITHOUT: 13.33 (includes clearance: (200 + 220) / 30)")
print(f"✅ Exclusion prevents contamination: {avg5_with_exclusion < avg5_without_exclusion}")

print("\n" + "="*70)
print("TEST COMPLETE")
print("="*70 + "\n")
