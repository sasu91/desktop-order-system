# Promo Preprocessing Feature

## Obiettivo

Preparare dati per stimare effetti promo usando **solo `promo_flag`** (binario: 0=no promo, 1=promo), escludendo giorni con vendite non informative (stock-out, assortment gaps).

## Design Principles

1. **Minimale**: Solo `promo_flag` (NO prezzo, tipo promo, visibilità)
2. **Non invasivo**: NON modifica logica core riordino
3. **Trasparente**: Logging chiaro di esclusioni
4. **Compatibile**: Backward compatible con `sales.csv` esistenti

## Componenti

### 1. Modello Dati (`src/domain/models.py`)

```python
@dataclass(frozen=True)
class SalesRecord:
    date: Date
    sku: str
    qty_sold: int
    promo_flag: int = 0  # NEW: 0 = no promo, 1 = promo day
```

**Schema CSV `sales.csv`**:
```csv
date,sku,qty_sold,promo_flag
2026-01-15,SKU001,45,0
2026-01-16,SKU001,68,1
2026-01-17,SKU001,42,0
```

### 2. Preprocessing Module (`src/promo_preprocessing.py`)

#### Funzione Principale: `prepare_promo_training_data()`

```python
from src.promo_preprocessing import prepare_promo_training_data

dataset = prepare_promo_training_data(
    sku="SKU001",
    sales_records=sales,
    transactions=txns,
    lookback_days=180,  # 6 mesi di storia
)

print(f"Promo days (valid): {len(dataset.promo_observations)}")
print(f"Non-promo days (valid): {len(dataset.non_promo_observations)}")
print(f"Censored: {dataset.censored_days_count} ({dataset.censored_reasons})")
```

**Output**:
```
Promo days (valid): 24
Non-promo days (valid): 142
Censored: 14 ({"OH=0 and sales=0": 8, "UNFULFILLED recent": 6})
```

#### Censoring Logic

Giorni esclusi da training:

1. **Stock-out**: `OH=0 AND sales=0` → Domanda ignota
2. **UNFULFILLED recenti**: Eventi inevasi negli ultimi N giorni → Segnale di shortage
3. **Assortment gaps**: Prodotto temporaneamente fuori assortimento (riservato per future implementazioni)

**Implementazione**: Riusa `is_day_censored()` da `src/domain/ledger.py` (già testato e robusto).

### 3. Stima Uplift Semplice

```python
from src.promo_preprocessing import estimate_promo_uplift_simple

uplift = estimate_promo_uplift_simple(
    dataset,
    min_promo_days=10,      # Minimo 10 giorni promo
    min_non_promo_days=30,  # Minimo 30 giorni non-promo
)

if uplift:
    print(f"Promo uplift: +{uplift['uplift_percent']:.1f}%")
    print(f"  ({uplift['avg_promo_sales']:.1f} vs {uplift['avg_non_promo_sales']:.1f} units/day)")
else:
    print("Dati insufficienti per stima uplift")
```

**Output**:
```
Promo uplift: +42.3%
  (31.2 vs 21.9 units/day)
```

**Formula**:
```
uplift% = (avg_promo_sales - avg_non_promo_sales) / avg_non_promo_sales * 100
```

### 4. Summary Stats (Quick Overview)

```python
from src.promo_preprocessing import get_promo_summary_stats

stats = get_promo_summary_stats("SKU001", sales, lookback_days=90)

print(f"Promo frequency: {stats['promo_frequency']:.1f}%")
print(f"Avg promo sales: {stats['avg_promo_sales']:.1f} units/day")
```

**Output**:
```
Promo frequency: 18.5%
Avg promo sales: 34.2 units/day
Avg non-promo sales: 23.1 units/day
```

## Logging

### Livello INFO (produzione)

```
[SKU001] Promo preprocessing complete: 24 promo days, 142 non-promo days, 14 censored days (7.8%)
[SKU001] Censored breakdown: {'OH=0 and sales=0': 8, 'UNFULFILLED recent': 6}
[SKU001] Promo uplift: +42.3% (31.2 vs 21.9 units/day, n_promo=24, n_non_promo=142)
```

### Livello DEBUG (sviluppo)

```
[SKU001] Censored day 2026-01-15: OH=0 and sales=0 on 2026-01-15
[SKU001] Censored day 2026-01-22: UNFULFILLED recent within 3 days before 2026-01-22
```

## Usage Examples

### Esempio 1: Preprocessing per un singolo SKU

```python
from src.persistence.csv_layer import CSVLayer
from src.promo_preprocessing import prepare_promo_training_data, estimate_promo_uplift_simple

csv_layer = CSVLayer()
sales = csv_layer.read_sales()
txns = csv_layer.read_transactions()

# Prepara dati per SKU001
dataset = prepare_promo_training_data(
    sku="SKU001",
    sales_records=sales,
    transactions=txns,
    lookback_days=180,
)

# Stima uplift
uplift = estimate_promo_uplift_simple(dataset)
if uplift:
    print(f"SKU001 promo uplift: +{uplift['uplift_percent']:.1f}%")
```

### Esempio 2: Batch processing per tutti gli SKU

```python
from src.persistence.csv_layer import CSVLayer
from src.promo_preprocessing import prepare_promo_training_data, estimate_promo_uplift_simple

csv_layer = CSVLayer()
sales = csv_layer.read_sales()
txns = csv_layer.read_transactions()
skus = csv_layer.get_all_sku_ids()

results = {}
for sku in skus:
    dataset = prepare_promo_training_data(sku, sales, txns, lookback_days=90)
    
    # Solo SKU con dati sufficienti
    if len(dataset.promo_observations) >= 10:
        uplift = estimate_promo_uplift_simple(dataset)
        if uplift:
            results[sku] = uplift['uplift_percent']

# Top SKU per uplift promo
top_skus = sorted(results.items(), key=lambda x: x[1], reverse=True)[:10]
print("Top 10 SKU per promo uplift:")
for sku, uplift_pct in top_skus:
    print(f"  {sku}: +{uplift_pct:.1f}%")
```

### Esempio 3: Integrazione in pipeline forecast

```python
# Future: Aggiungere promo uplift a forecast
def forecast_with_promo(sku, sales, txns, promo_plan):
    """
    Forecast con adjustment per promo pianificate.
    
    Args:
        sku: SKU identifier
        sales: Sales records with promo_flag
        txns: Transaction history
        promo_plan: Future promo dates (List[date])
    
    Returns:
        Adjusted forecast considering promo uplift
    """
    # 1. Stima uplift storico
    dataset = prepare_promo_training_data(sku, sales, txns, lookback_days=365)
    uplift = estimate_promo_uplift_simple(dataset)
    
    if not uplift:
        # Nessun dato promo storico → forecast normale
        return forecast_simple(sku, sales)
    
    # 2. Forecast base
    base_forecast = forecast_simple(sku, sales)
    
    # 3. Applica uplift ai giorni promo pianificati
    # (logica semplificata)
    uplift_factor = 1 + (uplift['uplift_percent'] / 100)
    
    # Se ci sono promo nel periodo forecast, aumenta la domanda
    # (implementazione dettagliata riservata per future features)
    
    return base_forecast
```

## Acceptance Criteria

✅ **AC1**: Per SKU con promo storiche, numero giorni promo "validi" e non-promo "validi" è tracciabile

```python
dataset = prepare_promo_training_data("SKU001", sales, txns)
assert len(dataset.promo_observations) > 0
assert len(dataset.non_promo_observations) > 0
print(f"Promo days: {len(dataset.promo_observations)}")
print(f"Non-promo days: {len(dataset.non_promo_observations)}")
```

✅ **AC2**: Logging chiaro di quanti giorni esclusi e perché

```
[SKU001] Promo preprocessing complete: 24 promo days, 142 non-promo days, 14 censored days (7.8%)
[SKU001] Censored breakdown: {'OH=0 and sales=0': 8, 'UNFULFILLED recent': 6}
```

✅ **AC3**: Non introduce prezzo, tipo promo, visibilità

- ✓ Solo `promo_flag` (binario 0/1) aggiunto a `SalesRecord`
- ✓ NO altre colonne nel modello o CSV

✅ **AC4**: Non cambia logica core riordino

- ✓ Modulo separato (`promo_preprocessing.py`)
- ✓ `OrderWorkflow`, `StockCalculator`, `replenishment_policy` NON modificati
- ✓ Preparazione dati è opt-in (non eseguita automaticamente)

## Testing

```bash
# Test preprocessing
python test_promo_preprocessing.py
```

**Test coverage**:
1. Creazione dataset con separazione promo/non-promo
2. Censoring giorni OOS
3. Logging esclusioni
4. Stima uplift con dati sufficienti
5. Gestione dati insufficienti (min_promo_days/min_non_promo_days)

## Migration Guide

### Existing sales.csv without promo_flag

**Backward compatible**: Se `promo_flag` non esiste nel CSV, assume `0` (no promo).

```python
# Old sales.csv (senza promo_flag)
date,sku,qty_sold
2026-01-15,SKU001,45
2026-01-16,SKU001,68

# read_sales() automaticamente assegna promo_flag=0
sales = csv_layer.read_sales()
assert all(s.promo_flag == 0 for s in sales)
```

### Adding promo_flag to existing data

**Opzione 1: Manuale** (per dati storici con info promo disponibile)

```python
from src.persistence.csv_layer import CSVLayer
from datetime import date

csv_layer = CSVLayer()
sales = csv_layer.read_sales()

# Marca giorni promo conosciuti
promo_dates = {date(2026, 1, 10), date(2026, 1, 20), date(2026, 1, 30)}

updated_sales = []
for s in sales:
    new_promo_flag = 1 if s.date in promo_dates else s.promo_flag
    updated_sales.append(SalesRecord(
        date=s.date,
        sku=s.sku,
        qty_sold=s.qty_sold,
        promo_flag=new_promo_flag,
    ))

# Sovrascrivi sales.csv
csv_layer.write_sales(updated_sales)
```

**Opzione 2: Import da sistema esterno**

```python
# Se promo info disponibile in sistema POS/ERP
# (esempio con CSV esterno)
import csv
from datetime import date

promo_calendar = {}  # {(sku, date): promo_flag}

with open('promo_calendar_export.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        sku = row['sku']
        promo_date = date.fromisoformat(row['date'])
        promo_calendar[(sku, promo_date)] = 1

# Aggiorna sales.csv
sales = csv_layer.read_sales()
updated_sales = []
for s in sales:
    promo_flag = promo_calendar.get((s.sku, s.date), 0)
    updated_sales.append(SalesRecord(
        date=s.date,
        sku=s.sku,
        qty_sold=s.qty_sold,
        promo_flag=promo_flag,
    ))

csv_layer.write_sales(updated_sales)
```

## Future Extensions (Reserved for Future Work)

1. **Promo type classification**:
   - Aggiungere `promo_type: str` (es. "discount", "bundle", "coupon")
   - Stima uplift separata per tipo promo

2. **Price elasticity**:
   - Aggiungere `price: float` a `SalesRecord`
   - Modello elasticità prezzo-domanda

3. **Seasonality adjustment**:
   - Uplift promo aggiustato per stagionalità
   - Confronto promo vs non-promo con matched weeks

4. **Monte Carlo integration**:
   - Promo uplift come boost factor in simulazioni MC
   - Uncertainty estimation per forecast promo

5. **Forecast adjustment automatico**:
   - Se promo pianificata nel periodo forecast, applica uplift
   - Integrazione con calendario promo

## References

- [CENSORED_DAYS_FEATURE.md](CENSORED_DAYS_FEATURE.md): Logica censoring OOS/inevasi
- [FORECAST_MODULE.md](FORECAST_MODULE.md): Forecast semplice e Monte Carlo
- [REPLENISHMENT_POLICY_SUMMARY.md](REPLENISHMENT_POLICY_SUMMARY.md): (s,S) policy core

## Changelog

- **2026-02-12**: Feature implementata
  - Aggiunto `promo_flag` a `SalesRecord` e schema `sales.csv`
  - Creato modulo `src/promo_preprocessing.py`
  - Logging esclusioni giorni censored
  - Test coverage con `test_promo_preprocessing.py`
