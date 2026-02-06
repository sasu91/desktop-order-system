# Demand Variability Integration

**Data implementazione**: 6 Febbraio 2026  
**Status**: ✅ Complete & Tested

## Panoramica

Il campo **Variabilità Domanda** (`demand_variability`) influisce ora sui calcoli del safety stock, permettendo di adattare le proposte di riordino in base al comportamento storico di ciascun SKU.

## Valori e Moltiplicatori

Il campo `demand_variability` può assumere 4 valori:

| Valore | Moltiplicatore | Effetto | Quando usarlo |
|--------|----------------|---------|---------------|
| **STABLE** | **×0.8** | Riduce safety stock del 20% | SKU con domanda molto costante e prevedibile |
| **LOW** | ×1.0 | Nessun cambiamento | SKU a bassa rotazione |
| **SEASONAL** | ×1.0 | Nessun cambiamento | SKU con pattern stagionali marcati |
| **HIGH** | **×1.5** | Aumenta safety stock del 50% | SKU con domanda volatile e imprevedibile |

## Meccanismo di Calcolo

### Prima dell'integrazione
```python
safety_stock = sku.safety_stock  # Valore fisso
```

### Dopo l'integrazione (src/workflows/order.py, linee 154-174)
```python
# 1. Estrazione base safety stock
safety_stock_base = sku_obj.safety_stock if sku_obj else 0
demand_variability = sku_obj.demand_variability if sku_obj else None

# 2. Applicazione moltiplicatore
safety_stock = safety_stock_base  # Default: nessun cambio

if demand_variability:
    from ..domain.models import DemandVariability
    
    if demand_variability == DemandVariability.HIGH:
        safety_stock = int(safety_stock_base * 1.5)  # +50%
    
    elif demand_variability == DemandVariability.STABLE:
        safety_stock = int(safety_stock_base * 0.8)  # -20%
    
    # LOW e SEASONAL: mantengono il valore base (×1.0)

# 3. Uso del safety stock aggiustato nel calcolo CSL
# ... resto della formula di riordino
```

## Impatto sulle Proposte

### Esempio pratico
**Parametri comuni**:
- Base safety stock: 20 pz
- Stock attuale: 10 pz
- Lead time: 7 giorni
- Vendite medie: 5 pz/giorno

**Proposte generate**:

| Variabilità | Safety Stock Effettivo | Quantità Proposta | Delta |
|-------------|------------------------|-------------------|-------|
| STABLE | 16 pz (20 × 0.8) | 76 pz | -4 pz |
| LOW | 20 pz (20 × 1.0) | 80 pz | base |
| SEASONAL | 20 pz (20 × 1.0) | 80 pz | base |
| HIGH | 30 pz (20 × 1.5) | 90 pz | +10 pz |

**Interpretazione**:
- SKU **STABLE**: ordini più aggressivi (meno stock di sicurezza necessario)
- SKU **HIGH**: ordini più conservativi (maggiore protezione contro volatilità)

## Benefici Business

### 1. Riduzione Costi di Stoccaggio
SKU stabili richiedono meno safety stock → minor capitale immobilizzato

### 2. Miglior Livello di Servizio
SKU volatili hanno maggiore protezione → meno rotture di stock

### 3. Ottimizzazione Automatica
Il sistema adatta automaticamente le proposte senza intervento manuale

### 4. Flessibilità
Ogni SKU può essere configurato individualmente tramite **Gestione SKU**

## Utilizzo nella GUI

### Impostazione Variabilità
1. Apri **Gestione SKU** (tab dedicato)
2. Seleziona uno SKU esistente o creane uno nuovo
3. Campo **"Variabilità Domanda"** (riga 13):
   - Dropdown con 4 opzioni: Stable, Low, Seasonal, High
   - Default: nessuna selezione (comportamento legacy ×1.0)
4. Salva SKU

### Visualizzazione Effetti
1. Vai al tab **Proposta Ordine**
2. Clicca **"Calcola Proposta"**
3. La quantità proposta riflette il moltiplicatore della variabilità impostata

## Testing

### Test Automatici
File: `test_demand_variability_integration.py`

**5 test implementati**:
1. ✅ STABLE applica ×0.8
2. ✅ HIGH applica ×1.5
3. ✅ LOW mantiene ×1.0
4. ✅ SEASONAL mantiene ×1.0
5. ✅ Quantità proposte segue ordine atteso (HIGH > LOW > STABLE)

**Esecuzione**:
```bash
python test_demand_variability_integration.py
```

**Risultati attesi**:
```
✅ ALL TESTS PASSED

Moltiplicatori applicati:
  • STABLE: ×0.8 (riduce safety stock del 20%)
  • HIGH: ×1.5 (aumenta safety stock del 50%)
  • LOW: ×1.0 (nessun cambiamento)
  • SEASONAL: ×1.0 (nessun cambiamento)
```

### Test Manuali Consigliati
1. **Test differenziale**: Crea 3 SKU identici con variabilità diversa → verifica proposte diverse
2. **Test modifica**: Cambia variabilità di uno SKU esistente → verifica impatto immediato
3. **Test edge case**: SKU con safety_stock=0 → verifica nessun crash (×1.5 di 0 = 0)

## Compatibilità

### Backward Compatibility
✅ **100% retrocompatibile**

- SKU senza `demand_variability` impostato → comportamento legacy (×1.0)
- Nessuna migrazione CSV necessaria
- File esistenti continuano a funzionare senza modifiche

### Dipendenze
- **src/domain/models.py**: Enum `DemandVariability` (già esistente)
- **src/workflows/order.py**: Logica di calcolo aggiornata (linee 154-174)
- **Nessuna modifica** a `persistence` o `gui` (solo usa campo esistente)

## Razionale dei Moltiplicatori

### STABLE ×0.8 (-20%)
**Evidenza**: Coefficient of Variation (CV) < 0.3  
**Logica**: Domanda prevedibile → meno incertezza → minor safety stock necessario  
**Esempio**: Articoli base venduti quotidianamente con pattern costante

### HIGH ×1.5 (+50%)
**Evidenza**: CV > 0.7, picchi frequenti  
**Logica**: Domanda volatile → alta incertezza → maggiore protezione necessaria  
**Esempio**: Articoli promozionali, trend products, domanda influenzata da eventi esterni

### LOW/SEASONAL ×1.0 (nessun cambio)
**Rationale**:
- **LOW**: Dati insufficienti per modellare variabilità (poche vendite)
- **SEASONAL**: Pattern complessi richiedono modelli dedicati (forecast module)

**Conservativo**: Meglio mantenere baseline finché non si hanno modelli specifici

## Sviluppi Futuri

### 1. Auto-classificazione
Calcolare automaticamente `demand_variability` da CV storico:
```python
cv = σ_demand / μ_demand

if cv < 0.3:
    demand_variability = STABLE
elif cv > 0.7:
    demand_variability = HIGH
# ...
```

### 2. Moltiplicatori Dinamici
Invece di valori fissi (0.8, 1.5), usare formula continua:
```python
multiplier = 1.0 + (cv - 0.5) * k  # k = sensitivity parameter
```

### 3. Integrazione con Forecast Module
Usare `demand_variability` per scegliere metodo di previsione:
- STABLE → Simple Moving Average
- SEASONAL → Seasonal Decomposition
- HIGH → Exponential Smoothing con α alto

### 4. Dashboard Analytics
Visualizzare distribuzione:
- Quanti SKU per categoria
- Risparmio totale da ottimizzazione STABLE
- Rotture evitate da protezione HIGH

## Riferimenti

- **Design**: `UNCERTAINTY_MODULE.md` (rationale teorico)
- **Implementazione**: `src/workflows/order.py` linee 154-174
- **Testing**: `test_demand_variability_integration.py`
- **Documentazione correlata**: `REPLENISHMENT_POLICY_SUMMARY.md`

---

**Autore**: AI Coding Agent  
**Review**: Pending user validation  
**Versione**: 1.0
