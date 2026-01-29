"""
Main GUI application for desktop-order-system.

Tkinter-based desktop UI with multiple tabs.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from datetime import date, timedelta
from pathlib import Path
import tempfile
import os
import csv
from collections import defaultdict

try:
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.pyplot as plt
    import numpy as np
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Warning: matplotlib not installed. Dashboard charts disabled.")

try:
    from PIL import Image, ImageTk
    import barcode
    from barcode.writer import ImageWriter
    BARCODE_AVAILABLE = True
except ImportError:
    BARCODE_AVAILABLE = False
    print("Warning: python-barcode or Pillow not installed. Barcode rendering disabled.")

from ..persistence.csv_layer import CSVLayer
from ..domain.ledger import StockCalculator, validate_ean
from ..domain.models import SKU, EventType, OrderProposal, Stock
from ..workflows.order import OrderWorkflow, calculate_daily_sales_average
from ..workflows.receiving import ReceivingWorkflow, ExceptionWorkflow
from ..workflows.daily_close import DailyCloseWorkflow
from .widgets import AutocompleteEntry


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
        self.daily_close_workflow = DailyCloseWorkflow(self.csv_layer)
        
        # Current AsOf date (for stock view)
        self.asof_date = date.today()
        
        # Current exception date (for exception view)
        self.exception_date = date.today()
        
        # Order proposals storage
        self.current_proposals = []  # List[OrderProposal]
        
        # EOD stock edits storage (for daily close)
        self.eod_stock_edits = {}  # {sku: eod_stock_on_hand}
        
        # Dashboard settings
        self.ma_period_daily = 7  # Default: 7-day moving average for daily sales
        self.ma_period_weekly = 3  # Default: 3-week moving average for weekly sales
        
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
        file_menu.add_command(label="Aggiorna", command=self._refresh_all)
        file_menu.add_separator()
        
        # Export submenu
        export_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Esporta in CSV", menu=export_menu)
        export_menu.add_command(label="Snapshot Stock (Data AsOf)", command=self._export_stock_snapshot)
        export_menu.add_command(label="Registro (Transazioni)", command=self._export_ledger)
        export_menu.add_command(label="Elenco SKU", command=self._export_sku_list)
        export_menu.add_command(label="Log Ordini", command=self._export_order_logs)
        export_menu.add_command(label="Log Ricevimenti", command=self._export_receiving_logs)
        
        file_menu.add_separator()
        file_menu.add_command(label="Esci", command=self.root.quit)
        
        # Tab notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Create tabs
        self.dashboard_tab = ttk.Frame(self.notebook)
        self.stock_tab = ttk.Frame(self.notebook)
        self.order_tab = ttk.Frame(self.notebook)
        self.receiving_tab = ttk.Frame(self.notebook)
        self.exception_tab = ttk.Frame(self.notebook)
        self.admin_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.dashboard_tab, text="📊 Dashboard")
        self.notebook.add(self.stock_tab, text="Stock (CalcolAto)")
        self.notebook.add(self.order_tab, text="Ordini")
        self.notebook.add(self.receiving_tab, text="Ricevimenti")
        self.notebook.add(self.exception_tab, text="Eccezioni")
        self.notebook.add(self.admin_tab, text="Admin")
        
        # Build tab contents
        self._build_dashboard_tab()
        self._build_stock_tab()
        self._build_order_tab()
        self._build_receiving_tab()
        self._build_exception_tab()
        self._build_admin_tab()
    
    def _build_stock_tab(self):
        """Build Stock tab (read-only stock view with audit timeline)."""
        # Main container with horizontal split
        main_frame = ttk.Frame(self.stock_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Left panel: Stock table
        left_panel = ttk.Frame(main_frame)
        left_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        # Date picker
        date_frame = ttk.Frame(left_panel)
        date_frame.pack(side="top", fill="x", pady=5)
        
        ttk.Label(date_frame, text="Data AsOf:").pack(side="left", padx=5)
        self.asof_date_var = tk.StringVar(value=self.asof_date.isoformat())
        ttk.Entry(date_frame, textvariable=self.asof_date_var, width=15).pack(side="left", padx=5)
        ttk.Button(date_frame, text="Aggiorna", command=self._refresh_stock_tab).pack(side="left", padx=5)
        
        # Stock table
        stock_frame = ttk.Frame(left_panel)
        stock_frame.pack(fill="both", expand=True)
        
        stock_scroll = ttk.Scrollbar(stock_frame)
        stock_scroll.pack(side="right", fill="y")
        
        self.stock_treeview = ttk.Treeview(
            stock_frame,
            columns=("SKU", "Description", "On Hand", "On Order", "Available", "EOD Stock"),
            height=15,
            yscrollcommand=stock_scroll.set,
        )
        stock_scroll.config(command=self.stock_treeview.yview)
        
        self.stock_treeview.column("#0", width=0, stretch=tk.NO)
        self.stock_treeview.column("SKU", anchor=tk.W, width=80)
        self.stock_treeview.column("Description", anchor=tk.W, width=200)
        self.stock_treeview.column("On Hand", anchor=tk.CENTER, width=80)
        self.stock_treeview.column("On Order", anchor=tk.CENTER, width=80)
        self.stock_treeview.column("Available", anchor=tk.CENTER, width=80)
        self.stock_treeview.column("EOD Stock", anchor=tk.CENTER, width=100)
        
        self.stock_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.stock_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.stock_treeview.heading("On Hand", text="Disponibile", anchor=tk.CENTER)
        self.stock_treeview.heading("On Order", text="In Ordine", anchor=tk.CENTER)
        self.stock_treeview.heading("Available", text="Totale", anchor=tk.CENTER)
        self.stock_treeview.heading("EOD Stock", text="Stock EOD 📝", anchor=tk.CENTER)
        
        self.stock_treeview.pack(fill="both", expand=True)
        
        # Tag for edited EOD stock
        self.stock_treeview.tag_configure("eod_edited", background="#ffffcc")
        
        # Bind selection to show audit trail
        self.stock_treeview.bind("<<TreeviewSelect>>", self._on_stock_select)
        # Bind double-click on EOD Stock column for editing
        self.stock_treeview.bind("<Double-Button-1>", self._on_stock_eod_double_click)
        
        # Search and EOD controls below table
        controls_frame = ttk.Frame(left_panel)
        controls_frame.pack(side="top", fill="x", pady=5)
        
        # Search bar
        ttk.Label(controls_frame, text="🔍 Cerca SKU:").pack(side="left", padx=5)
        self.stock_search_var = tk.StringVar()
        self.stock_search_var.trace('w', lambda *args: self._filter_stock_table())
        ttk.Entry(controls_frame, textvariable=self.stock_search_var, width=30).pack(side="left", padx=5)
        
        # EOD confirmation button
        ttk.Button(
            controls_frame,
            text="✓ Conferma Chiusura Giornaliera",
            command=self._confirm_eod_close,
        ).pack(side="left", padx=20)
        
        # Right panel: Audit Timeline
        right_panel = ttk.LabelFrame(main_frame, text="📋 Storico Audit (Seleziona SKU)", padding=5)
        right_panel.pack(side="right", fill="both", expand=False, ipadx=10)
        
        # Timeline header
        timeline_header = ttk.Frame(right_panel)
        timeline_header.pack(side="top", fill="x", pady=(0, 5))
        
        self.audit_sku_label = ttk.Label(timeline_header, text="Nessun SKU selezionato", font=("Helvetica", 10, "bold"))
        self.audit_sku_label.pack(side="left")
        
        ttk.Button(timeline_header, text="🔄", command=self._refresh_audit_timeline, width=3).pack(side="right")
        
        # Timeline treeview
        timeline_frame = ttk.Frame(right_panel)
        timeline_frame.pack(fill="both", expand=True)
        
        timeline_scroll = ttk.Scrollbar(timeline_frame)
        timeline_scroll.pack(side="right", fill="y")
        
        self.audit_timeline_treeview = ttk.Treeview(
            timeline_frame,
            columns=("Timestamp", "Event", "Qty", "Note"),
            height=20,
            show="tree headings",
            yscrollcommand=timeline_scroll.set,
        )
        timeline_scroll.config(command=self.audit_timeline_treeview.yview)
        
        self.audit_timeline_treeview.column("#0", width=0, stretch=tk.NO)
        self.audit_timeline_treeview.column("Timestamp", anchor=tk.W, width=130)
        self.audit_timeline_treeview.column("Event", anchor=tk.W, width=100)
        self.audit_timeline_treeview.column("Qty", anchor=tk.CENTER, width=50)
        self.audit_timeline_treeview.column("Note", anchor=tk.W, width=150)
        
        self.audit_timeline_treeview.heading("Timestamp", text="Data/Ora", anchor=tk.W)
        self.audit_timeline_treeview.heading("Event", text="Evento", anchor=tk.W)
        self.audit_timeline_treeview.heading("Qty", text="Q.tà", anchor=tk.CENTER)
        self.audit_timeline_treeview.heading("Note", text="Note", anchor=tk.W)
        
        self.audit_timeline_treeview.pack(fill="both", expand=True)
        
        # Store selected SKU for audit
        self.selected_sku_for_audit = None
    
    def _build_dashboard_tab(self):
        """Build Dashboard tab with KPI analytics and charts."""
        main_frame = ttk.Frame(self.dashboard_tab)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="📊 Dashboard & Analisi KPI", font=("Helvetica", 16, "bold")).pack(side="left")
        
        # Moving Average Controls
        ma_controls = ttk.Frame(title_frame)
        ma_controls.pack(side="right", padx=5)
        
        ttk.Label(ma_controls, text="Media Mobile Giornaliera:").pack(side="left", padx=(0, 5))
        self.ma_daily_var = tk.IntVar(value=self.ma_period_daily)
        ttk.Spinbox(
            ma_controls,
            from_=3,
            to=30,
            textvariable=self.ma_daily_var,
            width=5,
            command=self._on_ma_change
        ).pack(side="left", padx=(0, 10))
        
        ttk.Label(ma_controls, text="Media Mobile Settimanale:").pack(side="left", padx=(0, 5))
        self.ma_weekly_var = tk.IntVar(value=self.ma_period_weekly)
        ttk.Spinbox(
            ma_controls,
            from_=2,
            to=8,
            textvariable=self.ma_weekly_var,
            width=5,
            command=self._on_ma_change
        ).pack(side="left", padx=(0, 10))
        
        ttk.Button(title_frame, text="🔄 Aggiorna", command=self._refresh_dashboard).pack(side="right", padx=5)
        
        # === KPI CARDS ===
        kpi_frame = ttk.LabelFrame(main_frame, text="Indicatori Chiave di Prestazione", padding=10)
        kpi_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Row 1: Main KPIs
        kpi_row1 = ttk.Frame(kpi_frame)
        kpi_row1.pack(side="top", fill="x", pady=5)
        
        # Total SKUs
        self.kpi_total_skus_label = ttk.Label(kpi_row1, text="Totale SKU: -", font=("Helvetica", 11, "bold"))
        self.kpi_total_skus_label.pack(side="left", padx=20)
        
        # Total Stock Value
        self.kpi_stock_value_label = ttk.Label(kpi_row1, text="Valore Stock: -", font=("Helvetica", 11, "bold"))
        self.kpi_stock_value_label.pack(side="left", padx=20)
        
        # Avg Days Cover
        self.kpi_days_cover_label = ttk.Label(kpi_row1, text="Giorni Copertura Medi: -", font=("Helvetica", 11, "bold"))
        self.kpi_days_cover_label.pack(side="left", padx=20)
        
        # Row 2: Alerts and Turnover
        kpi_row2 = ttk.Frame(kpi_frame)
        kpi_row2.pack(side="top", fill="x", pady=5)
        
        # Low Stock Alerts
        self.kpi_low_stock_label = ttk.Label(kpi_row2, text="⚠️ Avvisi Stock Basso: -", font=("Helvetica", 11, "bold"), foreground="red")
        self.kpi_low_stock_label.pack(side="left", padx=20)
        
        # Turnover Ratio
        self.kpi_turnover_label = ttk.Label(kpi_row2, text="Indice di Rotazione: -", font=("Helvetica", 11, "bold"))
        self.kpi_turnover_label.pack(side="left", padx=20)
        
        # === CONTENT AREA (CHARTS + TABLES) ===
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill="both", expand=True)
        
        # Left panel: Charts
        charts_frame = ttk.LabelFrame(content_frame, text="Grafici & Tendenze", padding=5)
        charts_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        if MATPLOTLIB_AVAILABLE:
            # Create matplotlib figure with 2 subplots
            self.dashboard_figure = Figure(figsize=(8, 6), dpi=80)
            
            # Subplot 1: Daily Sales (Last 30 Days) - top
            self.daily_sales_ax = self.dashboard_figure.add_subplot(2, 1, 1)
            self.daily_sales_ax.set_title("Vendite Giornaliere (Ultimi 30 Giorni)")
            self.daily_sales_ax.set_xlabel("Data")
            self.daily_sales_ax.set_ylabel("Unità Vendute")
            self.daily_sales_ax.grid(True, alpha=0.3)
            
            # Subplot 2: Weekly Sales Comparison (Last 8 Weeks) - bottom
            self.weekly_sales_ax = self.dashboard_figure.add_subplot(2, 1, 2)
            self.weekly_sales_ax.set_title("Confronto Vendite Settimanali (Ultime 8 Settimane)")
            self.weekly_sales_ax.set_xlabel("Settimana")
            self.weekly_sales_ax.set_ylabel("Unità Vendute")
            self.weekly_sales_ax.grid(True, alpha=0.3)
            
            self.dashboard_figure.tight_layout()
            
            # Embed in Tkinter
            self.dashboard_canvas = FigureCanvasTkAgg(self.dashboard_figure, master=charts_frame)
            self.dashboard_canvas.draw()
            self.dashboard_canvas.get_tk_widget().pack(fill="both", expand=True)
        else:
            ttk.Label(charts_frame, text="Grafici disabilitati (matplotlib non installato)", foreground="gray").pack(pady=50)
        
        # Right panel: Top SKUs
        right_panel = ttk.Frame(content_frame)
        right_panel.pack(side="right", fill="both", expand=False, ipadx=10)
        
        # Top 10 by Movement
        movement_frame = ttk.LabelFrame(right_panel, text="Top 10 SKU per Movimento", padding=5)
        movement_frame.pack(side="top", fill="both", expand=True, pady=(0, 5))
        
        movement_scroll = ttk.Scrollbar(movement_frame)
        movement_scroll.pack(side="right", fill="y")
        
        self.movement_treeview = ttk.Treeview(
            movement_frame,
            columns=("SKU", "Sales"),
            height=10,
            yscrollcommand=movement_scroll.set,
            show="headings"
        )
        movement_scroll.config(command=self.movement_treeview.yview)
        
        self.movement_treeview.column("SKU", anchor=tk.W, width=100)
        self.movement_treeview.column("Sales", anchor=tk.CENTER, width=80)
        
        self.movement_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.movement_treeview.heading("Sales", text="Vendite Totali", anchor=tk.CENTER)
        
        self.movement_treeview.pack(fill="both", expand=True)
        
        # Top 10 by Stock Value
        value_frame = ttk.LabelFrame(right_panel, text="Top 10 SKU per Valore Stock", padding=5)
        value_frame.pack(side="top", fill="both", expand=True)
        
        value_scroll = ttk.Scrollbar(value_frame)
        value_scroll.pack(side="right", fill="y")
        
        self.value_treeview = ttk.Treeview(
            value_frame,
            columns=("SKU", "Units", "Value"),
            height=10,
            yscrollcommand=value_scroll.set,
            show="headings"
        )
        value_scroll.config(command=self.value_treeview.yview)
        
        self.value_treeview.column("SKU", anchor=tk.W, width=80)
        self.value_treeview.column("Units", anchor=tk.CENTER, width=60)
        self.value_treeview.column("Value", anchor=tk.CENTER, width=80)
        
        self.value_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.value_treeview.heading("Units", text="Unità", anchor=tk.CENTER)
        self.value_treeview.heading("Value", text="Valore", anchor=tk.CENTER)
        
        self.value_treeview.pack(fill="both", expand=True)
        
        # Initial load
        self._refresh_dashboard()
    
    def _refresh_dashboard(self):
        """Refresh all dashboard KPIs and charts."""
        try:
            # Get data
            sku_ids = self.csv_layer.get_all_sku_ids()
            skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
            transactions = self.csv_layer.read_transactions()
            sales_records = self.csv_layer.read_sales()
            
            today = date.today()
            
            # Calculate current stock for all SKUs
            stocks = StockCalculator.calculate_all_skus(
                sku_ids,
                today,
                transactions,
                sales_records,
            )
            
            # === KPI CALCULATIONS ===
            
            # 1. Total SKUs
            total_skus = len(sku_ids)
            self.kpi_total_skus_label.config(text=f"Totale SKU: {total_skus}")
            
            # 2. Total Stock Value (assuming unit price = 10 for demo; ideally from SKU data)
            # TODO: Add price field to SKU model
            unit_price = 10  # Default price
            total_stock_units = sum(stock.on_hand for stock in stocks.values())
            total_stock_value = total_stock_units * unit_price
            self.kpi_stock_value_label.config(text=f"Valore Stock: €{total_stock_value:,.0f}")
            
            # 3. Average Days Cover
            total_days_cover = 0
            skus_with_sales = 0
            for sku_id in sku_ids:
                stock = stocks[sku_id]
                daily_sales = calculate_daily_sales_average(sales_records, sku_id, days_lookback=30)
                if daily_sales > 0:
                    days_cover = stock.on_hand / daily_sales
                    total_days_cover += days_cover
                    skus_with_sales += 1
            
            avg_days_cover = total_days_cover / skus_with_sales if skus_with_sales > 0 else 0
            self.kpi_days_cover_label.config(text=f"Giorni Copertura Medi: {avg_days_cover:.1f} giorni")
            
            # 4. Low Stock Alerts (stock < 10 units or days_cover < 7)
            low_stock_count = 0
            for sku_id in sku_ids:
                stock = stocks[sku_id]
                daily_sales = calculate_daily_sales_average(sales_records, sku_id, days_lookback=30)
                days_cover = stock.on_hand / daily_sales if daily_sales > 0 else 999
                
                if stock.on_hand < 10 or days_cover < 7:
                    low_stock_count += 1
            
            self.kpi_low_stock_label.config(text=f"⚠️ Avvisi Stock Basso: {low_stock_count}")
            
            # 5. Turnover Ratio (Total Sales Last 30 Days / Avg Stock)
            thirty_days_ago = today - timedelta(days=30)
            recent_sales = [sr for sr in sales_records if sr.date >= thirty_days_ago]
            total_recent_sales = sum(sr.qty_sold for sr in recent_sales)
            avg_stock = total_stock_units  # Simplified; ideally average over period
            turnover_ratio = total_recent_sales / avg_stock if avg_stock > 0 else 0
            self.kpi_turnover_label.config(text=f"Indice di Rotazione: {turnover_ratio:.2f}")
            
            # === CHARTS ===
            
            if MATPLOTLIB_AVAILABLE:
                # Chart 1: Daily Sales (Last 30 Days)
                self.daily_sales_ax.clear()
                self.daily_sales_ax.set_title("Vendite Giornaliere (Ultimi 30 Giorni)")
                self.daily_sales_ax.set_xlabel("Data")
                self.daily_sales_ax.set_ylabel("Unità Vendute")
                self.daily_sales_ax.grid(True, alpha=0.3)
                
                # Calculate daily sales for last 30 days
                dates = []
                daily_totals = []
                for i in range(29, -1, -1):  # Last 30 days
                    calc_date = today - timedelta(days=i)
                    day_sales = [sr for sr in sales_records if sr.date == calc_date]
                    total_sold = sum(sr.qty_sold for sr in day_sales)
                    dates.append(calc_date.strftime("%d/%m"))
                    daily_totals.append(total_sold)
                
                # Plot with bar chart
                self.daily_sales_ax.bar(range(len(dates)), daily_totals, color='#2E86AB', alpha=0.7, width=0.8, label='Vendite')
                self.daily_sales_ax.set_xticks(range(0, len(dates), 5))
                self.daily_sales_ax.set_xticklabels([dates[i] for i in range(0, len(dates), 5)], rotation=45, ha='right')
                
                # Add moving average
                ma_period = self.ma_daily_var.get()
                if daily_totals and len(daily_totals) >= ma_period:
                    ma_values = self._calculate_moving_average(daily_totals, ma_period)
                    x_ma = np.arange(ma_period - 1, len(daily_totals))
                    self.daily_sales_ax.plot(
                        x_ma, 
                        ma_values, 
                        "r-", 
                        alpha=0.8, 
                        linewidth=2.5, 
                        label=f'Media Mobile {ma_period}d'
                    )
                    self.daily_sales_ax.legend(loc='upper left', fontsize=9)
                
                # Chart 2: Weekly Sales Comparison (Last 8 Weeks)
                self.weekly_sales_ax.clear()
                self.weekly_sales_ax.set_title("Confronto Vendite Settimanali (Ultime 8 Settimane)")
                self.weekly_sales_ax.set_xlabel("Settimana")
                self.weekly_sales_ax.set_ylabel("Unità Vendute")
                self.weekly_sales_ax.grid(True, alpha=0.3)
                
                # Calculate weekly sales for last 8 weeks
                weekly_labels = []
                weekly_totals = []
                
                for week_offset in range(7, -1, -1):  # Last 8 weeks, oldest to newest
                    # Calculate start and end of week
                    week_end = today - timedelta(days=week_offset * 7)
                    week_start = week_end - timedelta(days=6)
                    
                    # Calculate sales for this week
                    week_sales = [
                        sr for sr in sales_records 
                        if week_start <= sr.date <= week_end
                    ]
                    total_sold = sum(sr.qty_sold for sr in week_sales)
                    
                    # Label: "W-7" (7 weeks ago) to "W-0" (current week)
                    if week_offset == 0:
                        label = "Corrente"
                    else:
                        label = f"-{week_offset}w"
                    
                    weekly_labels.append(label)
                    weekly_totals.append(total_sold)
                
                # Create bar chart with color gradient
                colors = plt.cm.viridis(np.linspace(0.3, 0.9, len(weekly_totals)))
                bars = self.weekly_sales_ax.bar(range(len(weekly_labels)), weekly_totals, color=colors, width=0.7)
                
                # Add value labels on bars
                for bar, value in zip(bars, weekly_totals):
                    height = bar.get_height()
                    self.weekly_sales_ax.text(
                        bar.get_x() + bar.get_width() / 2.,
                        height,
                        f'{int(value)}',
                        ha='center',
                        va='bottom',
                        fontsize=8,
                        fontweight='bold'
                    )
                
                # Set x-axis labels
                self.weekly_sales_ax.set_xticks(range(len(weekly_labels)))
                self.weekly_sales_ax.set_xticklabels(weekly_labels, rotation=0)
                
                # Add moving average for weekly data
                ma_period_weekly = self.ma_weekly_var.get()
                if weekly_totals and len(weekly_totals) >= ma_period_weekly:
                    ma_weekly = self._calculate_moving_average(weekly_totals, ma_period_weekly)
                    x_ma = np.arange(ma_period_weekly - 1, len(weekly_totals))
                    self.weekly_sales_ax.plot(
                        x_ma,
                        ma_weekly,
                        'r-',
                        alpha=0.8,
                        linewidth=2.5,
                        label=f'Media Mobile {ma_period_weekly}w'
                    )
                    self.weekly_sales_ax.legend(loc='upper left', fontsize=9)
                
                self.dashboard_figure.tight_layout()
                self.dashboard_canvas.draw()
            
            # === TOP 10 TABLES ===
            
            # Top 10 by Movement (Total Sales)
            self.movement_treeview.delete(*self.movement_treeview.get_children())
            
            sales_by_sku = defaultdict(int)
            for sr in sales_records:
                sales_by_sku[sr.sku] += sr.qty_sold
            
            top_movement = sorted(sales_by_sku.items(), key=lambda x: x[1], reverse=True)[:10]
            
            for sku, total_sales in top_movement:
                self.movement_treeview.insert("", "end", values=(sku, total_sales))
            
            # Top 10 by Stock Value
            self.value_treeview.delete(*self.value_treeview.get_children())
            
            stock_values = []
            for sku_id in sku_ids:
                stock = stocks[sku_id]
                value = stock.on_hand * unit_price
                stock_values.append((sku_id, stock.on_hand, value))
            
            top_values = sorted(stock_values, key=lambda x: x[2], reverse=True)[:10]
            
            for sku, units, value in top_values:
                self.value_treeview.insert("", "end", values=(sku, units, f"€{value:,.0f}"))
        
        except Exception as e:
            messagebox.showerror("Errore Dashboard", f"Impossibile aggiornare dashboard: {str(e)}")
    
    def _calculate_moving_average(self, data: list, period: int) -> list:
        """
        Calculate moving average for given data.
        
        Args:
            data: List of values
            period: Window size for moving average
        
        Returns:
            List of moving average values (length = len(data) - period + 1)
        """
        if len(data) < period:
            return []
        
        ma_values = []
        for i in range(period - 1, len(data)):
            window = data[i - period + 1:i + 1]
            ma_values.append(sum(window) / period)
        
        return ma_values
    
    def _on_ma_change(self):
        """Callback when moving average period changes."""
        # Update stored values
        self.ma_period_daily = self.ma_daily_var.get()
        self.ma_period_weekly = self.ma_weekly_var.get()
        # Refresh dashboard with new MA
        self._refresh_dashboard()
    
    def _build_order_tab(self):
        """Build Order tab (proposal + confirmation)."""
        main_frame = ttk.Frame(self.order_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="Gestione Ordini", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # === PARAMETERS & PROPOSAL GENERATION ===
        param_frame = ttk.LabelFrame(main_frame, text="Genera Proposte Ordine", padding=10)
        param_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Parameters row
        params_row = ttk.Frame(param_frame)
        params_row.pack(side="top", fill="x", pady=5)
        
        ttk.Label(params_row, text="Stock Minimo:", width=12).pack(side="left", padx=(0, 5))
        self.min_stock_var = tk.StringVar(value="10")
        ttk.Entry(params_row, textvariable=self.min_stock_var, width=10).pack(side="left", padx=(0, 20))
        
        ttk.Label(params_row, text="Giorni Copertura:", width=12).pack(side="left", padx=(0, 5))
        self.days_cover_var = tk.StringVar(value="30")
        ttk.Entry(params_row, textvariable=self.days_cover_var, width=10).pack(side="left", padx=(0, 20))
        
        ttk.Label(params_row, text="Lead Time (giorni):", width=15).pack(side="left", padx=(0, 5))
        self.lead_time_var = tk.StringVar(value="7")
        ttk.Entry(params_row, textvariable=self.lead_time_var, width=10).pack(side="left", padx=(0, 20))
        
        # Buttons row
        buttons_row = ttk.Frame(param_frame)
        buttons_row.pack(side="top", fill="x", pady=5)
        
        ttk.Button(buttons_row, text="✓ Genera Tutte le Proposte", command=self._generate_all_proposals).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="🔄 Aggiorna Dati Stock", command=self._refresh_order_stock_data).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="✗ Cancella Proposte", command=self._clear_proposals).pack(side="left", padx=5)
        
        # === PROPOSALS TABLE (EDITABLE) ===
        proposal_frame = ttk.LabelFrame(main_frame, text="Proposte Ordine (Doppio click su Q.tà Proposta per modificare)", padding=5)
        proposal_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(proposal_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.proposal_treeview = ttk.Treeview(
            proposal_frame,
            columns=("SKU", "Description", "On Hand", "On Order", "Avg Sales", "Proposed Qty", "Receipt Date"),
            height=10,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.proposal_treeview.yview)
        
        self.proposal_treeview.column("#0", width=0, stretch=tk.NO)
        self.proposal_treeview.column("SKU", anchor=tk.W, width=80)
        self.proposal_treeview.column("Description", anchor=tk.W, width=200)
        self.proposal_treeview.column("On Hand", anchor=tk.CENTER, width=80)
        self.proposal_treeview.column("On Order", anchor=tk.CENTER, width=80)
        self.proposal_treeview.column("Avg Sales", anchor=tk.CENTER, width=90)
        self.proposal_treeview.column("Proposed Qty", anchor=tk.CENTER, width=100)
        self.proposal_treeview.column("Receipt Date", anchor=tk.CENTER, width=100)
        
        self.proposal_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.proposal_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.proposal_treeview.heading("On Hand", text="Disponibile", anchor=tk.CENTER)
        self.proposal_treeview.heading("On Order", text="In Ordine", anchor=tk.CENTER)
        self.proposal_treeview.heading("Avg Sales", text="Vendite Medie/Giorno", anchor=tk.CENTER)
        self.proposal_treeview.heading("Proposed Qty", text="Q.tà Proposta", anchor=tk.CENTER)
        self.proposal_treeview.heading("Receipt Date", text="Data Ricevimento", anchor=tk.CENTER)
        
        self.proposal_treeview.pack(fill="both", expand=True)
        
        # Bind double-click to edit
        self.proposal_treeview.bind("<Double-1>", self._on_proposal_double_click)
        
        # === CONFIRMATION SECTION ===
        confirm_frame = ttk.LabelFrame(main_frame, text="Conferma Ordini", padding=10)
        confirm_frame.pack(side="bottom", fill="x", pady=(10, 0))
        
        info_row = ttk.Frame(confirm_frame)
        info_row.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(info_row, text="Seleziona proposte con Q.tà Proposta > 0 sopra, poi clicca Conferma per creare ordini.").pack(side="left")
        
        buttons_row = ttk.Frame(confirm_frame)
        buttons_row.pack(side="top", fill="x")
        
        ttk.Button(buttons_row, text="✓ Conferma Tutti gli Ordini (Q.tà > 0)", command=self._confirm_orders).pack(side="left", padx=5)
    
    def _generate_all_proposals(self):
        """Generate order proposals for all SKUs."""
        try:
            min_stock = int(self.min_stock_var.get())
            days_cover = int(self.days_cover_var.get())
            lead_time = int(self.lead_time_var.get())
        except ValueError:
            messagebox.showerror("Errore di Validazione", "I parametri devono essere numeri interi.")
            return
        
        # Update workflow lead time
        self.order_workflow.lead_time_days = lead_time
        
        # Get all SKUs
        sku_ids = self.csv_layer.get_all_sku_ids()
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Read transactions and sales
        transactions = self.csv_layer.read_transactions()
        sales_records = self.csv_layer.read_sales()
        
        # Calculate stock for each SKU
        stocks = StockCalculator.calculate_all_skus(
            sku_ids,
            date.today(),
            transactions,
            sales_records,
        )
        
        # Generate proposals
        self.current_proposals = []
        for sku_id in sku_ids:
            stock = stocks[sku_id]
            sku_obj = skus_by_id.get(sku_id)
            description = sku_obj.description if sku_obj else "N/A"
            
            # Calculate daily sales average
            daily_sales = calculate_daily_sales_average(sales_records, sku_id, days_lookback=30)
            
            # Generate proposal (pass sku_obj for MOQ, lead_time_days, reorder_point)
            proposal = self.order_workflow.generate_proposal(
                sku=sku_id,
                description=description,
                current_stock=stock,
                daily_sales_avg=daily_sales,
                min_stock=min_stock,
                days_cover=days_cover,
                sku_obj=sku_obj,
            )
            self.current_proposals.append(proposal)
        
        # Populate table
        self._refresh_proposal_table()
        
        messagebox.showinfo(
            "Proposte Generate",
            f"Generate {len(self.current_proposals)} proposte ordine.\nProposte con Q.tà > 0: {sum(1 for p in self.current_proposals if p.proposed_qty > 0)}",
        )
    
    def _refresh_proposal_table(self):
        """Refresh proposals table."""
        self.proposal_treeview.delete(*self.proposal_treeview.get_children())
        
        for proposal in self.current_proposals:
            self.proposal_treeview.insert(
                "",
                "end",
                values=(
                    proposal.sku,
                    proposal.description,
                    proposal.current_on_hand,
                    proposal.current_on_order,
                    f"{proposal.daily_sales_avg:.1f}",
                    proposal.proposed_qty,
                    proposal.receipt_date.isoformat() if proposal.receipt_date else "",
                ),
            )
    
    def _refresh_order_stock_data(self):
        """Refresh stock data without regenerating proposals."""
        messagebox.showinfo("Info", "Dati stock aggiornati. Clicca 'Genera Tutte le Proposte' per ricalcolare.")
    
    def _clear_proposals(self):
        """Clear all proposals."""
        self.current_proposals = []
        self.proposal_treeview.delete(*self.proposal_treeview.get_children())
    
    def _on_proposal_double_click(self, event):
        """Handle double-click on proposal row to edit Proposed Qty."""
        selected = self.proposal_treeview.selection()
        if not selected:
            return
        
        item = self.proposal_treeview.item(selected[0])
        values = item["values"]
        sku = values[0]
        current_qty = values[5]
        
        # Find proposal
        proposal = next((p for p in self.current_proposals if p.sku == sku), None)
        if not proposal:
            return
        
        # Create edit dialog
        self._edit_proposed_qty_dialog(proposal, selected[0])
    
    def _edit_proposed_qty_dialog(self, proposal, tree_item_id):
        """Show dialog to edit proposed quantity."""
        popup = tk.Toplevel(self.root)
        popup.title(f"Edit Proposed Qty - {proposal.sku}")
        popup.geometry("400x200")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        
        # Form frame
        form_frame = ttk.Frame(popup, padding=20)
        form_frame.pack(fill="both", expand=True)
        
        ttk.Label(form_frame, text=f"SKU: {proposal.sku}", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=5)
        ttk.Label(form_frame, text=f"Descrizione: {proposal.description}").pack(anchor="w", pady=5)
        ttk.Label(form_frame, text=f"Q.tà Proposta Attuale: {proposal.proposed_qty}").pack(anchor="w", pady=5)
        
        ttk.Label(form_frame, text="Nuova Q.tà Proposta:", font=("Helvetica", 10)).pack(anchor="w", pady=(15, 5))
        new_qty_var = tk.StringVar(value=str(proposal.proposed_qty))
        qty_entry = ttk.Entry(form_frame, textvariable=new_qty_var, width=20)
        qty_entry.pack(anchor="w", pady=(0, 15))
        qty_entry.focus()
        
        def save_qty():
            try:
                new_qty = int(new_qty_var.get())
                if new_qty < 0:
                    messagebox.showerror("Errore di Validazione", "La quantità deve essere >= 0.", parent=popup)
                    return
                
                # Update proposal
                proposal.proposed_qty = new_qty
                
                # Update tree item
                self.proposal_treeview.item(
                    tree_item_id,
                    values=(
                        proposal.sku,
                        proposal.description,
                        proposal.current_on_hand,
                        proposal.current_on_order,
                        f"{proposal.daily_sales_avg:.1f}",
                        proposal.proposed_qty,
                        proposal.receipt_date.isoformat() if proposal.receipt_date else "",
                    ),
                )
                
                popup.destroy()
            except ValueError:
                messagebox.showerror("Errore di Validazione", "La quantità deve essere un numero intero.", parent=popup)
        
        # Buttons
        button_frame = ttk.Frame(form_frame)
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="Salva", command=save_qty).pack(side="right", padx=5)
        ttk.Button(button_frame, text="Annulla", command=popup.destroy).pack(side="right", padx=5)
        
        # Bind Enter to save
        qty_entry.bind("<Return>", lambda e: save_qty())
    
    def _confirm_orders(self):
        """Confirm all orders with qty > 0."""
        if not self.current_proposals:
            messagebox.showwarning("Nessuna Proposta", "Genera prima le proposte.")
            return
        
        # Filter proposals with qty > 0
        to_confirm = [p for p in self.current_proposals if p.proposed_qty > 0]
        
        if not to_confirm:
            messagebox.showwarning("Nessun Ordine", "Nessuna proposta con quantità > 0 da confermare.")
            return
        
        # Confirm with user
        confirm = messagebox.askyesno(
            "Conferma Ordini",
            f"Confermare {len(to_confirm)} ordine/i?\n\nQuesto creerà eventi ORDER nel ledger.",
        )
        if not confirm:
            return
        
        try:
            # Confirm orders
            confirmations, txns = self.order_workflow.confirm_order(
                to_confirm,
                [p.proposed_qty for p in to_confirm],
            )
            
            messagebox.showinfo(
                "Ordini Confermati",
                f"Confermati {len(confirmations)} ordine/i.\n\nID Ordine: {', '.join(c.order_id for c in confirmations[:3])}{'...' if len(confirmations) > 3 else ''}",
            )
            
            # Show receipt window
            self._show_receipt_window(confirmations)
            
            # Clear proposals
            self._clear_proposals()
            
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile confermare ordini: {str(e)}")
    
    def _show_receipt_window(self, confirmations):
        """Show receipt window with order confirmations (5 items per page, barcode rendering)."""
        if not confirmations:
            return
        
        # Create popup
        popup = tk.Toplevel(self.root)
        popup.title("Ricevuta Conferma Ordine")
        popup.geometry("700x600")
        popup.transient(self.root)
        popup.grab_set()
        
        # Header
        header_frame = ttk.Frame(popup, padding=10)
        header_frame.pack(side="top", fill="x")
        
        ttk.Label(header_frame, text="Ricevuta Conferma Ordine", font=("Helvetica", 14, "bold")).pack()
        
        # Page state
        items_per_page = 5
        total_pages = (len(confirmations) + items_per_page - 1) // items_per_page
        current_page = [0]  # Mutable for closure
        
        # Content frame (will be refreshed)
        content_frame_container = ttk.Frame(popup)
        content_frame_container.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Get SKUs with EAN
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        def render_page(page_num):
            """Render current page."""
            # Clear container
            for widget in content_frame_container.winfo_children():
                widget.destroy()
            
            # Page info
            page_info_frame = ttk.Frame(content_frame_container)
            page_info_frame.pack(side="top", fill="x", pady=(0, 10))
            ttk.Label(
                page_info_frame,
                text=f"Pagina {page_num + 1} di {total_pages}",
                font=("Helvetica", 10, "bold"),
            ).pack()
            
            # Items for this page
            start_idx = page_num * items_per_page
            end_idx = min(start_idx + items_per_page, len(confirmations))
            page_items = confirmations[start_idx:end_idx]
            
            # Scrollable frame
            canvas = tk.Canvas(content_frame_container, highlightthickness=0)
            scrollbar = ttk.Scrollbar(content_frame_container, orient="vertical", command=canvas.yview)
            scrollable_frame = ttk.Frame(canvas)
            
            scrollable_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            
            # Render each item
            for idx, confirmation in enumerate(page_items, start=1):
                item_frame = ttk.LabelFrame(scrollable_frame, text=f"{start_idx + idx}. {confirmation.sku}", padding=10)
                item_frame.pack(fill="x", pady=5)
                
                # SKU info
                sku_obj = skus_by_id.get(confirmation.sku)
                description = sku_obj.description if sku_obj else "N/A"
                ean = sku_obj.ean if sku_obj else None
                
                ttk.Label(item_frame, text=f"Descrizione: {description}").pack(anchor="w")
                ttk.Label(item_frame, text=f"Quantità Ordinata: {confirmation.qty_ordered}").pack(anchor="w")
                ttk.Label(item_frame, text=f"Data Ricevimento: {confirmation.receipt_date.isoformat()}").pack(anchor="w")
                ttk.Label(item_frame, text=f"ID Ordine: {confirmation.order_id}", font=("Courier", 9)).pack(anchor="w")
                
                # EAN and barcode
                if ean:
                    is_valid, error = validate_ean(ean)
                    if is_valid:
                        ttk.Label(item_frame, text=f"EAN: {ean}").pack(anchor="w", pady=(5, 0))
                        
                        # Render barcode
                        if BARCODE_AVAILABLE:
                            try:
                                barcode_img = self._generate_barcode_image(ean)
                                if barcode_img:
                                    barcode_label = ttk.Label(item_frame, image=barcode_img)
                                    barcode_label.image = barcode_img  # Keep reference
                                    barcode_label.pack(anchor="w", pady=5)
                            except Exception as e:
                                ttk.Label(item_frame, text=f"Errore barcode: {str(e)}", foreground="red").pack(anchor="w")
                        else:
                            ttk.Label(item_frame, text="(Rendering barcode disabilitato)", foreground="gray").pack(anchor="w")
                    else:
                        ttk.Label(item_frame, text=f"EAN: {ean} (Non valido - {error})", foreground="red").pack(anchor="w")
                else:
                    ttk.Label(item_frame, text="EAN: (vuoto - nessun barcode)", foreground="gray").pack(anchor="w")
            
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
        
        def next_page(event=None):
            if current_page[0] < total_pages - 1:
                current_page[0] += 1
                render_page(current_page[0])
        
        def prev_page(event=None):
            if current_page[0] > 0:
                current_page[0] -= 1
                render_page(current_page[0])
        
        # Initial render
        render_page(0)
        
        # Navigation
        nav_frame = ttk.Frame(popup, padding=10)
        nav_frame.pack(side="bottom", fill="x")
        
        ttk.Button(nav_frame, text="◀ Precedente", command=prev_page).pack(side="left", padx=5)
        ttk.Button(nav_frame, text="Successiva ▶", command=next_page).pack(side="left", padx=5)
        ttk.Label(nav_frame, text="(Premi SPAZIO per pagina successiva)").pack(side="left", padx=20)
        ttk.Button(nav_frame, text="Chiudi", command=popup.destroy).pack(side="right", padx=5)
        
        # Bind keys
        popup.bind("<space>", next_page)
        popup.bind("<Escape>", lambda e: popup.destroy())
    
    def _generate_barcode_image(self, ean):
        """Generate barcode image from EAN."""
        if not BARCODE_AVAILABLE:
            return None
        
        try:
            # Determine barcode type
            if len(ean) == 13:
                barcode_class = barcode.get_barcode_class('ean13')
            elif len(ean) == 12:
                barcode_class = barcode.get_barcode_class('ean')
            else:
                return None
            
            # Generate barcode
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmpfile:
                tmpfile_path = tmpfile.name
            
            ean_barcode = barcode_class(ean, writer=ImageWriter())
            ean_barcode.save(tmpfile_path.replace('.png', ''))  # Library adds .png
            
            # Load image
            img = Image.open(tmpfile_path)
            img = img.resize((250, 100), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            
            # Cleanup temp file
            try:
                os.unlink(tmpfile_path)
            except:
                pass
            
            return photo
        except Exception as e:
            print(f"Warning: Failed to generate barcode for {ean}: {e}")
            return None

    
    def _build_receiving_tab(self):
        """Build Receiving tab (pending orders + close receipt form + history)."""
        main_frame = ttk.Frame(self.receiving_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="Gestione Ricevimenti", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # === PENDING ORDERS SECTION ===
        pending_frame = ttk.LabelFrame(main_frame, text="Ordini in Sospeso (Non Completamente Ricevuti)", padding=5)
        pending_frame.pack(side="top", fill="both", expand=True, pady=(0, 10))
        
        # Toolbar con ricerca
        pending_toolbar = ttk.Frame(pending_frame)
        pending_toolbar.pack(side="top", fill="x", pady=(0, 5))
        ttk.Button(pending_toolbar, text="🔄 Aggiorna Sospesi", command=self._refresh_pending_orders).pack(side="left", padx=5)
        ttk.Label(pending_toolbar, text="Cerca:").pack(side="left", padx=(20, 5))
        self.pending_search_var = tk.StringVar()
        
        # Autocomplete per SKU/descrizione
        pending_search_ac = AutocompleteEntry(
            pending_toolbar,
            textvariable=self.pending_search_var,
            items_callback=self._filter_pending_sku_items,
            width=30
        )
        pending_search_ac.pack(side="left", padx=(0, 5))
        # Override trace per gestire filtro tabella
        self.pending_search_var.trace('w', lambda *args: self._filter_pending_orders())
        
        ttk.Label(pending_toolbar, text="(SKU o Descrizione)").pack(side="left", padx=5)
        
        # Dizionario per quantità modificate in memoria: {tree_item_id: qty_received}
        self.pending_qty_edits = {}
        
        # Pending orders table
        pending_scroll = ttk.Scrollbar(pending_frame)
        pending_scroll.pack(side="right", fill="y")
        
        self.pending_treeview = ttk.Treeview(
            pending_frame,
            columns=("Order ID", "SKU", "Description", "Qty Ordered", "Qty Received", "Pending", "Receipt Date"),
            height=6,
            yscrollcommand=pending_scroll.set,
        )
        pending_scroll.config(command=self.pending_treeview.yview)
        
        self.pending_treeview.column("#0", width=0, stretch=tk.NO)
        self.pending_treeview.column("Order ID", anchor=tk.W, width=120)
        self.pending_treeview.column("SKU", anchor=tk.W, width=80)
        self.pending_treeview.column("Description", anchor=tk.W, width=180)
        self.pending_treeview.column("Qty Ordered", anchor=tk.CENTER, width=100)
        self.pending_treeview.column("Qty Received", anchor=tk.CENTER, width=110)
        self.pending_treeview.column("Pending", anchor=tk.CENTER, width=80)
        self.pending_treeview.column("Receipt Date", anchor=tk.CENTER, width=100)
        
        self.pending_treeview.heading("Order ID", text="ID Ordine", anchor=tk.W)
        self.pending_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.pending_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.pending_treeview.heading("Qty Ordered", text="Q.tà Ordinata", anchor=tk.CENTER)
        self.pending_treeview.heading("Qty Received", text="Q.tà Ricevuta", anchor=tk.CENTER)
        self.pending_treeview.heading("Pending", text="In Sospeso", anchor=tk.CENTER)
        self.pending_treeview.heading("Receipt Date", text="Data Prevista", anchor=tk.CENTER)
        
        self.pending_treeview.pack(fill="both", expand=True)
        
        # Doppio click per editare quantità ricevuta
        self.pending_treeview.bind("<Double-1>", self._on_pending_qty_double_click)
        
        # Tag per evidenziare righe modificate
        self.pending_treeview.tag_configure("edited", background="#ffffcc")
        
        # === BULK RECEIPT CONFIRMATION ===
        confirm_frame = ttk.Frame(main_frame)
        confirm_frame.pack(side="top", fill="x", pady=(0, 10))
        
        ttk.Label(confirm_frame, text="Modifica le quantità ricevute nella tabella sopra (doppio click), poi:", font=("Helvetica", 10)).pack(side="left", padx=(10, 20))
        ttk.Button(confirm_frame, text="✓ Chiudi Ricevimento (Conferma Tutte)", command=self._close_receipt_bulk, style="Accent.TButton").pack(side="left", padx=5)
        
        # === RECEIVING HISTORY ===
        history_frame = ttk.LabelFrame(main_frame, text="Storico Ricevimenti", padding=5)
        history_frame.pack(side="top", fill="both", expand=True)
        
        # Toolbar
        history_toolbar = ttk.Frame(history_frame)
        history_toolbar.pack(side="top", fill="x", pady=(0, 5))
        ttk.Button(history_toolbar, text="🔄 Aggiorna Storico", command=self._refresh_receiving_history).pack(side="left", padx=5)
        
        ttk.Label(history_toolbar, text="Filtra SKU:").pack(side="left", padx=(20, 5))
        self.history_filter_sku_var = tk.StringVar()
        
        # Autocomplete per SKU
        history_filter_ac = AutocompleteEntry(
            history_toolbar,
            textvariable=self.history_filter_sku_var,
            items_callback=self._filter_sku_items_simple,
            width=25
        )
        history_filter_ac.pack(side="left", padx=(0, 5))
        ttk.Button(history_toolbar, text="Applica Filtro", command=self._refresh_receiving_history).pack(side="left", padx=5)
        ttk.Button(history_toolbar, text="Cancella Filtro", command=self._clear_history_filter).pack(side="left", padx=5)
        
        # History table
        history_scroll = ttk.Scrollbar(history_frame)
        history_scroll.pack(side="right", fill="y")
        
        self.receiving_history_treeview = ttk.Treeview(
            history_frame,
            columns=("Receipt ID", "Date", "SKU", "Qty Received", "Receipt Date", "Notes"),
            height=8,
            yscrollcommand=history_scroll.set,
        )
        history_scroll.config(command=self.receiving_history_treeview.yview)
        
        self.receiving_history_treeview.column("#0", width=0, stretch=tk.NO)
        self.receiving_history_treeview.column("Receipt ID", anchor=tk.W, width=150)
        self.receiving_history_treeview.column("Date", anchor=tk.CENTER, width=100)
        self.receiving_history_treeview.column("SKU", anchor=tk.W, width=80)
        self.receiving_history_treeview.column("Qty Received", anchor=tk.CENTER, width=110)
        self.receiving_history_treeview.column("Receipt Date", anchor=tk.CENTER, width=100)
        self.receiving_history_treeview.column("Notes", anchor=tk.W, width=250)
        
        self.receiving_history_treeview.heading("Receipt ID", text="ID Ricevimento", anchor=tk.W)
        self.receiving_history_treeview.heading("Date", text="Data Registrazione", anchor=tk.CENTER)
        self.receiving_history_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.receiving_history_treeview.heading("Qty Received", text="Q.tà Ricevuta", anchor=tk.CENTER)
        self.receiving_history_treeview.heading("Receipt Date", text="Data Ricevimento", anchor=tk.CENTER)
        self.receiving_history_treeview.heading("Notes", text="Note", anchor=tk.W)
        
        self.receiving_history_treeview.pack(fill="both", expand=True)
        
        # Initial load
        self._refresh_pending_orders()
        self._refresh_receiving_history()
    
    def _filter_pending_orders(self):
        """Filtra ordini in sospeso per SKU o descrizione."""
        search_text = self.pending_search_var.get().strip().lower()
        
        for item_id in self.pending_treeview.get_children():
            values = self.pending_treeview.item(item_id)["values"]
            sku = str(values[1]).lower()
            description = str(values[2]).lower()
            
            # Mostra se match o se ricerca vuota
            if not search_text or search_text in sku or search_text in description:
                self.pending_treeview.reattach(item_id, "", "end")
            else:
                self.pending_treeview.detach(item_id)
    
    def _refresh_pending_orders(self):
        """Calculate and display pending orders (qty_ordered - qty_received > 0)."""
        # Reset edits
        self.pending_qty_edits = {}
        # Read order logs
        order_logs = self.csv_layer.read_order_logs()
        
        # Read receiving logs
        receiving_logs = self.csv_layer.read_receiving_logs()
        
        # Calculate qty_received per (order_id, sku)
        received_by_order_sku = {}
        for log in receiving_logs:
            # Match by receipt_date to order's receipt_date (approximate)
            # For now, aggregate by SKU only
            sku = log.get("sku")
            qty = int(log.get("qty_received", 0))
            received_by_order_sku[sku] = received_by_order_sku.get(sku, 0) + qty
        
        # Get SKU descriptions
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Calculate pending
        self.pending_treeview.delete(*self.pending_treeview.get_children())
        
        for log in order_logs:
            order_id = log.get("order_id")
            sku = log.get("sku")
            qty_ordered = int(log.get("qty_ordered", 0))
            receipt_date_str = log.get("receipt_date", "")
            status = log.get("status", "PENDING")
            
            # Calculate qty_received for this SKU (aggregate across all receipts)
            qty_received = received_by_order_sku.get(sku, 0)
            pending_qty = max(0, qty_ordered - qty_received)
            
            # Only show if pending > 0
            if pending_qty > 0:
                description = skus_by_id.get(sku).description if sku in skus_by_id else "N/A"
                
                self.pending_treeview.insert(
                    "",
                    "end",
                    values=(
                        order_id,
                        sku,
                        description,
                        qty_ordered,
                        qty_received,
                        pending_qty,
                        receipt_date_str,
                    ),
                )
    
    def _on_pending_qty_double_click(self, event):
        """Edita quantità ricevuta con doppio click."""
        region = self.pending_treeview.identify("region", event.x, event.y)
        if region != "cell":
            return
        
        column = self.pending_treeview.identify_column(event.x)
        selected = self.pending_treeview.selection()
        
        if not selected:
            return
        
        item_id = selected[0]
        values = self.pending_treeview.item(item_id)["values"]
        
        # Solo colonna "Qty Received" (indice #4 = colonna 5)
        if column != "#5":
            return
        
        current_qty_received = values[4]
        pending_qty = values[5]
        
        # Dialog per inserire nuova quantità
        new_qty = tk.simpledialog.askinteger(
            "Modifica Quantità Ricevuta",
            f"SKU: {values[1]}\nInserisci quantità ricevuta:",
            initialvalue=current_qty_received,
            minvalue=0,
            parent=self.root,
        )
        
        if new_qty is None:
            return
        
        # Aggiorna valore nel treeview
        new_values = list(values)
        new_values[4] = new_qty
        new_values[5] = max(0, values[3] - new_qty)  # Ricalcola pending
        self.pending_treeview.item(item_id, values=new_values, tags=("edited",))
        
        # Salva in memoria
        self.pending_qty_edits[item_id] = new_qty
    
    def _close_receipt_bulk(self):
        """Chiudi ricevimento per tutte le quantità modificate."""
        if not self.pending_qty_edits:
            messagebox.showwarning(
                "Nessuna Modifica",
                "Nessuna quantità ricevuta modificata.\n\nModifica le quantità nella tabella (doppio click) prima di confermare.",
            )
            return
        
        # Conferma
        confirm = messagebox.askyesno(
            "Conferma Ricevimento",
            f"Confermare ricevimento per {len(self.pending_qty_edits)} SKU modificati?\n\nQuesta azione creerà eventi RECEIPT nel ledger.",
        )
        
        if not confirm:
            return
        
        receipt_date_obj = date.today()
        total_receipts = 0
        errors = []
        
        # Per ogni SKU modificato, crea un receipt
        for item_id, new_qty_received in self.pending_qty_edits.items():
            if new_qty_received <= 0:
                continue  # Skip se qty = 0
            
            values = self.pending_treeview.item(item_id)["values"]
            sku = values[1]
            
            # Genera receipt_id univoco per questo SKU
            receipt_id = ReceivingWorkflow.generate_receipt_id(
                receipt_date=receipt_date_obj,
                origin="MANUAL",
                sku=sku,
            )
            
            try:
                transactions, already_processed = self.receiving_workflow.close_receipt(
                    receipt_id=receipt_id,
                    receipt_date=receipt_date_obj,
                    sku_quantities={sku: new_qty_received},
                    notes="Bulk receiving",
                )
                
                if not already_processed:
                    total_receipts += 1
            except Exception as e:
                errors.append(f"SKU {sku}: {str(e)}")
        
        # Mostra risultato
        if errors:
            messagebox.showerror(
                "Errori durante ricevimento",
                f"Completati {total_receipts} ricevimenti.\n\nErrori:\n" + "\n".join(errors),
            )
        else:
            messagebox.showinfo(
                "Successo",
                f"Ricevimento completato per {total_receipts} SKU!",
            )
        
        # Refresh views
        self._refresh_pending_orders()
        self._refresh_receiving_history()
    
    def _refresh_receiving_history(self):
        """Refresh receiving history table."""
        logs = self.csv_layer.read_receiving_logs()
        
        # Apply filter
        filter_sku = self.history_filter_sku_var.get().strip().lower()
        if filter_sku:
            logs = [log for log in logs if filter_sku in log.get("sku", "").lower()]
        
        # Populate table
        self.receiving_history_treeview.delete(*self.receiving_history_treeview.get_children())
        
        # Read transactions for notes (RECEIPT events)
        transactions = self.csv_layer.read_transactions()
        notes_by_receipt = {}
        for txn in transactions:
            if txn.event == EventType.RECEIPT and "Receipt" in txn.note:
                # Extract receipt_id from note
                parts = txn.note.split(";")
                if len(parts) >= 1 and "Receipt" in parts[0]:
                    receipt_id = parts[0].replace("Receipt", "").strip()
                    if receipt_id not in notes_by_receipt:
                        notes_by_receipt[receipt_id] = txn.note
        
        for log in logs:
            receipt_id = log.get("receipt_id", "")
            date_str = log.get("date", "")
            sku = log.get("sku", "")
            qty_received = log.get("qty_received", "")
            receipt_date_str = log.get("receipt_date", "")
            
            # Get notes from transactions
            notes = notes_by_receipt.get(receipt_id, "")
            # Clean up notes (remove "Receipt {id};" prefix)
            if notes and ";" in notes:
                notes = notes.split(";", 1)[1].strip()
            
            self.receiving_history_treeview.insert(
                "",
                "end",
                values=(receipt_id, date_str, sku, qty_received, receipt_date_str, notes),
            )
    
    def _clear_history_filter(self):
        """Clear history filter and refresh."""
        self.history_filter_sku_var.set("")
        self._refresh_receiving_history()
    
    # === AUTOCOMPLETE CALLBACK METHODS ===
    
    def _filter_pending_sku_items(self, search_text: str) -> list:
        """
        Filtra SKU per autocomplete (codice + descrizione).
        
        Args:
            search_text: Testo cercato dall'utente
        
        Returns:
            Lista di stringhe formattate "SKU001 - Descrizione"
        """
        search_text = search_text.strip().lower()
        
        if not search_text:
            # Se vuoto, mostra tutti gli SKU
            skus = self.csv_layer.read_skus()
            return [f"{s.sku} - {s.description}" for s in skus]
        
        # Filtra per match su codice o descrizione
        skus = self.csv_layer.read_skus()
        filtered = []
        
        for sku_obj in skus:
            if (search_text in sku_obj.sku.lower() or 
                search_text in sku_obj.description.lower()):
                filtered.append(f"{sku_obj.sku} - {sku_obj.description}")
        
        return filtered
    
    def _filter_sku_items_simple(self, search_text: str) -> list:
        """
        Filtra SKU per autocomplete (solo codice SKU semplice).
        
        Args:
            search_text: Testo cercato dall'utente
        
        Returns:
            Lista di codici SKU
        """
        search_text = search_text.strip().lower()
        
        if not search_text:
            return self.csv_layer.get_all_sku_ids()
        
        # Filtra per match su codice
        all_skus = self.csv_layer.get_all_sku_ids()
        return [sku for sku in all_skus if search_text in sku.lower()]
    
    def _filter_supplier_items(self, search_text: str) -> list:
        """
        Filtra fornitori per autocomplete (lista aperta: mostra esistenti + permette nuovi).
        
        Args:
            search_text: Testo cercato dall'utente
        
        Returns:
            Lista di fornitori unici filtrati + suggerimento se è nuovo
        """
        search_text = search_text.strip().lower()
        
        # Estrai fornitori unici da SKU esistenti
        skus = self.csv_layer.read_skus()
        unique_suppliers = sorted(set(
            sku.supplier for sku in skus 
            if sku.supplier and sku.supplier.strip()
        ))
        
        if not search_text:
            return unique_suppliers
        
        # Filtra fornitori che contengono il testo cercato
        filtered = [s for s in unique_suppliers if search_text in s.lower()]
        
        # Se il testo non matcha nessun fornitore esistente, suggerisci come nuovo
        if not filtered and search_text:
            filtered.append(f"{search_text} (nuovo fornitore)")
        
        return filtered
    
    def _build_exception_tab(self):
        """Build Exception tab (WASTE, ADJUST, UNFULFILLED)."""
        main_frame = ttk.Frame(self.exception_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="Gestione Eccezioni", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # === QUICK ENTRY FORM (GRID LAYOUT) ===
        form_frame = ttk.LabelFrame(main_frame, text="Inserimento Rapido", padding=15)
        form_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Grid configuration (3 columns x 3 rows + buttons)
        form_frame.columnconfigure(1, weight=1)  # Column per widget input
        form_frame.columnconfigure(3, weight=1)
        
        # ROW 0: SKU (obbligatorio) - PRIMA POSIZIONE con ricerca filtrata
        ttk.Label(form_frame, text="SKU: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=8)
        self.exception_sku_var = tk.StringVar()
        
        # Entry invece di Combobox per mantenere focus
        self.exception_sku_entry = ttk.Entry(
            form_frame,
            textvariable=self.exception_sku_var,
            width=35,
        )
        self.exception_sku_entry.grid(row=0, column=1, sticky="w", pady=8)
        
        # Listbox popup per autocomplete
        self.exception_sku_listbox = None
        self.exception_sku_popup = None
        
        # Dizionario per mapping display -> codice SKU
        self.exception_sku_map = {}
        
        # Trace per filtro real-time e validazione
        self.exception_sku_var.trace('w', lambda *args: self._filter_exception_sku())
        self.exception_sku_var.trace('w', lambda *args: self._validate_exception_form())
        
        # Bind eventi per gestire selezione da listbox
        self.exception_sku_entry.bind('<Down>', self._on_sku_down)
        self.exception_sku_entry.bind('<Up>', self._on_sku_up)
        self.exception_sku_entry.bind('<Return>', self._on_sku_select)
        self.exception_sku_entry.bind('<Escape>', self._on_sku_escape)
        self.exception_sku_entry.bind('<FocusOut>', self._on_sku_focus_out)
        
        # Populate SKU dropdown
        self._populate_exception_sku_dropdown()
        
        # ROW 0 col 2: Tipo Evento (obbligatorio)
        ttk.Label(form_frame, text="Tipo Evento: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=0, column=2, sticky="e", padx=(20, 8), pady=8)
        self.exception_type_var = tk.StringVar(value="WASTE")
        exception_type_combo = ttk.Combobox(
            form_frame,
            textvariable=self.exception_type_var,
            values=["WASTE", "ADJUST", "UNFULFILLED"],
            state="readonly",
            width=15,
        )
        exception_type_combo.grid(row=0, column=3, sticky="w", pady=8)
        exception_type_combo.bind("<<ComboboxSelected>>", self._on_exception_type_change)
        
        # ROW 1: Quantità (obbligatorio) + Hint dinamico
        ttk.Label(form_frame, text="Quantità: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=8)
        qty_frame = ttk.Frame(form_frame)
        qty_frame.grid(row=1, column=1, sticky="w", pady=8)
        
        self.exception_qty_var = tk.StringVar()
        ttk.Entry(qty_frame, textvariable=self.exception_qty_var, width=12).pack(side="left", padx=(0, 10))
        self.exception_qty_var.trace('w', lambda *args: self._validate_exception_form())
        
        # Hint dinamico per quantità
        self.exception_qty_hint = ttk.Label(qty_frame, text="(scartato)", font=("Helvetica", 8, "italic"), foreground="#777")
        self.exception_qty_hint.pack(side="left")
        
        # ROW 1 col 2: Data (obbligatorio)
        ttk.Label(form_frame, text="Data: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=1, column=2, sticky="e", padx=(20, 8), pady=8)
        self.exception_date_var = tk.StringVar(value=self.exception_date.isoformat())
        ttk.Entry(form_frame, textvariable=self.exception_date_var, width=15).grid(row=1, column=3, sticky="w", pady=8)
        self.exception_date_var.trace('w', lambda *args: self._validate_exception_form())
        
        # ROW 2: Notes (opzionale) - span 4 colonne
        ttk.Label(form_frame, text="Note:", font=("Helvetica", 9)).grid(row=2, column=0, sticky="e", padx=(0, 8), pady=8)
        self.exception_notes_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.exception_notes_var, width=70).grid(row=2, column=1, columnspan=3, sticky="ew", pady=8)
        
        # ROW 3: Buttons
        button_frame = ttk.Frame(form_frame)
        button_frame.grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))
        
        self.exception_submit_btn = ttk.Button(button_frame, text="✓ Invia Eccezione", command=self._submit_exception, state="disabled")
        self.exception_submit_btn.pack(side="left", padx=5)
        ttk.Button(button_frame, text="✗ Cancella Modulo", command=self._clear_exception_form).pack(side="left", padx=5)
        
        # Validation status label
        self.exception_validation_label = ttk.Label(button_frame, text="", font=("Helvetica", 8), foreground="#d9534f")
        self.exception_validation_label.pack(side="left", padx=15)
        
        # === HISTORY TABLE ===
        history_frame = ttk.LabelFrame(main_frame, text="Storico Eccezioni", padding=5)
        history_frame.pack(fill="both", expand=True)
        
        # Toolbar
        toolbar_frame = ttk.Frame(history_frame)
        toolbar_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Label(toolbar_frame, text="Visualizza Data:", font=("Helvetica", 9)).pack(side="left", padx=(0, 5))
        self.exception_view_date_var = tk.StringVar(value=self.exception_date.isoformat())
        ttk.Entry(toolbar_frame, textvariable=self.exception_view_date_var, width=15).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar_frame, text="🔄 Aggiorna", command=self._refresh_exception_tab).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="📅 Oggi", command=self._set_exception_today).pack(side="left", padx=5)
        
        # Separator
        ttk.Separator(toolbar_frame, orient="vertical").pack(side="left", fill="y", padx=10)
        
        ttk.Button(toolbar_frame, text="🗑️ Annulla Selezionata", command=self._revert_selected_exception).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="🗑️ Annulla Tutte...", command=self._revert_bulk_exceptions).pack(side="left", padx=5)
        
        # Table
        table_frame = ttk.Frame(history_frame)
        table_frame.pack(fill="both", expand=True)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.exception_treeview = ttk.Treeview(
            table_frame,
            columns=("Type", "SKU", "Qty", "Notes", "Date"),
            height=9,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.exception_treeview.yview)
        
        self.exception_treeview.column("#0", width=0, stretch=tk.NO)
        self.exception_treeview.column("Type", anchor=tk.W, width=100)
        self.exception_treeview.column("SKU", anchor=tk.W, width=120)
        self.exception_treeview.column("Qty", anchor=tk.CENTER, width=90)
        self.exception_treeview.column("Notes", anchor=tk.W, width=200)
        self.exception_treeview.column("Date", anchor=tk.CENTER, width=110)
        
        self.exception_treeview.heading("Type", text="Type", anchor=tk.W)
        self.exception_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.exception_treeview.heading("Qty", text="Qty", anchor=tk.CENTER)
        self.exception_treeview.heading("Notes", text="Notes", anchor=tk.W)
        self.exception_treeview.heading("Date", text="Date", anchor=tk.CENTER)
        
        self.exception_treeview.pack(fill="both", expand=True)
    
    def _populate_exception_sku_dropdown(self):
        """Popola la combo SKU con codice e descrizione, inizializzando il mapping."""
        # Leggi tutti gli SKU con descrizioni
        skus = self.csv_layer.read_skus()
        
        # Crea mapping e lista formattata
        self.exception_sku_map = {}
        self.exception_sku_list = []
        
        for sku_obj in skus:
            display_text = f"{sku_obj.sku} - {sku_obj.description}"
            self.exception_sku_list.append(display_text)
            self.exception_sku_map[display_text] = sku_obj.sku  # Mapping display -> codice
    
    def _filter_exception_sku(self):
        """Filtra SKU in real-time mentre l'utente digita, mostrando codice e descrizione."""
        search_text = self.exception_sku_var.get().strip().lower()
        
        # Filtra SKU per match su codice o descrizione (case-insensitive)
        skus = self.csv_layer.read_skus()
        filtered = []
        
        for sku_obj in skus:
            if not search_text or (search_text in sku_obj.sku.lower() or 
                search_text in sku_obj.description.lower()):
                display_text = f"{sku_obj.sku} - {sku_obj.description}"
                filtered.append(display_text)
        
        # Mostra popup se ci sono risultati e se l'utente sta digitando
        if filtered and search_text:
            self._show_sku_popup(filtered)
        else:
            self._hide_sku_popup()
    
    def _show_sku_popup(self, items):
        """Mostra il popup con la lista filtrata."""
        if not self.exception_sku_popup:
            # Crea popup window
            self.exception_sku_popup = tk.Toplevel(self.root)
            self.exception_sku_popup.wm_overrideredirect(True)  # Rimuovi bordi finestra
            
            # Listbox con scrollbar
            frame = ttk.Frame(self.exception_sku_popup, relief='solid', borderwidth=1)
            frame.pack(fill='both', expand=True)
            
            scrollbar = ttk.Scrollbar(frame)
            scrollbar.pack(side='right', fill='y')
            
            self.exception_sku_listbox = tk.Listbox(
                frame,
                height=8,
                yscrollcommand=scrollbar.set,
                font=("Helvetica", 9),
                selectmode=tk.SINGLE
            )
            self.exception_sku_listbox.pack(fill='both', expand=True)
            scrollbar.config(command=self.exception_sku_listbox.yview)
            
            # Bind click per selezione
            self.exception_sku_listbox.bind('<Button-1>', self._on_sku_listbox_click)
            self.exception_sku_listbox.bind('<Return>', self._on_sku_select)
        
        # Aggiorna items
        self.exception_sku_listbox.delete(0, tk.END)
        for item in items:
            self.exception_sku_listbox.insert(tk.END, item)
        
        # Seleziona primo item
        if items:
            self.exception_sku_listbox.selection_clear(0, tk.END)
            self.exception_sku_listbox.selection_set(0)
            self.exception_sku_listbox.activate(0)
        
        # Posiziona popup sotto l'entry
        x = self.exception_sku_entry.winfo_rootx()
        y = self.exception_sku_entry.winfo_rooty() + self.exception_sku_entry.winfo_height()
        width = self.exception_sku_entry.winfo_width()
        
        self.exception_sku_popup.geometry(f"{width}x200+{x}+{y}")
        self.exception_sku_popup.deiconify()
    
    def _hide_sku_popup(self):
        """Nascondi il popup."""
        if self.exception_sku_popup:
            self.exception_sku_popup.withdraw()
    
    def _on_sku_down(self, event):
        """Naviga giù nella listbox."""
        if self.exception_sku_listbox and self.exception_sku_popup and self.exception_sku_popup.winfo_viewable():
            current = self.exception_sku_listbox.curselection()
            if current:
                next_index = min(current[0] + 1, self.exception_sku_listbox.size() - 1)
            else:
                next_index = 0
            
            self.exception_sku_listbox.selection_clear(0, tk.END)
            self.exception_sku_listbox.selection_set(next_index)
            self.exception_sku_listbox.activate(next_index)
            self.exception_sku_listbox.see(next_index)
            return 'break'  # Previeni comportamento default
    
    def _on_sku_up(self, event):
        """Naviga su nella listbox."""
        if self.exception_sku_listbox and self.exception_sku_popup and self.exception_sku_popup.winfo_viewable():
            current = self.exception_sku_listbox.curselection()
            if current:
                prev_index = max(current[0] - 1, 0)
                self.exception_sku_listbox.selection_clear(0, tk.END)
                self.exception_sku_listbox.selection_set(prev_index)
                self.exception_sku_listbox.activate(prev_index)
                self.exception_sku_listbox.see(prev_index)
            return 'break'
    
    def _on_sku_select(self, event):
        """Seleziona l'item dalla listbox."""
        if self.exception_sku_listbox and self.exception_sku_popup and self.exception_sku_popup.winfo_viewable():
            selection = self.exception_sku_listbox.curselection()
            if selection:
                selected_text = self.exception_sku_listbox.get(selection[0])
                self.exception_sku_var.set(selected_text)
                self._hide_sku_popup()
                # Sposta focus al prossimo campo (quantità)
                self.exception_sku_entry.event_generate('<Tab>')
                return 'break'
    
    def _on_sku_escape(self, event):
        """Chiudi popup con ESC."""
        self._hide_sku_popup()
        return 'break'
    
    def _on_sku_focus_out(self, event):
        """Nascondi popup quando focus esce (con delay per permettere click)."""
        # Delay per permettere click su listbox
        self.exception_sku_entry.after(200, self._hide_sku_popup)
    
    def _on_sku_listbox_click(self, event):
        """Gestisci click sulla listbox."""
        # Trova item cliccato
        index = self.exception_sku_listbox.nearest(event.y)
        if index >= 0:
            selected_text = self.exception_sku_listbox.get(index)
            self.exception_sku_var.set(selected_text)
            self._hide_sku_popup()
            self.exception_sku_entry.focus_set()
        return 'break'
    
    def _on_exception_type_change(self, event=None):
        """Aggiorna hint dinamico quando cambia tipo evento."""
        event_type = self.exception_type_var.get()
        
        if event_type == "WASTE":
            self.exception_qty_hint.config(text="(scartato)", foreground="#d9534f")
        elif event_type == "ADJUST":
            self.exception_qty_hint.config(text="(quantità corretta)", foreground="#5bc0de")
        elif event_type == "UNFULFILLED":
            self.exception_qty_hint.config(text="(inevaso)", foreground="#f0ad4e")
        else:
            self.exception_qty_hint.config(text="")
        
        # Re-valida form
        self._validate_exception_form()
    
    def _validate_exception_form(self):
        """Valida form eccezioni in real-time e abilita/disabilita bottone Invia."""
        sku_display = self.exception_sku_var.get().strip()
        qty_str = self.exception_qty_var.get().strip()
        date_str = self.exception_date_var.get().strip()
        
        # Estrai codice SKU
        if " - " in sku_display:
            sku = sku_display.split(" - ")[0].strip()
        else:
            sku = sku_display
        
        errors = []
        
        if not sku:
            errors.append("SKU")
        
        if not qty_str:
            errors.append("Quantità")
        else:
            try:
                int(qty_str)
            except ValueError:
                errors.append("Quantità (deve essere numero)")
        
        if not date_str:
            errors.append("Data")
        else:
            try:
                date.fromisoformat(date_str)
            except ValueError:
                errors.append("Data (formato YYYY-MM-DD)")
        
        if errors:
            self.exception_submit_btn.config(state="disabled")
            self.exception_validation_label.config(text=f"Campi mancanti: {', '.join(errors)}")
        else:
            self.exception_submit_btn.config(state="normal")
            self.exception_validation_label.config(text="✓ Pronto", foreground="#5cb85c")
    
    def _clear_exception_form(self):
        """Clear exception form fields."""
        self.exception_type_var.set("WASTE")
        self.exception_sku_var.set("")
        self.exception_qty_var.set("")
        self.exception_date_var.set(date.today().isoformat())
        self.exception_notes_var.set("")
        self._on_exception_type_change()  # Reset hint
        self._validate_exception_form()  # Reset validation
    
    def _submit_exception(self):
        """Submit exception from quick entry form."""
        # Validate inputs
        event_type_str = self.exception_type_var.get()
        sku_display = self.exception_sku_var.get().strip()
        qty_str = self.exception_qty_var.get().strip()
        date_str = self.exception_date_var.get().strip()
        notes = self.exception_notes_var.get().strip()
        
        # Estrai codice SKU dalla stringa formattata "SKU001 - Descrizione"
        if " - " in sku_display:
            sku = sku_display.split(" - ")[0].strip()
        else:
            sku = sku_display
        
        if not sku:
            messagebox.showerror("Errore di Validazione", "Seleziona uno SKU.")
            return
        
        if not qty_str:
            messagebox.showerror("Errore di Validazione", "Inserisci una quantità.")
            return
        
        try:
            qty = int(qty_str)
        except ValueError:
            messagebox.showerror("Errore di Validazione", "La quantità deve essere un numero intero.")
            return
        
        try:
            event_date = date.fromisoformat(date_str)
        except ValueError:
            messagebox.showerror("Errore di Validazione", "Formato data non valido. Usa YYYY-MM-DD.")
            return
        
        # Map string to EventType
        event_type_map = {
            "WASTE": EventType.WASTE,
            "ADJUST": EventType.ADJUST,
            "UNFULFILLED": EventType.UNFULFILLED,
        }
        event_type = event_type_map.get(event_type_str)
        
        if not event_type:
            messagebox.showerror("Errore", f"Tipo evento non valido: {event_type_str}")
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
                    "Successo",
                    f"Eccezione registrata con successo:\n{event_type_str} - {sku} - Q.tà: {qty}",
                )
                self._clear_exception_form()
                self._refresh_exception_tab()
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile registrare eccezione: {str(e)}")
    
    def _refresh_exception_tab(self):
        """Refresh exception history table."""
        try:
            view_date_str = self.exception_view_date_var.get()
            view_date = date.fromisoformat(view_date_str)
        except ValueError:
            messagebox.showerror("Errore", "Formato data visualizzazione non valido. Usa YYYY-MM-DD.")
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
            
            # Display quantity with correct sign
            if txn.event == EventType.ADJUST:
                # ADJUST shows signed value (can be + or -)
                display_qty = f"{txn.qty:+d}"
            elif txn.event == EventType.UNFULFILLED:
                # UNFULFILLED reduces on_order, show as negative
                display_qty = f"-{abs(txn.qty)}"
            else:
                # WASTE shows positive (quantity wasted)
                display_qty = str(txn.qty)
            
            self.exception_treeview.insert(
                "",
                "end",
                values=(
                    txn.event.value,
                    txn.sku,
                    display_qty,
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
            messagebox.showwarning("Nessuna Selezione", "Seleziona un'eccezione da annullare.")
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
            "Conferma Annullamento",
            f"Annullare tutte le eccezioni {event_type_str} per SKU '{sku}' del {date_str}?\n\nQuesta azione non può essere annullata.",
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
                    "Successo",
                    f"Annullate {reverted_count} eccezione/i per {event_type_str} - {sku} del {date_str}.",
                )
                self._refresh_exception_tab()
            else:
                messagebox.showwarning("Nessuna Modifica", "Nessuna eccezione trovata da annullare.")
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile annullare eccezione: {str(e)}")
    
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
        
        # SKU con autocomplete
        ttk.Label(form_frame, text="SKU:", font=("Helvetica", 10)).pack(anchor="w", pady=(0, 5))
        bulk_sku_var = tk.StringVar()
        
        bulk_sku_ac = AutocompleteEntry(
            form_frame,
            textvariable=bulk_sku_var,
            items_callback=self._filter_pending_sku_items,
            width=40
        )
        bulk_sku_ac.entry.pack(fill="x", pady=(0, 10))
        
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
                messagebox.showerror("Errore di Validazione", "Seleziona uno SKU.", parent=popup)
                return
            
            try:
                event_date = date.fromisoformat(date_str)
            except ValueError:
                messagebox.showerror("Errore di Validazione", "Formato data non valido. Usa YYYY-MM-DD.", parent=popup)
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
                    "Successo",
                    f"Annullate {reverted_count} eccezione/i.",
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
        ttk.Label(title_frame, text="Gestione SKU", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # Search bar
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Label(search_frame, text="Cerca:").pack(side="left", padx=5)
        self.search_var = tk.StringVar()
        
        # Autocomplete per SKU con ricerca real-time
        self.search_entry = AutocompleteEntry(
            search_frame,
            textvariable=self.search_var,
            items_callback=self._filter_pending_sku_items,
            width=35,
            on_select=lambda selected: self._search_skus()  # Auto-search on select
        )
        self.search_entry.pack(side="left", padx=5)
        ttk.Button(search_frame, text="Cerca", command=self._search_skus).pack(side="left", padx=5)
        ttk.Button(search_frame, text="Cancella", command=self._clear_search).pack(side="left", padx=2)
        
        # Bind Enter key to search
        self.search_entry.bind("<Return>", lambda e: self._search_skus())
        
        # Toolbar
        toolbar_frame = ttk.Frame(main_frame)
        toolbar_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Button(toolbar_frame, text="➕ Nuovo SKU", command=self._new_sku).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="✏️ Modifica SKU", command=self._edit_sku).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="🗑️ Elimina SKU", command=self._delete_sku).pack(side="left", padx=5)
        ttk.Button(toolbar_frame, text="🔄 Aggiorna", command=self._refresh_admin_tab).pack(side="left", padx=5)
        
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
        
        self.admin_treeview.heading("SKU", text="Codice SKU", anchor=tk.W)
        self.admin_treeview.heading("Description", text="Descrizione", anchor=tk.W)
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
            messagebox.showwarning("Nessuna Selezione", "Seleziona uno SKU da modificare.")
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
            messagebox.showwarning("Nessuna Selezione", "Seleziona uno SKU da eliminare.")
            return
        
        # Get selected SKU data
        item = self.admin_treeview.item(selected[0])
        values = item["values"]
        sku_code = values[0]
        
        # Check if can delete
        can_delete, reason = self.csv_layer.can_delete_sku(sku_code)
        if not can_delete:
            messagebox.showerror("Impossibile Eliminare", f"Impossibile eliminare SKU:\n{reason}")
            return
        
        # Confirm deletion
        confirm = messagebox.askyesno(
            "Conferma Eliminazione",
            f"Sei sicuro di voler eliminare lo SKU '{sku_code}'?\n\nQuesta azione non può essere annullata.",
        )
        if not confirm:
            return
        
        # Delete SKU
        success = self.csv_layer.delete_sku(sku_code)
        if success:
            # Log audit trail
            self.csv_layer.log_audit(
                operation="SKU_DELETE",
                details=f"Deleted SKU: {sku_code}",
                sku=sku_code,
            )
            
            messagebox.showinfo("Successo", f"SKU '{sku_code}' eliminato con successo.")
            self._refresh_admin_tab()
        else:
            messagebox.showerror("Errore", f"Impossibile eliminare SKU '{sku_code}'.")
    
    def _show_sku_form(self, mode="new", sku_code=None):
        """
        Show SKU form in popup window.
        
        Args:
            mode: "new" or "edit"
            sku_code: SKU code to edit (for edit mode)
        """
        # Create popup window
        popup = tk.Toplevel(self.root)
        popup.title("Nuovo SKU" if mode == "new" else "Modifica SKU")
        popup.geometry("600x500")
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
        ttk.Label(form_frame, text="Codice SKU:", font=("Helvetica", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=5
        )
        sku_var = tk.StringVar(value=current_sku.sku if current_sku else "")
        sku_entry = ttk.Entry(form_frame, textvariable=sku_var, width=40)
        sku_entry.grid(row=0, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Description field
        ttk.Label(form_frame, text="Descrizione:", font=("Helvetica", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=5
        )
        desc_var = tk.StringVar(value=current_sku.description if current_sku else "")
        desc_entry = ttk.Entry(form_frame, textvariable=desc_var, width=40)
        desc_entry.grid(row=1, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # EAN field
        ttk.Label(form_frame, text="EAN (opzionale):", font=("Helvetica", 10, "bold")).grid(
            row=2, column=0, sticky="w", pady=5
        )
        ean_var = tk.StringVar(value=current_sku.ean if current_sku and current_sku.ean else "")
        ean_entry = ttk.Entry(form_frame, textvariable=ean_var, width=40)
        ean_entry.grid(row=2, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # MOQ field
        ttk.Label(form_frame, text="Q.tà Minima Ordine (MOQ):", font=("Helvetica", 10, "bold")).grid(
            row=3, column=0, sticky="w", pady=5
        )
        moq_var = tk.StringVar(value=str(current_sku.moq) if current_sku else "1")
        ttk.Entry(form_frame, textvariable=moq_var, width=40).grid(row=3, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Lead Time field
        ttk.Label(form_frame, text="Lead Time (giorni):", font=("Helvetica", 10, "bold")).grid(
            row=4, column=0, sticky="w", pady=5
        )
        lead_time_var = tk.StringVar(value=str(current_sku.lead_time_days) if current_sku else "7")
        ttk.Entry(form_frame, textvariable=lead_time_var, width=40).grid(row=4, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Max Stock field
        ttk.Label(form_frame, text="Stock Massimo:", font=("Helvetica", 10, "bold")).grid(
            row=5, column=0, sticky="w", pady=5
        )
        max_stock_var = tk.StringVar(value=str(current_sku.max_stock) if current_sku else "999")
        ttk.Entry(form_frame, textvariable=max_stock_var, width=40).grid(row=5, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Reorder Point field
        ttk.Label(form_frame, text="Punto di Riordino:", font=("Helvetica", 10, "bold")).grid(
            row=6, column=0, sticky="w", pady=5
        )
        reorder_point_var = tk.StringVar(value=str(current_sku.reorder_point) if current_sku else "10")
        ttk.Entry(form_frame, textvariable=reorder_point_var, width=40).grid(row=6, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Supplier field con autocomplete
        ttk.Label(form_frame, text="Fornitore:", font=("Helvetica", 10, "bold")).grid(
            row=7, column=0, sticky="w", pady=5
        )
        supplier_var = tk.StringVar(value=current_sku.supplier if current_sku else "")
        
        supplier_ac = AutocompleteEntry(
            form_frame,
            textvariable=supplier_var,
            items_callback=self._filter_supplier_items,
            width=40
        )
        supplier_ac.entry.grid(row=7, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Demand Variability field
        ttk.Label(form_frame, text="Variabilità Domanda:", font=("Helvetica", 10, "bold")).grid(
            row=8, column=0, sticky="w", pady=5
        )
        demand_var = tk.StringVar(value=current_sku.demand_variability.value if current_sku else "STABLE")
        demand_combo = ttk.Combobox(form_frame, textvariable=demand_var, values=["STABLE", "LOW", "HIGH", "SEASONAL"], state="readonly", width=37)
        demand_combo.grid(row=8, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Validate EAN button and status label
        ean_status_var = tk.StringVar(value="")
        ttk.Button(
            form_frame, 
            text="Valida EAN", 
            command=lambda: self._validate_ean_field(ean_var.get(), ean_status_var)
        ).grid(row=9, column=1, sticky="w", pady=5, padx=(10, 0))
        
        ean_status_label = ttk.Label(form_frame, textvariable=ean_status_var, foreground="green")
        ean_status_label.grid(row=10, column=1, sticky="w", padx=(10, 0))
        
        # Configure grid
        form_frame.columnconfigure(1, weight=1)
        
        # Button frame
        button_frame = ttk.Frame(popup, padding=10)
        button_frame.pack(side="bottom", fill="x")
        
        ttk.Button(
            button_frame,
            text="Salva",
            command=lambda: self._save_sku_form(
                popup, mode, sku_var.get(), desc_var.get(), ean_var.get(),
                moq_var.get(), lead_time_var.get(), max_stock_var.get(),
                reorder_point_var.get(), supplier_var.get(), demand_var.get(),
                current_sku
            ),
        ).pack(side="right", padx=5)
        
        ttk.Button(button_frame, text="Annulla", command=popup.destroy).pack(side="right", padx=5)
        
        # Focus on first field
        if mode == "new":
            sku_entry.focus()
        else:
            desc_entry.focus()
    
    def _validate_ean_field(self, ean: str, status_var: tk.StringVar):
        """Validate EAN and update status label."""
        if not ean or not ean.strip():
            status_var.set("✓ EAN vuoto è valido")
            return
        
        is_valid, error = validate_ean(ean.strip())
        if is_valid:
            status_var.set("✓ EAN Valido")
        else:
            status_var.set(f"✗ {error}")
    
    def _save_sku_form(self, popup, mode, sku_code, description, ean,
                        moq_str, lead_time_str, max_stock_str, reorder_point_str,
                        supplier, demand_variability_str, current_sku):
        """Save SKU from form."""
        # Validate inputs
        if not sku_code or not sku_code.strip():
            messagebox.showerror("Errore di Validazione", "Il codice SKU non può essere vuoto.", parent=popup)
            return
        
        if not description or not description.strip():
            messagebox.showerror("Errore di Validazione", "La descrizione non può essere vuota.", parent=popup)
            return
        
        sku_code = sku_code.strip()
        description = description.strip()
        ean = ean.strip() if ean else None
        supplier = supplier.strip()
        
        # Parse and validate numeric fields
        try:
            moq = int(moq_str)
            lead_time_days = int(lead_time_str)
            max_stock = int(max_stock_str)
            reorder_point = int(reorder_point_str)
        except ValueError:
            messagebox.showerror("Errore di Validazione", "MOQ, Lead Time, Stock Massimo e Punto di Riordino devono essere numeri interi.", parent=popup)
            return
        
        # Parse demand variability
        from ..domain.models import DemandVariability
        try:
            demand_variability = DemandVariability[demand_variability_str]
        except KeyError:
            demand_variability = DemandVariability.STABLE
        
        # Validate EAN if provided
        if ean:
            is_valid, error = validate_ean(ean)
            if not is_valid:
                messagebox.showerror("EAN Non Valido", error, parent=popup)
                return
        
        # Check for duplicate SKU code (only for new or if code changed)
        if mode == "new" or (current_sku and sku_code != current_sku.sku):
            if self.csv_layer.sku_exists(sku_code):
                messagebox.showerror(
                    "SKU Duplicato",
                    f"Il codice SKU '{sku_code}' esiste già. Usa un codice diverso.",
                    parent=popup,
                )
                return
        
        try:
            if mode == "new":
                # Create new SKU
                new_sku = SKU(
                    sku=sku_code,
                    description=description,
                    ean=ean,
                    moq=moq,
                    lead_time_days=lead_time_days,
                    max_stock=max_stock,
                    reorder_point=reorder_point,
                    supplier=supplier,
                    demand_variability=demand_variability,
                )
                self.csv_layer.write_sku(new_sku)
                
                # Log audit trail
                self.csv_layer.log_audit(
                    operation="SKU_CREATE",
                    details=f"Created SKU: {description} (MOQ: {moq}, Lead Time: {lead_time_days}d)",
                    sku=sku_code,
                )
                
                messagebox.showinfo("Successo", f"SKU '{sku_code}' creato con successo.", parent=popup)
            else:
                # Update existing SKU
                old_sku_code = current_sku.sku
                success = self.csv_layer.update_sku(
                    old_sku_code, sku_code, description, ean,
                    moq, lead_time_days, max_stock, reorder_point,
                    supplier, demand_variability
                )
                if success:
                    # Build change details
                    changes = []
                    if old_sku_code != sku_code:
                        changes.append(f"Code: {old_sku_code} → {sku_code}")
                    if current_sku.description != description:
                        changes.append(f"Description: {current_sku.description} → {description}")
                    if current_sku.ean != ean:
                        changes.append(f"EAN: {current_sku.ean or 'N/A'} → {ean or 'N/A'}")
                    
                    change_details = ", ".join(changes) if changes else "No changes"
                    
                    # Log audit trail
                    self.csv_layer.log_audit(
                        operation="SKU_EDIT",
                        details=f"Updated SKU: {change_details}",
                        sku=sku_code,
                    )
                    
                    if old_sku_code != sku_code:
                        messagebox.showinfo(
                            "Successo",
                            f"SKU aggiornato con successo.\nCodice SKU cambiato da '{old_sku_code}' a '{sku_code}'.\nTutti i riferimenti nel ledger sono stati aggiornati.",
                            parent=popup,
                        )
                    else:
                        messagebox.showinfo("Successo", f"SKU '{sku_code}' aggiornato con successo.", parent=popup)
                else:
                    messagebox.showerror("Errore", "Impossibile aggiornare SKU.", parent=popup)
                    return
            
            # Refresh table and close popup
            popup.destroy()
            self._refresh_admin_tab()
            
        except ValueError as e:
            messagebox.showerror("Errore", str(e), parent=popup)

    
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
            
            # Get EOD stock value if edited
            eod_value = self.eod_stock_edits.get(sku_id, "")
            
            item_id = self.stock_treeview.insert(
                "",
                "end",
                values=(
                    stock.sku,
                    description,
                    stock.on_hand,
                    stock.on_order,
                    stock.available(),
                    eod_value,
                ),
            )
            
            # Apply tag if edited
            if sku_id in self.eod_stock_edits:
                self.stock_treeview.item(item_id, tags=("eod_edited",))
    
    def _on_stock_select(self, event):
        """Handle stock treeview selection to show audit timeline."""
        selection = self.stock_treeview.selection()
        if not selection:
            self.selected_sku_for_audit = None
            self.audit_sku_label.config(text="No SKU selected")
            self.audit_timeline_treeview.delete(*self.audit_timeline_treeview.get_children())
            return
        
        # Get selected SKU
        item = self.stock_treeview.item(selection[0])
        sku_code = item["values"][0]
        self.selected_sku_for_audit = sku_code
        
        # Update label
        self.audit_sku_label.config(text=f"Timeline for: {sku_code}")
        
        # Refresh timeline
        self._refresh_audit_timeline()
    
    def _refresh_audit_timeline(self):
        """Refresh audit timeline for selected SKU."""
        self.audit_timeline_treeview.delete(*self.audit_timeline_treeview.get_children())
        
        if not self.selected_sku_for_audit:
            return
        
        try:
            # Get all transactions for this SKU
            transactions = self.csv_layer.read_transactions()
            sku_transactions = [t for t in transactions if t.sku == self.selected_sku_for_audit]
            
            # Sort by date (most recent first)
            sku_transactions = sorted(sku_transactions, key=lambda t: t.date, reverse=True)
            
            # Get audit log entries for this SKU
            audit_logs = self.csv_layer.read_audit_log(sku=self.selected_sku_for_audit, limit=50)
            
            # Combine and display
            # First, show audit logs (SKU edits, etc.)
            if audit_logs:
                # Insert separator
                self.audit_timeline_treeview.insert("", "end", values=("=== AUDIT LOG ===", "", "", ""))
                
                for log in audit_logs:
                    self.audit_timeline_treeview.insert(
                        "",
                        "end",
                        values=(
                            log.timestamp,
                            log.operation,
                            "",
                            log.details,
                        ),
                    )
            
            # Then, show transactions
            if sku_transactions:
                # Insert separator
                self.audit_timeline_treeview.insert("", "end", values=("=== LEDGER EVENTS ===", "", "", ""))
                
                for txn in sku_transactions:
                    note = txn.note or ""
                    if txn.receipt_date:
                        note = f"Receipt: {txn.receipt_date.isoformat()} | {note}"
                    
                    # Display quantity with correct sign:
                    # - Events that reduce stock/orders: show negative (SALE, WASTE, RECEIPT, UNFULFILLED)
                    # - Events that increase stock/orders: show positive (ORDER, SNAPSHOT, ADJUST)
                    display_qty = txn.qty
                    if txn.event in [EventType.UNFULFILLED, EventType.RECEIPT]:
                        # These events reduce on_order, show as negative
                        display_qty = -abs(txn.qty)
                    elif txn.event in [EventType.SALE, EventType.WASTE]:
                        # These events reduce on_hand, show as negative
                        display_qty = -abs(txn.qty)
                    
                    self.audit_timeline_treeview.insert(
                        "",
                        "end",
                        values=(
                            txn.date.isoformat(),
                            txn.event.value,
                            f"{display_qty:+d}" if display_qty else "",
                            note,
                        ),
                    )
            
            if not audit_logs and not sku_transactions:
                self.audit_timeline_treeview.insert("", "end", values=("No history found", "", "", ""))
        
        except Exception as e:
            self.audit_timeline_treeview.insert("", "end", values=(f"Error: {str(e)}", "", "", ""))
    
    def _on_stock_eod_double_click(self, event):
        """Handle double-click on EOD Stock column for editing."""
        # Identify which column was clicked
        region = self.stock_treeview.identify_region(event.x, event.y)
        if region != "cell":
            return
        
        column = self.stock_treeview.identify_column(event.x)
        # EOD Stock is column #6 (0-indexed: #0, #1=SKU, #2=Desc, #3=OnHand, #4=OnOrder, #5=Available, #6=EOD)
        if column != "#6":
            return
        
        # Get selected item
        selection = self.stock_treeview.selection()
        if not selection:
            return
        
        item_id = selection[0]
        item = self.stock_treeview.item(item_id)
        sku = item["values"][0]
        on_hand = item["values"][2]
        
        # Show dialog to edit EOD stock
        new_eod_stock = simpledialog.askinteger(
            "Inserisci Stock EOD",
            f"SKU: {sku}\nDisponibile corrente: {on_hand}\n\nInserisci stock a fine giornata:",
            minvalue=0,
            initialvalue=on_hand if on_hand else 0,
        )
        
        if new_eod_stock is None:
            return  # User cancelled
        
        # Store edit
        self.eod_stock_edits[sku] = new_eod_stock
        
        # Update display
        self.stock_treeview.item(
            item_id,
            values=(
                item["values"][0],  # SKU
                item["values"][1],  # Description
                item["values"][2],  # On Hand
                item["values"][3],  # On Order
                item["values"][4],  # Available
                new_eod_stock,      # EOD Stock
            ),
            tags=("eod_edited",),
        )
    
    def _filter_stock_table(self):
        """Filter stock table based on search input."""
        search_text = self.stock_search_var.get().lower()
        
        # If search empty, refresh full table
        if not search_text:
            self._refresh_stock_tab()
            return
        
        # Filter items
        for item_id in self.stock_treeview.get_children():
            item = self.stock_treeview.item(item_id)
            sku = str(item["values"][0]).lower()
            description = str(item["values"][1]).lower()
            
            # Show if matches SKU or description
            if search_text in sku or search_text in description:
                self.stock_treeview.reattach(item_id, "", "end")
            else:
                self.stock_treeview.detach(item_id)
    
    def _confirm_eod_close(self):
        """Confirm EOD stock entries and calculate sales."""
        if not self.eod_stock_edits:
            messagebox.showinfo("Info", "Nessun dato EOD inserito. Modifica almeno un valore Stock EOD nella tabella.")
            return
        
        # Confirm with user
        num_entries = len(self.eod_stock_edits)
        confirm = messagebox.askyesno(
            "Conferma Chiusura",
            f"Confermare chiusura giornaliera per {num_entries} SKU?\n\n"
            f"Data: {self.asof_date.isoformat()}\n\n"
            "Questo calcolerà il venduto e aggiornerà stock e vendite.",
        )
        
        if not confirm:
            return
        
        # Process EOD entries
        try:
            results = self.daily_close_workflow.process_bulk_eod_stock(
                eod_entries=self.eod_stock_edits,
                eod_date=self.asof_date,
            )
            
            # Show results
            result_text = "\n".join(results)
            messagebox.showinfo("Chiusura Completata", f"Risultati:\n\n{result_text}")
            
            # Clear edits and refresh
            self.eod_stock_edits.clear()
            self._refresh_stock_tab()
            self._refresh_audit_timeline()
            
        except Exception as e:
            messagebox.showerror("Errore", f"Errore durante chiusura giornaliera:\n{str(e)}")
    
    # === EXPORT FUNCTIONALITY ===
    
    def _export_stock_snapshot(self):
        """Export stock snapshot as CSV (AsOf current date)."""
        try:
            # Ask user for file path
            file_path = filedialog.asksaveasfilename(
                title="Export Stock Snapshot",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"stock_snapshot_{self.asof_date.isoformat()}.csv",
            )
            
            if not file_path:
                return  # User cancelled
            
            # Get current stock
            sku_ids = self.csv_layer.get_all_sku_ids()
            skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
            transactions = self.csv_layer.read_transactions()
            sales_records = self.csv_layer.read_sales()
            
            stocks = StockCalculator.calculate_all_skus(
                sku_ids,
                self.asof_date,
                transactions,
                sales_records,
            )
            
            # Write CSV
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["SKU", "Description", "EAN", "On Hand", "On Order", "Available", "AsOf Date"])
                
                for sku_id in sku_ids:
                    stock = stocks[sku_id]
                    sku_obj = skus_by_id.get(sku_id)
                    description = sku_obj.description if sku_obj else "N/A"
                    ean = sku_obj.ean if sku_obj else ""
                    
                    writer.writerow([
                        sku_id,
                        description,
                        ean,
                        stock.on_hand,
                        stock.on_order,
                        stock.on_hand + stock.on_order,
                        self.asof_date.isoformat(),
                    ])
            
            messagebox.showinfo("Successo", f"Snapshot stock esportato in:\n{file_path}\n\n{len(sku_ids)} SKU esportati.")
            
            # Log export operation
            self.csv_layer.log_audit(
                operation="EXPORT",
                details=f"Stock snapshot exported ({len(sku_ids)} SKUs, AsOf {self.asof_date.isoformat()})",
                sku=None,
            )
        
        except Exception as e:
            messagebox.showerror("Errore Esportazione", f"Impossibile esportare snapshot stock: {str(e)}")
    
    def _export_ledger(self):
        """Export ledger (transactions) as CSV."""
        try:
            file_path = filedialog.asksaveasfilename(
                title="Export Ledger (Transactions)",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"ledger_{date.today().isoformat()}.csv",
            )
            
            if not file_path:
                return
            
            transactions = self.csv_layer.read_transactions()
            
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Date", "SKU", "Event", "Quantity", "Receipt Date", "Notes"])
                
                for txn in transactions:
                    writer.writerow([
                        txn.date.isoformat(),
                        txn.sku,
                        txn.event.name,
                        txn.qty,
                        txn.receipt_date.isoformat() if txn.receipt_date else "",
                        txn.note,
                    ])
            
            messagebox.showinfo("Successo", f"Ledger esportato in:\n{file_path}\n\n{len(transactions)} transazioni esportate.")
            
            # Log export operation
            self.csv_layer.log_audit(
                operation="EXPORT",
                details=f"Ledger exported ({len(transactions)} transactions)",
                sku=None,
            )
        
        except Exception as e:
            messagebox.showerror("Errore Esportazione", f"Impossibile esportare ledger: {str(e)}")
    
    def _export_sku_list(self):
        """Export SKU list as CSV."""
        try:
            file_path = filedialog.asksaveasfilename(
                title="Export SKU List",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"sku_list_{date.today().isoformat()}.csv",
            )
            
            if not file_path:
                return
            
            skus = self.csv_layer.read_skus()
            
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["SKU", "Description", "EAN"])
                
                for sku in skus:
                    writer.writerow([sku.sku, sku.description, sku.ean])
            
            messagebox.showinfo("Successo", f"Elenco SKU esportato in:\n{file_path}\n\n{len(skus)} SKU esportati.")
            
            # Log export operation
            self.csv_layer.log_audit(
                operation="EXPORT",
                details=f"SKU list exported ({len(skus)} SKUs)",
                sku=None,
            )
        
        except Exception as e:
            messagebox.showerror("Errore Esportazione", f"Impossibile esportare elenco SKU: {str(e)}")
    
    def _export_order_logs(self):
        """Export order logs as CSV."""
        try:
            file_path = filedialog.asksaveasfilename(
                title="Export Order Logs",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"order_logs_{date.today().isoformat()}.csv",
            )
            
            if not file_path:
                return
            
            logs = self.csv_layer.read_order_logs()
            
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Order ID", "Date", "SKU", "Qty Ordered", "Receipt Date", "Status"])
                
                for log in logs:
                    writer.writerow([
                        log.get("order_id", ""),
                        log.get("date", ""),
                        log.get("sku", ""),
                        log.get("qty_ordered", ""),
                        log.get("receipt_date", ""),
                        log.get("status", ""),
                    ])
            
            messagebox.showinfo("Successo", f"Log ordini esportati in:\n{file_path}\n\n{len(logs)} ordini esportati.")
            
            # Log export operation
            self.csv_layer.log_audit(
                operation="EXPORT",
                details=f"Order logs exported ({len(logs)} orders)",
                sku=None,
            )
        
        except Exception as e:
            messagebox.showerror("Errore Esportazione", f"Impossibile esportare log ordini: {str(e)}")
    
    def _export_receiving_logs(self):
        """Export receiving logs as CSV."""
        try:
            file_path = filedialog.asksaveasfilename(
                title="Export Receiving Logs",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"receiving_logs_{date.today().isoformat()}.csv",
            )
            
            if not file_path:
                return
            
            logs = self.csv_layer.read_receiving_logs()
            
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Receipt ID", "Date", "SKU", "Qty Received", "Receipt Date"])
                
                for log in logs:
                    writer.writerow([
                        log.get("receipt_id", ""),
                        log.get("date", ""),
                        log.get("sku", ""),
                        log.get("qty_received", ""),
                        log.get("receipt_date", ""),
                    ])
            
            messagebox.showinfo("Successo", f"Log ricevimenti esportati in:\n{file_path}\n\n{len(logs)} ricevimenti esportati.")
            
            # Log export operation
            self.csv_layer.log_audit(
                operation="EXPORT",
                details=f"Receiving logs exported ({len(logs)} receipts)",
                sku=None,
            )
        
        except Exception as e:
            messagebox.showerror("Errore Esportazione", f"Impossibile esportare log ricevimenti: {str(e)}")
    
    def _refresh_all(self):
        """Refresh all tabs."""
        self._refresh_dashboard()
        self._refresh_stock_tab()
        self._refresh_pending_orders()
        self._refresh_receiving_history()
        self._refresh_admin_tab()
        self._refresh_exception_tab()


def main():
    """Entry point for GUI."""
    root = tk.Tk()
    app = DesktopOrderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
