# CSL-MC Quantile Implementation - Complete

**Author:** Desktop Order System Team  
**Date:** February 2026  
**Status:** ✅ COMPLETE - All tests passing (703/704, 1 pre-existing failure unrelated)

---

## Executive Summary

Successfully implemented **quantile-first CSL-MC policy** to eliminate hybrid approach where Monte Carlo produced `mu_P` from simulation but `sigma_P` from simple model residuals.

**Core Achievement:** When `forecast_method=monte_carlo`, CSL policy now uses `S = Q(target_csl)` directly from simulated distribution `D_P`, ensuring all statistics (`mu_P`, `sigma_P`, quantiles) derive from the same source.

---

## Problem Statement

**Original Issue:**  
> "Devi rendere la policy CSL coerente con il metodo forecast, evitando l'ibrido: mu da un percorso e sigma da un altro."

**Specific Issues:**
1. **Hybrid path:** MC path produced `mu_P` from simulation but `sigma_P` from residuals of simple model → mathematically incoherent
2. **Incorrect quantiles:** System summed per-day percentiles instead of computing true quantiles of `D_P` distribution
3. **No transparency:** No tracking of whether `S` came from quantile or z-score calculation
4. **Missing metadata:** Simulation parameters not exported for reproducibility

---

## Solution Design

### 1. D_P Distribution Construction

**Key Concept:** Construct explicit distribution of **total demand over P days** by summing each simulation trajectory:

```python
# For each trajectory i in [1..N]:
D_P[i] = sum(sim[i, day] for day in range(P))

# Then:
mu_P = mean(D_P)
sigma_P = std(D_P)
quantiles = percentile(D_P, [50, 80, 90, 95, 98])
```

**Result:** All statistics coherent from same distribution.

### 2. Quantile-First Policy Logic

**When `forecast_method='monte_carlo'` and `policy_mode='csl'`:**

1. **Primary path (quantile):**
   - If `target_csl` matches available quantile key (e.g., 0.95 → "0.95")
   - Set `S = demand.quantiles[alpha_key]`
   - Record `reorder_point_method = "quantile"`
   - Record `quantile_used = S`

2. **Fallback path (z-score):**
   - If exact quantile not available
   - Set `S = mu_P + z * sigma_P` (both from same D_P)
   - Record `reorder_point_method = "z_score_fallback"`

3. **Legacy path (unchanged):**
   - If `policy_mode='legacy'` or `forecast_method='simple'`
   - Use existing z-score logic
   - Record `reorder_point_method = "z_score"` or `"legacy"`

---

## Implementation Steps (All ✅ Complete)

### Step 1: Extend DemandDistribution Contract ✅
**Files:** `src/domain/contracts.py`

**Changes:**
- Added MC metadata fields: `mc_n_simulations`, `mc_random_seed`, `mc_distribution`, `mc_horizon_days`, `mc_output_percentile`
- Changed quantiles dict to use normalized string keys: `"0.50"`, `"0.80"`, `"0.90"`, `"0.95"`, `"0.98"` (not `"p50"`)
- Extended `OrderExplain.to_dict()` to export all new fields
- Updated `CSV_COLUMNS` to include all MC metadata

**Result:** Contract now fully captures MC provenance and quantile tracking.

---

### Step 2: Refactor _build_mc with D_P ✅
**Files:** `src/domain/demand_builder.py`

**Changes:**
- `_build_mc()` now constructs `D_P` as distribution of P-day totals (sum per trajectory)
- Calculate `mu_P = mean(D_P)` and `sigma_P = std(D_P)` - both from same distribution
- Compute quantiles directly from `D_P` percentiles using `numpy.percentile([50, 80, 90, 95, 98])`
- Eliminated old hybrid approach where sigma came from residuals
- Populate all MC metadata fields in returned `DemandDistribution`

**Result:** Coherent statistics from single simulated distribution.

---

### Step 3: Adapt compute_order_v2 for Quantile-First ✅
**Files:** `src/replenishment_policy.py`

**Changes:**
- Added branching logic: if `forecast_method='monte_carlo'` and quantiles available, use `S = demand.quantiles[alpha_key]`
- Track `reorder_point_method`: `"quantile"`, `"z_score"`, `"z_score_fallback"`, `"legacy"`
- Add `quantile_used` field to result dict
- Fallback to `S = mu_P + z*sigma_P` if quantile not available (with warning in future)
- Return both `reorder_point_method` and `quantile_used` in breakdown

**Result:** CSL policy now quantile-first with full transparency.

---

### Step 4: Update propose_order_for_sku Facade ✅
**Files:** `src/workflows/order.py`

**Changes:**
- Extract `reorder_point_method` from `csl_breakdown` (default `"legacy"`)
- Extract `quantile_used` from `csl_breakdown` if CSL mode
- Pass both fields to `OrderExplain` constructor

**Result:** Workflow propagates new fields to explainability layer.

---

### Step 5: Extend OrderExplain and Export ✅
**Files:** `src/domain/contracts.py`, `src/gui/app.py`

**Changes:**
- Added fields: `reorder_point_method`, `quantile_used` to `OrderExplain` dataclass
- Updated `to_dict()` to export new fields as dict items
- Updated `CSV_COLUMNS` list with new field names
- Modified GUI `_export_order_explain()` to use `OrderExplain.CSV_COLUMNS` directly (DRY)

**Result:** Complete audit trail for reorder point calculation method exported to CSV.

---

### Step 6: Test Determinismo/Monotonia CSL-MC ✅
**Files:** `tests/test_csl_mc_quantile.py`

**Tests Created:**

1. **TestCSLMCDeterminism** (2 tests):
   - `test_same_seed_produces_identical_order`: Same seed + data → identical S and Q
   - `test_different_seed_produces_different_order`: Different seed → different Q (confirmed valid output)

2. **TestCSLMCMonotonicity** (2 tests):
   - `test_alpha_increase_monotonic_csl_mc`: Alpha sequence [0.80, 0.85, 0.90, 0.95, 0.98] → S and Q monotonically increasing
   - `test_quantile_method_used_when_available`: MC + CSL → `reorder_point_method == "quantile"` with correct quantile value

3. **TestCSLMCCoherence** (2 tests):
   - `test_mu_sigma_from_same_distribution`: Verify MC metadata populated and sigma_P > 0
   - `test_sigma_not_from_residuals`: Verify sigma_P differs from old hybrid value

4. **TestCSLMCLegacyRegression** (1 test):
   - `test_legacy_mode_unaffected_by_mc_changes`: Legacy mode output unchanged

5. **TestCSLMCExportability** (1 test):
   - `test_explain_to_dict_has_mc_fields`: Verify all MC fields present in export and JSON-serializable

**Result:** ✅ **8/8 tests passing** - All STOP conditions verified.

---

### Step 7: Test Regressione Legacy ✅
**Tests Run:**
- `tests/test_contracts_golden.py`: ✅ **29/29 passing**
- Full suite: ✅ **703/704 passing** (1 pre-existing failure unrelated to CSL-MC)

**Result:** No regressions introduced. Legacy golden tests unchanged.

---

## Test Results Summary

| Test Suite | Status | Details |
|------------|--------|---------|
| `test_csl_mc_quantile.py` | ✅ 8/8 | All CSL-MC coherence tests passing |
| `test_contracts_golden.py` | ✅ 29/29 | Legacy golden tests unchanged |
| **Full suite** | ✅ **703/704** | 1 pre-existing failure unrelated |

**Pre-existing failure:** `test_storage_adapter_fase5.py::test_backend_fallback_when_sqlite_unavailable` (unrelated to CSL-MC)

---

## Key Outcomes

### 1. Mathematical Coherence ✅
- `mu_P` and `sigma_P` both derived from `D_P` distribution
- Quantiles computed from `D_P`, not sum of daily percentiles
- No hybrid paths mixing MC and simple methods

### 2. Determinism ✅
- Same seed + same data → identical Q and S
- Reproducible results for auditing and debugging

### 3. Monotonicity ✅
- Higher target CSL → higher S and Q (never decrease)
- Verified across alpha sequence [0.80, 0.85, 0.90, 0.95, 0.98]

### 4. Transparency ✅
- `reorder_point_method` field tracks calculation path: `"quantile"`, `"z_score"`, `"z_score_fallback"`, `"legacy"`
- `quantile_used` field records actual quantile value used
- All MC metadata exported: `mc_n_simulations`, `mc_random_seed`, `mc_distribution`, `mc_horizon_days`, `mc_output_percentile`

### 5. Backward Compatibility ✅
- Legacy policy mode unchanged
- Simple forecast method unchanged
- Existing golden tests pass without modification

---

## Files Modified

| File | Purpose | Key Changes |
|------|---------|-------------|
| `src/domain/contracts.py` | Contract types | Added MC metadata + quantile tracking to DemandDistribution and OrderExplain |
| `src/domain/demand_builder.py` | Demand construction | Refactored `_build_mc()` with D_P approach, coherent mu/sigma/quantiles |
| `src/replenishment_policy.py` | CSL calculation | Quantile-first logic in `compute_order_v2` with method tracking |
| `src/workflows/order.py` | Order workflow | Propagate `reorder_point_method` and `quantile_used` to OrderExplain |
| `src/gui/app.py` | Export | Use `OrderExplain.CSV_COLUMNS` directly (DRY) |
| `tests/test_csl_mc_quantile.py` | Testing | 8 tests verifying determinism, monotonicity, coherence, exportability |

---

## Usage Example

### CSL-MC with Quantile (target_csl=0.95)

```python
from src.workflows.order import propose_order_for_sku
from datetime import date, timedelta

# Settings with Monte Carlo enabled
settings = {
    "reorder_engine": {
        "policy_mode": {"value": "csl"},
        "forecast_method": {"value": "monte_carlo"},
    },
    "service_level": {"default_csl": {"value": 0.95}},
    "monte_carlo": {
        "distribution": {"value": "empirical"},
        "n_simulations": {"value": 1000},
        "random_seed": {"value": 42},
    },
}

# Run order proposal
proposal, explain = propose_order_for_sku(
    sku_obj=sku,
    history=sales_history,
    stock=current_stock,
    pipeline=[],
    asof_date=date(2026, 2, 18),
    target_receipt_date=date(2026, 2, 25),
    protection_period_days=14,
    settings=settings,
)

# Verify quantile method used
assert explain.reorder_point_method == "quantile"
assert explain.quantile_used > 0
assert explain.demand.quantiles["0.95"] == explain.quantile_used
assert explain.reorder_point == explain.quantile_used

# Export includes MC metadata
export_dict = explain.to_dict()
print(export_dict["mc_n_simulations"])      # 1000
print(export_dict["mc_random_seed"])        # 42
print(export_dict["mc_distribution"])       # "empirical"
print(export_dict["reorder_point_method"])  # "quantile"
print(export_dict["quantile_used"])         # e.g., 182.5
```

---

## Future Enhancements

### Optional Improvements (Not Required)

1. **Quantile interpolation:**
   - Support alphas not in pre-computed set (e.g., 0.94)
   - Linear interpolation between nearest quantiles

2. **Distribution diagnostics:**
   - Export D_P histogram for validation
   - Add skewness/kurtosis metrics to detect non-normality

3. **Warning system:**
   - Warn if z-score fallback used (quantile not available)
   - Alert if sigma_P very different from expected range

4. **GUI visualization:**
   - Show D_P distribution plot in order proposal
   - Compare quantile method vs z-score in explainability panel

---

## Validation Checklist

- [x] **Determinism:** Same seed → same Q and S
- [x] **Monotonicity:** Higher alpha → higher S and Q
- [x] **Coherence:** mu_P and sigma_P from same D_P
- [x] **Quantile correctness:** Q(alpha) computed from D_P percentiles
- [x] **Method tracking:** reorder_point_method field populated correctly
- [x] **Export completeness:** All MC metadata in CSV
- [x] **Z-score fallback:** Works when quantile not available
- [x] **Legacy invariance:** Golden tests unchanged
- [x] **No regressions:** Full suite passing (703/704)
- [x] **Code quality:** Type hints, docstrings, no TODOs

---

## Conclusion

**Implementation Status:** ✅ **COMPLETE**

Quantile-first CSL-MC policy successfully eliminates hybrid approach and provides:
- Mathematical coherence (mu_P, sigma_P from same D_P)
- Deterministic results (same seed → same output)
- Monotonic behavior (higher alpha → higher S/Q)
- Full transparency (method tracking, MC metadata export)
- Zero regressions (703/704 tests passing, legacy unchanged)

**Next Actions:** None required. Feature ready for production use.

**Documentation:** This file + inline code comments provide complete reference.

---

**End of Implementation Report**
