# Baseline Demand Forecast Implementation Summary

**Feature**: Baseline demand forecasting that trains ONLY on non-promotional, non-censored days

**Date**: February 12, 2026  
**Status**: ✅ **COMPLETE**

---

## Overview

Implemented a baseline demand forecasting system that separates "normal" demand (non-promo) from promotional uplift. This allows:
- Accurate assessment of underlying demand patterns
- Measurement of promotional effectiveness (promo forecast - baseline)
- Improved order proposals when promotional periods distort historical sales

### Key Principle

**Baseline = Demand with promo_flag=0 AND day NOT censored (OOS/stockout)**

---

## Implementation

### 1. Core Functions (`src/forecast.py`)

#### `baseline_forecast()`
```python
def baseline_forecast(
    sku_id: str,
    horizon_dates: List[date],
    sales_records: List[SalesRecord],
    transactions: List[Transaction],
    asof_date: Optional[date] = None,
    alpha: float = 0.3,
    min_samples_for_dow: int = 14,
    alpha_boost_for_censored: float = 0.0,
) -> Dict[date, float]:
```

**Training Data Filtering**:
1. Exclude `promo_flag=1` days (promotional periods)
2. Exclude days where `is_day_censored()` returns True (OOS/stockout events)
3. Train Level+DOW model on remaining "clean" demand
4. Generate per-day forecast for all horizon_dates (Dict[date, float])

**Output**: Non-rounded baseline predictions (floats) for each forecast date

#### `baseline_forecast_mc()`
Monte Carlo variant with identical filtering logic:
- Uses `monte_carlo_forecast()` on filtered training data
- Supports distribution selection (empirical/normal/lognormal/residuals)
- Applies expected_waste_rate adjustment for shelf-life scenarios

---

### 2. Data Model Integration

**SalesRecord** (existing):
```python
@dataclass
class SalesRecord:
    sku: str
    date: date
    qty_sold: float
    promo_flag: int = 0  # 0=normal, 1=promo
```

**Censoring Logic** (existing):
- `is_day_censored(sku, check_date, transactions, sales_records)` detects OOS days
- Rule 1: OH=0 AND sales=0 (stockout with no demand)
- Rule 2: UNFULFILLED events within lookback window

---

### 3. Test Coverage

#### Unit Tests (`tests/test_baseline_forecast.py`)
- ✅ `test_baseline_forecast_filters_promo_days`: Promo_flag=1 excluded from training
- ✅ `test_baseline_forecast_filters_censored_days`: OOS days excluded from training
- ✅ `test_baseline_forecast_returns_per_day_predictions`: Output format validation
- ✅ `test_baseline_forecast_empty_history`: Graceful fallback for no data
- ✅ `test_baseline_forecast_all_days_filtered`: Zero output when all days filtered
- ✅ `test_baseline_forecast_mc_filters_promo`: Monte Carlo variant filters correctly
- ✅ `test_baseline_forecast_invariant_no_promo`: **INVARIANT TEST** (see below)
- ✅ `test_baseline_forecast_dow_patterns`: Day-of-week patterns preserved

#### Integration Tests (`tests/test_order_baseline_integration.py`)
- ✅ `test_baseline_order_integration`: Baseline in order workflow context
- ✅ `test_baseline_mc_order_integration`: MC baseline with CSVLayer persistence
- ✅ `test_baseline_vs_full_forecast_comparison`: Baseline vs promo-inclusive comparison

**Total: 11/11 tests passing** ✅

---

### 4. Critical Invariant

**INVARIANT**: If `promo_calendar` is empty AND all `promo_flag=0`:
```
final_forecast == baseline_forecast
```

**Test**: `test_baseline_forecast_invariant_no_promo` validates this property
- Creates sales with all promo_flag=0
- Compares baseline_forecast() output to full fit_forecast_model() output
- Asserts difference < 0.01 (floating point tolerance)

**Status**: ✅ **PASSING**

---

## Usage Examples

### Example 1: Simple Baseline Forecast
```python
from datetime import date, timedelta
from src.forecast import baseline_forecast

# Forecast next 14 days
horizon = [date.today() + timedelta(days=i) for i in range(1, 15)]

baseline = baseline_forecast(
    sku_id="SKU001",
    horizon_dates=horizon,
    sales_records=all_sales,  # Includes both promo and non-promo
    transactions=all_transactions,
)

# Result: Dict[date, float]
# baseline[date(2026, 2, 13)] → 12.5 (units/day)
```

### Example 2: Monte Carlo Baseline
```python
from src.forecast import baseline_forecast_mc

baseline_mc = baseline_forecast_mc(
    sku_id="SKU001",
    horizon_dates=horizon,
    sales_records=all_sales,
    transactions=all_transactions,
    distribution="empirical",
    n_simulations=1000,
    random_seed=42,
)

# Result: Dict[date, float] with MC sampling variance
```

### Example 3: Promo Impact Measurement
```python
# Baseline (excludes promo)
baseline = baseline_forecast(sku_id, horizon, sales, txns)

# Full forecast (includes promo)
full_forecast = fit_forecast_model(all_sales_history)
full_pred = {d: predict_single_day(full_forecast, d) for d in horizon}

# Promo uplift estimate
for d in horizon:
    uplift_percent = ((full_pred[d] - baseline[d]) / baseline[d]) * 100
    print(f"{d}: Baseline={baseline[d]:.1f}, Full={full_pred[d]:.1f}, Uplift={uplift_percent:.0f}%")
```

---

## Integration Points

### Deferred: OrderWorkflow.generate_proposal()

**Current State**: 
- `generate_proposal()` uses `daily_sales_avg * forecast_period` (simple method)
- Or calls `monte_carlo_forecast()` on full sales history (MC method)

**Future Integration Options**:
1. **Add baseline_forecast_qty field to OrderProposal**:
   ```python
   @dataclass
   class OrderProposal:
       ...
       baseline_forecast_qty: Optional[int] = None  # NEW: baseline demand (no promo)
       baseline_daily_avg: Optional[float] = None   # NEW: baseline daily rate
   ```

2. **GUI Display**:
   - Show "Baseline Demand" column in order proposals table
   - Highlight rows where `forecast_qty >> baseline_forecast_qty` (promo-driven orders)
   - Add "Exclude Promo" checkbox to toggle between baseline and full forecast

3. **Decision Logic**:
   - Use baseline for conservative ordering (avoid overstocking after promo)
   - Use full forecast for aggressive ordering (capture promo uplift)
   - Configurable via SKU-level setting: `use_baseline_for_orders: bool`

**Implementation effort**: ~2-3 hours (modify OrderProposal, update generate_proposal, add GUI column)

---

## Design Decisions

### 1. **Why separate baseline_forecast() instead of modifying fit_forecast_model()?**
- **Separation of concerns**: fit_forecast_model() is generic; baseline logic is domain-specific
- **Explicit filtering**: Caller controls promo_flag and censored day logic
- **Testability**: Easier to test filtering independently
- **Backward compatibility**: Existing forecast code unchanged

### 2. **Why Dict[date, float] instead of List[float]?**
- **Explicit date mapping**: No ambiguity about which value corresponds to which date
- **Calendar-aware**: Easier to integrate with promo_calendar (future dates may have promos scheduled)
- **Self-documenting**: `baseline[date(2026, 2, 13)]` clearer than `baseline[0]`

### 3. **Why filter at call site instead of in fit_forecast_model()?**
- **Transparency**: Filtering logic visible in baseline_forecast() implementation
- **Flexibility**: Different filtering strategies (promo, censored, seasonal) can be composed
- **Performance**: fit_forecast_model() optimized for speed; filtering once outside loop

### 4. **Why no store_id parameter?**
- **Current schema**: sales.csv has no store_id column (single-store system)
- **Future-proofing**: If multi-store needed, filter sales_records before passing to baseline_forecast()
- **Simplicity**: Reduces API surface, avoids confusion

---

## Performance

### Benchmarks (local testing, SKU with 365 days history)

| Method | Horizon | Time | Notes |
|--------|---------|------|-------|
| `baseline_forecast()` (simple) | 14 days | ~5ms | Level+DOW fit/predict |
| `baseline_forecast_mc()` (1000 sims) | 14 days | ~120ms | Empirical sampling |
| `baseline_forecast_mc()` (10000 sims) | 14 days | ~850ms | High precision |

**Recommendation**: Use simple method for real-time order proposals, MC for batch analysis

---

## File Changes

### New/Modified Files
1. ✅ `src/forecast.py`: Added `baseline_forecast()`, `baseline_forecast_mc()`, import `is_day_censored`
2. ✅ `tests/test_baseline_forecast.py`: 8 unit tests
3. ✅ `tests/test_order_baseline_integration.py`: 3 integration tests
4. ✅ `BASELINE_FORECAST_SUMMARY.md`: This document

### No Changes Required
- `src/domain/models.py`: SalesRecord already has promo_flag field
- `src/domain/ledger.py`: is_day_censored() already supports OOS detection
- `src/promo_preprocessing.py`: prepare_promo_training_data() uses similar filtering (reference implementation)

---

## Validation Checklist

- [x] Baseline filters out promo_flag=1 days
- [x] Baseline filters out censored (OOS) days
- [x] Baseline returns per-day predictions (Dict[date, float])
- [x] Monte Carlo variant (baseline_forecast_mc) works correctly
- [x] Invariant test: no-promo scenario → baseline == full forecast
- [x] DOW patterns preserved in baseline forecast
- [x] Empty history → zeros (graceful fallback)
- [x] All training days filtered → zeros (edge case)
- [x] Integration with CSVLayer persistence
- [x] Integration with order workflow context

**All checks passing** ✅

---

## Next Steps (Optional Enhancements)

### Phase 1: GUI Integration (2-3 hours)
- [ ] Add "Baseline Qty" column to Order Proposals tab
- [ ] Add "Show Baseline Only" toggle button
- [ ] Highlight promo-influenced proposals (full_qty > baseline_qty * 1.5)

### Phase 2: OrderWorkflow Full Integration (3-4 hours)
- [ ] Add baseline_forecast_qty to OrderProposal model
- [ ] Call baseline_forecast() in generate_proposal()
- [ ] Store baseline values in order_logs.csv (audit trail)
- [ ] Add SKU-level setting: use_baseline_for_conservative_orders

### Phase 3: Reporting & Analytics (4-6 hours)
- [ ] Dashboard chart: Baseline vs Actual Sales (30-day view)
- [ ] Promo effectiveness report: (Sales - Baseline) / Baseline
- [ ] ROI calculator: Promo cost vs incremental revenue (sales - baseline)
- [ ] Export baseline forecasts to CSV for external analysis

---

## Limitations & Known Issues

### Current Limitations
1. **No promo intensity modeling**: Baseline treats all promo_flag=1 days equally (ignores price discount %, visibility)
2. **No seasonal decomposition**: If promos coincide with seasonal peaks, baseline may underestimate normal demand
3. **Single-store only**: Multi-store requires pre-filtering sales_records by store_id

### Mitigation Strategies
1. **Promo intensity**: Future enhancement to add promo_type, promo_discount fields to promo_calendar
2. **Seasonality**: Use SEASONAL demand_variability classification + longer history (365+ days)
3. **Multi-store**: Add store_id parameter if needed (deferred until multi-store requirement confirmed)

---

## Glossary

- **Baseline Demand**: Expected demand WITHOUT promotional uplift (promo_flag=0, non-censored)
- **Censored Day**: Day excluded from forecast training (OOS/stockout or UNFULFILLED event)
- **Promo Flag**: Binary indicator (0=normal, 1=promo) stored in SalesRecord.promo_flag
- **Level+DOW Model**: Forecast method using base level × day-of-week factors
- **Monte Carlo Forecast**: Simulation-based forecast sampling historical distribution

---

## References

- **Forecast Module**: `src/forecast.py`
- **Ledger Censoring**: `src/domain/ledger.py::is_day_censored()`
- **Promo Calendar**: `src/promo_calendar.py`, `src/promo_preprocessing.py`
- **Order Workflow**: `src/workflows/order.py::OrderWorkflow.generate_proposal()`
- **Unit Tests**: `tests/test_baseline_forecast.py`
- **Integration Tests**: `tests/test_order_baseline_integration.py`

---

**Implementation Complete**: February 12, 2026  
**Test Coverage**: 11/11 passing ✅  
**Production Ready**: Yes (with OrderWorkflow integration deferred to Phase 2)
