# Expiry Threshold Configuration Feature

## Panoramica
Reso configurabile il color-coding della tabella lotti in scadenza nel tab ⏰ Scadenze.
I threshold per le soglie CRITICO (arancione) e ATTENZIONE (giallo) sono ora modificabili dall'utente tramite il tab Impostazioni.

## Funzionalità Implementate

### 1. Settings Persistenti
- **File**: `data/settings.json`
- **Sezione aggiunta**: `expiry_alerts`
- **Parametri configurabili**:
  * `critical_threshold_days`: Giorni alla scadenza per stato CRITICO 🔴 (default: 7)
  * `warning_threshold_days`: Giorni alla scadenza per stato ATTENZIONE 🟡 (default: 14)

### 2. Interfaccia Utente
- **Tab**: ⚙️ Impostazioni → Sezione "⏰ Soglie Alert Scadenze"
- **Controlli**:
  * Spinbox per "Giorni CRITICO (arancione)" (range: 1-30 giorni)
  * Spinbox per "Giorni ATTENZIONE (giallo)" (range: 1-60 giorni)
- **Pulsanti**: Salva Impostazioni, Ripristina Default, Ricarica

### 3. Aggiornamento Dinamico
- Modifica settings → Salva → Refresh automatico tab Scadenze
- Colori aggiornati immediatamente senza riavvio applicazione

## File Modificati

### `data/settings.json`
Aggiunta sezione:
```json
"expiry_alerts": {
  "critical_threshold_days": {
    "value": 7,
    "description": "Giorni scadenza per stato CRITICO (arancione)"
  },
  "warning_threshold_days": {
    "value": 14,
    "description": "Giorni scadenza per stato ATTENZIONE (giallo)"
  }
}
```

### `src/gui/app.py`
**Righe 76-79**: Caricamento threshold da settings nell'`__init__`
```python
settings = self.csv_layer.read_settings()
self.expiry_critical_days = settings.get("expiry_alerts", {}).get("critical_threshold_days", {}).get("value", 7)
self.expiry_warning_days = settings.get("expiry_alerts", {}).get("warning_threshold_days", {}).get("value", 14)
```

**Righe 3257-3262**: Uso threshold dinamici in `_refresh_expiry_alerts()`
```python
# Status based on days left (use configurable thresholds)
if days_left <= self.expiry_critical_days:
    status = "🔴 CRITICO"
    tag = "critical"
elif days_left <= self.expiry_warning_days:
    status = "🟡 ATTENZIONE"
    tag = "warning"
```

**Righe 4960-4984**: Sezione UI per Expiry Alerts in Settings tab
- Aggiunta `CollapsibleFrame` "⏰ Soglie Alert Scadenze"
- 2 controlli per threshold (critical, warning)

**Righe 5138-5139**: Mappatura parametri in `_refresh_settings_tab()`
```python
"expiry_critical_threshold_days": ("expiry_alerts", "critical_threshold_days"),
"expiry_warning_threshold_days": ("expiry_alerts", "warning_threshold_days"),
```

**Righe 5107-5108**: Stessa mappatura in `_save_settings()`

**Righe 5164-5169**: Aggiornamento thresholds e refresh tab dopo salvataggio
```python
# Update expiry thresholds if changed
self.expiry_critical_days = settings.get("expiry_alerts", {}).get("critical_threshold_days", {}).get("value", 7)
self.expiry_warning_days = settings.get("expiry_alerts", {}).get("warning_threshold_days", {}).get("value", 14)

# Refresh expiry tab with new thresholds
self._refresh_expiry_alerts()
```

### `test_expiry_thresholds.py` (NUOVO)
Test completo che verifica:
1. Presenza sezione `expiry_alerts` in settings
2. Lettura valori default (7, 14)
3. Modifica e persistenza (5, 10)
4. Reset ai default
5. Classificazione corretta status lotti (CRITICO/ATTENZIONE/OK)

## Testing

### Test Automatico
```bash
python test_expiry_thresholds.py
```

Output atteso:
```
✓ Critical threshold: 7 days
✓ Warning threshold: 14 days
✓ Changes persisted correctly
✓ Reset to default values (7, 14)
✓ All lot status classifications correct!
ALL TESTS PASSED ✓
```

### Test Manuale (GUI)
1. Avvia app: `python main.py`
2. Vai a tab ⚙️ Impostazioni
3. Espandi sezione "⏰ Soglie Alert Scadenze"
4. Modifica valori (es: critical=5, warning=10)
5. Clicca "💾 Salva Impostazioni"
6. Vai a tab ⏰ Scadenze
7. Verifica che i colori riflettano i nuovi threshold:
   - Lotti con ≤5 giorni → 🔴 CRITICO
   - Lotti con ≤10 giorni → 🟡 ATTENZIONE
   - Lotti con >10 giorni → 🟢 OK

## Logica di Color-Coding

| Giorni alla Scadenza | Status | Colore | Emoji |
|---------------------|--------|--------|-------|
| < 0 (scaduto) | SCADUTO | Rosso scuro | ❌ |
| 0 to `critical_threshold_days` | CRITICO | Arancione | 🔴 |
| `critical_threshold_days` to `warning_threshold_days` | ATTENZIONE | Giallo | 🟡 |
| > `warning_threshold_days` | OK | Verde/Normale | 🟢 |

## Esempi d'Uso

### Scenari Tipici
1. **Prodotti freschi (latticini, carne)**: critical=3, warning=5 (ciclo breve)
2. **Prodotti confezionati**: critical=7, warning=14 (default)
3. **Prodotti a lunga conservazione**: critical=14, warning=30 (margine ampio)

### Validazione Range
- **Critical**: 1-30 giorni (max 1 mese)
- **Warning**: 1-60 giorni (max 2 mesi)
- **Constraint**: Warning deve essere >= Critical (non forzato via codice, ma logico)

## Note Implementative

1. **Fallback graceful**: Se settings.json mancante o sezione assente → default (7, 14)
2. **Refresh automatico**: Salvataggio settings → chiamata `_refresh_expiry_alerts()` → aggiornamento immediato tabella
3. **No restart required**: Cambio threshold applicato immediatamente senza riavvio app
4. **Persistence**: Modifiche salvate in `data/settings.json` (formato JSON leggibile)

## Benefici

1. **Flessibilità**: Adattamento a diverse tipologie di prodotto
2. **Usabilità**: Configurazione tramite GUI (no modifica file manuale)
3. **Visibilità**: Utente controlla quando un lotto diventa "critico"
4. **Scalabilità**: Facile aggiungere nuovi threshold (es: "Urgente" < 3 giorni)

## Limitazioni Attuali

1. **Validazione mancante**: Non controlla che warning >= critical (possibile invertire accidentalmente)
2. **No preset**: Non esistono profili predefiniti ("Fresh", "Packaged", "Long-shelf")
3. **UI locale**: Tab Settings non mostra preview colori in tempo reale

## Possibili Evoluzioni

1. **Validazione inter-threshold**: Impedire critical > warning
2. **Preset profiles**: Dropdown con "Latticini", "Secco", "Surgelato" → valori pre-configurati
3. **Live preview**: Mostrare barra colorata nella Settings tab (visual feedback)
4. **Per-SKU override**: Permettere threshold specifici per SKU (es: "formaggio → critical=2")
5. **Alert automation**: Email/notifica quando lotto entra in stato CRITICO

---
**Implementato**: Gennaio 2026  
**Status**: Completato e testato  
**Test Coverage**: Settings persistence + UI + logic classification
