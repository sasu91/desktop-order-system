# CSL-Based Replenishment Policy - Implementation Summary

**Status**: ✅ COMPLETE  
**Date**: February 2026  
**Test Coverage**: 33/33 tests passing (100%)

## Overview

Implementazione completa di una **policy di riordino basata su Cycle Service Level (CSL)** con supporto per:
- Calcolo del punto di riordino usando la formula **S = μ_P + z(α) × σ_P**
- Applicazione deterministica di vincoli operativi (pack size, MOQ, capacità)
- Calcolo della **Inventory Position** con tracciamento del pipeline
- Breakdown completo di tutte le fasi per trasparenza e debug

---

## Formula Matematica

### Reorder Point (S)
```
S = μ_P + z(α) × σ_P

dove:
  μ_P  = Domanda prevista sul periodo di protezione P
  z(α) = Z-score per il livello di servizio α (es. z(0.95) = 1.645)
  σ_P  = Incertezza sulla domanda nel periodo P
  P    = Periodo di protezione (giorni tra consegne consecutive)
```

### Order Quantity (Q)
```
Q_raw = max(0, S - IP)

dove:
  IP = On-Hand + On-Order (pipeline)
```

### Constraint Application (deterministica)
```
1. Pack Size:  Q₁ = ceil(Q_raw / pack_size) × pack_size
2. MOQ:        Q₂ = Q₁ if Q₁ ≥ moq else 0
3. Capacity:   Q_final = min(Q₂, max_stock - IP)
```

---

## Componenti del Modulo

### 1. `src/replenishment_policy.py`

#### Dataclass: `OrderConstraints`
```python
@dataclass
class OrderConstraints:
    pack_size: int = 1    # Multipli di ordinazione
    moq: int = 0          # Minimum Order Quantity
    max_stock: int|None = None  # Capacità massima
```

#### Funzione principale: `compute_order()`
```python
def compute_order(
    sku: str,
    order_date: date,
    lane: Lane,
    alpha: float,              # CSL target (0-1)
    on_hand: int,
    pipeline: List[Dict],      # [{"receipt_date": date, "qty": int}]
    constraints: OrderConstraints,
    history: List[Dict]        # [{"date": date, "qty_sold": float}]
) -> dict:
    """
    Calcola la quantità da ordinare con breakdown completo.
    
    Returns:
        dict con 20+ campi tra cui:
        - order_final: Quantità finale da ordinare
        - reorder_point: Punto di riordino (S)
        - inventory_position: IP corrente
        - forecast_demand: μ_P
        - sigma_daily, sigma_horizon: σ_day, σ_P
        - z_score: z(α)
        - order_raw, order_after_pack, order_after_moq: Step intermedi
        - constraints_applied: Lista dei vincoli attivati
    """
```

#### Batch Processing: `compute_order_batch()`
```python
def compute_order_batch(
    skus: List[str],
    order_date: date,
    lane: Lane,
    alpha: float,
    inventory_data: Dict[str, Dict],  # {"SKU": {"on_hand": int, "pipeline": [...]}}
    constraints_map: Dict[str, OrderConstraints],
    history_map: Dict[str, List[Dict]]
) -> Dict[str, dict]:
    """
    Calcola ordini per più SKU in parallelo.
    
    Returns:
        {"SKU": result_dict, ...}
    """
```

---

## Test Coverage (33 test cases)

### 1. **Test CSL → Z-Score** (2 test)
- `test_z_score_lookup`: Verifica valori corretti (0.90→1.282, 0.95→1.645, 0.99→2.326)
- `test_z_score_interpolation`: CSL intermedi interpolati linearmente

### 2. **Test Pack Size** (4 test)
- `test_pack_size_rounding_up`: 7 → 10 (pack=10)
- `test_pack_size_exact_multiple`: 20 → 20 (nessun cambio)
- `test_pack_size_zero_order`: 0 → 0 (nessun arrotondamento)
- `test_pack_size_one`: pack=1 → nessun arrotondamento

### 3. **Test MOQ** (3 test)
- `test_moq_below_threshold`: order < moq → 0
- `test_moq_above_threshold`: order ≥ moq → invariato
- `test_moq_exact`: order = moq → invariato

### 4. **Test Capacity (Cap)** (3 test)
- `test_cap_order_reduced`: Order ridotto per rispettare max_stock
- `test_cap_no_constraint`: max_stock=None → nessun limite
- `test_cap_zero_order_if_at_max`: IP = max_stock → order=0

### 5. **Test Inventory Position** (3 test)
- `test_on_hand_contribution`: on_hand incluso in IP
- `test_pipeline_contribution`: pipeline incluso in IP
- `test_pipeline_filtered_by_date`: Solo ordini con receipt_date < order_date + P

### 6. **Test Monotonicity CSL** (2 test) ⚠️ **CRITICO**
- `test_alpha_increase_monotonic`: α↑ → order↑ (o uguale)
- `test_alpha_sequence_monotonic`: Sequenza [0.80, 0.85, 0.90, 0.95, 0.98] → ordini non decrescenti

### 7. **Test Compliance Pack Size** (2 test)
- `test_pack_size_multiple_compliance`: order_final % pack_size == 0
- `test_all_pack_sizes`: Verifica per pack_size ∈ {1, 5, 10, 25, 50}

### 8. **Test Compliance MOQ** (1 test)
- `test_moq_binary_decision`: order < moq → 0, order ≥ moq → invariato

### 9. **Test Compliance Capacity** (2 test)
- `test_cap_never_exceeded`: IP + order ≤ max_stock sempre
- `test_cap_respected_with_pipeline`: Cap rispettato anche con pipeline

### 10. **Test Inventory Position Impact** (2 test) ⚠️ **CRITICO**
- `test_on_hand_increase_reduces_order`: on_hand↑ → order↓ (o uguale)
- `test_pipeline_increase_reduces_order`: pipeline↑ → order↓ (o uguale)

### 11. **Test Breakdown Completeness** (1 test)
- `test_all_fields_present`: Verifica presenza di 20+ campi nel result dict

### 12. **Test Determinism** (1 test)
- `test_same_input_same_output`: Stesso input → stesso output (3 run)

### 13. **Test Batch Computation** (2 test)
- `test_batch_multiple_skus`: Batch di 3 SKU con parametri diversi
- `test_batch_empty`: Lista vuota → dict vuoto

### 14. **Test Edge Cases** (4 test)
- `test_zero_demand_history`: History vuoto → forecast=0, order=0
- `test_negative_on_hand`: on_hand < 0 → IP negativo, order aumentato
- `test_invalid_alpha`: α ∉ [0,1] → ValueError
- `test_past_pipeline`: Pipeline con date passate → escluso da IP

---

## Requisiti Critici Validati

### ✅ Monotonicity Requirements
1. **CSL Monotonicity**: α↑ → order↑ (o =)
   - Test: `test_alpha_increase_monotonic`, `test_alpha_sequence_monotonic`
   - Status: **PASSED** (α=0.95 order ≥ α=0.90 order)

2. **Inventory Position Impact**: IP↑ → order↓ (o =)
   - Test: `test_on_hand_increase_reduces_order`, `test_pipeline_increase_reduces_order`
   - Status: **PASSED** (on_hand 10→100 riduce order)

### ✅ Constraint Compliance
1. **Pack Size**: order_final % pack_size == 0
   - Test: `test_pack_size_multiple_compliance`
   - Status: **PASSED** (tutti i pack size testati)

2. **MOQ**: order < moq → 0
   - Test: `test_moq_binary_decision`
   - Status: **PASSED** (soglia MOQ rispettata)

3. **Capacity**: IP + order ≤ max_stock
   - Test: `test_cap_never_exceeded`, `test_cap_respected_with_pipeline`
   - Status: **PASSED** (cap mai violato)

### ✅ Determinism
- Test: `test_same_input_same_output`
- Status: **PASSED** (3 run identici)

---

## Integrazione con Altri Moduli

### Dipendenze
```python
from src.domain.calendar import Lane, protection_window  # Calcolo P
from src.forecast import estimate_demand               # Calcolo μ_P
from src.uncertainty import estimate_demand_uncertainty  # Calcolo σ_P
```

### Data Flow
```
Input:
  ├─ history → forecast.estimate_demand() → μ_P
  ├─ history → uncertainty.estimate_demand_uncertainty() → σ_day, σ_P
  ├─ order_date + lane → calendar.protection_window() → P
  ├─ on_hand + pipeline → _calculate_inventory_position() → IP
  └─ constraints → _apply_pack_size/moq/cap() → Q_final

Output:
  └─ result dict (20+ fields) con breakdown completo
```

---

## Esempi d'Uso

### Esempio 1: Ordine Base
```python
from datetime import date
from src.replenishment_policy import compute_order, OrderConstraints
from src.domain.calendar import Lane

history = [
    {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
    for i in range(90)
]

result = compute_order(
    sku="WIDGET-001",
    order_date=date(2024, 4, 1),
    lane=Lane.STANDARD,
    alpha=0.95,  # 95% service level
    on_hand=30,
    pipeline=[],
    constraints=OrderConstraints(pack_size=10, moq=20, max_stock=500),
    history=history
)

print(f"Order: {result['order_final']} units")
print(f"Reorder Point: {result['reorder_point']:.1f} units")
print(f"Inventory Position: {result['inventory_position']} units")
```

### Esempio 2: Batch Processing
```python
inventory_data = {
    "SKU-A": {"on_hand": 100, "pipeline": []},
    "SKU-B": {"on_hand": 50, "pipeline": [{"receipt_date": date(2024, 4, 5), "qty": 50}]},
}

constraints_map = {
    "SKU-A": OrderConstraints(pack_size=10, moq=20),
    "SKU-B": OrderConstraints(pack_size=5, moq=10),
}

results = compute_order_batch(
    skus=["SKU-A", "SKU-B"],
    order_date=date(2024, 4, 1),
    lane=Lane.STANDARD,
    alpha=0.95,
    inventory_data=inventory_data,
    constraints_map=constraints_map,
    history_map=history_map
)

for sku, result in results.items():
    print(f"{sku}: Order {result['order_final']} units")
```

### Esempio 3: Analisi Sensibilità CSL
```python
csl_levels = [0.80, 0.90, 0.95, 0.98, 0.99]

for alpha in csl_levels:
    result = compute_order(sku, order_date, Lane.STANDARD, alpha, ...)
    print(f"CSL {alpha*100:.0f}%: Order {result['order_final']} units")
```

---

## File di Esempio Disponibili

1. **`examples/replenishment_policy_usage.py`**
   - 6 esempi completi
   - Basic order, volatile demand, pipeline, CSL comparison, constraints, batch

2. **`examples/realistic_scenario.py`**
   - 5 scenari realistici
   - Low stock, strong pipeline, volatile demand, capacity limits, CSL sensitivity

3. **`examples/weekly_planning.py`**
   - Simulazione sessione settimanale di ordinazione
   - Portfolio di 5 SKU con caratteristiche diverse
   - Output: summary table, insights, raccomandazioni

---

## Performance

- **Test Suite**: 33 test in 0.88s (26 ms/test)
- **Batch Processing**: 5 SKU in <0.1s
- **Nessun I/O**: Calcolo deterministico in-memory

---

## Next Steps (Opzionali)

### Possibili Estensioni
1. **Multi-Period Optimization**: Ottimizzazione congiunta di più periodi
2. **Portfolio Safety Stock**: Aggregazione dell'incertezza per risparmio
3. **Autocorrelation Handling**: σ_P con correzione per autocorrelazione
4. **Dynamic CSL**: α variabile per SKU (ABC classification)
5. **GUI Integration**: Tab "Order Proposal" in Tkinter app

### Integrazione con Workflow Esistenti
- **OrderWorkflow**: Usare `compute_order()` per generare proposte automatiche
- **Daily Close**: Ricalcolo periodico degli ordini necessari
- **Exception Handling**: Override manuale delle quantità proposte

---

## Changelog

### v1.0.0 (2026-02-XX)
- ✅ Implementazione completa di `compute_order()`
- ✅ Formula S = μ_P + z(α) × σ_P
- ✅ Constraint application (pack/MOQ/cap)
- ✅ Inventory position con pipeline
- ✅ Batch processing
- ✅ 33/33 test passing (100%)
- ✅ Monotonicity requirements validated
- ✅ Esempi d'uso completi

---

**Maintainer**: Desktop Order System Team  
**License**: Internal Use  
**Documentation**: This file + inline docstrings + test suite
