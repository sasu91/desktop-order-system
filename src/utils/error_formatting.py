"""
Error UX & Messaging Module (FASE 7 - TASK 7.6)

Provides user-friendly error formatting, contextual messages, and recovery guidance.
Transforms technical exceptions into actionable, understandable messages for end users.
"""

from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import traceback
import sqlite3


# ============================================================
# Error Severity Levels
# ============================================================

class ErrorSeverity(Enum):
    """Error severity classification for UI presentation."""
    
    INFO = "info"           # Informational (no action needed)
    WARNING = "warning"     # Caution (optional action)
    ERROR = "error"         # Error (action required)
    CRITICAL = "critical"   # Critical (system-level issue)


# ============================================================
# Error Context
# ============================================================

@dataclass
class ErrorContext:
    """
    Structured error context for user-friendly messaging.
    
    Attributes:
        message: User-friendly error description
        severity: Error severity level
        technical_details: Technical error info (for logs/debugging)
        context: Additional context (SKU, operation, data)
        recovery_steps: List of recovery actions user can take
        error_code: Optional error code for support/documentation
    """
    message: str
    severity: ErrorSeverity
    technical_details: str
    context: Dict[str, Any]
    recovery_steps: list[str]
    error_code: Optional[str] = None
    
    def format_for_display(self, include_technical: bool = False) -> str:
        """
        Format error for GUI display (messagebox).
        
        Args:
            include_technical: Include technical details in message
            
        Returns:
            Formatted error message suitable for GUI display
        """
        lines = [self.message]
        
        # Add context if present
        if self.context:
            lines.append("")
            lines.append("Dettagli:")
            for key, value in self.context.items():
                if value is not None:
                    lines.append(f"  • {key}: {value}")
        
        # Add recovery steps
        if self.recovery_steps:
            lines.append("")
            lines.append("Azioni consigliate:")
            for i, step in enumerate(self.recovery_steps, 1):
                lines.append(f"  {i}. {step}")
        
        # Add technical details if requested (for advanced users)
        if include_technical and self.technical_details:
            lines.append("")
            lines.append("📋 Dettagli tecnici:")
            lines.append(f"  {self.technical_details}")
        
        # Add error code if present
        if self.error_code:
            lines.append("")
            lines.append(f"Codice errore: {self.error_code}")
        
        return "\n".join(lines)
    
    def format_for_log(self) -> str:
        """Format error for structured logging."""
        context_str = ", ".join(f"{k}={v}" for k, v in self.context.items() if v is not None)
        return f"[{self.severity.value.upper()}] {self.message} | Context: {context_str} | Technical: {self.technical_details}"


# ============================================================
# Error Formatters (Transform technical → user-friendly)
# ============================================================

class ErrorFormatter:
    """
    Main error formatting utility.
    Transforms exceptions into user-friendly ErrorContext objects.
    """
    
    @staticmethod
    def format_repository_error(
        exc: Exception,
        operation: str,
        sku: Optional[str] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> ErrorContext:
        """
        Format repository-level errors (from src/repositories.py).
        
        Args:
            exc: The exception raised
            operation: Operation that failed (e.g., "create_sku", "append_transaction")
            sku: SKU involved (if applicable)
            additional_context: Additional context data
            
        Returns:
            ErrorContext with user-friendly message and recovery steps
        """
        from src.repositories import (
            DuplicateKeyError,
            ForeignKeyError,
            NotFoundError,
            BusinessRuleError,
            RepositoryError
        )
        
        context = {"Operazione": operation}
        if sku:
            context["SKU"] = sku
        if additional_context:
            context.update(additional_context)
        
        # DuplicateKeyError
        if isinstance(exc, DuplicateKeyError):
            return ErrorContext(
                message=f"Elemento già esistente: {str(exc)}",
                severity=ErrorSeverity.ERROR,
                technical_details=f"DuplicateKeyError: {str(exc)}",
                context=context,
                recovery_steps=[
                    "Verifica che il codice SKU o ID non sia già in uso",
                    "Usa un codice diverso oppure modifica l'elemento esistente",
                ],
                error_code="REPO_001"
            )
        
        # ForeignKeyError
        elif isinstance(exc, ForeignKeyError):
            return ErrorContext(
                message=f"Riferimento non valido: {str(exc)}",
                severity=ErrorSeverity.ERROR,
                technical_details=f"ForeignKeyError: {str(exc)}",
                context=context,
                recovery_steps=[
                    "Verifica che lo SKU esistete nel sistema",
                    "Controlla che tutti i riferimenti (SKU, order_id) siano corretti",
                    "Se necessario, crea prima lo SKU mancante",
                ],
                error_code="REPO_002"
            )
        
        # NotFoundError
        elif isinstance(exc, NotFoundError):
            return ErrorContext(
                message=f"Elemento non trovato: {str(exc)}",
                severity=ErrorSeverity.WARNING,
                technical_details=f"NotFoundError: {str(exc)}",
                context=context,
                recovery_steps=[
                    "Verifica che il codice inserito sia corretto",
                    "Controlla la lista degli elementi esistenti",
                    "Se necessario, crea l'elemento prima di riferirlo",
                ],
                error_code="REPO_003"
            )
        
        # BusinessRuleError
        elif isinstance(exc, BusinessRuleError):
            return ErrorContext(
                message=f"Regola di business violata: {str(exc)}",
                severity=ErrorSeverity.ERROR,
                technical_details=f"BusinessRuleError: {str(exc)}",
                context=context,
                recovery_steps=[
                    "Controlla i vincoli del campo (es. quantità > 0, date valide)",
                    "Verifica che i dati rispettino le regole di business",
                    "Consulta la documentazione per i valori ammessi",
                ],
                error_code="REPO_004"
            )
        
        # Generic RepositoryError
        elif isinstance(exc, RepositoryError):
            return ErrorContext(
                message=f"Errore nell'operazione: {str(exc)}",
                severity=ErrorSeverity.ERROR,
                technical_details=f"RepositoryError: {str(exc)}",
                context=context,
                recovery_steps=[
                    "Controlla i dati inseriti",
                    "Verifica la connessione al database",
                    "Riprova l'operazione",
                ],
                error_code="REPO_999"
            )
        
        # Fallback for unknown errors
        else:
            return ErrorContext(
                message=f"Errore imprevisto durante {operation}",
                severity=ErrorSeverity.ERROR,
                technical_details=f"{type(exc).__name__}: {str(exc)}",
                context=context,
                recovery_steps=[
                    "Riprova l'operazione",
                    "Se l'errore persiste, contatta il supporto",
                ],
                error_code="REPO_UNKNOWN"
            )
    
    @staticmethod
    def format_validation_error(
        field: str,
        value: Any,
        constraint: str,
        expected: Optional[str] = None
    ) -> ErrorContext:
        """
        Format validation errors (form input, data validation).
        
        Args:
            field: Field name that failed validation
            value: Value that was rejected
            constraint: Constraint that was violated
            expected: Expected value/format (optional)
            
        Returns:
            ErrorContext with validation guidance
        """
        message = f"Campo '{field}' non valido"
        if expected:
            message += f": {expected}"
        
        recovery_steps = [
            f"Verifica il formato del campo '{field}'",
        ]
        
        # Add constraint-specific guidance
        if "format" in constraint.lower() or "date" in constraint.lower():
            recovery_steps.append("Formato data: YYYY-MM-DD (es. 2026-01-28)")
        elif "range" in constraint.lower() or "between" in constraint.lower():
            recovery_steps.append("Verifica che il valore sia nell'intervallo ammesso")
        elif "positive" in constraint.lower() or "> 0" in constraint:
            recovery_steps.append("Il valore deve essere un numero positivo")
        elif "integer" in constraint.lower() or "number" in constraint.lower():
            recovery_steps.append("Il valore deve essere un numero intero")
        
        return ErrorContext(
            message=message,
            severity=ErrorSeverity.WARNING,
            technical_details=f"ValidationError: field={field}, value={value}, constraint={constraint}",
            context={"Campo": field, "Valore inserito": str(value), "Vincolo": constraint},
            recovery_steps=recovery_steps,
            error_code="VAL_001"
        )
    
    @staticmethod
    def format_database_error(
        exc: Exception,
        operation: str,
        context: Optional[Dict[str, Any]] = None
    ) -> ErrorContext:
        """
        Format database-level errors (SQLite errors).
        
        Args:
            exc: Database exception
            operation: Operation that failed
            context: Additional context
            
        Returns:
            ErrorContext with database error guidance
        """
        ctx = {"Operazione": operation}
        if context:
            ctx.update(context)
        
        # SQLite3 OperationalError (locked, disk full, etc.)
        if isinstance(exc, sqlite3.OperationalError):
            exc_str = str(exc).lower()
            
            if "locked" in exc_str or "busy" in exc_str:
                return ErrorContext(
                    message="Database temporaneamente occupato",
                    severity=ErrorSeverity.WARNING,
                    technical_details=str(exc),
                    context=ctx,
                    recovery_steps=[
                        "Attendi qualche secondo e riprova",
                        "Chiudi altre finestre che potrebbero accedere al database",
                        "Se l'errore persiste, riavvia l'applicazione",
                    ],
                    error_code="DB_001"
                )
            
            elif "disk" in exc_str or "full" in exc_str:
                return ErrorContext(
                    message="Spazio su disco insufficiente",
                    severity=ErrorSeverity.CRITICAL,
                    technical_details=str(exc),
                    context=ctx,
                    recovery_steps=[
                        "Libera spazio su disco",
                        "Elimina file temporanei o vecchi backup",
                        "Sposta il database su un disco con più spazio",
                    ],
                    error_code="DB_002"
                )
            
            else:
                return ErrorContext(
                    message="Errore di accesso al database",
                    severity=ErrorSeverity.ERROR,
                    technical_details=str(exc),
                    context=ctx,
                    recovery_steps=[
                        "Verifica che il database non sia corrotto",
                        "Esegui un backup e ripristino",
                        "Contatta il supporto se il problema persiste",
                    ],
                    error_code="DB_003"
                )
        
        # SQLite3 IntegrityError (constraint violations)
        elif isinstance(exc, sqlite3.IntegrityError):
            exc_str = str(exc).lower()
            
            if "unique" in exc_str:
                return ErrorContext(
                    message="Violazione di unicità: elemento già esistente",
                    severity=ErrorSeverity.ERROR,
                    technical_details=str(exc),
                    context=ctx,
                    recovery_steps=[
                        "Verifica che il codice/ID non sia già in uso",
                        "Usa un identificatore diverso",
                    ],
                    error_code="DB_004"
                )
            
            elif "foreign key" in exc_str:
                return ErrorContext(
                    message="Riferimento non valido: elemento collegato non esiste",
                    severity=ErrorSeverity.ERROR,
                    technical_details=str(exc),
                    context=ctx,
                    recovery_steps=[
                        "Verifica che lo SKU o l'elemento riferito esista",
                        "Crea prima l'elemento mancante",
                    ],
                    error_code="DB_005"
                )
            
            elif "check" in exc_str:
                return ErrorContext(
                    message="Valore non ammesso: vincolo di integrità violato",
                    severity=ErrorSeverity.ERROR,
                    technical_details=str(exc),
                    context=ctx,
                    recovery_steps=[
                        "Controlla che i valori rispettino i vincoli (es. qty > 0)",
                        "Verifica le date (start_date <= end_date)",
                    ],
                    error_code="DB_006"
                )
            
            else:
                return ErrorContext(
                    message="Violazione di integrità del database",
                    severity=ErrorSeverity.ERROR,
                    technical_details=str(exc),
                    context=ctx,
                    recovery_steps=[
                        "Controlla i dati inseriti",
                        "Verifica che rispettino tutti i vincoli di integrità",
                    ],
                    error_code="DB_007"
                )
        
        # SQLite3 DatabaseError (generic)
        elif isinstance(exc, sqlite3.DatabaseError):
            return ErrorContext(
                message="Errore del database",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context=ctx,
                recovery_steps=[
                    "Riavvia l'applicazione",
                    "Verifica l'integrità del database con gli strumenti di diagnosi",
                    "Ripristina da backup se il problema persiste",
                ],
                error_code="DB_999"
            )
        
        # Fallback
        else:
            return ErrorContext(
                message=f"Errore database non specificato: {type(exc).__name__}",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context=ctx,
                recovery_steps=[
                    "Riprova l'operazione",
                    "Contatta il supporto se l'errore persiste",
                ],
                error_code="DB_UNKNOWN"
            )
    
    @staticmethod
    def format_workflow_error(
        exc: Exception,
        workflow: str,
        step: str,
        context: Optional[Dict[str, Any]] = None
    ) -> ErrorContext:
        """
        Format workflow-level errors (order generation, receiving, etc.).
        
        Args:
            exc: Exception raised during workflow
            workflow: Workflow name (e.g., "OrderWorkflow", "ReceivingWorkflow")
            step: Step where error occurred
            context: Additional context
            
        Returns:
            ErrorContext with workflow-specific guidance
        """
        ctx = {"Workflow": workflow, "Step": step}
        if context:
            ctx.update(context)
        
        # Check for common patterns
        exc_str = str(exc).lower()
        
        if "not found" in exc_str or "missing" in exc_str:
            return ErrorContext(
                message=f"Dati mancanti nel workflow '{workflow}'",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context=ctx,
                recovery_steps=[
                    "Verifica che tutti i dati necessari siano presenti",
                    "Controlla che gli SKU esistano nel sistema",
                    "Se necessario, crea i dati mancanti prima di procedere",
                ],
                error_code="WF_001"
            )
        
        elif "invalid" in exc_str or "not allowed" in exc_str:
            return ErrorContext(
                message=f"Operazione non ammessa in '{workflow}'",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context=ctx,
                recovery_steps=[
                    "Verifica che l'operazione sia valida per lo stato corrente",
                    "Controlla i prerequisiti del workflow",
                ],
                error_code="WF_002"
            )
        
        else:
            return ErrorContext(
                message=f"Errore nel workflow '{workflow}' allo step '{step}'",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context=ctx,
                recovery_steps=[
                    "Riprova l'operazione dall'inizio",
                    "Verifica che tutti i dati siano corretti",
                    "Se l'errore persiste, contatta il supporto",
                ],
                error_code="WF_999"
            )
    
    @staticmethod
    def format_io_error(
        exc: Exception,
        file_path: str,
        operation: str
    ) -> ErrorContext:
        """
        Format I/O errors (file not found, permission denied, etc.).
        
        Args:
            exc: I/O exception
            file_path: File path that caused the error
            operation: Operation attempted (read, write, delete)
            
        Returns:
            ErrorContext with I/O error guidance
        """
        if isinstance(exc, FileNotFoundError):
            return ErrorContext(
                message=f"File non trovato: {file_path}",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context={"File": file_path, "Operazione": operation},
                recovery_steps=[
                    "Verifica che il percorso del file sia corretto",
                    "Controlla che il file non sia stato spostato o eliminato",
                    "Se è un backup/export, verifica che la cartella esista",
                ],
                error_code="IO_001"
            )
        
        elif isinstance(exc, PermissionError):
            return ErrorContext(
                message=f"Permessi insufficienti per accedere a: {file_path}",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context={"File": file_path, "Operazione": operation},
                recovery_steps=[
                    "Verifica di avere i permessi di lettura/scrittura sul file",
                    "Controlla che il file non sia aperto in un'altra applicazione",
                    "Se necessario, esegui l'applicazione come amministratore",
                ],
                error_code="IO_002"
            )
        
        elif isinstance(exc, (OSError, IOError)):
            return ErrorContext(
                message=f"Errore I/O durante {operation}: {file_path}",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context={"File": file_path, "Operazione": operation},
                recovery_steps=[
                    "Verifica lo spazio disco disponibile",
                    "Controlla che il file system sia accessibile",
                    "Riprova l'operazione",
                ],
                error_code="IO_003"
            )
        
        else:
            return ErrorContext(
                message=f"Errore imprevisto con file: {file_path}",
                severity=ErrorSeverity.ERROR,
                technical_details=str(exc),
                context={"File": file_path, "Operazione": operation},
                recovery_steps=[
                    "Riprova l'operazione",
                    "Contatta il supporto se il problema persiste",
                ],
                error_code="IO_999"
            )
    
    @staticmethod
    def format_generic_error(
        exc: Exception,
        operation: str,
        context: Optional[Dict[str, Any]] = None
    ) -> ErrorContext:
        """
        Format generic/unknown errors with minimal guidance.
        
        Args:
            exc: Exception
            operation: Operation that failed
            context: Additional context
            
        Returns:
            ErrorContext with generic recovery steps
        """
        ctx = {"Operazione": operation}
        if context:
            ctx.update(context)
        
        return ErrorContext(
            message=f"Errore imprevisto durante {operation}",
            severity=ErrorSeverity.ERROR,
            technical_details=f"{type(exc).__name__}: {str(exc)}\n{traceback.format_exc()}",
            context=ctx,
            recovery_steps=[
                "Riprova l'operazione",
                "Riavvia l'applicazione se il problema persiste",
                "Contatta il supporto con il codice errore",
            ],
            error_code="GENERIC_999"
        )


# ============================================================
# Validation Message Helpers
# ============================================================

class ValidationMessages:
    """Pre-defined validation messages for common scenarios."""
    
    @staticmethod
    def required_field(field_name: str) -> str:
        """Message for missing required field."""
        return f"Campo '{field_name}' obbligatorio"
    
    @staticmethod
    def invalid_format(field_name: str, expected_format: str) -> str:
        """Message for invalid format."""
        return f"Campo '{field_name}' non valido. Formato atteso: {expected_format}"
    
    @staticmethod
    def out_of_range(field_name: str, min_val: Any, max_val: Any) -> str:
        """Message for out-of-range value."""
        return f"Campo '{field_name}' deve essere tra {min_val} e {max_val}"
    
    @staticmethod
    def date_format_error() -> str:
        """Message for date format error."""
        return "Formato data non valido. Usa: YYYY-MM-DD (es. 2026-01-28)"
    
    @staticmethod
    def date_range_error() -> str:
        """Message for invalid date range."""
        return "Data fine deve essere >= data inizio"
    
    @staticmethod
    def positive_number_required(field_name: str) -> str:
        """Message for positive number requirement."""
        return f"Campo '{field_name}' deve essere un numero positivo"
    
    @staticmethod
    def integer_required(field_name: str) -> str:
        """Message for integer requirement."""
        return f"Campo '{field_name}' deve essere un numero intero"
    
    @staticmethod
    def duplicate_entry(identifier: str) -> str:
        """Message for duplicate entry."""
        return f"Elemento '{identifier}' già esistente nel sistema"
    
    @staticmethod
    def not_found(entity: str, identifier: str) -> str:
        """Message for not found error."""
        return f"{entity} '{identifier}' non trovato"
    
    @staticmethod
    def form_validation_passed() -> str:
        """Message when form validation passes."""
        return "✓ Pronto"


# ============================================================
# Helper Functions for GUI Integration
# ============================================================

def format_error_for_messagebox(
    exc: Exception,
    operation: str,
    sku: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    include_technical: bool = False
) -> Tuple[str, str]:
    """
    Convenience function for GUI error display.
    
    Args:
        exc: Exception that occurred
        operation: Operation that failed
        sku: SKU involved (if applicable)
        context: Additional context
        include_technical: Include technical details
        
    Returns:
        Tuple of (title, message) for messagebox.showerror
    """
    # Determine appropriate formatter
    from src.repositories import RepositoryError
    
    if isinstance(exc, RepositoryError):
        error_ctx = ErrorFormatter.format_repository_error(exc, operation, sku, context)
    elif isinstance(exc, sqlite3.DatabaseError):
        error_ctx = ErrorFormatter.format_database_error(exc, operation, context)
    elif isinstance(exc, (FileNotFoundError, PermissionError, OSError, IOError)):
        file_path = context.get("file", "") if context else ""
        error_ctx = ErrorFormatter.format_io_error(exc, file_path, operation)
    else:
        error_ctx = ErrorFormatter.format_generic_error(exc, operation, context)
    
    # Format for display
    title_map = {
        ErrorSeverity.INFO: "Informazione",
        ErrorSeverity.WARNING: "Attenzione",
        ErrorSeverity.ERROR: "Errore",
        ErrorSeverity.CRITICAL: "Errore Critico",
    }
    
    title = title_map.get(error_ctx.severity, "Errore")
    message = error_ctx.format_for_display(include_technical=include_technical)
    
    return (title, message)


def validate_date_format(date_str: str) -> Tuple[bool, str]:
    """
    Validate date format (YYYY-MM-DD).
    
    Args:
        date_str: Date string to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    from datetime import date
    
    if not date_str or not date_str.strip():
        return (False, "Data obbligatoria")
    
    try:
        date.fromisoformat(date_str)
        return (True, "")
    except ValueError:
        return (False, ValidationMessages.date_format_error())


def validate_positive_integer(value_str: str, field_name: str) -> Tuple[bool, str]:
    """
    Validate positive integer.
    
    Args:
        value_str: Value to validate
        field_name: Field name for error message
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not value_str or not value_str.strip():
        return (False, ValidationMessages.required_field(field_name))
    
    try:
        value = int(value_str)
        if value <= 0:
            return (False, ValidationMessages.positive_number_required(field_name))
        return (True, "")
    except ValueError:
        return (False, ValidationMessages.integer_required(field_name))


def validate_float_range(
    value_str: str,
    field_name: str,
    min_val: float,
    max_val: float
) -> Tuple[bool, str]:
    """
    Validate float in range.
    
    Args:
        value_str: Value to validate
        field_name: Field name for error message
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not value_str or not value_str.strip():
        return (False, ValidationMessages.required_field(field_name))
    
    try:
        value = float(value_str)
        if value < min_val or value > max_val:
            return (False, ValidationMessages.out_of_range(field_name, min_val, max_val))
        return (True, "")
    except ValueError:
        return (False, f"Campo '{field_name}' deve essere un numero decimale")
