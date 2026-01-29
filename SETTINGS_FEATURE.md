# Funzionalità Impostazioni Motore di Riordino

## Panoramica

È stata aggiunta una nuova sezione **Impostazioni** (tab "⚙️ Impostazioni") per configurare i parametri globali del motore di riordino automatico con persistenza su file JSON.

## Funzionalità Implementate

### 1. Persistenza Impostazioni (JSON)
- **File**: `data/settings.json`
- **Auto-creazione**: Il file viene creato automaticamente al primo avvio con valori predefiniti
- **Formato**: JSON strutturato con sezione `reorder_engine`

### 2. Parametri Configurabili

Ogni parametro ha:
- **Valore**: Il valore numerico o testuale del parametro
- **Auto-applica**: Flag per applicare automaticamente il valore ai nuovi SKU

#### Parametri disponibili:

| Parametro | Descrizione | Tipo | Range | Default |
|-----------|-------------|------|-------|---------|
| Lead Time | Tempo di attesa dall'ordine alla ricezione | Intero | 1-90 giorni | 7 |
| Stock Minimo | Soglia minima di sicurezza | Intero | 0-1000 unità | 10 |
| Giorni di Copertura | Giorni di vendite da coprire con ordini | Intero | 1-90 giorni | 14 |
| MOQ | Quantità Minima Ordine (multiplo) | Intero | 1-1000 | 1 |
| Stock Massimo | Limite massimo di stock desiderato | Intero | 1-10000 unità | 999 |
| Punto di Riordino | Livello che attiva il riordino | Intero | 0-1000 unità | 10 |
| Variabilità Domanda | Livello di variabilità della domanda | Choice | STABLE/MODERATE/HIGH | STABLE |

### 3. Tab Impostazioni

**Posizione**: Tab "⚙️ Impostazioni" nella barra principale

**Funzionalità**:
- Modifica valori parametri con Spinbox (numeri) o Combobox (scelte)
- Checkbox "Auto-applica ai nuovi SKU" per ogni parametro
- Pulsante "💾 Salva Impostazioni" per persistere le modifiche
- Pulsante "↺ Ripristina Default" per resettare ai valori predefiniti
- Pulsante "🔄 Ricarica" per ricaricare dal file

### 4. Auto-applicazione ai Nuovi SKU

Quando si crea un nuovo SKU:
1. Il sistema controlla `settings.json`
2. Per ogni parametro con `auto_apply_to_new_sku: true`
3. Se il valore dello SKU è il default (es. moq=1, lead_time=7)
4. Viene sostituito con il valore dalle impostazioni

**Esempio**:
```json
{
  "reorder_engine": {
    "moq": {
      "value": 5,
      "auto_apply_to_new_sku": true
    },
    "lead_time_days": {
      "value": 14,
      "auto_apply_to_new_sku": true
    }
  }
}
```

Se creo un nuovo SKU con `moq=1` (default), verrà automaticamente impostato a `moq=5`.

### 5. Integrazione con Tab Ordini

**Prima delle modifiche**:
- Valori hardcoded: min_stock=10, days_cover=30, lead_time=7

**Dopo le modifiche**:
- I campi nella tab "Ordini" vengono inizializzati con i valori da `settings.json`
- L'utente può sovrascriverli temporaneamente per una generazione specifica
- Le impostazioni permanenti si salvano dalla tab "Impostazioni"

### 6. Audit Trail

Ogni modifica alle impostazioni viene registrata in `audit_log.csv`:
- `SETTINGS_UPDATE`: Quando si salvano le impostazioni
- `SETTINGS_RESET`: Quando si ripristinano i default

## File Modificati

### `src/persistence/csv_layer.py`
- Aggiunto import `json`
- Metodo `read_settings()`: Legge settings.json o crea con default
- Metodo `write_settings()`: Scrive settings.json
- Metodo `get_default_sku_params()`: Estrae parametri per auto-applicazione
- Modificato `write_sku()`: Applica automaticamente i default da settings

### `src/workflows/order.py`
- Modificato `__init__()`: `lead_time_days` opzionale, legge da settings se None

### `src/gui/app.py`
- Aggiunto tab `settings_tab`
- Metodo `_build_settings_tab()`: Costruisce UI impostazioni
- Metodo `_refresh_settings_tab()`: Carica valori dal file
- Metodo `_save_settings()`: Salva modifiche su file
- Metodo `_reset_settings_to_default()`: Ripristina default
- Modificato `_build_order_tab()`: Legge valori iniziali da settings
- Modificato `_generate_all_proposals()`: Usa settings come fallback
- Aggiornato `_refresh_all()`: Include refresh settings tab

## Struttura settings.json

```json
{
  "reorder_engine": {
    "lead_time_days": {
      "value": 7,
      "auto_apply_to_new_sku": true
    },
    "min_stock": {
      "value": 10,
      "auto_apply_to_new_sku": true
    },
    "days_cover": {
      "value": 14,
      "auto_apply_to_new_sku": true
    },
    "moq": {
      "value": 1,
      "auto_apply_to_new_sku": true
    },
    "max_stock": {
      "value": 999,
      "auto_apply_to_new_sku": true
    },
    "reorder_point": {
      "value": 10,
      "auto_apply_to_new_sku": true
    },
    "demand_variability": {
      "value": "STABLE",
      "auto_apply_to_new_sku": true
    }
  }
}
```

## Testing

Eseguire il test con:
```bash
python test_settings.py
```

Il test verifica:
1. Lettura settings
2. Estrazione default params
3. Modifica e salvataggio
4. Auto-applicazione ai nuovi SKU
5. Reset ai default

## Note Implementative

### Logica Auto-applicazione

L'auto-applicazione funziona confrontando il valore dello SKU con i "default hardcoded" del modello:
- Se `sku.moq == 1` → sostituisci con valore da settings
- Se `sku.lead_time_days == 7` → sostituisci con valore da settings
- Se `sku.demand_variability == STABLE` → sostituisci con valore da settings

Questo permette di distinguere tra:
- Valori **intenzionalmente** impostati dall'utente (vengono mantenuti)
- Valori **di default** non modificati (vengono sostituiti con settings)

### Estensibilità

Per aggiungere nuovi parametri:
1. Aggiungere in `default_settings` in `read_settings()`
2. Aggiungere nella lista `parameters` in `_build_settings_tab()`
3. Aggiornare logica in `write_sku()` se necessario

## Benefici

✅ **Configurabilità**: I parametri del motore sono modificabili senza toccare il codice  
✅ **Persistenza**: Le impostazioni sopravvivono ai riavvii  
✅ **Auto-applicazione**: I nuovi SKU ereditano automaticamente i parametri aziendali  
✅ **Flessibilità**: Ogni parametro può essere auto-applicato o no  
✅ **Tracciabilità**: Audit log delle modifiche alle impostazioni  
✅ **UI intuitiva**: Interfaccia grafica chiara con descrizioni e validazioni  

## Limitazioni Attuali

- I parametri per SKU esistenti **non** vengono aggiornati automaticamente (solo nuovi SKU)
- Per aggiornare SKU esistenti: usare funzionalità "Admin" → modifica SKU manualmente
- Possibile estensione futura: "Applica settings a tutti gli SKU esistenti" (con conferma)
