# Explainability & Operational Visibility System

**Status**: ✅ **IMPLEMENTED**  
**Date**: 2026-02-12  
**Objective**: "Rendere i risultati operativi: vista eccezioni, spiegazioni delle decisioni, export per revisione"

---

## 🎯 Overview

This feature provides **operational transparency** for order proposals, enabling users to answer "**Perché questa quantità?**" for every SKU with clear visibility into:

1. **Standard Explainability Drivers**: Target CSL, sigma horizon, reorder point, constraints, OOS impact, forecast method
2. **Smart Exception Filters**: Automated identification of problematic SKUs requiring attention (high OOS, low OTIF, high WMAPE, critical perishability)
3. **Export Order+KPI+Breakdown**: Comprehensive CSV export with live KPI recalculation for internal audit
4. **Quick-Links**: One-click navigation from exception view to SKU admin edit form

---

## 🧩 Implementation Components

### 1. **OrderProposal Model Extension** (`src/domain/models.py`)

Added 10 new explainability fields to `OrderProposal` dataclass (lines 382-391):

```python
# Explainability drivers (standard transparency)
target_csl: float = 0.0  # Alpha (target CSL) usato per safety stock
sigma_horizon: float = 0.0  # Deviazione standard domanda su horizon
reorder_point: int = 0  # Reorder point SKU
forecast_method: str = ""  # simple, monte_carlo, etc.
policy_mode: str = ""  # legacy, csl
equivalent_csl_legacy: float = 0.0  # Equivalente CSL per policy legacy (informativo)
constraints_applied_pack: bool = False  # True se arrotondamento pack applicato
constraints_applied_moq: bool = False  # True se MOQ constraint applicato
constraints_applied_max: bool = False  # True se max_stock cap applicato
constraint_details: str = ""  # Dettaglio testuale vincoli (es. "Pack: 12→24, MOQ: 10, Max: 500")
```

**Purpose**: Store standard driver information for every proposal, enabling UI transparency and export traceability.

---

### 2. **Workflow Explainability Extraction** (`src/workflows/order.py`)

Added explainability field population in `generate_proposal()` method (lines ~1147-1240):

**CSL Mode Extraction**:
- `target_csl`, `sigma_horizon`, `reorder_point` from `csl_breakdown` dict (returned by `compute_order()`)
- `constraints_applied_*` booleans parsed from `csl_result["constraints_applied"]` list
- `constraint_details` text built from constraints list

**Legacy Mode Extraction**:
- `equivalent_csl_legacy` computed as approximation (informational, non-binding) using safety stock heuristic
- Constraints inferred from local rounding/MOQ/cap logic
- `constraint_details` built from inferred constraints

**All Modes**:
- `policy_mode` set from `csl_breakdown["policy_mode"]`
- `forecast_method` set from SKU/global forecast method setting
- `reorder_point` set to `S` (target reorder point)

**Example Constraint Parsing (CSL mode)**:
```python
constraints_list = csl_result.get("constraints_applied", [])
# Example: ["pack_size: 100 → 120 (rounded up to 12 units/pack)", "moq: 80 < 100 → 0 (below MOQ)"]
for constraint_str in constraints_list:
    if "pack_size" in constraint_str.lower():
        expl_constraints_pack = True
    if "moq" in constraint_str.lower():
        expl_constraints_moq = True
    if "max_stock" in constraint_str.lower():
        expl_constraints_max = True
```

---

### 3. **Proposal Detail Panel UI Update** (`src/gui/app.py`)

Enhanced `_on_proposal_select()` method (lines 1451-1505) with **"PERCHÉ QUESTA QUANTITÀ?"** section:

**Displayed Fields** (before detailed breakdown):
```
═══ PERCHÉ QUESTA QUANTITÀ? ═══
Policy Mode: CSL
Forecast Method: MONTE_CARLO
Reorder Point (S): 450 pz
Inventory Position (IP): 280 pz
Target CSL (α): 0.950 (95.0%)
Domanda incertezza (σ): 35.2 pz
z-score: 1.64
⚠️ Giorni OOS rilevati: 3
   Boost applicato: +20%

Vincoli Applicati:
  ✓ Pack size (12 pz/collo)
  ✓ MOQ (100 pz)
  Dettagli: pack_size: 168 → 180 (arrotondato a 12 pz/collo); moq: 80 < 100 → 100
```

**For Legacy Mode**:
- Shows `CSL Equivalente (informativo): 0.870 (87.0%)` with disclaimer "(approssimazione per confronto, non vincolante)"
- No `sigma_horizon` or `z-score` (not computed in legacy)

**User Benefit**: Every proposal now has clear, standardized answer to "Why this quantity?" visible at the top of detail panel.

---

### 4. **Smart Exception Filters Tab** (`src/gui/app.py`)

Added **"SKU Problematici (Filtri Smart)"** section in Exception tab (lines 3247-3333):

**Filter Checkboxes** (4 types):
1. **OOS Rate Alto**: SKUs with OOS rate > threshold (default 15%)
2. **OTIF Basso/Unfulfilled**: SKUs with OTIF < threshold (default 80%) OR unfulfilled > 0
3. **WMAPE Alto**: SKUs with forecast error > threshold (default 50%)
4. **Perishability Critica**: SKUs with shelf_life < threshold (default 7d) AND stock > 10x shelf_life

**Filter Logic** (`_refresh_smart_exceptions()` method, lines 3747-3887):
- Load latest KPI from `kpi_daily.csv` for each SKU
- Load unfulfilled from current proposals (if available)
- Calculate current stock from ledger
- Apply enabled filters with user-configurable thresholds
- Build "Motivo Alert" string showing all failing criteria
- Sort by severity (number of reasons DESC, then OOS rate DESC)

**Table Columns**:
| SKU | Descrizione | OOS % | OTIF % | Unfulfilled | WMAPE % | Shelf Life (d) | Stock Attuale | Motivo Alert |
|-----|-------------|-------|--------|-------------|---------|----------------|---------------|--------------|

**Example Row**:
```
SKU001 | Prodotto A | 18.5% | 75.0% | 50 | 65.2% | 5 | 120 | OOS alto (18.5%); OTIF basso (75.0%) + Unfulfilled (50); WMAPE alto (65.2%); Shelf life critica (5d, stock=120)
```

**User Benefit**: "Reduce noise" by showing only SKUs that need attention, with clear multi-criteria alerts.

---

### 5. **Export Order+KPI+Breakdown CSV** (`src/gui/app.py`)

Added menu item: **File → Esporta in CSV → 📊 Ordini + KPI + Breakdown** (line 157).

**Export Function** (`_export_order_kpi_breakdown()`, lines 5575-5800):

**Workflow**:
1. Generate timestamp filename: `order_kpi_breakdown_20260212_143052.csv`
2. Create `export/` folder if not exists
3. Load proposals (use current if available, else generate fresh for all SKUs)
4. **Live KPI recalculation** for every SKU using:
   - `compute_oos_kpi()` → OOS rate, OOS days
   - `compute_forecast_accuracy()` → WMAPE, MAE
   - `compute_supplier_proxy_kpi()` → OTIF, unfulfilled qty/events
5. Join proposals + KPIs + SKU params into single CSV row per SKU
6. Write CSV with 30+ columns (see schema below)
7. Show success messagebox with row count
8. **Log EXPORT_LOG audit trail** (operation, file path, policy mode, KPI params)

**CSV Schema** (30 columns):
```
SKU, Descrizione, Qty Proposta, Receipt Date, Policy Mode, Forecast Method,
Target CSL, Sigma Horizon, Reorder Point, Inventory Position,
Pack Size, MOQ, Max Stock,
Constraint Pack, Constraint MOQ, Constraint Max, Constraint Details,
OOS Days Count, OOS Boost Applied, Shelf Life Days, Usable Stock, Waste Risk %,
KPI: OOS Rate %, KPI: OOS Days, KPI: WMAPE %, KPI: MAE,
KPI: OTIF %, KPI: Unfulfilled Qty, KPI: Unfulfilled Events, KPI: Waste Rate %,
Notes
```

**Example Row**:
```csv
SKU001,Prodotto A,180,2026-02-15,csl,monte_carlo,0.950,35.2,450,280,12,100,1000,YES,YES,NO,"pack_size: 168→180; moq: enforced",3,YES,30,250,5.2,18.5,12,65.2,8.5,75.0,50,2,0.0,S=450 IP=280 Pack=12 MOQ=100
```

**KPI Recalculation**: Always uses **live data** (not cached `kpi_daily.csv`) to ensure export reflects current state. Lookback period from settings (`oos_lookback_days`).

**User Benefit**: Single-file audit-ready export with all decision drivers + KPIs + breakdown, suitable for management review or Excel pivot analysis.

---

### 6. **Audit Trail EXPORT_LOG** (`src/persistence/csv_layer.py`)

Export operations logged to `audit_log.csv` as new operation type: **`EXPORT_LOG`**.

**Audit Entry Example**:
```csv
2026-02-12T14:30:52,EXPORT_LOG,"Order+KPI+Breakdown export: 145 SKUs, file=order_kpi_breakdown_20260212_143052.csv, policy=csl, kpi_lookback=90d",None
```

**Log Details Included**:
- Row count (SKUs exported)
- Filename (for traceability)
- Policy mode (legacy/csl)
- KPI lookback days (for reproducibility)
- Timestamp (from log_audit standard)

**User Benefit**: Full traceability of export operations; can cross-reference export file with audit log timestamp for compliance/internal review.

---

### 7. **Quick-Link Exception → SKU Edit** (`src/gui/app.py`)

**Two Access Methods**:

#### A. **Double-Click on Smart Exception Row**
- Binds `<Double-1>` event to `smart_exception_treeview` (line 3331)
- Triggers `_open_sku_in_admin_from_smart()` method (lines 3889-3913)

#### B. **"Apri in Admin" Button**
- Button in smart exceptions action frame (line 3328)
- Same handler as double-click

**Workflow** (`_open_sku_in_admin_from_smart()`):
1. Get selected SKU from smart exception table
2. Switch notebook to **Admin tab** (`self.notebook.select(self.admin_tab)`)
3. Find SKU row in `admin_treeview`
4. Select and focus row (with `see()` to scroll into view)
5. Call `_edit_sku()` to open edit form directly

**User Benefit**: One-click from "problematic SKU alert" to "edit target CSL/shelf life/etc." form. Workflow: Exception tab → See alert → Double-click → Admin tab opens with SKU form pre-populated → Adjust target_csl → Save → Regenerate proposal with new parameters.

---

## 📊 Usage Scenarios

### Scenario 1: Order Proposal Review
**User Question**: "Why is SKU001 proposing 180 units?"

**Action**: Click on SKU001 in proposal table.

**Explainability Panel Shows**:
```
═══ PERCHÉ QUESTA QUANTITÀ? ═══
Policy Mode: CSL
Forecast Method: MONTE_CARLO
Reorder Point (S): 450 pz
Inventory Position (IP): 280 pz
Target CSL (α): 0.950 (95.0%)
Domanda incertezza (σ): 35.2 pz
z-score: 1.64

Vincoli Applicati:
  ✓ Pack size (12 pz/collo)
  ✓ MOQ (100 pz)
  Dettagli: pack_size: 168 → 180 (arrotondato a 12 pz/collo)
```

**Answer**: Proposed qty = 180 because:
1. Reorder point S=450, current IP=280 → raw proposal = 170
2. Rounded up to pack size 12 → 180
3. MOQ=100 satisfied
4. Target CSL 95% with sigma 35.2 → z-score 1.64

---

### Scenario 2: Exception Triage
**User Goal**: "Show me only SKUs that need urgent attention"

**Action**:
1. Go to **Exception tab**
2. Smart filters section shows checkboxes (all enabled by default)
3. Adjust thresholds: OOS > 15%, OTIF < 80%, WMAPE > 50%, Shelf Life < 7d
4. Click "🔄 Aggiorna Filtri"

**Result**: Table shows 5 problematic SKUs:
```
SKU002 | Prodotto B | 22.3% | 65.0% | 80 | 72.1% | - | 350 | OOS alto (22.3%); OTIF basso (65.0%) + Unfulfilled (80); WMAPE alto (72.1%)
SKU005 | Prodotto E | 8.2% | 95.0% | - | 35.0% | 5 | 200 | Shelf life critica (5d, stock=200)
...
```

**User Action**: Double-click SKU002 → Admin tab opens with SKU002 edit form → Increase `target_csl` from 0.90 to 0.95 → Save → Regenerate proposals.

**User Benefit**: 5 SKUs to review instead of 145 → 97% noise reduction.

---

### Scenario 3: Monthly Audit Export
**User Goal**: "Export all proposals + KPIs for January 2026 review meeting"

**Action**:
1. Generate proposals for all SKUs (or use current)
2. **File → Esporta in CSV → 📊 Ordini + KPI + Breakdown**
3. Save to `export/order_kpi_breakdown_20260212_143052.csv`

**File Contents**: 145 rows (one per SKU) with 30 columns:
- Explainability: Policy Mode, Target CSL, Sigma, Reorder Point, Constraints
- KPIs: OOS Rate, WMAPE, OTIF, Unfulfilled (all live-calculated)
- Proposal: Qty, Receipt Date, Notes

**Manager Action**: Open in Excel, create pivot table:
- Filter: `OOS Rate > 20%` → Shows 8 SKUs
- Sort by: `WMAPE DESC` → Identify worst forecast performers
- Group by: `Policy Mode` → Compare CSL vs Legacy performance

**Audit Trail**: `audit_log.csv` entry:
```csv
2026-02-12T14:30:52,EXPORT_LOG,"Order+KPI+Breakdown export: 145 SKUs, file=order_kpi_breakdown_20260212_143052.csv, policy=csl, kpi_lookback=90d",None
```

**User Benefit**: Single CSV with all decision drivers + KPIs, ready for management review or compliance audit.

---

## 🧪 Testing Strategy

### Unit Tests (Recommended)
```python
# tests/test_explainability.py
def test_csl_explainability_fields():
    """Verify CSL mode populates target_csl, sigma_horizon, constraints."""
    proposal = generate_proposal_csl_mode(sku="TEST01")
    assert proposal.target_csl > 0.0
    assert proposal.sigma_horizon > 0.0
    assert proposal.reorder_point > 0
    assert proposal.policy_mode == "csl"
    assert proposal.forecast_method in ["simple", "monte_carlo"]

def test_legacy_equivalent_csl():
    """Verify legacy mode computes informational equivalent CSL."""
    proposal = generate_proposal_legacy_mode(sku="TEST02", safety_stock=50)
    assert proposal.equivalent_csl_legacy > 0.0  # Approximation computed
    assert proposal.target_csl == 0.0  # Legacy doesn't use explicit alpha
    assert proposal.policy_mode == "legacy"

def test_constraint_details_parsing():
    """Verify constraint details string is built correctly."""
    proposal = generate_proposal_with_constraints(pack_size=12, moq=100)
    assert proposal.constraints_applied_pack == True
    assert proposal.constraints_applied_moq == True
    assert "pack_size" in proposal.constraint_details.lower()
    assert "moq" in proposal.constraint_details.lower()
```

### Manual GUI Tests
1. **Explainability Panel**:
   - Generate proposals → Select SKU with CSL mode → Verify "PERCHÉ QUESTA QUANTITÀ?" section shows all drivers
   - Select SKU with legacy mode → Verify "CSL Equivalente (informativo)" displays with disclaimer

2. **Smart Filters**:
   - Enable all filters → Verify table shows only SKUs failing at least one criterion
   - Disable all filters → Verify table shows "Nessun filtro attivo" message
   - Adjust thresholds (e.g., OOS > 5%) → Verify more SKUs appear

3. **Export**:
   - File → Esporta → Order+KPI+Breakdown → Verify CSV generated in `export/` folder
   - Open CSV in Excel → Verify 30 columns present, data looks correct
   - Check `audit_log.csv` → Verify EXPORT_LOG entry with correct timestamp

4. **Quick-Link**:
   - Exception tab → Smart filters → Double-click problematic SKU → Verify Admin tab opens with SKU edit form
   - Click "Apri in Admin" button → Same result

### Regression Tests
- Run existing test suite (`pytest tests/`) to ensure no breakage in proposal generation, KPI calculation, or CSV layer.

---

## 🔑 Key Design Decisions

### Decision 1: EXPORT_LOG as Audit Operation Only
**User Chose**: Register export as `EXPORT_LOG` operation in `audit_log.csv`, NOT as transaction in `transactions.csv`.

**Rationale**: Export is metadata/administrative action, not inventory movement. Audit log provides sufficient traceability without polluting transaction ledger.

**Alternative Rejected**: Create `EventType.EXPORT_LOG` transaction → Would clutter ledger with non-inventory events.

---

### Decision 2: Live KPI Recalculation for Export
**User Chose**: Always recalculate KPIs from raw data during export, not use cached `kpi_daily.csv`.

**Rationale**: Export is for audit/review → must reflect **current state**, not stale cache. Cached KPI may be outdated if transactions added since last dashboard refresh.

**Alternative Rejected**: Use cached KPI from `kpi_daily.csv` → Faster but risks exporting stale data.

---

### Decision 3: Quick-Link Both Modes (Double-Click + Button)
**User Chose**: Support both double-click on row AND "Apri in Admin" button.

**Rationale**:
- Double-click: Fast for power users
- Button: Discoverable for new users (with hint label)

**Alternative Rejected**: Button only → Less convenient for power users.

---

### Decision 4: Legacy Equivalent CSL in Detail Panel Only
**User Chose**: Show `equivalent_csl_legacy` in proposal detail panel (sidebar), NOT in proposal table column or export as primary field.

**Rationale**: Equivalent CSL is informational approximation, not binding. Showing in table column might confuse users into thinking it's actionable. Detail panel context makes "informativo" disclaimer clear.

**Alternative Rejected**: Add table column "Equiv. CSL" → Would clutter table, risk misinterpretation as actual target.

---

## 📝 Integration with Existing Features

### With Closed-Loop KPI Tuning
- Smart exception filters use KPIs from `kpi_daily.csv` → If closed-loop applied adjustments, exceptions will reflect updated KPIs
- Export shows both **current proposal** (with adjusted CSL) AND **historical KPIs** (lookback 90d) → Can compare before/after closed-loop

### With Shelf Life System
- Export includes `Shelf Life Days`, `Usable Stock`, `Waste Risk %` columns
- Smart filters include "Perishability Critica" criterion → Flags SKUs with short shelf life + high stock

### With Calendar System
- Export shows `Receipt Date` (calendar-aware if dual-lane Friday order)
- Explainability panel shows protection period vs traditional lead+review if calendar override applied

### With Monte Carlo Forecast
- Export shows `Forecast Method` (simple/monte_carlo)
- Explainability panel shows MC distribution, simulations, percentile if MC used

---

## 🚀 Future Enhancements (Out of Scope for MVP)

1. **Constraint Strength Indicator**: Color-code constraints by severity (green=pack only, yellow=MOQ, red=max_stock cap)
2. **Export Format Options**: Add Excel (.xlsx) export with formatted sheets (Proposals, KPIs, Summary pivot)
3. **Smart Filter Presets**: Save/load filter configurations (e.g., "Critical SKUs", "Forecast Issues", "Supplier Problems")
4. **Historical Comparison Export**: Add columns showing "Proposal Last Week", "KPI Delta vs Last Month"
5. **Auto-Export Scheduling**: Background task to auto-export every Monday for weekly review meetings

---

## 📚 Documentation Updates Required

1. **User Manual**: Add "Explainability Panel" section with screenshot + field descriptions
2. **Admin Guide**: Document smart filter thresholds + adjustment recommendations
3. **Export Schema Doc**: Create CSV column reference guide for external audit teams
4. **Tutorial Video**: Record 5-minute walkthrough: "From Exception Alert to Parameter Tuning"

---

## ✅ Acceptance Criteria Met

| Requirement | Status | Evidence |
|-------------|--------|----------|
| **Explainability**: Show standard drivers for every proposal (alpha/CSL, sigma, reorder point, constraints, OOS, forecast method) | ✅ | Detail panel "PERCHÉ QUESTA QUANTITÀ?" section with 10 fields |
| **Equivalent CSL for Legacy**: Show informational equivalent CSL for legacy mode proposals | ✅ | `equivalent_csl_legacy` field computed and displayed with disclaimer |
| **Exception View**: Tab/filters showing SKUs with high OOS, low OTIF, high WMAPE, critical perishability | ✅ | Smart filters section with 4 criteria + configurable thresholds |
| **Export CSV**: Order + KPI + Breakdown with timestamp to `export/` folder | ✅ | Menu item → CSV with 30 columns → Saved to `export/order_kpi_breakdown_{timestamp}.csv` |
| **Audit Trail**: Register EXPORT_LOG in audit_log.csv | ✅ | `log_audit(operation="EXPORT_LOG", details=...)` called after export |
| **Quick-Links**: From exception row, button/double-click to open SKU edit with target CSL override | ✅ | Double-click + "Apri in Admin" button → Admin tab with edit form |

**User Validation Quote**: "For each ordered SKU, user can answer 'perché questa quantità?'" → ✅ **ACHIEVED**

---

**Last Updated**: 2026-02-12  
**Implementation Status**: ✅ Complete, ready for GUI testing  
**Next Steps**: Manual GUI testing, user acceptance, potential fine-tuning of thresholds based on production data
