# Esempio: Gestione Festività e Ordini Anticipati

## Scenario: Mercoledì Festivo

**Situazione:**
- Martedì 11 febbraio: giorno lavorativo normale
- **Mercoledì 12 febbraio: FESTIVITÀ** (fornitore chiuso)
- Giovedì 13 febbraio: riprende operatività
- Devi ordinare **martedì** con consegna **giovedì**

## Soluzione 1: Override Manuale (Già Disponibile)

```python
# Nella GUI o nel workflow:
from datetime import date

# Oggi è martedì
today = date(2026, 2, 11)
thursday = date(2026, 2, 13)

# Genera proposta con receipt_date manuale
proposal = workflow.generate_proposal(
    sku="SKU123",
    description="Ordine anticipato per festività",
    current_stock=stock,
    daily_sales_avg=10.0,
    sku_obj=sku,
    target_receipt_date=thursday,  # Override manuale
    protection_period_days=2,      # Gio→Sab (prossimo ordine venerdì)
    transactions=transactions,
    sales_records=sales
)

# Il sistema calcola automaticamente:
# - Vendite previste: 2 giorni (mar→gio) × 10 pz/giorno = 20 pz
# - IP proiettato = on_hand - 20 + ricevute_prima_di_giovedì
```

**Risultato:**
- IP tiene conto delle vendite di martedì e mercoledì
- Usable stock verificato al giovedì (lotti che scadono mercoledì sono esclusi)
- Quantità ordinata compensa il gap di 2 giorni

## Soluzione 2: Configurazione Festività (Avanzato)

Per gestire festività in modo **automatico**, configura il calendario:

```python
from datetime import date
from src.domain.calendar import CalendarConfig, next_receipt_date, Lane

# Definisci festività
holidays = {
    date(2026, 2, 12),  # Mercoledì festivo
    date(2026, 4, 25),  # 25 Aprile
    date(2026, 12, 25), # Natale
    # ... altre festività
}

# Crea configurazione custom
custom_config = CalendarConfig(
    order_days={0, 1, 2, 3, 4},  # Lun-Ven (default)
    delivery_days={0, 1, 2, 3, 4, 5},  # Lun-Sab (default)
    lead_time_days=1,
    saturday_lane_lead_time=1,
    holidays=holidays  # ← Festività configurate
)

# Usa la configurazione
from src.domain import calendar
calendar.DEFAULT_CONFIG = custom_config

# Ora next_receipt_date salta automaticamente le festività
tuesday = date(2026, 2, 11)
receipt = next_receipt_date(tuesday, Lane.STANDARD, custom_config)
# → Risultato: date(2026, 2, 13) (giovedì, perché mercoledì è festivo)
```

**Verifica automatica:**
```python
from src.domain.calendar import is_order_day, is_delivery_day

wednesday = date(2026, 2, 12)
print(is_order_day(wednesday, custom_config))    # False
print(is_delivery_day(wednesday, custom_config))  # False
```

## Soluzione 3: Lane Personalizzate (Futuro)

Per scenari ricorrenti (es. sempre ordine martedì per giovedì), puoi estendere l'enum `Lane`:

```python
class Lane(Enum):
    STANDARD = "STANDARD"
    SATURDAY = "SATURDAY"
    MONDAY = "MONDAY"
    TUESDAY_THURSDAY = "TUESDAY_THURSDAY"  # Nuovo: ordine mar, consegna gio
```

E aggiornare `next_receipt_date()`:

```python
elif lane == Lane.TUESDAY_THURSDAY:
    if order_date.weekday() != 1:  # 1 = Tuesday
        raise ValueError("TUESDAY_THURSDAY lane only for Tuesday orders")
    tentative = order_date + timedelta(days=2)  # Mar (festivo) → Gio
    return next_delivery_day(tentative, config)
```

## Gestione in GUI

Nel file [src/gui/app.py](src/gui/app.py#L916-L931), la lane selector può essere estesa:

```python
self.lane_combo = ttk.Combobox(
    controls_frame,
    values=["STANDARD", "SATURDAY", "MONDAY", "TUESDAY_THURSDAY"],
    state="readonly",
    width=15
)
```

## Vantaggi del Sistema Attuale

✅ **Projection automatica**: Funziona per **qualsiasi** coppia (asof_date, target_date)  
✅ **Vendite previste**: Calcola automaticamente `(target - asof).days × daily_sales`  
✅ **Lotti scadenti**: Usa `target_date` per verificare scadenze, non `today`  
✅ **Override manuale**: GUI già permette di impostare receipt_date custom  
✅ **Festività**: `CalendarConfig` supporta `holidays` (basta configurare)  

## Test Scenario

```python
# tests/test_holiday_order.py
def test_tuesday_order_for_thursday_with_wednesday_holiday():
    """
    Scenario: Mercoledì festivo, ordine martedì per giovedì.
    
    Verifica:
    - IP proiettato sottrae vendite di 2 giorni (mar→gio)
    - Lotti che scadono mercoledì sono esclusi
    - Receipt date salta correttamente il mercoledì festivo
    """
    holidays = {date(2026, 2, 12)}  # Mercoledì
    config = CalendarConfig(holidays=holidays)
    
    tuesday = date(2026, 2, 11)
    receipt = next_receipt_date(tuesday, Lane.STANDARD, config)
    
    assert receipt == date(2026, 2, 13), "Deve saltare mercoledì festivo"
    
    # Proiezione IP
    stock = Stock(sku="SKU1", on_hand=100, on_order=0, unfulfilled_qty=0, asof_date=tuesday)
    projected_ip = StockCalculator.projected_inventory_position(
        sku="SKU1",
        target_date=receipt,  # Giovedì
        current_stock=stock,
        transactions=[],
        daily_sales_forecast=10.0
    )
    
    # IP = 100 - (2 giorni × 10 pz) = 80
    assert projected_ip == 80, "Deve sottrarre 2 giorni di vendite (mar+mer)"
```

## Conclusione

**Il sistema è già predisposto**. Puoi usare:
1. **Override manuale** per casi eccezionali (già funzionante)
2. **CalendarConfig.holidays** per automazione (poche righe da aggiungere in main.py)
3. **Lane personalizzate** per pattern ricorrenti (estensione futura)

La logica di `projected_inventory_position()` è **generica** e funziona con qualsiasi combinazione di date, non solo venerdì→sabato/lunedì.
