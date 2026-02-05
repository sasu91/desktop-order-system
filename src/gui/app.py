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
import logging

try:
    from tkcalendar import DateEntry
    TKCALENDAR_AVAILABLE = True
except ImportError:
    DateEntry = None
    TKCALENDAR_AVAILABLE = False

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
from ..utils.logging_config import setup_logging, get_logger

# Initialize logging
setup_logging(log_dir="logs", app_name="desktop_order_system")
logger = get_logger()


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
        
        try:
            # Initialize CSV layer
            self.csv_layer = CSVLayer(data_dir=data_dir)
            
            # Initialize workflows
            self.order_workflow = OrderWorkflow(self.csv_layer, lead_time_days=7)
            self.receiving_workflow = ReceivingWorkflow(self.csv_layer)
            self.exception_workflow = ExceptionWorkflow(self.csv_layer)
            self.daily_close_workflow = DailyCloseWorkflow(self.csv_layer)
            
            logger.info("Application initialized successfully")
        except Exception as e:
            logger.critical(f"Failed to initialize application: {str(e)}", exc_info=True)
            messagebox.showerror("Errore Critico", f"Impossibile inizializzare l'applicazione:\n{str(e)}")
            raise
        
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
        
        # Selected SKU for dashboard detail charts
        self.selected_dashboard_sku = None
        self.dashboard_sku_var = tk.StringVar()
        self.dashboard_sku_items = []  # Available SKU list for autocomplete
        
        # Tab order mapping (tab_id -> tab_name)
        self.tab_map = {
            "stock": "📦 Stock & Chiusura",
            "order": "📋 Ordini",
            "receiving": "📥 Ricevimenti",
            "exception": "⚠️ Eccezioni",
            "dashboard": "📊 Dashboard",
            "admin": "🔧 Gestione SKU",
            "settings": "⚙️ Impostazioni"
        }
        
        # Load saved tab order from settings
        self.tab_order = self._load_tab_order()
        
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
        
        # Tab drag-and-drop state
        self.drag_tab_index = None
        self.drag_start_x = None
        
        # Bind events for tab reordering
        self.notebook.bind("<Button-1>", self._on_tab_press)
        self.notebook.bind("<B1-Motion>", self._on_tab_drag)
        self.notebook.bind("<ButtonRelease-1>", self._on_tab_release)
        
        # Create tabs
        self.dashboard_tab = ttk.Frame(self.notebook)
        self.stock_tab = ttk.Frame(self.notebook)
        self.order_tab = ttk.Frame(self.notebook)
        self.receiving_tab = ttk.Frame(self.notebook)
        self.exception_tab = ttk.Frame(self.notebook)
        self.admin_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        
        # Map tab IDs to frame objects
        self.tab_frames = {
            "stock": self.stock_tab,
            "order": self.order_tab,
            "receiving": self.receiving_tab,
            "exception": self.exception_tab,
            "dashboard": self.dashboard_tab,
            "admin": self.admin_tab,
            "settings": self.settings_tab
        }
        
        # Add tabs in saved order (or default order if not saved)
        for tab_id in self.tab_order:
            tab_frame = self.tab_frames[tab_id]
            tab_text = self.tab_map[tab_id]
            self.notebook.add(tab_frame, text=tab_text)
        
        # Build tab contents
        self._build_dashboard_tab()
        self._build_stock_tab()
        self._build_order_tab()
        self._build_receiving_tab()
        self._build_exception_tab()
        self._build_admin_tab()
        self._build_settings_tab()
    
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
        if TKCALENDAR_AVAILABLE:
            DateEntry(
                date_frame,
                textvariable=self.asof_date_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).pack(side="left", padx=5)
        else:
            ttk.Entry(date_frame, textvariable=self.asof_date_var, width=15).pack(side="left", padx=5)
            ttk.Label(date_frame, text="(Installa tkcalendar)").pack(side="left", padx=5)
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
        
        # EOD confirmation button (prominent, styled)
        eod_btn = ttk.Button(
            controls_frame,
            text="✓ Conferma Chiusura Giornaliera",
            command=self._confirm_eod_close,
        )
        eod_btn.pack(side="left", padx=20)
        
        # Tooltip hint
        ttk.Label(controls_frame, text="(Doppio click su Stock EOD per modificare)", font=("Helvetica", 8, "italic"), foreground="#777").pack(side="left", padx=5)
        
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
        ttk.Label(title_frame, text="(Monitoraggio vendite e andamento stock)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
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
        
        # SKU Detail Charts
        sku_detail_frame = ttk.LabelFrame(right_panel, text="Dettaglio SKU", padding=5)
        sku_detail_frame.pack(side="top", fill="both", expand=True)
        
        # SKU search bar
        search_frame = ttk.Frame(sku_detail_frame)
        search_frame.pack(fill="x", pady=5)
        ttk.Label(search_frame, text="Cerca SKU:").pack(side="left", padx=5)
        
        self.dashboard_sku_autocomplete = AutocompleteEntry(
            search_frame,
            textvariable=self.dashboard_sku_var,
            items_callback=self._filter_dashboard_sku_items,
            width=15,
            on_select=self._on_dashboard_sku_select
        )
        self.dashboard_sku_autocomplete.entry.pack(side="left", fill="x", expand=True, padx=5)
        
        # Reset button to return to general view
        ttk.Button(
            search_frame,
            text="Reset",
            command=self._reset_dashboard_view,
            width=8
        ).pack(side="left", padx=5)
        
        # Info label
        ttk.Label(
            sku_detail_frame,
            text="Seleziona uno SKU per visualizzare i grafici dettaglio nei pannelli principali",
            font=("Helvetica", 9),
            foreground="gray"
        ).pack(fill="x", padx=5, pady=5)
        
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
            
            # 2. Total Stock Value (from settings)
            settings = self.csv_layer.read_settings()
            unit_price = settings.get("dashboard", {}).get("stock_unit_price", {}).get("value", 10)
            total_stock_units = sum(stock.on_hand for stock in stocks.values())
            total_stock_value = total_stock_units * unit_price
            self.kpi_stock_value_label.config(text=f"Valore Stock: €{total_stock_value:,.0f}")
            
            # 3. Average Days Cover
            total_days_cover = 0
            skus_with_sales = 0
            for sku_id in sku_ids:
                stock = stocks[sku_id]
                daily_sales, _ = calculate_daily_sales_average(sales_records, sku_id, days_lookback=30, transactions=transactions, asof_date=today)
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
                daily_sales, _ = calculate_daily_sales_average(sales_records, sku_id, days_lookback=30, transactions=transactions, asof_date=today)
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
                # If SKU selected, show SKU-specific charts; otherwise show general charts
                if self.selected_dashboard_sku:
                    self._refresh_sku_detail_charts()
                else:
                    self._refresh_general_charts(sales_records, today)
            
            # === TOP 10 TABLES ===
            
            # Top 10 by Movement (Total Sales)
            self.movement_treeview.delete(*self.movement_treeview.get_children())
            
            sales_by_sku = defaultdict(int)
            for sr in sales_records:
                sales_by_sku[sr.sku] += sr.qty_sold
            
            top_movement = sorted(sales_by_sku.items(), key=lambda x: x[1], reverse=True)[:10]
            
            for sku, total_sales in top_movement:
                self.movement_treeview.insert("", "end", values=(sku, total_sales))
            
            # Update dashboard SKU search autocomplete items
            self.dashboard_sku_items = sku_ids
        
        except Exception as e:
            logger.error(f"Dashboard refresh failed: {str(e)}", exc_info=True)
            messagebox.showerror("Errore Dashboard", f"Impossibile aggiornare dashboard: {str(e)}")
    
    def _refresh_general_charts(self, sales_records: list, today: date):
        """Refresh general dashboard charts (all SKUs)."""
        # Chart 1: Daily Sales (Last 30 Days)
        self.daily_sales_ax.clear()
        self.daily_sales_ax.set_title("Vendite Giornaliere - Tutti gli SKU (Ultimi 30 Giorni)")
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
        self.weekly_sales_ax.set_title("Confronto Vendite Settimanali - Tutti gli SKU (Ultime 8 Settimane)")
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
        self.weekly_sales_ax.set_xticks(range(len(weekly_labels)))
        self.weekly_sales_ax.set_xticklabels(weekly_labels, rotation=0)
        
        # Add moving average for weekly data
        ma_period_weekly = self.ma_weekly_var.get()
        if len(weekly_totals) >= ma_period_weekly:
            ma_values_weekly = self._calculate_moving_average(weekly_totals, ma_period_weekly)
            x_ma_weekly = np.arange(ma_period_weekly - 1, len(weekly_totals))
            self.weekly_sales_ax.plot(
                x_ma_weekly,
                ma_values_weekly,
                "r-",
                alpha=0.9,
                linewidth=2.5,
                label=f'Media Mobile {ma_period_weekly}w'
            )
            self.weekly_sales_ax.legend(loc='upper left', fontsize=9)
        
        self.dashboard_figure.tight_layout()
        self.dashboard_canvas.draw()
    
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
    
    def _filter_dashboard_sku_items(self, typed_text: str) -> list:
        """
        Filter SKU items for dashboard autocomplete.
        
        Args:
            typed_text: User input
        
        Returns:
            Filtered list of SKU IDs
        """
        if not typed_text:
            return self.dashboard_sku_items
        
        typed_lower = typed_text.lower()
        return [item for item in self.dashboard_sku_items if typed_lower in item.lower()]
    
    def _on_dashboard_sku_select(self, selected_sku: str):
        """Handle SKU selection in dashboard search."""
        if not selected_sku:
            self.selected_dashboard_sku = None
            return
        
        # Validate SKU exists
        all_skus = self.csv_layer.get_all_sku_ids()
        if selected_sku not in all_skus:
            messagebox.showwarning("SKU Non Trovato", f"SKU '{selected_sku}' non esiste.")
            self.selected_dashboard_sku = None
            return
        
        # Update selected SKU and refresh charts
        self.selected_dashboard_sku = selected_sku
        self._refresh_sku_detail_charts()
    
    def _reset_dashboard_view(self):
        """Reset dashboard to general view (all SKUs)."""
        self.selected_dashboard_sku = None
        self.dashboard_sku_var.set("")  # Clear entry field
        self._refresh_dashboard()
    
    def _refresh_sku_detail_charts(self):
        """Refresh SKU-specific detail charts (sales 30d + stock evolution 30d) using main dashboard charts."""
        if not MATPLOTLIB_AVAILABLE or not self.selected_dashboard_sku:
            return
        
        try:
            sku = self.selected_dashboard_sku
            sales_records = self.csv_layer.read_sales()
            transactions = self.csv_layer.read_transactions()
            
            today = date.today()
            days_ago_30 = today - timedelta(days=30)
            
            # --- Chart 1: Daily Sales (Last 30 Days) for selected SKU ---
            self.daily_sales_ax.clear()
            
            # Filter sales for this SKU in last 30 days
            sku_sales = [sr for sr in sales_records if sr.sku == sku and sr.date >= days_ago_30]
            
            # Group by date
            sales_by_date = defaultdict(int)
            for sr in sku_sales:
                sales_by_date[sr.date] += sr.qty_sold
            
            # Create date range (last 30 days)
            date_range = [days_ago_30 + timedelta(days=i) for i in range(31)]
            sales_values = [sales_by_date.get(d, 0) for d in date_range]
            
            # Plot
            self.daily_sales_ax.bar(
                range(len(date_range)),
                sales_values,
                color='steelblue',
                alpha=0.7,
                label='Vendite'
            )
            
            # Moving average
            ma_period = self.ma_daily_var.get()
            ma_values = self._calculate_moving_average(sales_values, ma_period)
            if ma_values:
                ma_x = range(ma_period - 1, len(date_range))
                self.daily_sales_ax.plot(
                    ma_x,
                    ma_values,
                    color='red',
                    linewidth=2,
                    label=f'MA {ma_period}d'
                )
            
            self.daily_sales_ax.set_title(f"Vendite {sku} (Ultimi 30 Giorni)")
            self.daily_sales_ax.set_xlabel("Data")
            self.daily_sales_ax.set_ylabel("Quantità Venduta")
            self.daily_sales_ax.legend(loc='upper left')
            self.daily_sales_ax.grid(True, alpha=0.3)
            
            # X-axis labels (every 5 days)
            xtick_positions = range(0, len(date_range), 5)
            xtick_labels = [date_range[i].strftime('%d/%m') for i in xtick_positions]
            self.daily_sales_ax.set_xticks(xtick_positions)
            self.daily_sales_ax.set_xticklabels(xtick_labels, rotation=45, ha='right')
            
            # --- Chart 2: Waste Events (Last 30 Days) for selected SKU ---
            self.weekly_sales_ax.clear()
            
            # Filter WASTE transactions for this SKU in last 30 days
            sku_waste = [
                txn for txn in transactions 
                if txn.sku == sku and txn.event == EventType.WASTE and txn.date >= days_ago_30
            ]
            
            # Group by date
            waste_by_date = defaultdict(int)
            for txn in sku_waste:
                waste_by_date[txn.date] += txn.qty
            
            # Create values for date range (same as sales chart)
            waste_values = [waste_by_date.get(d, 0) for d in date_range]
            
            # Plot with bar chart
            self.weekly_sales_ax.bar(
                range(len(date_range)),
                waste_values,
                color='#D32F2F',  # Red color for waste
                alpha=0.7,
                label='Scarti'
            )
            
            self.weekly_sales_ax.set_title(f"Scarti {sku} (Ultimi 30 Giorni)")
            self.weekly_sales_ax.set_xlabel("Data")
            self.weekly_sales_ax.set_ylabel("Quantità Scartata")
            self.weekly_sales_ax.legend(loc='upper left')
            self.weekly_sales_ax.grid(True, alpha=0.3)
            
            # X-axis labels (every 5 days)
            self.weekly_sales_ax.set_xticks(xtick_positions)
            self.weekly_sales_ax.set_xticklabels(xtick_labels, rotation=45, ha='right')
            
            # Redraw canvas
            self.dashboard_figure.tight_layout()
            self.dashboard_canvas.draw()
        
        except Exception as e:
            messagebox.showerror("Errore Grafici SKU", f"Impossibile aggiornare grafici: {str(e)}")
    
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
        
        # Read default values from settings
        settings = self.csv_layer.read_settings()
        engine = settings.get("reorder_engine", {})
        
        # Parameters row
        params_row = ttk.Frame(param_frame)
        params_row.pack(side="top", fill="x", pady=5)
        
        ttk.Label(params_row, text="Lead Time (giorni):", width=15).pack(side="left", padx=(0, 5))
        self.lead_time_var = tk.StringVar(value=str(engine.get("lead_time_days", {}).get("value", 7)))
        ttk.Entry(params_row, textvariable=self.lead_time_var, width=10).pack(side="left", padx=(0, 20))
        
        # Buttons row with workflow guidance
        buttons_row = ttk.Frame(param_frame)
        buttons_row.pack(side="top", fill="x", pady=5)
        
        ttk.Button(buttons_row, text="1️⃣ Genera Tutte le Proposte", command=self._generate_all_proposals).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="🔄 Aggiorna Dati Stock", command=self._refresh_order_stock_data).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="✗ Cancella Proposte", command=self._clear_proposals).pack(side="left", padx=5)
        
        # === PROPOSALS TABLE (EDITABLE) ===
        # Create horizontal split: table on left, details sidebar on right
        split_frame = ttk.Frame(main_frame)
        split_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Left side: proposals table
        proposal_frame = ttk.LabelFrame(split_frame, text="Proposte Ordine (Doppio click su Colli Proposti per modificare)", padding=5)
        proposal_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(proposal_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.proposal_treeview = ttk.Treeview(
            proposal_frame,
            columns=("SKU", "Description", "Pack Size", "Colli Proposti", "Pezzi Proposti", "Receipt Date"),
            height=10,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.proposal_treeview.yview)
        
        self.proposal_treeview.column("#0", width=0, stretch=tk.NO)
        self.proposal_treeview.column("SKU", anchor=tk.W, width=100)
        self.proposal_treeview.column("Description", anchor=tk.W, width=250)
        self.proposal_treeview.column("Pack Size", anchor=tk.CENTER, width=80)
        self.proposal_treeview.column("Colli Proposti", anchor=tk.CENTER, width=120)
        self.proposal_treeview.column("Pezzi Proposti", anchor=tk.CENTER, width=120)
        self.proposal_treeview.column("Receipt Date", anchor=tk.CENTER, width=120)
        
        self.proposal_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.proposal_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.proposal_treeview.heading("Pack Size", text="Pz/Collo", anchor=tk.CENTER)
        self.proposal_treeview.heading("Colli Proposti", text="Colli Proposti", anchor=tk.CENTER)
        self.proposal_treeview.heading("Pezzi Proposti", text="Pezzi Totali", anchor=tk.CENTER)
        self.proposal_treeview.heading("Receipt Date", text="Data Ricevimento", anchor=tk.CENTER)
        
        self.proposal_treeview.pack(fill="both", expand=True)
        
        # Bind double-click to edit and single-click to show details
        self.proposal_treeview.bind("<Double-1>", self._on_proposal_double_click)
        self.proposal_treeview.bind("<<TreeviewSelect>>", self._on_proposal_select)
        
        # Right side: details sidebar
        details_frame = ttk.LabelFrame(split_frame, text="Dettagli Calcolo", padding=10)
        details_frame.pack(side="right", fill="both", padx=(5, 0))
        details_frame.config(width=350)
        
        # Details text widget with scrollbar (read-only)
        details_text_frame = ttk.Frame(details_frame)
        details_text_frame.pack(fill="both", expand=True)
        
        details_scrollbar = ttk.Scrollbar(details_text_frame)
        details_scrollbar.pack(side="right", fill="y")
        
        self.proposal_details_text = tk.Text(details_text_frame, wrap="word", width=40, height=20, state="disabled", font=("Courier", 9), yscrollcommand=details_scrollbar.set)
        self.proposal_details_text.pack(side="left", fill="both", expand=True)
        
        details_scrollbar.config(command=self.proposal_details_text.yview)
        
        # === CONFIRMATION SECTION ===
        confirm_frame = ttk.LabelFrame(main_frame, text="2️⃣ Conferma Ordini", padding=10)
        confirm_frame.pack(side="bottom", fill="x", pady=(10, 0))
        
        info_row = ttk.Frame(confirm_frame)
        info_row.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(info_row, text="Verifica le proposte sopra (doppio click per modificare), poi conferma gli ordini con Colli > 0.", font=("Helvetica", 9)).pack(side="left")
        
        buttons_row = ttk.Frame(confirm_frame)
        buttons_row.pack(side="top", fill="x")
        
        ttk.Button(buttons_row, text="✓ Conferma Tutti gli Ordini (Colli > 0)", command=self._confirm_orders).pack(side="left", padx=5)
    
    def _on_proposal_select(self, event):
        """Handle selection of proposal to show calculation details."""
        selected = self.proposal_treeview.selection()
        if not selected:
            # Clear details if no selection
            self.proposal_details_text.config(state="normal")
            self.proposal_details_text.delete("1.0", tk.END)
            self.proposal_details_text.config(state="disabled")
            return
        
        item = self.proposal_treeview.item(selected[0])
        values = item["values"]
        sku = values[0]
        
        # Find proposal
        proposal = next((p for p in self.current_proposals if p.sku == sku), None)
        if not proposal:
            return
        
        # Get SKU object for pack_size
        sku_obj = next((s for s in self.csv_layer.read_skus() if s.sku == proposal.sku), None)
        pack_size = sku_obj.pack_size if sku_obj else 1
        
        # Format details in colli where applicable
        def to_colli(pezzi, pack):
            """Convert pezzi to colli (rounded down) with remainder."""
            if pack <= 1:
                return f"{pezzi} pz"
            colli = pezzi // pack
            remainder = pezzi % pack
            if remainder > 0:
                return f"{colli} colli + {remainder} pz"
            return f"{colli} colli"
        
        # Build details text
        details = []
        details.append(f"SKU: {proposal.sku}")
        details.append(f"Descrizione: {proposal.description}")
        details.append("")
        
        # Forecast
        details.append("═══ FORECAST ═══")
        details.append(f"Periodo: {proposal.forecast_period_days} giorni")
        details.append(f"  (Lead Time: {sku_obj.lead_time_days if sku_obj else 0}d + Review: {sku_obj.review_period if sku_obj else 0}d)")
        details.append(f"Media vendite giornaliere: {proposal.daily_sales_avg:.2f} pz/gg")
        details.append(f"Forecast qty: {to_colli(proposal.forecast_qty, pack_size)}")
        details.append("")
        
        # Lead Time Demand
        details.append("═══ LEAD TIME DEMAND ═══")
        details.append(f"Lead Time: {sku_obj.lead_time_days if sku_obj else 0} giorni")
        details.append(f"Domanda in Lead Time: {to_colli(proposal.lead_time_demand, pack_size)}")
        details.append("")
        
        # Safety Stock
        details.append("═══ SAFETY STOCK ═══")
        details.append(f"Safety Stock: {to_colli(proposal.safety_stock, pack_size)}")
        details.append("")
        
        # Target S
        details.append("═══ TARGET S ═══")
        details.append(f"S = Forecast + Safety")
        details.append(f"S = {to_colli(proposal.target_S, pack_size)}")
        details.append("")
        
        # Inventory Position
        details.append("═══ INVENTORY POSITION (IP) ═══")
        details.append(f"On Hand: {to_colli(proposal.current_on_hand, pack_size)}")
        details.append(f"On Order: {to_colli(proposal.current_on_order, pack_size)}")
        if proposal.unfulfilled_qty > 0:
            details.append(f"Unfulfilled (backorder): {to_colli(proposal.unfulfilled_qty, pack_size)}")
        details.append(f"IP = on_hand + on_order - unfulfilled")
        details.append(f"IP = {to_colli(proposal.inventory_position, pack_size)}")
        details.append("")
        
        # Proposed Qty
        details.append("═══ QTY PROPOSTA ═══")
        details.append(f"Qty grezza (S - IP):")
        details.append(f"  {to_colli(proposal.proposed_qty_before_rounding, pack_size)}")
        details.append("")
        
        # Rounding
        details.append("═══ ARROTONDAMENTI ═══")
        details.append(f"Pack Size: {pack_size} pz/collo")
        details.append(f"MOQ: {proposal.moq} pz")
        if proposal.proposed_qty != proposal.proposed_qty_before_rounding:
            details.append(f"Dopo arrotondamento:")
            details.append(f"  {to_colli(proposal.proposed_qty, pack_size)}")
        else:
            details.append("Nessun arrotondamento necessario")
        details.append("")
        
        # Caps
        details.append("═══ CAP (MAX/SHELF-LIFE) ═══")
        details.append(f"Max Stock: {to_colli(proposal.max_stock, pack_size)}")
        if proposal.shelf_life_days > 0:
            shelf_capacity = int(proposal.daily_sales_avg * proposal.shelf_life_days)
            details.append(f"Shelf Life: {proposal.shelf_life_days} giorni")
            details.append(f"Capacità Shelf Life: {to_colli(shelf_capacity, pack_size)}")
            if proposal.shelf_life_warning:
                details.append("⚠️ WARNING: S > Capacità Shelf Life")
        else:
            details.append("Shelf Life: Non impostata")
        
        if proposal.capped_by_max_stock:
            details.append("✓ Cap applicato: Max Stock raggiunto")
        else:
            details.append("Cap non applicato")
        details.append("")
        
        # Projected Stock at Receipt
        details.append("═══ PROIEZIONE A RICEVIMENTO ═══")
        if proposal.receipt_date:
            details.append(f"Data prevista ricevimento: {proposal.receipt_date.isoformat()}")
            details.append(f"Stock previsto al ricevimento:")
            details.append(f"  {to_colli(proposal.projected_stock_at_receipt, pack_size)}")
            details.append(f"  (on_hand + qty ordinata, solo eventi ledger)")
        else:
            details.append("Nessuna data ricevimento disponibile")
        details.append("")
        
        # OOS Boost
        if proposal.oos_days_count > 0:
            details.append("═══ OOS BOOST ═══")
            details.append(f"Giorni OOS rilevati: {proposal.oos_days_count}")
            if proposal.oos_boost_applied:
                boost_pct = int(proposal.oos_boost_percent * 100)
                details.append(f"✓ Boost applicato: +{boost_pct}%")
            else:
                details.append("Boost rifiutato dall'utente")
            details.append("")
        
        # Simulation details (for intermittent demand)
        if proposal.simulation_used:
            details.append("═══ SIMULAZIONE DOMANDA INTERMITTENTE ═══")
            details.append(f"Rilevata domanda bassa (<{pack_size/2.5:.2f} pz/gg)")
            details.append(f"Usata simulazione giornaliera invece di formula lineare")
            if proposal.simulation_trigger_day >= 0:
                details.append(f"Trigger: IP scenderebbe sotto 1 collo al giorno {proposal.simulation_trigger_day}")
            details.append(f"Note: {proposal.simulation_notes}")
            details.append("")
        
        # Final motivation
        details.append("═══ MOTIVAZIONE FINALE ═══")
        if proposal.proposed_qty == 0:
            if proposal.inventory_position >= proposal.target_S:
                details.append("Stock sufficiente (IP ≥ S)")
            else:
                details.append("Qty = 0 (cap o constraints)")
        else:
            details.append(f"Ordinare {to_colli(proposal.proposed_qty, pack_size)}")
            if proposal.simulation_used:
                details.append(f"(determinato via simulazione giornaliera)")
            else:
                details.append(f"per raggiungere target S")
        
        if proposal.notes:
            details.append("")
            details.append("Note:")
            details.append(proposal.notes)
        
        # Update text widget
        self.proposal_details_text.config(state="normal")
        self.proposal_details_text.delete("1.0", tk.END)
        self.proposal_details_text.insert("1.0", "\n".join(details))
        self.proposal_details_text.config(state="disabled")
    
    def _generate_all_proposals(self):
        """Generate order proposals for all SKUs using settings or user input."""
        try:
            # Read settings for defaults
            settings = self.csv_layer.read_settings()
            engine = settings.get("reorder_engine", {})
            
            # Use user input if provided, otherwise use settings defaults
            lead_time = int(self.lead_time_var.get()) if self.lead_time_var.get() else engine.get("lead_time_days", {}).get("value", 7)
            
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
        
        # Read OOS boost default from settings
        oos_boost_default = settings.get("reorder_engine", {}).get("oos_boost_percent", {}).get("value", 20) / 100.0
        oos_lookback_days = settings.get("reorder_engine", {}).get("oos_lookback_days", {}).get("value", 30)
        oos_detection_mode_global = settings.get("reorder_engine", {}).get("oos_detection_mode", {}).get("value", "strict")
        
        # Track SKU-specific OOS boost preferences (in memory for this session)
        if not hasattr(self, 'oos_boost_preferences'):
            self.oos_boost_preferences = {}  # {sku: boost_percent or None}
        
        for sku_id in sku_ids:
            stock = stocks[sku_id]
            sku_obj = skus_by_id.get(sku_id)
            description = sku_obj.description if sku_obj else "N/A"
            
            # Determine OOS detection mode: use SKU-specific if set, otherwise global
            oos_detection_mode = sku_obj.oos_detection_mode if (sku_obj and sku_obj.oos_detection_mode) else oos_detection_mode_global
            
            # Calculate daily sales average (with OOS exclusion) - NOW RETURNS TUPLE
            daily_sales, oos_days_count = calculate_daily_sales_average(
                sales_records, sku_id, 
                days_lookback=oos_lookback_days, 
                transactions=transactions, 
                asof_date=date.today(),
                oos_detection_mode=oos_detection_mode
            )
            
            # Determine OOS boost for this SKU
            oos_boost_percent = 0.0
            if oos_days_count > 0:
                # Check if user has already decided for this SKU in this session
                if sku_id in self.oos_boost_preferences:
                    # User already decided (could be None for "no", or a percent for "yes"/"yes, always")
                    oos_boost_percent = self.oos_boost_preferences[sku_id] or 0.0
                else:
                    # Ask user via dialog
                    boost_choice = self._ask_oos_boost(sku_id, description, oos_days_count, oos_boost_default)
                    if boost_choice == "yes":
                        oos_boost_percent = oos_boost_default
                        self.oos_boost_preferences[sku_id] = oos_boost_default
                    elif boost_choice == "yes_always":
                        oos_boost_percent = oos_boost_default
                        self.oos_boost_preferences[sku_id] = oos_boost_default  # Store for session
                    else:  # "no"
                        oos_boost_percent = 0.0
                        self.oos_boost_preferences[sku_id] = None
            
            # Generate proposal (pass sku_obj for pack_size, MOQ, lead_time, review_period, safety_stock, max_stock)
            proposal = self.order_workflow.generate_proposal(
                sku=sku_id,
                description=description,
                current_stock=stock,
                daily_sales_avg=daily_sales,
                sku_obj=sku_obj,
                oos_days_count=oos_days_count,
                oos_boost_percent=oos_boost_percent,
            )
            self.current_proposals.append(proposal)
        
        # Populate table
        self._refresh_proposal_table()
        
        messagebox.showinfo(
            "Proposte Generate",
            f"Generate {len(self.current_proposals)} proposte ordine.\nProposte con Q.tà > 0: {sum(1 for p in self.current_proposals if p.proposed_qty > 0)}",
        )
    
    def _ask_oos_boost(self, sku: str, description: str, oos_days_count: int, default_percent: float) -> str:
        """
        Ask user if they want to apply OOS boost for a specific SKU.
        
        Returns:
            "yes", "yes_always", or "no"
        """
        popup = tk.Toplevel(self.root)
        popup.title("OOS Boost")
        popup.geometry("500x250")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        
        result = tk.StringVar(value="no")
        
        # Content frame
        content_frame = ttk.Frame(popup, padding=20)
        content_frame.pack(fill="both", expand=True)
        
        # Warning message
        ttk.Label(
            content_frame,
            text=f"⚠️ SKU: {sku}",
            font=("Helvetica", 11, "bold"),
            foreground="#d97706",
        ).pack(anchor="w", pady=(0, 5))
        
        ttk.Label(
            content_frame,
            text=f"Descrizione: {description}",
            font=("Helvetica", 10),
        ).pack(anchor="w", pady=(0, 10))
        
        ttk.Label(
            content_frame,
            text=f"Rilevati {oos_days_count} giorni di OOS (Out-of-Stock) negli ultimi 30 giorni.",
            wraplength=450,
        ).pack(anchor="w", pady=(0, 10))
        
        ttk.Label(
            content_frame,
            text=f"Vuoi aumentare la quantità ordinata del {int(default_percent * 100)}% per compensare?",
            font=("Helvetica", 10, "bold"),
            wraplength=450,
        ).pack(anchor="w", pady=(0, 20))
        
        # Buttons
        button_frame = ttk.Frame(content_frame)
        button_frame.pack(fill="x")
        
        def choose(choice):
            result.set(choice)
            popup.destroy()
        
        ttk.Button(
            button_frame,
            text="Sì",
            command=lambda: choose("yes"),
        ).pack(side="left", padx=5)
        
        ttk.Button(
            button_frame,
            text="Sì, sempre (per questo SKU)",
            command=lambda: choose("yes_always"),
        ).pack(side="left", padx=5)
        
        ttk.Button(
            button_frame,
            text="No",
            command=lambda: choose("no"),
        ).pack(side="left", padx=5)
        
        # Wait for user choice
        popup.wait_window()
        
        return result.get()
    
    def _refresh_proposal_table(self):
        """Refresh proposals table."""
        self.proposal_treeview.delete(*self.proposal_treeview.get_children())
        
        for proposal in self.current_proposals:
            # Get SKU object for pack_size
            sku_obj = next((s for s in self.csv_layer.read_skus() if s.sku == proposal.sku), None)
            pack_size = sku_obj.pack_size if sku_obj else 1
            
            # Calculate colli from pezzi
            colli_proposti = proposal.proposed_qty // pack_size if pack_size > 0 else proposal.proposed_qty
            
            self.proposal_treeview.insert(
                "",
                "end",
                values=(
                    proposal.sku,
                    proposal.description,
                    pack_size,
                    colli_proposti,
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
        """Handle double-click on proposal row to edit Proposed Qty (in colli)."""
        selected = self.proposal_treeview.selection()
        if not selected:
            return
        
        item = self.proposal_treeview.item(selected[0])
        values = item["values"]
        sku = values[0]
        
        # Find proposal
        proposal = next((p for p in self.current_proposals if p.sku == sku), None)
        if not proposal:
            return
        
        # Create edit dialog
        self._edit_proposed_qty_dialog(proposal, selected[0])
    
    def _edit_proposed_qty_dialog(self, proposal, tree_item_id):
        """Show dialog to edit proposed quantity in colli."""
        # Get SKU object for pack_size
        sku_obj = next((s for s in self.csv_layer.read_skus() if s.sku == proposal.sku), None)
        pack_size = sku_obj.pack_size if sku_obj else 1
        
        popup = tk.Toplevel(self.root)
        popup.title(f"Modifica Colli Proposti - {proposal.sku}")
        popup.geometry("450x250")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        
        # Form frame
        form_frame = ttk.Frame(popup, padding=20)
        form_frame.pack(fill="both", expand=True)
        
        ttk.Label(form_frame, text=f"SKU: {proposal.sku}", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=5)
        ttk.Label(form_frame, text=f"Descrizione: {proposal.description}").pack(anchor="w", pady=5)
        ttk.Label(form_frame, text=f"Pezzi per Collo: {pack_size}").pack(anchor="w", pady=5)
        
        current_colli = proposal.proposed_qty // pack_size if pack_size > 0 else proposal.proposed_qty
        ttk.Label(form_frame, text=f"Colli Proposti Attuali: {current_colli} ({proposal.proposed_qty} pezzi)").pack(anchor="w", pady=5)
        
        ttk.Label(form_frame, text="Nuovi Colli da Ordinare:", font=("Helvetica", 10)).pack(anchor="w", pady=(15, 5))
        new_colli_var = tk.StringVar(value=str(current_colli))
        colli_entry = ttk.Entry(form_frame, textvariable=new_colli_var, width=20)
        colli_entry.pack(anchor="w", pady=(0, 15))
        colli_entry.focus()
        
        def save_qty():
            try:
                new_colli = int(new_colli_var.get())
                if new_colli < 0:
                    messagebox.showerror("Errore di Validazione", "I colli devono essere >= 0.", parent=popup)
                    return
                
                # Convert colli to pezzi
                new_pezzi = new_colli * pack_size
                
                # Update proposal (store in pezzi)
                proposal.proposed_qty = new_pezzi
                
                # Update tree item (show colli)
                self.proposal_treeview.item(
                    tree_item_id,
                    values=(
                        proposal.sku,
                        proposal.description,
                        pack_size,
                        new_colli,
                        new_pezzi,
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
        colli_entry.bind("<Return>", lambda e: save_qty())
    
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
            
            logger.info(f"Order confirmation successful: {len(confirmations)} orders created")
            
        except Exception as e:
            logger.error(f"Order confirmation failed: {str(e)}", exc_info=True)
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
        ttk.Label(title_frame, text="3️⃣ Gestione Ricevimenti", font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Label(title_frame, text="(Chiudi ordini quando arriva la merce)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
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
            columns=("Order ID", "SKU", "Description", "Pack Size", "Colli Ordinati", "Colli Ricevuti", "Colli Sospesi", "Receipt Date"),
            height=6,
            yscrollcommand=pending_scroll.set,
        )
        pending_scroll.config(command=self.pending_treeview.yview)
        
        self.pending_treeview.column("#0", width=0, stretch=tk.NO)
        self.pending_treeview.column("Order ID", anchor=tk.W, width=120)
        self.pending_treeview.column("SKU", anchor=tk.W, width=80)
        self.pending_treeview.column("Description", anchor=tk.W, width=180)
        self.pending_treeview.column("Pack Size", anchor=tk.CENTER, width=80)
        self.pending_treeview.column("Colli Ordinati", anchor=tk.CENTER, width=110)
        self.pending_treeview.column("Colli Ricevuti", anchor=tk.CENTER, width=110)
        self.pending_treeview.column("Colli Sospesi", anchor=tk.CENTER, width=110)
        self.pending_treeview.column("Receipt Date", anchor=tk.CENTER, width=100)
        
        self.pending_treeview.heading("Order ID", text="ID Ordine", anchor=tk.W)
        self.pending_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.pending_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.pending_treeview.heading("Pack Size", text="Pz/Collo", anchor=tk.CENTER)
        self.pending_treeview.heading("Colli Ordinati", text="Colli Ordinati", anchor=tk.CENTER)
        self.pending_treeview.heading("Colli Ricevuti", text="Colli Ricevuti", anchor=tk.CENTER)
        self.pending_treeview.heading("Colli Sospesi", text="Colli Sospesi", anchor=tk.CENTER)
        self.pending_treeview.heading("Receipt Date", text="Data Prevista", anchor=tk.CENTER)
        
        self.pending_treeview.pack(fill="both", expand=True)
        
        # Doppio click per editare quantità ricevuta
        self.pending_treeview.bind("<Double-1>", self._on_pending_qty_double_click)
        
        # Tag per evidenziare righe modificate
        self.pending_treeview.tag_configure("edited", background="#ffffcc")
        
        # === BULK RECEIPT CONFIRMATION ===
        confirm_frame = ttk.Frame(main_frame)
        confirm_frame.pack(side="top", fill="x", pady=(0, 10))
        
        ttk.Label(confirm_frame, text="Verifica quantità nella tabella (doppio click per modificare), poi:", font=("Helvetica", 10)).pack(side="left", padx=(10, 20))
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
        """Calculate and display pending orders using ledger on_order as source of truth."""
        # Reset edits
        self.pending_qty_edits = {}
        
        # Read order logs
        order_logs = self.csv_layer.read_order_logs()
        
        # Calculate current on_order from ledger (source of truth)
        today = date.today()
        all_skus = self.csv_layer.get_all_sku_ids()
        transactions = self.csv_layer.read_transactions()
        sales = self.csv_layer.read_sales()
        
        stock_by_sku = StockCalculator.calculate_all_skus(
            all_skus=all_skus,
            asof_date=today,
            transactions=transactions,
            sales_records=sales,
        )
        
        # Get SKU descriptions
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Display pending orders using ledger on_order as source of truth
        self.pending_treeview.delete(*self.pending_treeview.get_children())
        
        # Group order logs by (SKU, receipt_date) for display purposes
        order_groups = {}  # {(sku, receipt_date): {order_ids: [], qty_ordered_total: int}}
        
        for log in order_logs:
            sku = log.get("sku")
            status = log.get("status", "PENDING")
            
            # Only show PENDING orders
            if status != "PENDING":
                continue
            
            order_id = log.get("order_id")
            qty_ordered = int(log.get("qty_ordered", 0))
            receipt_date_str = log.get("receipt_date", "")
            
            key = (sku, receipt_date_str)
            if key not in order_groups:
                order_groups[key] = {"order_ids": [], "qty_ordered_total": 0}
            
            order_groups[key]["order_ids"].append(order_id)
            order_groups[key]["qty_ordered_total"] += qty_ordered
        
        # Display rows: one per (SKU, receipt_date) with ledger on_order as pending qty
        for (sku, receipt_date_str), group_data in order_groups.items():
            description = skus_by_id.get(sku).description if sku in skus_by_id else "N/A"
            qty_ordered_total = group_data["qty_ordered_total"]
            order_ids_str = ", ".join(group_data["order_ids"])
            
            # Get current on_order from ledger (source of truth)
            stock = stock_by_sku.get(sku, Stock(sku=sku, on_hand=0, on_order=0, asof_date=today))
            ledger_on_order = stock.on_order
            
            # Only show if ledger reports on_order > 0
            if ledger_on_order > 0:
                # Estimate received: total ordered - current on_order
                qty_received_est = max(0, qty_ordered_total - ledger_on_order)
                
                self.pending_treeview.insert(
                    "",
                    "end",
                    values=(
                        order_ids_str,
                        sku,
                        description,
                        qty_ordered_total,
                        qty_received_est,
                        ledger_on_order,
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
        ttk.Label(title_frame, text="4️⃣ Gestione Eccezioni", font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Label(title_frame, text="(Scarti, correzioni, merce non consegnata)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
        # === QUICK ENTRY FORM (GRID LAYOUT) ===
        form_frame = ttk.LabelFrame(main_frame, text="Inserimento Rapido Eccezione (campi obbligatori marcati con *)", padding=15)
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
        if TKCALENDAR_AVAILABLE:
            DateEntry(
                form_frame,
                textvariable=self.exception_date_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).grid(row=1, column=3, sticky="w", pady=8)
        else:
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
        if TKCALENDAR_AVAILABLE:
            DateEntry(
                toolbar_frame,
                textvariable=self.exception_view_date_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).pack(side="left", padx=(0, 5))
        else:
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
        if TKCALENDAR_AVAILABLE:
            DateEntry(
                form_frame,
                textvariable=bulk_date_var,
                width=28,
                date_pattern="yyyy-mm-dd",
            ).pack(fill="x", pady=(0, 15))
        else:
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
        ttk.Label(title_frame, text="(Crea, modifica, elimina prodotti)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
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
        popup.geometry("600x700")
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
        
        # Pack Size field
        ttk.Label(form_frame, text="Confezione (Pack Size):", font=("Helvetica", 10, "bold")).grid(
            row=4, column=0, sticky="w", pady=5
        )
        pack_size_var = tk.StringVar(value=str(current_sku.pack_size) if current_sku else "1")
        ttk.Entry(form_frame, textvariable=pack_size_var, width=40).grid(row=4, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Lead Time field
        ttk.Label(form_frame, text="Lead Time (giorni):", font=("Helvetica", 10, "bold")).grid(
            row=5, column=0, sticky="w", pady=5
        )
        lead_time_var = tk.StringVar(value=str(current_sku.lead_time_days) if current_sku else "7")
        ttk.Entry(form_frame, textvariable=lead_time_var, width=40).grid(row=5, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Review Period field
        ttk.Label(form_frame, text="Periodo Revisione (giorni):", font=("Helvetica", 10, "bold")).grid(
            row=6, column=0, sticky="w", pady=5
        )
        review_period_var = tk.StringVar(value=str(current_sku.review_period) if current_sku else "7")
        ttk.Entry(form_frame, textvariable=review_period_var, width=40).grid(row=6, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Safety Stock field
        ttk.Label(form_frame, text="Scorta Sicurezza:", font=("Helvetica", 10, "bold")).grid(
            row=7, column=0, sticky="w", pady=5
        )
        safety_stock_var = tk.StringVar(value=str(current_sku.safety_stock) if current_sku else "0")
        ttk.Entry(form_frame, textvariable=safety_stock_var, width=40).grid(row=7, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Shelf Life field
        ttk.Label(form_frame, text="Shelf Life (giorni, 0=no scadenza):", font=("Helvetica", 10, "bold")).grid(
            row=8, column=0, sticky="w", pady=5
        )
        shelf_life_var = tk.StringVar(value=str(current_sku.shelf_life_days) if current_sku else "0")
        ttk.Entry(form_frame, textvariable=shelf_life_var, width=40).grid(row=8, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Max Stock field
        ttk.Label(form_frame, text="Stock Massimo:", font=("Helvetica", 10, "bold")).grid(
            row=9, column=0, sticky="w", pady=5
        )
        max_stock_var = tk.StringVar(value=str(current_sku.max_stock) if current_sku else "999")
        ttk.Entry(form_frame, textvariable=max_stock_var, width=40).grid(row=9, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Reorder Point field
        ttk.Label(form_frame, text="Punto di Riordino:", font=("Helvetica", 10, "bold")).grid(
            row=10, column=0, sticky="w", pady=5
        )
        reorder_point_var = tk.StringVar(value=str(current_sku.reorder_point) if current_sku else "10")
        ttk.Entry(form_frame, textvariable=reorder_point_var, width=40).grid(row=10, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Supplier field con autocomplete
        ttk.Label(form_frame, text="Fornitore:", font=("Helvetica", 10, "bold")).grid(
            row=11, column=0, sticky="w", pady=5
        )
        supplier_var = tk.StringVar(value=current_sku.supplier if current_sku else "")
        
        supplier_ac = AutocompleteEntry(
            form_frame,
            textvariable=supplier_var,
            items_callback=self._filter_supplier_items,
            width=40
        )
        supplier_ac.entry.grid(row=11, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Demand Variability field
        ttk.Label(form_frame, text="Variabilità Domanda:", font=("Helvetica", 10, "bold")).grid(
            row=12, column=0, sticky="w", pady=5
        )
        demand_var = tk.StringVar(value=current_sku.demand_variability.value if current_sku else "STABLE")
        demand_combo = ttk.Combobox(form_frame, textvariable=demand_var, values=["STABLE", "LOW", "HIGH", "SEASONAL"], state="readonly", width=37)
        demand_combo.grid(row=12, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # OOS Boost Percent field
        ttk.Label(form_frame, text="OOS Boost % (0=usa globale):", font=("Helvetica", 10, "bold")).grid(
            row=13, column=0, sticky="w", pady=5
        )
        oos_boost_var = tk.StringVar(value=str(current_sku.oos_boost_percent) if current_sku else "0")
        ttk.Entry(form_frame, textvariable=oos_boost_var, width=40).grid(row=13, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # OOS Detection Mode field
        ttk.Label(form_frame, text="Modalità OOS (\"\"=usa globale):", font=("Helvetica", 10, "bold")).grid(
            row=14, column=0, sticky="w", pady=5
        )
        oos_mode_var = tk.StringVar(value=current_sku.oos_detection_mode if current_sku else "")
        oos_mode_combo = ttk.Combobox(form_frame, textvariable=oos_mode_var, values=["", "strict", "relaxed"], state="readonly", width=37)
        oos_mode_combo.grid(row=14, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Validate EAN button and status label
        ean_status_var = tk.StringVar(value="")
        ttk.Button(
            form_frame, 
            text="Valida EAN", 
            command=lambda: self._validate_ean_field(ean_var.get(), ean_status_var)
        ).grid(row=15, column=1, sticky="w", pady=5, padx=(10, 0))
        
        ean_status_label = ttk.Label(form_frame, textvariable=ean_status_var, foreground="green")
        ean_status_label.grid(row=16, column=1, sticky="w", padx=(10, 0))
        
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
                moq_var.get(), pack_size_var.get(), lead_time_var.get(), 
                review_period_var.get(), safety_stock_var.get(), shelf_life_var.get(),
                max_stock_var.get(), reorder_point_var.get(), supplier_var.get(), 
                demand_var.get(), oos_boost_var.get(), oos_mode_var.get(), current_sku
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
                        moq_str, pack_size_str, lead_time_str, review_period_str, 
                        safety_stock_str, shelf_life_str, max_stock_str, reorder_point_str,
                        supplier, demand_variability_str, oos_boost_str, oos_mode_str, current_sku):
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
            pack_size = int(pack_size_str)
            lead_time_days = int(lead_time_str)
            review_period = int(review_period_str)
            safety_stock = int(safety_stock_str)
            shelf_life_days = int(shelf_life_str)
            max_stock = int(max_stock_str)
            reorder_point = int(reorder_point_str)
            oos_boost_percent = float(oos_boost_str)
        except ValueError:
            messagebox.showerror("Errore di Validazione", "Tutti i campi numerici devono essere numeri validi.", parent=popup)
            return
        
        # Validate positive values
        if any(v < 0 for v in [moq, pack_size, lead_time_days, review_period, safety_stock, shelf_life_days, max_stock, reorder_point]):
            messagebox.showerror("Errore di Validazione", "I valori numerici non possono essere negativi.", parent=popup)
            return
        
        if pack_size < 1:
            messagebox.showerror("Errore di Validazione", "Pack Size deve essere almeno 1.", parent=popup)
            return
        
        if oos_boost_percent < 0 or oos_boost_percent > 100:
            messagebox.showerror("Errore di Validazione", "OOS Boost deve essere tra 0 e 100.", parent=popup)
            return
        
        oos_detection_mode = (oos_mode_str or "").strip()
        if oos_detection_mode not in ["", "strict", "relaxed"]:
            messagebox.showerror("Errore di Validazione", "Modalità OOS non valida. Usa: strict, relaxed o vuoto.", parent=popup)
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
                    pack_size=pack_size,
                    lead_time_days=lead_time_days,
                    review_period=review_period,
                    safety_stock=safety_stock,
                    shelf_life_days=shelf_life_days,
                    max_stock=max_stock,
                    reorder_point=reorder_point,
                    supplier=supplier,
                    demand_variability=demand_variability,
                    oos_boost_percent=oos_boost_percent,
                    oos_detection_mode=oos_detection_mode,
                )
                self.csv_layer.write_sku(new_sku)
                
                # Log audit trail
                shelf_msg = f", Shelf Life: {shelf_life_days}d" if shelf_life_days > 0 else ""
                self.csv_layer.log_audit(
                    operation="SKU_CREATE",
                    details=f"Created SKU: {description} (Pack: {pack_size}, MOQ: {moq}, Lead: {lead_time_days}d, Review: {review_period}d, Safety: {safety_stock}{shelf_msg})",
                    sku=sku_code,
                )
                
                messagebox.showinfo("Successo", f"SKU '{sku_code}' creato con successo.", parent=popup)
            else:
                # Update existing SKU
                old_sku_code = current_sku.sku
                success = self.csv_layer.update_sku(
                    old_sku_code, sku_code, description, ean,
                    moq, pack_size, lead_time_days, review_period, 
                    safety_stock, shelf_life_days, max_stock, reorder_point,
                    supplier, demand_variability, oos_boost_percent, oos_detection_mode
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
                    if current_sku.pack_size != pack_size:
                        changes.append(f"Pack: {current_sku.pack_size} → {pack_size}")
                    if current_sku.review_period != review_period:
                        changes.append(f"Review: {current_sku.review_period}d → {review_period}d")
                    if current_sku.safety_stock != safety_stock:
                        changes.append(f"Safety: {current_sku.safety_stock} → {safety_stock}")
                    if current_sku.shelf_life_days != shelf_life_days:
                        changes.append(f"Shelf Life: {current_sku.shelf_life_days}d → {shelf_life_days}d")
                    
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

        try:
            asof_str = self.asof_date_var.get()
            selected_date = date.fromisoformat(asof_str)
        except ValueError:
            messagebox.showerror("Error", "Formato data non valido. Usa YYYY-MM-DD.")
            return
        self.asof_date = selected_date
        
        # Confirm with user
        num_entries = len(self.eod_stock_edits)
        confirm = messagebox.askyesno(
            "Conferma Chiusura",
            f"Confermare chiusura giornaliera per {num_entries} SKU?\n\n"
            f"Data: {selected_date.isoformat()}\n\n"
            "Questo calcolerà il venduto e aggiornerà stock e vendite.",
        )
        
        if not confirm:
            return
        
        # Process EOD entries
        try:
            results = self.daily_close_workflow.process_bulk_eod_stock(
                eod_entries=self.eod_stock_edits,
                eod_date=selected_date,
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
        self._refresh_settings_tab()
    
    def _build_settings_tab(self):
        """Build Settings tab for reorder engine configuration."""
        main_frame = ttk.Frame(self.settings_tab, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 20))
        ttk.Label(
            title_frame,
            text="⚙️ Impostazioni Motore di Riordino",
            font=("Helvetica", 16, "bold")
        ).pack(side="left")
        ttk.Label(title_frame, text="(Parametri globali per ordini automatici)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
        # Info label
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(
            info_frame,
            text="Configura i parametri globali del motore di riordino automatico. I parametri con 'Auto-applica' vengono applicati automaticamente ai nuovi SKU.",
            font=("Helvetica", 10),
            foreground="gray",
            wraplength=800
        ).pack(side="left")
        
        # Scrollable container for settings form
        scroll_container = ttk.Frame(main_frame)
        scroll_container.pack(fill="both", expand=True, pady=10)
        
        # Canvas with scrollbar
        canvas = tk.Canvas(scroll_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Enable mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        # Settings form inside scrollable frame
        settings_container = ttk.LabelFrame(scrollable_frame, text="Parametri Globali", padding=20)
        settings_container.pack(fill="both", expand=True)
        
        # Storage for widgets
        self.settings_widgets = {}
        
        # Parameters configuration
        parameters = [
            {
                "key": "lead_time_days",
                "label": "Lead Time (giorni)",
                "description": "Tempo di attesa dall'ordine alla ricezione",
                "type": "int",
                "min": 1,
                "max": 90
            },
            {
                "key": "moq",
                "label": "MOQ (Quantità Minima Ordine)",
                "description": "Multiplo minimo per gli ordini",
                "type": "int",
                "min": 1,
                "max": 1000
            },
            {
                "key": "pack_size",
                "label": "Pack Size (unità)",
                "description": "Multiplo di arrotondamento per colli",
                "type": "int",
                "min": 1,
                "max": 1000
            },
            {
                "key": "review_period",
                "label": "Review Period (giorni)",
                "description": "Finestra di revisione per il target S",
                "type": "int",
                "min": 1,
                "max": 90
            },
            {
                "key": "safety_stock",
                "label": "Safety Stock (unità)",
                "description": "Scorta di sicurezza aggiunta al target S",
                "type": "int",
                "min": 0,
                "max": 10000
            },
            {
                "key": "max_stock",
                "label": "Stock Massimo (unità)",
                "description": "Limite massimo di stock desiderato",
                "type": "int",
                "min": 1,
                "max": 10000
            },
            {
                "key": "reorder_point",
                "label": "Punto di Riordino (unità)",
                "description": "Livello di stock che attiva il riordino",
                "type": "int",
                "min": 0,
                "max": 1000
            },
            {
                "key": "demand_variability",
                "label": "Variabilità Domanda",
                "description": "Livello di variabilità della domanda",
                "type": "choice",
                "choices": ["STABLE", "MODERATE", "HIGH"]
            },
            {
                "key": "oos_boost_percent",
                "label": "OOS Boost (%)",
                "description": "Percentuale di incremento ordine per SKU con giorni OOS",
                "type": "int",
                "min": 0,
                "max": 100,
                "section": "reorder_engine"
            },
            {
                "key": "oos_lookback_days",
                "label": "Giorni Storico OOS",
                "description": "Numero giorni passati da analizzare per rilevare OOS (es. 30 = ultimi 30 giorni)",
                "type": "int",
                "min": 7,
                "max": 90,
                "section": "reorder_engine"
            },
            {
                "key": "oos_detection_mode",
                "label": "Modalità Rilevamento OOS",
                "description": "strict = on_hand=0 (più conservativo), relaxed = on_hand+on_order=0",
                "type": "choice",
                "choices": ["strict", "relaxed"],
                "section": "reorder_engine"
            },
            {
                "key": "stock_unit_price",
                "label": "Prezzo Unitario Stock (€)",
                "description": "Prezzo medio unitario per calcolo valore stock in Dashboard",
                "type": "int",
                "min": 1,
                "max": 10000,
                "section": "dashboard"
            }
        ]
        
        # Create form rows
        for i, param in enumerate(parameters):
            row_frame = ttk.Frame(settings_container)
            row_frame.pack(fill="x", pady=8)
            
            # Left: Label and description
            left_frame = ttk.Frame(row_frame)
            left_frame.pack(side="left", fill="x", expand=True)
            
            ttk.Label(
                left_frame,
                text=param["label"],
                font=("Helvetica", 10, "bold")
            ).pack(anchor="w")
            
            ttk.Label(
                left_frame,
                text=param["description"],
                font=("Helvetica", 9),
                foreground="gray"
            ).pack(anchor="w")
            
            # Right: Value input and checkbox
            right_frame = ttk.Frame(row_frame)
            right_frame.pack(side="right")
            
            # Value input
            if param["type"] == "int":
                value_var = tk.IntVar()
                value_entry = ttk.Spinbox(
                    right_frame,
                    from_=param["min"],
                    to=param["max"],
                    textvariable=value_var,
                    width=10
                )
                value_entry.pack(side="left", padx=5)
            elif param["type"] == "choice":
                value_var = tk.StringVar()
                value_entry = ttk.Combobox(
                    right_frame,
                    textvariable=value_var,
                    values=param["choices"],
                    state="readonly",
                    width=12
                )
                value_entry.pack(side="left", padx=5)
            
            # Auto-apply checkbox
            auto_apply_var = tk.BooleanVar()
            auto_apply_check = ttk.Checkbutton(
                right_frame,
                text="Auto-applica ai nuovi SKU",
                variable=auto_apply_var
            )
            auto_apply_check.pack(side="left", padx=10)
            
            # Store widgets
            self.settings_widgets[param["key"]] = {
                "value_var": value_var,
                "auto_apply_var": auto_apply_var
            }
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(side="bottom", fill="x", pady=10)
        
        ttk.Button(
            button_frame,
            text="💾 Salva Impostazioni",
            command=self._save_settings
        ).pack(side="left", padx=5)
        
        ttk.Button(
            button_frame,
            text="↺ Ripristina Default",
            command=self._reset_settings_to_default
        ).pack(side="left", padx=5)
        
        ttk.Button(
            button_frame,
            text="🔄 Ricarica",
            command=self._refresh_settings_tab
        ).pack(side="left", padx=5)
        
        # Load current settings
        self._refresh_settings_tab()
    
    def _refresh_settings_tab(self):
        """Refresh settings tab with current values."""
        try:
            settings = self.csv_layer.read_settings()
            
            # Read parameters list to determine section
            parameters = [
                {"key": "lead_time_days", "section": "reorder_engine"},
                {"key": "moq", "section": "reorder_engine"},
                {"key": "pack_size", "section": "reorder_engine"},
                {"key": "review_period", "section": "reorder_engine"},
                {"key": "safety_stock", "section": "reorder_engine"},
                {"key": "max_stock", "section": "reorder_engine"},
                {"key": "reorder_point", "section": "reorder_engine"},
                {"key": "demand_variability", "section": "reorder_engine"},
                {"key": "oos_boost_percent", "section": "reorder_engine"},
                {"key": "stock_unit_price", "section": "dashboard"},
            ]
            
            param_sections = {p["key"]: p.get("section", "reorder_engine") for p in parameters}
            
            for param_key, widgets in self.settings_widgets.items():
                section = param_sections.get(param_key, "reorder_engine")
                param_config = settings.get(section, {}).get(param_key, {})
                widgets["value_var"].set(param_config.get("value", 0))
                widgets["auto_apply_var"].set(param_config.get("auto_apply_to_new_sku", True))
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile caricare impostazioni: {str(e)}")
    
    def _save_settings(self):
        """Save settings to JSON file."""
        try:
            settings = self.csv_layer.read_settings()
            
            # Parameters with sections
            parameters = [
                {"key": "lead_time_days", "section": "reorder_engine"},
                {"key": "moq", "section": "reorder_engine"},
                {"key": "pack_size", "section": "reorder_engine"},
                {"key": "review_period", "section": "reorder_engine"},
                {"key": "safety_stock", "section": "reorder_engine"},
                {"key": "max_stock", "section": "reorder_engine"},
                {"key": "reorder_point", "section": "reorder_engine"},
                {"key": "demand_variability", "section": "reorder_engine"},
                {"key": "oos_boost_percent", "section": "reorder_engine"},
                {"key": "stock_unit_price", "section": "dashboard"},
            ]
            
            param_sections = {p["key"]: p.get("section", "reorder_engine") for p in parameters}
            
            # Update settings by section
            for param_key, widgets in self.settings_widgets.items():
                section = param_sections.get(param_key, "reorder_engine")
                
                # Ensure section exists
                if section not in settings:
                    settings[section] = {}
                
                settings[section][param_key] = {
                    "value": widgets["value_var"].get(),
                    "auto_apply_to_new_sku": widgets["auto_apply_var"].get()
                }
            
            # Write to file
            self.csv_layer.write_settings(settings)
            
            # Update OrderWorkflow lead_time if changed
            lead_time = settings["reorder_engine"]["lead_time_days"]["value"]
            self.order_workflow = OrderWorkflow(self.csv_layer, lead_time_days=lead_time)
            
            messagebox.showinfo("Successo", "Impostazioni salvate correttamente!")
            
            # Log operation
            self.csv_layer.log_audit(
                operation="SETTINGS_UPDATE",
                details="Reorder engine settings updated",
                sku=None
            )
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile salvare impostazioni: {str(e)}")
    
    def _reset_settings_to_default(self):
        """Reset all settings to default values."""
        confirm = messagebox.askyesno(
            "Conferma Ripristino",
            "Ripristinare tutte le impostazioni ai valori predefiniti?\n\nQuesta operazione non può essere annullata."
        )
        
        if not confirm:
            return
        
        try:
            # Default settings
            default_settings = {
                "reorder_engine": {
                    "lead_time_days": {"value": 7, "auto_apply_to_new_sku": True},
                    "min_stock": {"value": 10, "auto_apply_to_new_sku": True},
                    "days_cover": {"value": 14, "auto_apply_to_new_sku": True},
                    "moq": {"value": 1, "auto_apply_to_new_sku": True},
                    "max_stock": {"value": 999, "auto_apply_to_new_sku": True},
                    "reorder_point": {"value": 10, "auto_apply_to_new_sku": True},
                    "demand_variability": {"value": "STABLE", "auto_apply_to_new_sku": True}
                }
            }
            
            self.csv_layer.write_settings(default_settings)
            self._refresh_settings_tab()
            
            messagebox.showinfo("Successo", "Impostazioni ripristinate ai valori predefiniti.")
            
            # Log operation
            self.csv_layer.log_audit(
                operation="SETTINGS_RESET",
                details="Settings reset to default values",
                sku=None
            )
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile ripristinare impostazioni: {str(e)}")
    
    def _on_tab_press(self, event):
        """Handle mouse press on tab for drag-and-drop reordering."""
        # Check if click is on a tab
        try:
            clicked = self.notebook.tk.call(self.notebook._w, "identify", "tab", event.x, event.y)
            if clicked != "":
                self.drag_tab_index = int(clicked)
                self.drag_start_x = event.x
            else:
                self.drag_tab_index = None
                self.drag_start_x = None
        except:
            self.drag_tab_index = None
            self.drag_start_x = None
    
    def _on_tab_drag(self, event):
        """Handle mouse drag on tab."""
        if self.drag_tab_index is None or self.drag_start_x is None:
            return
        
        # Check if dragged far enough (minimum 30 pixels)
        if abs(event.x - self.drag_start_x) < 30:
            return
        
        # Find which tab position the mouse is over
        try:
            target = self.notebook.tk.call(self.notebook._w, "identify", "tab", event.x, event.y)
            if target != "":
                target_index = int(target)
                
                # Only move if different position
                if target_index != self.drag_tab_index:
                    # Get tab info before moving
                    tab_id = self.notebook.tabs()[self.drag_tab_index]
                    tab_text = self.notebook.tab(tab_id, "text")
                    
                    # Remove and reinsert at new position
                    self.notebook.forget(self.drag_tab_index)
                    self.notebook.insert(target_index, tab_id, text=tab_text)
                    
                    # Update drag index to new position
                    self.drag_tab_index = target_index
                    self.drag_start_x = event.x
        except:
            pass
    
    def _on_tab_release(self, event):
        """Handle mouse release after tab drag."""
        # Save new tab order if tabs were reordered
        if self.drag_tab_index is not None:
            self._save_tab_order()
        
        self.drag_tab_index = None
        self.drag_start_x = None
    
    def _load_tab_order(self):
        """
        Load saved tab order from settings.
        
        Returns:
            List of tab IDs in saved order (or default order if not saved)
        """
        default_order = ["stock", "order", "receiving", "exception", "dashboard", "admin", "settings"]
        
        try:
            settings = self.csv_layer.read_settings()
            if "ui" in settings and "tab_order" in settings["ui"]:
                saved_order = settings["ui"]["tab_order"]
                # Validate saved order (must contain all tab IDs)
                if set(saved_order) == set(default_order) and len(saved_order) == len(default_order):
                    return saved_order
        except:
            pass
        
        return default_order
    
    def _save_tab_order(self):
        """Save current tab order to settings."""
        try:
            # Get current tab order from notebook
            current_order = []
            for tab_id in self.notebook.tabs():
                tab_text = self.notebook.tab(tab_id, "text")
                # Find which tab this is
                for tid, tname in self.tab_map.items():
                    if tname == tab_text:
                        current_order.append(tid)
                        break
            
            # Load current settings
            settings = self.csv_layer.read_settings()
            
            # Ensure "ui" section exists
            if "ui" not in settings:
                settings["ui"] = {}
            
            # Update tab order
            settings["ui"]["tab_order"] = current_order
            
            # Save settings
            self.csv_layer.write_settings(settings)
            
            # Update instance variable
            self.tab_order = current_order
            
            logger.info(f"Tab order saved: {current_order}")
            
        except Exception as e:
            logger.error(f"Failed to save tab order: {str(e)}", exc_info=True)


def main():
    """Entry point for GUI."""
    root = tk.Tk()
    app = DesktopOrderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
