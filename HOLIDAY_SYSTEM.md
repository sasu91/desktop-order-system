# Sistema Festività e Chiusure

## Panoramica

Il sistema gestisce festività nazionali italiane e chiusure personalizzate (negozio/magazzino/fornitore) con **effetti granulari**:
- **no_order**: blocca ordini (fornitore chiuso)
- **no_receipt**: blocca ricezione (magazzino chiuso per inventario)
- **both**: blocca ordini E ricezione (festività nazionale)

## Architettura

### 1. Modulo Core: `src/domain/holidays.py`

**HolidayCalendar**: Gestisce festività e chiusure con effects
- `is_holiday(date, scope, effect)`: verifica se date è festivo
- `effects_on(date, scope)`: ritorna set di effects attivi
- `list_holidays(year, scope)`: lista festività per anno

**HolidayRule**: Definizione singola festività/chiusura
- `type`: single (una tantum), range (intervallo), fixed (ricorrenza annuale)
- `scope`: system/store/warehouse/supplier
- `effect`: no_order/no_receipt/both
- `params`: parametri specifici per type

**Festività automatiche**:
- Festività nazionali italiane (12 totali):
  - Fixed: Capodanno, Epifania, Liberazione, Lavoro, Repubblica, Ferragosto, Ognissanti, Immacolata, Natale, S.Stefano
  - Mobili: Pasqua, Lunedì dell'Angelo (calcolo algoritmo Meeus/Jones/Butcher)
- Caricamento automatico anche se `holidays.json` mancante

### 2. Integrazione: `src/domain/calendar.py`

**CalendarConfig** esteso con:
- `holiday_calendar: Optional[HolidayCalendar]`: gestione effect-aware
- `holidays: set`: **DEPRECATO** (mantenuto per backward compatibility)

**Funzioni aggiornate**:
- `is_order_day(date, config)`: verifica effect no_order
- `is_delivery_day(date, config)`: verifica effect no_receipt
- `next_receipt_date()` e `next_delivery_day()`: saltano festività automaticamente

**Helper per inizializzazione**:
```python
from src.domain.calendar import create_calendar_with_holidays

config = create_calendar_with_holidays(data_dir)
# Config con HolidayCalendar caricato da data/holidays.json
```

### 3. Configurazione: `data/holidays.json`

Schema JSON con validazione (date ISO, range start<=end, campi obbligatori):

```json
{
  "holidays": [
    {
      "name": "Santo Patrono Milano",
      "scope": "store",
      "effect": "both",
      "type": "fixed",
      "params": {"month": 12, "day": 7}
    },
    {
      "name": "Chiusura estiva",
      "scope": "store",
      "effect": "both",
      "type": "range",
      "params": {"start": "2026-08-10", "end": "2026-08-20"}
    },
    {
      "name": "Inventario magazzino",
      "scope": "warehouse",
      "effect": "no_receipt",
      "type": "single",
      "params": {"date": "2026-12-31"}
    },
    {
      "name": "Fornitore chiuso",
      "scope": "supplier",
      "effect": "no_order",
      "type": "range",
      "params": {"start": "2026-07-20", "end": "2026-08-05"}
    }
  ]
}
```

**Fallback graceful**: se file manca/invalido → solo festività italiane, nessun crash

## Uso

### Inizializzazione in main.py o GUI

```python
from pathlib import Path
from src.domain.calendar import create_calendar_with_holidays
from src.domain import calendar

# Carica festività da data/holidays.json
config = create_calendar_with_holidays(Path("data"))

# Imposta come configurazione globale
calendar.DEFAULT_CONFIG = config
```

### Verifica date

```python
from datetime import date
from src.domain.calendar import is_order_day, is_delivery_day

# Verifica se può ordinare/ricevere
martedi = date(2026, 2, 11)
print(is_order_day(martedi, config))      # True (giorno lavorativo)
print(is_delivery_day(martedi, config))   # True

# Natale 2026 (festività nazionale)
natale = date(2026, 12, 25)
print(is_order_day(natale, config))       # False (effect: both)
print(is_delivery_day(natale, config))    # False

# Inventario magazzino (effect: no_receipt)
inventario = date(2026, 12, 31)
print(is_order_day(inventario, config))   # True (può ordinare)
print(is_delivery_day(inventario, config)) # False (non può ricevere)
```

### Interrogazione HolidayCalendar

```python
from src.domain.calendar import load_holiday_calendar
from pathlib import Path

cal = load_holiday_calendar(Path("data"))

# Lista festività 2026
holidays_2026 = cal.list_holidays(2026)
print(f"Festività 2026: {len(holidays_2026)}")  # >= 12 (italiane + custom)

# Verifica effects su data specifica
effects = cal.effects_on(date(2026, 12, 25))
print(effects)  # {'no_order', 'no_receipt'}

# Filtra per scope
cal.is_holiday(date(2026, 8, 15), scope="system")  # Ferragosto
cal.is_holiday(date(2026, 12, 7), scope="store")   # Patrono (se configurato)
```

### Pasqua e festività mobili

```python
from src.domain.holidays import easter_sunday

easter_2026 = easter_sunday(2026)
print(easter_2026)  # 2026-04-05 (domenica)

easter_monday = easter_2026 + timedelta(days=1)
print(easter_monday)  # 2026-04-06 (lunedì dell'Angelo)
```

## Configurazione Custom

### 1. Scopi (scope)

- **system**: festività nazionali (automatiche + override da config)
- **store**: chiusure negozio/punto vendita
- **warehouse**: chiusure magazzino/deposito  
- **supplier**: chiusure fornitore

Esempio: fornitore chiuso per ferie ma magazzino può ricevere ordini precedenti.

### 2. Effetti (effect)

- **both**: blocca ordini E ricezione (default festività nazionali)
- **no_order**: blocca solo ordini (fornitore chiuso, può ricevere)
- **no_receipt**: blocca solo ricezione (inventario, può ordinare)

### 3. Tipi (type)

**fixed** - Ricorrenza annuale (patrono, compleanno negozio):
```json
{
  "type": "fixed",
  "params": {"month": 12, "day": 7}
}
```

**range** - Intervallo date (ferie estive):
```json
{
  "type": "range",
  "params": {"start": "2026-08-10", "end": "2026-08-20"}
}
```

**single** - Data singola (inventario, evento speciale):
```json
{
  "type": "single",
  "params": {"date": "2026-12-31"}
}
```

### 4. Precedenza

Le regole da `holidays.json` **PREVALGONO** sugli effetti delle festività italiane automatiche per la stessa data.

Esempio: override Natale per permettere ordini online:
```json
{
  "name": "Natale - solo chiusura magazzino",
  "scope": "system",
  "effect": "no_receipt",
  "type": "fixed",
  "params": {"month": 12, "day": 25}
}
```

## Validazione

Il sistema valida automaticamente:
- Date ISO (YYYY-MM-DD)
- Range: `start <= end`
- Fixed: `month [1-12]`, `day [1-31]`
- Campi obbligatori: name, scope, effect, type, params

Regole invalide → warning + skip (non crash)

## Testing

Test completi in `tests/test_calendar.py::TestHolidaySystem`:
- Easter calculation (2026: 5 aprile)
- Italian holidays (12 totali)
- Effect filtering (no_order vs no_receipt)
- Range e fixed-date rules
- Backward compatibility (holidays set deprecato)
- Config loading con fallback

Esegui:
```bash
pytest tests/test_calendar.py::TestHolidaySystem -v
```

## Migrazione da sistema precedente

Se usavi `CalendarConfig(holidays={...})`:

**Prima** (deprecato):
```python
config = CalendarConfig(holidays={date(2026, 12, 25), date(2026, 1, 1)})
```

**Dopo** (raccomandato):
```python
# 1. Aggiungi regole a data/holidays.json
# 2. Carica con helper
config = create_calendar_with_holidays(Path("data"))
```

**Backward compatibility**: il vecchio sistema continua a funzionare ma è meno flessibile (blocca sempre sia order che receipt).

## Festività Italiane Automatiche

Sempre attive senza configurazione:

**Fixed:**
- 1 gennaio: Capodanno
- 6 gennaio: Epifania
- 25 aprile: Liberazione
- 1 maggio: Festa del Lavoro
- 2 giugno: Festa della Repubblica
- 15 agosto: Ferragosto
- 1 novembre: Ognissanti
- 8 dicembre: Immacolata Concezione
- 25 dicembre: Natale
- 26 dicembre: Santo Stefano

**Mobili (calcolate automaticamente):**
- Pasqua (algoritmo Meeus/Jones/Butcher)
- Lunedì dell'Angelo (Pasqua + 1 giorno)

**Note:**
- Santi patroni variano per città → configura in `holidays.json`
- Ponte: usa type="range" per chiusure consecutive

## Esempi Pratici

### Scenario 1: Chiusura estiva negozio

```json
{
  "name": "Chiusura estiva 2026",
  "scope": "store",
  "effect": "both",
  "type": "range",
  "params": {"start": "2026-08-10", "end": "2026-08-25"}
}
```

### Scenario 2: Fornitore chiuso (può ancora ricevere ordini precedenti)

```json
{
  "name": "Ferie fornitore",
  "scope": "supplier",
  "effect": "no_order",
  "type": "range",
  "params": {"start": "2026-07-20", "end": "2026-08-05"}
}
```

### Scenario 3: Inventario annuale magazzino

```json
{
  "name": "Inventario fine anno",
  "scope": "warehouse",
  "effect": "no_receipt",
  "type": "fixed",
  "params": {"month": 12, "day": 31}
}
```

### Scenario 4: Santo Patrono Milano (ricorrenza annuale)

```json
{
  "name": "Sant'Ambrogio",
  "scope": "store",
  "effect": "both",
  "type": "fixed",
  "params": {"month": 12, "day": 7}
}
```

## Risoluzione Problemi

**File holidays.json non trovato**:
→ Sistema usa solo festività italiane (no crash), crea file vuoto:
```json
{"holidays": []}
```

**Regola ignorata**:
→ Controlla warning in console, verifica parametri (date ISO, start<=end, month/day validi)

**Date non bloccate**:
→ Verifica scope e effect corretti, usa `effects_on()` per debug:
```python
effects = config.holiday_calendar.effects_on(date(2026, 12, 7))
print(f"Effects: {effects}")
```

**Pasqua errata**:
→ Confronta con calendario esterno, algoritmo testato 2000-2099

## Compatibilità

- ✅ Backward compatible con `CalendarConfig(holidays=set())`
- ✅ next_receipt_date() salta festività automaticamente
- ✅ projected_inventory_position() rispetta festività
- ✅ GUI lane selector funziona con holidays
- ✅ Test suite: 350/350 passano (nessuna regressione)

## File Modificati

```
src/domain/holidays.py              # Nuovo modulo (logica festività)
src/domain/calendar.py              # Esteso (CalendarConfig + helpers)
data/holidays.json                  # Config esempio
tests/test_calendar.py              # Esteso (14 nuovi test)
HOLIDAY_SYSTEM.md                   # Questa documentazione
```

## Prossimi Sviluppi

Possibili estensioni future (non implementate):
- Import festività da fonti esterne (Google Calendar, iCal)
- UI configurazione festività in GUI
- Notifiche festività imminenti
- Report giorni lavorativi effettivi per periodo
- Santi patroni pre-configurati per città italiane
