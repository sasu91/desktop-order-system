# Shelf Life Integration - Implementation Summary

## Stato Attuale: FASE 1 COMPLETATA ✅

### Cosa è stato implementato

#### 1. Modello Dati Esteso (`src/domain/models.py`)

Aggiunto alla classe SKU:
```python
# Shelf life operational parameters (for reorder engine integration)
min_shelf_life_days: int = 0    # Minimum residual shelf life for sale (days, 0 = no constraint)
waste_penalty_mode: str = ""    # "soft", "hard", or "" (use global setting)
waste_penalty_factor: float = 0.0  # Soft penalty multiplier 0.0-1.0 (0 = use global)
waste_risk_threshold: float = 0.0  # Waste risk % threshold for penalty trigger (0-100, 0 = use global)
```

**Validazioni implementate**:
- `min_shelf_life_days >= 0`
- `min_shelf_life_days <= shelf_life_days` (quando shelf_life_days > 0)
- `waste_penalty_mode in ["", "soft", "hard"]`
- `waste_penalty_factor in [0.0, 1.0]`
- `waste_risk_threshold in [0.0, 100.0]`

#### 2. CSV Schema Aggiornato (`src/persistence/csv_layer.py`)

**Schema skus.csv esteso**:
```python
"skus.csv": [
    ..., 
    "shelf_life_days", 
    "min_shelf_life_days",  # ← NEW
    "waste_penalty_mode",   # ← NEW
    "waste_penalty_factor", # ← NEW
    "waste_risk_threshold", # ← NEW
    ...
]
```

**Metodi aggiornati**:
- `read_skus()`: Legge i nuovi campi con backward-compatibility (defaults: 0, "", 0.0)
- `write_sku()`: Scrive i nuovi campi

#### 3. Settings Globali (`data/settings.json`)

**Nuova sezione `shelf_life_policy`**:
```json
{
  "enabled": {"value": true},
  "min_shelf_life_global": {"value": 7},
  "waste_penalty_mode": {"value": "soft"},
  "waste_penalty_factor": {"value": 0.5},
  "waste_risk_threshold": {"value": 15.0},
  "waste_horizon_days": {"value": 14},
  "category_overrides": {
    "value": {
      "STABLE": {"min_shelf_life_days": 7, "waste_penalty_factor": 0.3, "waste_risk_threshold": 20.0},
      "LOW": {"min_shelf_life_days": 5, "waste_penalty_factor": 0.5, "waste_risk_threshold": 15.0},
      "HIGH": {"min_shelf_life_days": 10, "waste_penalty_factor": 0.7, "waste_risk_threshold": 10.0},
      "SEASONAL": {"min_shelf_life_days": 7, "waste_penalty_factor": 0.6, "waste_risk_threshold": 12.0}
    }
  }
}
```

**Parametri configurabili**:
- Globali: min shelf life, modalità penalty, fattore riduzione, soglia waste risk
- **Per categoria**: Override automatici per STABLE/LOW/HIGH/SEASONAL demand variability
- **Per SKU**: Campi individuali nel modello SKU

#### 4. Calcoli Core (`src/domain/ledger.py`)

**Nuove classi/funzioni**:

**`UsableStockResult` (dataclass)**:
```python
@dataclass
class UsableStockResult:
    total_on_hand: int
    usable_qty: int  # Qty with sufficient residual shelf life
    unusable_qty: int  # Qty expiring too soon (< min_shelf_life_days)
    expiring_soon_qty: int  # Qty in waste risk window
    waste_risk_percent: float  # % of stock at risk of waste
```

**`ShelfLifeCalculator` class**:

1. **`calculate_usable_stock()`**:
   - Input: Lista lotti, data check, min shelf life, waste horizon
   - Logica:
     * Expired (days < 0) → unusable
     * Insufficient shelf life (days < min_shelf_life_days) → unusable
     * Expiring soon (days <= waste_horizon_days) → usable BUT at risk
     * Good shelf life (days > waste_horizon_days) → usable safe
   - Output: `UsableStockResult` con breakdown completo

2. **`apply_shelf_life_penalty()`**:
   - Input: qty proposta, waste risk %, threshold, mode, penalty factor
   - Logica:
     * Se waste_risk < threshold → no penalty
     * Se mode="hard" → qty = 0 (blocco ordine)
     * Se mode="soft" → qty ridotta del penalty_factor%
   - Output: (adjusted_qty, reason_message)

### File Modificati

1. ✅ `src/domain/models.py` (linee 48-55, 93-102)
2. ✅ `src/persistence/csv_layer.py` (linee 26-29, 117-121, 250-254)
3. ✅ `data/settings.json` (linee 131-187)
4. ✅ `src/domain/ledger.py` (linee 406-549, nuove classi prima di LotConsumptionManager)

### Test Disponibili

**Test automatici da eseguire**:
```bash
# Test modello SKU
python -c "from src.domain.models import SKU; sku = SKU(sku='TEST', description='Test', min_shelf_life_days=7, waste_penalty_mode='soft', waste_penalty_factor=0.5, waste_risk_threshold=15.0); print('✓ SKU model OK')"

# Test CSV persistence
python -c "from src.persistence.csv_layer import CSVLayer; from src.domain.models import SKU; import tempfile; from pathlib import Path; csv = CSVLayer(Path(tempfile.mkdtemp())); sku = SKU(sku='TEST', description='Test', min_shelf_life_days=7); csv.write_sku(sku); skus = csv.read_skus(); print('✓ CSV persistence OK' if skus[0].min_shelf_life_days == 7 else '✗ FAIL')"

# Test usable stock calculation
python -c "
from src.domain.ledger import ShelfLifeCalculator
from src.domain.models import Lot
from datetime import date, timedelta

today = date.today()
lots = [
    Lot('L1', 'SKU1', today - timedelta(days=5), 10, 'R1', today),  # Expired
    Lot('L2', 'SKU1', today + timedelta(days=3), 20, 'R2', today),  # Too soon
    Lot('L3', 'SKU1', today + timedelta(days=10), 30, 'R3', today), # Expiring soon
    Lot('L4', 'SKU1', today + timedelta(days=30), 40, 'R4', today), # Safe
]

result = ShelfLifeCalculator.calculate_usable_stock(lots, today, min_shelf_life_days=7, waste_horizon_days=14)

print(f'Total: {result.total_on_hand}')
print(f'Usable: {result.usable_qty}')
print(f'Unusable: {result.unusable_qty}')
print(f'Waste Risk: {result.waste_risk_percent:.1f}%')
assert result.total_on_hand == 100
assert result.usable_qty == 70  # L3 + L4
assert result.unusable_qty == 30  # L1 + L2
assert result.expiring_soon_qty == 30  # L3
print('✓ Usable stock calculation OK')
"

# Test penalty application
python -c "
from src.domain.ledger import ShelfLifeCalculator

# Soft penalty
adj1, msg1 = ShelfLifeCalculator.apply_shelf_life_penalty(100, 20.0, 15.0, 'soft', 0.5)
assert adj1 == 50, f'Expected 50, got {adj1}'
assert 'Reduced by 50' in msg1, msg1
print('✓ Soft penalty OK')

# Hard penalty
adj2, msg2 = ShelfLifeCalculator.apply_shelf_life_penalty(100, 20.0, 15.0, 'hard', 0.5)
assert adj2 == 0, f'Expected 0, got {adj2}'
assert 'BLOCKED' in msg2, msg2
print('✓ Hard penalty OK')

# No penalty (below threshold)
adj3, msg3 = ShelfLifeCalculator.apply_shelf_life_penalty(100, 10.0, 15.0, 'soft', 0.5)
assert adj3 == 100, f'Expected 100, got {adj3}'
assert msg3 == '', msg3
print('✓ No penalty OK')
"
```

### Prossimi Step (FASE 2-6)

#### Fase 2: Workflow Integration 🔲
- [ ] Modificare `OrderWorkflow.generate_proposal()` per:
  * Caricare settings shelf life
  * Determinare parametri (SKU > Category > Global)
  * Calcolare usable stock usando `ShelfLifeCalculator`
  * Usare `usable_qty` invece di `on_hand` per IP
  * Applicare penalty al proposed_qty
  * Aggiungere info shelf life a OrderProposal

- [ ] Estendere `OrderProposal` model con campi:
  ```python
  usable_stock: int = 0
  unusable_stock: int = 0
  waste_risk_percent: float = 0.0
  shelf_life_penalty_applied: bool = False
  shelf_life_penalty_message: str = ""
  ```

- [ ] Modificare `src/replenishment_policy.py` e `src/workflows/replenishment.py`

#### Fase 3: Monte Carlo Enhancement 🔲
- [ ] `src/forecast.py`: Aggiungere `expected_waste_rate` parameter
- [ ] `src/uncertainty.py`: Aggiungere `WasteUncertainty` class

#### Fase 4: UI 🔲
- [ ] Settings tab: sezione "Shelf Life Policy"
- [ ] SKU Management: nuovi campi shelf life
- [ ] Order tab: colonne usable stock, waste risk%, penalty

#### Fase 5: Testing 🔲
- [ ] Test suite completa (`tests/test_shelf_life_integration.py`)
- [ ] Integration tests
- [ ] End-to-end scenarios

#### Fase 6: Documentation & Migration 🔲
- [ ] README update
- [ ] Migration script (`migrate_shelf_life_columns.py`)
- [ ] User guide

### Come Procedere

**Per continuare l'implementazione**:

1. **Testare le fondamenta**:
   ```bash
   # Esegui i test inline sopra per verificare che tutto funzioni
   ```

2. **Iniziare Fase 2** (workflow integration):
   ```bash
   # Modificare src/workflows/order.py
   # Seguire il piano dettagliato in SHELF_LIFE_INTEGRATION_PLAN.md sezione 4
   ```

3. **Backup prima di modifiche**:
   ```bash
   cp data/skus.csv data/skus_backup_$(date +%Y%m%d).csv
   ```

4. **Verificare compatibilità**:
   - File esistenti continueranno a funzionare (backward-compatibility)
   - Nuove colonne hanno defaults sicuri (0, "", 0.0)
   - Settings `shelf_life_enabled=true` ma può essere disabilitato

### Documentazione Creata

1. ✅ **SHELF_LIFE_INTEGRATION_PLAN.md**: Piano completo dettagliato (tutte le fasi)
2. ✅ **Questo file**: Summary dello stato attuale

### Domande Risolte

✅ **Soft vs Hard Penalty**: Soft penalty di default, configurabile in settings  
✅ **Livello integrazione**: A livello lotto (FEFO), non solo SKU  
✅ **Categoria prodotto**: Diverse soglie per STABLE/LOW/HIGH/SEASONAL via category_overrides

### Metriche Success

**Fase 1 (attuale)**:
- ✅ Modello dati esteso senza breaking changes
- ✅ CSV backward-compatible
- ✅ Settings strutturati con category overrides
- ✅ Calcoli core implementati e testabili

**Prossime fasi**:
- IP calculation usa usable stock invece di total on_hand
- Penalty applicato solo quando waste_risk > threshold
- UI intuitiva per configurazione
- Performance < 200ms per calcolo proposta

---

**Fine Summary - Pronto per Fase 2 Implementation**
