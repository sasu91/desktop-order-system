# Holiday GUI Management Feature

## Overview
The Settings tab now includes a **📅 Calendario e Festività** section that allows users to manage holidays and closures directly from the GUI, without manually editing `holidays.json`.

## Feature Details

### Location
**⚙️ Impostazioni** tab → **📅 Calendario e Festività** section (collapsible)

### Capabilities

#### 1. View Holidays
- Table displays all configured holidays with columns:
  - **Nome**: Holiday name
  - **Tipo**: Type (single, range, fixed)
  - **Date**: Date or date range
  - **Ambito**: Scope (logistics, orders, receipts)
  - **Effetto**: Effect (no_order, no_receipt, both)

#### 2. Add Holiday
Click **➕ Aggiungi Festività** to open dialog with fields:

**Required Fields:**
- **Nome festività**: Descriptive name (e.g., "Natale 2026", "Ferie Estive")
- **Tipo**: Holiday type
  - `single`: Single date (e.g., 2026-12-25)
  - `range`: Date range (e.g., 2026-08-10 → 2026-08-25)
  - `fixed`: Fixed day of month (e.g., day 1 = first of every month)
- **Ambito**: Scope
  - `logistics`: Affects both orders and receipts
  - `orders`: Only affects order placement
  - `receipts`: Only affects receiving
- **Effetto**: Effect type
  - `no_order`: Blocks order placement
  - `no_receipt`: Blocks receiving
  - `both`: Blocks both operations

**Date Parameters (dynamic based on type):**
- **Single**: Date field (YYYY-MM-DD format)
- **Range**: Start date + End date (YYYY-MM-DD format)
- **Fixed**: Day of month (1-31)

**Validation:**
- Name required
- Date format must be YYYY-MM-DD
- Range start must be ≤ end
- Day must be 1-31
- All dates validated before save

#### 3. Edit Holiday
- Select holiday in table
- Click **✏️ Modifica** (or double-click row)
- Opens same dialog as Add, pre-filled with current values
- Save updates the selected holiday

#### 4. Delete Holiday
- Select holiday in table
- Click **🗑️ Elimina**
- Confirmation dialog appears
- Holiday removed from `holidays.json`

#### 5. Refresh
- Click **🔄 Ricarica** to reload table from `holidays.json`
- Useful if file was edited externally

### Backend Integration

#### CSV Layer Methods (csv_layer.py)
```python
# Read all holidays
holidays = csv_layer.read_holidays()

# Add new holiday
csv_layer.add_holiday({
    "name": "Natale 2026",
    "scope": "logistics",
    "effect": "both",
    "type": "single",
    "params": {"date": "2026-12-25"}
})

# Update holiday by index
csv_layer.update_holiday(index, updated_holiday_dict)

# Delete holiday by index
csv_layer.delete_holiday(index)
```

#### Calendar Reload
After any holiday change (add/edit/delete), the system:
1. Saves changes to `data/holidays.json`
2. Refreshes the table display
3. Triggers `_reload_calendar()` (loads updated `HolidayCalendar`)

**Note**: `OrderWorkflow` and `ReceivingWorkflow` load calendar config during operation, so changes take effect immediately on next order/receipt.

### File Format
Holidays are stored in `data/holidays.json`:

```json
{
  "holidays": [
    {
      "name": "Natale 2026",
      "scope": "logistics",
      "effect": "both",
      "type": "single",
      "params": {
        "date": "2026-12-25"
      }
    },
    {
      "name": "Ferie Estive",
      "scope": "logistics",
      "effect": "no_receipt",
      "type": "range",
      "params": {
        "start": "2026-08-10",
        "end": "2026-08-25"
      }
    },
    {
      "name": "Chiusura Mensile",
      "scope": "orders",
      "effect": "no_order",
      "type": "fixed",
      "params": {
        "day": 1
      }
    }
  ]
}
```

### Automatic Italian Holidays
**Important**: The 12 official Italian public holidays (including Easter calculation) are **always included automatically** by the holiday system, even if not in `holidays.json`:
- Capodanno (1 Jan)
- Epifania (6 Jan)
- Lunedì di Pasqua (Easter Monday, calculated)
- Festa della Liberazione (25 Apr)
- Festa dei Lavoratori (1 May)
- Festa della Repubblica (2 Jun)
- Ferragosto (15 Aug)
- Ognissanti (1 Nov)
- Immacolata Concezione (8 Dec)
- Natale (25 Dec)
- Santo Stefano (26 Dec)

Users only need to add **custom holidays** (patron saint days, company closures, etc.) via GUI.

## Usage Workflow

### Example: Add Summer Closure
1. Open **⚙️ Impostazioni** tab
2. Expand **📅 Calendario e Festività** section
3. Click **➕ Aggiungi Festività**
4. Fill fields:
   - Nome: "Ferie Agosto 2026"
   - Tipo: range
   - Ambito: logistics
   - Effetto: both
   - Data inizio: 2026-08-10
   - Data fine: 2026-08-25
5. Click **💾 Salva**
6. Holiday appears in table
7. System blocks orders/receipts during 2026-08-10 → 2026-08-25

### Example: Add Patron Saint Day
1. Click **➕ Aggiungi Festività**
2. Fill fields:
   - Nome: "San Patrono Milano"
   - Tipo: single
   - Ambito: logistics
   - Effetto: both
   - Data: 2026-12-07
3. Click **💾 Salva**
4. December 7, 2026 is now blocked

### Example: Block First of Month Orders
1. Click **➕ Aggiungi Festività**
2. Fill fields:
   - Nome: "Inventario Mensile"
   - Tipo: fixed
   - Ambito: orders
   - Effetto: no_order
   - Giorno del mese: 1
3. Click **💾 Salva**
4. Orders blocked on day 1 of every month (receipts still allowed)

## Implementation Details

### Files Modified
- **src/persistence/csv_layer.py**: Added `read_holidays()`, `write_holidays()`, `add_holiday()`, `update_holiday()`, `delete_holiday()`
- **src/gui/app.py**: Added holiday management section in settings tab with CRUD dialogs

### New Methods in CSVLayer
```python
def read_holidays(self) -> List[Dict[str, Any]]
def write_holidays(self, holidays: List[Dict[str, Any]])
def add_holiday(self, holiday: Dict[str, Any])
def update_holiday(self, index: int, holiday: Dict[str, Any])
def delete_holiday(self, index: int)
```

### New GUI Components
- `_refresh_holidays_table()`: Refresh table from JSON
- `_add_holiday()`: Open add dialog
- `_edit_holiday()`: Open edit dialog for selected
- `_delete_holiday()`: Delete selected with confirmation
- `_show_holiday_dialog()`: Dynamic dialog (add/edit mode)
- `_reload_calendar()`: Reload calendar after changes

### Testing
All backend operations tested in `test_holiday_gui.py`:
- ✅ Read empty holidays
- ✅ Add single holiday
- ✅ Add range holiday
- ✅ Add fixed day holiday
- ✅ Update holiday
- ✅ Delete holiday
- ✅ JSON format validation
- ✅ Calendar reload with new holidays

## Benefits

1. **User-Friendly**: No need to manually edit JSON files
2. **Validation**: Automatic date format checking and range validation
3. **Safe**: Confirmation dialogs prevent accidental deletions
4. **Dynamic UI**: Parameters UI changes based on holiday type
5. **Immediate Effect**: Calendar reloads after each change
6. **Persistent**: Changes saved to `holidays.json` for future runs

## Constraints & Design Decisions

1. **Italian Holidays Automatic**: Users cannot delete core Italian holidays (they're generated automatically)
2. **Index-Based Edit/Delete**: Table row index used for operations (stable during single session)
3. **No Duplication Check**: Users responsible for avoiding duplicate entries
4. **Calendar Reload**: Future enhancement could cache calendar in workflows for real-time updates
5. **Minimal Validation**: Basic format checks only (advanced logic in `HolidayCalendar`)

## Future Enhancements

1. **Preview**: Show affected dates before save (e.g., "This will block 16 days")
2. **Duplicate Detection**: Warn if date overlaps existing holiday
3. **Bulk Import**: CSV import for multiple holidays
4. **Templates**: Pre-defined holiday sets (Italian regions, company-specific)
5. **Conflict Visualization**: Calendar view showing blocked days
6. **Undo**: Revert last holiday change

---

**Last Updated**: February 11, 2026  
**Status**: ✅ Implemented and Tested  
**Version**: 1.0
