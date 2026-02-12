# Promo Calendar Feature

## Obiettivo

Sistema minimale per pianificare e gestire periodi promozionali (promo windows) senza informazioni su prezzo, sconto%, tipo promo o visibilità. Focus esclusivo su **date scheduling** e **integrazione con sales data**.

## Design Principles

1. **Minimale**: Solo `sku`, `start_date`, `end_date`, `store_id` (opzionale), `promo_flag=1`
2. **Inclusivo**: Date inclusive (start_date e end_date compresi)
3. **Store-aware**: Supporto multi-store con finestre globali (store_id=None) e specifiche
4. **Non invasivo**: NON modifica logica core riordino
5. **Retroattivo**: Può arricchire vendite passate e future con promo_flag

## Vincoli Rispettati

✅ **NON** include: prezzo, sconto%, tipo promo, volantino, display, visibilità  
✅ **SÌ** include: sku, date range, store_id opzionale, promo_flag  
✅ Timezone: Date naive (assume business timezone coerente)  
✅ Gestione overlap: Validazione e prevenzione overlap per stesso SKU+store  

---

## Componenti

### 1. Domain Model: `PromoWindow` (src/domain/models.py)

```python
@dataclass(frozen=True)
class PromoWindow:
    """
    Promo window for calendar planning.
    
    Represents a promotional period for a SKU with start/end dates (inclusive).
    """
    sku: str
    start_date: Date
    end_date: Date
    store_id: Optional[str] = None  # None = global (all stores)
    promo_flag: int = 1  # Always 1 for PromoWindow
    
    def contains_date(self, check_date: Date) -> bool:
        """Check if date falls within promo window (inclusive)."""
        return self.start_date <= check_date <= self.end_date
    
    def overlaps_with(self, other: 'PromoWindow') -> bool:
        """Check if this promo window overlaps with another (same SKU+store)."""
        ...
    
    def duration_days(self) -> int:
        """Return duration of promo window in days (inclusive)."""
        return (self.end_date - self.start_date).days + 1
```

**Validazioni automatiche**:
- `start_date <= end_date` (ValueError se violato)
- `sku` non vuoto
- `promo_flag == 1` (sempre)

**Store Logic**:
- `store_id=None`: Finestra **globale**, vale per tutti gli store
- `store_id="STORE_A"`: Finestra **specifica**, vale solo per STORE_A
- Overlap check: Due finestre overlappano SOLO se stesso `sku` E stesso `store_id`

### 2. Persistence Layer: CSV Schema (src/persistence/csv_layer.py)

**File**: `data/promo_calendar.csv`

**Schema**:
```csv
sku,start_date,end_date,store_id,promo_flag
SKU001,2026-02-10,2026-02-15,,1
SKU001,2026-03-01,2026-03-07,,1
SKU002,2026-02-20,2026-02-28,STORE_B,1
```

**Funzioni CSV**:
```python
csv_layer.read_promo_calendar() -> List[PromoWindow]
csv_layer.write_promo_window(window: PromoWindow)  # Append
csv_layer.write_promo_calendar(windows: List[PromoWindow])  # Overwrite
```

**Auto-create**: File creato automaticamente con header se mancante (come tutti i CSV).

### 3. Promo Calendar Utilities (src/promo_calendar.py)

#### Query Functions

```python
from src.promo_calendar import is_promo, promo_windows_for_sku

# Check if specific date is promo day
is_promo_day = is_promo(
    check_date=date(2026, 2, 12),
    sku="SKU001",
    promo_windows=windows,
    store_id="STORE_A"  # Optional
)

# Get all promo windows for SKU
sku_windows = promo_windows_for_sku(
    sku="SKU001",
    promo_windows=all_windows,
    store_id="STORE_A"  # Optional: includes global + specific
)
```

**Store Filter Logic** (importante):
- `store_id=None` (no filter): Ritorna TUTTE le finestre per SKU
- `store_id="STORE_A"`: Ritorna finestre globali (None) + specifiche STORE_A
- Esempio: 3 finestre totali (2 globali, 1 STORE_B) → filtro STORE_A → 2 finestre (le 2 globali)

#### Mutation Functions

```python
from src.promo_calendar import add_promo_window, remove_promo_window, validate_no_overlap

# Add new promo window
from src.domain.models import PromoWindow

window = PromoWindow(
    sku="SKU001",
    start_date=date(2026, 4, 1),
    end_date=date(2026, 4, 5),
    store_id="STORE_A",
)

success = add_promo_window(
    csv_layer,
    window,
    allow_overlap=False  # Reject if overlaps with existing
)

if success:
    print("Window added successfully")
else:
    print("Overlap detected, window rejected")

# Remove promo window (exact match required)
removed = remove_promo_window(
    csv_layer,
    sku="SKU001",
    start_date=date(2026, 4, 1),
    end_date=date(2026, 4, 5),
    store_id="STORE_A",
)

# Validate no overlaps in existing calendar
overlaps = validate_no_overlap(windows)
if overlaps:
    for w1, w2 in overlaps:
        print(f"Overlap: {w1.start_date}-{w1.end_date} vs {w2.start_date}-{w2.end_date}")
```

#### Sales Data Integration

```python
from src.promo_calendar import apply_promo_flags_to_sales, enrich_sales_with_promo_calendar

# Apply promo flags to sales records (in-memory)
sales = csv_layer.read_sales()
windows = csv_layer.read_promo_calendar()

updated_sales = apply_promo_flags_to_sales(
    sales_records=sales,
    promo_windows=windows,
    store_id=None  # Optional store filter
)

# Direct enrichment (overwrites sales.csv)
enrich_sales_with_promo_calendar(csv_layer, store_id=None)
```

**Logic**:
- Per ogni vendita `(date, sku)`, controlla se `is_promo(date, sku, windows)` è True
- Se sì → `promo_flag=1`, altrimenti `promo_flag=0`
- Logging automatico di quante righe sono state aggiornate

#### Reporting Functions

```python
from src.promo_calendar import get_promo_stats, get_active_promos, get_upcoming_promos

# Summary stats
stats = get_promo_stats(windows, sku="SKU001")  # sku optional
print(f"Total windows: {stats['total_windows']}")
print(f"Total promo days: {stats['total_promo_days']}")
print(f"Avg duration: {stats['avg_window_duration']} days")

# Active promos on specific date
active = get_active_promos(windows, check_date=date.today())
print(f"Active promos today: {len(active)}")

# Upcoming promos in next 30 days
upcoming = get_upcoming_promos(
    windows,
    check_date=date.today(),
    days_ahead=30
)
print(f"Upcoming promos: {[w.sku for w in upcoming]}")
```

---

## Usage Examples

### Esempio 1: Pianificare una promo future

```python
from datetime import date
from src.persistence.csv_layer import CSVLayer
from src.domain.models import PromoWindow
from src.promo_calendar import add_promo_window

csv_layer = CSVLayer()

# Promo per SKU001 dal 15 al 21 febbraio 2026
promo = PromoWindow(
    sku="SKU001",
    start_date=date(2026, 2, 15),
    end_date=date(2026, 2, 21),
    store_id=None,  # Globale (tutti gli store)
)

# Add with overlap check
if add_promo_window(csv_layer, promo, allow_overlap=False):
    print(f"Promo added: {promo.duration_days()} days")
else:
    print("Overlap detected, promo rejected")
```

### Esempio 2: Arricchire sales.csv con promo calendar

```python
from src.persistence.csv_layer import CSVLayer
from src.promo_calendar import enrich_sales_with_promo_calendar

csv_layer = CSVLayer()

# Automatically apply promo_flag to all sales based on calendar
enrich_sales_with_promo_calendar(csv_layer)

# Now sales.csv has promo_flag=1 for promo days, 0 for non-promo
sales = csv_layer.read_sales()
promo_sales = [s for s in sales if s.promo_flag == 1]
print(f"Promo sales: {len(promo_sales)}/{len(sales)}")
```

### Esempio 3: Verificare promo attive oggi

```python
from datetime import date
from src.persistence.csv_layer import CSVLayer
from src.promo_calendar import get_active_promos

csv_layer = CSVLayer()
windows = csv_layer.read_promo_calendar()

active_today = get_active_promos(windows, date.today())

for promo in active_today:
    print(f"{promo.sku}: {promo.start_date} to {promo.end_date} ({promo.store_id or 'All stores'})")
```

### Esempio 4: Report promo per SKU specifico

```python
from src.persistence.csv_layer import CSVLayer
from src.promo_calendar import promo_windows_for_sku, get_promo_stats

csv_layer = CSVLayer()
windows = csv_layer.read_promo_calendar()

# Get all promo windows for SKU001
sku001_promos = promo_windows_for_sku("SKU001", windows)

print(f"SKU001 has {len(sku001_promos)} promo windows planned:")
for w in sku001_promos:
    print(f"  {w.start_date} to {w.end_date} ({w.duration_days()} days)")

# Stats
stats = get_promo_stats(windows, sku="SKU001")
print(f"Total promo days for SKU001: {stats['total_promo_days']}")
```

### Esempio 5: Multi-store scenario

```python
from datetime import date
from src.domain.models import PromoWindow
from src.promo_calendar import add_promo_window

csv_layer = CSVLayer()

# Global promo (all stores)
global_promo = PromoWindow(
    sku="SKU001",
    start_date=date(2026, 3, 1),
    end_date=date(2026, 3, 7),
    store_id=None,  # Global
)
add_promo_window(csv_layer, global_promo)

# Store-specific promo (solo STORE_B)
store_b_promo = PromoWindow(
    sku="SKU001",
    start_date=date(2026, 3, 10),
    end_date=date(2026, 3, 15),
    store_id="STORE_B",
)
add_promo_window(csv_layer, store_b_promo)

# Query per STORE_A (include solo global promo)
from src.promo_calendar import promo_windows_for_sku
store_a_promos = promo_windows_for_sku("SKU001", csv_layer.read_promo_calendar(), store_id="STORE_A")
print(f"STORE_A promos: {len(store_a_promos)}")  # 1 (global only)

# Query per STORE_B (include global + specific)
store_b_promos = promo_windows_for_sku("SKU001", csv_layer.read_promo_calendar(), store_id="STORE_B")
print(f"STORE_B promos: {len(store_b_promos)}")  # 2 (global + STORE_B specific)
```

---

## Overlap Management

### Come funziona

**Overlap check**: Due finestre overlappano se:
1. Stesso `sku`
2. Stesso `store_id` (o entrambe None)
3. Intervalli di date si sovrappongono

**Overlap formula**:
```
NOT (A.end < B.start OR B.end < A.start)
```

**Esempi**:
- `[2026-02-10, 2026-02-15]` overlap con `[2026-02-14, 2026-02-20]` ✅ (overlap su 14-15)
- `[2026-02-10, 2026-02-15]` NO overlap con `[2026-02-16, 2026-02-20]` ❌ (consecutivi, NO overlap)
- `[2026-02-10, 2026-02-15]` con SKU001 NO overlap con `[2026-02-12, 2026-02-18]` SKU002 ❌ (SKU diversi)

### Prevenzione Overlap

```python
# Add with overlap check (default: allow_overlap=False)
success = add_promo_window(csv_layer, new_window, allow_overlap=False)

if not success:
    print("Window rejected due to overlap")
    
    # Identify overlaps
    existing = csv_layer.read_promo_calendar()
    for w in existing:
        if new_window.overlaps_with(w):
            print(f"  Overlaps with: {w.start_date} to {w.end_date}")
```

### Validazione Calendar Esistente

```python
from src.promo_calendar import validate_no_overlap

windows = csv_layer.read_promo_calendar()
overlaps = validate_no_overlap(windows)

if overlaps:
    print(f"Found {len(overlaps)} overlap(s):")
    for w1, w2 in overlaps:
        print(f"  {w1.sku}: {w1.start_date}-{w1.end_date} vs {w2.start_date}-{w2.end_date}")
else:
    print("No overlaps detected")
```

### Gestione Overlap Volontari

Se overlap è intenzionale (es. promo sovrapposte multi-canale), usa `allow_overlap=True`:

```python
add_promo_window(csv_layer, window, allow_overlap=True)
```

---

## Integration con Forecast/Reorder

### Opzione 1: Promo Flag come Feature (Current)

Usa `promo_flag` come input per analisi:

```python
from src.promo_preprocessing import prepare_promo_training_data, estimate_promo_uplift_simple

# Stima uplift promo storico
dataset = prepare_promo_training_data(
    sku="SKU001",
    sales_records=sales,
    transactions=txns,
    lookback_days=180,
)

uplift = estimate_promo_uplift_simple(dataset)
if uplift:
    print(f"Promo uplift: +{uplift['uplift_percent']:.1f}%")
```

### Opzione 2: Forecast Adjustment (Future Extension)

Per promo **future** (non ancora in sales data):

```python
from src.promo_calendar import is_promo, get_upcoming_promos

# Durante forecast, check se giorni futuri sono promo
windows = csv_layer.read_promo_calendar()

forecast_date = date.today() + timedelta(days=7)
if is_promo(forecast_date, "SKU001", windows):
    # Apply uplift to forecast
    adjusted_forecast = base_forecast * (1 + uplift_percent / 100)
```

**Status**: Non implementato (reserved for future work).

---

## Testing

```bash
# Run test
python test_promo_calendar.py
```

**Coverage**:
1. ✅ PromoWindow model validation (date range, overlap, duration)
2. ✅ Query functions (is_promo, promo_windows_for_sku, store filters)
3. ✅ Mutation functions (add, remove, validate_no_overlap)
4. ✅ CSV persistence (read, write, round-trip)
5. ✅ Sales data integration (apply_promo_flags, enrich workflow)
6. ✅ Reporting functions (stats, active/upcoming promos)

---

## Migration Guide

### Existing System (No Promo Calendar)

**Step 1**: Auto-create promo_calendar.csv

```python
from src.persistence.csv_layer import CSVLayer
csv_layer = CSVLayer()  # Auto-creates promo_calendar.csv if missing
```

**Step 2**: (Optional) Populate with historical promo dates

```python
from datetime import date
from src.domain.models import PromoWindow

# Example: Add known past promos
past_promos = [
    PromoWindow(sku="SKU001", start_date=date(2025, 12, 1), end_date=date(2025, 12, 7)),
    PromoWindow(sku="SKU002", start_date=date(2025, 12, 15), end_date=date(2025, 12, 21)),
]

csv_layer.write_promo_calendar(past_promos)
```

**Step 3**: Enrich sales.csv with promo flags

```python
from src.promo_calendar import enrich_sales_with_promo_calendar

enrich_sales_with_promo_calendar(csv_layer)
# Now sales.csv has promo_flag column populated
```

### Existing Sales Data with Manual promo_flag

Se `sales.csv` già ha `promo_flag` inserito manualmente:

**Opzione A**: Migra a promo_calendar (consigliato per future scheduling)

```python
from src.persistence.csv_layer import CSVLayer
from src.domain.models import PromoWindow
from datetime import date, timedelta

csv_layer = CSVLayer()
sales = csv_layer.read_sales()

# Identify promo periods from sales data
promo_windows = []
current_window = None

for sale in sorted(sales, key=lambda s: (s.sku, s.date)):
    if sale.promo_flag == 1:
        if current_window is None or current_window.sku != sale.sku or (sale.date - current_window.end_date).days > 1:
            # Start new window
            current_window = PromoWindow(
                sku=sale.sku,
                start_date=sale.date,
                end_date=sale.date,
            )
        else:
            # Extend current window
            # (Need to recreate as frozen dataclass)
            current_window = PromoWindow(
                sku=current_window.sku,
                start_date=current_window.start_date,
                end_date=sale.date,
                store_id=current_window.store_id,
            )
    else:
        if current_window:
            promo_windows.append(current_window)
            current_window = None

# Save to calendar
csv_layer.write_promo_calendar(promo_windows)
print(f"Migrated {len(promo_windows)} promo windows to calendar")
```

**Opzione B**: Mantieni entrambi (sales promo_flag + calendar per future)

Calendar usato solo per promo pianificate (future), sales.csv mantiene flag storico.

---

## Acceptance Criteria

✅ **AC1**: Struttura dati minimale con `sku`, `start_date`, `end_date`, `store_id` (opzionale)

```python
window = PromoWindow(sku="SKU001", start_date=date(2026, 2, 10), end_date=date(2026, 2, 15))
assert window.duration_days() == 6
```

✅ **AC2**: Funzioni di utilità (is_promo, promo_windows_for_sku, add/remove)

```python
assert is_promo(date(2026, 2, 12), "SKU001", windows) is True
assert add_promo_window(csv_layer, window, allow_overlap=False) is True
```

✅ **AC3**: Validazione overlap (prevenzione o gestione)

```python
overlaps = validate_no_overlap(windows)
assert len(overlaps) == 0
```

✅ **AC4**: Integrazione con sales data (promo_flag)

```python
enriched_sales = apply_promo_flags_to_sales(sales, windows)
assert enriched_sales[0].promo_flag == 1  # Promo day
```

✅ **AC5**: Persistenza CSV con auto-create

```python
csv_layer = CSVLayer()  # Auto-creates promo_calendar.csv
csv_layer.write_promo_window(window)
loaded = csv_layer.read_promo_calendar()
assert len(loaded) == 1
```

✅ **AC6**: Timezone-naive, date inclusivity

```python
assert window.contains_date(window.start_date)  # Inclusive start
assert window.contains_date(window.end_date)    # Inclusive end
```

✅ **AC7**: NON include prezzo, sconto%, tipo, visibilità

- ✓ Solo `sku`, `start_date`, `end_date`, `store_id`, `promo_flag`
- ✓ NO campi aggiuntivi nel modello o CSV

---

## GUI Integration

### Tab: 📅 Calendario Promo

**Location**: Tab posizionato prima di "⚙️ Impostazioni" nel notebook principale

**Features**:
1. **Form di Inserimento Promo Window**:
   - SKU (obbligatorio): Autocomplete entry con filtro real-time
   - Data Inizio (obbligatorio): DateEntry widget (o Entry se tkcalendar non disponibile)
   - Data Fine (obbligatorio): DateEntry widget
   - Store ID (opzionale): Entry field (vuoto = tutti i negozi)
   - Validazione real-time: Abilita/Disabilita pulsante submit

2. **Tabella Finestre Promo**:
   - Colonne: SKU, Data Inizio, Data Fine, Durata (gg), Store ID, Stato
   - Colori stato:
     - **Verde** (attiva): Promo in corso oggi
     - **Blu** (futura): Promo non ancora iniziata
     - **Grigio** (scaduta): Promo terminata
   - Filtro per SKU: Entry field per filtrare righe

3. **Azioni**:
   - **Aggiungi Promo**: Aggiunge finestra con auto-merge overlap (user preference)
   - **Rimuovi Selezionata**: Rimuove finestra con conferma dialog
   - **Aggiorna**: Ricarica tabella da CSV

4. **Auto-sync Comportamento** (User Preferences):
   - ✓ **Merge overlap automatico**: `allow_overlap=True` (non blocca, permette sovrapposizioni)
   - ✓ **Auto-sync sales.csv**: Chiama `enrich_sales_with_promo_calendar()` dopo ogni add/remove
   - ✓ **Tab position**: Prima di Settings (default order aggiornato)

**Integration Points**:
- `src/gui/app.py`: 
  - `_build_promo_tab()`: Setup UI
  - `_refresh_promo_tab()`: Popola tabella
  - `_add_promo_window()`: Add logic con auto-sync
  - `_remove_promo_window()`: Remove logic con conferma
  - `_validate_promo_form()`: Validazione real-time
- `src/promo_calendar.py`: Backend utilities chiamati da GUI
- Audit log: Operations `PROMO_WINDOW_ADD` e `PROMO_WINDOW_REMOVE`

**Test**:
```bash
python test_promo_gui.py  # Manual GUI test script
```

---

## Future Extensions (Reserved)

1. **Promo Type Classification**:
   - Aggiungere `promo_type: str` (es. "discount", "bundle", "BOGOF")
   - Stima uplift separata per tipo

2. **Multi-Channel Promo**:
   - Aggiungere `channel: str` (es. "volantino", "web", "in-store")
   - Overlap consentito se canali diversi

3. **Forecast Integration**:
   - Auto-apply uplift a forecast per promo pianificate
   - Monte Carlo con promo boost factor

4. **Calendar View Enhancement**:
   - Visual calendar widget con highlight promo days
   - Drag-and-drop promo window resize
   - Multi-SKU promo window creation

5. **Import/Export**:
   - Import promo calendar da sistema esterno (CSV, JSON)
   - Export promo schedule per comunicazione marketing

---

## References

- [PROMO_PREPROCESSING_FEATURE.md](PROMO_PREPROCESSING_FEATURE.md): Promo flag in sales data + uplift estimation
- [FORECAST_MODULE.md](FORECAST_MODULE.md): Forecast semplice e Monte Carlo
- [REPLENISHMENT_POLICY_SUMMARY.md](REPLENISHMENT_POLICY_SUMMARY.md): (s,S) policy core

---

## Changelog

- **2026-02-12**: Feature implementata (backend + GUI)
  - `PromoWindow` domain model con validazioni
  - CSV schema `promo_calendar.csv` + read/write operations
  - Modulo `src/promo_calendar.py` con query, mutation, reporting functions
  - Test coverage completo (`test_promo_calendar.py`)
  - Store-aware logic (global vs specific windows)
  - Sales data enrichment workflow
  - **GUI Tab 📅 Calendario Promo** con form, table, auto-sync
  - Auto-merge overlap e auto-sync sales come user preferences
