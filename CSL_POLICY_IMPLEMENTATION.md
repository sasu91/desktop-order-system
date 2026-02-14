# CSL-Based Order Policy Implementation Summary

## Objective
Integrate the existing CSL (Customer Service Level) engine from `replenishment_policy.py` into the order proposal workflow, enabling users to switch between:
- **Legacy Mode**: Traditional formula `S = forecast + safety_stock`, `proposed = max(0, S - IP)`
- **CSL Mode**: Target service level driven policy using `compute_order()` with per-SKU alpha resolution

## Implementation Details

### 1. Pipeline Builder Module (`src/analytics/pipeline.py`)
**Purpose**: Extract unfulfilled orders from `order_logs.csv` to build the open pipeline for CSL calculations.

**Key Features**:
- Filters orders by `qty_unfulfilled > 0`
- Filters by `receipt_date > asof_date` (future arrivals only)
- Parses ISO date strings with error handling
- Sorts by `receipt_date` ascending
- Returns list of `{"receipt_date": date, "qty": int}` dictionaries

**Usage**:
```python
from src.analytics.pipeline import build_open_pipeline

pipeline = build_open_pipeline(csv_layer, "SKU001", date(2025, 1, 15))
# [{"receipt_date": date(2025, 1, 20), "qty": 50}, ...]
```

### 2. Settings Extension
**Added Parameter**: `reorder_engine.policy_mode`
- **Type**: Choice (`"legacy"` | `"csl"`)
- **Default**: `"legacy"`
- **Auto-apply to new SKU**: `False`

**Location**: 
- Backend: `src/persistence/csv_layer.py` (line ~1084 in DEFAULT_SETTINGS)
- GUI: `src/gui/app.py` (Settings tab → Reorder Engine section, line ~5386)

**GUI Integration**:
- Parameter mapping in `_refresh_settings_tab` (line ~6228)
- Parameter mapping in `_save_settings` (line ~6298)

### 3. OrderProposal Model Extension (`src/domain/models.py`)
**New Fields** (lines 401-410):
```python
csl_policy_mode: str = ""           # "legacy", "csl", or "legacy_fallback"
csl_alpha_target: float = 0.0       # Target CSL (α)
csl_alpha_eff: float = 0.0          # Effective α after censored boost
csl_reorder_point: float = 0.0      # S (reorder point)
csl_forecast_demand: float = 0.0    # μ_P (forecast demand)
csl_sigma_horizon: float = 0.0      # σ_P (demand uncertainty)
csl_z_score: float = 0.0            # z-score for target CSL
csl_lane: str = ""                  # STANDARD, SATURDAY, MONDAY
csl_n_censored: int = 0             # Number of censored periods
```

### 4. OrderWorkflow Integration (`src/workflows/order.py`)

#### 4.1. New Imports (lines 1-13)
- `TargetServiceLevelResolver` from `analytics.target_resolver`
- `Lane`, `next_receipt_date`, `calculate_protection_period_days` from `domain.calendar`
- `build_open_pipeline` from `analytics.pipeline`
- `compute_order`, `OrderConstraints` from `replenishment_policy`

#### 4.2. Lane Deduction Helper (lines 165-211)
**Method**: `_deduce_lane(target_receipt_date, protection_period_days, order_date) -> Lane`

**Purpose**: Reverse-engineer the Lane from calendar parameters by trying each lane candidate and matching results.

**Logic**:
1. Try `Lane.SATURDAY`, `Lane.MONDAY`, `Lane.STANDARD` in sequence
2. For each candidate, compute expected `next_receipt_date` and `calculate_protection_period_days`
3. Return first match where both values align with input parameters
4. Fallback to `Lane.STANDARD` if no match

#### 4.3. Pipeline Extra Parameter (line 228)
**New Parameter**: `pipeline_extra: Optional[List[dict]] = None`

**Purpose**: Support Friday dual-lane scenarios where the Saturday proposal becomes part of the Monday pipeline.

**Usage**:
```python
# Saturday proposal
sat_proposal = workflow.generate_proposal(..., target_receipt_date=sat_date, ...)

# Monday proposal with Saturday order in pipeline
pipeline_extra = [{"receipt_date": sat_date, "qty": sat_proposal.proposed_qty}]
mon_proposal = workflow.generate_proposal(..., pipeline_extra=pipeline_extra, ...)
```

#### 4.4. CSL Policy Branch (lines 316-334, 710-788)
**Location**: After settings read, before legacy formula execution

**CSL Mode Flow**:
1. **Read policy_mode** from settings (line 322)
2. **Resolve target α**:
   - Create `TargetServiceLevelResolver(settings)`
   - Call `get_target_csl(sku_obj)` with SKU-specific override fallback
   - Default to global `default_csl` if no SKU object
3. **Build pipeline** (lines 714-720):
   - Call `build_open_pipeline(csv_layer, sku, order_date)`
   - Append `pipeline_extra` if provided (Friday dual-lane support)
   - Re-sort by `receipt_date`
4. **Deduce lane** (line 723):
   - Call `_deduce_lane(target_receipt_date, protection_period_days, order_date)`
5. **Build constraints** (lines 725-729):
   - Create `OrderConstraints(pack_size, moq, max_stock)`
6. **Prepare sales history** (lines 731-738):
   - Convert `SalesRecord` list to dict format for `compute_order`
7. **Call compute_order** (lines 740-750):
   - Pass `sku`, `order_date`, `lane`, `alpha`, `on_hand=usable_qty`, `pipeline`, `constraints`, `history`
   - Window: 12 weeks (default forecast horizon)
   - Censored flags: `None` (TODO: integrate censored days detection)
8. **Extract results** (lines 752-769):
   - `proposed_qty_raw = csl_result["order_final"]`
   - Store breakdown: `alpha_target`, `alpha_eff`, `reorder_point`, `forecast_demand`, `sigma_horizon`, `z_score`, `lane`, `n_censored`
9. **Error handling** (lines 771-778):
   - On exception, log error and fallback to legacy formula
   - Mark as `"legacy_fallback"` in breakdown

**Legacy Mode Flow** (lines 780-799):
- Unchanged from previous implementation
- Use traditional formula or simulation for intermittent demand
- Mark as `"legacy"` in breakdown

#### 4.5. OrderProposal Construction (lines 1265-1273)
**CSL Fields Population**:
```python
csl_policy_mode=str(csl_breakdown.get("policy_mode", "")),
csl_alpha_target=float(csl_breakdown.get("alpha_target", 0.0)),
csl_alpha_eff=float(csl_breakdown.get("alpha_eff", 0.0)),
csl_reorder_point=float(csl_breakdown.get("reorder_point", 0.0)),
csl_forecast_demand=float(csl_breakdown.get("forecast_demand", 0.0)),
csl_sigma_horizon=float(csl_breakdown.get("sigma_horizon", 0.0)),
csl_z_score=float(csl_breakdown.get("z_score", 0.0)),
csl_lane=str(csl_breakdown.get("lane", "")),
csl_n_censored=int(csl_breakdown.get("n_censored", 0)),
```

### 5. GUI Proposal Details Extension (`src/gui/app.py`)
**Location**: `_show_proposal_details` method (lines 1163-1181)

**New Section**: "═══ CSL POLICY BREAKDOWN ═══"

**Displayed Fields** (when `proposal.csl_policy_mode == "csl"`):
- Policy Mode: CSL (Target Service Level)
- Lane: STANDARD / SATURDAY / MONDAY
- Target α (CSL): 0.950
- Effective α (after censored boost): 0.955 (if different from target)
- z-score: 1.64
- Reorder Point S: 85.2 pz
- Forecast Demand μ_P: 70.0 pz
- Demand Uncertainty σ_P: 9.3 pz
- ⚠️ Censored periods detected: 3 (if applicable)

**Position**: After "═══ TARGET S ═══" section, before "═══ INVENTORY POSITION (IP) ═══"

### 6. Testing (`test_csl_policy_integration.py`)

#### Test Coverage:
1. **Pipeline Builder Tests** (4 tests):
   - Empty pipeline
   - Filters past dates
   - Sorts by receipt_date
   - Ignores invalid receipt_date

2. **Policy Mode Tests** (2 tests):
   - Legacy mode uses traditional formula
   - CSL mode populates breakdown fields

3. **Lane Deduction Tests** (3 tests):
   - Deduce STANDARD lane
   - Deduce SATURDAY lane
   - Deduce MONDAY lane

4. **Integration Tests** (2 tests):
   - Friday dual-lane with pipeline_extra
   - CSL fallback on error

#### Test Results:
```
11 passed in 0.07s
```

### 7. Backward Compatibility Verification

**Existing Test Suites**:
- `tests/test_workflows.py`: 17/17 passed ✅
- `tests/test_calendar_aware_proposals.py`: 4/4 passed ✅

**Regression Safety**:
- Legacy mode produces identical results (default `policy_mode="legacy"`)
- All calendar-aware features (Friday dual-lane, protection period) preserved
- No breaking changes to OrderProposal structure (new fields optional, default to 0/"")

## Usage Examples

### Example 1: Legacy Mode (Default)
```python
# Settings: policy_mode = "legacy"
workflow = OrderWorkflow(csv_layer)

proposal = workflow.generate_proposal(
    sku="SKU001",
    description="Test Product",
    current_stock=Stock(sku="SKU001", on_hand=50, on_order=20, unfulfilled_qty=0),
    daily_sales_avg=5.0,
    sku_obj=sku_obj,
)

# Result: Traditional S = forecast + safety formula
assert proposal.csl_policy_mode == "legacy"
assert proposal.proposed_qty == 8  # max(0, 78 - 70)
```

### Example 2: CSL Mode
```python
# Settings: policy_mode = "csl"
# SKU: target_csl = 0.98
workflow = OrderWorkflow(csv_layer)

proposal = workflow.generate_proposal(
    sku="SKU001",
    description="Test Product",
    current_stock=Stock(sku="SKU001", on_hand=50, on_order=0, unfulfilled_qty=0),
    daily_sales_avg=5.0,
    sku_obj=sku_obj,
    sales_records=sales_history,
)

# Result: CSL-driven order with breakdown
assert proposal.csl_policy_mode == "csl"
assert proposal.csl_alpha_target == 0.98  # SKU-specific CSL
assert proposal.csl_reorder_point > 0     # S calculated by CSL engine
assert proposal.csl_lane == "STANDARD"
```

### Example 3: Friday Dual-Lane (CSL Mode)
```python
# Settings: policy_mode = "csl"
order_date = date(2025, 1, 17)  # Friday

# Saturday lane proposal
sat_receipt_date = next_receipt_date(order_date, Lane.SATURDAY)
sat_protection = calculate_protection_period_days(order_date, Lane.SATURDAY)

sat_proposal = workflow.generate_proposal(
    sku="SKU001",
    current_stock=stock,
    daily_sales_avg=5.0,
    sku_obj=sku_obj,
    target_receipt_date=sat_receipt_date,
    protection_period_days=sat_protection,
    sales_records=sales_history,
)

# Monday lane proposal with Saturday order in pipeline
mon_receipt_date = next_receipt_date(order_date, Lane.MONDAY)
mon_protection = calculate_protection_period_days(order_date, Lane.MONDAY)

pipeline_extra = [
    {"receipt_date": sat_receipt_date, "qty": sat_proposal.proposed_qty}
] if sat_proposal.proposed_qty > 0 else []

mon_proposal = workflow.generate_proposal(
    sku="SKU001",
    current_stock=stock,
    daily_sales_avg=5.0,
    sku_obj=sku_obj,
    target_receipt_date=mon_receipt_date,
    protection_period_days=mon_protection,
    sales_records=sales_history,
    pipeline_extra=pipeline_extra,  # Saturday order accounted for
)

assert sat_proposal.csl_lane == "SATURDAY"
assert mon_proposal.csl_lane == "MONDAY"
```

## Key Design Decisions

### 1. **Backward Compatibility First**
- Default to legacy mode to preserve existing behavior
- CSL mode opt-in via settings
- All existing tests pass without modification

### 2. **Transparent Breakdown**
- All CSL calculation details exposed in OrderProposal
- GUI displays alpha, S, μ_P, σ_P, z-score for transparency
- Lane and censored period count shown

### 3. **Graceful Fallback**
- CSL computation errors don't crash the workflow
- Fallback to legacy formula with clear logging
- `csl_policy_mode="legacy_fallback"` marker

### 4. **Friday Dual-Lane Support**
- `pipeline_extra` parameter enables second proposal to account for first
- Compatible with both legacy and CSL modes
- Lane deduction from calendar parameters

### 5. **Extensibility**
- Pipeline builder modular (can be used independently)
- TargetServiceLevelResolver already integrated (priority chain: SKU → perishability → variability → default)
- Censored days detection placeholder (TODO for future enhancement)

## Future Enhancements

1. **Censored Days Integration**: Pass `censored_flags` to `compute_order` from auto-variability module
2. **Pipeline Aggregation**: Optionally aggregate multiple orders with same receipt_date
3. **CSL History Tracking**: Store alpha_eff, n_censored in order_logs for analytics
4. **GUI Lane Selector**: Allow manual lane override in proposal tab (currently auto-deduced)
5. **Performance Optimization**: Cache pipeline builds for multiple SKUs on same order_date

## Files Modified

1. **Created**:
   - `src/analytics/pipeline.py` (109 lines)
   - `test_csl_policy_integration.py` (405 lines)

2. **Modified**:
   - `src/persistence/csv_layer.py` (3 lines added)
   - `src/gui/app.py` (27 lines added)
   - `src/domain/models.py` (9 fields added)
   - `src/workflows/order.py` (143 lines added/modified)

**Total LOC**: ~200 added, ~10 modified

## Migration Notes

### For Users:
1. No action required—legacy mode is default
2. To enable CSL mode: Settings → Reorder Engine → Policy Mode → `csl`
3. Configure SKU-specific CSL: SKU Management → CSL Target field (0.50-0.999)
4. Monitor proposal details for CSL breakdown visibility

### For Developers:
1. New dependency: `src/analytics/pipeline.py` must be importable
2. `TargetServiceLevelResolver` must be available (already implemented)
3. `compute_order` contract: expects `Lane`, `OrderConstraints`, history dict format
4. Test coverage: Pipeline builder, policy mode branching, lane deduction, Friday dual-lane

## Validation Checklist

- ✅ Pipeline builder filters and sorts correctly
- ✅ Legacy mode preserves existing behavior (17/17 workflow tests pass)
- ✅ CSL mode calls compute_order with correct parameters
- ✅ Lane deduction works for STANDARD/SATURDAY/MONDAY
- ✅ Friday dual-lane supports pipeline_extra
- ✅ CSL breakdown fields populated and displayed in GUI
- ✅ Fallback to legacy on CSL error
- ✅ Settings parameter integrated (backend + GUI)
- ✅ OrderProposal model extended without breaking changes
- ✅ Calendar-aware proposals still work (4/4 tests pass)
- ✅ No linting errors
- ✅ 11/11 integration tests pass

---

**Implementation Date**: 2026-02-14  
**Status**: Complete and tested  
**Backward Compatible**: Yes (default legacy mode)
