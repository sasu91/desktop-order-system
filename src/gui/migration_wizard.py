"""
Migration Wizard Dialog - GUI for CSV to SQLite migration

Provides user-friendly interface for running data migration:
- Confirmation screen with warnings
- Progress indication (indeterminate progress bar)
- Real-time log output in scrolled text widget
- Success/failure reporting
- Background thread execution (non-blocking UI)
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import sys
import io
from pathlib import Path
from typing import Callable, Optional


class MigrationWizardDialog:
    """
    Dialog for running CSV to SQLite migration.
    
    Features:
    - Non-blocking UI (migration runs in background thread)
    - Real-time log output capture
    - Progress indication
    - Error handling with user-friendly messages
    """
    
    def __init__(self, parent: tk.Tk, on_complete: Optional[Callable[[bool], None]] = None):
        """
        Initialize migration wizard dialog.
        
        Args:
            parent: Parent Tkinter window
            on_complete: Optional callback(success: bool) called when migration completes
        """
        self.parent = parent
        self.on_complete = on_complete
        
        # Create modal dialog
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Migration Wizard - CSV → SQLite")
        self.dialog.geometry("700x500")
        self.dialog.resizable(True, True)
        
        # Make modal
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Center on parent
        self._center_on_parent()
        
        # Migration state
        self.is_running = False
        self.migration_thread = None
        self.log_buffer = io.StringIO()
        
        # Build UI
        self._build_ui()
        
        # Handle window close
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _center_on_parent(self):
        """Center dialog on parent window"""
        self.dialog.update_idletasks()
        
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()
        
        dialog_width = self.dialog.winfo_width()
        dialog_height = self.dialog.winfo_height()
        
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        
        self.dialog.geometry(f"+{x}+{y}")
    
    def _build_ui(self):
        """Build dialog UI components"""
        main_frame = ttk.Frame(self.dialog, padding=15)
        main_frame.pack(fill="both", expand=True)
        
        # Title
        title_label = ttk.Label(
            main_frame,
            text="🚀 Migrazione Dati CSV → SQLite",
            font=("Helvetica", 14, "bold")
        )
        title_label.pack(anchor="w", pady=(0, 10))
        
        # Description
        desc_text = (
            "Questa procedura migrerà tutti i dati dai file CSV/JSON al database SQLite.\n\n"
            "Operazioni eseguite:\n"
            "• Lettura e validazione dati da CSV/JSON\n"
            "• Creazione schema database SQLite\n"
            "• Importazione dati con controlli integrità\n"
            "• Generazione report dettagliato\n\n"
            "⚠️ RACCOMANDAZIONI:\n"
            "• Backup dei file CSV prima di procedere\n"
            "• Chiudere altre applicazioni che accedono ai file\n"
            "• Non interrompere la migrazione una volta avviata"
        )
        
        desc_label = ttk.Label(
            main_frame,
            text=desc_text,
            font=("Helvetica", 9),
            foreground="gray",
            justify="left"
        )
        desc_label.pack(anchor="w", pady=(0, 15))
        
        # Progress frame
        progress_frame = ttk.LabelFrame(main_frame, text="Progresso", padding=10)
        progress_frame.pack(fill="x", pady=(0, 10))
        
        self.progress_label = ttk.Label(
            progress_frame,
            text="Pronto per iniziare migrazione...",
            font=("Helvetica", 9)
        )
        self.progress_label.pack(anchor="w", pady=(0, 5))
        
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            mode='indeterminate',
            length=600
        )
        self.progress_bar.pack(fill="x")
        
        # Log output frame
        log_frame = ttk.LabelFrame(main_frame, text="Log Output", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=12,
            width=80,
            font=("Courier", 9),
            wrap="word"
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")  # Read-only
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x", pady=(10, 0))
        
        self.start_button = ttk.Button(
            button_frame,
            text="▶️ Avvia Migrazione",
            command=self._start_migration
        )
        self.start_button.pack(side="left", padx=5)
        
        self.close_button = ttk.Button(
            button_frame,
            text="❌ Chiudi",
            command=self._on_close
        )
        self.close_button.pack(side="right", padx=5)
    
    def _append_log(self, message: str):
        """Append message to log text widget"""
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")  # Auto-scroll
        self.log_text.config(state="disabled")
    
    def _start_migration(self):
        """Start migration in background thread"""
        if self.is_running:
            messagebox.showwarning("Migrazione in Corso", "Migrazione già in corso.")
            return
        
        # Confirm
        confirm = messagebox.askyesno(
            "Conferma Migrazione",
            "Avviare la migrazione dei dati?\n\n"
            "Assicurarsi di aver fatto un backup dei file CSV."
        )
        
        if not confirm:
            return
        
        # Update UI state
        self.is_running = True
        self.start_button.config(state="disabled")
        self.close_button.config(state="disabled")
        self.progress_bar.start(10)  # Indeterminate animation
        self.progress_label.config(text="⏳ Migrazione in corso...")
        
        # Clear log
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        
        # Start migration thread
        self.migration_thread = threading.Thread(
            target=self._run_migration_worker,
            daemon=True
        )
        self.migration_thread.start()
    
    def _run_migration_worker(self):
        """Worker thread: run migration and capture output"""
        success = False
        
        try:
            self._append_log("🚀 Inizio migrazione CSV → SQLite...")
            self._append_log("")
            
            # Import migration tool
            from ..migrate_csv_to_sqlite import MigrationOrchestrator
            from ..db import open_connection
            from config import DATA_DIR, DATABASE_PATH
            
            self._append_log(f"📂 Data directory: {DATA_DIR}")
            self._append_log(f"🗄️ Database path: {DATABASE_PATH}")
            self._append_log("")
            
            # Open database connection
            conn = open_connection(DATABASE_PATH)
            
            # Create orchestrator
            orchestrator = MigrationOrchestrator(conn=conn, csv_dir=DATA_DIR)
            
            # Redirect stdout to capture migration logs
            old_stdout = sys.stdout
            sys.stdout = self.log_buffer
            
            try:
                # Run migration
                self._append_log("📊 Esecuzione migrazione...")
                self._append_log("-" * 60)
                
                report = orchestrator.migrate_all()
                
                # Restore stdout
                sys.stdout = old_stdout
                
                # Append captured logs
                captured_output = self.log_buffer.getvalue()
                if captured_output:
                    for line in captured_output.split('\n'):
                        if line.strip():
                            self._append_log(line)
                
                self._append_log("-" * 60)
                self._append_log("")
                
                # Display report summary
                self._append_log("📋 REPORT MIGRAZIONE:")
                self._append_log(f"  ✓ Tabelle migrate: {len(report.tables_migrated)}")
                self._append_log(f"  ✓ Righe inserite totali: {report.total_inserted()}")
                self._append_log(f"  ⚠ Errori totali: {report.total_errors()}")
                
                # Display per-table stats
                if report.table_stats:
                    self._append_log("")
                    self._append_log("  Dettagli per tabella:")
                    for table, stats in report.table_stats.items():
                        self._append_log(f"    • {table}: {stats.inserted} inseriti, {stats.skipped} saltati, {stats.errors} errori")
                
                # Display validation errors (if any)
                if report.has_errors():
                    self._append_log("")
                    self._append_log("❌ ERRORI DI VALIDAZIONE:")
                    error_count = 0
                    for table, stats in report.table_stats.items():
                        for error in stats.validation_errors:
                            if error_count >= 10:  # Max 10 errors
                                break
                            error_msg = error.error or "(no details)"
                            self._append_log(f"  • [{table}] Row {error.row_num}: {error_msg}")
                            error_count += 1
                        if error_count >= 10:
                            break
                
                success = not report.has_errors()
                
            except Exception as e:
                sys.stdout = old_stdout
                self._append_log("")
                self._append_log(f"❌ Errore durante migrazione: {e}")
                success = False
        
        except Exception as e:
            self._append_log("")
            self._append_log(f"❌ Errore critico: {e}")
            success = False
        
        # Update UI on main thread
        self.dialog.after(0, lambda: self._migration_complete(success))
    
    def _migration_complete(self, success: bool):
        """Called when migration completes (on main thread)"""
        self.is_running = False
        self.progress_bar.stop()
        self.start_button.config(state="normal")
        self.close_button.config(state="normal")
        
        if success:
            self.progress_label.config(text="✅ Migrazione completata con successo!")
            messagebox.showinfo(
                "Migrazione Completata",
                "Migrazione completata con successo!\n\n"
                "Database SQLite creato e popolato.\n"
                "È ora possibile switchare al backend SQLite nelle impostazioni."
            )
        else:
            self.progress_label.config(text="❌ Migrazione fallita (vedere log)")
            messagebox.showerror(
                "Migrazione Fallita",
                "Migrazione fallita con errori.\n\n"
                "Controllare il log output per dettagli."
            )
        
        # Call completion callback
        if self.on_complete:
            self.on_complete(success)
    
    def _on_close(self):
        """Handle window close request"""
        if self.is_running:
            messagebox.showwarning(
                "Migrazione in Corso",
                "Impossibile chiudere la finestra durante la migrazione.\n"
                "Attendere il completamento."
            )
            return
        
        self.dialog.destroy()
    
    def show(self):
        """Show dialog (blocking)"""
        self.dialog.wait_window()


# Test entry point
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()  # Hide root window
    
    def on_complete(success):
        print(f"Migration complete: {'SUCCESS' if success else 'FAILED'}")
    
    wizard = MigrationWizardDialog(root, on_complete=on_complete)
    wizard.show()
    
    root.destroy()
