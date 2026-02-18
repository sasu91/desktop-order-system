# FASE 6: Golden Tests & Equivalence Validation

**Status**: 🔄 IN PROGRESS  
**Objective**: Validate CSV ↔ SQLite equivalence for all critical business logic  
**Approach**: Golden dataset + deterministic tests + performance benchmarks

---

## Strategy

### 1. Test Categories

#### A. **Ledger Semantics** (CRITICAL)
- Stock calculation (AsOf date logic)
- Event application order (deterministic)
- Idempotency (same event twice → same result)
- Event types impact (SNAPSHOT, ORDER, RECEIPT, SALE, WASTE, ADJUST, UNFULFILLED)

#### B. **Order Workflow** (HIGH PRIORITY)
- Order proposal generation (forecast + safety stock)
- MOQ/pack size rounding
- Lead time calculations
- Review period logic

#### C. **FEFO Lot Management** (HIGH PRIORITY)
- Lot consumption order (expiry date priority)
- Lot availability checks
- Cross-lot restocking

#### D. **Event Uplift** (MEDIUM PRIORITY)
- Uplift factor application
- Date range filtering
- Scope matching (global, category, SKU)

#### E. **Performance** (MEDIUM PRIORITY)
- Read operations (1k, 10k, 100k rows)
- Write operations (batch insert)
- Complex queries (JOIN, aggregation)

---

## 2. Golden Dataset

**Purpose**: Reproducible dataset for deterministic testing

**Characteristics**:
- **Representative**: Covers common scenarios (daily sales, weekly orders, monthly receiving)
- **Edge Cases**: Empty dates, zero quantities, large numbers, special characters
- **Realistic Scale**: 50 SKUs, 365 days of sales, 100 transactions
- **Known Outcomes**: Pre-calculated expected results for validation

**Structure**:
```
tests/golden_data/
├── skus.csv          # 50 SKUs (various categories, lead times, variability)
├── transactions.csv  # 100 transactions (all event types)
├── sales.csv         # 365 days of sales (seasonal patterns)
├── expected/
│   ├── stock_asof_2026-01-15.json      # Expected stock for specific date
│   ├── order_proposal_2026-01-20.json  # Expected order proposal
│   └── lot_consumption_sku001.json     # Expected FEFO order
└── README.md         # Dataset documentation
```

---

## 3. Test Implementation Plan

### Phase 1: Infrastructure (Setup)
- [x] Create golden dataset generator
- [ ] Write golden dataset to files
- [ ] Create test fixtures (CSV dir, SQLite DB)
- [ ] Write migration helper (golden CSV → SQLite)

### Phase 2: Equivalence Tests
- [ ] Test stock calculation equivalence (CSV vs SQLite)
- [ ] Test order proposal equivalence
- [ ] Test FEFO lot consumption equivalence
- [ ] Test event uplift equivalence

### Phase 3: Idempotency Tests
- [ ] Test receiving idempotency (duplicate receipt → no change)
- [ ] Test exception idempotency (duplicate waste → no change)
- [ ] Test order confirmation idempotency

### Phase 4: Performance Benchmarks
- [ ] Benchmark read operations (SKUs, transactions, sales)
- [ ] Benchmark write operations (single, batch)
- [ ] Benchmark complex queries (stock calculation, order proposal)
- [ ] Generate performance report

---

## 4. Test Template

```python
def test_stock_calculation_equivalence(golden_dataset):
    """
    Validate that stock calculation produces identical results 
    regardless of backend (CSV or SQLite).
    """
    # Given: Golden dataset with known SKU and transactions
    sku = "SKU001"
    asof_date = date(2026, 1, 15)
    
    # When: Calculate stock using CSV backend
    csv_adapter = StorageAdapter(data_dir=golden_dataset, force_backend='csv')
    csv_calc = StockCalculator(csv_adapter)
    csv_stock = csv_calc.calculate_stock_asof(sku, asof_date)
    
    # And: Calculate stock using SQLite backend (after migration)
    migrate_golden_to_sqlite(golden_dataset)
    sqlite_adapter = StorageAdapter(data_dir=golden_dataset, force_backend='sqlite')
    sqlite_calc = StockCalculator(sqlite_adapter)
    sqlite_stock = sqlite_calc.calculate_stock_asof(sku, asof_date)
    
    # Then: Results are identical
    assert csv_stock.on_hand == sqlite_stock.on_hand
    assert csv_stock.on_order == sqlite_stock.on_order
    
    # And: Match expected golden value
    expected = load_expected_stock(sku, asof_date)
    assert csv_stock.on_hand == expected['on_hand']
    assert csv_stock.on_order == expected['on_order']
```

---

## 5. Success Criteria

✅ **Functional Equivalence**:
- All equivalence tests pass (CSV == SQLite for identical inputs)
- All idempotency tests pass (duplicate operations are no-ops)
- All golden values match (calculated == expected)

✅ **Performance**:
- SQLite reads are ≥2x faster than CSV for large datasets
- SQLite writes are ≥1.5x faster than CSV for batch operations
- No performance regressions in critical paths

✅ **Coverage**:
- All event types tested (SNAPSHOT, ORDER, RECEIPT, SALE, WASTE, ADJUST, UNFULFILLED)
- All edge cases covered (empty dates, zero quantities, large numbers)
- All workflows tested (order, receiving, exceptions)

---

## 6. Implementation Checklist

### Golden Dataset
- [ ] Generate 50 SKUs with realistic parameters
- [ ] Generate 365 days of sales with seasonal patterns
- [ ] Generate 100 transactions covering all event types
- [ ] Calculate expected stock for key dates
- [ ] Calculate expected order proposals
- [ ] Document dataset assumptions

### Test Suite
- [ ] `tests/test_equivalence_stock.py` - Stock calculation equivalence
- [ ] `tests/test_equivalence_orders.py` - Order proposal equivalence
- [ ] `tests/test_equivalence_fefo.py` - FEFO lot consumption equivalence
- [ ] `tests/test_idempotency.py` - Idempotency validation
- [ ] `tests/test_performance.py` - Performance benchmarks

### Documentation
- [ ] Golden dataset README
- [ ] Test strategy document
- [ ] Performance report template
- [ ] FASE 6 completion summary

---

**Current Status**: Planning complete, ready to implement golden dataset generator

**Next Step**: Create golden dataset generator script
