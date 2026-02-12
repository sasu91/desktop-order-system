# Promo Uplift Estimation System

**Status**: ✅ COMPLETE (January 2026)  
**Feature**: Event-level promo uplift estimation with hierarchical pooling and robust aggregation  
**Implementation**: [src/domain/promo_uplift.py](src/domain/promo_uplift.py)

---

## Overview

The **Promo Uplift Estimation System** quantifies the sales increase (uplift factor) during promotional periods using historical promo events. This enables accurate demand forecasting during future promos by applying the estimated uplift to baseline demand predictions.

**Core Methodology**:
- **Event-level uplift calculation**: For each historical promo event, compute `uplift_event = actual_sales / baseline_pred` (ratio of actual sales during promo to what baseline model would predict)
- **Anti-leakage baseline**: Baseline forecast trained ONLY on data strictly before promo event start (prevents promo sales from contaminating baseline)
- **Robust aggregation**: Winsorized mean (trim extreme values) to reduce impact of outliers
- **Hierarchical pooling**: If SKU lacks sufficient data, pool events from category → department → global
- **Confidence scoring**: A/B/C grades based on number of events and pooling depth

---

## Use Case

**Problem**: Baseline forecast predicts "normal" demand (no promo). When planning orders for a future promo period, we need to estimate how much sales will increase.

**Solution**: Use historical promo events to estimate a reusable **uplift_factor** per SKU. During future promo planning:
```
promo_forecast = baseline_forecast × uplift_factor
```

**Example**:
- SKU "Cola 500ml" has baseline forecast = 100 units/day
- Historical uplift estimation: 2.3x (promos increased sales by 130%)
- During next promo: forecast = 100 × 2.3 = 230 units/day

---

## Architecture

### 1. Event Extraction (`extract_promo_events`)

**Input**: List of `PromoWindow` objects (SKU, start_date, end_date)  
**Output**: Merged list of (start_date, end_date) tuples for historical promo events

**Logic**:
- Filter promo windows for target SKU
- Exclude future events (end_date >= asof_date)
- **Merge overlapping/adjacent windows** (gap ≤ 1 day) to avoid double-counting
  - Example: Windows (Jan 1-5, Jan 6-10) → merged to (Jan 1-10)

**Purpose**: Consolidate fragmented promo periods into distinct events for uplift calculation.

### 2. Per-Event Uplift Calculation (`calculate_uplift_for_event`)

**Input**: 
- Promo event dates (start_date, end_date)
- Historical sales records
- Transactions (for censoring detection)

**Output**: `UpliftEvent` object with:
- `actual_sales`: Sum of actual sales during promo (non-censored days only)
- `baseline_pred`: Sum of baseline predictions for same days
- `uplift_ratio`: actual_sales / baseline_pred
- `valid_days`: Number of non-censored days in event

**Anti-Leakage Mechanism**:
```python
# Train baseline with data STRICTLY BEFORE event
baseline_preds = baseline_forecast(
    sku_id=sku_id,
    horizon_dates=[event_start, ..., event_end],
    sales_records=[s for s in sales if s.date < event_start],  # ← Key filter
    asof_date=event_start - timedelta(days=1),  # ← Cutoff day before event
)
```

**Why Anti-Leakage Matters**:
- Without cutoff: Baseline trained on Jan 1-20 with promo on Jan 15-20 → baseline ≈ 150 (inflated by promo) → uplift = 150/150 = 1.0 ❌
- With cutoff: Baseline trained on Jan 1-14 only → baseline ≈ 100 → uplift = 150/100 = 1.5 ✓

**Censoring Exclusion**:
- Days with `is_day_censored() = True` (OOS/stockout) are **skipped**
- Only non-censored days contribute to actual_sales and baseline_pred sums

**Validation**:
- If no historical sales before event → return `None` (cannot train baseline)
- If all days censored → return `None` (no valid data)
- If baseline_sum < epsilon (default 0.1) → return `None` (avoid div-by-zero)

### 3. Robust Aggregation (`winsorized_mean`)

**Problem**: Single outlier events (e.g., 10x uplift due to external factor) can skew average.

**Solution**: **Winsorized Mean** (default: trim 10% from each tail)
1. Sort all uplift ratios: `[1.5, 1.8, 2.0, 2.2, 10.0]`
2. Trim 10% from top/bottom: `[1.8, 2.0, 2.2]` (1.5 → 1.8, 10.0 → 2.2)
3. Compute mean: `(1.8 + 2.0 + 2.2) / 3 = 2.0`

**Result**: Outlier (10.0) reduced to 2.2, preventing distortion.

**Implementation**:
```python
sorted_vals = sorted([event.uplift_ratio for event in events])
n = len(sorted_vals)
trim_count = int(n * trim_percent / 100)  # Default: 10% → 1 value trimmed per tail

lower_bound = sorted_vals[trim_count]
upper_bound = sorted_vals[-(trim_count + 1)]

winsorized_vals = [
    max(lower_bound, min(upper_bound, v)) for v in sorted_vals
]
return mean(winsorized_vals)
```

### 4. Guardrail Clipping (`aggregate_uplift_events`)

**Purpose**: Prevent unrealistic uplifts from propagating to forecasts.

**Guardrails** (configurable in `settings.json`):
- **min_uplift**: Default 1.0 (uplift cannot be < 1.0, i.e., promos never decrease sales)
- **max_uplift**: Default 3.0 (uplift cannot exceed 3x, even if historical data shows 10x)

**Logic**:
```python
agg_uplift = winsorized_mean([event.uplift_ratio for event in events])
agg_uplift = max(min_uplift, min(max_uplift, agg_uplift))  # Clip to [1.0, 3.0]
```

**Rationale**:
- Very high uplifts (e.g., 10x) may be due to one-time external factors (e.g., stockout at competitor)
- Clipping to max=3.0 prevents order quantity explosion while still capturing strong promo effects

### 5. Hierarchical Pooling (`hierarchical_pooling`)

**Problem**: New SKUs or SKUs with few promo events lack sufficient data for reliable uplift estimation.

**Solution**: Pool events from broader groups in hierarchy:

```
SKU-level (if >= min_events_sku=3)
  ↓ (Fallback if < 3 events)
Category-level (if >= min_events_category=5)
  ↓ (Fallback if < 5 events)
Department-level (if >= min_events_department=10)
  ↓ (Fallback if < 10 events)
Global (all SKUs in system)
```

**Example**:
- SKU "New Chips Flavor" launched 1 month ago → only 1 promo event (< 3 threshold)
- Category "Snacks" has 15 promo events across similar chips SKUs
- System pools 15 events from category → uplift estimated from category average

**Data Model Extension**:
To enable hierarchical pooling, SKU model was extended with:
```python
@dataclass
class SKU:
    sku: str
    description: str
    category: str = ""        # ← NEW: e.g., "SNACKS", "BEVERAGES"
    department: str = ""      # ← NEW: e.g., "FOOD", "DRINKS"
    # ... other fields
```

**CSV Schema Update**:
`skus.csv` now includes `category` and `department` columns (backward-compatible: empty string if missing).

### 6. Confidence Scoring

**Purpose**: Communicate reliability of uplift estimate to user.

**Confidence Grades**:
- **A (High)**: SKU-level estimate with >= 3 events (configurable threshold_a)
- **B (Medium)**: Pooled from category/department OR SKU with 1-2 events
- **C (Low)**: Global pooling fallback OR sparse data

**Logic**:
```python
if pooling_source == "SKU" and n_events >= confidence_threshold_a:
    confidence = "A"
elif pooling_source in ["category:...", "department:..."] or n_events >= confidence_threshold_b:
    confidence = "B"
else:
    confidence = "C"
```

**GUI Display**:
- Confidence **A**: Green, bold font
- Confidence **B**: Orange font
- Confidence **C**: Red font

### 7. Main API: `estimate_uplift()`

**Function Signature**:
```python
def estimate_uplift(
    sku_id: str,
    all_skus: List[SKU],
    promo_windows: List[PromoWindow],
    sales_records: List[SalesRecord],
    transactions: List[Transaction],
    settings: Dict,
) -> UpliftReport
```

**Workflow**:
1. Extract historical promo events for target SKU
2. For each event: calculate per-event uplift using anti-leakage baseline
3. Check if SKU has sufficient events/valid days (thresholds from settings)
4. If insufficient → apply hierarchical pooling (category → department → global)
5. Aggregate pooled events using winsorized mean + guardrails
6. Assign confidence grade based on pooling depth and event count
7. Return `UpliftReport` with:
   - `uplift_factor`: Final aggregated uplift (float)
   - `confidence`: A/B/C grade
   - `events_used`: List of `UpliftEvent` objects (for transparency)
   - `pooling_source`: "SKU", "category:<name>", "department:<name>", or "global"
   - `n_events`: Total events used in calculation
   - `n_valid_days_total`: Total non-censored days across all events

**Example Usage**:
```python
from src.domain.promo_uplift import estimate_uplift

report = estimate_uplift(
    sku_id="SKU_COLA_500ML",
    all_skus=csv_layer.read_skus(),
    promo_windows=csv_layer.read_promo_calendar(),
    sales_records=csv_layer.read_sales(),
    transactions=csv_layer.read_transactions(),
    settings=csv_layer.read_settings(),
)

print(f"Uplift: {report.uplift_factor:.2f}x")
print(f"Confidence: {report.confidence}")
print(f"Based on: {report.n_events} events ({report.pooling_source})")
# Output:
# Uplift: 2.15x
# Confidence: A
# Based on: 5 events (SKU)
```

---

## Configuration

**Settings** (`settings.json` → `promo_uplift` section):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_uplift` | 1.0 | Minimum uplift guardrail (floor) |
| `max_uplift` | 3.0 | Maximum uplift guardrail (ceiling) |
| `min_events_sku` | 3 | Min events for SKU-level estimation |
| `min_valid_days_sku` | 7 | Min total valid days for SKU-level |
| `min_events_category` | 5 | Min events for category pooling |
| `min_events_department` | 10 | Min events for department pooling |
| `winsorize_trim_percent` | 10.0 | % to trim from each tail (outlier reduction) |
| `denominator_epsilon` | 0.1 | Min baseline sum to avoid div-by-zero |
| `confidence_threshold_a` | 3 | Min events for confidence grade A |
| `confidence_threshold_b` | 5 | Min events for confidence grade B (fallback) |

**Tuning Recommendations**:
- **High-variability SKUs**: Increase `winsorize_trim_percent` to 15-20% to reduce outlier impact
- **Sparse data environments**: Lower `min_events_sku` to 2 to enable more SKU-level estimates
- **Conservative forecasting**: Lower `max_uplift` to 2.5 to cap promo boost expectations

---

## GUI Integration

### Promo Tab Enhancement

**New Section**: "📊 Analisi Uplift Promo" (below promo windows table)

**Components**:
1. **"Calcola Report Uplift" Button**: Triggers `_refresh_uplift_report()` to compute uplift for all SKUs with promo history
2. **Uplift Report Table** (TreeView):
   - **Columns**: SKU, N. Eventi, Uplift Finale, Confidence, Pooling Source, Totale Giorni
   - **Sorting**: By SKU (alphabetical)
   - **Filtering**: SKU name filter (real-time substring match)
   - **Color Coding**:
     - Confidence A → Green, bold
     - Confidence B → Orange
     - Confidence C → Red

**User Workflow**:
1. User adds promo windows in top section (dates, SKU)
2. User clicks "Calcola Report Uplift"
3. System processes all SKUs with promo events:
   - Calls `estimate_uplift()` for each SKU
   - Populates table with results
4. User reviews uplift factors and confidence grades
5. User applies uplift to future promo forecasts (manual or automated)

**Screenshot** (conceptual):
```
┌─── 📊 Analisi Uplift Promo ─────────────────────────────────────┐
│ [🔄 Calcola Report Uplift]   Filtra SKU: [______]              │
│                                                                  │
│ SKU          │ Eventi │ Uplift  │ Conf │ Pooling       │ Giorni│
│──────────────┼────────┼─────────┼──────┼───────────────┼───────│
│ SKU_COLA     │   5    │ 2.15x   │  A   │ SKU           │  35   │ (green)
│ SKU_CHIPS    │   2    │ 1.80x   │  B   │ category:SNACKS│ 14  │ (orange)
│ SKU_NEW_ITEM │   0    │ 1.95x   │  C   │ global        │   0   │ (red)
└──────────────────────────────────────────────────────────────────┘
```

**Implementation**:
- [src/gui/app.py](src/gui/app.py): New `_refresh_uplift_report()` method (line ~6590)
- Import: `from ..domain.promo_uplift import estimate_uplift, UpliftReport`

---

## Testing

### Unit Tests (`tests/test_promo_uplift.py`)

**25 test cases** covering all functions:

| Test Class | Tests | Coverage |
|------------|-------|----------|
| `TestExtractPromoEvents` | 6 | Window merging, overlap detection, future filtering |
| `TestWinsorizedMean` | 5 | Outlier trimming, edge cases (empty, single value) |
| `TestAggregateUpliftEvents` | 5 | Guardrail clipping, winsorized aggregation |
| `TestCalculateUpliftForEvent` | 5 | Anti-leakage, censoring exclusion, baseline validation |
| `TestHierarchicalPooling` | 2 | Category/global fallback logic |
| `TestEstimateUplift` | 3 | Full workflow, SKU not found, no promo events |

**Run**:
```bash
pytest tests/test_promo_uplift.py -v
# ✅ 25 passed in 0.13s
```

### Integration Tests (`tests/test_uplift_integration.py`)

**6 end-to-end scenarios**:

1. **Single SKU, single promo** → validates basic uplift calculation
2. **Multi-SKU category pooling** → validates hierarchical fallback
3. **Censored days exclusion** → validates OOS/UNFULFILLED filtering
4. **Anti-leakage baseline** → validates training cutoff (data < event_start)
5. **Guardrail clipping** → validates extreme uplift capping (10x → 3.0)
6. **CSV persistence workflow** → validates full I/O cycle with CSVLayer

**Run**:
```bash
pytest tests/test_uplift_integration.py -v
# ✅ 6 passed in 0.16s
```

**Total Test Coverage**:
- **31 tests** (25 unit + 6 integration)
- **All passing** as of January 2026
- **Critical invariants validated**:
  - Anti-leakage: baseline uses only pre-event data
  - Censoring: OOS days excluded from uplift calculation
  - Pooling: fallback to category/dept/global when SKU data sparse
  - Guardrails: uplift clipped to [1.0, 3.0] range

---

## Data Model Changes

### 1. SKU Model Extension

**Before**:
```python
@dataclass
class SKU:
    sku: str
    description: str
    ean: str = ""
    # ... other fields
```

**After**:
```python
@dataclass
class SKU:
    sku: str
    description: str
    ean: str = ""
    category: str = ""     # ← NEW
    department: str = ""   # ← NEW
    # ... other fields
```

**Migration**: Backward-compatible (empty strings for missing fields in existing `skus.csv`).

### 2. CSV Schema Update

**File**: `skus.csv`  
**New Columns**: `category`, `department`

**Example**:
```csv
sku,description,ean,category,department,lead_time_days,moq,...
SKU_COLA,Cola 500ml,8001234567890,BEVERAGES,DRINKS,7,12,...
SKU_CHIPS,Chips 100g,8009876543210,SNACKS,FOOD,5,24,...
```

**Persistence Changes**:
- [src/persistence/csv_layer.py](src/persistence/csv_layer.py):
  - `SCHEMAS["skus.csv"]` includes `category`, `department` columns (line 25)
  - `read_skus()` parses with fallback: `row.get("category", "").strip()` (line 132)
  - `write_sku()` persists category/department in 3 locations (lines 203-204, 234-235, 275-276)

### 3. Settings Extension

**New Section**: `promo_uplift` with 9 parameters (see Configuration table above)

**Storage**: `settings.json`  
**Access**: Via `csv_layer.read_settings()["promo_uplift"]`

---

## Performance Considerations

### Computational Complexity

**Per-SKU Uplift Estimation**:
- Extract events: O(P) where P = number of promo windows
- Calculate per-event uplift: O(E × S) where E = events/SKU, S = sales records
  - Baseline forecast: O(S log S) (fitting exponential smoothing model)
- Aggregate: O(E log E) (winsorized mean sorting)

**Total for N SKUs**: O(N × E × S)

**Realistic Scale**:
- 100 SKUs, 5 events/SKU avg, 1000 sales records → ~0.5 seconds
- GUI batch calculation for all SKUs: <2 seconds for typical inventory

**Optimization**:
- Cache baseline forecasts per SKU (avoid recomputation for each event)
- Parallelize uplift estimation across SKUs (future enhancement)

### Memory Usage

- **UpliftEvent**: ~200 bytes per event
- **UpliftReport**: ~500 bytes per SKU
- **Total**: <1 MB for 1000 SKUs with 5 events each

---

## Known Limitations & Future Enhancements

### Limitations

1. **Static Uplift**: Assumes uplift is constant across all promo types (does not distinguish flash sale vs. 2-for-1 vs. percentage discount)
2. **No Temporal Decay**: Old promo events weighted equally to recent events (no time-based discounting)
3. **No External Factors**: Does not account for seasonality, competitor actions, or macroeconomic shifts
4. **Single Store**: Current implementation assumes single-store SKUs (multi-store pooling not implemented)

### Future Enhancements

**Priority 1 (High Impact)**:
- **Promo Type Differentiation**: Tag promo windows with `promo_type` (e.g., "discount_20pct", "BOGO", "flash_sale") and estimate separate uplift factors per type
- **Temporal Weighting**: Apply exponential decay to older events (e.g., events >6 months old weighted at 50%)

**Priority 2 (Medium Impact)**:
- **Multi-Store Pooling**: Extend hierarchical pooling to include same-SKU across stores
- **Confidence Interval**: Provide uplift range (e.g., "2.0x ± 0.3x at 95% CI") instead of single point estimate

**Priority 3 (Low Impact)**:
- **GUI Chart**: Add histogram of per-event uplifts in report table (visual distribution)
- **Export Report**: CSV export of uplift report table

---

## Integration with Order Workflow

### Current State (Manual)

User manually applies uplift when viewing order proposals:
1. Generate order proposal (uses baseline forecast)
2. Review uplift report in Promo tab
3. Manually adjust order quantities: `new_qty = baseline_qty × uplift_factor`

### Future Automation (Planned)

**Enhancement**: Automatically apply uplift to order proposals for SKUs with active/future promo windows.

**Workflow**:
1. Order workflow checks if SKU has promo window overlapping with order horizon
2. If yes: fetch `report = estimate_uplift(sku_id, ...)`
3. Adjust forecast: `promo_forecast = baseline_forecast × report.uplift_factor`
4. Display in order proposal table:
   - Column "Promo Uplift": "2.15x (A)" → indicates uplift applied
   - Original baseline forecast shown in tooltip

**Implementation Sketch**:
```python
# In src/workflows/order.py
def generate_order_proposal(sku_id, horizon_dates, ...):
    baseline = baseline_forecast(sku_id, horizon_dates, ...)
    
    # Check for active promo
    promo_windows = csv_layer.read_promo_calendar()
    active_promos = [w for w in promo_windows if w.sku == sku_id and overlaps(w, horizon_dates)]
    
    if active_promos:
        uplift_report = estimate_uplift(sku_id, ...)
        promo_forecast = {d: baseline[d] * uplift_report.uplift_factor for d in horizon_dates}
        return OrderProposal(forecast=promo_forecast, uplift_applied=True, uplift_factor=uplift_report.uplift_factor)
    else:
        return OrderProposal(forecast=baseline, uplift_applied=False)
```

---

## Troubleshooting

### Issue: Uplift = 1.0 for all SKUs (no increase detected)

**Causes**:
- Promo windows not correctly enriched in `sales_records` (promo_flag=0 for all days)
- Baseline forecast incorrectly includes promo days (anti-leakage not working)

**Debug**:
```python
# Check promo_flag enrichment
sales = csv_layer.read_sales()
promo_sales = [s for s in sales if s.promo_flag == 1]
print(f"Sales with promo_flag=1: {len(promo_sales)}")  # Should be > 0

# Check baseline exclusion
from src.forecast import baseline_forecast
baseline = baseline_forecast("SKU001", horizon_dates, sales, txns)
# Inspect training data: should NOT include promo days
```

### Issue: All SKUs have Confidence C (global pooling)

**Causes**:
- `min_events_sku` threshold too high (e.g., 10 but most SKUs have <10 events)
- Category/department fields not populated in `skus.csv`

**Fix**:
1. Lower `min_events_sku` in settings.json (e.g., 2 instead of 3)
2. Populate `category` and `department` columns in skus.csv:
   ```python
   sku = csv_layer.read_skus()[0]
   sku.category = "BEVERAGES"
   sku.department = "DRINKS"
   csv_layer.write_sku(sku)
   ```

### Issue: Uplift calculation very slow (>10 seconds)

**Causes**:
- Very large sales history (>100k records) → baseline forecast re-fit for each event
- Many SKUs processed sequentially

**Optimization**:
1. Cache baseline forecasts per SKU (avoid recomputation)
2. Filter sales_records to relevant date range before passing to `estimate_uplift()`
3. Parallelize: use `multiprocessing.Pool` to process SKUs in batches

---

## References

### Related Features
- **Baseline Forecast** ([BASELINE_FORECAST_SUMMARY.md](BASELINE_FORECAST_SUMMARY.md)): Non-promo demand prediction (denominator for uplift)
- **Promo Calendar** ([PROMO_CALENDAR_MODULE.md](PROMO_CALENDAR_MODULE.md)): Promo window management and promo_flag enrichment
- **Demand Variability** ([DEMAND_VARIABILITY_INTEGRATION.md](DEMAND_VARIABILITY_INTEGRATION.md)): Uncertainty quantification (complementary to uplift)

### Academic References
- **Winsorized Mean**: Tukey, J.W. (1962). "The Future of Data Analysis". Annals of Mathematical Statistics.
- **Hierarchical Pooling**: Also known as "hierarchical forecasting" or "grouped time series" (Hyndman et al., 2011)

### Implementation Files
- **Core Module**: [src/domain/promo_uplift.py](src/domain/promo_uplift.py) (580 lines)
- **Unit Tests**: [tests/test_promo_uplift.py](tests/test_promo_uplift.py) (25 tests)
- **Integration Tests**: [tests/test_uplift_integration.py](tests/test_uplift_integration.py) (6 tests)
- **GUI Integration**: [src/gui/app.py](src/gui/app.py) (lines 46, 4959-5010, 6590-6670)
- **Data Model**: [src/domain/models.py](src/domain/models.py) (SKU with category/department)
- **Persistence**: [src/persistence/csv_layer.py](src/persistence/csv_layer.py) (schema + read/write updates)

---

**Document Version**: 1.0  
**Last Updated**: January 2026  
**Authors**: @sasu91 (implementation), GitHub Copilot (documentation)
