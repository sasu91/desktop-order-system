"""
FASE 7 TASK 7.6 — Error UX & Messaging Tests

Test comprehensive error formatting, user-friendly messages, and recovery guidance.
"""

import pytest
import sqlite3
from datetime import date
from src.utils.error_formatting import (
    ErrorContext,
    ErrorSeverity,
    ErrorFormatter,
    ValidationMessages,
    format_error_for_messagebox,
    validate_date_format,
    validate_positive_integer,
    validate_float_range,
)
from src.repositories import (
    DuplicateKeyError,
    ForeignKeyError,
    NotFoundError,
    BusinessRuleError,
    RepositoryError,
)


# ============================================================
# TEST 1-3: Error Context & Formatting
# ============================================================

def test_error_context_creation():
    """TEST 1: ErrorContext can be created with all required fields."""
    ctx = ErrorContext(
        message="Test error message",
        severity=ErrorSeverity.ERROR,
        technical_details="Technical: ValueError('invalid')",
        context={"SKU": "TEST001", "Operation": "create"},
        recovery_steps=["Step 1", "Step 2"],
        error_code="TEST_001"
    )
    
    assert ctx.message == "Test error message"
    assert ctx.severity == ErrorSeverity.ERROR
    assert ctx.context["SKU"] == "TEST001"
    assert len(ctx.recovery_steps) == 2
    assert ctx.error_code == "TEST_001"


def test_error_context_format_for_display():
    """TEST 2: ErrorContext formats correctly for GUI display."""
    ctx = ErrorContext(
        message="Elemento già esistente",
        severity=ErrorSeverity.ERROR,
        technical_details="DuplicateKeyError: UNIQUE constraint failed",
        context={"SKU": "TEST001", "Operazione": "create_sku"},
        recovery_steps=[
            "Verifica che il codice SKU non sia già in uso",
            "Usa un codice diverso",
        ],
        error_code="REPO_001"
    )
    
    display_text = ctx.format_for_display(include_technical=False)
    
    # Check message is present
    assert "Elemento già esistente" in display_text
    
    # Check context is present
    assert "SKU: TEST001" in display_text
    assert "Operazione: create_sku" in display_text
    
    # Check recovery steps are present
    assert "Azioni consigliate:" in display_text
    assert "1. Verifica che il codice SKU" in display_text
    assert "2. Usa un codice diverso" in display_text
    
    # Check error code is present
    assert "Codice errore: REPO_001" in display_text
    
    # Technical details should NOT be present
    assert "DuplicateKeyError" not in display_text


def test_error_context_format_with_technical_details():
    """TEST 3: ErrorContext includes technical details when requested."""
    ctx = ErrorContext(
        message="Errore database",
        severity=ErrorSeverity.CRITICAL,
        technical_details="sqlite3.OperationalError: database is locked",
        context={},
        recovery_steps=["Riprova dopo qualche secondo"],
        error_code="DB_001"
    )
    
    display_text = ctx.format_for_display(include_technical=True)
    
    # Technical details should be present
    assert "📋 Dettagli tecnici:" in display_text
    assert "sqlite3.OperationalError: database is locked" in display_text


# ============================================================
# TEST 4-8: Repository Error Formatting
# ============================================================

def test_format_duplicate_key_error():
    """TEST 4: DuplicateKeyError is formatted with user-friendly message."""
    exc = DuplicateKeyError("SKU TEST001 already exists")
    
    error_ctx = ErrorFormatter.format_repository_error(
        exc=exc,
        operation="create_sku",
        sku="TEST001"
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "già esistente" in error_ctx.message.lower()
    assert error_ctx.context["SKU"] == "TEST001"
    assert len(error_ctx.recovery_steps) >= 1
    assert "codice" in error_ctx.recovery_steps[0].lower() or "sku" in error_ctx.recovery_steps[0].lower()
    assert error_ctx.error_code == "REPO_001"


def test_format_foreign_key_error():
    """TEST 5: ForeignKeyError is formatted with relationship guidance."""
    exc = ForeignKeyError("SKU TEST999 does not exist")
    
    error_ctx = ErrorFormatter.format_repository_error(
        exc=exc,
        operation="append_transaction",
        sku="TEST999"
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "riferimento" in error_ctx.message.lower() or "non valido" in error_ctx.message.lower()
    assert error_ctx.context["SKU"] == "TEST999"
    # Check for Italian keywords: "esist" (esista/esistete) or "verifica" or "crea"
    assert any(("sku" in step.lower() and ("esist" in step.lower() or "verifica" in step.lower())) or "crea" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "REPO_002"


def test_format_not_found_error():
    """TEST 6: NotFoundError is formatted as warning with search guidance."""
    exc = NotFoundError("SKU TEST404 not found")
    
    error_ctx = ErrorFormatter.format_repository_error(
        exc=exc,
        operation="get_sku",
        sku="TEST404"
    )
    
    assert error_ctx.severity == ErrorSeverity.WARNING
    assert "non trovato" in error_ctx.message.lower()
    assert error_ctx.context["SKU"] == "TEST404"
    assert any("verifica" in step.lower() or "controlla" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "REPO_003"


def test_format_business_rule_error():
    """TEST 7: BusinessRuleError provides constraint guidance."""
    exc = BusinessRuleError("Quantity must be positive, got -5")
    
    error_ctx = ErrorFormatter.format_repository_error(
        exc=exc,
        operation="append_transaction",
        sku="TEST001",
        additional_context={"qty": -5}
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "regola" in error_ctx.message.lower() or "business" in error_ctx.message.lower()
    assert error_ctx.context["qty"] == -5
    assert any("vincol" in step.lower() or "controlla" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "REPO_004"


def test_format_generic_repository_error():
    """TEST 8: Generic RepositoryError has fallback formatting."""
    exc = RepositoryError("Unknown repository error")
    
    error_ctx = ErrorFormatter.format_repository_error(
        exc=exc,
        operation="unknown_operation",
        sku="TEST001"
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert error_ctx.context["Operazione"] == "unknown_operation"
    assert len(error_ctx.recovery_steps) >= 1
    assert error_ctx.error_code == "REPO_999"


# ============================================================
# TEST 9-13: Database Error Formatting
# ============================================================

def test_format_database_locked_error():
    """TEST 9: Database locked error provides retry guidance."""
    exc = sqlite3.OperationalError("database is locked")
    
    error_ctx = ErrorFormatter.format_database_error(
        exc=exc,
        operation="insert_transaction"
    )
    
    assert error_ctx.severity == ErrorSeverity.WARNING
    assert "occupato" in error_ctx.message.lower() or "locked" in error_ctx.message.lower()
    assert any("attendi" in step.lower() or "riprova" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "DB_001"


def test_format_disk_full_error():
    """TEST 10: Disk full error is marked as CRITICAL."""
    exc = sqlite3.OperationalError("disk I/O error: disk full")
    
    error_ctx = ErrorFormatter.format_database_error(
        exc=exc,
        operation="backup_database"
    )
    
    assert error_ctx.severity == ErrorSeverity.CRITICAL
    assert "spazio" in error_ctx.message.lower() or "disco" in error_ctx.message.lower()
    assert any("libera" in step.lower() or "elimina" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "DB_002"


def test_format_unique_constraint_error():
    """TEST 11: UNIQUE constraint error identifies duplicate."""
    exc = sqlite3.IntegrityError("UNIQUE constraint failed: skus.sku")
    
    error_ctx = ErrorFormatter.format_database_error(
        exc=exc,
        operation="insert_sku",
        context={"sku": "TEST001"}
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "unicità" in error_ctx.message.lower() or "già esistente" in error_ctx.message.lower()
    assert error_ctx.context["sku"] == "TEST001"
    assert any("già in uso" in step.lower() or "identificatore" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "DB_004"


def test_format_foreign_key_constraint_error():
    """TEST 12: FOREIGN KEY constraint error provides relationship context."""
    exc = sqlite3.IntegrityError("FOREIGN KEY constraint failed")
    
    error_ctx = ErrorFormatter.format_database_error(
        exc=exc,
        operation="insert_transaction",
        context={"sku": "NONEXISTENT"}
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "riferimento" in error_ctx.message.lower() or "foreign" in error_ctx.message.lower()
    assert error_ctx.context["sku"] == "NONEXISTENT"
    assert any("esista" in step.lower() or "crea prima" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "DB_005"


def test_format_check_constraint_error():
    """TEST 13: CHECK constraint error identifies value constraints."""
    exc = sqlite3.IntegrityError("CHECK constraint failed: qty > 0")
    
    error_ctx = ErrorFormatter.format_database_error(
        exc=exc,
        operation="insert_transaction",
        context={"qty": -5}
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "non ammesso" in error_ctx.message.lower() or "vincolo" in error_ctx.message.lower()
    assert error_ctx.context["qty"] == -5
    assert any("qty" in step.lower() or "date" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "DB_006"


# ============================================================
# TEST 14-16: Validation Error Formatting
# ============================================================

def test_format_validation_error_date_format():
    """TEST 14: Validation error for date format provides format guidance."""
    error_ctx = ErrorFormatter.format_validation_error(
        field="date",
        value="28/01/2026",
        constraint="date format YYYY-MM-DD",
        expected="YYYY-MM-DD (es. 2026-01-28)"
    )
    
    assert error_ctx.severity == ErrorSeverity.WARNING
    assert "date" in error_ctx.message.lower()
    assert error_ctx.context["Campo"] == "date"
    assert any("yyyy-mm-dd" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "VAL_001"


def test_format_validation_error_positive_number():
    """TEST 15: Validation error for positive number constraint."""
    error_ctx = ErrorFormatter.format_validation_error(
        field="qty",
        value=-5,
        constraint="must be positive (> 0)"
    )
    
    assert error_ctx.severity == ErrorSeverity.WARNING
    assert "qty" in error_ctx.message.lower()
    assert error_ctx.context["Campo"] == "qty"
    assert error_ctx.context["Valore inserito"] == "-5"
    assert any("positivo" in step.lower() for step in error_ctx.recovery_steps)


def test_format_validation_error_range():
    """TEST 16: Validation error for range constraint identifies limits."""
    error_ctx = ErrorFormatter.format_validation_error(
        field="service_level",
        value=150,
        constraint="range 0-100",
        expected="Valore tra 0 e 100"
    )
    
    assert error_ctx.severity == ErrorSeverity.WARNING
    assert "service_level" in error_ctx.message.lower()
    assert error_ctx.context["Valore inserito"] == "150"
    assert any("intervallo" in step.lower() for step in error_ctx.recovery_steps)


# ============================================================
# TEST 17-19: Workflow Error Formatting
# ============================================================

def test_format_workflow_error_missing_data():
    """TEST 17: Workflow error for missing data identifies prerequisites."""
    exc = ValueError("SKU not found in order workflow")
    
    error_ctx = ErrorFormatter.format_workflow_error(
        exc=exc,
        workflow="OrderWorkflow",
        step="generate_proposal",
        context={"sku": "TEST404"}
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "mancant" in error_ctx.message.lower() or "missing" in error_ctx.message.lower()
    assert error_ctx.context["Workflow"] == "OrderWorkflow"
    assert error_ctx.context["sku"] == "TEST404"
    assert any("verifica" in step.lower() or "controllo" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "WF_001"


def test_format_workflow_error_invalid_operation():
    """TEST 18: Workflow error for invalid operation suggests prerequisites."""
    exc = RuntimeError("Cannot close receipt: order_id not found")
    
    error_ctx = ErrorFormatter.format_workflow_error(
        exc=exc,
        workflow="ReceivingWorkflow",
        step="close_receipt",
        context={"order_id": "ORD_MISSING"}
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert error_ctx.context["Step"] == "close_receipt"
    assert len(error_ctx.recovery_steps) >= 1
    assert error_ctx.error_code in ["WF_001", "WF_002", "WF_999"]


def test_format_workflow_error_generic():
    """TEST 19: Generic workflow error has fallback formatting."""
    exc = Exception("Unexpected workflow error")
    
    error_ctx = ErrorFormatter.format_workflow_error(
        exc=exc,
        workflow="ExceptionWorkflow",
        step="record_exception"
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "workflow" in error_ctx.message.lower()
    assert error_ctx.context["Workflow"] == "ExceptionWorkflow"
    assert any("riprova" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "WF_999"


# ============================================================
# TEST 20-22: I/O Error Formatting
# ============================================================

def test_format_file_not_found_error():
    """TEST 20: FileNotFoundError provides path verification guidance."""
    exc = FileNotFoundError("File not found: /path/to/backup.db")
    
    error_ctx = ErrorFormatter.format_io_error(
        exc=exc,
        file_path="/path/to/backup.db",
        operation="restore"
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "non trovato" in error_ctx.message.lower()
    assert error_ctx.context["File"] == "/path/to/backup.db"
    assert any("percorso" in step.lower() or "verifica" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "IO_001"


def test_format_permission_error():
    """TEST 21: PermissionError identifies access rights issue."""
    exc = PermissionError("Permission denied: /protected/file.csv")
    
    error_ctx = ErrorFormatter.format_io_error(
        exc=exc,
        file_path="/protected/file.csv",
        operation="write"
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "permess" in error_ctx.message.lower()
    assert error_ctx.context["File"] == "/protected/file.csv"
    assert any("permessi" in step.lower() or "amministratore" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "IO_002"


def test_format_os_error():
    """TEST 22: OSError provides disk/filesystem guidance."""
    exc = OSError("Disk I/O error")
    
    error_ctx = ErrorFormatter.format_io_error(
        exc=exc,
        file_path="/data/database.db",
        operation="read"
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert error_ctx.context["Operazione"] == "read"
    assert any("spazio" in step.lower() or "file system" in step.lower() for step in error_ctx.recovery_steps)
    assert error_ctx.error_code == "IO_003"


# ============================================================
# TEST 23-25: Generic Error Formatting
# ============================================================

def test_format_generic_error_with_context():
    """TEST 23: Generic error formatting includes context and traceback."""
    exc = ValueError("Unexpected value error")
    
    error_ctx = ErrorFormatter.format_generic_error(
        exc=exc,
        operation="calculate_stock",
        context={"sku": "TEST001", "date": "2026-01-28"}
    )
    
    assert error_ctx.severity == ErrorSeverity.ERROR
    assert "imprevisto" in error_ctx.message.lower()
    assert error_ctx.context["sku"] == "TEST001"
    assert error_ctx.context["date"] == "2026-01-28"
    assert "ValueError" in error_ctx.technical_details
    assert len(error_ctx.recovery_steps) >= 1
    assert error_ctx.error_code == "GENERIC_999"


def test_format_generic_error_includes_traceback():
    """TEST 24: Generic error includes full traceback for debugging."""
    exc = RuntimeError("Test error with traceback")
    
    error_ctx = ErrorFormatter.format_generic_error(
        exc=exc,
        operation="test_operation"
    )
    
    # Traceback should be in technical_details
    assert "RuntimeError" in error_ctx.technical_details
    assert "Test error with traceback" in error_ctx.technical_details
    # Full traceback includes newlines
    assert "\n" in error_ctx.technical_details


def test_error_context_format_for_log():
    """TEST 25: ErrorContext formats correctly for structured logging."""
    ctx = ErrorContext(
        message="Test error for logging",
        severity=ErrorSeverity.ERROR,
        technical_details="Technical details here",
        context={"sku": "TEST001", "operation": "test"},
        recovery_steps=["Step 1"],
        error_code="TEST_LOG"
    )
    
    log_msg = ctx.format_for_log()
    
    # Should include severity
    assert "[ERROR]" in log_msg
    
    # Should include message
    assert "Test error for logging" in log_msg
    
    # Should include context as key=value pairs
    assert "sku=TEST001" in log_msg
    assert "operation=test" in log_msg
    
    # Should include technical details
    assert "Technical details here" in log_msg


# ============================================================
# TEST 26-28: Validation Message Helpers
# ============================================================

def test_validation_messages_required_field():
    """TEST 26: Pre-defined message for required field."""
    msg = ValidationMessages.required_field("sku")
    
    assert "sku" in msg.lower()
    assert "obbligatorio" in msg.lower()


def test_validation_messages_invalid_format():
    """TEST 27: Pre-defined message for invalid format."""
    msg = ValidationMessages.invalid_format("date", "YYYY-MM-DD")
    
    assert "date" in msg.lower()
    assert "yyyy-mm-dd" in msg.lower()
    assert "formato" in msg.lower()


def test_validation_messages_date_range_error():
    """TEST 28: Pre-defined message for date range error."""
    msg = ValidationMessages.date_range_error()
    
    assert "data" in msg.lower()
    assert ">=" in msg or "maggiore" in msg.lower() or "fine" in msg.lower()


# ============================================================
# TEST 29-31: Helper Functions for GUI Integration
# ============================================================

def test_format_error_for_messagebox_repository():
    """TEST 29: format_error_for_messagebox handles repository errors."""
    exc = DuplicateKeyError("SKU TEST001 already exists")
    
    title, message = format_error_for_messagebox(
        exc=exc,
        operation="create_sku",
        sku="TEST001"
    )
    
    assert title == "Errore"
    assert "già esistente" in message.lower()
    assert "TEST001" in message
    assert "Azioni consigliate:" in message


def test_format_error_for_messagebox_database():
    """TEST 30: format_error_for_messagebox handles database errors."""
    exc = sqlite3.OperationalError("database is locked")
    
    title, message = format_error_for_messagebox(
        exc=exc,
        operation="insert_data"
    )
    
    assert title in ["Errore", "Attenzione"]
    assert "occupato" in message.lower() or "locked" in message.lower()
    assert "Azioni consigliate:" in message


def test_format_error_for_messagebox_with_technical():
    """TEST 31: format_error_for_messagebox includes technical when requested."""
    exc = ValueError("Invalid value")
    
    title, message = format_error_for_messagebox(
        exc=exc,
        operation="validate_input",
        include_technical=True
    )
    
    # Should include technical details section
    assert "📋 Dettagli tecnici:" in message or "ValueError" in message


# ============================================================
# TEST 32-35: Validation Helper Functions
# ============================================================

def test_validate_date_format_valid():
    """TEST 32: validate_date_format accepts valid ISO date."""
    is_valid, error = validate_date_format("2026-01-28")
    
    assert is_valid is True
    assert error == ""


def test_validate_date_format_invalid():
    """TEST 33: validate_date_format rejects invalid date format."""
    is_valid, error = validate_date_format("28/01/2026")
    
    assert is_valid is False
    assert "yyyy-mm-dd" in error.lower()


def test_validate_positive_integer_valid():
    """TEST 34: validate_positive_integer accepts positive integers."""
    is_valid, error = validate_positive_integer("42", "quantity")
    
    assert is_valid is True
    assert error == ""


def test_validate_positive_integer_invalid():
    """TEST 35: validate_positive_integer rejects negative/zero/non-integer."""
    # Negative
    is_valid, error = validate_positive_integer("-5", "quantity")
    assert is_valid is False
    assert "positivo" in error.lower()
    
    # Zero
    is_valid, error = validate_positive_integer("0", "quantity")
    assert is_valid is False
    
    # Non-integer
    is_valid, error = validate_positive_integer("abc", "quantity")
    assert is_valid is False
    assert "intero" in error.lower()
    
    # Empty
    is_valid, error = validate_positive_integer("", "quantity")
    assert is_valid is False
    assert "obbligatorio" in error.lower()


# ============================================================
# TEST 36-38: Float Range Validation
# ============================================================

def test_validate_float_range_valid():
    """TEST 36: validate_float_range accepts values in range."""
    is_valid, error = validate_float_range("50.5", "service_level", 0.0, 100.0)
    
    assert is_valid is True
    assert error == ""


def test_validate_float_range_invalid_out_of_range():
    """TEST 37: validate_float_range rejects values outside range."""
    # Too high
    is_valid, error = validate_float_range("150.0", "service_level", 0.0, 100.0)
    assert is_valid is False
    assert "100" in error
    
    # Too low
    is_valid, error = validate_float_range("-10.0", "service_level", 0.0, 100.0)
    assert is_valid is False
    assert "0" in error


def test_validate_float_range_invalid_non_numeric():
    """TEST 38: validate_float_range rejects non-numeric values."""
    is_valid, error = validate_float_range("abc", "service_level", 0.0, 100.0)
    
    assert is_valid is False
    assert "numero" in error.lower()


# ============================================================
# TEST 39-40: Error Severity Classification
# ============================================================

def test_error_severity_classification_repository():
    """TEST 39: Repository errors classified with appropriate severity."""
    # DuplicateKeyError → ERROR
    exc = DuplicateKeyError("Duplicate")
    ctx = ErrorFormatter.format_repository_error(exc, "create")
    assert ctx.severity == ErrorSeverity.ERROR
    
    # NotFoundError → WARNING (less severe, recoverable)
    exc = NotFoundError("Not found")
    ctx = ErrorFormatter.format_repository_error(exc, "get")
    assert ctx.severity == ErrorSeverity.WARNING
    
    # ForeignKeyError → ERROR
    exc = ForeignKeyError("FK violation")
    ctx = ErrorFormatter.format_repository_error(exc, "insert")
    assert ctx.severity == ErrorSeverity.ERROR


def test_error_severity_classification_database():
    """TEST 40: Database errors classified with appropriate severity."""
    # Locked database → WARNING (temporary, retry possible)
    exc = sqlite3.OperationalError("database is locked")
    ctx = ErrorFormatter.format_database_error(exc, "insert")
    assert ctx.severity == ErrorSeverity.WARNING
    
    # Disk full → CRITICAL (system-level issue)
    exc = sqlite3.OperationalError("disk full")
    ctx = ErrorFormatter.format_database_error(exc, "backup")
    assert ctx.severity == ErrorSeverity.CRITICAL
    
    # IntegrityError → ERROR
    exc = sqlite3.IntegrityError("UNIQUE constraint failed")
    ctx = ErrorFormatter.format_database_error(exc, "insert")
    assert ctx.severity == ErrorSeverity.ERROR


# ============================================================
# TEST 41-42: Recovery Steps Quality
# ============================================================

def test_recovery_steps_are_actionable():
    """TEST 41: Recovery steps are specific and actionable."""
    exc = ForeignKeyError("SKU TEST999 does not exist")
    ctx = ErrorFormatter.format_repository_error(exc, "append_transaction", "TEST999")
    
    # Should have at least 2 recovery steps
    assert len(ctx.recovery_steps) >= 2
    
    # All steps should be non-empty and specific
    for step in ctx.recovery_steps:
        assert len(step) > 10  # Not just "Retry"
        # Should contain action verbs
        assert any(verb in step.lower() for verb in [
            "verifica", "controlla", "crea", "modifica", "inserisci",
            "riprova", "contatta", "consulta", "elimina"
        ])


def test_recovery_steps_contextual():
    """TEST 42: Recovery steps are contextual to error type."""
    # Date format error → should mention date format
    ctx = ErrorFormatter.format_validation_error(
        "date", "28/01/2026", "date format", "YYYY-MM-DD"
    )
    assert any("yyyy-mm-dd" in step.lower() for step in ctx.recovery_steps)
    
    # Duplicate error → should mention uniqueness
    exc = DuplicateKeyError("SKU TEST001 already exists")
    ctx = ErrorFormatter.format_repository_error(exc, "create", "TEST001")
    assert any("già in uso" in step.lower() or "codice" in step.lower() for step in ctx.recovery_steps)
    
    # File not found → should mention path
    exc = FileNotFoundError("/path/to/file.csv")
    ctx = ErrorFormatter.format_io_error(exc, "/path/to/file.csv", "read")
    assert any("percorso" in step.lower() for step in ctx.recovery_steps)


# ============================================================
# COMPLETION SUMMARY
# ============================================================

def test_all_error_codes_unique():
    """TEST 43: All error codes are unique across formatters."""
    codes = set()
    
    # Test multiple error scenarios and collect error codes
    test_cases = [
        (DuplicateKeyError("dup"), "create", None, "REPO_001"),
        (ForeignKeyError("fk"), "insert", None, "REPO_002"),
        (NotFoundError("nf"), "get", None, "REPO_003"),
        (BusinessRuleError("br"), "update", None, "REPO_004"),
        (sqlite3.OperationalError("database is locked"), "insert", None, "DB_001"),
        (sqlite3.OperationalError("disk full"), "backup", None, "DB_002"),
        (sqlite3.IntegrityError("UNIQUE"), "insert", None, "DB_004"),
        (FileNotFoundError("nf"), "/path", "read", "IO_001"),
        (PermissionError("pe"), "/path", "write", "IO_002"),
    ]
    
    for exc, op, file_path, expected_code in test_cases:
        if isinstance(exc, (DuplicateKeyError, ForeignKeyError, NotFoundError, BusinessRuleError)):
            ctx = ErrorFormatter.format_repository_error(exc, op)
        elif isinstance(exc, (sqlite3.DatabaseError,)):
            ctx = ErrorFormatter.format_database_error(exc, op)
        elif isinstance(exc, (FileNotFoundError, PermissionError)):
            ctx = ErrorFormatter.format_io_error(exc, file_path, op)
        
        assert ctx.error_code == expected_code
        assert ctx.error_code not in codes or ctx.error_code.endswith("999")  # Generic codes can repeat
        codes.add(ctx.error_code)
    
    # Should have collected at least 9 unique error codes
    assert len(codes) >= 9


def test_error_formatting_comprehensive_coverage():
    """TEST 44: Error formatting covers all major exception types."""
    from src.repositories import RepositoryError
    
    # Repository errors
    assert ErrorFormatter.format_repository_error(DuplicateKeyError("dup"), "op")
    assert ErrorFormatter.format_repository_error(ForeignKeyError("fk"), "op")
    assert ErrorFormatter.format_repository_error(NotFoundError("nf"), "op")
    assert ErrorFormatter.format_repository_error(BusinessRuleError("br"), "op")
    assert ErrorFormatter.format_repository_error(RepositoryError("re"), "op")
    
    # Database errors
    assert ErrorFormatter.format_database_error(sqlite3.OperationalError("locked"), "op")
    assert ErrorFormatter.format_database_error(sqlite3.IntegrityError("unique"), "op")
    assert ErrorFormatter.format_database_error(sqlite3.DatabaseError("db"), "op")
    
    # I/O errors
    assert ErrorFormatter.format_io_error(FileNotFoundError("fnf"), "/path", "read")
    assert ErrorFormatter.format_io_error(PermissionError("pe"), "/path", "write")
    assert ErrorFormatter.format_io_error(OSError("ose"), "/path", "read")
    
    # Workflow errors
    assert ErrorFormatter.format_workflow_error(ValueError("val"), "wf", "step")
    
    # Validation errors
    assert ErrorFormatter.format_validation_error("field", "value", "constraint")
    
    # Generic errors
    assert ErrorFormatter.format_generic_error(Exception("exc"), "op")
    
    # All formatters should return ErrorContext
    # (already verified by returning non-None above)
