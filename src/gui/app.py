"""
Main GUI application for desktop-order-system.

Tkinter-based desktop UI with multiple tabs.
"""
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date, timedelta
from pathlib import Path

from ..persistence.csv_layer import CSVLayer
from ..domain.ledger import StockCalculator, validate_ean
from ..domain.models import SKU, EventType
from ..workflows.order import OrderWorkflow, calculate_daily_sales_average
from ..workflows.receiving import ReceivingWorkflow, ExceptionWorkflow


class DesktopOrderApp:
    """Main application window."""
    
    def __init__(self, root: tk.Tk, data_dir: Path = None):
        """
        Initialize the application.
        
        Args:
            root: Tkinter root window
            data_dir: Data directory for CSV files (defaults to ./data)
        """
        self.root = root
        self.root.title("Desktop Order System")
        self.root.geometry("1000x600")
        
        # Initialize CSV layer
        self.csv_layer = CSVLayer(data_dir=data_dir)
        
        # Initialize workflows
        self.order_workflow = OrderWorkflow(self.csv_layer, lead_time_days=7)
        self.receiving_workflow = ReceivingWorkflow(self.csv_layer)
        self.exception_workflow = ExceptionWorkflow(self.csv_layer)
        
        # Current AsOf date (for stock view)
        self.asof_date = date.today()
        
        # Current exception date (for exception view)
        self.exception_date = date.today()
        
        # Create GUI
        self._create_widgets()
        self._refresh_all()
    
    def _create_widgets(self):
        """Create main UI components."""
        # Menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Refresh", command=self._refresh_all)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # Tab notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Create tabs
        self.stock_tab = ttk.Frame(self.notebook)
        self.order_tab = ttk.Frame(self.notebook)
        self.exception_tab = ttk.Frame(self.notebook)
        self.admin_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.stock_tab, text="Stock (CalcolAto)")
        self.notebook.add(self.order_tab, text="Ordini")
        self.notebook.add(self.exception_tab, text="Eccezioni")
        self.notebook.add(self.admin_tab, text="Admin")
        
        # Build tab contents
        self._build_stock_tab()
        self._build_order_tab()
        self._build_exception_tab()
        self._build_admin_tab()
    
    def _build_stock_tab(self):
        """Build Stock tab (read-only stock view)."""
        frame = ttk.Frame(self.stock_tab)
        frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Date picker
        date_frame = ttk.Frame(frame)
        date_frame.pack(side="top", fill="x", pady=5)
        
        ttk.Label(date_frame, text="AsOf Date:").pack(side="left", padx=5)
        self.asof_date_var = tk.StringVar(value=self.asof_date.isoformat())
        ttk.Entry(date_frame, textvariable=self.asof_date_var, width=15).pack(side="left", padx=5)
        ttk.Button(date_frame, text="Update", command=self._refresh_stock_tab).pack(side="left", padx=5)
        
        # Stock table
        self.stock_treeview = ttk.Treeview(
            frame,
            columns=("SKU", "Description", "On Hand", "On Order", "Available"),
            height=15,
        )
        self.stock_treeview.column("#0", width=0, stretch=tk.NO)
        self.stock_treeview.column("SKU", anchor=tk.W, width=80)
        self.stock_treeview.column("Description", anchor=tk.W, width=250)
        self.stock_treeview.column("On Hand", anchor=tk.CENTER, width=100)
        self.stock_treeview.column("On Order", anchor=tk.CENTER, width=100)
        self.stock_treeview.column("Available", anchor=tk.CENTER, width=100)
        
        self.stock_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.stock_treeview.heading("Description", text="Description", anchor=tk.W)
        self.stock_treeview.heading("On Hand", text="On Hand", anchor=tk.CENTER)
        self.stock_treeview.heading("On Order", text="On Order", anchor=tk.CENTER)
        self.stock_treeview.heading("Available", text="Available", anchor=tk.CENTER)
        
        self.stock_treeview.pack(fill="both", expand=True)
    
    def _build_order_tab(self):
        """Build Order tab (proposal + confirmation)."""
        frame = ttk.Frame(self.order_tab)
        frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        ttk.Label(frame, text="Order Management (TBD)", font=("Helvetica", 12)).pack(pady=10)
        ttk.Label(frame, text="This tab will contain order proposal and confirmation UI").pack()
    
    def _build_exception_tab(self):
        """Build Exception tab (WASTE, ADJUST, UNFULFILLED)."""
        main_frame = ttk.Frame(self.exception_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="Exception Management", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # === QUICK ENTRY FORM (INLINE) ===
        form_frame = ttk.LabelFrame(main_frame, text="Quick Entry", padding=10)
        form_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Row 1: Event Type and SKU
        row1_frame = ttk.Frame(form_frame)
        row1_frame.pack(side="top", fill="x", pady=5)
        
        ttk.Label(row1_frame, text="Event Type:", width=12).pack(side="left", padx=(0, 5))
        self.exception_type_var = tk.StringVar(value="WASTE")
        exception_type_combo = ttk.Combobox(
            row1_frame,
            textvariable=self.exception_type_var,
            values=["WASTE", "ADJUST", "UNFULFILLED"],
            state="readonly",
            width=15,
        )
        exception_type_combo.pack(side="left", padx=(0, 20))
        
        ttk.Label(row1_frame, text="SKU:", width=8).pack(side="left", padx=(0, 5))
        self.exception_sku_var = tk.StringVar()
        self.exception_sku_combo = ttk.Combobox(
            row1_frame,
            textvariable=self.exception_sku_var,
            width=20,
        )
        self.exception_sku_combo.pack(side="left", padx=(0, 20))
        
        # Populate SKU dropdown
        self._populate_exception_sku_dropdown()
        
        ttk.Label(row1_frame, text="Quantity:", width=8).pack(side="left", padx=(0, 5))
        self.exception_qty_var = tk.StringVar()
        ttk.Entry(row1_frame, textvariable=self.exception_qty_var, width=10).pack(side="left", padx=(0, 20))
        
        # Row 2: Date and Notes
        row2_frame = ttk.Frame(form_frame)
        row2_frame.pack(side="top", fill="x", pady=5)
        
        ttk.Label(row2_frame, text="Date:", width=12).pack(side="left", padx=(0, 5))
        self.exception_date_var = tk.StringVar(value=self.exception_date.isoformat())
        ttk.Entry(row2_frame, textvariable=self.exception_date_var, width=15).pack(side="left", padx=(0, 20))
        
        ttk.Label(row2_frame, text="Notes:", width=8).pack(side="left", padx=(0, 5))
        self.exception_notes_var = tk.StringVar()
        ttk.Entry(row2_frame, textvariable=self.exception_notes_var, width=40).pack(side="left", padx=(0, 20))
        
        # Row 3: Buttons
        row3_frame = ttk.Frame(form_frame)
        row3_frame.pack(side="top", fill="x", pady=5)
        
        ttk.Button(row3_frame, text="✓ Submit Exception", command=self._submit_exception).pack(side="left", padx=5)
        ttk.Button(row3_frame, text="✗ Clear Form", command=self._clear_exception_form).pack(side="left", padx=5)
        
        # === HISTORY TABLE ===
        history_frame = ttk.LabelFrame(main_frame, text="Exception History", padding=5)
        history_frame.pack(fill="both", expand=True)
        
        # Toolbar
        toolbar_frame = ttk.Frame(history_frame)
        toolbar_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Label(toolbar_frame, text="View Date:", font=("Helvetica", 9)).pack(side="left", padx=(0, 5))
        self.exception_view_date_var = tk.StringVar(value=self.exception_date.isoformat())
        ttk.Entry(toolbar_frame, textvariable=self.exception_view_date_var, width=15).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar_frame, text="🔄 Refresh", command=self._refresh_exception_tab).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="📅 Today", command=self._set_exception_today).pack(side="left", padx=5)
        
        # Separator
        ttk.Separator(toolbar_frame, orient="vertical").pack(side="left", fill="y", padx=10)
        
        ttk.Button(toolbar_frame, text="🗑️ Revert Selected", command=self._revert_selected_exception).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="🗑️ Revert All...", command=self._revert_bulk_exceptions).pack(side="left", padx=5)
        
        # Table
        table_frame = ttk.Frame(history_frame)
        table_frame.pack(fill="both", expand=True)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.exception_treeview = ttk.Treeview(
            table_frame,
            columns=("Type", "SKU", "Qty", "Notes", "Date"),
            height=15,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.exception_treeview.yview)
        
        self.exception_treeview.column("#0", width=0, stretch=tk.NO)
        self.exception_treeview.column("Type", anchor=tk.W, width=100)
        self.exception_treeview.column("SKU", anchor=tk.W, width=100)
        self.exception_treeview.column("Qty", anchor=tk.CENTER, width=80)
        self.exception_treeview.column("Notes", anchor=tk.W, width=300)
        self.exception_treeview.column("Date", anchor=tk.CENTER, width=100)
        
        self.exception_treeview.heading("Type", text="Type", anchor=tk.W)
        self.exception_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.exception_treeview.heading("Qty", text="Qty", anchor=tk.CENTER)
        self.exception_treeview.heading("Notes", text="Notes", anchor=tk.W)
        self.exception_treeview.heading("Date", text="Date", anchor=tk.CENTER)
        
        self.exception_treeview.pack(fill="both", expand=True)
    
    def _populate_exception_sku_dropdown(self):
        """Populate SKU dropdown in exception form."""
        sku_ids = self.csv_layer.get_all_sku_ids()
        self.exception_sku_combo["values"] = sku_ids
    
    def _clear_exception_form(self):
        """Clear exception form fields."""
        self.exception_type_var.set("WASTE")
        self.exception_sku_var.set("")
        self.exception_qty_var.set("")
        self.exception_date_var.set(date.today().isoformat())
        self.exception_notes_var.set("")
    
    def _submit_exception(self):
        """Submit exception from quick entry form."""
        # Validate inputs
        event_type_str = self.exception_type_var.get()
        sku = self.exception_sku_var.get().strip()
        qty_str = self.exception_qty_var.get().strip()
        date_str = self.exception_date_var.get().strip()
        notes = self.exception_notes_var.get().strip()
        
        if not sku:
            messagebox.showerror("Validation Error", "Please select a SKU.")
            return
        
        if not qty_str:
            messagebox.showerror("Validation Error", "Please enter a quantity.")
            return
        
        try:
            qty = int(qty_str)
        except ValueError:
            messagebox.showerror("Validation Error", "Quantity must be an integer.")
            return
        
        try:
            event_date = date.fromisoformat(date_str)
        except ValueError:
            messagebox.showerror("Validation Error", "Invalid date format. Use YYYY-MM-DD.")
            return
        
        # Map string to EventType
        event_type_map = {
            "WASTE": EventType.WASTE,
            "ADJUST": EventType.ADJUST,
            "UNFULFILLED": EventType.UNFULFILLED,
        }
        event_type = event_type_map.get(event_type_str)
        
        if not event_type:
            messagebox.showerror("Error", f"Invalid event type: {event_type_str}")
            return
        
        # Record exception
        try:
            txn, already_recorded = self.exception_workflow.record_exception(
                event_type=event_type,
                sku=sku,
                qty=qty,
                event_date=event_date,
                notes=notes,
            )
            
            if already_recorded:
                messagebox.showwarning(
                    "Already Recorded",
                    f"Exception of type {event_type_str} for SKU '{sku}' on {event_date.isoformat()} was already recorded today.",
                )
            else:
                messagebox.showinfo(
                    "Success",
                    f"Exception recorded successfully:\n{event_type_str} - {sku} - Qty: {qty}",
                )
                self._clear_exception_form()
                self._refresh_exception_tab()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to record exception: {str(e)}")
    
    def _refresh_exception_tab(self):
        """Refresh exception history table."""
        try:
            view_date_str = self.exception_view_date_var.get()
            view_date = date.fromisoformat(view_date_str)
        except ValueError:
            messagebox.showerror("Error", "Invalid view date format. Use YYYY-MM-DD.")
            return
        
        self.exception_treeview.delete(*self.exception_treeview.get_children())
        
        # Read all transactions and filter for exceptions on view_date
        all_txns = self.csv_layer.read_transactions()
        exception_txns = [
            t for t in all_txns
            if t.event in [EventType.WASTE, EventType.ADJUST, EventType.UNFULFILLED]
            and t.date == view_date
        ]
        
        # Populate table
        for txn in exception_txns:
            # Extract notes (remove exception_key prefix)
            notes = txn.note or ""
            if ";" in notes:
                notes = notes.split(";", 1)[1].strip()
            
            self.exception_treeview.insert(
                "",
                "end",
                values=(
                    txn.event.value,
                    txn.sku,
                    f"{txn.qty:+d}" if txn.event == EventType.ADJUST else str(txn.qty),
                    notes,
                    txn.date.isoformat(),
                ),
            )
    
    def _set_exception_today(self):
        """Set exception view date to today."""
        today = date.today()
        self.exception_view_date_var.set(today.isoformat())
        self._refresh_exception_tab()
    
    def _revert_selected_exception(self):
        """Revert selected exception from table."""
        selected = self.exception_treeview.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select an exception to revert.")
            return
        
        # Get selected exception data
        item = self.exception_treeview.item(selected[0])
        values = item["values"]
        event_type_str = values[0]
        sku = values[1]
        date_str = values[4]
        
        # Map string to EventType
        event_type_map = {
            "WASTE": EventType.WASTE,
            "ADJUST": EventType.ADJUST,
            "UNFULFILLED": EventType.UNFULFILLED,
        }
        event_type = event_type_map.get(event_type_str)
        event_date = date.fromisoformat(date_str)
        
        # Confirm revert
        confirm = messagebox.askyesno(
            "Confirm Revert",
            f"Revert all {event_type_str} exceptions for SKU '{sku}' on {date_str}?\n\nThis action cannot be undone.",
        )
        if not confirm:
            return
        
        # Revert
        try:
            reverted_count = self.exception_workflow.revert_exception_day(
                event_date=event_date,
                sku=sku,
                event_type=event_type,
            )
            
            if reverted_count > 0:
                messagebox.showinfo(
                    "Success",
                    f"Reverted {reverted_count} exception(s) for {event_type_str} - {sku} on {date_str}.",
                )
                self._refresh_exception_tab()
            else:
                messagebox.showwarning("No Changes", "No exceptions found to revert.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to revert exception: {str(e)}")
    
    def _revert_bulk_exceptions(self):
        """Revert bulk exceptions with filters (popup dialog)."""
        # Create popup
        popup = tk.Toplevel(self.root)
        popup.title("Bulk Revert Exceptions")
        popup.geometry("400x250")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        
        # Form frame
        form_frame = ttk.Frame(popup, padding=20)
        form_frame.pack(fill="both", expand=True)
        
        ttk.Label(form_frame, text="Bulk Revert Exceptions", font=("Helvetica", 12, "bold")).pack(pady=(0, 15))
        
        # Event Type
        ttk.Label(form_frame, text="Event Type:", font=("Helvetica", 10)).pack(anchor="w", pady=(0, 5))
        bulk_type_var = tk.StringVar(value="WASTE")
        ttk.Combobox(
            form_frame,
            textvariable=bulk_type_var,
            values=["WASTE", "ADJUST", "UNFULFILLED"],
            state="readonly",
            width=30,
        ).pack(fill="x", pady=(0, 10))
        
        # SKU
        ttk.Label(form_frame, text="SKU:", font=("Helvetica", 10)).pack(anchor="w", pady=(0, 5))
        bulk_sku_var = tk.StringVar()
        bulk_sku_combo = ttk.Combobox(form_frame, textvariable=bulk_sku_var, width=30)
        bulk_sku_combo["values"] = self.csv_layer.get_all_sku_ids()
        bulk_sku_combo.pack(fill="x", pady=(0, 10))
        
        # Date
        ttk.Label(form_frame, text="Date:", font=("Helvetica", 10)).pack(anchor="w", pady=(0, 5))
        bulk_date_var = tk.StringVar(value=self.exception_view_date_var.get())
        ttk.Entry(form_frame, textvariable=bulk_date_var, width=30).pack(fill="x", pady=(0, 15))
        
        # Buttons
        button_frame = ttk.Frame(form_frame)
        button_frame.pack(fill="x")
        
        def do_bulk_revert():
            event_type_str = bulk_type_var.get()
            sku = bulk_sku_var.get().strip()
            date_str = bulk_date_var.get().strip()
            
            if not sku:
                messagebox.showerror("Validation Error", "Please select a SKU.", parent=popup)
                return
            
            try:
                event_date = date.fromisoformat(date_str)
            except ValueError:
                messagebox.showerror("Validation Error", "Invalid date format. Use YYYY-MM-DD.", parent=popup)
                return
            
            event_type_map = {
                "WASTE": EventType.WASTE,
                "ADJUST": EventType.ADJUST,
                "UNFULFILLED": EventType.UNFULFILLED,
            }
            event_type = event_type_map.get(event_type_str)
            
            # Confirm
            confirm = messagebox.askyesno(
                "Confirm Bulk Revert",
                f"Revert ALL {event_type_str} exceptions for SKU '{sku}' on {date_str}?\n\nThis action cannot be undone.",
                parent=popup,
            )
            if not confirm:
                return
            
            # Revert
            try:
                reverted_count = self.exception_workflow.revert_exception_day(
                    event_date=event_date,
                    sku=sku,
                    event_type=event_type,
                )
                
                messagebox.showinfo(
                    "Success",
                    f"Reverted {reverted_count} exception(s).",
                    parent=popup,
                )
                popup.destroy()
                self._refresh_exception_tab()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to revert: {str(e)}", parent=popup)
        
        ttk.Button(button_frame, text="Revert", command=do_bulk_revert).pack(side="right", padx=5)
        ttk.Button(button_frame, text="Cancel", command=popup.destroy).pack(side="right", padx=5)

    
    def _build_admin_tab(self):
        """Build Admin tab (SKU management, data view)."""
        main_frame = ttk.Frame(self.admin_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="SKU Management", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # Search bar
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Label(search_frame, text="Search:").pack(side="left", padx=5)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left", padx=5)
        ttk.Button(search_frame, text="Search", command=self._search_skus).pack(side="left", padx=5)
        ttk.Button(search_frame, text="Clear", command=self._clear_search).pack(side="left", padx=2)
        
        # Bind Enter key to search
        self.search_entry.bind("<Return>", lambda e: self._search_skus())
        
        # Toolbar
        toolbar_frame = ttk.Frame(main_frame)
        toolbar_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Button(toolbar_frame, text="➕ New SKU", command=self._new_sku).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="✏️ Edit SKU", command=self._edit_sku).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="🗑️ Delete SKU", command=self._delete_sku).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="🔄 Refresh", command=self._refresh_admin_tab).pack(side="left", padx=5)
        
        # SKU table
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill="both", expand=True)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.admin_treeview = ttk.Treeview(
            table_frame,
            columns=("SKU", "Description", "EAN"),
            height=20,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.admin_treeview.yview)
        
        self.admin_treeview.column("#0", width=0, stretch=tk.NO)
        self.admin_treeview.column("SKU", anchor=tk.W, width=120)
        self.admin_treeview.column("Description", anchor=tk.W, width=400)
        self.admin_treeview.column("EAN", anchor=tk.W, width=150)
        
        self.admin_treeview.heading("SKU", text="SKU Code", anchor=tk.W)
        self.admin_treeview.heading("Description", text="Description", anchor=tk.W)
        self.admin_treeview.heading("EAN", text="EAN", anchor=tk.W)
        
        self.admin_treeview.pack(fill="both", expand=True)
        
        # Bind double-click to edit
        self.admin_treeview.bind("<Double-1>", lambda e: self._edit_sku())
    
    def _refresh_admin_tab(self):
        """Refresh SKU table with all SKUs."""
        self.admin_treeview.delete(*self.admin_treeview.get_children())
        
        skus = self.csv_layer.read_skus()
        for sku in skus:
            self.admin_treeview.insert(
                "",
                "end",
                values=(sku.sku, sku.description, sku.ean or ""),
            )
    
    def _search_skus(self):
        """Search SKUs by code or description."""
        query = self.search_var.get()
        self.admin_treeview.delete(*self.admin_treeview.get_children())
        
        skus = self.csv_layer.search_skus(query)
        for sku in skus:
            self.admin_treeview.insert(
                "",
                "end",
                values=(sku.sku, sku.description, sku.ean or ""),
            )
    
    def _clear_search(self):
        """Clear search and show all SKUs."""
        self.search_var.set("")
        self._refresh_admin_tab()
    
    def _new_sku(self):
        """Open form to create new SKU."""
        self._show_sku_form(mode="new")
    
    def _edit_sku(self):
        """Open form to edit selected SKU."""
        selected = self.admin_treeview.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a SKU to edit.")
            return
        
        # Get selected SKU data
        item = self.admin_treeview.item(selected[0])
        values = item["values"]
        selected_sku = values[0]  # SKU code
        
        self._show_sku_form(mode="edit", sku_code=selected_sku)
    
    def _delete_sku(self):
        """Delete selected SKU after confirmation."""
        selected = self.admin_treeview.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a SKU to delete.")
            return
        
        # Get selected SKU data
        item = self.admin_treeview.item(selected[0])
        values = item["values"]
        sku_code = values[0]
        
        # Check if can delete
        can_delete, reason = self.csv_layer.can_delete_sku(sku_code)
        if not can_delete:
            messagebox.showerror("Cannot Delete", f"Cannot delete SKU:\n{reason}")
            return
        
        # Confirm deletion
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete SKU '{sku_code}'?\n\nThis action cannot be undone.",
        )
        if not confirm:
            return
        
        # Delete SKU
        success = self.csv_layer.delete_sku(sku_code)
        if success:
            messagebox.showinfo("Success", f"SKU '{sku_code}' deleted successfully.")
            self._refresh_admin_tab()
        else:
            messagebox.showerror("Error", f"Failed to delete SKU '{sku_code}'.")
    
    def _show_sku_form(self, mode="new", sku_code=None):
        """
        Show SKU form in popup window.
        
        Args:
            mode: "new" or "edit"
            sku_code: SKU code to edit (for edit mode)
        """
        # Create popup window
        popup = tk.Toplevel(self.root)
        popup.title("New SKU" if mode == "new" else "Edit SKU")
        popup.geometry("500x300")
        popup.resizable(False, False)
        
        # Center popup
        popup.transient(self.root)
        popup.grab_set()
        
        # Form frame
        form_frame = ttk.Frame(popup, padding=20)
        form_frame.pack(fill="both", expand=True)
        
        # Load existing SKU data if editing
        current_sku = None
        if mode == "edit" and sku_code:
            skus = self.csv_layer.read_skus()
            current_sku = next((s for s in skus if s.sku == sku_code), None)
            if not current_sku:
                messagebox.showerror("Error", f"SKU '{sku_code}' not found.")
                popup.destroy()
                return
        
        # SKU Code field
        ttk.Label(form_frame, text="SKU Code:", font=("Helvetica", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=5
        )
        sku_var = tk.StringVar(value=current_sku.sku if current_sku else "")
        sku_entry = ttk.Entry(form_frame, textvariable=sku_var, width=40)
        sku_entry.grid(row=0, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Description field
        ttk.Label(form_frame, text="Description:", font=("Helvetica", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=5
        )
        desc_var = tk.StringVar(value=current_sku.description if current_sku else "")
        desc_entry = ttk.Entry(form_frame, textvariable=desc_var, width=40)
        desc_entry.grid(row=1, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # EAN field
        ttk.Label(form_frame, text="EAN (optional):", font=("Helvetica", 10, "bold")).grid(
            row=2, column=0, sticky="w", pady=5
        )
        ean_var = tk.StringVar(value=current_sku.ean if current_sku and current_sku.ean else "")
        ean_entry = ttk.Entry(form_frame, textvariable=ean_var, width=40)
        ean_entry.grid(row=2, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Validate EAN button and status label
        ean_status_var = tk.StringVar(value="")
        ttk.Button(
            form_frame, 
            text="Validate EAN", 
            command=lambda: self._validate_ean_field(ean_var.get(), ean_status_var)
        ).grid(row=3, column=1, sticky="w", pady=5, padx=(10, 0))
        
        ean_status_label = ttk.Label(form_frame, textvariable=ean_status_var, foreground="green")
        ean_status_label.grid(row=4, column=1, sticky="w", padx=(10, 0))
        
        # Configure grid
        form_frame.columnconfigure(1, weight=1)
        
        # Button frame
        button_frame = ttk.Frame(popup, padding=10)
        button_frame.pack(side="bottom", fill="x")
        
        ttk.Button(
            button_frame,
            text="Save",
            command=lambda: self._save_sku_form(
                popup, mode, sku_var.get(), desc_var.get(), ean_var.get(), current_sku
            ),
        ).pack(side="right", padx=5)
        
        ttk.Button(button_frame, text="Cancel", command=popup.destroy).pack(side="right", padx=5)
        
        # Focus on first field
        if mode == "new":
            sku_entry.focus()
        else:
            desc_entry.focus()
    
    def _validate_ean_field(self, ean: str, status_var: tk.StringVar):
        """Validate EAN and update status label."""
        if not ean or not ean.strip():
            status_var.set("✓ Empty EAN is valid")
            return
        
        is_valid, error = validate_ean(ean.strip())
        if is_valid:
            status_var.set("✓ Valid EAN")
        else:
            status_var.set(f"✗ {error}")
    
    def _save_sku_form(self, popup, mode, sku_code, description, ean, current_sku):
        """Save SKU from form."""
        # Validate inputs
        if not sku_code or not sku_code.strip():
            messagebox.showerror("Validation Error", "SKU code cannot be empty.", parent=popup)
            return
        
        if not description or not description.strip():
            messagebox.showerror("Validation Error", "Description cannot be empty.", parent=popup)
            return
        
        sku_code = sku_code.strip()
        description = description.strip()
        ean = ean.strip() if ean else None
        
        # Validate EAN if provided
        if ean:
            is_valid, error = validate_ean(ean)
            if not is_valid:
                messagebox.showerror("Invalid EAN", error, parent=popup)
                return
        
        # Check for duplicate SKU code (only for new or if code changed)
        if mode == "new" or (current_sku and sku_code != current_sku.sku):
            if self.csv_layer.sku_exists(sku_code):
                messagebox.showerror(
                    "Duplicate SKU",
                    f"SKU code '{sku_code}' already exists. Please use a different code.",
                    parent=popup,
                )
                return
        
        try:
            if mode == "new":
                # Create new SKU
                new_sku = SKU(sku=sku_code, description=description, ean=ean)
                self.csv_layer.write_sku(new_sku)
                messagebox.showinfo("Success", f"SKU '{sku_code}' created successfully.", parent=popup)
            else:
                # Update existing SKU
                old_sku_code = current_sku.sku
                success = self.csv_layer.update_sku(old_sku_code, sku_code, description, ean)
                if success:
                    if old_sku_code != sku_code:
                        messagebox.showinfo(
                            "Success",
                            f"SKU updated successfully.\nSKU code changed from '{old_sku_code}' to '{sku_code}'.\nAll ledger references have been updated.",
                            parent=popup,
                        )
                    else:
                        messagebox.showinfo("Success", f"SKU '{sku_code}' updated successfully.", parent=popup)
                else:
                    messagebox.showerror("Error", "Failed to update SKU.", parent=popup)
                    return
            
            # Refresh table and close popup
            popup.destroy()
            self._refresh_admin_tab()
            
        except ValueError as e:
            messagebox.showerror("Error", str(e), parent=popup)

    
    def _refresh_stock_tab(self):
        """Refresh stock calculations and update tab."""
        try:
            asof_str = self.asof_date_var.get()
            self.asof_date = date.fromisoformat(asof_str)
        except ValueError:
            messagebox.showerror("Error", "Invalid date format. Use YYYY-MM-DD.")
            return
        
        self.stock_treeview.delete(*self.stock_treeview.get_children())
        
        # Get all SKUs
        sku_ids = self.csv_layer.get_all_sku_ids()
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Read transactions and sales
        transactions = self.csv_layer.read_transactions()
        sales_records = self.csv_layer.read_sales()
        
        # Calculate stock for each SKU
        stocks = StockCalculator.calculate_all_skus(
            sku_ids,
            self.asof_date,
            transactions,
            sales_records,
        )
        
        # Populate table
        for sku_id in sku_ids:
            stock = stocks[sku_id]
            sku_obj = skus_by_id.get(sku_id)
            description = sku_obj.description if sku_obj else "N/A"
            
            self.stock_treeview.insert(
                "",
                "end",
                values=(
                    stock.sku,
                    description,
                    stock.on_hand,
                    stock.on_order,
                    stock.available(),
                ),
            )
    
    def _refresh_all(self):
        """Refresh all tabs."""
        self._refresh_stock_tab()
        self._refresh_admin_tab()
        self._refresh_exception_tab()


def main():
    """Entry point for GUI."""
    root = tk.Tk()
    app = DesktopOrderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
