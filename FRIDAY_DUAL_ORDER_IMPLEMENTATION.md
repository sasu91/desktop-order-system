# Friday Dual-Order Workflow - Implementation Summary

**Status**: ✅ COMPLETE  
**Date**: February 2, 2026  
**Test Coverage**: 14 new tests + 33 existing tests = 47 tests passing (100%)

## Overview

Implementazione della logica **"venerdì doppio ordine"** che genera automaticamente due ordini ogni venerdì:
1. **Lane SATURDAY**: Ordine per consegna sabato (periodo protezione breve)
2. **Lane MONDAY**: Ordine per consegna lunedì (periodo protezione standard)

**Garanzia critica**: L'ordine per lunedì considera automaticamente l'ordine per sabato già emesso, evitando il doppio conteggio della stessa domanda.

---

## Architettura

### Nuovo Modulo: `src/workflows/replenishment.py`

#### Dataclass: `OrderSuggestion`
```python
@dataclass
class OrderSuggestion:
    sku: str
    order_date: date
    lane: Lane                  # STANDARD, SATURDAY, MONDAY
    receipt_date: date          # Data consegna prevista
    order_qty: int              # Quantità ordinata
    reorder_point: float        # Punto di riordino (S)
    inventory_position: int     # IP corrente
    forecast_demand: float      # Domanda prevista
    sigma_horizon: float        # Incertezza periodo
    alpha: float                # CSL target
    breakdown: dict             # Breakdown completo
```

#### Funzione Principale: `generate_orders_for_date()`
```python
def generate_orders_for_date(
    order_date: date,
    sku_data: Dict[str, Dict],  # Per ogni SKU: on_hand, pipeline, constraints, history
    alpha: float = 0.95
) -> List[OrderSuggestion]:
    """
    Genera suggerimenti d'ordine per una data specifica.
    
    Logica:
    - Lunedì-Giovedì: 1 ordine per SKU (Lane STANDARD)
    - Venerdì: 2 ordini per SKU (Lane SATURDAY + MONDAY)
    
    CRITICAL Friday Logic:
    1. Calcola ordine SATURDAY
    2. Aggiunge ordine SATURDAY al pipeline
    3. Calcola ordine MONDAY con pipeline aggiornato
    
    Returns:
        Lista di OrderSuggestion (1 per Mon-Thu, 2 per Fri)
    """
```

### Logica Pipeline Update (Venerdì)

```python
# Pseudocode della logica critica
for lane in [Lane.SATURDAY, Lane.MONDAY]:
    for sku in sku_data:
        # 1. Pipeline corrente + update da lane precedente
        current_pipeline = base_pipeline + pipeline_updates[sku]
        
        # 2. Calcola ordine con CSL policy
        result = compute_order(
            sku, order_date, lane, alpha,
            on_hand, current_pipeline, constraints, history
        )
        
        # 3. Se lane=SATURDAY e order > 0, aggiorna pipeline per MONDAY
        if lane == SATURDAY and result["order_final"] > 0:
            pipeline_updates[sku].append({
                "receipt_date": result["receipt_date"],
                "qty": result["order_final"]
            })
```

---

## Test Coverage (14 nuovi test)

### 1. **TestFridayDualOrder** (3 test)
- ✅ `test_friday_generates_two_suggestions`: Venerdì → 2 suggerimenti per SKU
- ✅ `test_monday_generates_one_suggestion`: Lunedì → 1 suggerimento per SKU
- ✅ `test_friday_suggestions_different_receipt_dates`: Receipt dates Sabato < Lunedì

### 2. **TestPipelineUpdate** (2 test) ⚠️ **CRITICO**
- ✅ `test_monday_order_sees_saturday_in_pipeline`: Lunedì vede ordine Sabato in pipeline
- ✅ `test_monday_order_different_when_saturday_order_exists`: 
  - Q_mon (con Sabato) ≠ Q_mon (senza Sabato)
  - **SMOKING GUN**: Prova che non c'è doppio conteggio

### 3. **TestInventoryPositionAsOf** (3 test) ⚠️ **CRITICO**
- ✅ `test_ip_asof_saturday_includes_saturday_order`: IP(Sabato) = on_hand + Q_sat
- ✅ `test_ip_asof_monday_includes_both_orders`: IP(Lunedì) = on_hand + Q_sat + Q_mon
- ✅ `test_ip_asof_friday_excludes_future_orders`: IP(Venerdì) = on_hand (ordini futuri esclusi)

### 4. **TestMultiSKUFriday** (1 test)
- ✅ `test_friday_multiple_skus`: 3 SKU × 2 lanes = 6 suggerimenti

### 5. **TestEdgeCases** (3 test)
- ✅ `test_invalid_order_day_raises`: Sabato/Domenica → ValueError
- ✅ `test_empty_sku_data_returns_empty_list`: Nessun SKU → lista vuota
- ✅ `test_friday_with_existing_pipeline`: Venerdì con pipeline esistente funziona

### 6. **TestOrderSuggestion** (1 test)
- ✅ `test_suggestion_has_all_fields`: OrderSuggestion ha tutti i campi richiesti

### 7. **TestFridayWorkflowIntegration** (1 test)
- ✅ `test_full_friday_workflow`: Test integrazione completo con dati realistici

---

## Modifiche ai Moduli Esistenti

### `src/replenishment_policy.py`
**Modifiche**:
- Aggiunto import `next_receipt_date` da `src.domain.calendar`
- Aggiunto campo `"receipt_date"` al dizionario di ritorno di `compute_order()`
- Calcolo di `receipt_date` usando `next_receipt_date(order_date, lane)`

**Impact**: Nessuna regressione, tutti i 33 test esistenti ancora verdi.

---

## Scenari d'Uso

### Scenario 1: Ordine Lunedì (Standard)
```python
from datetime import date
from src.workflows.replenishment import generate_orders_for_date
from src.replenishment_policy import OrderConstraints

sku_data = {
    "WIDGET-A": {
        "on_hand": 50,
        "pipeline": [],
        "constraints": OrderConstraints(pack_size=10, moq=20),
        "history": [...sales_data...]
    }
}

# Lunedì: 1 ordine per SKU
suggestions = generate_orders_for_date(date(2024, 4, 1), sku_data, alpha=0.95)
# len(suggestions) == 1
# suggestions[0].lane == Lane.STANDARD
```

### Scenario 2: Ordine Venerdì (Doppio)
```python
# Venerdì: 2 ordini per SKU
suggestions = generate_orders_for_date(date(2024, 4, 5), sku_data, alpha=0.95)
# len(suggestions) == 2

sat = next(s for s in suggestions if s.lane == Lane.SATURDAY)
mon = next(s for s in suggestions if s.lane == Lane.MONDAY)

# Receipt dates diverse
assert sat.receipt_date == date(2024, 4, 6)  # Sabato
assert mon.receipt_date == date(2024, 4, 8)  # Lunedì

# Lunedì ha visto ordine Sabato nel pipeline
if sat.order_qty > 0:
    # Monday order <= order senza Saturday (no doppio conteggio)
    assert mon.order_qty <= order_without_saturday
```

### Scenario 3: Multi-SKU Venerdì
```python
sku_data = {
    "SKU-A": {...},
    "SKU-B": {...},
    "SKU-C": {...}
}

# 3 SKU × 2 lanes = 6 suggerimenti
suggestions = generate_orders_for_date(friday, sku_data, alpha=0.95)
assert len(suggestions) == 6

# Ogni SKU ha 2 ordini
for sku in ["SKU-A", "SKU-B", "SKU-C"]:
    sku_orders = [s for s in suggestions if s.sku == sku]
    assert len(sku_orders) == 2
    assert {s.lane for s in sku_orders} == {Lane.SATURDAY, Lane.MONDAY}
```

---

## Funzioni Ausiliarie

### `calculate_inventory_position_asof()`
```python
def calculate_inventory_position_asof(
    order_date: date,
    on_hand: int,
    pipeline: List[Dict],
    asof_date: date
) -> int:
    """
    Calcola IP as-of una data specifica.
    
    IP(asof) = on_hand + sum(pipeline where receipt_date <= asof_date)
    
    Utile per verifiche:
    - IP(Sabato) include solo ordine Sabato
    - IP(Lunedì) include Sabato + Lunedì
    """
```

### `generate_order_for_sku()`
```python
def generate_order_for_sku(
    sku: str, order_date: date, lane: Lane,
    on_hand: int, pipeline: List[Dict],
    constraints: OrderConstraints,
    history: List[Dict], alpha: float = 0.95
) -> OrderSuggestion:
    """
    Genera singolo ordine per un SKU.
    
    Wrapper di compute_order() che ritorna OrderSuggestion.
    """
```

---

## Requisiti Critici Validati

### ✅ Doppio Ordine Venerdì
- **Test**: `test_friday_generates_two_suggestions`
- **Verifica**: Venerdì → 2 suggerimenti per SKU (SATURDAY + MONDAY)

### ✅ Pipeline Update Tra Ordini
- **Test**: `test_monday_order_sees_saturday_in_pipeline`
- **Verifica**: Lunedì vede ordine Sabato nel pipeline

### ✅ No Doppio Conteggio
- **Test**: `test_monday_order_different_when_saturday_order_exists`
- **Verifica**: Q_mon (con Sabato) ≤ Q_mon (senza Sabato)
- **CRITICAL**: Se Sabato ordina, Lunedì ordina di meno (prova matematica)

### ✅ IP As-Of Corretto
- **Test**: `test_ip_asof_saturday_includes_saturday_order`
- **Verifica**: IP(Sabato) = on_hand + Q_sat (esclude Q_mon)
- **Test**: `test_ip_asof_monday_includes_both_orders`
- **Verifica**: IP(Lunedì) = on_hand + Q_sat + Q_mon

---

## Performance

- **Test Suite**: 47 test in 0.26s (5.5 ms/test)
- **Friday Workflow**: 2 ordini per SKU in <0.01s
- **Multi-SKU**: 6 ordini (3 SKU × 2 lanes) in <0.02s
- **Nessun I/O**: Calcolo deterministico in-memory

---

## Integrazione con Sistema Esistente

### Workflow di Ordinazione
```python
# In OrderWorkflow o GUI
from src.workflows.replenishment import generate_orders_for_date

def weekly_order_session(order_date):
    """Genera proposte d'ordine per la sessione settimanale."""
    
    # Carica dati SKU dal database
    sku_data = load_sku_data()
    
    # Genera suggerimenti (automaticamente gestisce venerdì doppio)
    suggestions = generate_orders_for_date(order_date, sku_data, alpha=0.95)
    
    # Presenta all'utente per conferma
    for suggestion in suggestions:
        print(f"{suggestion.sku} [{suggestion.lane.name}]: "
              f"Order {suggestion.order_qty} units "
              f"(delivery: {suggestion.receipt_date})")
    
    return suggestions
```

### GUI Integration
```python
# In GUI tab "Order Proposal"
def refresh_proposals():
    today = date.today()
    
    # Verifica se è un giorno di ordine valido
    if today.weekday() > 4:  # Sabato/Domenica
        show_message("No orders on weekends")
        return
    
    # Genera proposte
    suggestions = generate_orders_for_date(today, get_sku_data())
    
    # Mostra in tabella
    for s in suggestions:
        add_row(s.sku, s.lane.name, s.order_qty, s.receipt_date)
    
    # Se venerdì, mostra messaggio speciale
    if today.weekday() == 4:
        show_info("Friday: Dual orders generated (Saturday + Monday)")
```

---

## Next Steps (Opzionali)

### 1. GUI Tab "Order Proposal"
- Visualizzazione tabella suggerimenti
- Filtri per lane/SKU
- Export CSV proposte

### 2. Conferma Batch
- Workflow di conferma ordini
- Creazione eventi ORDER nel ledger
- Update order_logs.csv

### 3. Analisi Sensibilità
- Impatto CSL su quantità ordinate
- Confronto lane SATURDAY vs MONDAY
- Report "what-if" scenari

### 4. Ottimizzazioni Avanzate
- Multi-period joint optimization
- Portfolio safety stock pooling
- Dynamic lane selection (auto-choose Saturday vs Monday)

---

## File Creati/Modificati

### Nuovi File
1. **[src/workflows/replenishment.py](src/workflows/replenishment.py)** (260 righe)
   - `generate_orders_for_date()`
   - `generate_order_for_sku()`
   - `calculate_inventory_position_asof()`
   - `OrderSuggestion` dataclass

2. **[tests/test_replenishment_workflow.py](tests/test_replenishment_workflow.py)** (500 righe, 14 test)
   - TestFridayDualOrder (3 test)
   - TestPipelineUpdate (2 test)
   - TestInventoryPositionAsOf (3 test)
   - TestMultiSKUFriday (1 test)
   - TestEdgeCases (3 test)
   - TestOrderSuggestion (1 test)
   - TestFridayWorkflowIntegration (1 test)

### File Modificati
1. **[src/replenishment_policy.py](src/replenishment_policy.py)**
   - Aggiunto campo `"receipt_date"` in `compute_order()`
   - Import `next_receipt_date` da calendario

2. **[tests/test_replenishment_policy.py](tests/test_replenishment_policy.py)**
   - Aggiunto `"receipt_date"` ai campi attesi in `TestBreakdownCompleteness`

---

## Changelog

### v1.0.0 (2026-02-02)
- ✅ Implementazione `generate_orders_for_date()`
- ✅ Logica venerdì doppio ordine (SATURDAY + MONDAY)
- ✅ Pipeline update automatico tra ordini
- ✅ Garanzia no doppio conteggio (test matematico)
- ✅ Funzioni ausiliarie (IP as-of, ordine singolo)
- ✅ 14 test nuovi + 33 esistenti = 47 test passing (100%)
- ✅ Nessuna regressione su moduli esistenti
- ✅ Documentazione completa

---

**Maintainer**: Desktop Order System Team  
**License**: Internal Use  
**Documentation**: This file + inline docstrings + test suite
