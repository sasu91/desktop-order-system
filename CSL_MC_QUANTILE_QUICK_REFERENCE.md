# CSL-MC Quantile - Quick Reference

**Date:** February 2026  
**Status:** Production Ready  
**Test Coverage:** 703/704 passing

---

## What Changed

**Before:** CSL-MC used hybrid approach - `mu_P` from simulation, `sigma_P` from residuals → **incoherent**

**After:** CSL-MC uses quantile-first - `S = Q(target_csl)` from simulated distribution `D_P` → **coherent**

---

## Key Concepts

### D_P Distribution
**Total demand over protection period P:**
```python
D_P[i] = sum of trajectory i over P days
mu_P = mean(D_P)
sigma_P = std(D_P)
quantiles = percentile(D_P, [50, 80, 90, 95, 98])
```

### Quantile-First Logic
1. **Primary:** `S = quantiles[target_csl]` if available
2. **Fallback:** `S = mu_P + z*sigma_P` if quantile missing
3. **Legacy:** Unchanged for `policy_mode=legacy`

---

## Configuration

### Enable CSL-MC with Quantile

```python
settings = {
    "reorder_engine": {
        "policy_mode": {"value": "csl"},
        "forecast_method": {"value": "monte_carlo"},
    },
    "service_level": {
        "default_csl": {"value": 0.95},  # Use 0.80, 0.85, 0.90, 0.95, 0.98 for direct quantile
    },
    "monte_carlo": {
        "distribution": {"value": "empirical"},
        "n_simulations": {"value": 1000},
        "random_seed": {"value": 42},  # For determinism
    },
}
```

### Pre-Computed Quantiles
Available alphas: **0.50, 0.80, 0.90, 0.95, 0.98**

If `target_csl` matches one of these → quantile method used directly  
If not → z-score fallback with `mu_P + z*sigma_P` (still coherent)

---

## Explainability Fields

### New Fields in OrderExplain

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `reorder_point_method` | str | Calculation method used | `"quantile"` |
| `quantile_used` | float | Actual quantile value if used | `182.5` |
| `mc_n_simulations` | int | Number of MC trajectories | `1000` |
| `mc_random_seed` | int | Seed for reproducibility | `42` |
| `mc_distribution` | str | Distribution type | `"empirical"` |
| `mc_horizon_days` | int | Protection period P | `14` |
| `mc_output_percentile` | int | Output stat percentile | `80` |
| `quantiles_json` | str | JSON dict of all quantiles | `{"0.50": 140, "0.95": 182.5}` |

### reorder_point_method Values

| Value | Meaning |
|-------|---------|
| `"quantile"` | Used Q(alpha) from D_P directly |
| `"z_score"` | Used mu + z*sigma (simple method) |
| `"z_score_fallback"` | MC but quantile unavailable, used mu_P + z*sigma_P |
| `"legacy"` | Legacy policy mode |

---

## Validation

### Check Quantile Method Used

```python
from src.workflows.order import propose_order_for_sku

proposal, explain = propose_order_for_sku(...)

# Verify quantile method
if explain.reorder_point_method == "quantile":
    print(f"✅ Used quantile: {explain.quantile_used}")
    print(f"✅ Alpha: {explain.target_csl}")
    print(f"✅ S = Q({explain.target_csl}) = {explain.reorder_point}")
else:
    print(f"⚠️ Fallback method: {explain.reorder_point_method}")
```

### Export to CSV

```python
from src.gui.app import OrderViewTab

# OrderExplain.to_dict() includes all new fields
export_dict = explain.to_dict()

# CSV export automatically includes:
# - mc_n_simulations
# - mc_random_seed
# - mc_distribution
# - mc_horizon_days
# - mc_output_percentile
# - reorder_point_method
# - quantile_used
# - quantiles_json
```

---

## Testing

### Run CSL-MC Tests

```bash
# CSL-MC quantile tests (determinism, monotonicity, coherence)
pytest tests/test_csl_mc_quantile.py -v

# Golden legacy tests (no regression)
pytest tests/test_contracts_golden.py -v

# Full suite
pytest tests/ --ignore=tests/test_migration_fase4.py -q
```

### Expected Results
- `test_csl_mc_quantile.py`: **8/8 passing**
- `test_contracts_golden.py`: **29/29 passing**
- Full suite: **703/704 passing** (1 pre-existing failure)

---

## Troubleshooting

### Issue: Z-Score Fallback Used Instead of Quantile

**Cause:** `target_csl` not in pre-computed quantiles [0.50, 0.80, 0.90, 0.95, 0.98]

**Solution:** Use one of the pre-computed alphas, or accept fallback (still coherent)

---

### Issue: Quantile Value Seems Wrong

**Check:**
1. Verify seed is fixed: `mc_random_seed` in settings
2. Check history has sufficient data (>= 56 days recommended)
3. Verify protection period P is reasonable (e.g., lead_time + review_period)

**Debug:**
```python
print(f"Quantiles: {explain.demand.quantiles}")
print(f"mu_P: {explain.demand.mu_P}")
print(f"sigma_P: {explain.demand.sigma_P}")
print(f"Protection period: {explain.demand.mc_horizon_days}")
```

---

### Issue: Determinism Broken (Different Q Each Run)

**Cause:** No fixed seed in Monte Carlo settings

**Solution:**
```python
settings["monte_carlo"]["random_seed"]["value"] = 42  # Fixed seed
```

---

## Best Practices

### 1. Use Fixed Seeds in Production
```python
"monte_carlo": {"random_seed": {"value": 42}}
```
→ Ensures reproducibility for auditing

### 2. Use Standard CSL Values
Use **0.90, 0.95, 0.98** for direct quantile method

### 3. Monitor reorder_point_method in Reports
Export `reorder_point_method` to track quantile vs fallback usage

### 4. Validate MC Parameters
- `n_simulations >= 1000` for stable quantiles
- `protection_period_days >= 7` for meaningful distribution

---

## Performance Notes

### Quantile Computation Cost
- Pre-computed during `_build_mc()` in demand builder
- No additional cost at CSL policy evaluation time
- Quantiles computed once per demand distribution build

### Typical Performance
- MC simulation: ~100ms for 1000 trajectories × 14 days
- Quantile computation: ~1ms (numpy.percentile)
- Total CSL-MC order proposal: ~120ms

---

## Migration from Old Hybrid Approach

### No Manual Migration Required
- Old code path automatically replaced
- Existing settings work without changes
- Legacy golden tests pass unchanged

### Verification Steps
1. Run order proposal with `forecast_method='monte_carlo'`
2. Check `explain.reorder_point_method == "quantile"`
3. Export to CSV and verify new columns present
4. Compare Q values: New Q from quantile may differ slightly from old hybrid Q

### Expected Differences
- New Q may be **higher** for high CSL (0.95+) due to proper quantile from D_P
- New Q more conservative for skewed demand patterns
- New Q deterministic with fixed seed (old was not fully deterministic)

---

## Support

### Documentation
- Full implementation: `CSL_MC_QUANTILE_IMPLEMENTATION.md`
- Tests: `tests/test_csl_mc_quantile.py`
- Code: `src/domain/demand_builder.py`, `src/replenishment_policy.py`

### Key Files
- `src/domain/contracts.py`: DemandDistribution and OrderExplain contracts
- `src/domain/demand_builder.py`: D_P construction in `_build_mc()`
- `src/replenishment_policy.py`: Quantile-first logic in `compute_order_v2()`
- `src/workflows/order.py`: Field propagation in `propose_order_for_sku()`

---

**Last Updated:** February 2026  
**Version:** 1.0  
**Status:** Production Ready

