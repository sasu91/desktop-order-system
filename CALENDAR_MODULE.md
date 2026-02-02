# Logistics Calendar Module

## Overview

Il modulo calendario logistico (`src/domain/calendar.py`) gestisce la pianificazione di ordini e consegne con regole specifiche per giorni lavorativi, weekend e "lane" speciali del venerdì.

## Features

✅ **Ordini**: Lunedì-Venerdì  
✅ **Consegne**: Lunedì-Sabato  
✅ **Friday Dual Lanes**: Sabato (express) o Lunedì (standard)  
✅ **Protection Period (P)**: Calcolo automatico del periodo di copertura  
✅ **Configurabile**: Via `CalendarConfig` (lead time, holidays, etc.)  
✅ **Testato**: 39 test unitari, tutti passing  

## Quick Start

```python
from datetime import date
from src.domain.calendar import next_receipt_date, protection_window, Lane

# Ordine mercoledì -> consegna giovedì
wed = date(2026, 2, 4)
receipt = next_receipt_date(wed, Lane.STANDARD)  # 2026-02-05 (Thursday)

# Periodo di protezione (P)
r1, r2, P = protection_window(wed, Lane.STANDARD)
# r1 = 2026-02-05 (prima consegna)
# r2 = 2026-02-06 (prossima consegna)
# P = 1 giorno

# Venerdì: due lane disponibili
fri = date(2026, 2, 6)
sat_receipt = next_receipt_date(fri, Lane.SATURDAY)  # 2026-02-07 (Saturday)
mon_receipt = next_receipt_date(fri, Lane.MONDAY)    # 2026-02-09 (Monday)

# Confronto periodi di protezione
_, _, P_sat = protection_window(fri, Lane.SATURDAY)  # P = 3 giorni (copre weekend)
_, _, P_mon = protection_window(fri, Lane.MONDAY)    # P = 1 giorno
```

## Core Concepts

### Protection Period (P)

Il **periodo di protezione** è il tempo tra:
- **r1**: Prima consegna possibile (da questo ordine)
- **r2**: Prossima consegna possibile (da ordine successivo)

**P = (r2 - r1) in giorni**

Questo definisce quanto stock deve coprire l'ordine prima che arrivi il prossimo rifornimento.

### Friday Lanes

Il venerdì offre due opzioni:

| Lane | Consegna | P (giorni) | Use Case |
|------|----------|------------|----------|
| SATURDAY | Sabato | 3 | Stock critico, urgenza alta |
| MONDAY | Lunedì | 1 | Stock standard |

**Nota**: La lane SATURDAY ha P più lungo perché copre Sabato + Domenica + Lunedì.

## API Reference

### Funzioni Principali

#### `next_receipt_date(order_date, lane) -> date`
Calcola la data di consegna per un ordine.

**Args**:
- `order_date` (date): Data ordine (deve essere lun-ven)
- `lane` (Lane): STANDARD, SATURDAY, o MONDAY

**Returns**: Data di consegna

#### `protection_window(order_date, lane) -> (r1, r2, P_days)`
Calcola il periodo di protezione.

**Args**:
- `order_date` (date): Data ordine
- `lane` (Lane): Tipo di lane

**Returns**: Tuple (r1: date, r2: date, P_days: int)

#### `get_friday_lanes(friday) -> ((r1_sat, r2_sat, P_sat), (r1_mon, r2_mon, P_mon))`
Calcola entrambe le lane del venerdì.

### Lane Types

```python
class Lane(Enum):
    STANDARD = "STANDARD"      # Lun-Gio ordini, consegna standard
    SATURDAY = "SATURDAY"      # Ven -> Sab (express)
    MONDAY = "MONDAY"          # Ven -> Lun (standard)
```

### Configuration

```python
from src.domain.calendar import CalendarConfig

# Configurazione custom
config = CalendarConfig(
    order_days={0, 1, 2, 3, 4},     # Lun-Ven (0=Mon, 4=Fri)
    delivery_days={0, 1, 2, 3, 4, 5},  # Lun-Sab
    lead_time_days=1,                # Lead time standard
    saturday_lane_lead_time=1,       # Lead time per Ven->Sab
    holidays={date(2026, 12, 25)}    # Giorni festivi
)

# Usa config custom
receipt = next_receipt_date(order_date, Lane.STANDARD, config)
```

## Integration with OrderWorkflow

### Calendar-Aware Order Proposal

```python
from src.domain.calendar import protection_window, Lane

def generate_order_proposal(sku, order_date, lane=Lane.STANDARD):
    # Calcola periodo di protezione
    r1, r2, P = protection_window(order_date, lane)
    
    # Forecast basato su P (non su lead_time fisso)
    forecast_qty = daily_sales_avg * P
    target_S = forecast_qty + safety_stock
    
    # Inventory position
    inventory_position = on_hand + on_order - unfulfilled_qty
    
    # Proposed qty
    proposed_qty = max(0, target_S - inventory_position)
    
    return OrderProposal(
        sku=sku,
        proposed_qty=proposed_qty,
        receipt_date=r1,
        protection_period_days=P,
        forecast_qty=forecast_qty,
        # ... altri campi
    )
```

### Calendar-Aware Reorder Point

```python
from src.domain.calendar import calculate_protection_period_days, Lane

def calculate_reorder_point(order_date, lane, daily_sales_avg, safety_stock):
    P = calculate_protection_period_days(order_date, lane)
    ROP = (P * daily_sales_avg) + safety_stock
    return ROP

# Esempio
wed = date(2026, 2, 4)
ROP_wed = calculate_reorder_point(wed, Lane.STANDARD, 10.0, 20)  # ROP = 30

fri = date(2026, 2, 6)
ROP_sat = calculate_reorder_point(fri, Lane.SATURDAY, 10.0, 20)  # ROP = 50
ROP_mon = calculate_reorder_point(fri, Lane.MONDAY, 10.0, 20)    # ROP = 30
```

## Test Coverage

Esegui test:
```bash
pytest tests/test_calendar.py -v
```

**39 test cases** verificano:
- ✅ Giorni ordine/consegna validi
- ✅ Salto weekend/domenica
- ✅ Friday dual lanes
- ✅ Calcolo P corretto per ogni scenario
- ✅ Gestione configurazioni custom
- ✅ Edge cases e validazioni

## Examples

Esegui esempi completi:
```bash
python examples/calendar_usage.py
```

Mostra:
- Uso base del calendario
- Calcolo periodi di protezione
- Confronto Friday lanes
- Integrazione con OrderWorkflow
- Reorder point calendar-aware

## Key Insights

### Protection Period Comparison

| Order Day | Lane | r1 | r2 | P |
|-----------|------|----|----|---|
| Lun | STANDARD | Mar | Mer | 1 |
| Mer | STANDARD | Gio | Ven | 1 |
| Ven | SATURDAY | Sab | Mar | 3 |
| Ven | MONDAY | Lun | Mar | 1 |

**Osservazioni**:
1. **P_sat > P_mon**: Saturday lane richiede più stock (copre weekend)
2. **Friday decision**: Scegliere lane in base a urgenza e disponibilità stock
3. **Calendar-driven**: Ogni giorno può avere P diverso → no lead_time fisso

## Future Enhancements

- [ ] Supporto holidays dinamici (da CSV o API)
- [ ] Multi-week protection period (per ordini bisettimanali)
- [ ] Lane recommendation engine (suggerimento automatico Sat vs Mon)
- [ ] Integration with GUI (lane selector in order tab)

## Files

- **Module**: `src/domain/calendar.py` (350 lines)
- **Tests**: `tests/test_calendar.py` (450 lines, 39 tests)
- **Examples**: `examples/calendar_usage.py` (200 lines)
- **Documentation**: `CALENDAR_MODULE.md` (questo file)

---

**Status**: ✅ Implemented and tested  
**Version**: 1.0  
**Last Updated**: February 2, 2026
