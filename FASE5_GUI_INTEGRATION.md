# FASE 5: GUI Integration (Work in Progress)

**Status**: 🔄 IN PROGRESS (Infrastructure Complete, GUI Integration Pending)  
**Test Coverage**: 17/17 tests passing (100%) - StorageAdapter layer  
**Deliverables**: config.py + StorageAdapter + test suite

---

## Overview

FASE 5 integrates the SQLite backend into the existing application with a toggle to switch between CSV and SQLite storage. This phase enables dual-mode operation with graceful fallback.

**Design Approach**:
- **Adapter Pattern**: StorageAdapter wraps CSVLayer and SQLite repositories
- **Transparent Routing**: Backend selection is transparent to caller
- **Backward Compatible**: Existing code works without modification (uses StorageAdapter instead of CSVLayer)
- **Graceful Fallback**: Automatically falls back to CSV if SQLite unavailable

---

## Completed Work

### 1. Configuration Layer (✅ COMPLETED)

**File**: `config.py`

**Added Components**:
- `STORAGE_BACKEND` global variable ('csv' or 'sqlite')
- `DATABASE_PATH` constant
- `SETTINGS_FILE` constant
- `get_storage_backend()` - Read backend from settings.json
- `set_storage_backend(backend)` - Write backend to settings.json
- `is_sqlite_available()` - Check if SQLite database is initialized

**Usage**:
```python
from config import get_storage_backend, set_storage_backend, is_sqlite_available

# Get current backend
backend = get_storage_backend()  # Returns 'csv' or 'sqlite'

# Switch to SQLite
set_storage_backend('sqlite')

# Check if SQLite is ready
if is_sqlite_available():
    print("SQLite database initialized and accessible")
```

**Settings Persistence**:
Settings are stored in `data/settings.json`:
```json
{
  "storage_backend": "sqlite",
  "default_lead_time": 7,
  ...
}
```

---

### 2. StorageAdapter Layer (✅ COMPLETED)

**File**: `src/persistence/storage_adapter.py` (483 LOC)

**Architecture**:
```
┌─────────────────────────────────┐
│      Application Code           │
└────────────┬────────────────────┘
             │
             v
┌─────────────────────────────────┐
│      StorageAdapter             │  ← Routing layer
│  ┌───────────┬─────────────┐    │
│  │ CSV Layer │ SQLite Repos│    │
│  └───────────┴─────────────┘    │
└─────────────────────────────────┘
             │
         ┌───┴────┐
         v        v
      CSV Files  SQLite DB
```

**Key Features**:
1. **Backend Auto-Detection**: Reads `storage_backend` from config
2. **Graceful Fallback**: Falls back to CSV if SQLite unavailable
3. **Drop-in Replacement**: Same interface as CSVLayer
4. **Partial Implementation**: Critical operations (SKU, transactions, sales) implemented; others delegate to CSV

**Implemented Operations**:

| Category | Methods | Backend Support |
|----------|---------|-----------------|
| **SKU** | read_skus, write_sku, get_all_sku_ids, sku_exists, delete_sku | CSV + SQLite |
| **Transactions** | read_transactions, write_transaction, write_transactions_batch | CSV + SQLite |
| **Sales** | read_sales, write_sales_record, append_sales | CSV + SQLite |
| **Settings** | read_settings, write_settings, get_default_sku_params | CSV only |
| **Holidays** | read_holidays, write_holidays, add_holiday, update_holiday, delete_holiday | CSV only |
| **Orders** | read_order_logs, write_order_log | CSV only (delegation) |
| **Receiving** | read_receiving_logs, write_receiving_log | CSV only (delegation) |
| **Audit** | read_audit_log, write_audit_log | CSV only (delegation) |
| **Lots** | read_lots, write_lot | CSV only (delegation) |
| **Promo** | read_promo_calendar, write_promo_window | CSV only (delegation) |
| **Events** | read_event_uplift_rules, write_event_uplift_rule | CSV only (delegation) |

**Domain Model Conversions**:
- `_sku_to_dict(sku: SKU) -> Dict`: Convert SKU domain model to repository dict
- `_dict_to_sku(d: Dict) -> SKU`: Convert repository dict to SKU domain model
- `_dict_to_transaction(d: Dict) -> Transaction`: Convert repository dict to Transaction domain model

**Fallback Logic**:
```python
def write_sku(self, sku: SKU):
    if self.is_sqlite_mode():
        try:
            # Try SQLite
            sku_dict = self._sku_to_dict(sku)
            self.repos.skus().upsert(sku_dict)
        except Exception as e:
            # Fallback to CSV on error
            print(f"⚠ SQLite write_sku failed, falling back to CSV: {e}")
            self.csv_layer.write_sku(sku)
    else:
        # CSV mode
        self.csv_layer.write_sku(sku)
```

**Usage Example**:
```python
from src.persistence.storage_adapter import StorageAdapter

# Auto-detect backend from config
storage = StorageAdapter()

# Check current backend
print(f"Using backend: {storage.get_backend()}")  # 'csv' or 'sqlite'

# Use same interface as CSVLayer
skus = storage.read_skus()
storage.write_sku(sku)
storage.write_transaction(txn)

# Close connection when done
storage.close()
```

---

### 3. Test Suite (✅ COMPLETED)

**File**: `tests/test_storage_adapter_fase5.py` (296 LOC)

**Test Coverage**: 17/17 tests passing (100%)

**Test Categories**:

1. **Adapter Initialization** (2 tests):
   - ✅ CSV mode initialization
   - ✅ Backend fallback when SQLite unavailable

2. **SKU Operations** (4 tests):
   - ✅ Write and read SKU (CSV mode)
   - ✅ SKU existence check
   - ✅ Get all SKU IDs
   - ✅ Delete SKU

3. **Transaction Operations** (2 tests):
   - ✅ Write and read transaction
   - ✅ Write transactions batch

4. **Sales Operations** (2 tests):
   - ✅ Write and read sales record
   - ✅ Append sales (alias method)

5. **Settings Operations** (2 tests):
   - ✅ Read and write settings
   - ✅ Get default SKU parameters

6. **Holidays Operations** (2 tests):
   - ✅ Read and write holidays
   - ✅ Add holiday

7. **Domain Model Conversions** (3 tests):
   - ✅ SKU to dict conversion
   - ✅ Dict to SKU conversion
   - ✅ Dict to Transaction conversion

**Test Execution**:
```bash
$ pytest tests/test_storage_adapter_fase5.py -v
============================= test session starts ==============================
collected 17 items                                                             

tests/test_storage_adapter_fase5.py::TestAdapterInitialization::... PASSED
tests/test_storage_adapter_fase5.py::TestSKUOperations::... PASSED
tests/test_storage_adapter_fase5.py::TestTransactionOperations::... PASSED
tests/test_storage_adapter_fase5.py::TestSalesOperations::... PASSED
tests/test_storage_adapter_fase5.py::TestSettingsOperations::... PASSED
tests/test_storage_adapter_fase5.py::TestHolidaysOperations::... PASSED
tests/test_storage_adapter_fase5.py::TestDomainModelConversions::... PASSED

============================== 17 passed in 0.13s ==============================
```

---

## Pending Work

### 4. Application Integration (⏳ PENDING)

**Goal**: Replace CSVLayer with StorageAdapter throughout the application.

**Files to Modify**:
- `main.py` (main entry point)
- `src/gui/app.py` (GUI tabs)
- `src/workflows/*.py` (workflow classes)

**Search Strategy**:
```bash
# Find all CSVLayer instantiations
grep -r "CSVLayer(" src/ main.py
```

**Example Refactoring**:
```python
# Before
from src.persistence.csv_layer import CSVLayer
csv_layer = CSVLayer()

# After
from src.persistence.storage_adapter import StorageAdapter
storage = StorageAdapter()  # Auto-detects backend
```

**Backward Compatibility**:
StorageAdapter implements the same interface as CSVLayer, so most code will work without changes. Only instantiation needs to be updated.

---

### 5. Settings GUI Toggle (⏳ PENDING)

**Goal**: Add backend toggle in Settings tab (Tkinter GUI)

**UI Design**:
```
┌─────────────────────────────────────────┐
│ Settings Tab                            │
├─────────────────────────────────────────┤
│                                         │
│ Storage Backend:                        │
│   ( ) CSV Files                         │
│   (•) SQLite Database                   │
│                                         │
│   [Migrate CSV → SQLite]  ← Button     │
│                                         │
│   Status: ✓ SQLite database ready      │
│                                         │
└─────────────────────────────────────────┘
```

**Implementation**:
```python
# In src/gui/settings_tab.py (or similar)
import tkinter as tk
from tkinter import ttk, messagebox
from config import get_storage_backend, set_storage_backend, is_sqlite_available

class SettingsTab:
    def create_storage_section(self):
        frame = ttk.LabelFrame(self.parent, text="Storage Backend")
        
        # Radio buttons
        backend_var = tk.StringVar(value=get_storage_backend())
        
        csv_radio = ttk.Radiobutton(
            frame, text="CSV Files", 
            variable=backend_var, value='csv',
            command=lambda: self.on_backend_change('csv')
        )
        csv_radio.pack(anchor='w')
        
        sqlite_radio = ttk.Radiobutton(
            frame, text="SQLite Database", 
            variable=backend_var, value='sqlite',
            command=lambda: self.on_backend_change('sqlite')
        )
        sqlite_radio.pack(anchor='w')
        
        # Migrate button
        migrate_btn = ttk.Button(
            frame, text="Migrate CSV → SQLite",
            command=self.on_migrate_click
        )
        migrate_btn.pack(pady=5)
        
        # Status label
        status = "✓ SQLite database ready" if is_sqlite_available() else "⚠ SQLite not initialized"
        status_label = ttk.Label(frame, text=f"Status: {status}")
        status_label.pack()
        
        frame.pack(pady=10, padx=10, fill='x')
    
    def on_backend_change(self, backend):
        if backend == 'sqlite' and not is_sqlite_available():
            messagebox.showwarning(
                "SQLite Not Ready",
                "SQLite database not initialized. Please run migration first."
            )
            return
        
        set_storage_backend(backend)
        messagebox.showinfo("Success", f"Backend switched to {backend.upper()}")
        # Restart application or reload storage layer
    
    def on_migrate_click(self):
        # TODO: Launch migration workflow (see next section)
        pass
```

---

### 6. Migration Workflow Prompt (⏳ PENDING)

**Goal**: One-time migration prompt on first switch to SQLite.

**Workflow**:
1. User clicks "Migrate CSV → SQLite" button
2. Show migration wizard (Tkinter dialog)
3. Run migration tool (FASE 4: `migrate_csv_to_sqlite.py`)
4. Show progress bar during migration
5. Display migration report
6. Switch backend to SQLite on success

**Implementation**:
```python
# In src/gui/migration_wizard.py
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
from src.migrate_csv_to_sqlite import MigrationOrchestrator
from src.db import open_connection

class MigrationWizard(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Migrate CSV to SQLite")
        self.geometry("600x400")
        
        # Step 1: Confirmation
        ttk.Label(self, text="This will migrate all CSV data to SQLite database.").pack(pady=10)
        ttk.Label(self, text="CSV files will be backed up before migration.").pack()
        
        # Progress bar
        self.progress = ttk.Progressbar(self, mode='indeterminate')
        self.progress.pack(pady=10, fill='x', padx=20)
        
        # Log output
        self.log = scrolledtext.ScrolledText(self, height=15, state='disabled')
        self.log.pack(pady=10, fill='both', expand=True, padx=20)
        
        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        
        self.start_btn = ttk.Button(btn_frame, text="Start Migration", command=self.start_migration)
        self.start_btn.pack(side='left', padx=5)
        
        self.close_btn = ttk.Button(btn_frame, text="Close", command=self.destroy, state='disabled')
        self.close_btn.pack(side='left', padx=5)
    
    def start_migration(self):
        self.start_btn.config(state='disabled')
        self.progress.start()
        
        # Run migration in background thread
        thread = threading.Thread(target=self.run_migration)
        thread.start()
    
    def run_migration(self):
        try:
            # Connect to database
            conn = open_connection()
            
            # Run migration
            orchestrator = MigrationOrchestrator(conn)
            
            self.log_message("→ Starting migration...\n")
            report = orchestrator.migrate_all(dry_run=False)
            
            # Display report
            self.log_message(f"\nMigration completed!\n")
            self.log_message(f"Total rows migrated: {report.total_inserted()}\n")
            self.log_message(f"Total errors: {report.total_errors()}\n")
            
            if report.has_errors():
                self.log_message("\n⚠ Some errors occurred. Check report for details.\n")
            else:
                self.log_message("\n✓ Migration successful!\n")
            
            conn.close()
            
        except Exception as e:
            self.log_message(f"\n✗ Migration failed: {e}\n")
        
        finally:
            self.progress.stop()
            self.close_btn.config(state='normal')
    
    def log_message(self, message):
        self.log.config(state='normal')
        self.log.insert('end', message)
        self.log.see('end')
        self.log.config(state='disabled')
```

---

## Next Steps (Priority Order)

### Step 1: Application Integration (High Priority)

**Goal**: Replace CSVLayer with StorageAdapter in application code.

**Tasks**:
1. Find all CSVLayer instantiations: `grep -r "CSVLayer(" src/ main.py`
2. Replace with StorageAdapter imports and instantiations
3. Test each module after replacement
4. Verify backward compatibility (CSV mode still works)

**Estimated Effort**: 2-3 hours

---

### Step 2: GUI Toggle (Medium Priority)

**Goal**: Add backend toggle in Settings tab.

**Tasks**:
1. Locate Settings tab code (likely `src/gui/settings_tab.py` or `src/gui/app.py`)
2. Add storage backend section with radio buttons
3. Add "Migrate CSV → SQLite" button
4. Display backend status (SQLite ready/not ready)
5. Handle backend switch (may require app restart)

**Estimated Effort**: 1-2 hours

---

### Step 3: Migration Wizard (Medium Priority)

**Goal**: Create user-friendly migration workflow.

**Tasks**:
1. Create MigrationWizard Tkinter dialog
2. Run migration in background thread (non-blocking)
3. Display progress bar during migration
4. Show migration report (success/errors)
5. Handle migration errors gracefully

**Estimated Effort**: 2-3 hours

---

### Step 4: Integration Testing (High Priority)

**Goal**: Validate dual-mode operation.

**Tasks**:
1. Test CSV mode: All operations work as before
2. Test SQLite mode: All operations use SQLite backend
3. Test backend switch: Switch between CSV and SQLite without data loss
4. Test fallback: SQLite errors gracefully fall back to CSV
5. Test migration: CSV data correctly migrated to SQLite

**Test Scenarios**:
- Add SKU in CSV mode → Switch to SQLite → SKU still visible
- Add transaction in SQLite mode → Switch to CSV → Transaction still visible
- SQLite database corrupted → Automatic fallback to CSV
- Migration twice → Idempotent (no duplicates)

**Estimated Effort**: 2-3 hours

---

## Current Status Summary

**✅ Completed**:
- Config layer with backend detection and persistence
- StorageAdapter with transparent routing and fallback
- Comprehensive test suite (17/17 passing)
- Domain model conversions (SKU, Transaction)

**⏳ Pending**:
- Application integration (replace CSVLayer with StorageAdapter)
- GUI toggle for backend selection
- Migration workflow wizard (Tkinter dialog)
- Integration testing (dual-mode validation)

**🎯 FASE 5 Progress**: ~40% complete (infrastructure done, GUI pending)

---

## Testing Instructions

**Run StorageAdapter Tests**:
```bash
# All tests
pytest tests/test_storage_adapter_fase5.py -v

# Specific test category
pytest tests/test_storage_adapter_fase5.py::TestSKUOperations -v

# Quick run (quiet mode)
pytest tests/test_storage_adapter_fase5.py -q
```

**Manual Testing**:
```bash
# Test backend detection
python -c "from config import get_storage_backend; print(f'Current backend: {get_storage_backend()}')"

# Test backend switch
python -c "from config import set_storage_backend; set_storage_backend('sqlite'); print('Switched to SQLite')"

# Test StorageAdapter initialization
python -c "from src.persistence.storage_adapter import StorageAdapter; s = StorageAdapter(); print(f'Backend: {s.get_backend()}')"
```

---

## Known Limitations

1. **Partial SQLite Implementation**: Not all operations use SQLite yet (orders, receiving, lots, etc. still use CSV)
2. **No SKU Rename in SQLite**: SKU ID changes not supported in SQLite mode yet (requires cascade update)
3. **No Full-Text Search in SQLite**: Search operations always use CSV (SQLite FTS not implemented)
4. **Settings/Holidays Always CSV**: Settings and holidays always stored in JSON/CSV (not migrated to SQLite yet)
5. **Single Connection**: StorageAdapter creates one SQLite connection per instance (potential for connection pooling)

---

## Future Enhancements (Post-FASE 5)

1. **Complete SQLite Implementation**: Migrate all operations to SQLite (orders, receiving, lots, audit, etc.)
2. **SKU Rename Support**: Implement cascade update for SKU ID changes in SQLite
3. **Full-Text Search**: Add SQLite FTS (Full-Text Search) for SKU search
4. **Settings/Holidays in SQLite**: Migrate settings.json and holidays.json to SQLite tables
5. **Connection Pooling**: Implement connection pooling for multi-threaded scenarios
6. **Read-Only CSV Mode**: Support read-only access to legacy CSV during transition
7. **Data Sync**: Bi-directional sync between CSV and SQLite for hybrid mode
8. **Migration Resumption**: Support resuming failed migrations from checkpoint

---

**Last Updated**: 2024-02-17  
**Phase Status**: 🔄 IN PROGRESS (~40% complete)  
**Next Milestone**: Application integration + GUI toggle
