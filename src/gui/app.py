"""
Main GUI application for desktop-order-system.

Tkinter-based desktop UI with multiple tabs.
"""
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date, timedelta
from pathlib import Path

from ..persistence.csv_layer import CSVLayer
from ..domain.ledger import StockCalculator
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
        frame = ttk.Frame(self.exception_tab)
        frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        ttk.Label(frame, text="Exception Management (TBD)", font=("Helvetica", 12)).pack(pady=10)
        ttk.Label(frame, text="Quick entry for WASTE, ADJUST, UNFULFILLED").pack()
    
    def _build_admin_tab(self):
        """Build Admin tab (SKU management, data view)."""
        frame = ttk.Frame(self.admin_tab)
        frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        ttk.Label(frame, text="Admin (TBD)", font=("Helvetica", 12)).pack(pady=10)
        ttk.Label(frame, text="SKU management, legacy migration, data import").pack()
    
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


def main():
    """Entry point for GUI."""
    root = tk.Tk()
    app = DesktopOrderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
