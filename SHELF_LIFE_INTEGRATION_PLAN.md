# Shelf Life Integration Plan - Desktop Order System

## Executive Summary

Piano completo per integrare la shelf life e le scadenze nel motore di riordino (legacy + Monte Carlo), con soft penalty configurabile per categoria prodotto.

**Status**: ✅ Fondamenta implementate (modelli, settings, calcoli core)  
**Next**: Integrazione nei workflow di riordino e UI

---

## 1. Modifiche ai Modelli (✅ COMPLETATO)

### SKU Model (`src/domain/models.py`)

Aggiunti nuovi parametri:
```python
# Shelf life operational parameters
min_shelf_life_days: int = 0    # Minimum residual shelf life for sale
waste_penalty_mode: str = ""    # "soft", "hard", or "" (use global)
waste_penalty_factor: float = 0.0  # Soft penalty multiplier 0.0-1.0
waste_risk_threshold: float = 0.0  # Waste risk % threshold (0-100)
```

**Validazioni**:
- `min_shelf_life_days >= 0`
- `min_shelf_life_days <= shelf_life_days` (se shelf_life_days > 0)
- `waste_penalty_mode in ["", "soft", "hard"]`
- `waste_penalty_factor in [0.0, 1.0]`
- `waste_risk_threshold in [0.0, 100.0]`

---

## 2. Settings Globali (✅ COMPLETATO)

### `data/settings.json` - Nuova sezione `shelf_life_policy`

```json
"shelf_life_policy": {
  "enabled": {
    "value": true,
    "description": "Abilita integrazione shelf life nel motore riordino"
  },
  "min_shelf_life_global": {
    "value": 7,
    "auto_apply_to_new_sku": false,
    "description": "Giorni shelf life residua minima (globale)"
  },
  "waste_penalty_mode": {
    "value": "soft",
    "description": "soft (riduzione qty) o hard (blocco ordine)"
  },
  "waste_penalty_factor": {
    "value": 0.5,
    "description": "Riduzione qty in soft penalty (0.5 = -50%)"
  },
  "waste_risk_threshold": {
    "value": 15.0,
    "description": "Soglia waste risk % per penalty"
  },
  "waste_horizon_days": {
    "value": 14,
    "description": "Orizzonte giorni calcolo waste risk"
  },
  "category_overrides": {
    "value": {
      "STABLE": {
        "min_shelf_life_days": 7,
        "waste_penalty_factor": 0.3,
        "waste_risk_threshold": 20.0
      },
      "LOW": {
        "min_shelf_life_days": 5,
        "waste_penalty_factor": 0.5,
        "waste_risk_threshold": 15.0
      },
      "HIGH": {
        "min_shelf_life_days": 10,
        "waste_penalty_factor": 0.7,
        "waste_risk_threshold": 10.0
      },
      "SEASONAL": {
        "min_shelf_life_days": 7,
        "waste_penalty_factor": 0.6,
        "waste_risk_threshold": 12.0
      }
    },
    "description": "Override per categoria demand_variability"
  }
}
```

**Logica Override**:
1. SKU-specific parameters (se != 0 o "")
2. Category override (se enabled e category match)
3. Global defaults

---

## 3. Calcoli Core - Ledger (`src/domain/ledger.py`) (✅ COMPLETATO)

### `UsableStockResult` (dataclass)

```python
@dataclass
class UsableStockResult:
    total_on_hand: int
    usable_qty: int  # Qty with shelf life >= min_shelf_life_days
    unusable_qty: int  # Qty expiring too soon
    expiring_soon_qty: int  # Qty in waste risk window
    waste_risk_percent: float  # % stock at risk
```

### `ShelfLifeCalculator.calculate_usable_stock()`

**Input**:
- `lots: List[Lot]` (tutti i lotti per SKU)
- `check_date: date` (tipicamente oggi)
- `min_shelf_life_days: int` (soglia minima vendibilità)
- `waste_horizon_days: int` (finestra rischio spreco)

**Logica**:
1. Per ogni lotto:
   - Se `expiry_date is None` → usable (infinite shelf life)
   - Se `days_left < 0` → unusable (scaduto)
   - Se `days_left < min_shelf_life_days` → unusable (shelf life insufficiente)
   - Se `days_left <= waste_horizon_days` → usable MA expiring_soon
   - Altrimenti → usable (shelf life buona)

2. `waste_risk_percent = (expiring_soon_qty / total_on_hand) * 100`

**Output**: `UsableStockResult`

### `ShelfLifeCalculator.apply_shelf_life_penalty()`

**Input**:
- `proposed_qty: int`
- `waste_risk_percent: float`
- `waste_risk_threshold: float`
- `penalty_mode: str` ("soft" | "hard")
- `penalty_factor: float` (0.0-1.0)

**Logica**:
```python
if waste_risk_percent < waste_risk_threshold:
    return proposed_qty, ""  # No penalty

if penalty_mode == "hard":
    return 0, "❌ BLOCKED: Waste risk too high"

elif penalty_mode == "soft":
    adjusted_qty = int(proposed_qty * (1.0 - penalty_factor))
    return adjusted_qty, f"⚠️ Reduced by {penalty_factor*100}%"
```

---

## 4. Integrazione Workflow - Order.py (🔲 TODO)

### Modifiche a `OrderWorkflow.generate_proposal()`

**Step 1: Carica Settings Shelf Life**
```python
# Dopo lettura settings
settings = self.csv_layer.read_settings()
shelf_life_enabled = settings.get("shelf_life_policy", {}).get("enabled", {}).get("value", True)

if not shelf_life_enabled:
    # Logica esistente senza modifiche
    pass
```

**Step 2: Determina Parametri Shelf Life (con Category Override)**
```python
# Get category-specific overrides
category = sku_obj.demand_variability.value if sku_obj else "STABLE"
category_overrides = settings.get("shelf_life_policy", {}).get("category_overrides", {}).get("value", {})
category_params = category_overrides.get(category, {})

# SKU > Category > Global fallback
min_shelf_life = sku_obj.min_shelf_life_days if (sku_obj and sku_obj.min_shelf_life_days > 0) else \
                 category_params.get("min_shelf_life_days", 
                 settings.get("shelf_life_policy", {}).get("min_shelf_life_global", {}).get("value", 7))

waste_penalty_mode = sku_obj.waste_penalty_mode if (sku_obj and sku_obj.waste_penalty_mode) else \
                     settings.get("shelf_life_policy", {}).get("waste_penalty_mode", {}).get("value", "soft")

waste_penalty_factor = sku_obj.waste_penalty_factor if (sku_obj and sku_obj.waste_penalty_factor > 0) else \
                       category_params.get("waste_penalty_factor",
                       settings.get("shelf_life_policy", {}).get("waste_penalty_factor", {}).get("value", 0.5))

waste_risk_threshold = sku_obj.waste_risk_threshold if (sku_obj and sku_obj.waste_risk_threshold > 0) else \
                       category_params.get("waste_risk_threshold",
                       settings.get("shelf_life_policy", {}).get("waste_risk_threshold", {}).get("value", 15.0))

waste_horizon_days = settings.get("shelf_life_policy", {}).get("waste_horizon_days", {}).get("value", 14)
```

**Step 3: Calcola Usable Stock**
```python
# Fetch lots for SKU
from ..domain.ledger import ShelfLifeCalculator

lots = self.csv_layer.get_lots_by_sku(sku, sort_by_expiry=True)

usable_result = ShelfLifeCalculator.calculate_usable_stock(
    lots=lots,
    check_date=date.today(),
    min_shelf_life_days=min_shelf_life,
    waste_horizon_days=waste_horizon_days
)

# Usa usable_qty invece di on_hand per calcolo IP
# NEW: IP = usable_qty + on_order - unfulfilled_qty
inventory_position = usable_result.usable_qty + current_stock.on_order - current_stock.unfulfilled_qty
```

**Step 4: Applica Penalty al Proposed Qty (DOPO rounding/MOQ/cap)**
```python
# Dopo: proposed_qty = ...  (con tutti i rounding)

# Apply shelf life penalty if waste risk high
shelf_life_penalty_applied = False
shelf_life_penalty_message = ""

if usable_result.waste_risk_percent >= waste_risk_threshold:
    original_proposed = proposed_qty
    proposed_qty, penalty_msg = ShelfLifeCalculator.apply_shelf_life_penalty(
        proposed_qty=proposed_qty,
        waste_risk_percent=usable_result.waste_risk_percent,
        waste_risk_threshold=waste_risk_threshold,
        penalty_mode=waste_penalty_mode,
        penalty_factor=waste_penalty_factor
    )
    
    if penalty_msg:
        shelf_life_penalty_applied = True
        shelf_life_penalty_message = penalty_msg
        notes += f" | {penalty_msg}"
```

**Step 5: Aggiungi Info a OrderProposal**
```python
return OrderProposal(
    # ... campi esistenti ...
    
    # NEW: Shelf life details
    usable_stock=usable_result.usable_qty,
    unusable_stock=usable_result.unusable_qty,
    waste_risk_percent=usable_result.waste_risk_percent,
    shelf_life_penalty_applied=shelf_life_penalty_applied,
    shelf_life_penalty_message=shelf_life_penalty_message,
)
```

**Modifiche a OrderProposal Model**:
```python
@dataclass
class OrderProposal:
    # Existing fields...
    
    # NEW: Shelf life info
    usable_stock: int = 0
    unusable_stock: int = 0
    waste_risk_percent: float = 0.0
    shelf_life_penalty_applied: bool = False
    shelf_life_penalty_message: str = ""
```

---

## 5. Integrazione Replenishment Policy (🔲 TODO)

### `src/replenishment_policy.py` - `calculate_reorder_point()`

Stessa logica di Order.py:
1. Calcola usable stock
2. Usa `usable_qty` per IP
3. Applica penalty se waste_risk alta

---

## 6. Integrazione Monte Carlo (🔲 TODO)

### `src/forecast.py` - `monte_carlo_forecast()`

**Modifica 1: Perdite per Scadenza nelle Simulazioni**

Aggiungere parametro `expected_waste_rate`:
```python
def monte_carlo_forecast(
    history: List[dict],
    horizon_days: int,
    distribution: str = "empirical",
    n_simulations: int = 1000,
    random_seed: int = 42,
    output_stat: str = "mean",
    output_percentile: int = 80,
    expected_waste_rate: float = 0.0,  # NEW: % perdite attese (0.0-1.0)
) -> List[float]:
```

**Logica**:
```python
# In ogni simulazione, dopo generare forecast grezzo
simulated_forecast = ...  # generate samples

# Applica riduzione per waste atteso
if expected_waste_rate > 0:
    simulated_forecast = [v * (1.0 - expected_waste_rate) for v in simulated_forecast]
```

**expected_waste_rate Calculation** (in OrderWorkflow):
```python
# Se waste_risk_percent > soglia, stima perdite attese
if usable_result.waste_risk_percent > waste_risk_threshold:
    # Esempio: 20% waste risk → 10% perdite attese nel periodo
    expected_waste_rate = usable_result.waste_risk_percent / 100 * 0.5
else:
    expected_waste_rate = 0.0
```

### `src/uncertainty.py` - Aggiungere Waste Variability

Aggiungere `WasteUncertainty`:
```python
class WasteUncertainty:
    """Uncertainty from shelf life waste."""
    
    @staticmethod
    def calculate_waste_variance(
        waste_risk_percent: float,
        base_cv: float,
    ) -> float:
        """
        Increase demand CV based on waste risk.
        
        Higher waste risk → higher uncertainty in usable stock.
        """
        waste_factor = 1.0 + (waste_risk_percent / 100 * 0.3)  # +30% CV per 100% waste risk
        return base_cv * waste_factor
```

Integrare in `calculate_reorder_point()`:
```python
# Dopo calcolo base_cv
if waste_risk_percent > 0:
    adjusted_cv = WasteUncertainty.calculate_waste_variance(waste_risk_percent, base_cv)
else:
    adjusted_cv = base_cv
```

---

## 7. UI Integration (🔲 TODO)

### Settings Tab - Nuova Sezione "Shelf Life Policy"

**Location**: `src/gui/app.py` - `_build_settings_tab()`

Aggiungere dopo "Expiry Alerts":
```python
# ===== SECTION: Shelf Life Policy =====
section_shelf_life = CollapsibleFrame(scrollable_frame, title="📦 Politica Shelf Life Riordino", expanded=False)
section_shelf_life.pack(fill="x", pady=5)

shelf_life_params = [
    {
        "key": "shelf_life_enabled",
        "label": "✅ Abilita Integrazione Shelf Life",
        "description": "Usa shelf life nel calcolo proposte ordine",
        "type": "bool"
    },
    {
        "key": "min_shelf_life_global",
        "label": "Min Shelf Life Residua (giorni)",
        "description": "Shelf life minima per considerare stock vendibile",
        "type": "int",
        "min": 0,
        "max": 90
    },
    {
        "key": "waste_penalty_mode",
        "label": "Modalità Penalty",
        "description": "soft = riduzione qty, hard = blocco ordine",
        "type": "choice",
        "choices": ["soft", "hard"]
    },
    {
        "key": "waste_penalty_factor",
        "label": "Fattore Riduzione (soft)",
        "description": "0.5 = -50% qty ordinata (0.0-1.0)",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "increment": 0.1
    },
    {
        "key": "waste_risk_threshold",
        "label": "Soglia Waste Risk (%)",
        "description": "% stock in scadenza per attivare penalty",
        "type": "int",
        "min": 0,
        "max": 100
    },
    {
        "key": "waste_horizon_days",
        "label": "Orizzonte Waste Risk (giorni)",
        "description": "Finestra per calcolo rischio spreco",
        "type": "int",
        "min": 7,
        "max": 60
    }
]

self._create_param_rows(section_shelf_life.get_content_frame(), shelf_life_params, "shelf_life_policy")
```

**Mapping in `_save_settings()` e `_refresh_settings_tab()`**:
```python
param_map = {
    # ... existing params ...
    "shelf_life_enabled": ("shelf_life_policy", "enabled"),
    "min_shelf_life_global": ("shelf_life_policy", "min_shelf_life_global"),
    "waste_penalty_mode": ("shelf_life_policy", "waste_penalty_mode"),
    "waste_penalty_factor": ("shelf_life_policy", "waste_penalty_factor"),
    "waste_risk_threshold": ("shelf_life_policy", "waste_risk_threshold"),
    "waste_horizon_days": ("shelf_life_policy", "waste_horizon_days"),
}
```

### SKU Management Tab - Nuovi Campi

**Aggiungere in SKU form**:
```python
# Shelf Life Operational Parameters
ttk.Label(shelf_life_frame, text="Min Shelf Life Residua (giorni):").grid(...)
min_shelf_life_var = tk.IntVar(value=sku.min_shelf_life_days if sku else 0)
ttk.Spinbox(shelf_life_frame, from_=0, to=90, textvariable=min_shelf_life_var).grid(...)

ttk.Label(shelf_life_frame, text="Waste Penalty Mode:").grid(...)
waste_mode_var = tk.StringVar(value=sku.waste_penalty_mode if sku else "")
ttk.Combobox(shelf_life_frame, values=["", "soft", "hard"], textvariable=waste_mode_var).grid(...)

ttk.Label(shelf_life_frame, text="Waste Penalty Factor (0-1):").grid(...)
waste_factor_var = tk.DoubleVar(value=sku.waste_penalty_factor if sku else 0.0)
ttk.Spinbox(shelf_life_frame, from_=0.0, to=1.0, increment=0.1, textvariable=waste_factor_var).grid(...)

ttk.Label(shelf_life_frame, text="Waste Risk Threshold (%):").grid(...)
waste_threshold_var = tk.DoubleVar(value=sku.waste_risk_threshold if sku else 0.0)
ttk.Spinbox(shelf_life_frame, from_=0, to=100, increment=5, textvariable=waste_threshold_var).grid(...)
```

### Order Tab - Mostrare Shelf Life Info

**Aggiungere colonne alla Order Proposal table**:
- "Usable Stock" (usable_qty)
- "Waste Risk %" (waste_risk_percent)
- "Penalty" (shelf_life_penalty_message)

**Tooltip dettagli**:
- Total On Hand: X
- Usable: Y (shelf life >= Z giorni)
- Unusable: W (scadenza < Z giorni)
- Expiring Soon: V (rischio spreco)

---

## 8. Testing Strategy (🔲 TODO)

### Test Suite: `tests/test_shelf_life_integration.py`

**Test 1: Usable Stock Calculation**
```python
def test_usable_stock_calculation():
    # Setup: 3 lotti con scadenze diverse
    # Lot 1: scaduto (-5 giorni)
    # Lot 2: scade tra 3 giorni (< min_shelf_life=7)
    # Lot 3: scade tra 10 giorni (usable, ma waste risk)
    # Lot 4: scade tra 30 giorni (usable, safe)
    
    # Assert:
    # unusable_qty = Lot1 + Lot2
    # usable_qty = Lot3 + Lot4
    # expiring_soon = Lot3
    # waste_risk_percent = Lot3 / total * 100
```

**Test 2: Soft Penalty Application**
```python
def test_soft_penalty_reduces_quantity():
    proposed_qty = 100
    waste_risk = 20.0  # > threshold 15.0
    penalty_factor = 0.5
    
    adjusted, msg = apply_shelf_life_penalty(..., mode="soft", ...)
    
    assert adjusted == 50  # -50%
    assert "Reduced by 50%" in msg
```

**Test 3: Hard Penalty Blocks Order**
```python
def test_hard_penalty_blocks_order():
    proposed_qty = 100
    waste_risk = 20.0
    
    adjusted, msg = apply_shelf_life_penalty(..., mode="hard", ...)
    
    assert adjusted == 0
    assert "BLOCKED" in msg
```

**Test 4: Category Override**
```python
def test_category_override_applies():
    # SKU with HIGH variability
    # Global: min_shelf_life=7, penalty=0.5
    # Category HIGH: min_shelf_life=10, penalty=0.7
    
    # Assert: usa parametri category HIGH
```

**Test 5: Integration - Order Proposal with Shelf Life**
```python
def test_order_proposal_with_shelf_life():
    # Setup: SKU con 100 pezzi stock
    #   - 30 scaduti/unusable
    #   - 20 expiring soon (waste risk)
    #   - 50 usable safe
    
    # IP = 50 (usable) + on_order - unfulfilled
    # S = forecast + safety
    # proposed = S - IP
    
    # Se waste_risk > threshold → applica penalty
    
    # Assert:
    # - proposal usa usable_qty corretto
    # - penalty applicato se waste_risk alta
    # - note contengono shelf life info
```

**Test 6: Monte Carlo with Waste Rate**
```python
def test_monte_carlo_with_waste_adjustment():
    # Forecast con expected_waste_rate=0.1 (10%)
    # Assert: output ridotto del 10% rispetto a forecast base
```

---

## 9. Migration Script (🔲 TODO)

### `migrate_shelf_life_columns.py`

Aggiungere nuove colonne a `skus.csv` esistenti:
```python
def migrate_skus_csv():
    # Backup
    # Read existing
    # Add columns: min_shelf_life_days, waste_penalty_mode, waste_penalty_factor, waste_risk_threshold
    # Default values: 0, "", 0.0, 0.0
    # Write back
```

---

## 10. Documentation Updates (🔲 TODO)

### README.md - Nuova Sezione "Shelf Life Management"

```markdown
## Shelf Life & Waste Management

Il sistema integra la shelf life nei calcoli di riordino:

### Concetti Chiave

- **Usable Stock**: Stock con shelf life residua >= `min_shelf_life_days`
- **Waste Risk**: % di stock che scade entro `waste_horizon_days`
- **Soft Penalty**: Riduzione quantità ordinata se waste risk alta
- **Hard Penalty**: Blocco ordine se waste risk critica

### Parametri Configurabili

1. **Globali** (Settings → Shelf Life Policy)
   - Min Shelf Life Residua
   - Modalità Penalty (soft/hard)
   - Fattore Riduzione
   - Soglia Waste Risk

2. **Per Categoria** (category_overrides in settings.json)
   - Override automatico per STABLE/LOW/HIGH/SEASONAL

3. **Per SKU** (SKU Management → Shelf Life tab)
   - Override specifici per singolo SKU

### Esempi d'Uso

**Prodotto Fresco (HIGH variability)**:
- min_shelf_life: 10 giorni
- penalty_mode: soft
- penalty_factor: 0.7 (-70% qty se waste risk alta)
- waste_risk_threshold: 10%

**Prodotto Secco (STABLE)**:
- min_shelf_life: 7 giorni
- penalty_mode: soft
- penalty_factor: 0.3 (-30%)
- waste_risk_threshold: 20%
```

---

## 11. Rollout Plan

### Phase 1: Foundation (✅ DONE)
- [x] SKU model extension
- [x] CSV schema update
- [x] Settings structure
- [x] Core calculations (ShelfLifeCalculator)

### Phase 2: Workflow Integration (⏳ IN PROGRESS)
- [ ] Order.py - generate_proposal()
- [ ] Replenishment policy integration
- [ ] OrderProposal model extension

### Phase 3: Monte Carlo Enhancement
- [ ] Forecast.py - waste rate adjustment
- [ ] Uncertainty.py - waste variability

### Phase 4: UI
- [ ] Settings tab - shelf life section
- [ ] SKU management - new fields
- [ ] Order tab - display shelf life info

### Phase 5: Testing & Validation
- [ ] Unit tests (shelf life calculator)
- [ ] Integration tests (workflow)
- [ ] End-to-end scenarios

### Phase 6: Documentation & Migration
- [ ] README update
- [ ] Migration script
- [ ] User guide

---

## 12. Risk Mitigation

### Backward Compatibility
- Nuovi campi SKU hanno default sicuri (0, "", 0.0)
- CSV layer con backward-compatibility (row.get() con defaults)
- Settings: shelf_life_enabled = false per disabilitare completamente

### Data Integrity
- Validazione input (min_shelf_life <= shelf_life_days)
- Migration script con backup automatico
- Rollback: rimuovere colonne aggiunte, settings section

### Performance
- `calculate_usable_stock()` è O(n) sui lotti (tipicamente < 50 per SKU)
- Cache results se chiamato più volte nella stessa proposta
- Lazy loading: calcola solo se shelf_life_enabled=true

---

## 13. Success Metrics

1. **Funzionalità**:
   - Usable stock calcolato correttamente per 100% SKU con scadenza
   - Penalty applicato solo quando waste_risk > threshold
   - Category override funzionanti

2. **Performance**:
   - Tempo calcolo proposta < 200ms (con shelf life)
   - Nessun impatto su SKU senza scadenza

3. **Usabilità**:
   - UI intuitiva per configurazione
   - Messaggi chiari su penalty applicati
   - Documentazione completa

---

## Appendice A: Esempio Calcolo Completo

**Scenario**:
- SKU: "Yogurt 125g"
- Categoria: HIGH variability
- shelf_life_days: 21
- min_shelf_life_days: 10 (category override)
- waste_penalty_factor: 0.7 (category override)
- waste_risk_threshold: 10% (category override)
- waste_horizon_days: 14 (global)

**Lotti Attuali**:
| Lot ID | Expiry Date | Days Left | Qty |
|--------|-------------|-----------|-----|
| LOT001 | 2026-02-08  | -2        | 20  |
| LOT002 | 2026-02-15  | 5         | 30  |
| LOT003 | 2026-02-20  | 10        | 40  |
| LOT004 | 2026-03-01  | 19        | 50  |
| LOT005 | 2026-03-15  | 33        | 60  |

**Step 1: Calcola Usable Stock**
```
Total on_hand: 200
Unusable (days < 10):
  - LOT001 (scaduto): 20
  - LOT002 (days=5 < 10): 30
  = 50

Usable (days >= 10):
  - LOT003 (days=10): 40 ← expiring soon (10 <= 14)
  - LOT004 (days=19): 50 ← expiring soon (19 <= 14)? No, 19 > 14
  - LOT005 (days=33): 60
  = 150

Expiring soon (days <= 14):
  - LOT003: 40
  = 40

Waste risk % = 40 / 200 * 100 = 20%
```

**Step 2: Calcola IP**
```
IP = usable_qty + on_order - unfulfilled
   = 150 + 0 - 0
   = 150
```

**Step 3: Calcola Proposta**
```
forecast_qty = daily_sales_avg * (lead_time + review_period)
             = 5 * (7 + 7) = 70
safety_stock = 20 (HIGH variability adjustment)
S = 70 + 20 = 90

proposed_raw = S - IP = 90 - 150 = -60 → 0 (no order needed)
```

**Caso Alternativo: Se IP fosse 50**
```
proposed_raw = 90 - 50 = 40
proposed (after rounding) = 40

Waste risk = 20% > threshold 10% → APPLICA PENALTY

adjusted_qty = 40 * (1 - 0.7) = 12
penalty_msg = "⚠️ Reduced by 70% (waste risk 20.0%)"
```

**Output OrderProposal**:
```python
OrderProposal(
    sku="Yogurt 125g",
    proposed_qty=12,  # ← ridotto da 40
    usable_stock=150,
    unusable_stock=50,
    waste_risk_percent=20.0,
    shelf_life_penalty_applied=True,
    shelf_life_penalty_message="⚠️ Reduced by 70% (waste risk 20.0%)",
    notes="S=90 (forecast=70+safety=20), IP=50 | ⚠️ Reduced by 70% (waste risk 20.0%)"
)
```

---

**Fine del Piano**

Questo documento serve come blueprint completo per l'integrazione. I prossimi step sono implementare Phase 2 (workflow integration) e Phase 4 (UI).
