# On-Order Pipeline by Receipt Date

## Overview

**Feature**: Granular tracking of pending orders by expected receipt date  
**Status**: ✅ COMPLETE  
**Date**: February 2026  
**Author**: Desktop Order System Team

## Problem Statement

### Before
- `on_order` was a single aggregated number per SKU
- No distinction between orders arriving Saturday vs Monday (Friday dual orders)
- Impossible to calculate accurate inventory position (IP) for future dates
- Stock planning couldn't account for delivery timing

### After
- Orders tracked with explicit `receipt_date`
- `on_order_by_date(sku)` returns `{date: qty}` mapping
- `inventory_position(sku, as_of_date)` filters orders by arrival date
- Friday dual orders (Saturday/Monday lanes) fully supported

---

## Architecture Changes

### 1. Data Model Extensions

#### `order_logs.csv` Schema Update
**Before**:
```csv
order_id,date,sku,qty_ordered,status
```

**After**:
```csv
order_id,date,sku,qty_ordered,status,receipt_date
```

**Migration**: Existing rows get `receipt_date` populated from ledger `Transaction.receipt_date` or estimated from `order_date + lead_time`.

#### `Transaction` Model (No Changes)
Already had `receipt_date` field:
```python
@dataclass(frozen=True)
class Transaction:
    date: Date
    sku: str
    event: EventType
    qty: int
    receipt_date: Optional[Date] = None  # Used for ORDER/RECEIPT events
    note: Optional[str] = None
```

### 2. New StockCalculator Methods

#### `on_order_by_date(sku, transactions, as_of_date)`

Returns pending orders grouped by expected receipt date.

**Signature**:
```python
@staticmethod
def on_order_by_date(
    sku: str,
    transactions: List[Transaction],
    as_of_date: Optional[date] = None,
) -> Dict[date, int]:
    """
    Calculate on-order quantities by expected receipt date for a SKU.
    
    Returns:
        Dict mapping {receipt_date: qty} for pending orders.
    """
```

**Algorithm**:
1. Find all `ORDER` events with `receipt_date` for SKU
2. Find all `RECEIPT` events with `receipt_date` for SKU
3. Match `RECEIPT` to `ORDER` by `receipt_date`
4. Return `ORDER` quantities not yet received

**Example**:
```python
# Friday dual order scenario
transactions = [
    Transaction(date=Fri, sku="SKU001", event=ORDER, qty=30, receipt_date=Sat),
    Transaction(date=Fri, sku="SKU001", event=ORDER, qty=50, receipt_date=Mon),
]

result = StockCalculator.on_order_by_date("SKU001", transactions)
# Returns: {Sat: 30, Mon: 50}

# After Saturday receipt
transactions.append(
    Transaction(date=Sat, sku="SKU001", event=RECEIPT, qty=30, receipt_date=Sat)
)
result = StockCalculator.on_order_by_date("SKU001", transactions)
# Returns: {Mon: 50}  # Saturday order fulfilled
```

#### `inventory_position(sku, as_of_date, transactions, sales_records)`

Calculates inventory position accounting for orders arriving by a specific date.

**Signature**:
```python
@staticmethod
def inventory_position(
    sku: str,
    as_of_date: date,
    transactions: List[Transaction],
    sales_records: Optional[List[SalesRecord]] = None,
) -> int:
    """
    Calculate inventory position (IP) as of a specific date.
    
    IP = on_hand + on_order(arriving by as_of_date) - unfulfilled_qty
    
    Returns:
        Inventory position (int)
    """
```

**Algorithm**:
1. Calculate base stock: `on_hand`, `unfulfilled_qty` (using `calculate_asof`)
2. Get pending orders by date: `on_order_by_date()`
3. Sum only orders with `receipt_date <= as_of_date`
4. Return: `on_hand + on_order_filtered - unfulfilled_qty`

**Example**:
```python
# Setup
transactions = [
    Transaction(date=2024-02-01, sku="SKU001", event=SNAPSHOT, qty=50),
    Transaction(date=2024-02-09, sku="SKU001", event=ORDER, qty=30, receipt_date=2024-02-10),  # Sat
    Transaction(date=2024-02-09, sku="SKU001", event=ORDER, qty=50, receipt_date=2024-02-12),  # Mon
]

# IP as of Saturday
ip_sat = StockCalculator.inventory_position("SKU001", Date(2024, 2, 10), transactions)
# Returns: 80 = 50 (on_hand) + 30 (order arriving Sat)

# IP as of Monday
ip_mon = StockCalculator.inventory_position("SKU001", Date(2024, 2, 12), transactions)
# Returns: 130 = 50 (on_hand) + 30 (Sat) + 50 (Mon)
```

### 3. Workflow Integration

#### `OrderWorkflow.confirm_order()`

**Before**:
- Created `ORDER` events with `receipt_date`
- Wrote to `order_logs.csv` **without** `receipt_date`

**After**:
- Creates `ORDER` events with `receipt_date` (unchanged)
- Writes to `order_logs.csv` **with** `receipt_date`

**Code Change**:
```python
# workflows/order.py
self.csv_layer.write_order_log(
    order_id=confirmation.order_id,
    date_str=confirmation.date.isoformat(),
    sku=confirmation.sku,
    qty=confirmation.qty_ordered,
    status=confirmation.status,
    receipt_date=confirmation.receipt_date.isoformat() if confirmation.receipt_date else None,  # NEW
)
```

#### `CSVLayer.write_order_log()`

**Signature Update**:
```python
def write_order_log(
    self, 
    order_id: str, 
    date_str: str, 
    sku: str, 
    qty: int, 
    status: str, 
    receipt_date: Optional[str] = None  # NEW parameter
):
```

---

## Use Cases

### 1. Friday Dual Orders (Primary Use Case)

**Scenario**:
- Friday: Place 2 orders for same SKU
  - Lane 1 (Saturday): qty=30, delivery Saturday
  - Lane 2 (Monday): qty=50, delivery Monday
- Calculate IP for Saturday (only includes Sat order)
- Calculate IP for Monday (includes both orders)

**Code**:
```python
from datetime import date as Date
from src.domain.calendar import next_receipt_date, Lane
from src.domain.ledger import StockCalculator

friday = Date(2024, 2, 9)

# Generate orders with calendar-based receipt dates
receipt_sat = next_receipt_date(friday, Lane.SATURDAY)  # 2024-02-10
receipt_mon = next_receipt_date(friday, Lane.MONDAY)    # 2024-02-12

# Create ORDER transactions
transactions = [
    Transaction(date=friday, sku="SKU001", event=ORDER, qty=30, receipt_date=receipt_sat),
    Transaction(date=friday, sku="SKU001", event=ORDER, qty=50, receipt_date=receipt_mon),
]

# IP calculations
ip_saturday = StockCalculator.inventory_position("SKU001", receipt_sat, transactions)
ip_monday = StockCalculator.inventory_position("SKU001", receipt_mon, transactions)

# Decision logic
if ip_saturday < safety_stock:
    print("Saturday delivery needed!")
elif ip_monday < safety_stock:
    print("Monday delivery sufficient")
else:
    print("No order needed")
```

### 2. Multi-Day Stock Projection

**Scenario**: Forecast stock levels for next 7 days accounting for scheduled deliveries.

**Code**:
```python
from datetime import timedelta

def project_stock_next_week(sku, start_date, daily_sales_avg):
    projections = []
    for day_offset in range(7):
        target_date = start_date + timedelta(days=day_offset)
        
        # IP includes only orders arriving by target_date
        ip = StockCalculator.inventory_position(sku, target_date, transactions, sales)
        
        # Forecast depletion
        projected_stock = ip - (day_offset * daily_sales_avg)
        
        projections.append({
            "date": target_date,
            "ip": ip,
            "projected_stock": projected_stock,
        })
    
    return projections
```

### 3. Order Fulfillment Tracking

**Scenario**: Track which orders are pending by delivery date.

**Code**:
```python
def show_pending_orders_by_date(sku):
    pending = StockCalculator.on_order_by_date(sku, transactions)
    
    print(f"Pending orders for {sku}:")
    for receipt_date, qty in sorted(pending.items()):
        print(f"  {receipt_date}: {qty} units")

# Example output:
# Pending orders for SKU001:
#   2024-02-10: 30 units
#   2024-02-12: 50 units
```

---

## Testing

### Test Coverage: 18 tests (100% pass rate)

#### Unit Tests: `test_on_order_pipeline.py`

**`TestOnOrderPipeline`** (9 tests):
- ✅ Empty ledger returns empty dict
- ✅ Single order with receipt_date
- ✅ Friday dual orders (Saturday vs Monday)
- ✅ Multiple orders for same date aggregate
- ✅ Orders without receipt_date ignored
- ✅ Filters by SKU correctly
- ✅ Receipt events reduce pending orders
- ✅ Partial receipt reduces pending
- ✅ Respects as_of_date cutoff

**`TestInventoryPosition`** (7 tests):
- ✅ Base case: IP = on_hand + on_order - unfulfilled
- ✅ Friday dual orders: IP(Saturday) includes only Sat order
- ✅ Friday dual orders: IP(Monday) includes both orders
- ✅ Excludes orders with receipt_date > as_of_date
- ✅ Accounts for unfulfilled quantities
- ✅ After receipt, on_order reduced, on_hand increased
- ✅ With sales, on_hand decreases

**`TestOnOrderPipelineIntegration`** (2 tests):
- ✅ Friday dual order full workflow (place → receive Sat → receive Mon)
- ✅ IP progression across week with daily sales and multiple orders

### Key Test Scenarios

#### Friday Dual Order Test
```python
def test_inventory_position_friday_dual_orders_saturday():
    """IP on Saturday includes only Saturday order."""
    friday = Date(2024, 2, 9)
    saturday = Date(2024, 2, 10)
    monday = Date(2024, 2, 12)
    
    transactions = [
        Transaction(date=Date(2024, 2, 1), sku="SKU001", event=SNAPSHOT, qty=50),
        Transaction(date=friday, sku="SKU001", event=ORDER, qty=30, receipt_date=saturday),
        Transaction(date=friday, sku="SKU001", event=ORDER, qty=50, receipt_date=monday),
    ]
    
    ip_saturday = StockCalculator.inventory_position("SKU001", saturday, transactions)
    assert ip_saturday == 80  # 50 + 30 (only Sat order)
    
    ip_monday = StockCalculator.inventory_position("SKU001", monday, transactions)
    assert ip_monday == 130  # 50 + 30 + 50 (both orders)
```

---

## Backward Compatibility

### ✅ Fully Backward Compatible

1. **Existing `Transaction` model**: Already had `receipt_date` field (optional)
2. **Existing ledger logic**: `calculate_asof()` unchanged, still uses aggregated `on_order`
3. **Existing workflows**: OrderWorkflow already created transactions with `receipt_date`
4. **CSV migration**: `order_logs.csv` header updated, but existing rows auto-migrated with empty `receipt_date` (optional column)

### Migration Path

**For existing `order_logs.csv` without `receipt_date`**:
1. Schema updated: `order_id,date,sku,qty_ordered,status,receipt_date`
2. Existing rows: `receipt_date` left empty or estimated from `date + lead_time`
3. New orders: `receipt_date` populated from `Transaction.receipt_date`

**No breaking changes**: Old code continues to work, new functionality is opt-in.

---

## Performance Considerations

### Time Complexity
- `on_order_by_date()`: O(n) where n = number of transactions for SKU
- `inventory_position()`: O(n + m) where m = number of pending orders (typically < 10)

### Space Complexity
- O(d) where d = number of distinct receipt dates (typically 1-5 for any SKU)

### Optimization Notes
- Both methods are stateless, can be called repeatedly without side effects
- Could cache `on_order_by_date()` result if called multiple times with same data
- For large transaction histories, consider indexing by (sku, receipt_date)

---

## Integration with Calendar Module

The on-order pipeline integrates seamlessly with the calendar module for Friday dual orders:

```python
from src.domain.calendar import next_receipt_date, Lane, calculate_protection_period_days
from src.domain.ledger import StockCalculator

# Friday dual order workflow
friday = Date.today()  # Assume it's Friday

# Calculate receipt dates using calendar
receipt_sat = next_receipt_date(friday, Lane.SATURDAY)
receipt_mon = next_receipt_date(friday, Lane.MONDAY)

# Calculate protection periods
P_sat = calculate_protection_period_days(friday, Lane.SATURDAY)  # 3 days
P_mon = calculate_protection_period_days(friday, Lane.MONDAY)    # 1 day

# Generate order proposals
forecast_sat = daily_sales_avg * P_sat
forecast_mon = daily_sales_avg * P_mon

# Check IP for each lane
ip_sat = StockCalculator.inventory_position("SKU001", receipt_sat, transactions)
ip_mon = StockCalculator.inventory_position("SKU001", receipt_mon, transactions)

# Decide which lane(s) to order
if ip_sat < safety_stock + forecast_sat:
    # Need Saturday delivery
    create_order(qty=forecast_sat, receipt_date=receipt_sat)
elif ip_mon < safety_stock + forecast_mon:
    # Monday delivery sufficient
    create_order(qty=forecast_mon, receipt_date=receipt_mon)
```

---

## Future Enhancements

### Potential Extensions
1. **Multi-supplier pipeline**: Track orders by (receipt_date, supplier)
2. **Partial receipts**: `on_order_by_date()` could return `(ordered, received, pending)` tuple
3. **Delivery windows**: Group orders by week instead of exact date
4. **Late delivery tracking**: Flag orders with `receipt_date < today` and status PENDING

### API Evolution
Current methods are designed to be extensible:
- `on_order_by_date()` could add `group_by_supplier` parameter
- `inventory_position()` could add `include_safety_stock` parameter
- Both methods are static, no state dependencies

---

## References

- **Code**: [src/domain/ledger.py](src/domain/ledger.py#L120-L238)
- **Tests**: [tests/test_on_order_pipeline.py](tests/test_on_order_pipeline.py)
- **Calendar Module**: [CALENDAR_MODULE.md](CALENDAR_MODULE.md)
- **Related PRs**: [Calendar Implementation](CALENDAR_IMPLEMENTATION_SUMMARY.md)

---

## Change Log

| Date | Version | Change |
|------|---------|--------|
| 2026-02-02 | 1.0 | Initial implementation |
|  |  | - Added `on_order_by_date()` |
|  |  | - Added `inventory_position()` |
|  |  | - Extended `order_logs.csv` schema |
|  |  | - 18 tests, 100% passing |

---

**Status**: ✅ COMPLETE  
**Approved for Production**: YES  
**Breaking Changes**: NONE
