# Intermittent Demand Forecasting - Quick Reference

> **TL;DR**: Special forecasting for SKUs with many zero-sale days and unpredictable spikes. Use `intermittent_auto` to let the system pick the best method automatically.

---

## What Problem Does This Solve?

**Traditional forecasting (moving average) fails for sparse demand:**
- **Problem:** SKU sells 0, 0, 0, 45, 0, 0, 50, 0, 0, 0, 40 → moving average oscillates wildly
- **Solution:** Intermittent methods (Croston, SBA, TSB) separate **timing** from **size** of demand events

**When to use:** SKUs with:
- Many zero-sale days (> 25% zeros)
- Unpredictable spikes in demand
- Seasonal specialty items, slow-movers, clearance items

---

## Quick Start (3 Steps)

### 1. Enable Globally (Recommended)

**Settings → Forecast → Global Forecast Method:**
```
Change: simple → intermittent_auto
```

**What happens:** System auto-classifies each SKU:
- **Intermittent pattern detected** → Use Croston/SBA/TSB (automatic selection)
- **Stable pattern** → Fallback to simple forecast (no change)

### 2. Or Enable Per-SKU

**SKU Form → Forecast Method:**
```
Options:
- intermittent_auto  ← Auto-select best intermittent method
- croston            ← Forces Croston (unbiased)
- sba                ← Forces SBA (bias-corrected, default)
- tsb                ← Forces TSB (good for obsolescence)
```

### 3. Verify Results

**After order proposal, open:** `order_explain_{date}.csv`

**Key columns:**
- `intermittent_classification` → TRUE if detected as intermittent
- `intermittent_adi` → Average days between demands (> 1.32 = sparse)
- `intermittent_cv2` → Demand variability (> 0.49 = high variability)
- `intermittent_method` → Actual method used (croston/sba/tsb)

---

## The Three Methods Explained

| Method | Best For | Formula | Pros | Cons |
|--------|----------|---------|------|------|
| **Croston** | General intermittent | z / p | Simple, unbiased for interval | Slight positive bias in forecast |
| **SBA** | Default intermittent | (z / p) × (1 - α/2) | Bias-corrected, most accurate | Slightly complex |
| **TSB** | Declining/obsolescence | b × z | Tracks declining probability | Needs clear trend |

**z** = size estimate (how much when demand occurs)  
**p** = interval estimate (average days between demands)  
**b** = probability (chance of demand per day)  
**α** = smoothing parameter (default: 0.1)

---

## Classification Logic (Automatic)

**SKU is classified as intermittent if BOTH:**
1. **ADI (Average Demand Interval) > 1.32**  
   → Demand doesn't happen every day (at least 1.32 days between purchases on average)

2. **CV² (Squared Coefficient of Variation) > 0.49**  
   → High variability in demand sizes (when demand occurs, it's unpredictable)

**Example calculations:**

| SKU | Pattern | ADI | CV² | Classification |
|-----|---------|-----|-----|----------------|
| Bread | 10,12,9,11,10,12 (daily) | 1.0 | 0.02 | ❌ Stable (use simple) |
| Specialty Tea | 0,0,0,5,0,0,0,6,0,0 (sparse) | 3.3 | 0.08 | ❌ Sparse but consistent sizes (use simple) |
| Craft Beer | 0,0,45,0,0,0,50,0,0,40 (erratic) | 3.0 | 0.85 | ✅ Intermittent (use SBA/TSB) |
| Old Stock | 30,0,25,0,20,0,15,0,10,0 (declining) | 2.0 | 0.65 | ✅ Intermittent + obsolescence (TSB) |

---

## Settings Reference

**Location:** Settings Tab → "Intermittent Forecast"

### Core Settings

| Setting | Default | What It Does | Adjust If... |
|---------|---------|--------------|--------------|
| **Enabled** | ✓ | Master switch for intermittent forecasting | Never (keep enabled) |
| **ADI Threshold** | 1.32 | Minimum sparsity to classify intermittent | You want stricter (raise to 2.0) or looser (lower to 1.1) |
| **CV² Threshold** | 0.49 | Minimum variability to classify intermittent | Too many false positives (raise to 0.6) |
| **Alpha Default** | 0.1 | Smoothing: lower=stable, higher=responsive | Forecasts too noisy (lower to 0.05) or too slow (raise to 0.2) |
| **Lookback Days** | 90 | Historical window for classification | Seasonal patterns (extend to 180) |

### Backtest Settings (Auto-Selection)

| Setting | Default | What It Does |
|---------|---------|--------------|
| **Backtest Enabled** | ✓ | Test Croston/SBA/TSB on history, pick best |
| **Backtest Periods** | 4 | Number of rolling test folds (more = slower but accurate) |
| **Backtest Metric** | wmape | Accuracy measure (wmape=absolute error, bias=over/under) |
| **Backtest Min History** | 28 | Min days needed to run backtest (else use default) |

### Policy Settings

| Setting | Default | What It Does |
|---------|---------|--------------|
| **Default Method** | sba | If backtest disabled or not enough history |
| **Fallback to Simple** | ✓ | Use simple forecast if not classified intermittent (recommended) |
| **Obsolescence Window** | 14 | Days to check for declining trend (TSB preference) |

---

## Common Workflows

### Workflow 1: Adopt Intermittent for All SKUs (Global)

**Goal:** Let system auto-classify every SKU, use best method for each

1. **Settings → Forecast → Global Forecast Method:** `intermittent_auto`
2. **Settings → Intermittent Forecast → Fallback to Simple:** ✓ (ensure checked)
3. **Save settings**
4. **Run order proposal**
5. **Check:** Open `order_explain_{date}.csv`, filter `intermittent_classification=TRUE`
6. **Result:** Intermittent SKUs use SBA/TSB, stable SKUs still use simple (no disruption)

**Pros:** Zero manual work, safe (fallback protects stable SKUs)  
**Cons:** All-or-nothing (can't test one SKU first)

---

### Workflow 2: Pilot Test on Single Problem SKU

**Goal:** Try intermittent forecast on one SKU before rolling out

1. **Identify candidate:** SKU with many zeros + forecast errors (e.g., "SEASONAL_SALSA")
2. **SKU Form → Open SKU → Forecast Method:** Select `intermittent_auto`
3. **Save**
4. **Run order proposal**
5. **Open `order_explain_{date}.csv`, find SKU row:**
   ```csv
   sku,intermittent_classification,intermittent_method,intermittent_adi,intermittent_cv2,mu_P
   SEASONAL_SALSA,True,sba,3.8,0.72,38.5
   ```
6. **Validate:** Does `mu_P=38.5` seem reasonable? Check recent history.
7. **Monitor:** After delivery, compare `mu_P` to actual demand. If closer than old forecast → adopt more SKUs.

**Pros:** Safe, easy to revert (change back to simple)  
**Cons:** Manual per-SKU (time-consuming for many SKUs)

---

### Workflow 3: Force TSB for Known Obsolescence

**Goal:** SKU has declining sales trend, want forecast to reduce automatically

1. **Example:** "OLD_PRODUCT_X" sales went from 50/week → 10/week over 2 months
2. **SKU Form → Forecast Method:** Select `tsb` (force TSB method)
3. **Save, run order proposal**
4. **Check OrderExplain CSV:**
   ```csv
   intermittent_b_t,intermittent_z_t,mu_P
   0.18,12.5,31.5  # b_t=18% prob → low forecast
   ```
5. **Interpretation:** TSB detected low demand probability (18%), reducing forecast accordingly
6. **Result:** Avoid over-ordering obsolete stock

**When to use:** Clear declining trend visible in sales history  
**Caution:** If trend reverses (promo, relaunch), TSB will lag—switch back to `sba` or `intermittent_auto`

---

## Troubleshooting

### Issue 1: SKU Not Classified as Intermittent (But Has Many Zeros)

**Symptoms:**
- `intermittent_classification = FALSE`
- ADI shows > 2.0 (sparse)
- But CV² < 0.49 (low variability)

**Cause:** Non-zero demand sizes are consistent (e.g., always 10 units when demand occurs)

**Solution:** 
- **If OK:** Simple forecast works fine for consistent sizes, no action needed
- **If forecast still bad:** Lower CV² threshold in settings (e.g., 0.4) to catch this pattern

**Example:**
| Day | Sales | Issue |
|-----|-------|-------|
| 1-3 | 0, 0, 0 | Zeros present |
| 4 | 10 | Demand occurs |
| 5-7 | 0, 0, 0 | More zeros |
| 8 | 10 | Consistent size → CV² low |

**Diagnosis:** Pattern is sparse (ADI=4) but predictable (CV²=0), intermittent methods don't help.

---

### Issue 2: Forecast Too Volatile (Spiky)

**Symptoms:**
- `mu_P` changes dramatically between proposals (40 → 65 → 30)
- Frequent over/under-ordering

**Cause:** Alpha too high (overreacting to recent spikes)

**Solution:**
1. **Settings → Intermittent Forecast → Alpha Default:** Lower from 0.1 to 0.05
2. **Trade-off:** More stable forecast, but slower to detect real trend changes
3. **Re-run proposal, check if smoother**

**Rule of thumb:**
- **Alpha = 0.05:** Ultra-stable (use for long shelf-life, slow-changing demand)
- **Alpha = 0.1:** Default (balanced)
- **Alpha = 0.2:** Responsive (use for fast-changing trends, obsolescence)

---

### Issue 3: Backtest Results Seem Wrong

**Symptoms:**
- `intermittent_backtest_wmape = 0.0` (should have value)
- Or: backtesting picks TSB but SBA visually looks better

**Cause 1:** Not enough history (< 28 days default)
- **Check:** `intermittent_n_nonzero` < 10 → insufficient data
- **Solution:** Wait for more history, or use `default_method = sba` without backtest

**Cause 2:** Test periods too few (4 folds on 30 days = ~7 days per fold, noisy)
- **Solution:** Increase `backtest_min_history` to 60 or `backtest_periods` to 6

**Cause 3:** All methods perform similarly (flat time series)
- **Behavior:** Backtest picks arbitrarily (e.g., whichever ran first)
- **Solution:** Not a problem—if all methods equal, choice doesn't matter

---

### Issue 4: TSB Forecast Lower Than Expected (Declining)

**Symptoms:**
- `intermittent_method = tsb`
- `intermittent_b_t = 0.12` (very low probability)
- Forecast seems pessimistic

**Cause:** TSB detected obsolescence (declining demand probability)

**Verify:**
1. **Check recent history:** Are sales actually declining? (e.g., last 14 days avg < previous 14 days)
2. **If TRUE decline:** TSB correct—reduce forecast to avoid dead stock
3. **If FALSE alarm (e.g., temporary stockout):** Force method to `sba` or wait for demand to resume

**Override:**
1. **SKU Form → Forecast Method:** Change `tsb` → `sba` (ignores probability decay)
2. **Or:** Adjust `obsolescence_window` in settings to longer period (e.g., 28 days)

---

## Explainability: Reading OrderExplain CSV

### Key Columns Reference

| Column | Type | What It Means | Action If... |
|--------|------|---------------|--------------|
| `intermittent_classification` | bool | TRUE = classified intermittent | FALSE but many zeros → check ADI/CV² |
| `intermittent_adi` | float | Avg days between demands | > 5 = very sparse (good for intermittent) |
| `intermittent_cv2` | float | Demand size variability | > 0.8 = erratic (good for SBA) |
| `intermittent_method` | str | Method used (croston/sba/tsb) | tsb + low b_t → obsolescence detected |
| `intermittent_alpha` | float | Smoothing parameter | High values (>0.15) → may cause volatility |
| `intermittent_z_t` | float | Latest size estimate | Compare to actual avg sale size |
| `intermittent_p_t` | float | Latest interval (Croston/SBA) | Compare to actual ADI |
| `intermittent_b_t` | float | Latest probability (TSB only) | < 0.2 → low demand probability (declining) |
| `intermittent_backtest_wmape` | float | Backtest error % (0-1) | > 0.4 = poor fit (try different method) |
| `intermittent_n_nonzero` | int | Non-zero demand days | < 5 → insufficient data (fallback to simple) |

### Example Interpretation

```csv
sku,intermittent_classification,intermittent_adi,intermittent_cv2,intermittent_method,intermittent_z_t,intermittent_p_t,intermittent_b_t,mu_P
CRAFT_BEER_X,True,3.6,0.78,sba,15.2,3.58,0.0,59.5
```

**Reading:**
1. ✅ Classified intermittent (ADI=3.6 > 1.32, CV²=0.78 > 0.49)
2. Method: SBA (bias-corrected)
3. When demand occurs: avg size = 15.2 units (`z_t`)
4. Demand occurs every ~3.6 days (`p_t`)
5. Forecast over 14 days: 15.2 / 3.6 × 14 = 59.5 units (`mu_P`)
6. No `b_t` value (SBA doesn't use probability, only TSB does)

**Validation:** 
- Realistic? Recent history shows 12-18 unit sales every 3-4 days → ✅ matches
- Action: Accept forecast, no adjustment needed

---

## Best Practices

### ✅ DO

- **Use `intermittent_auto` globally** (safe with fallback enabled)
- **Monitor first 2-3 cycles** after enabling (compare forecasts to actuals)
- **Export OrderExplain CSV** regularly to audit classifications
- **Lower alpha for stable industries** (grocery: 0.05, MRO: 0.08)
- **Raise alpha for fast obsolescence** (fashion clearance: 0.2)
- **Enable backtest** (finds best method per SKU automatically)
- **Check `intermittent_n_nonzero` ≥ 10** for reliable forecasts

### ❌ DON'T

- **Don't disable fallback** (unless you want intermittent methods forced on stable SKUs)
- **Don't set alpha > 0.3** (extreme noise, unstable forecasts)
- **Don't ignore ADI/CV² thresholds** (changing without data analysis causes misclassification)
- **Don't force TSB on growing SKUs** (TSB assumes decline, will underestimate)
- **Don't expect miracles with < 30 days history** (intermittent methods need data)
- **Don't compare intermittent vs. simple on stable SKUs** (intermittent will fallback to simple anyway)

---

## Performance Tips

### For Large Catalogs (1000+ SKUs)

**Problem:** Order proposal takes too long with backtest enabled

**Solutions:**
1. **Disable backtest globally, set `default_method = sba`** (skip rolling validation)
   - Cost: Lose per-SKU method optimization
   - Gain: 4x faster proposal generation

2. **Increase `backtest_min_history` to 60 days** (only backtest SKUs with rich history)
   - Cost: New SKUs use default SBA
   - Gain: 2x faster (fewer backtests run)

3. **Lower `backtest_periods` from 4 to 2** (fewer test folds)
   - Cost: Less accurate method selection
   - Gain: 2x faster backtesting

### For Real-Time Reordering

**Problem:** Need instant forecast updates during day (not just batch overnight)

**Solution:** Pre-calculate intermittent models at proposal time, cache results
- **Implementation:** Store `intermittent_z_t`, `intermittent_p_t`, `intermittent_b_t` in SKU table
- **Update:** On-demand recalc only if new sales recorded since last proposal
- **Benefit:** Sub-second forecast retrieval vs. 1-2ms re-fitting

---

## Migration Checklist (For New Adopters)

- [ ] **Backup current settings** (`settings.csv` copy)
- [ ] **Test on dev/staging** with last 30 days production data
- [ ] **Identify 5-10 pilot SKUs** (known intermittent patterns)
- [ ] **Enable `intermittent_auto` per-SKU** for pilots only
- [ ] **Run 1 proposal cycle,** validate forecasts manually
- [ ] **Check OrderExplain CSV** for pilot SKUs (classification correct?)
- [ ] **Monitor 2-3 delivery cycles** (forecast vs. actual demand)
- [ ] **If successful:** Enable globally via forecast method dropdown
- [ ] **Set up quarterly review** (WMAPE audit, alpha tuning)

---

## FAQs

### Q: Can I use intermittent methods for stable SKUs with no zeros?
**A:** Yes (if you force method via SKU form), but it's unnecessary—simple forecast is equally accurate and faster. The system will classify stable SKUs as non-intermittent, so `intermittent_auto` will fallback to simple automatically (if `fallback_to_simple=True`).

### Q: What if I have daily sales but they're very small and variable (e.g., 1-3 units/day)?
**A:** ADI will be ~1.0 (demand every day), so not classified intermittent. Use simple or monte_carlo forecast. Intermittent methods are for *sparse* demand (many zeros), not just *variable* demand.

### Q: Does intermittent forecasting work with promo/holiday uplift?
**A:** Yes, but apply uplift **after** base forecast. Example:
1. Intermittent forecast: `mu_P = 40` (baseline)
2. Promo uplift: 50% increase
3. Final forecast: `mu_P = 40 × 1.5 = 60`

The intermittent methods provide the base, uplift layers adjust on top.

### Q: Can I override alpha per-SKU instead of global default?
**A:** Not yet (future feature). Current workaround: segment SKUs by category, run separate proposals with different settings profiles.

### Q: What happens if demand suddenly spikes after long zero period (viral product)?
**A:** Intermittent methods will lag initially (smoothing effect), but with alpha=0.1, forecast updates within 2-3 cycles. For faster response, temporarily raise alpha to 0.2 for that SKU, or use `simple` method (moving avg reacts immediately).

### Q: How does OOS censoring interact with intermittent forecasting?
**A:** Censored days (stockout, no sales possible) are **excluded** from ADI/CV² calculation and model fitting. This prevents misclassifying a stocked-out SKU as intermittent. Example:
- Sales: 10, 0 (censored), 0 (censored), 12, 0, 0, 15
- Non-censored: 10, 12, 0, 0, 15
- ADI calculated on non-censored only: 5 days / 3 nonzero = 1.67

---

## Glossary

| Term | Definition |
|------|------------|
| **ADI** | Average Demand Interval - days between demand events (n_total / n_nonzero) |
| **CV²** | Squared Coefficient of Variation - (std/mean)² of non-zero demands |
| **Croston** | Original intermittent method (Croston 1972), exponential smoothing on interval/size |
| **SBA** | Syntetos-Boylan Approximation - bias-corrected Croston with (1 - α/2) factor |
| **TSB** | Teunter-Syntetos-Babai - probability-based intermittent method for obsolescence |
| **Alpha (α)** | Smoothing parameter (0-1): 0=no update, 1=full react to latest; typical 0.05-0.3 |
| **Backtest** | Rolling origin validation - test forecast on historical holdout data |
| **WMAPE** | Weighted Mean Absolute Percentage Error - accuracy metric (lower is better) |
| **Obsolescence** | Declining demand pattern (recent avg < old avg), detected by TSB's b_t decay |
| **z_t** | Size estimate - expected demand amount when demand occurs |
| **p_t** | Interval estimate - expected days until next demand (Croston/SBA) |
| **b_t** | Probability estimate - chance of demand per day (TSB only) |
| **mu_P** | Forecast mean - expected demand over P-day protection period |
| **sigma_P** | Forecast uncertainty - standard deviation of demand over P days |

---

## Quick Decision Tree

```
┌─ Do you have SKUs with many zero-sale days?
│
├─ NO → Use "simple" or "monte_carlo" forecast (intermittent not needed)
│
└─ YES → Continue below
    │
    ├─ Do you want automatic method selection?
    │  ├─ YES → Use "intermittent_auto" (backtest picks best)
    │  └─ NO → Choose specific method:
    │         ├─ General intermittent → "sba" (bias-corrected, default)
    │         ├─ Obsolescence/declining → "tsb" (probability decay)
    │         └─ Academic/research → "croston" (original unbiased)
    │
    ├─ Are forecasts too volatile?
    │  └─ YES → Lower alpha (0.1 → 0.05)
    │
    ├─ Are forecasts too slow to react?
    │  └─ YES → Raise alpha (0.1 → 0.2)
    │
    └─ Want to validate before global rollout?
       └─ YES → Test on 5-10 SKUs first (SKU form override), monitor 2 cycles
```

---

## Support & Further Reading

**Full Technical Documentation:** [INTERMITTENT_FORECAST_IMPLEMENTATION.md](INTERMITTENT_FORECAST_IMPLEMENTATION.md)

**Academic Papers (Optional Deep Dive):**
- Croston (1972): Original method for intermittent demand
- Syntetos & Boylan (2001): Bias correction (SBA method)
- Teunter et al. (2010): TSB method for obsolescence

**Project Files:**
- Core implementation: [src/domain/intermittent_forecast.py](src/domain/intermittent_forecast.py)
- Builder integration: [src/domain/demand_builder.py](src/domain/demand_builder.py)
- Test suite: [tests/test_intermittent_forecast.py](tests/test_intermittent_forecast.py)

**Need Help?**
1. Check OrderExplain CSV first (most issues visible in metadata)
2. Review troubleshooting section above
3. Run test suite to verify installation: `pytest tests/test_intermittent_forecast.py -v`

---

**Last Updated:** January 2025  
**Version:** 1.0 (Initial Release)  
**Status:** Production-Ready ✅
