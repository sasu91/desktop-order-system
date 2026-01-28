# Audit Trail - Documentazione

## Panoramica

Il sistema di Audit Trail traccia tutte le operazioni critiche e modifiche ai dati, fornendo una timeline completa per ogni SKU.

## Funzionalità Implementate

### 1. Timeline Eventi per SKU ✅

**Ubicazione**: Tab Stock → Pannello laterale destro

- **Selezione SKU**: Click su una riga nella tabella Stock mostra automaticamente la timeline nel pannello laterale
- **Visualizzazione**: Timeline ordinata cronologicamente (più recenti prima)
- **Contenuto**:
  - **Audit Log**: Modifiche SKU, export, operazioni critiche
  - **Ledger Events**: Tutte le transazioni (ORDER, RECEIPT, SALE, WASTE, ADJUST, ecc.)

**Colonne Timeline**:
- `Timestamp`: Data/ora operazione (YYYY-MM-DD HH:MM:SS per audit, YYYY-MM-DD per ledger)
- `Event`: Tipo evento (SKU_CREATE, SKU_EDIT, SKU_DELETE, EXPORT, ORDER, RECEIPT, ecc.)
- `Qty`: Quantità (per eventi ledger)
- `Note`: Dettagli operazione

### 2. Storico Modifiche SKU ✅

**Tracking automatico per**:
- **SKU_CREATE**: Creazione nuovo SKU
  - Dettagli: Description, EAN
- **SKU_EDIT**: Modifica SKU esistente
  - Dettagli: Campo modificato (Code, Description, EAN) con valori before → after
- **SKU_DELETE**: Eliminazione SKU
  - Dettagli: SKU code eliminato

**Formato audit entry**:
```csv
timestamp,operation,sku,details,user
2026-01-28 14:30:00,SKU_CREATE,PROD001,"Created SKU: Widget (EAN: 1234567890123)",system
2026-01-28 15:45:12,SKU_EDIT,PROD001,"Updated SKU: Description: Widget → Super Widget",system
```

### 3. Log Esportazioni ✅

**Tracking automatico per tutte le esportazioni CSV**:
- Stock Snapshot
- Ledger (Transactions)
- SKU List
- Order Logs
- Receiving Logs

**Formato**:
```csv
2026-01-28 16:20:00,EXPORT,,"Stock snapshot exported (50 SKUs, AsOf 2026-01-28)",system
```

### 4. Panel Contextual nel Stock Tab ✅

**Design**:
- **Split layout**: Tabella stock a sinistra, audit timeline a destra
- **Auto-update**: Selezione SKU → aggiorna timeline automaticamente
- **Refresh manual**: Pulsante 🔄 per ricaricare timeline
- **Scroll indipendente**: Timeline scrollabile per storico lungo

**UI Components**:
```
┌─────────────────────────────────────┬──────────────────────────┐
│ Stock Table (AsOf Date)            │ 📋 Audit Timeline        │
│ ┌─────────────────────────────────┐│ Selected: PROD001 🔄    │
│ │ SKU  │ Desc │ On Hand │ On Order││ ┌────────────────────────┤
│ │PROD001│Widget│   100   │   50   ││ │ === AUDIT LOG ===      │
│ │PROD002│Gadget│    75   │    0   ││ │ 2026-01-28 15:45:12   │
│ │...   │...   │   ...   │   ... ││ │ SKU_EDIT │ │ Updated... │
│ └─────────────────────────────────┘│ │ 2026-01-28 14:30:00   │
│                                    │ │ SKU_CREATE │ │ Created..│
│                                    │ │ === LEDGER EVENTS === │
│                                    │ │ 2026-01-27 │ ORDER │+50│
│                                    │ │ 2026-01-25 │ SALE │-10│
│                                    │ └────────────────────────┤
└─────────────────────────────────────┴──────────────────────────┘
```

## Architettura Tecnica

### Event Types Estesi

```python
class EventType(Enum):
    # ... existing events ...
    SKU_EDIT = "SKU_EDIT"      # SKU metadata change (no stock impact)
    EXPORT_LOG = "EXPORT_LOG"  # Export operation log (no stock impact)
```

### Dataclass AuditLog

```python
@dataclass(frozen=True)
class AuditLog:
    timestamp: str      # ISO format: YYYY-MM-DD HH:MM:SS
    operation: str      # SKU_CREATE, SKU_EDIT, SKU_DELETE, EXPORT
    sku: Optional[str]  # Affected SKU (None for global operations)
    details: str        # Human-readable description
    user: str = "system"  # Operator/user (default: system)
```

### CSV Schema (audit_log.csv)

```csv
timestamp,operation,sku,details,user
2026-01-28 14:30:00,SKU_CREATE,PROD001,"Created SKU: Widget",system
2026-01-28 15:45:12,SKU_EDIT,PROD001,"Updated SKU: Description changed",system
2026-01-28 16:20:00,EXPORT,,"Stock snapshot exported (50 SKUs)",system
```

### CSVLayer Methods

**Logging**:
```python
csv_layer.log_audit(
    operation="SKU_EDIT",
    details="Updated SKU: Code: OLD001 → NEW001",
    sku="NEW001",
    user="system"
)
```

**Retrieval**:
```python
# Get all audit logs for a SKU
logs = csv_layer.read_audit_log(sku="PROD001", limit=50)

# Get all logs (no filter)
all_logs = csv_layer.read_audit_log()
```

## Utilizzo nel GUI

### Admin Tab (SKU Management)

**CREATE**:
```python
self.csv_layer.write_sku(new_sku)
self.csv_layer.log_audit(
    operation="SKU_CREATE",
    details=f"Created SKU: {description} (EAN: {ean or 'N/A'})",
    sku=sku_code,
)
```

**UPDATE**:
```python
self.csv_layer.update_sku(old_code, new_code, description, ean)
changes = ["Code: OLD → NEW", "Description: ..."]
self.csv_layer.log_audit(
    operation="SKU_EDIT",
    details=f"Updated SKU: {', '.join(changes)}",
    sku=new_code,
)
```

**DELETE**:
```python
self.csv_layer.delete_sku(sku_code)
self.csv_layer.log_audit(
    operation="SKU_DELETE",
    details=f"Deleted SKU: {sku_code}",
    sku=sku_code,
)
```

### Export Operations

**Esempio**:
```python
# After successful export
self.csv_layer.log_audit(
    operation="EXPORT",
    details=f"Stock snapshot exported ({len(sku_ids)} SKUs, AsOf {asof_date})",
    sku=None,
)
```

### Stock Tab (Timeline Display)

**Event handlers**:
```python
def _on_stock_select(self, event):
    # Get selected SKU from treeview
    sku_code = item["values"][0]
    self.selected_sku_for_audit = sku_code
    self._refresh_audit_timeline()

def _refresh_audit_timeline(self):
    # Get transactions for SKU
    transactions = [t for t in all_txns if t.sku == selected_sku]
    
    # Get audit logs for SKU
    audit_logs = csv_layer.read_audit_log(sku=selected_sku, limit=50)
    
    # Display combined timeline
```

## Best Practices

### 1. Deterministic Logging
- **Timestamp**: Auto-generated con `datetime.now()` (solo per audit, non per ledger)
- **Details**: Human-readable, specifico (non generico)
- **User**: Default "system", estendibile per multi-user

### 2. Performance
- **Limit**: Usare `limit` parameter per timeline lunghe (default: 50 entries)
- **Filter**: Sempre filtrare per SKU quando possibile
- **Index**: CSV ordinato per timestamp desc (già implementato)

### 3. Privacy & Security
- **No sensitive data**: Evitare dati sensibili nei details
- **Immutable**: AuditLog è frozen (non modificabile)
- **Append-only**: Nessuna modifica/cancellazione di log esistenti

## Testing

### Test Coverage

```bash
pytest tests/test_audit_trail.py -v
```

**Test Cases**:
- ✅ `test_log_audit_creates_entry`: Creazione entry
- ✅ `test_log_audit_filter_by_sku`: Filtro per SKU
- ✅ `test_log_audit_sorted_by_timestamp_desc`: Ordinamento
- ✅ `test_log_audit_limit`: Limite record
- ✅ `test_audit_log_empty_sku`: Entry senza SKU (export)
- ✅ `test_sku_edit_event_exists`: Nuovo EventType
- ✅ `test_audit_log_immutable`: Immutabilità dataclass

## Roadmap Futuro

### Possibili Miglioramenti

1. **Multi-User Support**
   - Autenticazione utenti
   - Tracciare username effettivo invece di "system"

2. **Export Audit Log**
   - Esportare timeline completa come PDF/CSV
   - Filtri avanzati (date range, event type)

3. **Dashboard Integration**
   - KPI: Operazioni per tipo/giorno
   - Chart: Audit activity trend

4. **Search & Filter**
   - Ricerca full-text nei details
   - Filtro per operation type
   - Date range selector

5. **Retention Policy**
   - Auto-archivio log >1 anno
   - Compressione log storici

---

**Status**: ✅ Implementato e testato  
**Data**: Gennaio 2026  
**Version**: 1.0
