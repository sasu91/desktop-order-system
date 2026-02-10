# Shelf Life Integration - Phase 5 Testing Plan

**Status**: Planning  
**Target**: Comprehensive validation before production deployment  
**Estimated Duration**: 4-6 hours  

---

## Objectives

1. **Stress Testing**: Validate scalability with production-scale data volumes
2. **Edge Case Coverage**: Ensure robustness against extreme scenarios
3. **Performance Validation**: Confirm acceptable response times for batch operations
4. **Integration Testing**: Verify complete workflow from lot creation to order proposal
5. **Regression Prevention**: Lock in current behavior for future refactoring

---

## Test Categories

### Category 1: Stress Tests (Scale Validation)

#### Test 1.1: Large SKU Catalog
**Goal**: Validate order generation with 10+ SKUs

**Setup**:
```python
# Generate 10 SKUs with varied shelf life configs
skus = []
for i in range(10):
    category = random.choice(["STABLE", "LOW", "HIGH", "SEASONAL"])
    shelf_life = random.randint(0, 120) if i % 3 == 0 else 0  # 33% perishable
    skus.append(SKU(
        sku=f"SKU_{i:04d}",
        description=f"Product {i}",
        pack_size=random.randint(1, 24),
        shelf_life_days=shelf_life,
        min_shelf_life_days=shelf_life // 2 if shelf_life > 0 else 0,
        waste_penalty_mode=random.choice(["", "soft", "hard"]) if shelf_life > 0 else "",
        demand_variability=DemandVariability[category]
    ))
```

**Assertions**:
- Order generation completes in < 1 second for 10 SKUs
- No memory leaks (check memory before/after)
- All SKUs with shelf_life_days > 0 have usable_stock calculated
- At least 1 perishable SKU has penalty applied

**Success Criteria**: ✅ < 1s execution, ✅ Memory stable, ✅ All shelf life SKUs processed

---

#### Test 1.2: High Lot Volume per SKU
**Goal**: Validate usable stock calculation with 10+ lots per SKU

**Setup**:
```python
# Create 1 SKU with 10 lots (simulating high-volume warehouse)
lots = []
base_date = date(2026, 2, 10)
for i in range(10):
    expiry = base_date + timedelta(days=random.randint(1, 90))
    lots.append(Lot(
        lot_id=f"LOT_{i:03d}",
        sku="HIGH_VOLUME_SKU",
        expiry_date=expiry,
        qty_on_hand=random.randint(10, 500),
        receipt_id=f"RCV_{i}",
        receipt_date=base_date - timedelta(days=random.randint(1, 30))
    ))
```

**Assertions**:
- `calculate_usable_stock()` completes in < 10ms for 10 lots
- Usable/unusable split is mathematically correct (manual validation on subset)
- Waste_risk_percent accurately reflects expiry distribution

**Success Criteria**: ✅ < 10ms per SKU, ✅ Correct arithmetic, ✅ No overflow errors

---

#### Test 1.3: Batch Order Generation (Full Catalog)
**Goal**: End-to-end order workflow with 50 SKUs + 500 lots

**Setup**:
```python
# 50 SKUs, 10 lots each on average = 500 lots total
# Mix: 40% no shelf life, 30% low waste risk, 20% medium, 10% high
```

**Assertions**:
- Complete order generation in < 3 seconds
- OrderProposal list length = 50 (all SKUs processed)
- Penalty distribution matches expected profile (10% blocked, 20% reduced)
- No database/CSV corruption after batch write

**Success Criteria**: ✅ < 3s total, ✅ 50 proposals, ✅ Data integrity intact

---

### Category 2: Edge Cases (Robustness)

#### Test 2.1: 100% Unusable Stock
**Goal**: Handle SKU with all lots expired or insufficient shelf life

**Scenario**:
```python
# All lots expired or < min_shelf_life_days
lots = [
    Lot(lot_id="L1", sku="EXPIRED_SKU", expiry_date=date(2026, 2, 1), qty_on_hand=50, ...),  # Expired
    Lot(lot_id="L2", sku="EXPIRED_SKU", expiry_date=date(2026, 2, 12), qty_on_hand=30, ...),  # Only 2d left, min=7d
]
# Total: 80 units, usable: 0, waste_risk: 100%
```

**Expected Behavior**:
- `usable_qty = 0`
- `waste_risk_percent = 100.0`
- IP calculation: `IP = 0 + on_order - unfulfilled`
- Penalty: If mode="hard" → `proposed_qty = 0`, message = "❌ BLOCKED: Waste risk 100.0% > 20.0%"
- UI Display: "Stock Usabile" shows "0/80"

**Assertions**:
- No division by zero errors
- Order proposal = 0 units (hard mode) or minimal (soft mode)
- UI renders correctly without crashes

**Success Criteria**: ✅ Graceful degradation, ✅ Clear warning message

---

#### Test 2.2: Zero Waste Risk (All Fresh Stock)
**Goal**: Verify no penalty when all lots have long shelf life

**Scenario**:
```python
# All lots expire far in future (> waste_horizon_days)
lots = [
    Lot(lot_id="L1", sku="FRESH_SKU", expiry_date=date(2026, 4, 10), qty_on_hand=100, ...),  # 60d away
]
# waste_horizon_days=30 → expiring_soon_qty=0 → waste_risk=0%
```

**Expected Behavior**:
- `waste_risk_percent = 0.0`
- No penalty applied (`shelf_life_penalty_applied = False`)
- IP uses full `usable_qty = 100`
- Monte Carlo: `expected_waste_rate = 0.0` → no forecast reduction

**Assertions**:
- Penalty message empty
- Proposed qty NOT reduced (matches non-shelf-life calculation)
- MC forecast = simple forecast (when waste=0)

**Success Criteria**: ✅ No false positives, ✅ Penalty logic bypassed correctly

---

#### Test 2.3: No Lots Exist (New SKU)
**Goal**: Handle SKU with shelf_life_days > 0 but no lots yet

**Scenario**:
```python
sku = SKU(sku="NEW_SKU", shelf_life_days=21, min_shelf_life_days=14, ...)
# lots.csv has NO entries for "NEW_SKU"
```

**Expected Behavior**:
- `calculate_usable_stock(lots=[])` returns `UsableStockResult(0, 0, 0, 0, 0.0)`
- IP = 0 + on_order
- Order proposal generated normally (triggers reorder from scratch)
- No penalty (waste_risk=0%)

**Assertions**:
- No KeyError or crashes
- Proposal generated with proposed_qty based on forecast alone
- UI shows "0/0" for Stock Usabile

**Success Criteria**: ✅ Handles empty lots gracefully, ✅ New SKU reorder works

---

#### Test 2.4: Exactly at Threshold (Boundary Test)
**Goal**: Verify penalty trigger at exact threshold value

**Scenario**:
```python
# waste_risk_percent = 20.0, waste_risk_threshold = 20.0
# Should penalty apply? (>= vs >)
```

**Expected Behavior**:
- Per code: `if waste_risk_percent >= waste_risk_threshold:` → YES, penalty applies
- Proposed qty SHOULD be reduced

**Assertions**:
- Penalty applied when `waste_risk == threshold` (>= logic)
- Penalty NOT applied when `waste_risk < threshold` (19.9%)

**Success Criteria**: ✅ Boundary condition handled correctly

---

#### Test 2.5: Negative Quantities (Data Integrity)
**Goal**: Validate system rejects invalid lot quantities

**Scenario**:
```python
# Malformed lot with negative qty_on_hand
lot = Lot(lot_id="BAD", sku="TEST", qty_on_hand=-10, ...)  # Should fail validation
```

**Expected Behavior**:
- `Lot.__post_init__()` raises `ValueError("Lot quantity cannot be negative")`
- CSV read skips malformed row with warning log
- No crashes in downstream calculations

**Assertions**:
- ValueError raised at model level
- CSV layer logs warning, continues processing
- Usable stock calculation ignores bad data

**Success Criteria**: ✅ Data validation enforced, ✅ Bad data isolated

---

### Category 3: Performance Benchmarks

#### Benchmark 3.1: Usable Stock Calculation
**Target**: < 10ms per SKU for typical lot counts (5-20 lots)

**Test Matrix**:
| Lot Count | Expected Time | Max Allowed |
|-----------|---------------|-------------|
| 1         | < 1ms         | 5ms         |
| 10        | < 5ms         | 10ms        |
| 50        | < 20ms        | 50ms        |
| 100       | < 50ms        | 100ms       |

**Implementation**:
```python
import time

def benchmark_usable_stock(num_lots):
    lots = generate_random_lots("SKU_BENCH", num_lots)
    
    start = time.perf_counter()
    for _ in range(100):  # 100 iterations
        ShelfLifeCalculator.calculate_usable_stock(
            lots=lots,
            check_date=date.today(),
            min_shelf_life_days=7,
            waste_horizon_days=30
        )
    end = time.perf_counter()
    
    avg_time_ms = (end - start) / 100 * 1000
    return avg_time_ms
```

**Success Criteria**: ✅ All lot counts meet max allowed time

---

#### Benchmark 3.2: Order Generation Throughput
**Target**: ≥ 50 SKUs/second for batch order generation

**Test**:
```python
def benchmark_order_generation():
    # 500 SKUs with mixed shelf life configs
    workflow = OrderWorkflow(csv_layer)
    
    start = time.perf_counter()
    proposals = []
    for sku_code in sku_list:
        proposal = workflow.generate_order_proposal(sku_code)
        proposals.append(proposal)
    end = time.perf_counter()
    
    throughput = len(proposals) / (end - start)
    print(f"Throughput: {throughput:.2f} SKUs/sec")
    assert throughput >= 50, f"Too slow: {throughput} < 50"
```

**Success Criteria**: ✅ ≥ 50 SKUs/sec on standard hardware

---

#### Benchmark 3.3: Monte Carlo Overhead with Waste
**Target**: < 10% slowdown vs. MC without waste

**Test**:
```python
def benchmark_mc_waste_overhead():
    sales = generate_sales_data(365)  # 1 year
    
    # Baseline: MC without waste
    start1 = time.perf_counter()
    result1 = monte_carlo_forecast(sales, horizon_days=14, expected_waste_rate=0.0)
    time1 = time.perf_counter() - start1
    
    # With waste: MC with 20% waste
    start2 = time.perf_counter()
    result2 = monte_carlo_forecast(sales, horizon_days=14, expected_waste_rate=0.2)
    time2 = time.perf_counter() - start2
    
    overhead_pct = (time2 - time1) / time1 * 100
    print(f"Overhead: {overhead_pct:.2f}%")
    assert overhead_pct < 10, f"Excessive overhead: {overhead_pct}%"
```

**Success Criteria**: ✅ < 10% overhead (waste reduction is O(n) operation)

---

### Category 4: Integration Tests (End-to-End)

#### Integration 4.1: Complete Replenishment Cycle
**Workflow**:
1. Create SKU with shelf life parameters
2. Receive order (create lots with expiry dates)
3. Process daily sales (consume lots via FEFO - manual for now)
4. Generate order proposal (uses usable stock)
5. Confirm order
6. Verify ledger audit trail

**Steps**:
```python
# Day 1: Setup
sku = SKU(sku="MILK_001", shelf_life_days=7, min_shelf_life_days=5, ...)
csv_layer.write_sku(sku)

# Day 1: Receive initial stock
lots = [Lot(lot_id="L1", sku="MILK_001", expiry_date=date(2026, 2, 17), qty_on_hand=100, ...)]
csv_layer.write_lot(lots[0])

# Day 5: Stock aging
check_result = ShelfLifeCalculator.calculate_usable_stock(
    lots=lots,
    check_date=date(2026, 2, 15),  # 2 days before expiry
    min_shelf_life_days=5
)
# Expected: usable=0 (only 2d left, need 5d min)

# Day 5: Order proposal
proposal = order_workflow.generate_order_proposal("MILK_001")
assert proposal.usable_stock == 0
assert proposal.unusable_stock == 100
assert proposal.inventory_position == 0  # IP uses usable_stock

# Day 5: Confirm order
order_workflow.confirm_order("MILK_001", qty=100)

# Verify: Order logged, receipt_date set, ledger updated
orders = csv_layer.read_order_logs()
assert len(orders) == 1
assert orders[0].sku == "MILK_001"
```

**Success Criteria**: ✅ Complete cycle executes without errors, ✅ Audit trail complete

---

#### Integration 4.2: Multi-Category Batch Processing
**Scenario**: Mixed catalog with all demand variability categories + varied shelf life

**Setup**:
```python
# 100 SKUs: 25 STABLE, 25 LOW, 25 HIGH, 25 SEASONAL
# 50 SKUs have shelf_life_days > 0
# Category overrides in settings for HIGH + SEASONAL
```

**Workflow**:
1. Batch generate proposals for all 100 SKUs
2. Verify category overrides applied correctly:
   - HIGH SKUs: use `waste_penalty_factor=0.6, threshold=15.0`
   - SEASONAL SKUs: use `waste_penalty_factor=0.7, threshold=25.0`
3. Confirm subset of proposals
4. Verify order_logs.csv has correct entries

**Assertions**:
- All 100 proposals generated
- HIGH category SKUs have lower threshold (15% vs 20% global)
- SEASONAL SKUs have higher penalty factor (0.7 vs 0.5 global)
- Confirmed orders appear in ledger with ORDER events

**Success Criteria**: ✅ Category logic correct, ✅ No cross-contamination

---

### Category 5: Regression Tests (Lock Current Behavior)

#### Regression 5.1: Existing Tests Still Pass
**Goal**: Ensure Phases 1-4 changes don't break unrelated features

**Test Suite**:
```bash
# Run full test suite
pytest tests/ -v --tb=short

# Expected results:
# - test_stock_calculation.py: PASS (ledger core)
# - test_workflows.py: PASS (order/receiving workflows)
# - test_calendar_integration.py: PASS (censored days)
# - test_forecast.py: PASS (MC forecast)
# - test_replenishment_policy.py: PASS (automatic variability)
# - test_shelf_life_ui_integration.py: PASS (Phase 4)
```

**Success Criteria**: ✅ 100% existing tests pass (no regressions)

---

#### Regression 5.2: Backward Compatibility (No Shelf Life)
**Goal**: SKUs without shelf_life_days=0 work identically to pre-Phase 1 behavior

**Test**:
```python
# Create SKU without shelf life (shelf_life_days=0)
sku = SKU(sku="NON_PERISHABLE", shelf_life_days=0, ...)

# Verify:
# 1. No usable stock calculation triggered
# 2. IP = on_hand + on_order (old formula)
# 3. No penalty applied
# 4. MC forecast: expected_waste_rate=0.0
# 5. UI shows empty shelf life columns
```

**Assertions**:
- Order proposal identical to pre-shelf-life system
- No performance degradation
- UI displays gracefully (no "N/A" or errors)

**Success Criteria**: ✅ Zero impact on non-perishable SKUs

---

### Category 6: Manual UI Testing (GUI Validation)

**Prerequisites**: Windows environment with GUI display, test database with sample data

#### Manual Test 6.1: Settings Tab Shelf Life Section
**Steps**:
1. Open GUI → Settings tab
2. Locate "♻️ Shelf Life & Gestione Scadenze" section
3. Expand section
4. Modify each parameter:
   - Toggle "Abilita Shelf Life" → Verify checkbox works
   - Change "Shelf Life Minima Globale" to 21 → Verify int validation
   - Select "Modalità Penalità" = "hard" → Verify dropdown
   - Set "Fattore Penalità" = "0.8" → Verify float validation (should accept)
   - Set "Fattore Penalità" = "1.5" → Verify error (out of range 0-1)
5. Click "💾 Salva Impostazioni"
6. Reload GUI, verify settings persisted

**Expected**:
- All controls interactive
- Validation errors shown in messagebox
- Settings.json updated correctly
- No GUI crashes

**Screenshot**: Capture before/after settings save

---

#### Manual Test 6.2: SKU Form Shelf Life Fields
**Steps**:
1. Admin tab → "Nuovo SKU"
2. Fill basic fields (SKU code, description)
3. Expand "♻️ Shelf Life & Scadenze" section
4. Fill shelf life fields:
   - Shelf Life Minima: 14
   - Modalità Penalità: "soft"
   - Fattore Penalità: 0.5
   - Soglia Rischio: 20.0
5. Save SKU
6. Reload SKU in edit mode
7. Verify all shelf life fields persisted

**Expected**:
- Form section collapses/expands correctly
- Dropdown choices match ("", "soft", "hard")
- Validation prevents min_shelf_life > shelf_life_days
- CSV correctly updated

**Screenshot**: Capture SKU form with shelf life section

---

#### Manual Test 6.3: Order Tab Proposal Display
**Steps**:
1. Prepare test data:
   - SKU with shelf_life_days=21, min=14
   - 3 lots: 1 expired, 1 expiring soon, 1 fresh
   - waste_risk ~30% (above threshold 20%)
2. Order tab → "Genera Tutte le Proposte"
3. Locate test SKU in proposals table
4. Verify columns:
   - "Stock Usabile" shows "X/Y" format (usable/total)
   - "Rischio ♻️" shows "30.0%"
   - "Penalità ⚠️" shows "Reduced by 50%"
5. Single-click row → Check sidebar details
6. Verify sidebar shows:
   - Stock breakdown (usable/unusable)
   - Waste risk percentage
   - Penalty message
   - IP formula uses usable_stock

**Expected**:
- All new columns visible and populated
- Sidebar updates on selection
- Data matches backend calculation (cross-check with CSV)
- No rendering glitches

**Screenshot**: Capture proposal table + sidebar with shelf life data

---

#### Manual Test 6.4: Stress Test UI Responsiveness
**Steps**:
1. Load 50 SKUs into system
2. Order tab → "Genera Tutte le Proposte"
3. Measure time to:
   - Generate proposals (backend)
   - Render table (frontend)
4. Scroll through proposals → Check for lag
5. Click multiple SKUs rapidly → Check sidebar updates

**Expected**:
- Proposal generation < 3 seconds
- Table rendering < 1 second
- Scrolling smooth (60fps target)
- No UI freezing or crashes

**Performance Log**: Record timings for 10, 30, 50 SKUs

---

## Test Execution Plan

### Phase 5.1: Automated Tests (Day 1-2)
**Duration**: 4-6 hours

1. **Create test files**:
   - `tests/test_shelf_life_stress.py` (Category 1: Stress)
   - `tests/test_shelf_life_edge_cases.py` (Category 2: Edge cases)
   - `tests/test_shelf_life_performance.py` (Category 3: Benchmarks)
   - `tests/test_shelf_life_integration.py` (Category 4: E2E)

2. **Run test suite**:
   ```bash
   # Stress tests
   pytest tests/test_shelf_life_stress.py -v --durations=10
   
   # Edge cases
   pytest tests/test_shelf_life_edge_cases.py -v
   
   # Performance benchmarks
   pytest tests/test_shelf_life_performance.py -v --benchmark-only
   
   # Integration tests
   pytest tests/test_shelf_life_integration.py -v
   
   # Regression
   pytest tests/ -v --ignore=tests/test_shelf_life_*.py
   ```

3. **Collect metrics**:
   - Test pass/fail counts
   - Performance benchmark results
   - Code coverage report (target: >90% for shelf life modules)

---

### Phase 5.2: Manual GUI Testing (Day 3)
**Duration**: 2-3 hours  
**Environment**: Windows 10/11 with Python 3.12 + Tkinter

**Test Execution**:
1. Manual Test 6.1 (Settings) → 30 min
2. Manual Test 6.2 (SKU Form) → 30 min
3. Manual Test 6.3 (Order Display) → 45 min
4. Manual Test 6.4 (Stress UI) → 30 min
5. Screenshot collection → 15 min

**Deliverable**: Manual test report with screenshots

---

## Success Criteria Summary

| Category | Tests | Target | Critical |
|----------|-------|--------|----------|
| Stress Tests | 3 | All pass | ✅ YES |
| Edge Cases | 5 | All pass | ✅ YES |
| Performance | 3 | Meet benchmarks | ⚠️ Advisory |
| Integration | 2 | All pass | ✅ YES |
| Regression | 2 | 100% pass | ✅ YES |
| Manual UI | 4 | All scenarios work | ⚠️ Advisory |

**Overall Success**: ≥ 90% critical tests passing + no performance regressions > 20%

---

## Risk Mitigation

### Risk 1: Performance Degradation at Scale
**Mitigation**:
- Profile hotspots with `cProfile`
- Optimize lot sorting (pre-sort by expiry_date)
- Cache usable_stock results per SKU (invalidate on lot change)

### Risk 2: GUI Testing Environment Unavailable
**Mitigation**:
- Defer Manual UI tests to Phase 6
- Automated Selenium/PyAutoGUI tests as fallback
- Screenshot validation on CI/CD with X virtual framebuffer

### Risk 3: Edge Cases Uncover Critical Bugs
**Mitigation**:
- Fix immediately if data integrity at risk
- Document as "Known Issue" if low impact
- Add regression test to prevent recurrence

---

## Test Data Requirements

### Minimal Test Dataset (For Quick Tests)
- 10 SKUs (5 perishable, 5 non-perishable)
- 50 lots total
- 30 days sales history
- 3 demand variability categories

### Full Test Dataset (For Stress Tests)
- 50 SKUs (15 perishable)
- 500 lots total
- 365 days sales history
- All 4 demand variability categories
- Settings with category overrides

**Data Generation Script**: `tests/fixtures/generate_shelf_life_test_data.py`

---

## Deliverables

1. **Test Suite**: 4 new test files (~600-800 lines total)
2. **Performance Report**: Benchmark results CSV/JSON
3. **Manual Test Report**: Screenshots + checklist PDF
4. **Bug List**: Issues discovered (GitHub issues or KNOWN_ISSUES.md)
5. **Coverage Report**: HTML coverage report (pytest-cov)

---

## Next Steps After Phase 5

If all tests pass:
- ✅ **Proceed to Phase 6**: Documentation & user training
- ✅ **Tag release**: `v2.0.0-shelf-life-complete`
- ✅ **Production deployment**: Gradual rollout plan

If critical tests fail:
- 🔴 **Fix bugs**: Address failures in order of severity
- 🔴 **Re-run tests**: Regression suite + failed tests
- 🔴 **Update documentation**: Known limitations section

---

**Prepared by**: AI Agent  
**Date**: February 10, 2026  
**Status**: Ready for execution  
**Approval**: Pending stakeholder review
