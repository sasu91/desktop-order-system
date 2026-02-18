# FASE 7 TASK 7.6 — Error UX & Messaging (COMPLETE)

**Date**: 2026-02-18  
**Status**: ✅ COMPLETE  
**Test Pass Rate**: 44/44 (100%)  
**Overall FASE 7**: 130/130 tests passing (100%)

---

## Executive Summary

Created comprehensive error formatting system that transforms technical exceptions into user-friendly, actionable messages:
- **ErrorContext framework** for structured error information
- **Error formatters** for repository, database, validation, workflow, and I/O errors
- **Recovery guidance** with specific, actionable steps
- **Severity classification** (INFO, WARNING, ERROR, CRITICAL)
- **GUI integration helpers** for messagebox display
- **Validation helpers** for common input scenarios

All errors now include:
1. Clear, non-technical message in Italian
2. Contextual information (SKU, operation, values)
3. Specific recovery steps
4. Error codes for support/documentation
5. Technical details (for advanced users/logging)

---

## Deliverables

### 1. Error Formatting Module ([src/utils/error_formatting.py](src/utils/error_formatting.py))

**Status**: ✅ Complete (~850 lines)

**Core Components**:

#### ErrorSeverity Enum
```python
class ErrorSeverity(Enum):
    INFO = "info"        # Informational
    WARNING = "warning"  # Caution (optional action)
    ERROR = "error"      # Error (action required)
    CRITICAL = "critical" # System-level issue
```

#### ErrorContext Dataclass
```python
@dataclass
class ErrorContext:
    message: str                  # User-friendly message
    severity: ErrorSeverity       # Severity level
    technical_details: str        # Technical error info
    context: Dict[str, Any]       # Additional context
    recovery_steps: list[str]     # Recovery actions
    error_code: Optional[str]     # Error code
    
    def format_for_display(include_technical=False) -> str
    def format_for_log() -> str
```

**Example Usage**:
```python
from src.utils.error_formatting import ErrorFormatter

try:
    sku_repo.create(sku="TEST001", description="Test")
except DuplicateKeyError as e:
    error_ctx = ErrorFormatter.format_repository_error(
        exc=e,
        operation="create_sku",
        sku="TEST001"
    )
    
    # Display in GUI
    messagebox.showerror(
        "Errore",
        error_ctx.format_for_display()
    )
```

**Sample Output**:
```
Elemento già esistente: SKU TEST001 already exists

Dettagli:
  • Operazione: create_sku
  • SKU: TEST001

Azioni consigliate:
  1. Verifica che il codice SKU non sia già in uso
  2. Usa un codice diverso oppure modifica l'elemento esistente

Codice errore: REPO_001
```

---

### 2. Error Formatters

#### Repository Error Formatter
Handles errors from `src/repositories.py`:

```python
ErrorFormatter.format_repository_error(exc, operation, sku=None, additional_context=None)
```

**Supported Exceptions**:
- **DuplicateKeyError** (REPO_001): "Elemento già esistente"
  - Recovery: Verify uniqueness, use different code
- **ForeignKeyError** (REPO_002): "Riferimento non valido"
  - Recovery: Verify SKU exists, create missing entities first
- **NotFoundError** (REPO_003): "Elemento non trovato"
  - Recovery: Verify code, check existing entities list
- **BusinessRuleError** (REPO_004): "Regola di business violata"
  - Recovery: Check field constraints, verify business rules
- **Generic RepositoryError** (REPO_999): Generic repository error

**Example**:
```python
try:
    ledger_repo.append_transaction(date="2026-01-28", sku="NONEXISTENT", event="SALE", qty=10)
except ForeignKeyError as e:
    ctx = ErrorFormatter.format_repository_error(e, "append_transaction", "NONEXISTENT")
    # ctx.message: "Riferimento non valido: SKU NONEXISTENT does not exist"
    # ctx.recovery_steps: ["Verifica che lo SKU esista", "Se necessario, crea prima lo SKU"]
```

---

#### Database Error Formatter
Handles SQLite errors:

```python
ErrorFormatter.format_database_error(exc, operation, context=None)
```

**Supported Exceptions**:
- **OperationalError (locked)** (DB_001): "Database temporaneamente occupato"
  - Recovery: Wait and retry, close other windows
- **OperationalError (disk full)** (DB_002): "Spazio su disco insufficiente" [CRITICAL]
  - Recovery: Free disk space, move database
- **OperationalError (other)** (DB_003): "Errore di accesso al database"
  - Recovery: Verify database integrity, backup/restore
- **IntegrityError (UNIQUE)** (DB_004): "Violazione di unicità"
  - Recovery: Use different identifier
- **IntegrityError (FOREIGN KEY)** (DB_005): "Riferimento non valido"
  - Recovery: Create referenced entity first
- **IntegrityError (CHECK)** (DB_006): "Valore non ammesso"
  - Recovery: Check value constraints (qty > 0, date ranges)
- **Generic DatabaseError** (DB_999): Generic database error

**Example**:
```python
try:
    conn.execute("INSERT INTO skus (sku) VALUES (?)", ("TEST001",))
except sqlite3.IntegrityError as e:
    ctx = ErrorFormatter.format_database_error(e, "insert_sku", {"sku": "TEST001"})
    # If UNIQUE constraint: "Violazione di unicità: elemento già esistente"
```

---

#### Validation Error Formatter
Handles input validation errors:

```python
ErrorFormatter.format_validation_error(field, value, constraint, expected=None)
```

**Common Constraints**:
- **Date format**: "Formato data: YYYY-MM-DD (es. 2026-01-28)"
- **Positive number**: "Il valore deve essere un numero positivo"
- **Range**: "Verifica che il valore sia nell'intervallo ammesso"
- **Integer**: "Il valore deve essere un numero intero"

**Example**:
```python
ctx = ErrorFormatter.format_validation_error(
    field="date",
    value="28/01/2026",
    constraint="date format YYYY-MM-DD",
    expected="YYYY-MM-DD (es. 2026-01-28)"
)
# ctx.message: "Campo 'date' non valido: YYYY-MM-DD (es. 2026-01-28)"
# ctx.recovery_steps: ["Verifica il formato del campo 'date'", "Formato data: YYYY-MM-DD (es. 2026-01-28)"]
```

---

#### Workflow Error Formatter
Handles high-level workflow errors:

```python
ErrorFormatter.format_workflow_error(exc, workflow, step, context=None)
```

**Common Patterns**:
- **Missing data** (WF_001): "Dati mancanti nel workflow"
  - Recovery: Verify all required data, create missing entities
- **Invalid operation** (WF_002): "Operazione non ammessa"
  - Recovery: Check prerequisites, verify workflow state
- **Generic workflow error** (WF_999): Generic workflow error

**Example**:
```python
try:
    order_workflow.generate_proposal(sku="MISSING")
except ValueError as e:
    ctx = ErrorFormatter.format_workflow_error(
        e, "OrderWorkflow", "generate_proposal", {"sku": "MISSING"}
    )
```

---

#### I/O Error Formatter
Handles file system errors:

```python
ErrorFormatter.format_io_error(exc, file_path, operation)
```

**Supported Exceptions**:
- **FileNotFoundError** (IO_001): "File non trovato"
  - Recovery: Verify path, check if file was moved/deleted
- **PermissionError** (IO_002): "Permessi insufficienti"
  - Recovery: Check read/write permissions, run as admin if needed
- **OSError/IOError** (IO_003): "Errore I/O"
  - Recovery: Check disk space, verify filesystem accessible

**Example**:
```python
try:
    with open("/protected/backup.db", "r") as f:
        data = f.read()
except PermissionError as e:
    ctx = ErrorFormatter.format_io_error(e, "/protected/backup.db", "read")
    # ctx.message: "Permessi insufficienti per accedere a: /protected/backup.db"
```

---

#### Generic Error Formatter
Fallback for unknown errors:

```python
ErrorFormatter.format_generic_error(exc, operation, context=None)
```

- Includes full exception traceback in technical_details
- Error code: GENERIC_999
- Generic recovery steps: retry, restart, contact support

---

### 3. Pre-defined Validation Messages

**ValidationMessages** class provides common validation messages:

```python
ValidationMessages.required_field("sku")  
# → "Campo 'sku' obbligatorio"

ValidationMessages.invalid_format("date", "YYYY-MM-DD")  
# → "Campo 'date' non valido. Formato atteso: YYYY-MM-DD"

ValidationMessages.out_of_range("service_level", 0, 100)  
# → "Campo 'service_level' deve essere tra 0 e 100"

ValidationMessages.date_format_error()  
# → "Formato data non valido. Usa: YYYY-MM-DD (es. 2026-01-28)"

ValidationMessages.date_range_error()  
# → "Data fine deve essere >= data inizio"

ValidationMessages.positive_number_required("qty")  
# → "Campo 'qty' deve essere un numero positivo"

ValidationMessages.integer_required("qty")  
# → "Campo 'qty' deve essere un numero intero"

ValidationMessages.duplicate_entry("TEST001")  
# → "Elemento 'TEST001' già esistente nel sistema"

ValidationMessages.not_found("SKU", "TEST404")  
# → "SKU 'TEST404' non trovato"

ValidationMessages.form_validation_passed()  
# → "✓ Pronto"
```

---

### 4. GUI Integration Helpers

#### format_error_for_messagebox()
Convenience function for GUI error display:

```python
from src.utils.error_formatting import format_error_for_messagebox

try:
    sku_repo.create(sku="TEST001", description="Test")
except Exception as e:
    title, message = format_error_for_messagebox(
        exc=e,
        operation="create_sku",
        sku="TEST001",
        include_technical=False  # True for advanced users
    )
    
    messagebox.showerror(title, message)
```

**Automatic Detection**:
- Detects exception type (RepositoryError, DatabaseError, IOError, etc.)
- Selects appropriate formatter
- Formats severity into Italian title:
  - INFO → "Informazione"
  - WARNING → "Attenzione"
  - ERROR → "Errore"
  - CRITICAL → "Errore Critico"

---

#### Validation Helper Functions

**validate_date_format()**:
```python
is_valid, error = validate_date_format("2026-01-28")
# → (True, "")

is_valid, error = validate_date_format("28/01/2026")
# → (False, "Formato data non valido. Usa: YYYY-MM-DD (es. 2026-01-28)")
```

**validate_positive_integer()**:
```python
is_valid, error = validate_positive_integer("42", "quantity")
# → (True, "")

is_valid, error = validate_positive_integer("-5", "quantity")
# → (False, "Campo 'quantity' deve essere un numero positivo")

is_valid, error = validate_positive_integer("abc", "quantity")
# → (False, "Campo 'quantity' deve essere un numero intero")
```

**validate_float_range()**:
```python
is_valid, error = validate_float_range("50.5", "service_level", 0.0, 100.0)
# → (True, "")

is_valid, error = validate_float_range("150", "service_level", 0.0, 100.0)
# → (False, "Campo 'service_level' deve essere tra 0.0 e 100.0")
```

---

### 5. Test Suite ([tests/test_error_ux_fase7.py](tests/test_error_ux_fase7.py))

**Status**: ✅ Complete (~1200 lines, 44 tests, 100% pass)

**Test Coverage**:

| Category | Tests | Coverage |
|----------|-------|----------|
| Error Context & Formatting | 1-3 | ErrorContext creation, display formatting, technical details |
| Repository Errors | 4-8 | DuplicateKey, ForeignKey, NotFound, BusinessRule, generic |
| Database Errors | 9-13 | Locked, disk full, UNIQUE, FOREIGN KEY, CHECK constraints |
| Validation Errors | 14-16 | Date format, positive number, range constraints |
| Workflow Errors | 17-19 | Missing data, invalid operation, generic workflow |
| I/O Errors | 20-22 | FileNotFound, Permission, OSError |
| Generic Errors | 23-25 | Generic error, traceback inclusion, log formatting |
| Validation Messages | 26-28 | Pre-defined messages (required, format, range) |
| GUI Integration | 29-31 | messagebox formatting, repository/database/generic |
| Validation Helpers | 32-38 | Date format, positive integer, float range |
| Severity Classification | 39-40 | Repository/database error severity |
| Recovery Steps Quality | 41-42 | Actionable steps, contextual guidance |
| Comprehensive Coverage | 43-44 | Error code uniqueness, exception type coverage |

**Key Tests**:

```python
def test_error_context_format_for_display():
    """TEST 2: ErrorContext formats correctly for GUI display."""
    # Verifies message, context, recovery steps, error code are all present
    # Verifies technical details excluded by default

def test_format_duplicate_key_error():
    """TEST 4: DuplicateKeyError is formatted with user-friendly message."""
    # Verifies Italian message, SKU context, recovery guidance
    # Verifies error code REPO_001

def test_format_database_locked_error():
    """TEST 9: Database locked error provides retry guidance."""
    # Verifies WARNING severity, retry guidance
    # Verifies error code DB_001

def test_format_validation_error_date_format():
    """TEST 14: Validation error for date format provides format guidance."""
    # Verifies format guidance (YYYY-MM-DD)
    # Verifies error code VAL_001

def test_format_workflow_error_missing_data():
    """TEST 17: Workflow error for missing data identifies prerequisites."""
    # Verifies workflow context, prerequisite guidance
    # Verifies error code WF_001

def test_format_file_not_found_error():
    """TEST 20: FileNotFoundError provides path verification guidance."""
    # Verifies file path context, path verification steps
    # Verifies error code IO_001

def test_format_error_for_messagebox_repository():
    """TEST 29: format_error_for_messagebox handles repository errors."""
    # Verifies automatic formatter selection
    # Verifies Italian title + formatted message

def test_validate_date_format_valid():
    """TEST 32: validate_date_format accepts valid ISO date."""
    # Verifies ISO 8601 date acceptance

def test_recovery_steps_are_actionable():
    """TEST 41: Recovery steps are specific and actionable."""
    # Verifies steps > 10 chars, contain action verbs
    # Verifies Italian action verbs (verifica, controlla, crea, etc.)

def test_all_error_codes_unique():
    """TEST 43: All error codes are unique across formatters."""
    # Verifies no duplicate error codes (except *_999 generic codes)
```

---

## Error Code Reference

Complete error code catalog for support/documentation:

### Repository Errors (REPO_*)
- **REPO_001**: Elemento già esistente (DuplicateKeyError)
- **REPO_002**: Riferimento non valido (ForeignKeyError)
- **REPO_003**: Elemento non trovato (NotFoundError)
- **REPO_004**: Regola di business violata (BusinessRuleError)
- **REPO_999**: Errore repository generico

### Database Errors (DB_*)
- **DB_001**: Database temporaneamente occupato (locked)
- **DB_002**: Spazio su disco insufficiente (disk full) [CRITICAL]
- **DB_003**: Errore di accesso al database
- **DB_004**: Violazione di unicità (UNIQUE constraint)
- **DB_005**: Riferimento non valido (FOREIGN KEY constraint)
- **DB_006**: Valore non ammesso (CHECK constraint)
- **DB_007**: Violazione di integrità (generic IntegrityError)
- **DB_999**: Errore database generico

### Validation Errors (VAL_*)
- **VAL_001**: Campo non valido (validation error)

### Workflow Errors (WF_*)
- **WF_001**: Dati mancanti nel workflow
- **WF_002**: Operazione non ammessa
- **WF_999**: Errore workflow generico

### I/O Errors (IO_*)
- **IO_001**: File non trovato
- **IO_002**: Permessi insufficienti
- **IO_003**: Errore I/O generico
- **IO_999**: Errore I/O imprevisto

### Generic Errors
- **GENERIC_999**: Errore imprevisto

---

## Usage Examples

### Example 1: Repository Error (DuplicateKeyError)

```python
from src.repositories import SKURepository, DuplicateKeyError
from src.utils.error_formatting import format_error_for_messagebox
from tkinter import messagebox

sku_repo = SKURepository(conn)

try:
    sku_repo.create(sku="TEST001", description="Test Product")
except DuplicateKeyError as e:
    title, message = format_error_for_messagebox(
        exc=e,
        operation="create_sku",
        sku="TEST001"
    )
    messagebox.showerror(title, message, parent=parent_window)
```

**User sees**:
```
Errore

Elemento già esistente: SKU TEST001 already exists

Dettagli:
  • Operazione: create_sku
  • SKU: TEST001

Azioni consigliate:
  1. Verifica che il codice SKU non sia già in uso
  2. Usa un codice diverso oppure modifica l'elemento esistente

Codice errore: REPO_001
```

---

### Example 2: Database Error (Locked)

```python
import sqlite3
from src.utils.error_formatting import ErrorFormatter

try:
    cursor.execute("INSERT INTO transactions (...) VALUES (...)")
    conn.commit()
except sqlite3.OperationalError as e:
    if "locked" in str(e).lower():
        ctx = ErrorFormatter.format_database_error(e, "insert_transaction")
        
        # Log structured error
        logger.warning(ctx.format_for_log())
        
        # Show user-friendly message
        messagebox.showwarning(
            "Attenzione",
            ctx.format_for_display()
        )
```

**User sees**:
```
Attenzione

Database temporaneamente occupato

Dettagli:
  • Operazione: insert_transaction

Azioni consigliate:
  1. Attendi qualche secondo e riprova
  2. Chiudi altre finestre che potrebbero accedere al database
  3. Se l'errore persiste, riavvia l'applicazione

Codice errore: DB_001
```

---

### Example 3: Validation Error (Real-time Form Feedback)

```python
from src.utils.error_formatting import validate_date_format, ValidationMessages

def _validate_form(self):
    """Validate form and show inline feedback."""
    errors = []
    
    # Date validation
    is_valid, error_msg = validate_date_format(self.date_entry.get())
    if not is_valid:
        errors.append(error_msg)
    
    # Quantity validation
    is_valid, error_msg = validate_positive_integer(self.qty_entry.get(), "quantità")
    if not is_valid:
        errors.append(error_msg)
    
    # Update validation label
    if errors:
        self.validation_label.config(
            text="; ".join(errors),
            foreground="#d9534f"
        )
        self.submit_btn.config(state="disabled")
    else:
        self.validation_label.config(
            text=ValidationMessages.form_validation_passed(),
            foreground="#5cb85c"
        )
        self.submit_btn.config(state="normal")
```

**User sees**:
```
[Date entry: "28/01/2026"]
[Validation label: "Formato data non valido. Usa: YYYY-MM-DD (es. 2026-01-28)" (red)]
[Submit button: DISABLED]

↓ User corrects to "2026-01-28"

[Date entry: "2026-01-28"]
[Validation label: "✓ Pronto" (green)]
[Submit button: ENABLED]
```

---

### Example 4: Workflow Error with Context

```python
from src.workflows.order import OrderWorkflow
from src.utils.error_formatting import ErrorFormatter

try:
    proposals = order_workflow.generate_proposals(date.today())
except ValueError as e:
    if "SKU not found" in str(e):
        ctx = ErrorFormatter.format_workflow_error(
            exc=e,
            workflow="OrderWorkflow",
            step="generate_proposals",
            context={"date": date.today().isoformat()}
        )
        
        # Show detailed error
        messagebox.showerror(
            "Errore",
            ctx.format_for_display()
        )
```

**User sees**:
```
Errore

Dati mancanti nel workflow 'OrderWorkflow'

Dettagli:
  • Workflow: OrderWorkflow
  • Step: generate_proposals
  • date: 2026-01-28

Azioni consigliate:
  1. Verifica che tutti i dati necessari siano presenti
  2. Controlla che gli SKU esistano nel sistema
  3. Se necessario, crea i dati mancanti prima di procedere

Codice errore: WF_001
```

---

### Example 5: I/O Error (Backup Failure)

```python
from src.utils.error_formatting import ErrorFormatter

try:
    shutil.copy2(db_path, backup_path)
except PermissionError as e:
    ctx = ErrorFormatter.format_io_error(
        exc=e,
        file_path=str(backup_path),
        operation="backup"
    )
    
    messagebox.showerror(
        "Errore Critico",
        ctx.format_for_display()
    )
```

**User sees**:
```
Errore Critico

Permessi insufficienti per accedere a: /protected/backup/app_backup_20260128.db

Dettagli:
  • File: /protected/backup/app_backup_20260128.db
  • Operazione: backup

Azioni consigliate:
  1. Verifica di avere i permessi di lettura/scrittura sul file
  2. Controlla che il file non sia aperto in un'altra applicazione
  3. Se necessario, esegui l'applicazione come amministratore

Codice errore: IO_002
```

---

## Integration Points

### 1. Repository Layer
All repository methods should use error formatter for user-facing errors:

```python
from src.utils.error_formatting import format_error_for_messagebox

class SKURepository:
    def create(self, sku: str, description: str, **kwargs):
        try:
            # ... repository logic ...
            pass
        except Exception as e:
            # Log technical error
            logger.error(f"SKU creation failed: {e}", exc_info=True)
            
            # Format for user
            title, message = format_error_for_messagebox(
                exc=e,
                operation="create_sku",
                sku=sku
            )
            
            # Re-raise with formatted message (or show directly in GUI)
            raise RepositoryError(message) from e
```

---

### 2. GUI Forms
Use validation helpers for real-time feedback:

```python
from src.utils.error_formatting import (
    validate_date_format,
    validate_positive_integer,
    validate_float_range,
    ValidationMessages
)

class SKUEditForm:
    def _validate_form(self):
        errors = []
        
        # Validate each field
        is_valid, error = validate_date_format(self.date_var.get())
        if not is_valid:
            errors.append(error)
        
        is_valid, error = validate_positive_integer(self.qty_var.get(), "quantità")
        if not is_valid:
            errors.append(error)
        
        is_valid, error = validate_float_range(
            self.service_level_var.get(),
            "service_level",
            0.0,
            100.0
        )
        if not is_valid:
            errors.append(error)
        
        # Update UI
        if errors:
            self.validation_label.config(text="; ".join(errors), foreground="red")
            self.submit_btn.config(state="disabled")
        else:
            self.validation_label.config(
                text=ValidationMessages.form_validation_passed(),
                foreground="green"
            )
            self.submit_btn.config(state="normal")
```

---

### 3. Workflow Orchestration
Catch and format errors at workflow boundaries:

```python
from src.utils.error_formatting import ErrorFormatter

class OrderWorkflow:
    def generate_proposals(self, asof_date: date) -> list:
        try:
            # ... workflow logic ...
            return proposals
        except ValueError as e:
            ctx = ErrorFormatter.format_workflow_error(
                exc=e,
                workflow="OrderWorkflow",
                step="generate_proposals",
                context={"date": asof_date.isoformat()}
            )
            
            # Log structured error
            logger.error(ctx.format_for_log())
            
            # Show user-friendly message
            messagebox.showerror("Errore", ctx.format_for_display())
            
            return []  # Return empty list, don't crash
```

---

### 4. Structured Logging
Use `format_for_log()` for consistent log formatting:

```python
from src.utils.error_formatting import ErrorFormatter
import logging

logger = logging.getLogger(__name__)

try:
    # ... operation ...
    pass
except Exception as e:
    ctx = ErrorFormatter.format_generic_error(
        exc=e,
        operation="calculate_stock",
        context={"sku": sku, "date": asof_date}
    )
    
    # Log with structured format
    logger.error(ctx.format_for_log())
    # Output: [ERROR] Errore imprevisto durante calculate_stock | Context: sku=TEST001, date=2026-01-28 | Technical: ValueError: invalid value
```

---

## Design Decisions

### 1. Italian Messages for End Users
**Decision**: All user-facing messages in Italian (technical details in English)  
**Rationale**: Application targets Italian users; technical details remain in English for easier debugging/support

### 2. Structured Error Context
**Decision**: Use dataclass `ErrorContext` instead of simple strings  
**Rationale**: Enables flexible formatting (GUI vs log), preserves structure for potential future analytics

### 3. Separate Formatters by Layer
**Decision**: Dedicated formatters for repository, database, validation, workflow, I/O  
**Rationale**: Each layer has different error patterns and recovery guidance; specialized formatters provide better UX

### 4. Recovery Steps Always Included
**Decision**: Every error includes actionable recovery steps  
**Rationale**: Users need to know "what to do next", not just "what went wrong"; reduces support burden

### 5. Error Code System
**Decision**: Hierarchical error codes (REPO_001, DB_004, etc.)  
**Rationale**: Enables quick support lookup, log analysis, future error tracking/monitoring

### 6. Severity Classification
**Decision**: Four severity levels (INFO, WARNING, ERROR, CRITICAL)  
**Rationale**: Allows UI to prioritize display (e.g., CRITICAL → blocking modal, WARNING → banner); aligns with logging standards

### 7. No Auto-Display in Formatters
**Decision**: Formatters return ErrorContext, don't show messagebox directly  
**Rationale**: Separation of concerns; allows batch error handling, testing without GUI

---

## Testing Strategy

### Test Categories

**Unit Tests** (all 44 tests):
- Error context creation and formatting
- All error formatters (repository, database, validation, workflow, I/O)
- Pre-defined validation messages
- GUI integration helpers
- Validation helper functions
- Severity classification
- Recovery step quality
- Error code uniqueness

**Integration Tests** (implicit in FASE 7):
- Repository errors → ErrorFormatter workflow
- Database errors → ErrorFormatter → GUI display
- Validation helpers → Form feedback loop

**Quality Metrics**:
- ✅ All error codes unique
- ✅ All recovery steps > 10 chars (actionable)
- ✅ All recovery steps contain action verbs
- ✅ Severity appropriately classified
- ✅ Technical details separated from user message

---

## Future Enhancements

**(Not in scope for TASK 7.6)**:

### 1. Error Analytics
- Track error frequency by error code
- Identify most common user errors
- Prioritize UX improvements based on data

### 2. Multi-language Support
- Externalize messages to i18n files
- Support English, Italian, other languages
- User preference for language

### 3. Context-Sensitive Help
- Error code → documentation link
- Inline help tooltips for common errors
- Video tutorials for complex recovery

### 4. Error Recovery Automation
- Auto-retry for transient errors (DB locked)
- Auto-suggest fixes (e.g., "Create missing SKU?" button)
- Bulk error resolution (fix all similar errors)

### 5. Error Notification System
- Toast notifications for non-blocking warnings
- Error summary panel (all today's errors)
- Error count badge on status bar

---

## Stop Conditions (Acceptance Criteria)

| Requirement | Status | Evidence |
|-------------|--------|----------|
| 1. Error formatting module created | ✅ Done | src/utils/error_formatting.py (~850 lines) |
| 2. ErrorContext framework implemented | ✅ Done | Dataclass with format_for_display/log |
| 3. Repository error formatter | ✅ Done | 5 exception types, error codes REPO_* |
| 4. Database error formatter | ✅ Done | 7+ SQLite error patterns, codes DB_* |
| 5. Validation error formatter | ✅ Done | Field/constraint/expected params |
| 6. Workflow error formatter | ✅ Done | Workflow/step context, codes WF_* |
| 7. I/O error formatter | ✅ Done | File/operation context, codes IO_* |
| 8. Generic error formatter | ✅ Done | Fallback with traceback |
| 9. Pre-defined validation messages | ✅ Done | 11 common message patterns |
| 10. GUI integration helpers | ✅ Done | format_error_for_messagebox() + 3 validators |
| 11. Recovery steps always included | ✅ Done | All formatters provide 1-3 actionable steps |
| 12. Severity classification | ✅ Done | INFO/WARNING/ERROR/CRITICAL |
| 13. Error codes unique | ✅ Done | Verified in TEST 43 |
| 14. Italian user messages | ✅ Done | All user-facing strings in Italian |
| 15. Test suite complete | ✅ Done | 44 tests, 100% pass |
| 16. All FASE 7 tests passing | ✅ Done | 130/130 tests (100%) |

---

## Completion Checklist

- [x] Error formatting module created (error_formatting.py)
- [x] ErrorContext dataclass implemented
- [x] ErrorSeverity enum defined
- [x] Repository error formatter implemented
- [x] Database error formatter implemented
- [x] Validation error formatter implemented
- [x] Workflow error formatter implemented
- [x] I/O error formatter implemented
- [x] Generic error formatter implemented
- [x] Pre-defined validation messages added
- [x] GUI integration helpers created
- [x] Validation helper functions implemented
- [x] Error codes defined and documented
- [x] Recovery steps verified actionable
- [x] Test suite created (44 tests)
- [x] All tests passing (44/44)
- [x] All FASE 7 tests passing (130/130)
- [x] Integration examples documented
- [x] Usage patterns documented

---

## Sign-Off

**TASK 7.6 — Error UX & Messaging**: ✅ COMPLETE

**Summary**: Created comprehensive error formatting system that transforms technical exceptions into user-friendly, actionable messages. All errors now include clear Italian descriptions, contextual information, specific recovery steps, and error codes. Test suite validates all formatters and helpers (44 tests, 100% pass).

**FASE 7 Status**: **COMPLETE** ✅

All 6 tasks complete:
- ✅ TASK 7.1: Concurrency (13 tests)
- ✅ TASK 7.2: Invariants (17 tests)
- ✅ TASK 7.3: Recovery & Backup (15 tests)
- ✅ TASK 7.4: Audit & Traceability (19 tests)
- ✅ TASK 7.5: Performance Tuning (22 tests)
- ✅ TASK 7.6: Error UX & Messaging (44 tests)

**Total FASE 7**: 130/130 tests (100% pass rate)

**Production Hardening Complete**: The application now has enterprise-grade error handling, performance optimization, audit trails, backup/recovery, invariant checks, and concurrency management.

---

**Signed**: AI Agent  
**Date**: 2026-02-18  
**Phase**: FASE 7 — Hardening, Operatività, Osservabilità  
**Task**: 7.6 — Error UX & Messaging ✅  
**FASE 7**: COMPLETE ✅
