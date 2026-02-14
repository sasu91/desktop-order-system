# CSL Policy Mode - Quick Reference Guide

## What is CSL Mode?

CSL (Customer Service Level) mode is an advanced order policy that calculates optimal reorder quantities based on target service levels (probability of not stocking out during the protection period).

### Legacy Mode vs CSL Mode

| Feature | Legacy Mode | CSL Mode |
|---------|-------------|----------|
| Formula | S = forecast + safety_stock | S = μ_P + z(α) × σ_P |
| Input | Lead time, review period, safety stock | Target CSL (α), sales history |
| Output | Fixed safety stock | Dynamic safety stock based on α and σ_P |
| Variability | Manual (STABLE/MODERATE/HIGH) | Calculated from history |
| Transparency | Forecast, S, IP | α, z-score, μ_P, σ_P, S |

## Enabling CSL Mode

### Step 1: Configure Settings
1. Open **Settings** tab
2. Navigate to **Reorder Engine** section
3. Find **🎯 Modalità Policy Ordini**
4. Change from `legacy` to `csl`
5. Click **Salva Impostazioni**

### Step 2: Set Target CSL (Optional)

#### Global Default (All SKUs)
1. Open **Settings** tab
2. Navigate to **Service Level** section
3. Set **Default CSL** (recommended: 0.90-0.95)

#### Per-SKU Override
1. Open **Gestione SKU** tab
2. Select SKU or create new
3. Set **CSL Target** field (0.50-0.999)
4. Priority chain:
   - SKU-specific `target_csl` (highest priority)
   - Perishability (shelf_life ≤ 7 days → higher CSL)
   - Variability cluster CSL
   - Global default (lowest priority)

### Step 3: Generate Proposals
1. Open **Proposta Ordini** tab
2. Select SKU(s)
3. Click **Genera Tutte Proposte**
4. Proposals now use CSL calculations

## Understanding CSL Breakdown

### Proposal Details Display

When you click **Dettagli** on a proposal in CSL mode, you'll see:

```
═══ CSL POLICY BREAKDOWN ═══
Policy Mode: CSL (Target Service Level)
Lane: STANDARD
Target α (CSL): 0.950
z-score: 1.64

Reorder Point S: 85.2 pz
Forecast Demand μ_P: 70.0 pz
Demand Uncertainty σ_P: 9.3 pz
```

### Key Metrics Explained

- **Target α (CSL)**: Probability of not stocking out (0.95 = 95% service level)
- **z-score**: Standard deviations above mean for target CSL (1.64 for 95%)
- **Reorder Point S**: When to reorder (S = μ_P + z × σ_P)
- **Forecast Demand μ_P**: Expected demand over protection period
- **Demand Uncertainty σ_P**: Standard deviation of demand over protection period
- **Lane**: Order lane (STANDARD, SATURDAY, MONDAY for Friday orders)

### Optional Fields

- **Effective α (after censored boost)**: Adjusted CSL if out-of-stock periods detected
- **⚠️ Censored periods detected: N**: Number of OOS days that inflated alpha

## Interpreting Results

### Example Scenario

**SKU**: ABC123  
**Target CSL**: 0.95 (95% service level)  
**History**: 90 days, avg 5 pz/day, σ = 1.5 pz/day  
**Protection Period**: 14 days

**Legacy Mode**:
- Forecast: 5 × 14 = 70 pz
- Safety Stock: 10 pz (manual setting)
- S = 80 pz
- IP = 65 pz
- Proposed: 15 pz

**CSL Mode**:
- μ_P: 70 pz (5 × 14)
- σ_P: 5.6 pz (1.5 × √14)
- z(0.95): 1.64
- S = 70 + 1.64 × 5.6 = 79.2 pz
- IP = 65 pz
- Proposed: 15 pz

**Result**: Similar proposed qty, but CSL mode dynamically adjusts based on actual demand variability.

### High Variability SKU

**SKU**: XYZ789  
**Target CSL**: 0.98 (98% service level, perishable)  
**History**: σ = 3.0 pz/day (high variance)

**Legacy Mode**:
- S = 70 + 10 = 80 pz

**CSL Mode**:
- σ_P: 11.2 pz (3.0 × √14)
- z(0.98): 2.05
- S = 70 + 2.05 × 11.2 = 93.0 pz
- **+13 pz more safety stock** due to high variability

## Friday Dual-Lane Orders

CSL mode fully supports Friday dual-lane ordering:

1. **Saturday Lane** (short protection period = 2 days):
   - Lower μ_P, lower S, smaller order
   
2. **Monday Lane** (long protection period = 4 days):
   - Higher μ_P, higher S, larger order
   - Accounts for Saturday order in pipeline

**Example**:
- Friday order, Saturday delivery: 20 pz (covers weekend)
- Friday order, Monday delivery: 35 pz (covers weekend + Monday arrivals)

## Troubleshooting

### CSL Mode Shows "legacy_fallback"

**Cause**: CSL computation failed (e.g., weekend order date, insufficient history)

**Solution**:
1. Check logs for error message
2. Ensure order date is Monday-Friday (weekday 0-4)
3. Verify sales history has at least 4 weeks of data
4. Temporarily switch to legacy mode if persistent

### Proposed Qty Seems Too High/Low

**Diagnosis**: Check CSL breakdown
- **Too high**: σ_P might be high (demand variability underestimated in legacy)
- **Too low**: α might be too low for SKU criticality

**Solution**:
- Adjust **SKU-specific target_csl** (increase for critical items)
- Review sales history quality (outliers, promos, stockouts affecting σ_P)
- Compare with legacy mode to validate

### CSL Breakdown Not Showing

**Cause**: Policy mode is still "legacy"

**Solution**:
1. Settings → Reorder Engine → Policy Mode → `csl`
2. Salva Impostazioni
3. Regenerate proposals

## Best Practices

### 1. **Gradual Rollout**
- Start with 5-10 pilot SKUs
- Compare CSL vs legacy for 2-4 weeks
- Monitor service levels and inventory

### 2. **Target CSL Assignment**
- **Perishable** (≤7 days shelf life): 0.96-0.98
- **Fast movers** (>50 units/day): 0.93-0.95
- **Standard items**: 0.90-0.92
- **Slow movers**: 0.85-0.88

### 3. **History Quality**
- Minimum 4 weeks (28 days)
- Recommended 12 weeks (84 days)
- Clean outliers (promo spikes, stockouts)

### 4. **Monitor KPIs**
- **Service Level**: Actual vs target α
- **Inventory Days**: Compare CSL vs legacy
- **Stockout Frequency**: Should decrease with CSL
- **Overstock**: Monitor max_stock caps

### 5. **Validation**
- Use proposal **Dettagli** to audit CSL logic
- Check z-score matches α (1.28 @ 90%, 1.64 @ 95%, 2.05 @ 98%)
- Verify σ_P seems reasonable given sales variance

## FAQ

**Q: Can I mix legacy and CSL mode?**  
A: Yes, but only at the order generation level. Change `policy_mode` in settings, then generate. Each proposal is calculated with the current mode.

**Q: Does CSL mode work with Monte Carlo forecast?**  
A: Yes, CSL uses the forecast method setting (simple/monte_carlo) for μ_P calculation. σ_P is always calculated from historical variance.

**Q: What if I have no sales history?**  
A: CSL will fall back to legacy mode. Ensure at least 4 weeks of sales data for CSL to work.

**Q: How does CSL handle shelf life?**  
A: CSL respects `usable_qty` (shelf-life adjusted on_hand) and waste risk penalties. The α target ensures high service level without excessive waste.

**Q: Can I override α per order?**  
A: Not directly in GUI (uses SKU target_csl). For one-off overrides, temporarily change SKU's `target_csl`, generate proposal, then revert.

**Q: Does CSL support promo uplift?**  
A: Yes, CSL uses the same forecast enrichment (promo uplift, cannibalization, post-promo dip) as legacy mode. μ_P reflects adjusted forecast.

## Support

- **Implementation Details**: See `CSL_POLICY_IMPLEMENTATION.md`
- **Test Coverage**: Run `pytest test_csl_policy_integration.py -v`
- **Algorithm**: See `src/replenishment_policy.py::compute_order`
- **Pipeline Logic**: See `src/analytics/pipeline.py`

---

**Version**: 1.0  
**Date**: 2026-02-14  
**Default Mode**: Legacy (backward compatible)
