# Gestione Giorni Censurati (OOS/Inevasi)

## Problema

In un sistema di riordino automatico, i giorni di **stockout (OOS)** o con **inevasi** creano **dati censurati** che distorcono le previsioni:

1. **OH=0 e vendite=0**: La domanda reale è ignota (il cliente avrebbe comprato, ma non c'era stock).
2. **Inevasi registrati**: Eventi `UNFULFILLED` indicano domanda non soddisfatta.
3. **Impatto negativo**: Includere questi giorni nei calcoli abbassa artificialmente:
   - La previsione della domanda media (μ)
   - La stima dell'incertezza (σ)
   - Il safety stock necessario
   - Le quantità ordinate

**Risultato**: Ciclo vizioso di sottoscorta → ordini insufficienti → più stockout.

## Soluzione Implementata

### 1. Flag "censored" per Giorno/SKU

Funzione `is_day_censored()` in `src/domain/ledger.py`:

```python
def is_day_censored(
    sku: str,
    check_date: date,
    transactions: List[Transaction],
    sales_records: Optional[List[SalesRecord]] = None,
    lookback_days: int = 3,
) -> Tuple[bool, str]:
    """
    Determina se un giorno deve essere censurato (escluso dai calcoli).
    
    Regole:
    1. OH=0 at EOD AND sales=0 → Stockout confermato
    2. Evento UNFULFILLED su check_date o entro lookback_days → Inevaso recente
    
    Returns:
        (is_censored, reason)
    """
```

**Regole di censoring**:
- **Regola 1**: `OH==0` a fine giornata E `sales==0` → Censored (vera rottura di stock)
- **Regola 2**: Evento `UNFULFILLED` nel periodo `check_date - lookback_days` a `check_date` → Censored (inevaso recente)

**Output**: `(True, "OH=0 and sales=0 on 2026-01-15")` oppure `(False, "Normal demand observation")`

### 2. Esclusione da Forecast e Uncertainty

#### Forecast Model (`src/forecast.py`)

```python
def fit_forecast_model(
    history: List[Dict[str, Any]],
    alpha: float = 0.3,
    censored_flags: Optional[List[bool]] = None,
    alpha_boost_for_censored: float = 0.0,
) -> Dict[str, Any]:
    """
    Fit forecast con esclusione giorni censored.
    
    Novità:
    - censored_flags: Lista bool (len == len(history))
    - Filtra history prima di calcolare level/DOW factors
    - alpha_eff = min(0.99, alpha + alpha_boost_for_censored) se censored presenti
    
    Returns:
        {
            "level": float,
            "dow_factors": List[float],
            "n_samples": int,  # Dopo filtering
            "n_censored": int,
            "alpha_eff": float,  # Alpha effettivo (possibilmente boosted)
            ...
        }
    """
```

**Comportamento**:
- Giorni con `censored_flags[i] = True` → esclusi dal calcolo
- `alpha_eff` aumentato se `n_censored > 0` (più reattività per compensare dati mancanti)

#### Uncertainty Estimation (`src/uncertainty.py`)

```python
def calculate_forecast_residuals(
    history: List[Dict[str, Any]],
    forecast_func,
    window_weeks: int = 8,
    censored_flags: Optional[List[bool]] = None,
) -> Tuple[List[float], int]:
    """
    Calcola residui escludendo giorni censored.
    
    Novità:
    - Days con censored_flags[i]=True → NON aggiunti a residuals
    - Returns: (residuals, n_censored_excluded)
    """
```

```python
def estimate_demand_uncertainty(
    history: List[Dict[str, Any]],
    forecast_func,
    censored_flags: Optional[List[bool]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Returns:
        (sigma_day, metadata)
        
    metadata:
        {
            "residuals": List[float],
            "n_residuals": int,
            "n_censored_excluded": int,  # Quanti giorni esclusi
            "method": str
        }
    """
```

**Risultato**: σ_day calcolato solo su giorni "puliti", prevenendo collasso artificiale.

### 3. Logging e Audit Trail

#### Metadati in `compute_order` (`src/replenishment_policy.py`)

```python
def compute_order(
    sku: str,
    order_date: date,
    lane: Lane,
    alpha: float,
    censored_flags: Optional[List[bool]] = None,
    alpha_boost_for_censored: float = 0.05,  # Default: +5% alpha se censored
    ...
) -> Dict[str, Any]:
    """
    Returns breakdown completo con:
        - "alpha": float (alpha originale)
        - "alpha_eff": float (alpha effettivo usato)
        - "n_censored": int (giorni censored in history)
        - "censored_reasons": List[str] (sample di motivazioni)
        - "n_censored_excluded_from_sigma": int
        - "forecast_n_censored": int
        - "forecast_alpha_eff": float
        - "sigma_n_residuals": int
        ... (tutti i parametri esistenti)
    """
```

**Esempio output**:
```json
{
  "sku": "SKU-A",
  "order_final": 120,
  "alpha": 0.95,
  "alpha_eff": 0.98,
  "n_censored": 6,
  "censored_reasons": [
    "2026-01-10 (OOS/inevaso)",
    "2026-01-15 (OOS/inevaso)",
    "... +4 more"
  ],
  "n_censored_excluded_from_sigma": 3,
  "sigma_daily": 12.5,
  "sigma_horizon": 30.6,
  "reorder_point": 185.3,
  "order_raw": 65.3,
  "constraints_applied": ["pack_size: 65.3 → 70"],
  ...
}
```

Questo breakdown può essere salvato in `audit_log.csv` per ogni proposta d'ordine.

## Utilizzo

### Esempio 1: Rilevamento Automatico Censored Days

```python
from datetime import date
from src.domain.ledger import is_day_censored
from src.domain.models import Transaction, EventType, SalesRecord

# Transazioni e vendite
txns = [
    Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
    Transaction(date=date(2026, 1, 15), sku="SKU001", event=EventType.SALE, qty=100),  # OH → 0
]
sales = []  # Nessuna vendita il 15 (demand censored)

# Check
is_censored, reason = is_day_censored("SKU001", date(2026, 1, 15), txns, sales)
print(is_censored)  # True
print(reason)       # "OH=0 and sales=0 on 2026-01-15"
```

### Esempio 2: Forecast con Censored Filtering

```python
from src.forecast import fit_forecast_model, predict

history = [
    {"date": date(2026, 1, 1), "qty_sold": 10},
    {"date": date(2026, 1, 2), "qty_sold": 0},   # OOS
    {"date": date(2026, 1, 3), "qty_sold": 12},
]
censored = [False, True, False]  # Day 2 censored

model = fit_forecast_model(history, censored_flags=censored, alpha_boost_for_censored=0.05)
print(model["n_samples"])       # 2 (only non-censored)
print(model["n_censored"])      # 1
print(model["alpha_eff"])       # 0.35 (0.3 + 0.05 boost)
```

### Esempio 3: Order Computation con Full Metadata

```python
from src.replenishment_policy import compute_order, OrderConstraints
from src.domain.calendar import Lane

# Detect censored days (manual o automatico via is_day_censored)
censored = [False] * 50 + [True] * 3  # Last 3 days OOS

result = compute_order(
    sku="SKU-X",
    order_date=date(2026, 2, 1),
    lane=Lane.STANDARD,
    alpha=0.95,
    on_hand=20,
    pipeline=[],
    constraints=OrderConstraints(pack_size=10, moq=20),
    history=sales_history,
    censored_flags=censored,
    alpha_boost_for_censored=0.05
)

# Audit trail
print(f"Order qty: {result['order_final']}")
print(f"Alpha effective: {result['alpha_eff']}")
print(f"Censored days: {result['n_censored']}")
print(f"Reasons: {result['censored_reasons']}")
```

## Test Coverage

File: `tests/test_censored_days.py`

**Test implementati**:
1. ✅ `test_is_day_censored_oh_zero_sales_zero`: Verifica censoring su OH=0 e sales=0
2. ✅ `test_is_day_censored_unfulfilled_event`: Verifica censoring su UNFULFILLED events
3. ✅ `test_is_day_censored_normal_day`: Giorni normali non censurati
4. ✅ `test_fit_forecast_model_excludes_censored_days`: Forecast esclude giorni censored
5. ✅ `test_fit_forecast_model_alpha_boost_for_censored`: Alpha boost applicato
6. ✅ `test_calculate_forecast_residuals_excludes_censored`: Residui escludono censored
7. ✅ `test_sigma_does_not_collapse_with_censored`: Sigma non collassa con filtering
8. ✅ `test_compute_order_with_censored_days`: Ordini non scendono artificialmente
9. ✅ `test_regression_sigma_collapse_prevented`: Scenario reale 2% OOS, sigma stabile

**Eseguire i test**:
```bash
python -m pytest tests/test_censored_days.py -v
```

**Output atteso**:
```
tests/test_censored_days.py::test_is_day_censored_oh_zero_sales_zero PASSED
tests/test_censored_days.py::test_is_day_censored_unfulfilled_event PASSED
tests/test_censored_days.py::test_sigma_does_not_collapse_with_censored PASSED
tests/test_censored_days.py::test_regression_sigma_collapse_prevented PASSED
...
```

## Configurazione Raccomandata

Per sistemi di produzione:

```python
# Settings.json (o parametri globali)
CENSORED_LOOKBACK_DAYS = 3  # Giorni di lookback per UNFULFILLED
ALPHA_BOOST_FOR_CENSORED = 0.05  # +5% alpha se censored present
DEFAULT_ALPHA = 0.95  # CSL target
```

**Workflow completo**:
1. Per ogni SKU, calcola `censored_flags` usando `is_day_censored()`
2. Passa `censored_flags` a `compute_order()`
3. Sistema automaticamente:
   - Esclude giorni censored da forecast
   - Esclude giorni censored da sigma
   - Applica alpha boost
   - Logga metadata completo

## Benefici

✅ **Previsioni accurate**: Domanda stimata su dati reali, non censurati  
✅ **Sigma stabile**: Nessun collasso artificiale dovuto a OOS  
✅ **Safety stock adeguato**: Quantità ordinate riflettono vera variabilità  
✅ **Audit trail completo**: Motivazioni e breakdown per ogni decisione  
✅ **Configurabile**: Alpha boost e lookback regolabili per business logic  
✅ **Test robusti**: Coverage completa su scenari critici

## Limitazioni e Future Work

**Limitazioni attuali**:
- Censoring binario (True/False); potrebbe essere esteso a "peso" (0-1)
- Alpha boost lineare; potrebbe essere funzione di % censored
- Lookback fisso (3 giorni); potrebbe essere SKU-specific

**Miglioramenti futuri**:
1. **Censoring parziale**: Stimare domanda latente da inevasi parziali
2. **Imputation avanzata**: Usare ML per inferire demand su giorni OOS
3. **Dashboard**: Visualizzare giorni censored per SKU su timeline
4. **Alert**: Notifica se % censored > soglia (es. >5% → review SKU)

## References

- **Censored Data Theory**: Statistical methods for censored observations (Tobit models)
- **Inventory Management**: Silver-Pyke-Peterson, "Inventory and Production Management in Supply Chains"
- **Robust Statistics**: Huber, "Robust Statistics" (MAD, Winsorization)

---

**Last Updated**: February 2, 2026  
**Author**: Desktop Order System Team  
**Status**: ✅ Implementato e testato
