# Auto-Classificazione Variabilità Domanda

**Data implementazione**: 6 Febbraio 2026  
**Status**: ✅ Complete & Tested

## Panoramica

Sistema di classificazione automatica della variabilità domanda basato su **soglie adattive** calcolate da quartili storici. Integrato nel workflow di salvataggio SKU per aggiornamento automatico della classificazione.

## Architettura

### Componenti Principali

1. **Calcolo Metriche** (`src/domain/auto_variability.py`)
   - `calculate_cv()`: Coefficiente di Variazione (σ/μ)
   - `calculate_autocorrelation()`: Rilevamento pattern stagionali (lag-7)
   - `compute_sku_metrics()`: Aggregazione metriche per singolo SKU

2. **Soglie Adattive** (Quartili)
   - `compute_adaptive_thresholds()`: Calcola Q1 (25°) e Q3 (75°) da tutti SKU
   - Fallback a soglie fisse (0.3, 0.7) se < 4 SKU

3. **Classificazione** (Decision Tree)
   - `classify_demand_variability()`: Logica decisionale per singolo SKU
   - `classify_all_skus()`: Classificazione batch con soglie adattive

4. **Integrazione Persistenza** (`src/persistence/csv_layer.py`)
   - Auto-classificazione in `write_sku()` e `update_sku()`
   - Applicata solo se `demand_variability == STABLE` (default)
   - Preserva classificazioni manuali esistenti

5. **UI Configurazione** (`src/gui/app.py`)
   - Tab "Impostazioni" con sezione "Auto-classificazione Variabilità"
   - 6 parametri configurabili tramite GUI
   - Valori salvati in `data/settings.json`

## Algoritmo di Classificazione

### Two-Pass Adaptive Algorithm

```python
# Pass 1: Compute metrics for all SKUs
for sku in all_skus:
    metrics[sku] = compute_sku_metrics(sku, sales_history)
    # Output: mean, σ, CV, autocorr_lag7, observations

# Pass 2: Calculate adaptive thresholds from quartiles
cv_values = [m.cv for m in metrics if m.has_sufficient_data]
Q1 = percentile(cv_values, 25)  # STABLE threshold
Q3 = percentile(cv_values, 75)  # HIGH threshold

# Pass 3: Classify each SKU
for sku, m in metrics.items():
    if not m.has_sufficient_data:
        category = FALLBACK  # Default: LOW
    elif m.autocorr_lag7 > seasonal_threshold:
        category = SEASONAL  # Weekly pattern detected
    elif m.cv <= Q1:
        category = STABLE    # Predictable demand
    elif m.cv >= Q3:
        category = HIGH      # Volatile demand
    else:
        category = LOW       # Moderate variability
```

### Decision Tree

```
                    ┌──────────────────┐
                    │ Has sufficient   │
                    │   data (≥30d)?   │
                    └────────┬─────────┘
                             │
                 ┌───────────┴────────────┐
                 NO                      YES
                 │                        │
          ┌──────▼──────┐         ┌──────▼──────────┐
          │  FALLBACK   │         │ Autocorr > 0.3? │
          │   (LOW)     │         └────────┬────────┘
          └─────────────┘                  │
                                ┌──────────┴──────────┐
                               YES                   NO
                                │                     │
                         ┌──────▼──────┐      ┌──────▼──────┐
                         │  SEASONAL   │      │  CV <= Q1?  │
                         └─────────────┘      └──────┬──────┘
                                                     │
                                          ┌──────────┴──────────┐
                                         YES                   NO
                                          │                     │
                                   ┌──────▼──────┐      ┌──────▼──────┐
                                   │   STABLE    │      │  CV >= Q3?  │
                                   └─────────────┘      └──────┬──────┘
                                                                │
                                                     ┌──────────┴──────────┐
                                                    YES                   NO
                                                     │                     │
                                              ┌──────▼──────┐      ┌──────▼──────┐
                                              │    HIGH     │      │     LOW     │
                                              └─────────────┘      └─────────────┘
```

## Parametri Configurabili (Settings)

### `data/settings.json` → sezione `auto_variability`

| Parametro | Default | Range | Descrizione |
|-----------|---------|-------|-------------|
| **enabled** | `true` | bool | Abilita/disabilita auto-classificazione |
| **min_observations** | `30` | 7-365 | Minimo giorni vendita richiesti |
| **stable_percentile** | `25` | 1-50 | Percentile per soglia STABLE (Q1) |
| **high_percentile** | `75` | 50-99 | Percentile per soglia HIGH (Q3) |
| **seasonal_threshold** | `0.3` | 0.0-1.0 | Soglia autocorrelazione per SEASONAL |
| **fallback_category** | `"LOW"` | enum | Categoria se dati insufficienti |

### Esempio Configurazione

```json
{
  "auto_variability": {
    "enabled": {
      "value": true,
      "description": "Abilita classificazione automatica variabilità domanda"
    },
    "min_observations": {
      "value": 30,
      "description": "Minimo giorni vendita richiesti per classificazione"
    },
    "stable_percentile": {
      "value": 25,
      "description": "Percentile per soglia STABLE (CV <= Q1)"
    },
    "high_percentile": {
      "value": 75,
      "description": "Percentile per soglia HIGH (CV >= Q3)"
    },
    "seasonal_threshold": {
      "value": 0.3,
      "description": "Soglia autocorrelazione per rilevare SEASONAL (0-1)"
    },
    "fallback_category": {
      "value": "LOW",
      "description": "Categoria di default se dati insufficienti"
    }
  }
}
```

## Workflow di Salvataggio SKU

### write_sku() / update_sku()

1. **Check abilitazione**: Leggi `settings.json` → `auto_variability.enabled`
2. **Filter**: Applica solo se `sku.demand_variability == STABLE` (preserva manuali)
3. **Load sales**: Carica storico vendite da `data/sales.csv`
4. **Classify**: Chiama `auto_classify_variability(sku, sales, settings)`
5. **Update**: Crea nuovo oggetto SKU con variabilità auto-classificata
6. **Save**: Persiste in `data/skus.csv`

### Preservazione Classificazioni Manuali

```python
# Se utente ha impostato manualmente HIGH/SEASONAL/LOW → NON sovrascrivere
if sku.demand_variability != DemandVariability.STABLE:
    # Skip auto-classification
    save_as_is(sku)
else:
    # Auto-classify (default STABLE)
    auto_classified = classify(sku)
    save(auto_classified)
```

## Metriche Utilizzate

### 1. Coefficient of Variation (CV)

**Formula**: `CV = σ / μ`

**Interpretazione**:
- CV < 0.3: Bassa variabilità (domanda stabile)
- 0.3 ≤ CV < 0.7: Variabilità moderata
- CV ≥ 0.7: Alta variabilità (domanda volatile)

**Robustezza**: Normalizzato (indipendente da scala), ma sensibile a outlier

### 2. Autocorrelation (lag-7)

**Formula**: `ρ(7) = Cov(X_t, X_{t+7}) / Var(X_t)`

**Interpretazione**:
- ρ > 0.3: Pattern settimanale forte (SEASONAL)
- ρ ≈ 0: Domanda casuale (no pattern)
- ρ < 0: Anti-correlazione (raro in vendite)

**Utilizzo**: Rilevamento pattern ricorrenti (es. vendite alte weekend)

## Esempi Pratici

### Scenario 1: Portfolio con 10 SKU

**Dati**:
- 10 SKU con 60 giorni di storico ciascuno
- CV variabili: [0.1, 0.2, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2]

**Calcolo Quartili**:
- Q1 (25°): CV ≈ 0.25
- Q3 (75°): CV ≈ 0.85

**Classificazioni**:
| SKU | CV | Autocorr | Categoria | Rationale |
|-----|-------|----------|-----------|-----------|
| SKU001 | 0.1 | 0.1 | **STABLE** | CV < Q1 (0.25) |
| SKU002 | 0.2 | 0.1 | **STABLE** | CV < Q1 |
| SKU003 | 0.3 | 0.5 | **SEASONAL** | Autocorr > 0.3 |
| SKU004 | 0.5 | 0.1 | **LOW** | Q1 < CV < Q3 |
| SKU005 | 0.6 | 0.1 | **LOW** | Q1 < CV < Q3 |
| SKU006 | 0.7 | 0.1 | **LOW** | Q1 < CV < Q3 (border) |
| SKU007 | 0.8 | 0.1 | **LOW** | Q1 < CV < Q3 (border) |
| SKU008 | 0.9 | 0.1 | **HIGH** | CV > Q3 (0.85) |
| SKU009 | 1.0 | 0.1 | **HIGH** | CV > Q3 |
| SKU010 | 1.2 | 0.1 | **HIGH** | CV > Q3 |

**Distribuzione**: 20% STABLE, 10% SEASONAL, 40% LOW, 30% HIGH

### Scenario 2: Nuovo SKU (primo salvataggio)

1. **Input**: Utente crea SKU "NEW001", lascia `demand_variability` = STABLE (default)
2. **Sales history**: 0 giorni (appena creato)
3. **Auto-classification**:
   - `observations < min_observations` (0 < 30)
   - Classificato come **LOW** (fallback)
4. **Salvataggio**: `data/skus.csv` → `demand_variability = "LOW"`
5. **Effetto safety stock**: Moltiplicatore ×1.0 (nessun cambio)

### Scenario 3: Aggiornamento dopo 60 giorni

1. **Sales history**: 60 giorni, vendite = [10, 11, 10, 12, 10, 11, ...] (CV ~0.1)
2. **Auto-classification**:
   - Quartili ricalcolati da tutti SKU → Q1 = 0.3
   - CV = 0.1 < Q1 → **STABLE**
3. **Update SKU**: `demand_variability` cambia da LOW → STABLE
4. **Effetto safety stock**: Moltiplicatore passa da ×1.0 → ×0.8 (riduzione 20%)

## Testing

### Test Suite: `test_auto_variability.py`

**7 test cases**:
1. ✅ `test_calculate_cv`: Calcolo CV corretto (stable vs volatile)
2. ✅ `test_calculate_autocorrelation`: Rilevamento pattern settimanali
3. ✅ `test_compute_sku_metrics`: Aggregazione metriche singolo SKU
4. ✅ `test_compute_adaptive_thresholds`: Quartili Q1/Q3 corretti
5. ✅ `test_classify_demand_variability`: Logica decisionale (5 casi)
6. ✅ `test_classify_all_skus_integration`: End-to-end con 5 SKU realistici
7. ✅ `test_adaptive_thresholds_with_few_skus`: Fallback soglie fisse

**Esecuzione**:
```bash
python test_auto_variability.py
# Output: ✅ ALL AUTO-CLASSIFICATION TESTS PASSED
```

**Coverage**:
- Casi edge: dati insufficienti, CV=0, autocorr=None
- Fallback: < 4 SKU, < 30 giorni
- Boundary conditions: CV esattamente su Q1/Q3

## Integrazione con Safety Stock

### Pipeline Completo

```
┌─────────────────┐
│  Sales History  │
│  (sales.csv)    │
└────────┬────────┘
         │
    ┌────▼─────────────┐
    │ Auto-Classifier  │
    │  (on save SKU)   │
    └────┬─────────────┘
         │
┌────────▼──────────┐
│ demand_variability│ → STABLE / LOW / SEASONAL / HIGH
└────────┬──────────┘
         │
    ┌────▼─────────────────┐
    │ Safety Stock Calc    │ (order.py, lines 154-174)
    │  base × multiplier   │
    └────┬─────────────────┘
         │
    ┌────▼────────┐
    │  Proposal   │ → Qty adjusted by variability
    └─────────────┘
```

### Moltiplicatori Applicati

| Variabilità | Moltiplicatore | Effetto | Quando |
|-------------|----------------|---------|--------|
| **STABLE** | ×0.8 | -20% safety stock | CV < Q1 |
| **LOW** | ×1.0 | Nessun cambio | Q1 ≤ CV < Q3 |
| **SEASONAL** | ×1.0 | Nessun cambio | Autocorr > 0.3 |
| **HIGH** | ×1.5 | +50% safety stock | CV ≥ Q3 |

## Vantaggi Soglie Adattive

### vs Soglie Fisse (0.3, 0.7)

**Problema soglie fisse**:
- Portfolio omogeneo (tutti CV ~0.5) → nessuno STABLE/HIGH
- Portfolio eterogeneo (CV 0.01-2.0) → tutti classificati estremi

**Soluzione adattiva (quartili)**:
- Sempre 25% STABLE, 25% HIGH (per definizione)
- Restante 50% distribuito tra LOW/SEASONAL
- Adattamento automatico al comportamento del portfolio

**Esempio**:
```
Portfolio A (settore farmacia):        Portfolio B (settore moda):
CV range: 0.1 - 0.6                    CV range: 0.5 - 2.0
Q1 = 0.25, Q3 = 0.45                   Q1 = 0.9, Q3 = 1.5

SKU con CV=0.5:                        SKU con CV=0.5:
→ HIGH (> Q3=0.45)                     → STABLE (< Q1=0.9)

Stessa metrica, classificazione diversa → contestualizzata al portfolio!
```

## Limitazioni e Future Enhancements

### Limitazioni Attuali

1. **Indipendenza Daily Errors**: CV assume errori giornalieri indipendenti (no autocorr forecast)
2. **Sample Size**: Min 30 giorni arbitrario (potrebbe servire > 60 per SEASONAL detection)
3. **Autocorr Lag Fisso**: Solo lag-7 (settimanale), non detecta mensili/trimestrali
4. **No Trend Removal**: CV influenzato da trend (dovrebbe usare residui detrended)

### Roadmap Futuri Sviluppi

#### 1. Auto-Update Periodico (Batch)
```python
# Cron job giornaliero
def auto_reclassify_all_skus():
    """Ricalcola variabilità per tutti SKU con nuovi dati."""
    for sku in all_skus:
        if days_since_last_update(sku) > 30:
            reclassify(sku)
```

#### 2. Multi-Lag Autocorrelation
```python
# Detect weekly + monthly patterns
autocorr_7 = calculate_autocorrelation(sales, lag=7)
autocorr_30 = calculate_autocorrelation(sales, lag=30)

if autocorr_7 > 0.3 or autocorr_30 > 0.3:
    category = SEASONAL
```

#### 3. Detrending per CV Robusto
```python
# Remove trend before calculating CV
trend_line = linear_regression(sales, dates)
residuals = sales - trend_line
cv_detrended = std(residuals) / mean(sales)
```

#### 4. Dashboard Analytics
- Heatmap distribuzione variabilità per categoria prodotto
- Timeline classificazioni: come SKU migrano tra categorie
- Alert: SKU che cambiano improvvisamente categoria (outlier detection)

#### 5. Machine Learning Integration
```python
# Train classifier su feature storiche
features = [cv, autocorr, trend_slope, seasonality_strength]
model = RandomForest()
predicted_category = model.predict(features)
```

## Riferimenti

### Codice Sorgente
- **Auto-classificazione**: [src/domain/auto_variability.py](src/domain/auto_variability.py)
- **Helper integrazione**: [src/domain/models.py](src/domain/models.py) (funzione `auto_classify_variability`)
- **Persistenza**: [src/persistence/csv_layer.py](src/persistence/csv_layer.py) (linee 131-191, 285-338)
- **UI Settings**: [src/gui/app.py](src/gui/app.py) (linee 4120-4145)
- **Testing**: [test_auto_variability.py](test_auto_variability.py)

### Documentazione Correlata
- [DEMAND_VARIABILITY_INTEGRATION.md](DEMAND_VARIABILITY_INTEGRATION.md) - Moltiplicatori safety stock
- [UNCERTAINTY_MODULE.md](UNCERTAINTY_MODULE.md) - Base teorica σ e MAD
- [REPLENISHMENT_POLICY_SUMMARY.md](REPLENISHMENT_POLICY_SUMMARY.md) - Policy CSL

### Letteratura
- **CV for Demand Classification**: Silver, Pyke, & Peterson (1998), "Inventory Management and Production Planning and Scheduling"
- **Autocorrelation in Retail**: Box & Jenkins (1970), "Time Series Analysis: Forecasting and Control"
- **Adaptive Thresholds**: Tukey (1977), "Exploratory Data Analysis" (quartile methods)

---

**Autore**: AI Coding Agent  
**Review**: Pending user validation  
**Versione**: 1.0  
**Last updated**: 6 Febbraio 2026
