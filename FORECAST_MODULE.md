# Demand Forecasting Module

## Overview

**Module**: `src/forecast.py`  
**Purpose**: Simple, robust demand forecasting for daily sales with day-of-week seasonality  
**Status**: ✅ PRODUCTION READY  
**Test Coverage**: 24/24 tests passing (100%)

## Design Philosophy

### Pragmatic Approach
- **Simple**: Level + DOW factors (no complex ML)
- **Robust**: Works with short history (fallback strategies)
- **Production-ready**: Validated, documented, tested
- **Lightweight**: Zero heavy dependencies (uses only stdlib + statistics)

### Model: Level + DOW Factors

**Formula**:
```
Forecast(day) = Level × DOW_Factor[weekday(day)]
```

Where:
- **Level**: Base demand level (exponential smoothing)
- **DOW_Factor**: Multiplicative factor per day of week (0=Monday, 6=Sunday)
- **Weekday**: 0=Mon, 1=Tue, ..., 6=Sun

**Example**:
- Level = 10 units/day
- DOW_Factor[Monday] = 1.5 (50% higher on Mondays)
- Forecast(Monday) = 10 × 1.5 = 15 units

---

## API Reference

### Core Functions

#### `fit_forecast_model(history, alpha=0.3)`

Fit forecasting model from historical sales data.

**Parameters**:
- `history`: `List[Dict[str, Any]]` - Sales records with keys:
  - `"date"`: `date` object
  - `"qty_sold"`: `float` (quantity sold)
- `alpha`: `float` (default 0.3) - Smoothing parameter for EMA
  - Lower α = more smoothing (0 < α ≤ 1)
  - Recommended: 0.2-0.4 for daily data

**Returns**: `Dict[str, Any]` - Model state
```python
{
    "level": float,              # Base demand level
    "dow_factors": List[float],  # 7 factors (Mon-Sun)
    "last_date": date,           # Last training date
    "n_samples": int,            # Training samples
    "method": str,               # "full", "simple", or "fallback"
}
```

**Fallback Strategy**:
- **< 7 days**: Simple mean, uniform DOW factors (1.0 for all)
- **7-13 days**: Partial DOW factors (where data exists)
- **≥ 14 days**: Full model with smoothed DOW factors

**Example**:
```python
history = [
    {"date": date(2024, 1, 1), "qty_sold": 10.0},
    {"date": date(2024, 1, 2), "qty_sold": 12.0},
    # ... more days
]

model = fit_forecast_model(history, alpha=0.3)
print(f"Level: {model['level']}")
print(f"DOW factors: {model['dow_factors']}")
```

---

#### `predict(model_state, horizon, start_date=None)`

Generate multi-day forecast.

**Parameters**:
- `model_state`: Model from `fit_forecast_model()`
- `horizon`: `int` - Number of days to forecast
- `start_date`: `date` (optional) - Forecast start date (default: last_date + 1)

**Returns**: `List[float]` - Forecast values (length = horizon)

**Guarantees**:
- All values ≥ 0 (non-negative)
- Length = horizon

**Example**:
```python
model = fit_forecast_model(history)
forecast = predict(model, horizon=7)  # Next 7 days

for i, value in enumerate(forecast):
    print(f"Day {i+1}: {value:.1f} units")
```

---

#### `predict_single_day(model_state, target_date)`

Forecast for a specific single day.

**Parameters**:
- `model_state`: Model from `fit_forecast_model()`
- `target_date`: `date` - Date to forecast

**Returns**: `float` - Forecast value (non-negative)

**Example**:
```python
next_monday = date(2024, 2, 5)  # Assuming Monday
forecast = predict_single_day(model, next_monday)
print(f"Monday forecast: {forecast:.1f} units")
```

---

### Utility Functions

#### `get_forecast_stats(model_state)`

Extract statistical summary from model.

**Returns**: `Dict[str, Any]`
```python
{
    "level": float,
    "min_daily_forecast": float,
    "max_daily_forecast": float,
    "mean_daily_forecast": float,
    "method": str,
    "n_samples": int,
}
```

---

#### `validate_forecast_inputs(history)`

Validate input data format.

**Returns**: `(bool, Optional[str])`
- `(True, None)` if valid
- `(False, error_message)` if invalid

---

#### `quick_forecast(history, horizon=7)`

One-shot forecasting (fit + predict + stats).

**Returns**: `Dict[str, Any]`
```python
{
    "forecast": List[float],
    "model": Dict,
    "stats": Dict,
}
```

**Example**:
```python
result = quick_forecast(history, horizon=7)
print(result["forecast"])  # [10.5, 12.0, ...]
print(result["stats"]["mean_daily_forecast"])  # 11.2
```

---

## Model Behavior

### Exponential Smoothing (Alpha Parameter)

**Alpha (α)** controls sensitivity to recent data:
- **α = 0.1**: Heavy smoothing, slow response
- **α = 0.3**: Balanced (default, recommended)
- **α = 0.9**: Reactive, follows recent trends

**Formula**: `Level(t) = α × Qty(t) + (1 - α) × Level(t-1)`

**When to adjust**:
- Stable demand → lower α (0.1-0.2)
- Volatile demand → higher α (0.4-0.6)
- Trending demand → higher α

---

### DOW Factor Calculation

**Full Model** (≥ 14 samples):
1. For each day of week, calculate `qty / level`
2. Average these ratios across all samples
3. Normalize so `mean(factors) = 1.0`

**Normalization ensures**: `Σ(forecasts) ≈ Σ(historical sales)`

**Example**:
```
Monday sales: [20, 22, 18] → ratios: [2.0, 2.2, 1.8] → factor = 2.0
Weekend sales: [5, 6, 4] → ratios: [0.5, 0.6, 0.4] → factor = 0.5
```

---

### Fallback Strategies

| History Size | Method | DOW Factors | Level Calculation |
|--------------|--------|-------------|-------------------|
| 0 days | `fallback` | [1.0] × 7 | 0.0 |
| 1-6 days | `fallback` | [1.0] × 7 | EMA |
| 7-13 days | `simple` | Partial (where data exists) | EMA |
| ≥ 14 days | `full` | Smoothed, normalized | EMA |

---

## Use Cases

### 1. Reorder Calculation (Most Common)

```python
from src.forecast import fit_forecast_model, predict
from datetime import date

# Fit model
history = load_sales_history(sku="SKU001", days=30)
model = fit_forecast_model(history)

# Forecast for protection period
protection_period = 3  # Days
forecast = predict(model, horizon=protection_period)
forecast_demand = sum(forecast)

# Calculate reorder
safety_stock = 20
inventory_position = 50 + 30  # on_hand + on_order
target_stock = forecast_demand + safety_stock
reorder_qty = max(0, target_stock - inventory_position)

if reorder_qty > 0:
    print(f"Order {reorder_qty:.0f} units")
```

### 2. Weekly Stock Projection

```python
# Project stock for next 7 days
forecast = predict(model, horizon=7)

current_stock = 100
projected_stock = [current_stock]

for daily_forecast in forecast:
    next_stock = projected_stock[-1] - daily_forecast
    projected_stock.append(next_stock)

# Find when stock-out occurs
for day, stock in enumerate(projected_stock):
    if stock < 0:
        print(f"Stock-out expected on day {day}")
        break
```

### 3. Seasonal Pattern Analysis

```python
from src.forecast import get_forecast_stats

model = fit_forecast_model(history)
stats = get_forecast_stats(model)

dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
for dow, (name, factor) in enumerate(zip(dow_names, model['dow_factors'])):
    expected_qty = model['level'] * factor
    print(f"{name}: {factor:.2f}x (≈ {expected_qty:.0f} units)")

# Output example:
# Mon: 1.5x (≈ 15 units)
# Sat: 2.0x (≈ 20 units)
# Sun: 0.5x (≈ 5 units)
```

---

## Integration Examples

### With Existing Order Workflow

```python
from src.workflows.order import OrderWorkflow
from src.forecast import fit_forecast_model, predict
from src.domain.calendar import calculate_protection_period_days, Lane

# Fit forecast model
sales_history = csv_layer.read_sales_records()
history = [
    {"date": s.date, "qty_sold": s.qty_sold}
    for s in sales_history
    if s.sku == "SKU001"
]

model = fit_forecast_model(history)

# Get protection period from calendar
order_date = date.today()
P = calculate_protection_period_days(order_date, Lane.STANDARD)

# Forecast demand for protection period
forecast = predict(model, horizon=P)
forecast_demand = sum(forecast)

# Use in order workflow
order_workflow = OrderWorkflow(csv_layer)
proposal = order_workflow.generate_proposal(
    sku="SKU001",
    description="Widget",
    current_stock=stock,
    daily_sales_avg=model['level'],  # Use forecast level
    # ... other params
)
```

---

## Performance

### Time Complexity
- `fit_forecast_model()`: O(n) where n = history length
- `predict()`: O(h) where h = horizon
- `predict_single_day()`: O(1)

### Space Complexity
- Model state: O(1) (fixed size dict)
- History: O(n) (input data)

### Benchmarks
- Fit 30 days: < 1ms
- Predict 7 days: < 0.1ms
- Total workflow: < 2ms

**Conclusion**: Negligible overhead, suitable for real-time calculations.

---

## Testing

### Test Coverage: 24 tests, 100% passing

**Test Classes**:
1. `TestForecastModelDOWPattern` (3 tests)
   - Strong DOW pattern preservation
   - DOW factor normalization
   - Weekly sum preservation

2. `TestShortHistoryRobustness` (4 tests)
   - Empty history (no crash)
   - Single day history
   - Short history (3 days)
   - Partial DOW coverage (7-13 days)

3. `TestNonNegativeOutputs` (3 tests)
   - Zero sales → zero forecast
   - Negative inputs → non-negative outputs
   - Mixed positive/negative

4. `TestPredictSingleDay` (1 test)
   - Specific date forecasting

5. `TestForecastStats` (1 test)
   - Statistics extraction

6. `TestValidation` (4 tests)
   - Valid history
   - Invalid formats (not list, missing keys, non-numeric)

7. `TestQuickForecast` (2 tests)
   - Complete workflow
   - Invalid input raises ValueError

8. `TestSmoothingParameter` (2 tests)
   - Alpha high follows recent
   - Alpha bounds

9. `TestEdgeCases` (3 tests)
   - All same DOW
   - Date gaps
   - Custom start_date

10. `TestRealWorldScenario` (1 test)
    - Retail weekly pattern (4 weeks)

---

## Limitations & Future Enhancements

### Current Limitations
1. **No trend detection**: Assumes stationary demand
2. **Fixed seasonality**: Only weekly (DOW) patterns
3. **Univariate**: Only uses historical sales (no external features)

### Potential Extensions

#### 1. Trend Component
```python
# Add linear trend: forecast = (level + trend × t) × dow_factor
model_state["trend"] = calculate_trend(history)
```

#### 2. Multi-Week Seasonality
```python
# Detect monthly patterns (week 1-4 within month)
model_state["monthly_factors"] = [4 floats]
```

#### 3. External Regressors
```python
# Include holidays, promotions, weather
fit_forecast_model(history, holidays=[date(...)])
```

#### 4. Confidence Intervals
```python
# Return (forecast, lower_bound, upper_bound)
forecast_with_ci = predict_with_intervals(model, horizon, confidence=0.95)
```

#### 5. Auto-tuning Alpha
```python
# Optimize α based on historical forecast accuracy
alpha_optimal = auto_tune_alpha(history, validation_period=7)
```

---

## Dependencies

**Lightweight** - Uses only Python standard library:
- `datetime` (date handling)
- `statistics` (mean calculations)
- `typing` (type hints)

**No external packages required** (pandas, numpy, scikit-learn, etc.)

**Footprint**: < 15KB source code

---

## Migration from Hardcoded Values

### Before (hardcoded daily_sales_avg)
```python
# Old: manual average
daily_sales_avg = sum(sales_last_30_days) / 30
```

### After (forecasted)
```python
# New: model-based with DOW awareness
model = fit_forecast_model(history)
daily_sales_avg = model['level']  # Or use forecast for specific period
```

**Benefits**:
- Captures seasonality (Monday ≠ Sunday)
- Adapts to recent trends (exponential smoothing)
- Robust to short history (fallback)

---

## FAQ

### Q: Why not use ARIMA, Prophet, or other ML models?

**A**: Production priorities:
- **Simplicity**: No complex tuning, interpretable
- **Robustness**: Works with 3 days or 300 days
- **Performance**: < 2ms execution time
- **Dependencies**: Zero external packages

For most inventory systems, Level + DOW is sufficient. Upgrade to ML when:
- Forecasting 100+ SKUs in batch (parallelization needed)
- Complex seasonality (multi-week, monthly patterns)
- External features (holidays, promotions) critical

### Q: What if demand has a strong upward trend?

**A**: Current model assumes stationary demand (level changes slowly via smoothing). For trending data:
1. Use higher α (0.5-0.7) to follow trend
2. Add trend component (future enhancement)
3. Retrain model frequently (e.g., weekly)

### Q: How often should I retrain the model?

**A**: Recommended:
- **Daily**: If demand is volatile (α = 0.3-0.5)
- **Weekly**: If demand is stable (α = 0.1-0.3)
- **On-demand**: After promotions, seasonality changes

Model is stateless, retraining is fast (< 1ms).

### Q: Can I use this for hourly forecasting?

**A**: Yes, with modifications:
- Replace DOW factors with Hour-of-Day (HOD) factors (24 factors)
- Adjust α for hourly data (recommend 0.1-0.2)
- Use "simple" method if < 168 hours (1 week)

### Q: Output is all zeros. What's wrong?

**A**: Causes:
1. **Empty history**: Use fallback, level=0
2. **All zero sales**: Model level=0.1 (minimum), but forecast still ~0

**Solution**: Check `model["n_samples"]` and `model["method"]`. If `method="fallback"`, insufficient data.

---

## References

- **Code**: [src/forecast.py](src/forecast.py)
- **Tests**: [tests/test_forecast.py](tests/test_forecast.py)
- **Examples**: [examples/forecast_usage.py](examples/forecast_usage.py)
- **Related**: [Calendar Module](CALENDAR_MODULE.md), [Order Workflow](src/workflows/order.py)

---

## Change Log

| Date | Version | Change |
|------|---------|--------|
| 2026-02-02 | 1.0 | Initial implementation |
|  |  | - Level + DOW factors model |
|  |  | - Fallback for short history |
|  |  | - 24 tests, 100% passing |
|  |  | - Zero external dependencies |

---

**Status**: ✅ PRODUCTION READY  
**Maintainer**: Desktop Order System Team  
**License**: Internal use
