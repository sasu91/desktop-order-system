"""
Main GUI application for desktop-order-system.

Tkinter-based desktop UI with multiple tabs.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from typing import cast, Literal
from datetime import date, timedelta, datetime
from pathlib import Path
import tempfile
import os
import csv
from collections import defaultdict
import logging

try:
    from tkcalendar import DateEntry  # type: ignore[import-untyped]
    TKCALENDAR_AVAILABLE = True
except ImportError:
    DateEntry = None
    TKCALENDAR_AVAILABLE = False

try:
    import matplotlib  # type: ignore[import-not-found]
    matplotlib.use('TkAgg')
    from matplotlib.figure import Figure  # type: ignore[import-not-found]
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # type: ignore[import-not-found]
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]
    import numpy as np
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Warning: matplotlib not installed. Dashboard charts disabled.")

try:
    from PIL import Image, ImageTk  # type: ignore[import-untyped]
    import barcode  # type: ignore[import-untyped]
    from barcode.writer import ImageWriter  # type: ignore[import-untyped]
    BARCODE_AVAILABLE = True
except ImportError:
    BARCODE_AVAILABLE = False
    print("Warning: python-barcode or Pillow not installed. Barcode rendering disabled.")

from ..persistence.storage_adapter import StorageAdapter
from ..domain.ledger import StockCalculator, validate_ean
from ..domain.models import SKU, EventType, OrderProposal, Stock, Transaction, PromoWindow
from ..domain.promo_uplift import estimate_uplift, UpliftReport
from ..workflows.order import OrderWorkflow, calculate_daily_sales_average
from ..workflows.receiving import ExceptionWorkflow
from ..workflows.receiving_v2 import ReceivingWorkflow
from ..workflows.daily_close import DailyCloseWorkflow
from .. import promo_calendar
from .widgets import AutocompleteEntry
from .collapsible_frame import CollapsibleFrame
from ..utils.logging_config import setup_logging, get_logger

# Initialize logging — uses frozen-aware path (next to .exe or project root/logs)
setup_logging(app_name="desktop_order_system")
logger = get_logger()


class DesktopOrderApp:
    """Main application window."""
    
    def __init__(self, root: tk.Tk, data_dir: Path | None = None):
        """
        Initialize the application.
        
        Args:
            root: Tkinter root window
            data_dir: Data directory for CSV files (defaults to ./data)
        """
        self.root = root
        self.root.title("Desktop Order System")
        self.root.geometry("1100x680")
        self.root.minsize(700, 520)  # Prevents hiding of paned sections
        
        try:
            # Initialize storage layer (StorageAdapter - transparent CSV/SQLite routing)
            self.csv_layer = StorageAdapter(data_dir=data_dir)
            
            # Load expiry thresholds from settings
            settings = self.csv_layer.read_settings()
            self.expiry_critical_days = settings.get("expiry_alerts", {}).get("critical_threshold_days", {}).get("value", 7)
            self.expiry_warning_days = settings.get("expiry_alerts", {}).get("warning_threshold_days", {}).get("value", 14)
            
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
        
        # Settings modification tracking
        self.settings_modified = False
        self._previous_tab_index = 0  # Track previous tab for detecting tab changes
        
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
        
        # Import submenu
        file_menu.add_command(label="Importa SKU da CSV...", command=self._import_sku_from_csv)
        file_menu.add_separator()
        
        # Export submenu
        export_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Esporta in CSV", menu=export_menu)
        export_menu.add_command(label="Snapshot Stock (Data AsOf)", command=self._export_stock_snapshot)
        export_menu.add_command(label="Registro (Transazioni)", command=self._export_ledger)
        export_menu.add_command(label="Elenco SKU", command=self._export_sku_list)
        export_menu.add_command(label="Log Ordini", command=self._export_order_logs)
        export_menu.add_command(label="Log Ricevimenti", command=self._export_receiving_logs)
        export_menu.add_separator()
        export_menu.add_command(label="📊 Ordini + KPI + Breakdown", command=self._export_order_kpi_breakdown)
        export_menu.add_command(label="🔍 Order Explain (Audit Trail)", command=self._export_order_explain)
        
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
        
        # Bind event for tab change (to check unsaved settings)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        
        # Create tabs
        self.dashboard_tab = ttk.Frame(self.notebook)
        self.stock_tab = ttk.Frame(self.notebook)
        self.order_tab = ttk.Frame(self.notebook)
        self.receiving_tab = ttk.Frame(self.notebook)
        self.exception_tab = ttk.Frame(self.notebook)
        self.expiry_tab = ttk.Frame(self.notebook)  # NEW: Expiry tracking tab
        self.promo_tab = ttk.Frame(self.notebook)  # NEW: Promo calendar tab
        self.event_uplift_tab = ttk.Frame(self.notebook)  # NEW: Event uplift tab
        self.admin_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        
        # Map tab IDs to frame objects
        self.tab_frames = {
            "stock": self.stock_tab,
            "order": self.order_tab,
            "receiving": self.receiving_tab,
            "exception": self.exception_tab,
            "expiry": self.expiry_tab,  # NEW
            "promo": self.promo_tab,  # NEW
            "event_uplift": self.event_uplift_tab,  # NEW
            "dashboard": self.dashboard_tab,
            "admin": self.admin_tab,
            "settings": self.settings_tab
        }
        
        # Update tab_map with expiry and promo tabs
        self.tab_map = {
            "dashboard": "📊 Dashboard",
            "stock": "📦 Stock",
            "order": "🛒 Ordini",
            "receiving": "📥 Ricevimento",
            "exception": "⚠️ Eccezioni",
            "expiry": "⏰ Scadenze",  # NEW
            "promo": "📅 Calendario Promo",  # NEW
            "event_uplift": "📈 Eventi/Uplift",  # NEW
            "admin": "🔧 Admin",
            "settings": "⚙️ Impostazioni"
        }
        
        # Add tabs in saved order (or default order if not saved)
        for tab_id in self.tab_order:
            tab_frame = self.tab_frames.get(tab_id)
            if tab_frame is not None:
                tab_text = self.tab_map[tab_id]
                self.notebook.add(tab_frame, text=tab_text)
        
        # Build tab contents
        self._build_dashboard_tab()
        self._build_stock_tab()
        self._build_order_tab()
        self._build_receiving_tab()
        self._build_exception_tab()
        self._build_expiry_tab()  # NEW
        self._build_promo_tab()  # NEW
        self._build_event_uplift_tab()  # NEW
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
            DateEntry(  # type: ignore[misc]
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
        
        # === REORDER KPI ANALYSIS ===
        reorder_kpi_frame = ttk.LabelFrame(main_frame, text="Analisi KPI Riordino", padding=10)
        reorder_kpi_frame.pack(side="top", fill="both", expand=False, pady=(0, 10))
        
        # Controls row
        controls_row = ttk.Frame(reorder_kpi_frame)
        controls_row.pack(side="top", fill="x", pady=(0, 10))
        
        # Lookback days
        ttk.Label(controls_row, text="Periodo Analisi (giorni):").pack(side="left", padx=(0, 5))
        self.kpi_lookback_var = tk.IntVar(value=30)
        ttk.Spinbox(
            controls_row,
            from_=7,
            to=365,
            textvariable=self.kpi_lookback_var,
            width=8
        ).pack(side="left", padx=(0, 15))
        
        # OOS detection mode
        ttk.Label(controls_row, text="Modalità OOS:").pack(side="left", padx=(0, 5))
        self.kpi_mode_var = tk.StringVar(value="strict")
        mode_combo = ttk.Combobox(
            controls_row,
            textvariable=self.kpi_mode_var,
            values=["strict", "relaxed"],
            state="readonly",
            width=10
        )
        mode_combo.pack(side="left", padx=(0, 15))
        
        # Calculate button
        ttk.Button(
            controls_row,
            text="📊 Calcola KPI",
            command=self._calculate_kpi_all_skus,
            style="Accent.TButton"
        ).pack(side="left", padx=(0, 15))
        
        # Refresh from cache button
        ttk.Button(
            controls_row,
            text="🔄 Aggiorna da Cache",
            command=self._refresh_kpi_from_cache
        ).pack(side="left", padx=(0, 5))
        
        # Info label
        ttk.Label(
            controls_row,
            text="📌 Tip: Calcola KPI aggiorna la cache, Aggiorna da Cache legge i valori salvati",
            font=("Helvetica", 8, "italic"),
            foreground="gray"
        ).pack(side="left", padx=(10, 0))
        
        # KPI Table
        kpi_table_frame = ttk.Frame(reorder_kpi_frame)
        kpi_table_frame.pack(side="top", fill="both", expand=True)
        
        kpi_scroll_y = ttk.Scrollbar(kpi_table_frame, orient="vertical")
        kpi_scroll_y.pack(side="right", fill="y")
        
        kpi_scroll_x = ttk.Scrollbar(kpi_table_frame, orient="horizontal")
        kpi_scroll_x.pack(side="bottom", fill="x")
        
        self.kpi_treeview = ttk.Treeview(
            kpi_table_frame,
            columns=("SKU", "OOS_Rate", "Lost_Sales", "WMAPE", "Bias", "Fill_Rate", "OTIF", "Delay"),
            height=8,
            yscrollcommand=kpi_scroll_y.set,
            xscrollcommand=kpi_scroll_x.set,
            show="headings"
        )
        kpi_scroll_y.config(command=self.kpi_treeview.yview)
        kpi_scroll_x.config(command=self.kpi_treeview.xview)
        
        # Column configuration
        self.kpi_treeview.column("SKU", anchor=tk.W, width=120)
        self.kpi_treeview.column("OOS_Rate", anchor=tk.CENTER, width=100)
        self.kpi_treeview.column("Lost_Sales", anchor=tk.CENTER, width=100)
        self.kpi_treeview.column("WMAPE", anchor=tk.CENTER, width=90)
        self.kpi_treeview.column("Bias", anchor=tk.CENTER, width=90)
        self.kpi_treeview.column("Fill_Rate", anchor=tk.CENTER, width=100)
        self.kpi_treeview.column("OTIF", anchor=tk.CENTER, width=90)
        self.kpi_treeview.column("Delay", anchor=tk.CENTER, width=110)
        
        # Headings
        self.kpi_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.kpi_treeview.heading("OOS_Rate", text="OOS Rate %", anchor=tk.CENTER)
        self.kpi_treeview.heading("Lost_Sales", text="Lost Sales Est.", anchor=tk.CENTER)
        self.kpi_treeview.heading("WMAPE", text="WMAPE %", anchor=tk.CENTER)
        self.kpi_treeview.heading("Bias", text="Bias", anchor=tk.CENTER)
        self.kpi_treeview.heading("Fill_Rate", text="Fill Rate %", anchor=tk.CENTER)
        self.kpi_treeview.heading("OTIF", text="OTIF %", anchor=tk.CENTER)
        self.kpi_treeview.heading("Delay", text="Avg Delay (days)", anchor=tk.CENTER)
        
        self.kpi_treeview.pack(fill="both", expand=True)
        
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
        colors = plt.cm.viridis(np.linspace(0.3, 0.9, len(weekly_totals)))  # type: ignore[attr-defined]
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
    
    def _calculate_kpi_all_skus(self):
        """Calculate reorder KPIs for all SKUs and write to cache."""
        try:
            from ..analytics.kpi import (
                compute_oos_kpi,
                estimate_lost_sales,
                compute_forecast_accuracy,
                compute_supplier_proxy_kpi,
            )
            
            # Get parameters
            lookback_days = self.kpi_lookback_var.get()
            mode = self.kpi_mode_var.get()
            today = date.today()
            
            # Get all SKUs
            sku_ids = self.csv_layer.get_all_sku_ids()
            
            if not sku_ids:
                messagebox.showinfo("Info", "Nessun SKU disponibile per l'analisi KPI.")
                return
            
            # Show progress (simplified - could add a progress bar later)
            logger.info(f"Calculating KPIs for {len(sku_ids)} SKUs...")
            
            # Calculate KPIs for each SKU
            kpi_snapshots = []
            
            for sku in sku_ids:
                try:
                    # Compute all KPIs
                    oos_result = compute_oos_kpi(sku, lookback_days, mode, self.csv_layer, today)
                    lost_sales_result = estimate_lost_sales(sku, lookback_days, mode, self.csv_layer, today, method="forecast")
                    accuracy_result = compute_forecast_accuracy(sku, lookback_days, mode, self.csv_layer, today)
                    supplier_result = compute_supplier_proxy_kpi(sku, lookback_days, self.csv_layer, today)
                    
                    # Build snapshot
                    snapshot = {
                        "sku": sku,
                        "date": today.isoformat(),
                        "oos_rate": oos_result.get("oos_rate"),
                        "lost_sales_est": lost_sales_result.get("lost_units_est"),
                        "wmape": accuracy_result.get("wmape"),
                        "bias": accuracy_result.get("bias"),
                        "fill_rate": supplier_result.get("fill_rate"),
                        "otif_rate": supplier_result.get("otif_rate"),
                        "avg_delay_days": supplier_result.get("avg_delay_days"),
                        "n_periods": oos_result.get("n_periods"),
                        "lookback_days": lookback_days,
                        "mode": mode,
                    }
                    
                    kpi_snapshots.append(snapshot)
                
                except Exception as e:
                    logger.warning(f"KPI calculation failed for SKU {sku}: {str(e)}")
                    continue
            
            # Write to cache
            self.csv_layer.write_kpi_daily_batch(kpi_snapshots)
            
            logger.info(f"KPI calculation complete. {len(kpi_snapshots)} SKUs processed.")
            
            # Refresh display from cache
            self._refresh_kpi_from_cache()
            
            messagebox.showinfo("Success", f"KPI calcolati per {len(kpi_snapshots)} SKU.\nRisultati salvati in kpi_daily.csv")
        
        except Exception as e:
            logger.error(f"KPI calculation failed: {str(e)}", exc_info=True)
            messagebox.showerror("Errore", f"Calcolo KPI fallito: {str(e)}")
    
    def _refresh_kpi_from_cache(self):
        """Refresh KPI table from cached data."""
        try:
            # Get parameters
            lookback_days = self.kpi_lookback_var.get()
            mode = self.kpi_mode_var.get()
            
            # Read cached KPIs
            cached_kpis = self.csv_layer.read_kpi_daily(lookback_days=lookback_days)
            
            # Filter by mode
            cached_kpis = [k for k in cached_kpis if k.get("mode") == mode]
            
            # Clear existing table
            self.kpi_treeview.delete(*self.kpi_treeview.get_children())
            
            if not cached_kpis:
                logger.info("No cached KPI data found. Use 'Calcola KPI' to generate.")
                return
            
            # Sort by SKU
            cached_kpis.sort(key=lambda k: k.get("sku", ""))
            
            # Populate table
            for kpi in cached_kpis:
                sku = kpi.get("sku", "")
                
                # Format values
                oos_rate = kpi.get("oos_rate", "")
                if oos_rate and oos_rate != "":
                    try:
                        oos_rate = f"{float(oos_rate) * 100:.1f}%"
                    except (ValueError, TypeError):
                        oos_rate = "n/a"
                else:
                    oos_rate = "n/a"
                
                lost_sales = kpi.get("lost_sales_est", "")
                if lost_sales and lost_sales != "":
                    try:
                        lost_sales = f"{float(lost_sales):.1f}"
                    except (ValueError, TypeError):
                        lost_sales = "n/a"
                else:
                    lost_sales = "n/a"
                
                wmape = kpi.get("wmape", "")
                if wmape and wmape != "":
                    try:
                        wmape = f"{float(wmape):.1f}%"
                    except (ValueError, TypeError):
                        wmape = "n/a"
                else:
                    wmape = "n/a"
                
                bias = kpi.get("bias", "")
                if bias and bias != "":
                    try:
                        bias = f"{float(bias):.2f}"
                    except (ValueError, TypeError):
                        bias = "n/a"
                else:
                    bias = "n/a"
                
                fill_rate = kpi.get("fill_rate", "")
                if fill_rate and fill_rate != "":
                    try:
                        fill_rate = f"{float(fill_rate) * 100:.1f}%"
                    except (ValueError, TypeError):
                        fill_rate = "n/a"
                else:
                    fill_rate = "n/a"
                
                otif = kpi.get("otif_rate", "")
                if otif and otif != "":
                    try:
                        otif = f"{float(otif) * 100:.1f}%"
                    except (ValueError, TypeError):
                        otif = "n/a"
                else:
                    otif = "n/a"
                
                delay = kpi.get("avg_delay_days", "")
                if delay and delay != "":
                    try:
                        delay = f"{float(delay):.1f}"
                    except (ValueError, TypeError):
                        delay = "n/a"
                else:
                    delay = "n/a"
                
                # Insert row
                self.kpi_treeview.insert("", "end", values=(
                    sku, oos_rate, lost_sales, wmape, bias, fill_rate, otif, delay
                ))
            
            logger.info(f"KPI table refreshed with {len(cached_kpis)} entries.")
        
        except Exception as e:
            logger.error(f"KPI refresh from cache failed: {str(e)}", exc_info=True)
            messagebox.showerror("Errore", f"Aggiornamento KPI fallito: {str(e)}")
    
    def _run_closed_loop_analysis(self):
        """Execute closed-loop analysis and display results."""
        from analytics.closed_loop import run_closed_loop
        from datetime import datetime
        
        try:
            # Check if enabled
            settings = self.csv_layer.read_settings()
            cl_enabled = settings.get("closed_loop", {}).get("enabled", {}).get("value", False)
            action_mode = settings.get("closed_loop", {}).get("action_mode", {}).get("value", "suggest")
            
            if not cl_enabled:
                result = messagebox.askyesno(
                    "Closed-Loop Disabilitato",
                    "Il sistema closed-loop è disabilitato nelle impostazioni.\n\n"
                    "Vuoi eseguire l'analisi comunque (solo report, nessuna modifica)?"
                )
                if not result:
                    return
            
            # Confirm if action_mode is "apply"
            if action_mode == "apply" and cl_enabled:
                result = messagebox.askyesno(
                    "Conferma Applicazione Automatica",
                    "⚠️ ATTENZIONE: Modalità 'apply' attiva!\n\n"
                    "Le modifiche suggerite verranno APPLICATE AUTOMATICAMENTE ai parametri SKU.\n\n"
                    "Vuoi procedere con l'analisi e l'applicazione automatica?",
                    icon="warning"
                )
                if not result:
                    return
            
            # Run analysis
            asof_date = datetime.now()
            
            logger.info(f"Running closed-loop analysis asof {asof_date.strftime('%Y-%m-%d')}")
            report = run_closed_loop(self.csv_layer, asof_date)
            
            # Update treeview with results
            self._refresh_closed_loop_results(report)
            
            # Show summary
            summary_msg = (
                f"Analisi Closed-Loop Completata\n\n"
                f"Data Analisi: {report.asof_date}\n"
                f"Modalità: {report.action_mode}\n"
                f"Abilitato: {'Sì' if report.enabled else 'No'}\n\n"
                f"SKU Processati: {report.skus_processed}\n"
                f"SKU con Proposte: {report.skus_with_changes}\n"
                f"SKU Bloccati (WMAPE alto): {report.skus_blocked}\n"
            )
            
            if report.action_mode == "apply" and report.enabled:
                summary_msg += f"SKU Modificati: {report.skus_applied}\n"
            
            messagebox.showinfo("Successo", summary_msg)
            
            logger.info(f"Closed-loop analysis completed: {report.skus_processed} SKUs, {report.skus_with_changes} changes, {report.skus_blocked} blocked")
        
        except Exception as e:
            logger.exception("Error running closed-loop analysis")
            messagebox.showerror("Errore", f"Errore nell'analisi closed-loop: {str(e)}")
    
    def _refresh_closed_loop_results(self, report):
        """Refresh closed-loop results treeview with report data."""
        # Clear existing rows
        for item in self.closed_loop_treeview.get_children():
            self.closed_loop_treeview.delete(item)
        
        # Populate with decisions
        for decision in report.decisions:
            # Format values
            csl_current = f"{decision.current_csl:.3f}"
            csl_suggested = f"{decision.suggested_csl:.3f}"
            delta = f"{decision.delta_csl:+.3f}" if decision.delta_csl != 0 else "0.000"
            
            oos = f"{decision.oos_rate * 100:.1f}%" if decision.oos_rate is not None else "n/a"
            wmape = f"{decision.wmape * 100:.1f}%" if decision.wmape is not None else "n/a"
            waste = f"{decision.waste_rate * 100:.1f}%" if decision.waste_rate is not None else "n/a"
            
            # Translate action
            action_map = {
                "increase": "▲ Aumenta",
                "decrease": "▼ Riduci",
                "hold": "● Hold",
                "blocked": "✖ Bloccato"
            }
            action_text = action_map.get(decision.action, decision.action)
            
            # Get tag for color
            tag = decision.action
            
            # Insert row
            self.closed_loop_treeview.insert(
                "", "end",
                values=(
                    decision.sku,
                    csl_current,
                    csl_suggested,
                    delta,
                    action_text,
                    decision.reason,
                    oos,
                    wmape,
                    waste
                ),
                tags=(tag,)
            )
        
        logger.info(f"Closed-loop results refreshed with {len(report.decisions)} decisions")
    
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
        title_frame.pack(side="top", fill="x", pady=(0, 5))
        ttk.Label(title_frame, text="Gestione Ordini", font=("Helvetica", 14, "bold")).pack(side="left")

        # === OUTER PANED WINDOW (horizontal): left (params+table) | right (Dettagli Calcolo) ===
        self.order_outer_paned = ttk.PanedWindow(main_frame, orient="horizontal")
        self.order_outer_paned.pack(fill="both", expand=True)

        # Left pane: vertical PanedWindow — top (params/controls) | bottom (proposals table)
        self.order_left_paned = ttk.PanedWindow(self.order_outer_paned, orient="vertical")
        self.order_outer_paned.add(self.order_left_paned, weight=3)

        # === PARAMETERS & PROPOSAL GENERATION ===
        param_frame = ttk.LabelFrame(self.order_left_paned, text="Genera Proposte Ordine", padding=10)
        
        # Read default values from settings
        settings = self.csv_layer.read_settings()
        engine = settings.get("reorder_engine", {})

        # ── Row 1: Data Ordine ───────────────────────────────────────────────
        row_order = ttk.Frame(param_frame)
        row_order.pack(side="top", fill="x", pady=2)
        ttk.Label(row_order, text="Data Ordine:", width=22).pack(side="left", padx=(0, 5))
        self.order_date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(row_order, textvariable=self.order_date_var, width=12).pack(side="left", padx=(0, 5))
        ttk.Label(row_order, text="(YYYY-MM-DD, default = oggi)", font=("Helvetica", 8), foreground="gray").pack(side="left")

        # ── Row 2: Corsia Logistica ──────────────────────────────────────────
        row_lane = ttk.Frame(param_frame)
        row_lane.pack(side="top", fill="x", pady=2)
        ttk.Label(row_lane, text="Corsia Logistica:", width=22).pack(side="left", padx=(0, 5))
        self.lane_var = tk.StringVar(value="STANDARD")
        self.lane_combo = ttk.Combobox(row_lane, textvariable=self.lane_var, width=12, state="readonly")
        self.lane_combo['values'] = ("STANDARD", "SATURDAY", "MONDAY")
        self.lane_combo.pack(side="left", padx=(0, 5))
        ttk.Label(row_lane, text="(Lun-Gio → STANDARD  |  Ven → SATURDAY o MONDAY)", font=("Helvetica", 8), foreground="gray").pack(side="left")

        # ── Row 3: Override Data Ricevimento ─────────────────────────────────
        row_override = ttk.Frame(param_frame)
        row_override.pack(side="top", fill="x", pady=2)
        self.force_receipt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row_override, text="Forza Data Ricevimento:", variable=self.force_receipt_var, width=22).pack(side="left", padx=(0, 5))
        self.override_receipt_var = tk.StringVar(value="")
        self.override_receipt_entry = ttk.Entry(row_override, textvariable=self.override_receipt_var, width=12, state="disabled")
        self.override_receipt_entry.pack(side="left", padx=(0, 5))
        ttk.Label(row_override, text="(YYYY-MM-DD)", font=("Helvetica", 8), foreground="gray").pack(side="left")

        # ── Row 4: Motivo override (enabled only when force_receipt active) ──
        row_reason = ttk.Frame(param_frame)
        row_reason.pack(side="top", fill="x", pady=2)
        ttk.Label(row_reason, text="Motivo Override:", width=22).pack(side="left", padx=(0, 5))
        self.override_reason_var = tk.StringVar(value="")
        self.override_reason_combo = ttk.Combobox(row_reason, textvariable=self.override_reason_var, width=30, state="disabled")
        self.override_reason_combo['values'] = (
            "Chiusura fornitore", "Festività", "Variazione logistica",
            "Richiesta cliente", "Altro",
        )
        self.override_reason_combo.pack(side="left", padx=(0, 5))

        # ── Info section: read-only calendar preview ─────────────────────────
        info_frame = ttk.LabelFrame(param_frame, text="Pianificazione Calcolata (solo lettura)", padding=5)
        info_frame.pack(side="top", fill="x", pady=(6, 2))
        info_grid = ttk.Frame(info_frame)
        info_grid.pack(fill="x")
        ttk.Label(info_grid, text="Ricevimento calcolato:", foreground="gray", width=22).grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.info_receipt_lbl = ttk.Label(info_grid, text="—", foreground="#0066cc", font=("Helvetica", 9, "bold"))
        self.info_receipt_lbl.grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Label(info_grid, text="Lead time effettivo:", foreground="gray", width=20).grid(row=0, column=2, sticky="w", padx=(0, 5))
        self.info_lt_lbl = ttk.Label(info_grid, text="—", foreground="#0066cc", font=("Helvetica", 9, "bold"))
        self.info_lt_lbl.grid(row=0, column=3, sticky="w")
        ttk.Label(info_grid, text="P (giorni copertura):", foreground="gray", width=22).grid(row=1, column=0, sticky="w", padx=(0, 5))
        self.info_p_lbl = ttk.Label(info_grid, text="—", foreground="#0066cc", font=("Helvetica", 9, "bold"))
        self.info_p_lbl.grid(row=1, column=1, sticky="w", padx=(0, 20))
        ttk.Label(info_grid, text="Corsia attiva:", foreground="gray", width=20).grid(row=1, column=2, sticky="w", padx=(0, 5))
        self.info_lane_lbl = ttk.Label(info_grid, text="—", foreground="#0066cc", font=("Helvetica", 9, "bold"))
        self.info_lane_lbl.grid(row=1, column=3, sticky="w")

        # Wire auto-update callbacks
        self.order_date_var.trace_add("write", self._update_order_info)
        self.lane_var.trace_add("write", self._update_order_info)
        self.force_receipt_var.trace_add("write", self._update_order_info)
        self.override_receipt_var.trace_add("write", self._update_order_info)
        # Trigger initial info display after widgets are created
        param_frame.after(50, self._update_order_info)

        # Buttons row with workflow guidance
        buttons_row = ttk.Frame(param_frame)
        buttons_row.pack(side="top", fill="x", pady=5)
        
        ttk.Button(buttons_row, text="1️⃣ Genera Tutte le Proposte", command=self._generate_all_proposals).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="🔄 Aggiorna Dati Stock", command=self._refresh_order_stock_data).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="✗ Cancella Proposte", command=self._clear_proposals).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="✓ Conferma Tutti gli Ordini (Colli > 0)", command=self._confirm_orders).pack(side="left", padx=(15, 5))

        # Register param_frame as top pane of left vertical split
        self.order_left_paned.add(param_frame, weight=0)

        # === PROPOSALS TABLE (EDITABLE) ===
        proposal_frame = ttk.LabelFrame(self.order_left_paned, text="Proposte Ordine (Doppio click su Colli Proposti per modificare)", padding=5)
        self.order_left_paned.add(proposal_frame, weight=1)
        
        # Scrollbars (vertical + horizontal)
        scrollbar_y = ttk.Scrollbar(proposal_frame, orient="vertical")
        scrollbar_y.pack(side="right", fill="y")
        scrollbar_x = ttk.Scrollbar(proposal_frame, orient="horizontal")
        scrollbar_x.pack(side="bottom", fill="x")

        self.proposal_treeview = ttk.Treeview(
            proposal_frame,
            columns=("SKU", "Description", "Pack Size", "Usable Stock", "Waste Risk %", "Colli Proposti", "Pezzi Proposti", "Shelf Penalty", "MC Comparison", "Promo Δ", "Event Uplift", "Receipt Date"),
            height=10,
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set,
        )
        scrollbar_y.config(command=self.proposal_treeview.yview)
        scrollbar_x.config(command=self.proposal_treeview.xview)
        
        self.proposal_treeview.column("#0", width=0, stretch=tk.NO)
        self.proposal_treeview.column("SKU", anchor=tk.W, width=100)
        self.proposal_treeview.column("Description", anchor=tk.W, width=200)
        self.proposal_treeview.column("Pack Size", anchor=tk.CENTER, width=70)
        self.proposal_treeview.column("Usable Stock", anchor=tk.CENTER, width=90)
        self.proposal_treeview.column("Waste Risk %", anchor=tk.CENTER, width=90)
        self.proposal_treeview.column("Colli Proposti", anchor=tk.CENTER, width=100)
        self.proposal_treeview.column("Pezzi Proposti", anchor=tk.CENTER, width=100)
        self.proposal_treeview.column("Shelf Penalty", anchor=tk.CENTER, width=90)
        self.proposal_treeview.column("MC Comparison", anchor=tk.CENTER, width=110)
        self.proposal_treeview.column("Promo Δ", anchor=tk.CENTER, width=90)
        self.proposal_treeview.column("Event Uplift", anchor=tk.CENTER, width=90)
        self.proposal_treeview.column("Receipt Date", anchor=tk.CENTER, width=120)
        
        self.proposal_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.proposal_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.proposal_treeview.heading("Pack Size", text="Pz/Collo", anchor=tk.CENTER)
        self.proposal_treeview.heading("Usable Stock", text="Stock Usabile", anchor=tk.CENTER)
        self.proposal_treeview.heading("Waste Risk %", text="Rischio ♻️", anchor=tk.CENTER)
        self.proposal_treeview.heading("Colli Proposti", text="Colli Proposti", anchor=tk.CENTER)
        self.proposal_treeview.heading("Pezzi Proposti", text="Pezzi Totali", anchor=tk.CENTER)
        self.proposal_treeview.heading("Shelf Penalty", text="Penalità ⚠️", anchor=tk.CENTER)
        self.proposal_treeview.heading("MC Comparison", text="📊 MC Info", anchor=tk.CENTER)
        self.proposal_treeview.heading("Promo Δ", text="� Promo", anchor=tk.CENTER)
        self.proposal_treeview.heading("Event Uplift", text="Event", anchor=tk.CENTER)
        self.proposal_treeview.heading("Receipt Date", text="Data Ricevimento", anchor=tk.CENTER)
        
        # Configure tag for low-history SKUs (<=7 valid days)
        self.proposal_treeview.tag_configure("low_history", background="#fff3cd", foreground="#856404")
        
        self.proposal_treeview.pack(fill="both", expand=True)
        
        # Bind double-click to edit and single-click to show details
        self.proposal_treeview.bind("<Double-1>", self._on_proposal_double_click)
        self.proposal_treeview.bind("<<TreeviewSelect>>", self._on_proposal_select)
        
        # ── RIGHT SIDE: Dettagli Calcolo — full-height right pane of outer PanedWindow ──
        details_outer = ttk.LabelFrame(self.order_outer_paned, text="Dettagli Calcolo", padding=5)
        self.order_outer_paned.add(details_outer, weight=1)
        details_outer.config(width=370)

        # Set initial sash position after layout is realized
        def _set_order_sash():
            total_w = self.order_outer_paned.winfo_width()
            if total_w > 10:
                self.order_outer_paned.sashpos(0, max(total_w - 390, 350))
        self.order_outer_paned.after(150, _set_order_sash)

        # Responsive: keep right panel visible when window is resized
        def _on_order_paned_configure(event=None):
            total_w = self.order_outer_paned.winfo_width()
            if total_w < 10:
                return
            try:
                sash = self.order_outer_paned.sashpos(0)
                # Ensure both panes always have at least their minsize visible
                if total_w - sash < 220:
                    self.order_outer_paned.sashpos(0, total_w - 220)
                if sash < 320:
                    self.order_outer_paned.sashpos(0, 320)
            except Exception:
                pass
        self.order_outer_paned.bind("<Configure>", _on_order_paned_configure)

        # Scrollable canvas container
        _details_canvas = tk.Canvas(details_outer, highlightthickness=0)
        _details_vscroll = ttk.Scrollbar(details_outer, orient="vertical", command=_details_canvas.yview)
        _details_canvas.configure(yscrollcommand=_details_vscroll.set)
        _details_vscroll.pack(side="right", fill="y")
        _details_canvas.pack(side="left", fill="both", expand=True)

        details_scroll_frame = ttk.Frame(_details_canvas)
        _dcw = _details_canvas.create_window((0, 0), window=details_scroll_frame, anchor="nw")

        def _on_dsf_configure(e):
            _details_canvas.configure(scrollregion=_details_canvas.bbox("all"))
        def _on_dc_configure(e):
            _details_canvas.itemconfig(_dcw, width=e.width)
        details_scroll_frame.bind("<Configure>", _on_dsf_configure)
        _details_canvas.bind("<Configure>", _on_dc_configure)

        # Mouse-wheel scroll on the panel
        def _on_detail_wheel(event):
            _details_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        _details_canvas.bind("<MouseWheel>", _on_detail_wheel)
        details_scroll_frame.bind("<MouseWheel>", _on_detail_wheel)

        # ── Section 1 : SKU Header Card ───────────────────────────────────
        sku_card = ttk.LabelFrame(details_scroll_frame, text="SKU Selezionato", padding=8)
        sku_card.pack(fill="x", padx=5, pady=(5, 3))

        self.detail_sku_lbl = ttk.Label(sku_card, text="—", font=("Helvetica", 12, "bold"))
        self.detail_sku_lbl.pack(anchor="w")
        self.detail_desc_lbl = ttk.Label(sku_card, text="—", font=("Helvetica", 9), foreground="gray")
        self.detail_desc_lbl.pack(anchor="w")

        stat_row = ttk.Frame(sku_card)
        stat_row.pack(fill="x", pady=(8, 0))

        rp_box = tk.Frame(stat_row, bg="#dbeafe", relief="flat", bd=1)
        rp_box.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Label(rp_box, text="REORDER POINT", bg="#dbeafe", fg="#1e40af",
                 font=("Helvetica", 7, "bold")).pack(anchor="w", padx=5, pady=(4, 0))
        self.detail_rp_lbl = tk.Label(rp_box, text="— pz", bg="#dbeafe", fg="#1e3a8a",
                                      font=("Helvetica", 11, "bold"))
        self.detail_rp_lbl.pack(anchor="w", padx=5, pady=(0, 4))

        sa_box = tk.Frame(stat_row, bg="#d1fae5", relief="flat", bd=1)
        sa_box.pack(side="left", fill="x", expand=True, padx=(4, 0))
        tk.Label(sa_box, text="STOCK ATTUALE", bg="#d1fae5", fg="#065f46",
                 font=("Helvetica", 7, "bold")).pack(anchor="w", padx=5, pady=(4, 0))
        self.detail_stock_lbl = tk.Label(sa_box, text="— pz", bg="#d1fae5", fg="#064e3b",
                                         font=("Helvetica", 11, "bold"))
        self.detail_stock_lbl.pack(anchor="w", padx=5, pady=(0, 4))

        # ── Section 2 : Stock Projection Chart ───────────────────────────
        chart_lf = ttk.LabelFrame(details_scroll_frame, text="📈 Stock Projection", padding=5)
        chart_lf.pack(fill="x", padx=5, pady=3)

        if MATPLOTLIB_AVAILABLE:
            self.detail_fig = Figure(figsize=(3.5, 2.0), dpi=80)
            self.detail_fig.patch.set_facecolor("#f8fafc")  # type: ignore[union-attr]
            self.detail_ax = self.detail_fig.add_subplot(111)
            self.detail_chart_canvas = FigureCanvasTkAgg(self.detail_fig, master=chart_lf)
            self.detail_chart_canvas.get_tk_widget().pack(fill="x")
            self._draw_projection_chart(None)
        else:
            self.detail_chart_canvas = None
            self.detail_fig = None
            self.detail_ax = None
            ttk.Label(chart_lf, text="(matplotlib non disponibile)",
                      foreground="gray", font=("Helvetica", 8)).pack(pady=8)

        legend_row = ttk.Frame(chart_lf)
        legend_row.pack(fill="x", pady=(2, 0))
        for dot_color, dot_label in [("#94a3b8", "Storico"), ("#3b82f6", "Proj. Stock"), ("#f87171", "Safety")]:
            _dc = tk.Canvas(legend_row, width=10, height=10, highlightthickness=0)
            _dc.create_oval(1, 1, 9, 9, fill=dot_color, outline="")
            _dc.pack(side="left", padx=(4, 1))
            ttk.Label(legend_row, text=dot_label, font=("Helvetica", 7)).pack(side="left", padx=(0, 6))

        # ── Section 3 : Demand Simulation ────────────────────────────────
        sim_lf = ttk.LabelFrame(details_scroll_frame, text="🔬 Demand Simulation", padding=8)
        sim_lf.pack(fill="x", padx=5, pady=3)

        ttk.Label(sim_lf, text="Method Comparison", font=("Helvetica", 8),
                  foreground="gray").pack(anchor="w", pady=(0, 4))

        sma_row = ttk.Frame(sim_lf)
        sma_row.pack(fill="x")
        ttk.Label(sma_row, text="Simple Moving Avg", font=("Helvetica", 8, "bold"),
                  width=18).pack(side="left")
        self.detail_sma_val_lbl = ttk.Label(sma_row, text="—", font=("Helvetica", 8),
                                            foreground="gray")
        self.detail_sma_val_lbl.pack(side="right")
        self.detail_sma_bar = ttk.Progressbar(sim_lf, orient="horizontal",
                                              length=300, maximum=100, value=0)
        self.detail_sma_bar.pack(fill="x", pady=(2, 6))

        mc_row = ttk.Frame(sim_lf)
        mc_row.pack(fill="x")
        ttk.Label(mc_row, text="Monte Carlo Sim", font=("Helvetica", 8, "bold"),
                  width=18).pack(side="left")
        self.detail_mc_val_lbl = ttk.Label(mc_row, text="—", font=("Helvetica", 8),
                                           foreground="#3b82f6")
        self.detail_mc_val_lbl.pack(side="right")
        self.detail_mc_bar = ttk.Progressbar(sim_lf, orient="horizontal",
                                             length=300, maximum=100, value=0)
        self.detail_mc_bar.pack(fill="x", pady=(2, 4))

        self.detail_sim_note_lbl = ttk.Label(sim_lf, text="", font=("Helvetica", 7, "italic"),
                                             foreground="gray", wraplength=310)
        self.detail_sim_note_lbl.pack(anchor="w")

        # ── Section 4 : Vincoli Attivi (shown only when constraints present) ─
        self.detail_vincoli_outer = ttk.LabelFrame(details_scroll_frame,
                                                   text="⚠️ Vincoli Attivi", padding=5)
        self.detail_vincoli_inner = tk.Frame(self.detail_vincoli_outer, bg="#fef3c7",
                                             relief="flat")
        self.detail_vincoli_inner.pack(fill="x", padx=2, pady=2)
        self.detail_vincoli_title_lbl = tk.Label(
            self.detail_vincoli_inner, text="", bg="#fef3c7", fg="#92400e",
            font=("Helvetica", 8, "bold"), anchor="w", justify="left", wraplength=300)
        self.detail_vincoli_title_lbl.pack(fill="x", padx=5, pady=(4, 0))
        self.detail_vincoli_msg_lbl = tk.Label(
            self.detail_vincoli_inner, text="", bg="#fef3c7", fg="#78350f",
            font=("Helvetica", 8), anchor="w", justify="left", wraplength=300)
        self.detail_vincoli_msg_lbl.pack(fill="x", padx=5, pady=(0, 4))
        # (pack/forget driven by _update_detail_panel)

        # ── Section 5 : Dettagli Completi (collapsible raw text) ─────────
        self.detail_collapsible = CollapsibleFrame(details_scroll_frame,
                                                   title="Dettagli Completi", expanded=False)
        self.detail_collapsible.pack(fill="x", padx=5, pady=3)

        _raw_content = self.detail_collapsible.get_content_frame()
        _raw_tf = ttk.Frame(_raw_content)
        _raw_tf.pack(fill="both", expand=True)
        _raw_sb = ttk.Scrollbar(_raw_tf)
        _raw_sb.pack(side="right", fill="y")
        self.proposal_details_text = tk.Text(
            _raw_tf, wrap="word", width=36, height=15,
            state="disabled", font=("Courier", 8),
            yscrollcommand=_raw_sb.set)
        self.proposal_details_text.pack(side="left", fill="both", expand=True)
        _raw_sb.config(command=self.proposal_details_text.yview)
        
    # ── Detail panel helpers ────────────────────────────────────────────────

    def _draw_projection_chart(self, proposal):
        """Redraw the stock projection chart in the detail sidebar."""
        if not MATPLOTLIB_AVAILABLE or self.detail_chart_canvas is None:
            return
        ax = self.detail_ax
        fig = self.detail_fig
        if ax is None or fig is None:
            return
        ax.clear()
        ax.set_facecolor("#f8fafc")

        if proposal is None:
            ax.text(0.5, 0.5, "Seleziona uno SKU",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=8, color="#94a3b8")
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
            fig.tight_layout(pad=0.3)
            self.detail_chart_canvas.draw()
            return

        today = date.today()
        daily_avg = max(proposal.daily_sales_avg, 0.01)
        receipt_date = proposal.receipt_date
        proposed_qty = proposal.proposed_qty

        # Past 7-day synthetic history (stock was higher by daily_avg * days_ago)
        past_days = 7
        past_x = list(range(-past_days, 1))
        past_stock = [proposal.current_on_hand + daily_avg * abs(d) for d in past_x]

        # Future: project day-by-day until receipt_date + 5 extra days
        receipt_offset = (receipt_date - today).days if receipt_date else 14
        future_end = max(receipt_offset + 5, 10)
        future_x: list[int] = []
        future_stock: list[float] = []
        for d in range(0, future_end + 1):
            if d == 0:
                s = float(proposal.current_on_hand)
            else:
                s = future_stock[-1] - daily_avg
                if receipt_date and d == receipt_offset and proposed_qty > 0:
                    s += proposed_qty
                s = max(s, 0.0)
            future_x.append(d)
            future_stock.append(s)

        # Plot history (dashed grey) and projection (solid blue)
        ax.plot(past_x, past_stock, color="#94a3b8", linewidth=1.5,
                linestyle="--", zorder=2)
        ax.plot(future_x, future_stock, color="#3b82f6", linewidth=2, zorder=3)

        # Today marker
        ax.axvline(x=0, color="#1e293b", linewidth=0.8, linestyle=":", alpha=0.6, zorder=4)

        # Receipt date marker
        if receipt_date and 0 < receipt_offset <= future_end:
            ax.axvline(x=receipt_offset, color="#10b981", linewidth=0.8,
                       linestyle=":", alpha=0.7, zorder=4)

        # Safety stock
        safety = proposal.safety_stock
        all_xmin = past_x[0]
        ax.axhline(y=safety, color="#f87171", linewidth=1.0, linestyle="--",
                   alpha=0.85, zorder=2)

        # Max stock cap
        if proposal.max_stock > 0:
            ax.axhline(y=proposal.max_stock, color="#f59e0b", linewidth=0.8,
                       linestyle=":", alpha=0.5, zorder=2)

        # Axis ticks
        x_ticks = [past_x[0], 0]
        x_labels = [f"{past_x[0]}d", "Oggi"]
        if receipt_date and 0 < receipt_offset <= future_end:
            x_ticks.append(receipt_offset)
            x_labels.append("Ric.")
        ax.set_xticks(x_ticks)
        ax.set_xticklabels(x_labels, fontsize=6)
        ax.tick_params(axis="y", labelsize=6)
        ax.tick_params(length=0)

        # Clean spines
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#e2e8f0")
        ax.spines["bottom"].set_color("#e2e8f0")

        # "Safety" label on the line
        try:
            ylim = ax.get_ylim()
            if ylim[1] > ylim[0]:
                ax.text(all_xmin, safety, " Safety", fontsize=5,
                        color="#f87171", va="bottom", alpha=0.85)
        except Exception:
            pass

        fig.tight_layout(pad=0.3)
        self.detail_chart_canvas.draw()

    def _update_detail_panel(self, proposal, sku_obj, pack_size: int):
        """Populate all structured sub-panels of the detail sidebar."""
        # ── Section 1: SKU Header ─────────────────────────────────────────
        self.detail_sku_lbl.config(text=proposal.sku)
        self.detail_desc_lbl.config(text=proposal.description or "")
        self.detail_rp_lbl.config(text=f"{proposal.reorder_point} pz")
        self.detail_stock_lbl.config(text=f"{proposal.current_on_hand} pz")

        # ── Section 2: Stock Projection Chart ─────────────────────────────
        self._draw_projection_chart(proposal)

        # ── Section 3: Demand Simulation ─────────────────────────────────
        # SMA: daily_sales_avg * forecast_period_days  (= baseline forecast qty)
        sma_daily = proposal.daily_sales_avg
        sma_total = sma_daily * proposal.forecast_period_days

        # MC: mc_comparison_qty (or mc_forecast_qty) if available; else same as SMA
        mc_qty = proposal.mc_comparison_qty if proposal.mc_comparison_qty is not None else None
        mc_daily: float | None = None
        if mc_qty is not None and proposal.forecast_period_days > 0:
            mc_daily = mc_qty / proposal.forecast_period_days

        max_daily = max(sma_daily, mc_daily or 0, 0.01)

        sma_pct = min(int((sma_daily / max_daily) * 100), 100)
        mc_pct = min(int(((mc_daily or 0) / max_daily) * 100), 100) if mc_daily else 0

        self.detail_sma_val_lbl.config(text=f"{sma_daily:.1f} u/day")
        self.detail_sma_bar["value"] = sma_pct

        if mc_daily is not None:
            self.detail_mc_val_lbl.config(text=f"{mc_daily:.1f} u/day")
            self.detail_mc_bar["value"] = mc_pct
            diff_pct = ((mc_daily - sma_daily) / max(sma_daily, 0.01)) * 100
            sign = "+" if diff_pct >= 0 else ""
            note = f"*Monte Carlo: {sign}{diff_pct:.0f}% rispetto a SMA"
            self.detail_sim_note_lbl.config(text=note)
        else:
            self.detail_mc_val_lbl.config(text="N/D")
            self.detail_mc_bar["value"] = 0
            self.detail_sim_note_lbl.config(text="*Monte Carlo non eseguito per questo SKU")

        # ── Section 4: Vincoli Attivi ─────────────────────────────────────
        has_constraints = bool(
            proposal.constraints_applied_max
            or proposal.constraints_applied_pack
            or proposal.constraints_applied_moq
            or (proposal.constraint_details
                and proposal.constraint_details != "Nessun vincolo applicato")
        )
        if has_constraints:
            titles = []
            if proposal.constraints_applied_max:
                titles.append("Max Stock Capped")
            if proposal.constraints_applied_pack:
                titles.append(f"Pack Size ({pack_size} pz/collo)")
            if proposal.constraints_applied_moq:
                titles.append(f"MOQ ({proposal.moq} pz)")
            title_text = " · ".join(titles) if titles else "Vincolo applicato"
            msg = proposal.constraint_details or ""
            self.detail_vincoli_title_lbl.config(text=title_text)
            self.detail_vincoli_msg_lbl.config(text=msg)
            self.detail_vincoli_outer.pack(fill="x", padx=5, pady=3)
        else:
            self.detail_vincoli_outer.pack_forget()

    def _clear_detail_panel(self):
        """Reset the detail sidebar to its empty/placeholder state."""
        self.detail_sku_lbl.config(text="—")
        self.detail_desc_lbl.config(text="—")
        self.detail_rp_lbl.config(text="— pz")
        self.detail_stock_lbl.config(text="— pz")
        self._draw_projection_chart(None)
        self.detail_sma_val_lbl.config(text="—")
        self.detail_sma_bar["value"] = 0
        self.detail_mc_val_lbl.config(text="—")
        self.detail_mc_bar["value"] = 0
        self.detail_sim_note_lbl.config(text="")
        self.detail_vincoli_outer.pack_forget()

    # ── Proposal selection ──────────────────────────────────────────────────

    def _on_proposal_select(self, event):
        """Handle selection of proposal to show calculation details."""
        selected = self.proposal_treeview.selection()
        if not selected:
            # Clear details if no selection
            self._clear_detail_panel()
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
        
        effective_lead_time = self._get_effective_lead_time(sku_obj)

        # Update the structured detail panel (chart, bars, vincoli, header)
        self._update_detail_panel(proposal, sku_obj, pack_size)

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
        
        # === EXPLAINABILITY DRIVERS (Standard Transparency) ===
        details.append("═══ PERCHÉ QUESTA QUANTITÀ? ═══")
        details.append(f"Policy Mode: {proposal.policy_mode.upper() if proposal.policy_mode else 'N/A'}")
        details.append(f"Forecast Method: {proposal.forecast_method.upper() if proposal.forecast_method else 'N/A'}")
        details.append(f"Reorder Point (S): {to_colli(proposal.reorder_point, pack_size)}")
        details.append(f"Inventory Position (IP): {to_colli(proposal.inventory_position, pack_size)}")
        
        # Policy-specific drivers
        if proposal.policy_mode == "csl":
            # CSL mode: show target alpha, sigma, z-score
            details.append(f"Target CSL (α): {proposal.target_csl:.3f} ({proposal.target_csl*100:.1f}%)")
            details.append(f"Domanda incertezza (σ): {proposal.sigma_horizon:.1f} pz")
            details.append(f"z-score: {proposal.csl_z_score:.2f}")
        elif proposal.policy_mode == "legacy":
            # Legacy mode: show equivalent CSL (informational)
            if proposal.equivalent_csl_legacy > 0:
                details.append(f"CSL Equivalente (informativo): {proposal.equivalent_csl_legacy:.3f} ({proposal.equivalent_csl_legacy*100:.1f}%)")
                details.append(f"  (approssimazione per confronto, non vincolante)")
        
        # OOS days impact
        if proposal.oos_days_count > 0:
            details.append(f"⚠️ Giorni OOS rilevati: {proposal.oos_days_count}")
            if proposal.oos_boost_applied:
                details.append(f"   Boost applicato: +{int(proposal.oos_boost_percent*100)}%")
        
        # History tracking (valid days used by forecast)
        if hasattr(proposal, 'history_valid_days'):
            details.append(f"Storico valido: {proposal.history_valid_days} giorni")
            if proposal.history_valid_days <= 7:
                details.append(f"  ⚠️ ATTENZIONE: Storico limitato (<= 7gg)")
        
        # Constraints applied
        details.append("")
        details.append("Vincoli Applicati:")
        constraints_list = []
        if proposal.constraints_applied_pack:
            constraints_list.append(f"✓ Pack size ({pack_size} pz/collo)")
        if proposal.constraints_applied_moq:
            constraints_list.append(f"✓ MOQ ({proposal.moq} pz)")
        if proposal.constraints_applied_max:
            constraints_list.append(f"✓ Max Stock ({proposal.max_stock} pz)")
        
        if constraints_list:
            for c in constraints_list:
                details.append(f"  {c}")
        else:
            details.append("  Nessun vincolo applicato")
        
        # Constraint details (full explanation)
        if proposal.constraint_details and proposal.constraint_details != "Nessun vincolo applicato":
            details.append(f"  Dettagli: {proposal.constraint_details}")
        
        details.append("")
        details.append("═══ DETTAGLI COMPLETI ═══")
        details.append("")
        
        # Forecast
        details.append("═══ FORECAST ═══")
        details.append(f"Periodo: {proposal.forecast_period_days} giorni")
        
        # Check if this was calendar-driven (protection period) or traditional (lead+review)
        expected_traditional = effective_lead_time + (sku_obj.review_period if sku_obj else 0)
        if proposal.forecast_period_days != expected_traditional:
            # Calendar-aware: show protection period
            details.append(f"  (Protezione calendario: {proposal.forecast_period_days}d)")
            details.append(f"  [vs tradizionale: Lead {effective_lead_time}d + Review {sku_obj.review_period if sku_obj else 0}d = {expected_traditional}d]")
        else:
            # Traditional: show lead+review breakdown
            details.append(f"  (Lead Time: {effective_lead_time}d + Review: {sku_obj.review_period if sku_obj else 0}d)")
        
        details.append(f"Media vendite giornaliere: {proposal.daily_sales_avg:.2f} pz/gg")
        details.append(f"Forecast qty: {to_colli(proposal.forecast_qty, pack_size)}")
        details.append("")
        
        # Promo Adjustment (if enabled and applied)
        if proposal.promo_adjustment_note:
            details.append("═══ PROMO ADJUSTMENT ═══")
            details.append(f"Baseline forecast: {to_colli(proposal.baseline_forecast_qty, pack_size)}")
            if proposal.promo_uplift_factor_used > 1.0:
                details.append(f"Promo uplift: {proposal.promo_uplift_factor_used:.2f}x")
                details.append(f"Adjusted forecast: {to_colli(proposal.promo_adjusted_forecast_qty, pack_size)}")
                delta = proposal.promo_adjusted_forecast_qty - proposal.baseline_forecast_qty
                details.append(f"Delta: +{to_colli(delta, pack_size)} ({(delta/proposal.baseline_forecast_qty*100):.1f}%)")
            details.append(f"Status: {proposal.promo_adjustment_note}")
            details.append("")
        
        # Event Uplift (delivery-date-based demand driver) - always visible
        details.append("═══ EVENT UPLIFT ═══")
        
        # Read event_uplift enabled status from settings
        try:
            settings = self.csv_layer.read_settings()
            event_uplift_enabled = settings.get("event_uplift", {}).get("enabled", {}).get("value", False)
        except Exception:
            event_uplift_enabled = False
        
        details.append(f"Enabled: {'Sì' if event_uplift_enabled else 'No'}")
        
        if proposal.receipt_date:
            details.append(f"Receipt date: {proposal.receipt_date.isoformat()}")
        if proposal.event_delivery_date:
            details.append(f"Delivery date: {proposal.event_delivery_date.isoformat()}")
        
        # Always show multiplier
        change_pct = (proposal.event_m_i - 1.0) * 100
        sign = "+" if change_pct >= 0 else ""
        details.append(f"Multiplier (m_i): {proposal.event_m_i:.3f} ({sign}{change_pct:.1f}%)")
        
        # Show details if active
        if proposal.event_uplift_active:
            if proposal.event_reason:
                details.append(f"Reason: {proposal.event_reason}")
            details.append(f"U (event shock): {proposal.event_u_store_day:.3f}")
            details.append(f"Beta (SKU sensitivity): {proposal.event_beta_i:.3f}")
            details.append(f"Quantile: P{int(proposal.event_quantile * 100)}")
            details.append(f"U fallback: {proposal.event_fallback_level}")
            details.append(f"Beta fallback: {proposal.event_beta_fallback_level}")
            if proposal.event_explain_short:
                details.append(f"Summary: {proposal.event_explain_short}")
        
        # Motivation (contextual explanation)
        motivation = None
        if not event_uplift_enabled:
            motivation = "Event uplift disabilitato nelle impostazioni"
        elif not proposal.event_uplift_active:
            motivation = "Nessuna regola uplift per questa data di consegna"
        elif proposal.event_m_i > 1.01:  # Significant increase
            if proposal.event_reason:
                motivation = f"Incremento del {change_pct:.1f}% per evento: {proposal.event_reason}"
            else:
                motivation = f"Incremento del {change_pct:.1f}% rilevato dai dati storici di giorni simili"
        elif proposal.event_m_i < 0.99:  # Significant decrease
            if proposal.event_reason:
                motivation = f"Riduzione del {abs(change_pct):.1f}% per evento: {proposal.event_reason}"
            else:
                motivation = f"Riduzione del {abs(change_pct):.1f}% rilevata dai dati storici di giorni simili"
        else:  # Neutral (m_i ≈ 1.0)
            if proposal.event_u_store_day is not None and abs(proposal.event_u_store_day - 1.0) < 0.05:
                motivation = "Giorni simili mostrano domanda normale (U ≈ 1.0)"
            elif proposal.event_beta_i is not None and abs(proposal.event_beta_i) < 0.1:
                motivation = "SKU insensibile agli eventi (Beta ≈ 0)"
            elif hasattr(proposal, 'event_strength') and proposal.event_strength is not None and proposal.event_strength < 0.1:
                motivation = f"Regola configurata con forza bassa (strength = {proposal.event_strength:.2f})"
            else:
                motivation = "Impatto netto neutro (combinazione U, Beta, Strength)"
        
        if motivation:
            details.append(f"Motivazione: {motivation}")
        
        details.append("")
        
        # Promo Prebuild (if enabled and applied)
        if proposal.promo_prebuild_enabled:
            details.append("═══ PROMO PREBUILD ═══")
            if proposal.promo_start_date:
                details.append(f"Promo start: {proposal.promo_start_date.isoformat()}")
            details.append(f"Coverage period: {proposal.prebuild_coverage_days} giorni")
            details.append(f"Target opening stock: {to_colli(proposal.target_open_qty, pack_size)}")
            details.append(f"Projected stock on promo start: {to_colli(proposal.projected_stock_on_promo_start, pack_size)}")
            details.append(f"Delta (target - projected): {to_colli(proposal.prebuild_delta_qty, pack_size)}")
            if proposal.prebuild_qty > 0:
                details.append(f"✓ Prebuild qty aggiunta: {to_colli(proposal.prebuild_qty, pack_size)}")
                if proposal.prebuild_distribution_note:
                    details.append(f"Distribution: {proposal.prebuild_distribution_note}")
            else:
                details.append("Prebuild non necessario (projected >= target)")
            details.append("")
        
        # Post-Promo Guardrail (if applied)
        if proposal.post_promo_guardrail_applied:
            details.append("═══ POST-PROMO GUARDRAIL ═══")
            details.append(f"Finestra post-promo: {proposal.post_promo_window_days} giorni dopo end_date")
            if proposal.post_promo_factor_used < 1.0:
                reduction_pct = (1.0 - proposal.post_promo_factor_used) * 100
                details.append(f"Cooldown factor: {proposal.post_promo_factor_used:.2f} (-{reduction_pct:.1f}%)")
            if proposal.post_promo_dip_factor < 1.0:
                dip_reduction_pct = (1.0 - proposal.post_promo_dip_factor) * 100
                details.append(f"Dip storico: {proposal.post_promo_dip_factor:.2f} (-{dip_reduction_pct:.1f}%)")
            if proposal.post_promo_cap_applied:
                details.append("✓ Qty cap assoluto applicato")
            if proposal.post_promo_alert:
                details.append(f"⚠️ {proposal.post_promo_alert}")
            details.append("")
        
        # Cannibalization (Downlift anti-sostituzione)
        if proposal.cannibalization_applied:
            details.append("═══ CANNIBALIZZAZIONE ═══")
            details.append(f"Driver promo: {proposal.cannibalization_driver_sku}")
            reduction_pct = (1.0 - proposal.cannibalization_downlift_factor) * 100
            details.append(f"Downlift factor: {proposal.cannibalization_downlift_factor:.2f} (-{reduction_pct:.1f}%)")
            details.append(f"Confidence: {proposal.cannibalization_confidence}")
            if proposal.cannibalization_note:
                details.append(f"Note: {proposal.cannibalization_note}")
            details.append("")
        
        # Lead Time Demand
        details.append("═══ LEAD TIME DEMAND ═══")
        details.append(f"Lead Time: {effective_lead_time} giorni")
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
        
        # CSL Breakdown (if CSL mode used)
        if proposal.csl_policy_mode == "csl":
            details.append("═══ CSL POLICY BREAKDOWN ═══")
            details.append(f"Policy Mode: CSL (Target Service Level)")
            details.append(f"Lane: {proposal.csl_lane}")
            details.append(f"Target α (CSL): {proposal.csl_alpha_target:.3f}")
            if proposal.csl_alpha_eff != proposal.csl_alpha_target:
                details.append(f"Effective α (after censored boost): {proposal.csl_alpha_eff:.3f}")
            details.append(f"z-score: {proposal.csl_z_score:.2f}")
            details.append("")
            details.append(f"Reorder Point S: {proposal.csl_reorder_point:.1f} pz")
            details.append(f"Forecast Demand μ_P: {proposal.csl_forecast_demand:.1f} pz")
            details.append(f"Demand Uncertainty σ_P: {proposal.csl_sigma_horizon:.1f} pz")
            if proposal.csl_n_censored > 0:
                details.append(f"⚠️ Censored periods detected: {proposal.csl_n_censored}")
            details.append("")
        
        # Inventory Position
        details.append("═══ INVENTORY POSITION (IP) ═══")
        details.append(f"On Hand: {to_colli(proposal.current_on_hand, pack_size)}")
        if proposal.usable_stock < proposal.current_on_hand:
            # Show shelf life impact on stock
            details.append(f"  Stock usabile (shelf life OK): {to_colli(proposal.usable_stock, pack_size)}")
            details.append(f"  Stock inutilizzabile (scaduto): {to_colli(proposal.unusable_stock, pack_size)}")
            if proposal.waste_risk_percent > 0:
                details.append(f"  Rischio spreco: {proposal.waste_risk_percent:.1f}%")
        details.append(f"On Order: {to_colli(proposal.current_on_order, pack_size)}")
        if proposal.unfulfilled_qty > 0:
            details.append(f"Unfulfilled (backorder): {to_colli(proposal.unfulfilled_qty, pack_size)}")
        ip_formula = "IP = usable_stock + on_order - unfulfilled" if proposal.usable_stock < proposal.current_on_hand else "IP = on_hand + on_order - unfulfilled"
        details.append(ip_formula)
        details.append(f"IP = {to_colli(proposal.inventory_position, pack_size)}")
        if proposal.shelf_life_penalty_applied:
            details.append(f"⚠️ PENALTY SHELF LIFE: {proposal.shelf_life_penalty_message}")
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
        
        # Monte Carlo Details (if MC was used as main method or for comparison)
        if proposal.mc_method_used or proposal.mc_comparison_qty is not None:
            details.append("")
            if proposal.mc_method_used == "monte_carlo":
                details.append("═══ METODO MONTE CARLO (PRINCIPALE) ═══")
            else:
                details.append("═══ CONFRONTO MONTE CARLO ═══")
            
            # Distribution and parameters
            if proposal.mc_distribution:
                dist_labels = {
                    "empirical": "Empirica (storico)",
                    "normal": "Normale",
                    "lognormal": "Log-Normale",
                    "residuals": "Residui"
                }
                dist_name = dist_labels.get(proposal.mc_distribution, proposal.mc_distribution)
                details.append(f"Distribuzione: {dist_name}")
            
            if proposal.mc_n_simulations > 0:
                details.append(f"Simulazioni: {proposal.mc_n_simulations:,}")
            
            # Output statistic
            if proposal.mc_output_stat:
                if proposal.mc_output_stat == "percentile" and proposal.mc_output_percentile > 0:
                    details.append(f"Statistica: P{proposal.mc_output_percentile} (Percentile)")
                elif proposal.mc_output_stat == "mean":
                    details.append(f"Statistica: Media")
                else:
                    details.append(f"Statistica: {proposal.mc_output_stat}")
            
            # Horizon
            if proposal.mc_horizon_days > 0:
                horizon_mode_label = "auto (lead+review)" if proposal.mc_horizon_mode == "auto" else "custom"
                details.append(f"Orizzonte: {proposal.mc_horizon_days} giorni ({horizon_mode_label})")
            
            # Forecast summary
            if proposal.mc_forecast_values_summary:
                details.append(f"Forecast: {proposal.mc_forecast_values_summary}")
            
            # Seed (for reproducibility)
            if proposal.mc_random_seed > 0:
                details.append(f"Seed: {proposal.mc_random_seed}")
            
            # Comparison quantity (if this is a comparison, not main method)
            if proposal.mc_method_used != "monte_carlo" and proposal.mc_comparison_qty is not None:
                details.append("")
                details.append(f"Qty proposta MC: {to_colli(proposal.mc_comparison_qty, pack_size)}")
                diff = proposal.mc_comparison_qty - proposal.proposed_qty
                sign = "+" if diff > 0 else ""
                details.append(f"Differenza: {sign}{to_colli(diff, pack_size)}")
        
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
        """Generate order proposals for all SKUs using calendar-driven planning."""
        # Read settings for workflow defaults
        settings = self.csv_layer.read_settings()
        engine = settings.get("reorder_engine", {})
        lead_time = engine.get("lead_time_days", {}).get("value", 7)

        # Update workflow lead time (used internally by generate_proposal fallback path)
        self.order_workflow.lead_time_days = lead_time

        # === CALENDAR-AWARE RECEIPT DATE & PROTECTION PERIOD ===
        from ..domain.calendar import (
            Lane, resolve_receipt_and_protection,
            next_order_opportunity, create_calendar_with_holidays, is_order_day,
        )

        # Parse order date from UI (default: today)
        try:
            order_date = date.fromisoformat(self.order_date_var.get().strip())
        except (ValueError, AttributeError):
            order_date = date.today()

        calendar_config = create_calendar_with_holidays(self.csv_layer.data_dir)

        # Parse lane
        try:
            lane = Lane[self.lane_var.get() or "STANDARD"]
        except KeyError:
            lane = Lane.STANDARD

        # Determine effective planning date (prompt if non-order day)
        planning_date = order_date
        if not is_order_day(order_date, calendar_config):
            try:
                next_valid = next_order_opportunity(
                    order_date - timedelta(days=1), calendar_config
                )
            except ValueError:
                next_valid = order_date
            if next_valid != order_date:
                use_next = messagebox.askyesno(
                    "Giorno ordine non valido",
                    (
                        f"{order_date.strftime('%d/%m/%Y')} non è un giorno d'ordine valido.\n"
                        f"Vuoi usare il prossimo giorno valido ({next_valid.strftime('%d/%m/%Y')})?\n\n"
                        "Se scegli No, l'operazione verrà annullata."
                    ),
                )
                if not use_next:
                    logger.info(
                        f"Proposal generation cancelled: {order_date} not order day, next={next_valid}."
                    )
                    return
                planning_date = next_valid

        # If SATURDAY/MONDAY lane requested but planning_date is not Friday → downgrade
        if lane in (Lane.SATURDAY, Lane.MONDAY) and planning_date.weekday() != 4:
            logger.warning(
                f"Lane {lane} requires Friday; planning_date={planning_date}. Downgrading to STANDARD."
            )
            lane = Lane.STANDARD

        # Resolve receipt override
        force_override = getattr(self, "force_receipt_var", None) and self.force_receipt_var.get()
        receipt_override = None
        if force_override:
            try:
                receipt_override = date.fromisoformat(self.override_receipt_var.get().strip())
            except (ValueError, AttributeError):
                messagebox.showerror(
                    "Data Override non valida",
                    "La data ricevimento forzata non è nel formato YYYY-MM-DD.\n"
                    "Correggi il campo oppure disattiva l'override.",
                )
                return
            # Validate: receipt must be >= planning_date
            if receipt_override < planning_date:
                messagebox.showerror(
                    "Data Override non valida",
                    f"La data ricevimento forzata ({receipt_override}) "
                    f"è anteriore alla data ordine ({planning_date}).",
                )
                return

        # Compute (r1, P) via single authoritative domain function
        try:
            target_receipt_date, protection_period = resolve_receipt_and_protection(
                planning_date, lane, calendar_config, receipt_override
            )
        except ValueError as final_error:
            logger.warning(
                f"resolve_receipt_and_protection failed ({final_error}). "
                f"Forcing STANDARD fallback on {planning_date}."
            )
            lane = Lane.STANDARD
            try:
                target_receipt_date, protection_period = resolve_receipt_and_protection(
                    planning_date, Lane.STANDARD, calendar_config, None
                )
            except ValueError:
                target_receipt_date = planning_date + timedelta(days=max(1, lead_time))
                protection_period = max(1, (target_receipt_date - planning_date).days)

        
        # Get all SKUs
        sku_ids = self.csv_layer.get_all_sku_ids()
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Filter: only SKUs in assortment
        sku_ids = [sku_id for sku_id in sku_ids if sku_id in skus_by_id and skus_by_id[sku_id].in_assortment]
        
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
            
            # Calculate daily sales average (with OOS exclusion + detailed breakdown)
            daily_sales, oos_days_count, oos_days_list, out_of_assortment_days = calculate_daily_sales_average(
                sales_records, sku_id, 
                days_lookback=oos_lookback_days, 
                transactions=transactions, 
                asof_date=date.today(),
                oos_detection_mode=oos_detection_mode,
                return_details=True
            )
            
            # Calculate valid history days (used by forecast engine)
            history_valid_days = oos_lookback_days - len(oos_days_list) - len(out_of_assortment_days)
            
            # Determine OOS boost for this SKU
            oos_boost_percent = 0.0
            if oos_days_count > 0:
                # First, check SKU's permanent preference
                if sku_obj and sku_obj.oos_popup_preference == "always_yes":
                    # Always apply boost without asking
                    oos_boost_percent = oos_boost_default
                    self.oos_boost_preferences[sku_id] = oos_boost_default
                elif sku_obj and sku_obj.oos_popup_preference == "always_no":
                    # Never apply boost without asking
                    oos_boost_percent = 0.0
                    self.oos_boost_preferences[sku_id] = None
                # Then check if user has already decided for this SKU in this session
                elif sku_id in self.oos_boost_preferences:
                    # User already decided (could be None for "no", or a percent for "yes"/"yes, always")
                    oos_boost_percent = self.oos_boost_preferences[sku_id] or 0.0
                else:
                    # Ask user via dialog (now returns tuple with estimate info)
                    boost_choice, estimate_date, estimate_colli = self._ask_oos_boost(sku_id, description, oos_days_count, oos_boost_default)
                    
                    # Process estimate if provided
                    if estimate_date and estimate_colli and estimate_colli > 0:
                        try:
                            # Convert colli to pezzi
                            pack_size = sku_obj.pack_size if sku_obj else 1
                            estimate_pz = estimate_colli * pack_size
                            
                            # Check if estimate already registered for this date
                            existing_sales = [s for s in sales_records if s.sku == sku_id and s.date == estimate_date]
                            
                            # Idempotency key in note
                            estimate_note = f"OOS_ESTIMATE_OVERRIDE:{estimate_date.isoformat()}"
                            existing_override = any(
                                t.sku == sku_id and t.date == estimate_date and estimate_note in (t.note or "")
                                for t in transactions
                            )
                            
                            if not existing_override:
                                # Write sales record for estimated lost sales
                                from ..domain.models import SalesRecord
                                new_sale = SalesRecord(
                                    date=estimate_date,
                                    sku=sku_id,
                                    qty_sold=estimate_pz
                                )
                                self.csv_layer.write_sales([new_sale])
                                sales_records.append(new_sale)
                                
                                # Write WASTE event with special note to mark this day as non-OOS
                                # WASTE qty=0 is just a marker, doesn't change stock
                                marker_txn = Transaction(
                                    date=estimate_date,
                                    sku=sku_id,
                                    event=EventType.WASTE,
                                    qty=0,
                                    note=f"{estimate_note}|{estimate_colli} colli ({estimate_pz} pz) - prevents OOS counting"
                                )
                                self.csv_layer.write_transaction(marker_txn)
                                transactions.append(marker_txn)
                                
                                # Recalculate daily average with override marker
                                daily_sales, oos_days_count, oos_days_list, out_of_assortment_days = calculate_daily_sales_average(
                                    sales_records, sku_id,
                                    days_lookback=oos_lookback_days,
                                    transactions=transactions,
                                    asof_date=date.today(),
                                    oos_detection_mode=oos_detection_mode,
                                    return_details=True
                                )
                                
                                # Update valid history days after recalculation
                                history_valid_days = oos_lookback_days - len(oos_days_list) - len(out_of_assortment_days)
                                
                                logger.info(f"OOS estimate registered for {sku_id} on {estimate_date}: {estimate_colli} colli ({estimate_pz} pz)")
                        except Exception as e:
                            logger.error(f"Failed to register OOS estimate for {sku_id}: {e}", exc_info=True)
                    
                    # Process boost choice
                    if boost_choice == "yes":
                        oos_boost_percent = oos_boost_default
                        self.oos_boost_preferences[sku_id] = oos_boost_default
                    elif boost_choice == "yes_always":
                        oos_boost_percent = oos_boost_default
                        self.oos_boost_preferences[sku_id] = oos_boost_default  # Store for session
                        # Save permanently to SKU
                        if sku_obj:
                            self.csv_layer.update_sku(
                                sku_obj.sku, sku_obj.sku, sku_obj.description, sku_obj.ean,
                                sku_obj.moq, sku_obj.pack_size, sku_obj.lead_time_days, 
                                sku_obj.review_period, sku_obj.safety_stock, sku_obj.shelf_life_days,
                                sku_obj.max_stock, sku_obj.reorder_point,
                                sku_obj.demand_variability, sku_obj.oos_boost_percent, 
                                sku_obj.oos_detection_mode, "always_yes",
                                category=sku_obj.category, department=sku_obj.department,
                            )
                    elif boost_choice == "no_never":
                        oos_boost_percent = 0.0
                        self.oos_boost_preferences[sku_id] = None
                        # Save permanently to SKU
                        if sku_obj:
                            self.csv_layer.update_sku(
                                sku_obj.sku, sku_obj.sku, sku_obj.description, sku_obj.ean,
                                sku_obj.moq, sku_obj.pack_size, sku_obj.lead_time_days, 
                                sku_obj.review_period, sku_obj.safety_stock, sku_obj.shelf_life_days,
                                sku_obj.max_stock, sku_obj.reorder_point,
                                sku_obj.demand_variability, sku_obj.oos_boost_percent, 
                                sku_obj.oos_detection_mode, "always_no",
                                category=sku_obj.category, department=sku_obj.department,
                            )
                    else:  # "no"
                        oos_boost_percent = 0.0
                        self.oos_boost_preferences[sku_id] = None
            
            # Generate proposal (pass sku_obj for pack_size, MOQ, lead_time, review_period, safety_stock, max_stock)
            # CALENDAR-AWARE: pass target_receipt_date and protection_period for inventory position calculation
            proposal = self.order_workflow.generate_proposal(
                sku=sku_id,
                description=description,
                current_stock=stock,
                daily_sales_avg=daily_sales,
                sku_obj=sku_obj,
                oos_days_count=oos_days_count,
                oos_boost_percent=oos_boost_percent,
                target_receipt_date=target_receipt_date,
                protection_period_days=protection_period,
                transactions=transactions,
                sales_records=sales_records,
            )
            
            # Inject history_valid_days into proposal (not part of workflow generation)
            proposal.history_valid_days = history_valid_days
            
            self.current_proposals.append(proposal)
        
        # Populate table
        self._refresh_proposal_table()
        
        messagebox.showinfo(
            "Proposte Generate",
            f"Generate {len(self.current_proposals)} proposte ordine.\nProposte con Q.tà > 0: {sum(1 for p in self.current_proposals if p.proposed_qty > 0)}",
        )
    
    def _ask_oos_boost(self, sku: str, description: str, oos_days_count: int, default_percent: float) -> tuple:
        """
        Ask user if they want to apply OOS boost for a specific SKU.
        
        Returns:
            (boost_choice, estimate_date, estimate_colli)
            - boost_choice: "yes", "yes_always", or "no"
            - estimate_date: date object or None (stima mancata vendita)
            - estimate_colli: int or None (colli stimati per quella data)
        """
        popup = tk.Toplevel(self.root)
        popup.title("OOS Boost")
        popup.geometry("550x400")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        
        result = tk.StringVar(value="no")
        estimate_date_var = tk.StringVar(value=(date.today() - timedelta(days=1)).isoformat())
        estimate_colli_var = tk.StringVar(value="")
        
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
            wraplength=500,
        ).pack(anchor="w", pady=(0, 10))
        
        ttk.Label(
            content_frame,
            text=f"Vuoi aumentare la quantità ordinata del {int(default_percent * 100)}% per compensare?",
            font=("Helvetica", 10, "bold"),
            wraplength=500,
        ).pack(anchor="w", pady=(0, 15))
        
        # Optional estimate section
        separator = ttk.Separator(content_frame, orient="horizontal")
        separator.pack(fill="x", pady=(0, 15))
        
        ttk.Label(
            content_frame,
            text="Opzionale: Stima mancata vendita",
            font=("Helvetica", 9, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        
        ttk.Label(
            content_frame,
            text="Se hai una stima di vendite perse in un giorno OOS, inseriscila qui.\nIl giorno NON sarà conteggiato come OOS e verrà registrato con vendite = IP + stima.",
            font=("Helvetica", 8),
            foreground="gray",
            wraplength=500,
        ).pack(anchor="w", pady=(0, 10))
        
        estimate_frame = ttk.Frame(content_frame)
        estimate_frame.pack(fill="x", pady=(0, 15))
        
        # Date selector
        date_row = ttk.Frame(estimate_frame)
        date_row.pack(fill="x", pady=(0, 5))
        ttk.Label(date_row, text="Data:", width=15).pack(side="left")
        if TKCALENDAR_AVAILABLE:
            DateEntry(  # type: ignore[misc]
                date_row,
                textvariable=estimate_date_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).pack(side="left")
        else:
            ttk.Entry(date_row, textvariable=estimate_date_var, width=15).pack(side="left")
        
        # Colli estimate
        colli_row = ttk.Frame(estimate_frame)
        colli_row.pack(fill="x")
        ttk.Label(colli_row, text="Colli stimati:", width=15).pack(side="left")
        ttk.Entry(colli_row, textvariable=estimate_colli_var, width=10).pack(side="left")
        ttk.Label(colli_row, text="(lascia vuoto se non hai stima)", font=("Helvetica", 8), foreground="gray").pack(side="left", padx=(5, 0))
        
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
        
        ttk.Button(
            button_frame,
            text="No, mai (per questo SKU)",
            command=lambda: choose("no_never"),
        ).pack(side="left", padx=5)
        
        # Wait for user choice
        popup.wait_window()
        
        # Parse estimate
        estimate_date = None
        estimate_colli = None
        estimate_str = estimate_colli_var.get().strip()
        if estimate_str:
            try:
                estimate_colli = int(estimate_str)
                if estimate_colli > 0:
                    estimate_date = date.fromisoformat(estimate_date_var.get())
            except (ValueError, TypeError):
                pass  # Invalid input, ignore estimate
        
        return (result.get(), estimate_date, estimate_colli)
    
    def _get_effective_lead_time(self, sku_obj):
        """Return SKU lead time if set, otherwise global lead time."""
        if sku_obj and sku_obj.lead_time_days > 0:
            return sku_obj.lead_time_days
        return self.order_workflow.lead_time_days

    def _build_proposal_row_values(self, proposal):
        """Build treeview row values for a proposal."""
        sku_obj = next((s for s in self.csv_layer.read_skus() if s.sku == proposal.sku), None)
        pack_size = sku_obj.pack_size if sku_obj else 1

        colli_proposti = proposal.proposed_qty // pack_size if pack_size > 0 else proposal.proposed_qty

        mc_comparison_display = ""
        if proposal.mc_method_used == "monte_carlo":
            stat_label = f"P{proposal.mc_output_percentile}" if proposal.mc_output_stat == "percentile" else "Media"
            mc_comparison_display = f"MC: {stat_label}"
        elif proposal.mc_comparison_qty is not None:
            stat_label = f"P{proposal.mc_output_percentile}" if proposal.mc_output_stat == "percentile" else "Media"
            mc_comparison_display = f"Confronto: {stat_label} ({proposal.mc_comparison_qty} pz)"

        usable_stock_display = f"{proposal.usable_stock}/{proposal.current_on_hand}" if proposal.usable_stock < proposal.current_on_hand else str(proposal.current_on_hand)
        waste_risk_display = f"{proposal.waste_risk_percent:.1f}%" if proposal.waste_risk_percent > 0 else ""
        shelf_penalty_display = proposal.shelf_life_penalty_message if proposal.shelf_life_penalty_applied else ""

        # Promo delta display: show uplift factor if > 1.0, else "-"
        promo_delta_display = ""
        if proposal.promo_uplift_factor_used > 1.0:
            promo_delta_display = f"📈 {proposal.promo_uplift_factor_used:.2f}x"
        elif proposal.promo_adjustment_note and "Promo attiva" in proposal.promo_adjustment_note:
            promo_delta_display = "⚠️ N/A"  # Promo active but uplift unavailable
        else:
            promo_delta_display = "-"  # No promo
        
        # Post-promo guardrail indicator (append to promo_delta_display)
        if proposal.post_promo_guardrail_applied:
            if proposal.post_promo_alert and "RISCHIO OVERSTOCK" in proposal.post_promo_alert:
                promo_delta_display += " ⚠️⏳"  # Alert + post-promo
            else:
                promo_delta_display += " ⏳"  # Post-promo active (no alert)
        
        # Cannibalization indicator (append downlift badge)
        if proposal.cannibalization_applied:
            reduction_pct = (1.0 - proposal.cannibalization_downlift_factor) * 100
            promo_delta_display += f" 📉{reduction_pct:.0f}%"  # Downlift badge con driver
        
        # Event uplift display: show m_i multiplier if event active
        event_uplift_display = ""
        if proposal.event_uplift_active and proposal.event_m_i > 1.0:
            change_pct = (proposal.event_m_i - 1.0) * 100
            event_uplift_display = f"+{change_pct:.0f}%"
            # Add reason badge if available
            if proposal.event_reason:
                event_uplift_display += f" ({proposal.event_reason[:8]})"
        elif proposal.event_uplift_active and proposal.event_m_i < 1.0:
            change_pct = (1.0 - proposal.event_m_i) * 100
            event_uplift_display = f"-{change_pct:.0f}%"
        else:
            event_uplift_display = "-"  # No event
        
        return (
            proposal.sku,
            proposal.description,
            pack_size,
            usable_stock_display,
            waste_risk_display,
            colli_proposti,
            proposal.proposed_qty,
            shelf_penalty_display,
            mc_comparison_display,
            promo_delta_display,
            event_uplift_display,
            proposal.receipt_date.isoformat() if proposal.receipt_date else "",
        )

    def _refresh_proposal_table(self):
        """Refresh proposals table."""
        self.proposal_treeview.delete(*self.proposal_treeview.get_children())
        
        for proposal in self.current_proposals:
            # Apply low_history tag if valid days <= 7
            tags = ()
            if hasattr(proposal, 'history_valid_days') and proposal.history_valid_days <= 7:
                tags = ("low_history",)
            
            self.proposal_treeview.insert(
                "",
                "end",
                values=self._build_proposal_row_values(proposal),
                tags=tags
            )

    def _update_order_info(self, *_):
        """Auto-update the read-only planning info section when order inputs change."""
        from ..domain.calendar import (
            Lane, resolve_receipt_and_protection,
            create_calendar_with_holidays, next_order_opportunity, is_order_day,
        )

        # Parse order date
        order_date_str = self.order_date_var.get().strip()
        try:
            order_date = date.fromisoformat(order_date_str)
        except ValueError:
            for lbl in (self.info_receipt_lbl, self.info_p_lbl, self.info_lt_lbl, self.info_lane_lbl):
                lbl.config(text="— data non valida —", foreground="red")
            return

        # Load calendar config (non-blocking; default config on error)
        try:
            calendar_config = create_calendar_with_holidays(self.csv_layer.data_dir)
        except Exception:
            from ..domain.calendar import DEFAULT_CONFIG
            calendar_config = DEFAULT_CONFIG

        force = self.force_receipt_var.get()

        # Enable/disable widgets based on override mode
        self.override_receipt_entry.config(state="normal" if force else "disabled")
        self.override_reason_combo.config(state="readonly" if force else "disabled")
        self.lane_combo.config(state="disabled" if force else "readonly")

        # Parse lane
        try:
            lane = Lane[self.lane_var.get() or "STANDARD"]
        except KeyError:
            lane = Lane.STANDARD

        # Resolve override date if active
        receipt_override = None
        if force:
            try:
                receipt_override = date.fromisoformat(self.override_receipt_var.get().strip())
            except ValueError:
                self.info_receipt_lbl.config(text="— data override non valida —", foreground="red")
                for lbl in (self.info_p_lbl, self.info_lt_lbl, self.info_lane_lbl):
                    lbl.config(text="—", foreground="gray")
                return

        # If today is not a valid order day, silently use next valid day for preview
        preview_order_date = order_date
        if not is_order_day(order_date, calendar_config):
            try:
                preview_order_date = next_order_opportunity(
                    order_date - timedelta(days=1), calendar_config
                )
            except ValueError:
                preview_order_date = order_date

        # Compute (r1, P) via the domain function
        try:
            r1, P = resolve_receipt_and_protection(preview_order_date, lane, calendar_config, receipt_override)
            lead_time_eff = (r1 - order_date).days
            self.info_receipt_lbl.config(text=r1.strftime("%d/%m/%Y (%a)"), foreground="#0066cc")
            self.info_lt_lbl.config(text=f"{lead_time_eff} giorni", foreground="#0066cc")
            self.info_p_lbl.config(text=f"{P} giorni", foreground="#0066cc")
            self.info_lane_lbl.config(text=lane.value, foreground="#0066cc")
        except ValueError as exc:
            self.info_receipt_lbl.config(text=f"— {exc} —", foreground="red")
            for lbl in (self.info_p_lbl, self.info_lt_lbl, self.info_lane_lbl):
                lbl.config(text="—", foreground="gray")

    def _refresh_order_stock_data(self):
        """Refresh stock data without regenerating proposals."""
        messagebox.showinfo("Info", "Dati stock aggiornati. Clicca 'Genera Tutte le Proposte' per ricalcolare.")
    
    def _clear_proposals(self):
        """Clear all proposals."""
        self.current_proposals = []
        self.proposal_treeview.delete(*self.proposal_treeview.get_children())
    
    def _on_proposal_double_click(self, event):
        """Handle double-click on proposal row to edit proposed qty or receipt date."""
        selected = self.proposal_treeview.selection()
        if not selected:
            return

        column_id = self.proposal_treeview.identify_column(event.x)
        if not column_id:
            return

        try:
            column_index = int(column_id.replace("#", "")) - 1
        except ValueError:
            return

        columns = self.proposal_treeview["columns"]
        if column_index < 0 or column_index >= len(columns):
            return

        column_name = columns[column_index]

        item = self.proposal_treeview.item(selected[0])
        values = item["values"]
        sku = values[0]

        proposal = next((p for p in self.current_proposals if p.sku == sku), None)
        if not proposal:
            return

        if column_name == "Receipt Date":
            self._edit_receipt_date_dialog(proposal, selected[0])
            return

        if column_name == "Colli Proposti":
            self._edit_proposed_qty_inline(proposal, selected[0], event)
    
    def _edit_proposed_qty_inline(self, proposal, tree_item_id, event):
        """Edit proposed quantity inline (in-cell) with Entry widget."""
        sku_obj = next((s for s in self.csv_layer.read_skus() if s.sku == proposal.sku), None)
        pack_size = sku_obj.pack_size if sku_obj else 1
        
        # Get current value
        current_colli = proposal.proposed_qty // pack_size if pack_size > 0 else proposal.proposed_qty
        
        # Get cell bounding box
        column_id = self.proposal_treeview.identify_column(event.x)
        bbox = self.proposal_treeview.bbox(tree_item_id, column_id)
        if not bbox:
            return
        
        x, y, width, height = bbox
        
        # Create Entry widget over the cell
        entry_var = tk.StringVar(value=str(current_colli))
        entry = ttk.Entry(self.proposal_treeview, textvariable=entry_var, justify="center")
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()
        entry.select_range(0, tk.END)
        
        def save_value(event=None):
            """Save the edited value (Enter key)."""
            try:
                new_colli = int(entry_var.get())
                if new_colli < 0:
                    messagebox.showerror("Errore", "I colli devono essere >= 0.")
                    entry.destroy()
                    return
                
                # Convert colli to pezzi
                new_pezzi = new_colli * pack_size
                
                # Update proposal
                proposal.proposed_qty = new_pezzi
                
                # Update tree item
                self.proposal_treeview.item(
                    tree_item_id,
                    values=self._build_proposal_row_values(proposal),
                )
                
                entry.destroy()
            except ValueError:
                messagebox.showerror("Errore", "Inserire un numero intero valido.")
                entry.destroy()
        
        def cancel_edit(event=None):
            """Cancel the edit (Esc key or focus out)."""
            entry.destroy()
        
        # Bind keys
        entry.bind("<Return>", save_value)
        entry.bind("<KP_Enter>", save_value)  # Keypad Enter
        entry.bind("<Escape>", cancel_edit)
        entry.bind("<FocusOut>", cancel_edit)
    
    def _edit_proposed_qty_dialog(self, proposal, tree_item_id):
        """Show dialog to edit proposed quantity in colli."""
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
                
                # Update tree item
                self.proposal_treeview.item(
                    tree_item_id,
                    values=self._build_proposal_row_values(proposal),
                )
                
                popup.destroy()
            except ValueError:
                messagebox.showerror("Errore di Validazione", "La quantità deve essere un numero intero.", parent=popup)
        
        # Buttons
        button_frame = ttk.Frame(form_frame)
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="Salva", command=save_qty).pack(side="right", padx=5)
        ttk.Button(button_frame, text="Annulla", command=popup.destroy).pack(side="right", padx=5)

    def _edit_receipt_date_dialog(self, proposal, tree_item_id):
        """Show dialog to edit receipt date for a proposal."""
        popup = tk.Toplevel(self.root)
        popup.title(f"Modifica Data Ricevimento - {proposal.sku}")
        popup.geometry("450x220")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()

        form_frame = ttk.Frame(popup, padding=20)
        form_frame.pack(fill="both", expand=True)

        ttk.Label(form_frame, text=f"SKU: {proposal.sku}", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=5)
        ttk.Label(form_frame, text=f"Descrizione: {proposal.description}").pack(anchor="w", pady=5)
        ttk.Label(form_frame, text="Data Ricevimento (YYYY-MM-DD, vuoto=default):").pack(anchor="w", pady=(15, 5))

        current_date = proposal.receipt_date.isoformat() if proposal.receipt_date else ""
        receipt_date_var = tk.StringVar(value=current_date)
        date_entry = ttk.Entry(form_frame, textvariable=receipt_date_var, width=20)
        date_entry.pack(anchor="w", pady=(0, 15))
        date_entry.focus()

        def save_date():
            date_str = receipt_date_var.get().strip()
            if not date_str:
                proposal.receipt_date = None
            else:
                try:
                    proposal.receipt_date = date.fromisoformat(date_str)
                except ValueError:
                    messagebox.showerror("Errore di Validazione", "Formato data non valido. Usa YYYY-MM-DD.", parent=popup)
                    return

            self.proposal_treeview.item(
                tree_item_id,
                values=self._build_proposal_row_values(proposal),
            )
            popup.destroy()

        button_frame = ttk.Frame(form_frame)
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="Salva", command=save_date).pack(side="right", padx=5)
        ttk.Button(button_frame, text="Annulla", command=popup.destroy).pack(side="right", padx=5)
        
        # Bind Enter to save
        date_entry.bind("<Return>", lambda e: save_date())
    
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
                pack_size = sku_obj.pack_size if sku_obj else 1
                
                # Layout a 2 colonne: sinistra = info, destra = barcode
                columns_frame = ttk.Frame(item_frame)
                columns_frame.pack(fill="x", expand=True)
                
                # COLONNA SINISTRA: Info prodotto
                left_frame = ttk.Frame(columns_frame)
                left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))
                
                ttk.Label(left_frame, text=f"Descrizione: {description}").pack(anchor="w")
                
                # QUANTITÀ IN COLLI - EVIDENZIATA
                colli = confirmation.qty_ordered // pack_size if pack_size > 0 else confirmation.qty_ordered
                resto_pz = confirmation.qty_ordered % pack_size if pack_size > 0 else 0
                
                qty_frame = ttk.Frame(left_frame)
                qty_frame.pack(anchor="w", pady=(5, 5))
                
                ttk.Label(
                    qty_frame, 
                    text="QUANTITÀ:", 
                    font=("Helvetica", 10, "bold")
                ).pack(side="left", padx=(0, 5))
                
                ttk.Label(
                    qty_frame,
                    text=f"{colli} colli",
                    font=("Helvetica", 14, "bold"),
                    foreground="darkblue"
                ).pack(side="left", padx=(0, 5))
                
                if resto_pz > 0:
                    ttk.Label(
                        qty_frame,
                        text=f"+ {resto_pz} pz",
                        font=("Helvetica", 11, "bold"),
                        foreground="darkblue"
                    ).pack(side="left")
                
                ttk.Label(
                    left_frame,
                    text=f"({confirmation.qty_ordered} pz totali)",
                    font=("Helvetica", 9),
                    foreground="gray"
                ).pack(anchor="w")
                
                ttk.Label(left_frame, text=f"Data Ricevimento: {confirmation.receipt_date.isoformat()}").pack(anchor="w", pady=(5, 0))
                ttk.Label(left_frame, text=f"ID Ordine: {confirmation.order_id}", font=("Courier", 9)).pack(anchor="w")
                
                # COLONNA DESTRA: Barcode
                right_frame = ttk.Frame(columns_frame)
                right_frame.pack(side="right", padx=5)
                
                if ean:
                    is_valid, error = validate_ean(ean)
                    if is_valid:
                        ttk.Label(
                            right_frame,
                            text=f"EAN: {ean}",
                            font=("Courier", 10, "bold")
                        ).pack(anchor="center", pady=(0, 5))
                        
                        # Render barcode
                        if BARCODE_AVAILABLE:
                            try:
                                barcode_img = self._generate_barcode_image(ean)
                                if barcode_img:
                                    barcode_label = ttk.Label(right_frame, image=barcode_img)
                                    barcode_label.image = barcode_img  # type: ignore[attr-defined] # Keep reference
                                    barcode_label.pack(anchor="center")
                            except Exception as e:
                                ttk.Label(right_frame, text=f"Errore barcode: {str(e)}", foreground="red").pack(anchor="center")
                        else:
                            ttk.Label(right_frame, text="(Rendering barcode\ndisabilitato)", foreground="gray", justify="center").pack(anchor="center")
                    else:
                        ttk.Label(right_frame, text=f"EAN: {ean}\n(Non valido - {error})", foreground="red", justify="center").pack(anchor="center")
                else:
                    ttk.Label(right_frame, text="EAN non disponibile\n(nessun barcode)", foreground="gray", justify="center").pack(anchor="center")
            
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
        """Build Receiving tab - layout matching Goods Receiving Management mockup."""
        # ── Outer scrollable canvas ──────────────────────────────────────────
        outer = ttk.Frame(self.receiving_tab)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        main_frame = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=main_frame, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(canvas_window, width=e.width)
        main_frame.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse-wheel scrolling
        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        PAD = dict(padx=12, pady=4)

        # ── PAGE HEADER ──────────────────────────────────────────────────────
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", padx=16, pady=(14, 4))

        header_left = ttk.Frame(header_frame)
        header_left.pack(side="left", fill="x", expand=True)
        ttk.Label(
            header_left,
            text="Gestione Ricevimenti",
            font=("Helvetica", 17, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header_left,
            text="Gestisci le spedizioni in arrivo, verifica le quantità e traccia le informazioni sui lotti.",
            font=("Helvetica", 9),
            foreground="gray",
        ).pack(anchor="w")

        ttk.Button(
            header_frame,
            text="↻  Aggiorna Dati",
            command=lambda: (self._refresh_pending_orders(), self._refresh_receiving_history()),
        ).pack(side="right", padx=(0, 2), pady=4)

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", padx=16, pady=(6, 10))

        # ── SECTION 1 — PENDING RECEIPTS ─────────────────────────────────────
        pending_card = ttk.LabelFrame(main_frame, text="  📋  Ricevimenti in Sospeso  —  Ordini in attesa di conferma", padding=(10, 6))
        pending_card.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # Card toolbar: title info left, search right
        p_toolbar = ttk.Frame(pending_card)
        p_toolbar.pack(fill="x", pady=(0, 6))

        self.pending_search_var = tk.StringVar()
        search_frame = ttk.Frame(p_toolbar)
        search_frame.pack(side="right")
        ttk.Label(search_frame, text="🔍").pack(side="left")
        pending_search_ac = AutocompleteEntry(
            search_frame,
            textvariable=self.pending_search_var,
            items_callback=self._filter_pending_sku_items,
            width=32,
        )
        pending_search_ac.pack(side="left", padx=(2, 0))
        self.pending_search_var.trace("w", lambda *_: self._filter_pending_orders())
        ttk.Label(p_toolbar, text="Cerca SKU o Descrizione…", foreground="gray", font=("Helvetica", 9)).pack(side="left")

        # Pending qty edits dict
        self.pending_qty_edits = {}
        self.pending_expiry_edits = {}  # Per-row expiry dates for has_expiry_label SKUs

        # Treeview + scrollbar
        p_tv_frame = ttk.Frame(pending_card)
        p_tv_frame.pack(fill="both", expand=True)

        p_scroll = ttk.Scrollbar(p_tv_frame, orient="vertical")
        p_scroll.pack(side="right", fill="y")

        cols = ("Order ID", "SKU", "Description", "Pack Size",
                "Colli Ordinati", "Colli Ricevuti", "Colli Sospesi", "Receipt Date", "Scadenza")
        self.pending_treeview = ttk.Treeview(
            p_tv_frame,
            columns=cols,
            show="headings",
            height=6,
            yscrollcommand=p_scroll.set,
        )
        p_scroll.config(command=self.pending_treeview.yview)

        col_cfg = [
            ("Order ID",       "ID Ordine",      tk.W,      130),
            ("SKU",            "SKU",             tk.W,       90),
            ("Description",    "Descrizione",     tk.W,      200),
            ("Pack Size",      "Pz/Collo",        tk.CENTER,  72),
            ("Colli Ordinati", "Ordinati",        tk.CENTER,  80),
            ("Colli Ricevuti", "Ricevuti",        tk.CENTER,  80),
            ("Colli Sospesi",  "In Sospeso",      tk.CENTER,  80),
            ("Receipt Date",   "Data Prevista",   tk.CENTER, 110),
            ("Scadenza",       "Data Scadenza",   tk.CENTER, 110),
        ]
        for col_id, heading, _anchor, width in col_cfg:
            anchor = cast(Literal["w", "center", "e"], _anchor)
            self.pending_treeview.column(col_id, anchor=anchor, width=width, stretch=(col_id == "Description"))
            self.pending_treeview.heading(col_id, text=heading, anchor=anchor)

        self.pending_treeview.pack(fill="both", expand=True)
        self.pending_treeview.bind("<ButtonRelease-1>", self._on_pending_cell_click)

        # Row tags
        self.pending_treeview.tag_configure("edited",   background="#fffde7")   # yellow tint
        self.pending_treeview.tag_configure("complete", background="#e8f5e9")   # green tint
        self.pending_treeview.tag_configure("partial",  background="#fff3e0")   # orange tint

        pending_hint = ttk.Label(
            pending_card,
            text="💡 Clic su Ricevuti per modificare la quantità direttamente in tabella. Per SKU con etichetta scadenza: clic su Data Scadenza per aprire il calendario inline.",
            font=("Helvetica", 8),
            foreground="gray",
        )
        pending_hint.pack(anchor="w", pady=(4, 0))

        # ── SECTION 2 — CONFIRM RICEVIMENTO ───────────────────────────────────
        confirm_card = ttk.Frame(main_frame)
        confirm_card.pack(fill="x", padx=16, pady=(0, 10))

        # Notes field
        ttk.Label(confirm_card, text="Note:", font=("Helvetica", 9, "bold")).pack(side="left", padx=(0, 6))
        self.receiving_notes_var = tk.StringVar()
        ttk.Entry(confirm_card, textvariable=self.receiving_notes_var, width=40).pack(side="left", padx=(0, 10))

        # Confirm button
        ttk.Button(
            confirm_card,
            text="✔  Conferma Ricevimento",
            command=self._close_receipt_bulk,
            style="Accent.TButton",
            width=22,
        ).pack(side="right", padx=(4, 0))

        # ── SECTION 3 — RECEIPT HISTORY ──────────────────────────────────────
        history_card = ttk.LabelFrame(main_frame, text="  🕐  Storico Ricevimenti  —  Articoli recentemente processati", padding=(10, 6))
        history_card.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        h_toolbar = ttk.Frame(history_card)
        h_toolbar.pack(fill="x", pady=(0, 6))

        ttk.Label(h_toolbar, text="🔍").pack(side="left")
        self.history_filter_sku_var = tk.StringVar()
        history_filter_ac = AutocompleteEntry(
            h_toolbar,
            textvariable=self.history_filter_sku_var,
            items_callback=self._filter_sku_items_simple,
            width=22,
        )
        history_filter_ac.pack(side="left", padx=(2, 6))
        ttk.Label(h_toolbar, text="Filtra per SKU", foreground="gray", font=("Helvetica", 9)).pack(side="left")

        ttk.Button(h_toolbar, text="Applica", command=self._refresh_receiving_history).pack(side="right", padx=(4, 0))
        ttk.Button(h_toolbar, text="✕ Cancella", command=self._clear_history_filter).pack(side="right", padx=(4, 0))

        h_tv_frame = ttk.Frame(history_card)
        h_tv_frame.pack(fill="both", expand=True)

        h_scroll = ttk.Scrollbar(h_tv_frame, orient="vertical")
        h_scroll.pack(side="right", fill="y")

        h_cols = ("Document ID", "Receipt ID", "Date", "SKU", "Qty Received", "Receipt Date", "Order IDs")
        self.receiving_history_treeview = ttk.Treeview(
            h_tv_frame,
            columns=h_cols,
            show="headings",
            height=7,
            yscrollcommand=h_scroll.set,
        )
        h_scroll.config(command=self.receiving_history_treeview.yview)

        h_col_cfg = [
            ("Document ID",  "Documento",        tk.W,      110),
            ("Receipt ID",   "ID Ricevimento",   tk.W,      120),
            ("Date",         "Data Reg.",         tk.CENTER,  90),
            ("SKU",          "SKU",               tk.W,       90),
            ("Qty Received", "Q.tà",              tk.CENTER,  60),
            ("Receipt Date", "Data Ric.",         tk.CENTER,  90),
            ("Order IDs",    "Ordini Collegati",  tk.W,      220),
        ]
        for col_id, heading, _anchor, width in h_col_cfg:
            anchor = cast(Literal["w", "center", "e"], _anchor)
            self.receiving_history_treeview.column(col_id, anchor=anchor, width=width, stretch=(col_id == "Order IDs"))
            self.receiving_history_treeview.heading(col_id, text=heading, anchor=anchor)

        self.receiving_history_treeview.pack(fill="both", expand=True)

        # ── Initial data load ─────────────────────────────────────────────────
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
        """Calculate and display pending orders at per-order granularity."""
        # Reset edits
        self.pending_qty_edits = {}
        self.pending_expiry_edits = {}  # Per-row expiry dates (keyed by treeview item_id)
        
        # Read order logs
        order_logs = self.csv_layer.read_order_logs()
        
        # Get SKU data
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Clear treeview
        self.pending_treeview.delete(*self.pending_treeview.get_children())
        
        for log in order_logs:
            sku = log.get("sku", "")
            if not sku:
                continue
            status = log.get("status", "PENDING")
            
            # Only show PENDING orders
            if status != "PENDING":
                continue
            
            order_id = log.get("order_id")
            qty_ordered = int(log.get("qty_ordered", 0))
            qty_received = int(log.get("qty_received", 0))
            receipt_date_str = log.get("receipt_date", "")
            
            qty_pending = max(0, qty_ordered - qty_received)
            if qty_pending <= 0:
                continue
            
            sku_obj = skus_by_id.get(sku)
            description = sku_obj.description if sku_obj else "N/A"
            pack_size = getattr(sku_obj, "pack_size", 1) if sku_obj else 1
            
            colli_ordinati = qty_ordered // pack_size
            # Pre-fill "Colli Ricevuti" with the full ordered quantity so the user
            # only needs to reduce it if partial; this also pre-populates the edits
            # dict so Confirm works without requiring an explicit click per row.
            colli_ricevuti_prefill = colli_ordinati

            item_id = self.pending_treeview.insert(
                "",
                "end",
                values=(
                    order_id,
                    sku,
                    description,
                    pack_size,
                    colli_ordinati,
                    colli_ricevuti_prefill,
                    0,  # Colli Sospesi = 0 because we pre-assume full receipt
                    receipt_date_str,
                    "",  # Scadenza — empty until user edits
                ),
                tags=("complete",),
            )
            # Pre-populate edits dict with full ordered quantity (pezzi)
            self.pending_qty_edits[item_id] = colli_ordinati * pack_size
    
    def _on_pending_cell_click(self, event):
        """Edita quantità ricevuta (inline) o data scadenza (popup) con click singolo."""
        region = self.pending_treeview.identify("region", event.x, event.y)
        if region != "cell":
            return
        
        column = self.pending_treeview.identify_column(event.x)
        item_id = self.pending_treeview.identify_row(event.y)
        
        if not item_id:
            return
        
        values = self.pending_treeview.item(item_id)["values"]
        
        # ── Colonna "Colli Ricevuti" (#6) ───────────────────────────────────
        if column == "#6":
            self._start_inline_qty_edit(item_id, event)
            return
        
        # ── Colonna "Scadenza" (#9) ──────────────────────────────────────────
        if column == "#9":
            sku = values[1]
            skus_by_id = {s.sku: s for s in self.csv_layer.read_skus()}
            sku_obj = skus_by_id.get(sku)
            if not sku_obj or not sku_obj.has_expiry_label:
                return  # Field disabled for non-labelled SKUs
            self._start_inline_expiry_edit(item_id)

    def _start_inline_qty_edit(self, item_id: str, event) -> None:
        """Place a floating Entry widget over the 'Colli Ricevuti' cell for inline editing."""
        bbox = self.pending_treeview.bbox(item_id, column="#6")
        if not bbox:
            return
        x, y, w, h = bbox

        values = self.pending_treeview.item(item_id)["values"]
        pack_size = int(values[3])
        colli_ordinati = int(values[4])
        current_colli = int(values[5])

        var = tk.StringVar(value=str(current_colli))
        entry = ttk.Entry(self.pending_treeview, textvariable=var, justify="center", width=8)
        entry.place(x=x, y=y, width=w, height=h)
        entry.select_range(0, tk.END)
        entry.focus_set()

        def _commit(event=None):
            try:
                new_colli = max(0, int(var.get()))
            except ValueError:
                entry.destroy()
                return
            qty_pz = new_colli * pack_size
            new_vals = list(values)
            new_vals[5] = str(new_colli)
            new_vals[6] = str(max(0, colli_ordinati - new_colli))
            tag = "complete" if new_colli >= colli_ordinati else "edited"
            self.pending_treeview.item(item_id, values=new_vals, tags=(tag,))
            self.pending_qty_edits[item_id] = qty_pz
            entry.destroy()

        def _cancel(event=None):
            entry.destroy()

        entry.bind("<Return>", _commit)
        entry.bind("<Tab>", _commit)
        entry.bind("<FocusOut>", _commit)
        entry.bind("<Escape>", _cancel)

    def _start_inline_expiry_edit(self, item_id: str) -> None:
        """Place a floating DateEntry (or plain Entry) over the 'Scadenza' cell for inline editing."""
        bbox = self.pending_treeview.bbox(item_id, column="#9")
        if not bbox:
            return
        x, y, w, h = bbox

        values = self.pending_treeview.item(item_id)["values"]
        current_expiry = self.pending_expiry_edits.get(item_id, "")
        initial_date = None
        if current_expiry:
            try:
                initial_date = date.fromisoformat(current_expiry)
            except ValueError:
                pass

        def _store_and_close(new_expiry: str, widget) -> None:
            if not widget.winfo_exists():
                return
            if new_expiry:
                self.pending_expiry_edits[item_id] = new_expiry
                new_vals = list(values)
                new_vals[8] = new_expiry
                tags = self.pending_treeview.item(item_id, "tags")
                self.pending_treeview.item(item_id, values=new_vals, tags=tags or ("edited",))
            widget.destroy()

        if TKCALENDAR_AVAILABLE:
            kw: dict = {"date_pattern": "yyyy-mm-dd"}
            if initial_date:
                kw.update({"year": initial_date.year, "month": initial_date.month, "day": initial_date.day})
            cal = DateEntry(self.pending_treeview, **kw)  # type: ignore[misc]
            cal.place(x=x, y=y, width=max(w, 110), height=h)
            cal.focus_set()
            # Auto-open the dropdown calendar
            try:
                cal.drop_down()
            except AttributeError:
                pass

            def _commit_cal(event=None):
                if cal.winfo_exists():
                    _store_and_close(cal.get_date().isoformat(), cal)

            def _focusout_cal(event=None):
                # Delay so <<DateEntrySelected>> fires before we commit on focus loss
                cal.after(200, lambda: _commit_cal() if cal.winfo_exists() else None)

            cal.bind("<<DateEntrySelected>>", _commit_cal)
            cal.bind("<Return>", _commit_cal)
            cal.bind("<Escape>", lambda e: cal.destroy() if cal.winfo_exists() else None)
            cal.bind("<FocusOut>", _focusout_cal)
        else:
            # Fallback when tkcalendar is not installed: plain Entry for YYYY-MM-DD
            var = tk.StringVar(value=current_expiry)
            entry = ttk.Entry(self.pending_treeview, textvariable=var, justify="center")
            entry.place(x=x, y=y, width=max(w, 110), height=h)
            entry.select_range(0, tk.END)
            entry.focus_set()

            def _commit_text(event=None):
                new_expiry = var.get().strip()
                if new_expiry:
                    try:
                        date.fromisoformat(new_expiry)
                    except ValueError:
                        entry.destroy()
                        messagebox.showerror("Formato Data", "Formato non valido. Usa YYYY-MM-DD.")
                        return
                _store_and_close(new_expiry, entry)

            entry.bind("<Return>", _commit_text)
            entry.bind("<Tab>", _commit_text)
            entry.bind("<FocusOut>", _commit_text)
            entry.bind("<Escape>", lambda e: entry.destroy())

    def _close_receipt_bulk(self):
        """Chiudi ricevimento per tutte le quantità modificate con tracciabilità documento."""
        if not self.pending_qty_edits:
            messagebox.showwarning(
                "Nessuna Modifica",
                "Nessuna quantità ricevuta modificata.\n\nModifica le quantità nella tabella prima di confermare.",
            )
            return
        
        # Richiedi numero documento (DDT/fattura)
        from tkinter import simpledialog
        document_id = simpledialog.askstring(
            "Numero Documento",
            "Inserisci numero documento (es. DDT-2026-001, INV-12345):",
            initialvalue=f"DDT-{date.today().strftime('%Y%m%d')}",
        )
        
        if not document_id:
            return  # Utente ha annullato
        
        # Conferma
        confirm = messagebox.askyesno(
            "Conferma Ricevimento",
            f"Confermare ricevimento per {len(self.pending_qty_edits)} SKU modificati?\n"
            f"Documento: {document_id}\n\n"
            f"Questa azione creerà eventi RECEIPT nel ledger.",
        )
        
        if not confirm:
            return
        
        receipt_date_obj = date.today()
        
        # Prepara items per il nuovo metodo
        items = []
        for item_id, new_qty_received in self.pending_qty_edits.items():
            if new_qty_received <= 0:
                continue  # Skip se qty = 0
            
            values = self.pending_treeview.item(item_id)["values"]
            sku = values[1]
            expiry_date_for_item = self.pending_expiry_edits.get(item_id, "")
            
            items.append({
                "sku": sku,
                "qty_received": new_qty_received,
                "order_ids": [],  # FIFO allocation automatica
                "expiry_date": expiry_date_for_item,  # Per-row expiry (only used for has_expiry_label SKUs)
            })
        
        if not items:
            messagebox.showwarning("Nessun Articolo", "Nessun articolo da ricevere (tutte le quantità sono 0).")
            return
        
        try:
            # Usa il nuovo metodo con tracciabilità documento
            transactions, already_processed, order_updates = self.receiving_workflow.close_receipt_by_document(
                document_id=document_id,
                receipt_date=receipt_date_obj,
                items=items,
                notes=self.receiving_notes_var.get().strip() or "Bulk receiving via GUI",
            )
            
            if already_processed:
                messagebox.showwarning(
                    "Documento Già Processato",
                    f"Il documento '{document_id}' è già stato ricevuto.\n\n"
                    f"Nessuna modifica apportata (idempotenza).",
                )
            else:
                # Mostra riepilogo
                summary = f"✅ Ricevimento completato con successo!\n\n"
                summary += f"📄 Documento: {document_id}\n"
                summary += f"📦 Articoli ricevuti: {len(items)}\n"
                summary += f"📝 Transazioni create: {len(transactions)}\n"
                summary += f"📋 Ordini aggiornati: {len(order_updates)}\n\n"
                
                if order_updates:
                    summary += "Stato ordini:\n"
                    for order_id, update in list(order_updates.items())[:5]:  # Max 5
                        summary += f"  • {order_id}: {update['qty_received_total']}/{update['qty_ordered']} pz → {update['new_status']}\n"
                    
                    if len(order_updates) > 5:
                        summary += f"  ... e altri {len(order_updates) - 5} ordini\n"
                
                messagebox.showinfo("Successo", summary)
                
                logger.info(f"Bulk receiving completed: document_id={document_id}, items={len(items)}, orders_updated={len(order_updates)}")
        
        except Exception as e:
            logger.error(f"Bulk receiving failed: {str(e)}", exc_info=True)
            messagebox.showerror(
                "Errore durante ricevimento",
                f"Errore durante l'elaborazione del documento '{document_id}':\n\n{str(e)}",
            )
            return
        
        # Refresh views
        self._refresh_pending_orders()
        self._refresh_receiving_history()
    
    def _refresh_receiving_history(self):
        """Refresh receiving history table with document traceability."""
        logs = self.csv_layer.read_receiving_logs()
        
        # Apply filter
        filter_sku = self.history_filter_sku_var.get().strip().lower()
        if filter_sku:
            logs = [log for log in logs if filter_sku in log.get("sku", "").lower()]
        
        # Populate table
        self.receiving_history_treeview.delete(*self.receiving_history_treeview.get_children())
        
        for log in logs:
            document_id = log.get("document_id", "")
            receipt_id = log.get("receipt_id", "")
            date_str = log.get("date", "")
            sku = log.get("sku", "")
            qty_received = log.get("qty_received", "")
            receipt_date_str = log.get("receipt_date", "")
            order_ids_str = log.get("order_ids", "")
            
            # Format order_ids for display (limit length)
            if order_ids_str and len(order_ids_str) > 50:
                order_ids_display = order_ids_str[:47] + "..."
            else:
                order_ids_display = order_ids_str
            
            self.receiving_history_treeview.insert(
                "",
                "end",
                values=(document_id, receipt_id, date_str, sku, qty_received, receipt_date_str, order_ids_display),
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
    
    def _build_exception_tab(self):
        """Build Exception tab — Master-Detail split layout.

        LEFT (55%) : Notebook with Triage + Storico tabs.
        RIGHT (45%): SKU-context card + exception form card + history-action card.
        Zero changes to business logic; only layout/wiring updated.
        """
        root_frame = ttk.Frame(self.exception_tab)
        root_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # ── Title bar ──────────────────────────────────────────────────────
        title_bar = ttk.Frame(root_frame)
        title_bar.pack(side="top", fill="x", pady=(0, 8))
        ttk.Label(title_bar, text="4️⃣ Gestione Eccezioni",
                  font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Label(title_bar, text="(Scarti, correzioni, merce non consegnata)",
                  font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))

        # ── PanedWindow (horizontal split) ────────────────────────────────
        self.ex_paned = ttk.PanedWindow(root_frame, orient="horizontal")
        self.ex_paned.pack(fill="both", expand=True)

        # ══════════════════════════════════════════════════════════════════
        # LEFT — MASTER (Notebook: Triage + Storico)
        # ══════════════════════════════════════════════════════════════════
        master_outer = ttk.Frame(self.ex_paned)
        self.ex_paned.add(master_outer, weight=55)

        self.ex_master_nb = ttk.Notebook(master_outer)
        self.ex_master_nb.pack(fill="both", expand=True)

        # ── Tab 1: Triage ──────────────────────────────────────────────────
        triage_tab = ttk.Frame(self.ex_master_nb, padding=(8, 6))
        self.ex_master_nb.add(triage_tab, text="🔍 Triage")

        # Row 1 – filter checkboxes
        filter_row1 = ttk.Frame(triage_tab)
        filter_row1.pack(fill="x", pady=(0, 4))

        self.filter_oos_var   = tk.BooleanVar(value=True)
        self.filter_otif_var  = tk.BooleanVar(value=True)
        self.filter_wmape_var = tk.BooleanVar(value=True)
        self.filter_perish_var = tk.BooleanVar(value=True)

        ttk.Checkbutton(filter_row1, text="OOS Rate Alto",      variable=self.filter_oos_var).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(filter_row1, text="OTIF/Unfulfilled",   variable=self.filter_otif_var).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(filter_row1, text="WMAPE Alto",         variable=self.filter_wmape_var).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(filter_row1, text="Perishability",      variable=self.filter_perish_var).pack(side="left")

        # Row 2 – threshold inputs + apply button
        filter_row2 = ttk.Frame(triage_tab)
        filter_row2.pack(fill="x", pady=(0, 6))

        ttk.Label(filter_row2, text="OOS%>",     font=("Helvetica", 8)).pack(side="left")
        self.threshold_oos_var = tk.StringVar(value="15.0")
        ttk.Entry(filter_row2, textvariable=self.threshold_oos_var, width=5).pack(side="left", padx=(2, 8))

        ttk.Label(filter_row2, text="OTIF%<",    font=("Helvetica", 8)).pack(side="left")
        self.threshold_otif_var = tk.StringVar(value="80.0")
        ttk.Entry(filter_row2, textvariable=self.threshold_otif_var, width=5).pack(side="left", padx=(2, 8))

        ttk.Label(filter_row2, text="WMAPE%>",   font=("Helvetica", 8)).pack(side="left")
        self.threshold_wmape_var = tk.StringVar(value="50.0")
        ttk.Entry(filter_row2, textvariable=self.threshold_wmape_var, width=5).pack(side="left", padx=(2, 8))

        ttk.Label(filter_row2, text="ShelfLife<", font=("Helvetica", 8)).pack(side="left")
        self.threshold_shelf_var = tk.StringVar(value="7")
        ttk.Entry(filter_row2, textvariable=self.threshold_shelf_var, width=4).pack(side="left", padx=(2, 8))

        ttk.Button(filter_row2, text="🔄 Applica",
                   command=self._refresh_smart_exceptions).pack(side="right")

        # Count label (above treeview, outside it)
        self.ex_triage_count_lbl = ttk.Label(
            triage_tab, text="SKU trovati: —",
            font=("Helvetica", 8, "italic"), foreground="gray")
        self.ex_triage_count_lbl.pack(anchor="w", pady=(0, 3))

        # Triage treeview
        triage_tv_frame = ttk.Frame(triage_tab)
        triage_tv_frame.pack(fill="both", expand=True)

        triage_sby = ttk.Scrollbar(triage_tv_frame, orient="vertical")
        triage_sbx = ttk.Scrollbar(triage_tv_frame, orient="horizontal")
        triage_sby.pack(side="right", fill="y")
        triage_sbx.pack(side="bottom", fill="x")

        self.smart_exception_treeview = ttk.Treeview(
            triage_tv_frame,
            columns=("sku", "description", "oos_rate", "otif", "unfulfilled",
                     "wmape", "shelf_life", "stock", "reason"),
            show="headings",
            yscrollcommand=triage_sby.set,
            xscrollcommand=triage_sbx.set,
            height=14,
            selectmode="browse",
        )
        triage_sby.config(command=self.smart_exception_treeview.yview)
        triage_sbx.config(command=self.smart_exception_treeview.xview)
        self.smart_exception_treeview.pack(side="left", fill="both", expand=True)

        for col, label, width, anchor in [
            ("sku",        "SKU",          90, "w"),
            ("description","Descrizione", 155, "w"),
            ("oos_rate",   "OOS%",         58, "center"),
            ("otif",       "OTIF%",        58, "center"),
            ("unfulfilled","Unful.",        52, "center"),
            ("wmape",      "WMAPE%",       68, "center"),
            ("shelf_life", "ShelfLife",    68, "center"),
            ("stock",      "Stock",        62, "center"),
            ("reason",     "Motivo Alert",210, "w"),
        ]:
            self.smart_exception_treeview.heading(col, text=label)
            self.smart_exception_treeview.column(col, width=width, anchor=anchor)  # type: ignore[arg-type]

        self.smart_exception_treeview.bind(
            "<<TreeviewSelect>>", self._on_select_triage_row)
        self.smart_exception_treeview.bind(
            "<Double-1>", lambda e: self._open_sku_in_admin_from_smart())

        # ── Tab 2: Storico ─────────────────────────────────────────────────
        history_tab = ttk.Frame(self.ex_master_nb, padding=(8, 6))
        self.ex_master_nb.add(history_tab, text="📋 Storico")

        hist_toolbar = ttk.Frame(history_tab)
        hist_toolbar.pack(fill="x", pady=(0, 6))

        ttk.Label(hist_toolbar, text="Data:", font=("Helvetica", 9)).pack(side="left")
        self.exception_view_date_var = tk.StringVar(value=self.exception_date.isoformat())
        if TKCALENDAR_AVAILABLE:
            DateEntry(  # type: ignore[misc]
                hist_toolbar,
                textvariable=self.exception_view_date_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).pack(side="left", padx=(4, 6))
        else:
            ttk.Entry(hist_toolbar, textvariable=self.exception_view_date_var,
                      width=14).pack(side="left", padx=(4, 6))

        ttk.Button(hist_toolbar, text="🔄 Aggiorna",
                   command=self._refresh_exception_tab).pack(side="left", padx=(0, 4))
        ttk.Button(hist_toolbar, text="📅 Oggi",
                   command=self._set_exception_today).pack(side="left")

        # History count label
        self.ex_history_count_lbl = ttk.Label(
            history_tab, text="",
            font=("Helvetica", 8, "italic"), foreground="gray")
        self.ex_history_count_lbl.pack(anchor="w", pady=(0, 3))

        # History treeview
        hist_tv_frame = ttk.Frame(history_tab)
        hist_tv_frame.pack(fill="both", expand=True)

        hist_sb = ttk.Scrollbar(hist_tv_frame)
        hist_sb.pack(side="right", fill="y")

        self.exception_treeview = ttk.Treeview(
            hist_tv_frame,
            columns=("Type", "SKU", "Qty", "Notes", "Date"),
            height=16,
            yscrollcommand=hist_sb.set,
            show="headings",
            selectmode="browse",
        )
        hist_sb.config(command=self.exception_treeview.yview)

        self.exception_treeview.column("Type",  width=100, anchor="w")
        self.exception_treeview.column("SKU",   width=110, anchor="w")
        self.exception_treeview.column("Qty",   width=70,  anchor="center")
        self.exception_treeview.column("Notes", width=200, anchor="w")
        self.exception_treeview.column("Date",  width=100, anchor="center")

        self.exception_treeview.heading("Type",  text="Tipo")
        self.exception_treeview.heading("SKU",   text="SKU")
        self.exception_treeview.heading("Qty",   text="Qtà")
        self.exception_treeview.heading("Notes", text="Note")
        self.exception_treeview.heading("Date",  text="Data")

        self.exception_treeview.pack(fill="both", expand=True)
        self.exception_treeview.bind(
            "<<TreeviewSelect>>", self._on_select_history_row)

        # ══════════════════════════════════════════════════════════════════
        # RIGHT — DETAIL (Context card + Form card + Action card)
        # ══════════════════════════════════════════════════════════════════
        detail_outer = ttk.Frame(self.ex_paned, padding=(12, 0, 8, 0))
        self.ex_paned.add(detail_outer, weight=45)

        detail_outer.rowconfigure(0, weight=0)
        detail_outer.rowconfigure(1, weight=0)
        detail_outer.rowconfigure(2, weight=0)
        detail_outer.columnconfigure(0, weight=1)

        # ── Card 1: Contesto SKU ──────────────────────────────────────────
        ctx_lf = ttk.LabelFrame(detail_outer, text="📦 Contesto SKU", padding=10)
        ctx_lf.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctx_lf.columnconfigure(1, weight=1)
        ctx_lf.columnconfigure(3, weight=1)

        ttk.Label(ctx_lf, text="SKU:",        font=("Helvetica", 8, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.ex_ctx_sku_lbl  = ttk.Label(ctx_lf, text="—", foreground="#555")
        self.ex_ctx_sku_lbl.grid(row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Label(ctx_lf, text="Descrizione:", font=("Helvetica", 8, "bold")).grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.ex_ctx_desc_lbl = ttk.Label(ctx_lf, text="—", foreground="#555")
        self.ex_ctx_desc_lbl.grid(row=0, column=3, sticky="w")

        ttk.Label(ctx_lf, text="Stock:",  font=("Helvetica", 8, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.ex_ctx_stock_lbl = ttk.Label(ctx_lf, text="—", foreground="#555")
        self.ex_ctx_stock_lbl.grid(row=1, column=1, sticky="w", pady=(4, 0))

        ttk.Label(ctx_lf, text="On Order:", font=("Helvetica", 8, "bold")).grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(4, 0))
        self.ex_ctx_onorder_lbl = ttk.Label(ctx_lf, text="—", foreground="#555")
        self.ex_ctx_onorder_lbl.grid(row=1, column=3, sticky="w", pady=(4, 0))

        ttk.Label(ctx_lf, text="Shelf Life:", font=("Helvetica", 8, "bold")).grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.ex_ctx_shelf_lbl = ttk.Label(ctx_lf, text="—", foreground="#555")
        self.ex_ctx_shelf_lbl.grid(row=2, column=1, sticky="w", pady=(4, 0))

        self.ex_ctx_alert_lbl = ttk.Label(
            ctx_lf, text="", foreground="#c0392b", font=("Helvetica", 8, "bold"))
        self.ex_ctx_alert_lbl.grid(row=2, column=2, columnspan=2, sticky="w", pady=(4, 0))

        # ── Card 2: Form Eccezione ────────────────────────────────────────
        form_lf = ttk.LabelFrame(detail_outer, text="✏️ Eccezione", padding=(12, 10))
        form_lf.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        form_lf.columnconfigure(1, weight=1)

        # SKU *
        ttk.Label(form_lf, text="SKU *").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=8)
        self.exception_sku_var = tk.StringVar()
        self.exception_sku_entry = ttk.Entry(
            form_lf, textvariable=self.exception_sku_var, width=28)
        self.exception_sku_entry.grid(row=0, column=1, sticky="ew", pady=8)
        self.ex_sku_err_lbl = ttk.Label(
            form_lf, text="", foreground="#c0392b", font=("Helvetica", 8))
        self.ex_sku_err_lbl.grid(row=0, column=2, sticky="w", padx=(6, 0))

        self.exception_sku_listbox = None
        self.exception_sku_popup   = None
        self.exception_sku_map     = {}

        self.exception_sku_var.trace("w", lambda *a: self._filter_exception_sku())
        self.exception_sku_var.trace("w", lambda *a: self._validate_exception_form())
        self.exception_sku_entry.bind("<Down>",     self._on_sku_down)
        self.exception_sku_entry.bind("<Up>",       self._on_sku_up)
        self.exception_sku_entry.bind("<Return>",   self._on_sku_select)
        self.exception_sku_entry.bind("<Escape>",   self._on_sku_escape)
        self.exception_sku_entry.bind("<FocusOut>", self._on_sku_focus_out)
        self._populate_exception_sku_dropdown()

        # Quantità *
        ttk.Label(form_lf, text="Quantità *").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=8)
        qty_inner = ttk.Frame(form_lf)
        qty_inner.grid(row=1, column=1, sticky="ew", pady=8)
        self.exception_qty_var = tk.StringVar()
        ttk.Entry(qty_inner, textvariable=self.exception_qty_var, width=10).pack(side="left", padx=(0, 8))
        self.exception_qty_hint = ttk.Label(
            qty_inner, text="(scartato)", font=("Helvetica", 8, "italic"), foreground="#777")
        self.exception_qty_hint.pack(side="left")
        self.exception_qty_var.trace("w", lambda *a: self._validate_exception_form())
        self.ex_qty_err_lbl = ttk.Label(
            form_lf, text="", foreground="#c0392b", font=("Helvetica", 8))
        self.ex_qty_err_lbl.grid(row=1, column=2, sticky="w", padx=(6, 0))

        # Tipo Evento *
        ttk.Label(form_lf, text="Tipo Evento *").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=8)
        self.exception_type_var = tk.StringVar(value="WASTE")
        exc_type_cb = ttk.Combobox(
            form_lf,
            textvariable=self.exception_type_var,
            values=["WASTE", "ADJUST", "UNFULFILLED"],
            state="readonly",
            width=18,
        )
        exc_type_cb.grid(row=2, column=1, sticky="w", pady=8)
        exc_type_cb.bind("<<ComboboxSelected>>", self._on_exception_type_change)

        # Data *
        ttk.Label(form_lf, text="Data *").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=8)
        self.exception_date_var = tk.StringVar(value=self.exception_date.isoformat())
        if TKCALENDAR_AVAILABLE:
            DateEntry(  # type: ignore[misc]
                form_lf,
                textvariable=self.exception_date_var,
                width=14,
                date_pattern="yyyy-mm-dd",
            ).grid(row=3, column=1, sticky="w", pady=8)
        else:
            ttk.Entry(form_lf, textvariable=self.exception_date_var,
                      width=16).grid(row=3, column=1, sticky="w", pady=8)
        self.exception_date_var.trace("w", lambda *a: self._validate_exception_form())
        self.ex_date_err_lbl = ttk.Label(
            form_lf, text="", foreground="#c0392b", font=("Helvetica", 8))
        self.ex_date_err_lbl.grid(row=3, column=2, sticky="w", padx=(6, 0))

        # Note (optional, spans full width)
        ttk.Label(form_lf, text="Note").grid(row=4, column=0, sticky="nw", padx=(0, 8), pady=8)
        self.exception_notes_var = tk.StringVar()
        ttk.Entry(form_lf, textvariable=self.exception_notes_var).grid(
            row=4, column=1, columnspan=2, sticky="ew", pady=8)

        # CTA row
        cta_frame = ttk.Frame(form_lf)
        cta_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        self.exception_submit_btn = ttk.Button(
            cta_frame, text="✓ Salva Eccezione",
            command=self._submit_exception, state="disabled")
        self.exception_submit_btn.pack(side="left", padx=(0, 8))

        ttk.Button(cta_frame, text="✗ Pulisci",
                   command=self._clear_exception_form).pack(side="left")

        self.exception_validation_label = ttk.Label(
            cta_frame, text="", font=("Helvetica", 8), foreground="#c0392b")
        self.exception_validation_label.pack(side="left", padx=(12, 0))

        # Tab-order enforced via grid row order (SKU→Qty→Tipo→Data→Note→Salva)

        # ── Card 3: Azioni sullo Storico ──────────────────────────────────
        action_lf = ttk.LabelFrame(
            detail_outer, text="🗑️ Azione su Evento Selezionato", padding=10)
        action_lf.grid(row=2, column=0, sticky="ew")

        self.ex_action_summary_lbl = ttk.Label(
            action_lf,
            text="Seleziona una riga nello Storico per abilitare le azioni.",
            font=("Helvetica", 8, "italic"),
            foreground="gray",
            wraplength=330,
        )
        self.ex_action_summary_lbl.pack(anchor="w", pady=(0, 8))

        action_btn_row = ttk.Frame(action_lf)
        action_btn_row.pack(fill="x")

        self.ex_revert_one_btn = ttk.Button(
            action_btn_row,
            text="↩ Annulla evento selezionato",
            command=self._revert_selected_exception,
            state="disabled",
        )
        self.ex_revert_one_btn.pack(side="left", padx=(0, 8))

        self.ex_revert_all_btn = ttk.Button(
            action_btn_row,
            text="⚠ Annulla tutto (giorno)…",
            command=self._revert_bulk_exceptions,
            state="disabled",
        )
        self.ex_revert_all_btn.pack(side="left")

    # ── New master-detail binding methods ─────────────────────────────────

    def _on_select_triage_row(self, event=None):
        """Fill SKU form and context card when a triage row is selected."""
        selected = self.smart_exception_treeview.selection()
        if not selected:
            return
        values = self.smart_exception_treeview.item(selected[0])["values"]
        sku = str(values[0]) if values else ""
        if not sku:
            return
        # Pre-fill form with display string "SKU - Description"
        skus = self.csv_layer.read_skus()
        sku_obj = next((s for s in skus if s.sku == sku), None)
        display = f"{sku_obj.sku} - {sku_obj.description}" if sku_obj else sku
        # Pause autocomplete popup while setting
        self.exception_sku_var.set(display)
        self._hide_sku_popup()
        self._update_sku_context_card(sku)

    def _on_select_history_row(self, event=None):
        """Enable action card and fill context when a history row is selected."""
        selected = self.exception_treeview.selection()
        if not selected:
            self._reset_history_action_card()
            return
        values = self.exception_treeview.item(selected[0])["values"]
        if not values:
            self._reset_history_action_card()
            return
        event_type, sku, qty, notes, event_date = values
        self.ex_action_summary_lbl.config(
            text=(f"Evento: {event_type}  •  SKU: {sku}"
                  f"  •  Qtà: {qty}  •  Data: {event_date}"),
            foreground="#333",
        )
        self.ex_revert_one_btn.config(state="normal")
        self.ex_revert_all_btn.config(state="normal")
        # Pre-fill SKU form
        skus = self.csv_layer.read_skus()
        sku_obj = next((s for s in skus if s.sku == sku), None)
        display = f"{sku_obj.sku} - {sku_obj.description}" if sku_obj else str(sku)
        self.exception_sku_var.set(display)
        self._hide_sku_popup()
        self._update_sku_context_card(str(sku))

    def _reset_history_action_card(self):
        """Reset action card to disabled/placeholder state."""
        if hasattr(self, "ex_action_summary_lbl"):
            self.ex_action_summary_lbl.config(
                text="Seleziona una riga nello Storico per abilitare le azioni.",
                foreground="gray",
            )
        if hasattr(self, "ex_revert_one_btn"):
            self.ex_revert_one_btn.config(state="disabled")
        if hasattr(self, "ex_revert_all_btn"):
            self.ex_revert_all_btn.config(state="disabled")

    def _update_sku_context_card(self, sku: str):
        """Populate the context card for the given SKU code."""
        if not hasattr(self, "ex_ctx_sku_lbl"):
            return
        skus = self.csv_layer.read_skus()
        sku_obj = next((s for s in skus if s.sku == sku), None)
        if not sku_obj:
            for lbl in (self.ex_ctx_sku_lbl, self.ex_ctx_desc_lbl,
                        self.ex_ctx_stock_lbl, self.ex_ctx_onorder_lbl, self.ex_ctx_shelf_lbl):
                lbl.config(text="—")
            self.ex_ctx_alert_lbl.config(text="")
            return
        # Calculate stock
        try:
            from src.domain.ledger import StockCalculator
            txns = self.csv_layer.read_transactions()
            stock = StockCalculator.calculate_asof(
                sku=sku,
                asof_date=date.today() + timedelta(days=1),
                transactions=txns,
                sales_records=None,
            )
            on_hand  = stock.on_hand
            on_order = stock.on_order
        except Exception:
            on_hand, on_order = "?", "?"
        self.ex_ctx_sku_lbl.config(    text=sku_obj.sku)
        self.ex_ctx_desc_lbl.config(   text=(sku_obj.description or "")[:32])
        self.ex_ctx_stock_lbl.config(  text=str(on_hand))
        self.ex_ctx_onorder_lbl.config(text=str(on_order))
        shelf = sku_obj.shelf_life_days
        self.ex_ctx_shelf_lbl.config(  text=f"{shelf}g" if shelf else "—")
        # Alerts
        alerts = []
        if isinstance(on_hand, int) and on_hand <= 0:
            alerts.append("⚠ Stock esaurito")
        if shelf and isinstance(on_hand, int) and on_hand > 0 and shelf < 7:
            alerts.append("⚠ Shelf life critica")
        self.ex_ctx_alert_lbl.config(text="  ".join(alerts))

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
        self.exception_sku_listbox.delete(0, tk.END)  # type: ignore[union-attr]
        for item in items:
            self.exception_sku_listbox.insert(tk.END, item)  # type: ignore[union-attr]
        
        # Seleziona primo item
        if items:
            self.exception_sku_listbox.selection_clear(0, tk.END)  # type: ignore[union-attr]
            self.exception_sku_listbox.selection_set(0)  # type: ignore[union-attr]
            self.exception_sku_listbox.activate(0)  # type: ignore[union-attr]
        
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
        index = self.exception_sku_listbox.nearest(event.y)  # type: ignore[union-attr]
        if index >= 0:
            selected_text = self.exception_sku_listbox.get(index)  # type: ignore[union-attr]
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
        """Validate exception form in real-time; drive inline field labels and CTA."""
        sku_display = self.exception_sku_var.get().strip()
        qty_str     = self.exception_qty_var.get().strip()
        date_str    = self.exception_date_var.get().strip()

        # Extract SKU code from "SKU - Description" display format
        if " - " in sku_display:
            sku = sku_display.split(" - ")[0].strip()
        else:
            sku = sku_display

        # ── Per-field validation ────────────────────────────────────────
        sku_err = "" if sku else "Campo obbligatorio"
        qty_err = ""
        if not qty_str:
            qty_err = "Campo obbligatorio"
        else:
            try:
                int(qty_str)
            except ValueError:
                qty_err = "Deve essere un intero"

        date_err = ""
        if not date_str:
            date_err = "Campo obbligatorio"
        else:
            try:
                date.fromisoformat(date_str)
            except ValueError:
                date_err = "Formato YYYY-MM-DD"

        # ── Update inline error labels (only if widgets exist) ─────────
        if hasattr(self, "ex_sku_err_lbl"):
            self.ex_sku_err_lbl.config(text=sku_err)
        if hasattr(self, "ex_qty_err_lbl"):
            self.ex_qty_err_lbl.config(text=qty_err)
        if hasattr(self, "ex_date_err_lbl"):
            self.ex_date_err_lbl.config(text=date_err)

        # ── Overall CTA state ──────────────────────────────────────────
        all_ok = not (sku_err or qty_err or date_err)
        self.exception_submit_btn.config(state="normal" if all_ok else "disabled")
        if all_ok:
            self.exception_validation_label.config(text="✓ Pronto", foreground="#27ae60")
        else:
            errors = [e for e in (sku_err, qty_err, date_err) if e]
            self.exception_validation_label.config(
                text=f"{len(errors)} campo/i da correggere", foreground="#c0392b")
    
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

        # Update count label and reset action card
        if hasattr(self, "ex_history_count_lbl"):
            n = len(exception_txns)
            if n == 0:
                self.ex_history_count_lbl.config(
                    text="Nessuna eccezione in questa data.", foreground="gray")
            else:
                self.ex_history_count_lbl.config(
                    text=f"Eccezioni trovate: {n}", foreground="#333")
        self._reset_history_action_card()

    def _set_exception_today(self):
        """Set exception view date to today."""
        today = date.today()
        self.exception_view_date_var.set(today.isoformat())
        self._refresh_exception_tab()
    
    def _refresh_smart_exceptions(self):
        """Refresh smart exception filters to show problematic SKUs."""
        # Clear existing table
        self.smart_exception_treeview.delete(*self.smart_exception_treeview.get_children())
        
        # Parse thresholds
        try:
            oos_threshold = float(self.threshold_oos_var.get())
            otif_threshold = float(self.threshold_otif_var.get())
            wmape_threshold = float(self.threshold_wmape_var.get())
            shelf_threshold = int(self.threshold_shelf_var.get())
        except ValueError:
            messagebox.showerror("Errore", "Soglie non valide. Usa numeri.")
            return
        
        # Get filters enabled
        filter_oos = self.filter_oos_var.get()
        filter_otif = self.filter_otif_var.get()
        filter_wmape = self.filter_wmape_var.get()
        filter_perish = self.filter_perish_var.get()
        
        # If no filters enabled, show message and return
        if not any([filter_oos, filter_otif, filter_wmape, filter_perish]):
            self.smart_exception_treeview.insert("", "end", values=("", "Nessun filtro attivo", "", "", "", "", "", "", "Abilita almeno un filtro sopra"))
            return
        
        # Load data sources
        all_skus = self.csv_layer.read_skus()
        kpi_daily = self.csv_layer.read_kpi_daily()  # Returns list of KPIDaily records
        current_stock_map = {}  # Map SKU -> on_hand
        transactions = self.csv_layer.read_transactions()
        
        # Calculate current stock for each SKU
        from src.domain.ledger import StockCalculator
        for sku_obj in all_skus:
            stock = StockCalculator.calculate_asof(
                sku=sku_obj.sku,
                asof_date=date.today() + timedelta(days=1),  # Include today
                transactions=transactions,
                sales_records=None,
            )
            current_stock_map[sku_obj.sku] = stock.on_hand
        
        # Build KPI map (latest KPI for each SKU)
        kpi_map = {}  # Map SKU -> latest KPI dict
        for kpi in kpi_daily:
            sku_key = kpi.get("sku", "")
            kpi_date = kpi.get("date", "")
            if sku_key and (sku_key not in kpi_map or kpi_date > kpi_map[sku_key].get("date", "")):
                kpi_map[sku_key] = kpi
        
        # Calculate unfulfilled for each SKU from current proposals (if available)
        unfulfilled_map = {}  # Map SKU -> unfulfilled_qty
        if hasattr(self, 'current_proposals') and self.current_proposals:
            for prop in self.current_proposals:
                unfulfilled_map[prop.sku] = prop.unfulfilled_qty
        
        # Filter SKUs
        problematic_skus = []
        
        for sku_obj in all_skus:
            sku = sku_obj.sku
            reasons = []
            
            # Get KPI data for this SKU
            kpi = kpi_map.get(sku)
            
            # Helper to safely convert KPI values to float
            def safe_float(value, default=0.0):
                if value is None or value == '' or value == 'None':
                    return default
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return default
            
            oos_rate = safe_float(kpi.get("oos_rate"), 0.0) if kpi else 0.0
            otif = safe_float(kpi.get("otif_rate"), 100.0) if kpi else 100.0
            wmape = safe_float(kpi.get("wmape"), 0.0) if kpi else 0.0
            
            unfulfilled = unfulfilled_map.get(sku, 0)
            shelf_life = sku_obj.shelf_life_days if sku_obj.shelf_life_days else 0
            stock = current_stock_map.get(sku, 0)
            
            # Apply filters
            passes = False
            
            if filter_oos and oos_rate > oos_threshold:
                reasons.append(f"OOS alto ({oos_rate:.1f}%)")
                passes = True
            
            if filter_otif and (otif < otif_threshold or unfulfilled > 0):
                if otif < otif_threshold and unfulfilled > 0:
                    reasons.append(f"OTIF basso ({otif:.1f}%) + Unfulfilled ({unfulfilled})")
                elif unfulfilled > 0:
                    reasons.append(f"Unfulfilled ({unfulfilled})")
                else:
                    reasons.append(f"OTIF basso ({otif:.1f}%)")
                passes = True
            
            if filter_wmape and wmape > wmape_threshold:
                reasons.append(f"WMAPE alto ({wmape:.1f}%)")
                passes = True
            
            if filter_perish and shelf_life > 0 and shelf_life < shelf_threshold and stock > 0:
                # Critical perishability: short shelf life + stock on hand
                # Approximate coverage from stock / (avg daily sales estimate)
                # Use simple ratio: if stock > shelf_life/2, flag as critical
                if stock > shelf_life * 10:  # Heuristic: stock > 10x shelf_life days is risky
                    reasons.append(f"Shelf life critica ({shelf_life}d, stock={stock})")
                    passes = True
            
            if passes:
                problematic_skus.append({
                    "sku": sku,
                    "description": sku_obj.description,
                    "oos_rate": oos_rate,
                    "otif": otif,
                    "unfulfilled": unfulfilled,
                    "wmape": wmape,
                    "shelf_life": shelf_life,
                    "stock": stock,
                    "reason": "; ".join(reasons),
                })
        
        # Sort by severity (number of reasons, then by OOS rate descending)
        problematic_skus.sort(key=lambda x: (-len(x["reason"].split(";")), -x["oos_rate"]))
        
        # Populate table
        for item in problematic_skus:
            self.smart_exception_treeview.insert(
                "",
                "end",
                values=(
                    item["sku"],
                    item["description"],
                    f"{item['oos_rate']:.1f}%" if item['oos_rate'] > 0 else "-",
                    f"{item['otif']:.1f}%" if item['otif'] < 100 else "-",
                    str(item["unfulfilled"]) if item["unfulfilled"] > 0 else "-",
                    f"{item['wmape']:.1f}%" if item['wmape'] > 0 else "-",
                    str(item["shelf_life"]) if item["shelf_life"] > 0 else "-",
                    str(item["stock"]),
                    item["reason"],
                ),
            )
        
        # Update triage count label; placeholder row only when truly empty
        if hasattr(self, "ex_triage_count_lbl"):
            n = len(problematic_skus)
            if n == 0:
                self.ex_triage_count_lbl.config(
                    text="Nessun SKU problematico trovato.", foreground="gray")
            else:
                self.ex_triage_count_lbl.config(
                    text=f"SKU trovati: {n}", foreground="#333")
        elif not problematic_skus:
            # Fallback for old layout (no count label)
            self.smart_exception_treeview.insert(
                "", "end",
                values=("", "Nessun SKU problematico trovato", "", "", "", "", "", "",
                        "Tutti gli SKU passano i filtri attivi"))

    def _open_sku_in_admin_from_smart(self):
        """Open selected SKU in Admin tab for editing (from smart exceptions table)."""
        selected = self.smart_exception_treeview.selection()
        if not selected:
            messagebox.showwarning("Nessuna Selezione", "Seleziona uno SKU problematico per aprire in Admin.")
            return
        
        item = self.smart_exception_treeview.item(selected[0])
        sku = item["values"][0]
        
        if not sku:
            return
        
        # Switch to Admin tab
        self.notebook.select(self.admin_tab)
        
        # Find SKU in admin table and select it, then open edit form
        # First, find the item in admin_treeview
        for item_id in self.admin_treeview.get_children():
            item_values = self.admin_treeview.item(item_id)["values"]
            if item_values and item_values[0] == sku:
                # Select and focus
                self.admin_treeview.selection_set(item_id)
                self.admin_treeview.focus(item_id)
                self.admin_treeview.see(item_id)
                
                # Trigger edit form
                self._edit_sku()
                break
    
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
        sku           = values[1]
        qty           = values[2]
        date_str      = values[4]

        # Map string to EventType
        event_type_map = {
            "WASTE": EventType.WASTE,
            "ADJUST": EventType.ADJUST,
            "UNFULFILLED": EventType.UNFULFILLED,
        }
        event_type = event_type_map.get(event_type_str)
        event_date = date.fromisoformat(date_str)

        # Confirm revert — explicit message per UX spec
        confirm = messagebox.askyesno(
            "Conferma Annullamento",
            (f"Stai annullando 1 evento:\n\n"
             f"  Tipo: {event_type_str}\n"
             f"  SKU:  {sku}\n"
             f"  Qtà:  {qty}\n"
             f"  Data: {date_str}\n\n"
             "Questa azione NON può essere annullata.  Confermi?"),
        )
        if not confirm:
            return
        
        # Revert
        try:
            reverted_count = self.exception_workflow.revert_exception_day(
                event_date=event_date,
                sku=sku,
                event_type=event_type,  # type: ignore
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
        popup.title("Annullamento Multiplo Eccezioni")
        popup.geometry("420x340")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        
        # Form frame
        form_frame = ttk.Frame(popup, padding=20)
        form_frame.pack(fill="both", expand=True)
        
        ttk.Label(form_frame, text="Annullamento Multiplo Eccezioni", font=("Helvetica", 12, "bold")).pack(pady=(0, 15))
        
        # Event Type
        ttk.Label(form_frame, text="Tipo Evento:", font=("Helvetica", 10)).pack(anchor="w", pady=(0, 5))
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
        ttk.Label(form_frame, text="Data:", font=("Helvetica", 10)).pack(anchor="w", pady=(0, 5))
        bulk_date_var = tk.StringVar(value=self.exception_view_date_var.get())
        if TKCALENDAR_AVAILABLE:
            DateEntry(  # type: ignore[misc]
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

            # Count matching events before confirming
            try:
                all_txns = self.csv_layer.read_transactions()
                matching = [
                    t for t in all_txns
                    if t.sku == sku and t.date == event_date
                    and t.event == event_type
                ]
                count = len(matching)
            except Exception:
                count = 0

            # Explicit risk-aware confirm dialog
            confirm = messagebox.askyesno(
                "Conferma Annullamento Multiplo",
                (f"Stai annullando TUTTI gli eventi {event_type_str}\n"
                 f"per SKU '{sku}' del {date_str}.\n\n"
                 f"  Numero eventi trovati: {count}\n"
                 f"  Questa azione NON può essere annullata.\n\n"
                 "Sei sicuro di voler procedere?"),
                parent=popup,
            )
            if not confirm:
                return
            
            # Revert
            try:
                reverted_count = self.exception_workflow.revert_exception_day(
                    event_date=event_date,
                    sku=sku,
                    event_type=event_type,  # type: ignore[arg-type]
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
        
        ttk.Button(button_frame, text="✔ Conferma Annullamento", command=do_bulk_revert).pack(side="right", padx=5)
        ttk.Button(button_frame, text="Annulla", command=popup.destroy).pack(side="right", padx=5)

    
    def _build_expiry_tab(self):
        """Build Expiry tracking tab (lots with expiry dates)."""
        main_frame = ttk.Frame(self.expiry_tab)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="⏰ Gestione Scadenze Lotti", font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Label(title_frame, text="(Tracciamento FEFO - First Expired First Out)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
        # === ALERT PANEL ===
        alert_frame = ttk.LabelFrame(main_frame, text="⚠️ Alert Scadenze", padding=10)
        alert_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Days threshold selector
        threshold_frame = ttk.Frame(alert_frame)
        threshold_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Label(threshold_frame, text="Mostra lotti in scadenza entro:").pack(side="left", padx=5)
        self.expiry_threshold_var = tk.IntVar(value=30)
        ttk.Spinbox(threshold_frame, from_=1, to=365, textvariable=self.expiry_threshold_var, width=10).pack(side="left", padx=5)
        ttk.Label(threshold_frame, text="giorni").pack(side="left", padx=5)
        ttk.Button(threshold_frame, text="🔄 Aggiorna", command=self._refresh_expiry_alerts).pack(side="left", padx=20)
        
        # Alert summary
        self.expiry_alert_label = ttk.Label(alert_frame, text="Nessun alert", font=("Helvetica", 10))
        self.expiry_alert_label.pack(side="top", pady=5)
        
        # === EXPIRING LOTS TABLE ===
        expiring_frame = ttk.LabelFrame(main_frame, text="Lotti in Scadenza", padding=5)
        expiring_frame.pack(side="top", fill="both", expand=True, pady=(0, 10))
        
        expiring_scroll = ttk.Scrollbar(expiring_frame)
        expiring_scroll.pack(side="right", fill="y")
        
        self.expiring_lots_treeview = ttk.Treeview(
            expiring_frame,
            columns=("Lot ID", "SKU", "Description", "Expiry Date", "Days Left", "Qty", "Status"),
            height=8,
            yscrollcommand=expiring_scroll.set,
        )
        expiring_scroll.config(command=self.expiring_lots_treeview.yview)
        
        self.expiring_lots_treeview.column("#0", width=0, stretch=tk.NO)
        self.expiring_lots_treeview.column("Lot ID", anchor=tk.W, width=120)
        self.expiring_lots_treeview.column("SKU", anchor=tk.W, width=80)
        self.expiring_lots_treeview.column("Description", anchor=tk.W, width=200)
        self.expiring_lots_treeview.column("Expiry Date", anchor=tk.CENTER, width=100)
        self.expiring_lots_treeview.column("Days Left", anchor=tk.CENTER, width=80)
        self.expiring_lots_treeview.column("Qty", anchor=tk.CENTER, width=80)
        self.expiring_lots_treeview.column("Status", anchor=tk.CENTER, width=100)
        
        self.expiring_lots_treeview.heading("Lot ID", text="ID Lotto", anchor=tk.W)
        self.expiring_lots_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.expiring_lots_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.expiring_lots_treeview.heading("Expiry Date", text="Scadenza", anchor=tk.CENTER)
        self.expiring_lots_treeview.heading("Days Left", text="Giorni", anchor=tk.CENTER)
        self.expiring_lots_treeview.heading("Qty", text="Quantità", anchor=tk.CENTER)
        self.expiring_lots_treeview.heading("Status", text="Stato", anchor=tk.CENTER)
        
        self.expiring_lots_treeview.pack(fill="both", expand=True)
        
        # Color tags
        self.expiring_lots_treeview.tag_configure("expired", background="#ffcccc")
        self.expiring_lots_treeview.tag_configure("critical", background="#ffe6cc")
        self.expiring_lots_treeview.tag_configure("warning", background="#ffffcc")
        
        # === ALL LOTS TABLE ===
        all_lots_frame = ttk.LabelFrame(main_frame, text="Tutti i Lotti Attivi", padding=5)
        all_lots_frame.pack(side="top", fill="both", expand=True)
        
        # Toolbar
        lot_toolbar = ttk.Frame(all_lots_frame)
        lot_toolbar.pack(side="top", fill="x", pady=(0, 5))
        ttk.Button(lot_toolbar, text="🔄 Aggiorna", command=self._refresh_all_lots).pack(side="left", padx=5)
        
        ttk.Label(lot_toolbar, text="Filtra SKU:").pack(side="left", padx=(20, 5))
        self.lot_filter_sku_var = tk.StringVar()
        ttk.Entry(lot_toolbar, textvariable=self.lot_filter_sku_var, width=15).pack(side="left", padx=5)
        ttk.Button(lot_toolbar, text="Applica", command=self._refresh_all_lots).pack(side="left", padx=5)
        
        all_lots_scroll = ttk.Scrollbar(all_lots_frame)
        all_lots_scroll.pack(side="right", fill="y")
        
        self.all_lots_treeview = ttk.Treeview(
            all_lots_frame,
            columns=("Lot ID", "SKU", "Description", "Expiry Date", "Qty", "Receipt Date"),
            height=10,
            yscrollcommand=all_lots_scroll.set,
        )
        all_lots_scroll.config(command=self.all_lots_treeview.yview)
        
        self.all_lots_treeview.column("#0", width=0, stretch=tk.NO)
        self.all_lots_treeview.column("Lot ID", anchor=tk.W, width=120)
        self.all_lots_treeview.column("SKU", anchor=tk.W, width=80)
        self.all_lots_treeview.column("Description", anchor=tk.W, width=200)
        self.all_lots_treeview.column("Expiry Date", anchor=tk.CENTER, width=100)
        self.all_lots_treeview.column("Qty", anchor=tk.CENTER, width=80)
        self.all_lots_treeview.column("Receipt Date", anchor=tk.CENTER, width=100)
        
        self.all_lots_treeview.heading("Lot ID", text="ID Lotto", anchor=tk.W)
        self.all_lots_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.all_lots_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.all_lots_treeview.heading("Expiry Date", text="Scadenza", anchor=tk.CENTER)
        self.all_lots_treeview.heading("Qty", text="Quantità", anchor=tk.CENTER)
        self.all_lots_treeview.heading("Receipt Date", text="Data Ricevimento", anchor=tk.CENTER)
        
        self.all_lots_treeview.pack(fill="both", expand=True)
        
        # Initial load
        self._refresh_expiry_alerts()
        self._refresh_all_lots()
    
    def _refresh_expiry_alerts(self):
        """Refresh expiring lots alert table."""
        threshold_days = self.expiry_threshold_var.get()
        
        # Get expiring + expired lots
        expiring_lots = self.csv_layer.get_expiring_lots(threshold_days)
        expired_lots = self.csv_layer.get_expired_lots()
        
        # Clear table
        self.expiring_lots_treeview.delete(*self.expiring_lots_treeview.get_children())
        
        # Get SKU descriptions
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Display expired first
        for lot in expired_lots:
            sku_obj = skus_by_id.get(lot.sku)
            description = sku_obj.description if sku_obj else "N/A"
            days_left = lot.days_until_expiry(date.today())
            
            self.expiring_lots_treeview.insert(
                "",
                "end",
                values=(lot.lot_id, lot.sku, description, lot.expiry_date.isoformat() if lot.expiry_date else "", days_left, lot.qty_on_hand, "❌ SCADUTO"),
                tags=("expired",)
            )
        
        # Then expiring
        for lot in expiring_lots:
            sku_obj = skus_by_id.get(lot.sku)
            description = sku_obj.description if sku_obj else "N/A"
            days_left = lot.days_until_expiry(date.today())
            
            # Status based on days left (use configurable thresholds)
            if days_left <= self.expiry_critical_days:
                status = "🔴 CRITICO"
                tag = "critical"
            elif days_left <= self.expiry_warning_days:
                status = "🟡 ATTENZIONE"
                tag = "warning"
            else:
                status = "🟢 OK"
                tag = ""
            
            self.expiring_lots_treeview.insert(
                "",
                "end",
                values=(lot.lot_id, lot.sku, description, lot.expiry_date.isoformat() if lot.expiry_date else "", days_left, lot.qty_on_hand, status),
                tags=(tag,) if tag else ()
            )
        
        # Update alert label
        total_expiring = len(expiring_lots) + len(expired_lots)
        if total_expiring == 0:
            self.expiry_alert_label.config(text="✅ Nessun lotto in scadenza", foreground="green")
        else:
            self.expiry_alert_label.config(
                text=f"⚠️ {total_expiring} lotti richiedono attenzione ({len(expired_lots)} scaduti, {len(expiring_lots)} in scadenza)",
                foreground="red" if expired_lots else "orange"
            )
    
    def _refresh_all_lots(self):
        """Refresh all active lots table."""
        lots = self.csv_layer.read_lots()
        
        # Apply SKU filter
        sku_filter = self.lot_filter_sku_var.get().strip().lower()
        if sku_filter:
            lots = [lot for lot in lots if sku_filter in lot.sku.lower()]
        
        # Clear table
        self.all_lots_treeview.delete(*self.all_lots_treeview.get_children())
        
        # Get SKU descriptions
        skus_by_id = {sku.sku: sku for sku in self.csv_layer.read_skus()}
        
        # Sort by expiry date (None last)
        lots.sort(key=lambda lot: (lot.expiry_date is None, lot.expiry_date or date.max))
        
        for lot in lots:
            sku_obj = skus_by_id.get(lot.sku)
            description = sku_obj.description if sku_obj else "N/A"
            
            self.all_lots_treeview.insert(
                "",
                "end",
                values=(
                    lot.lot_id,
                    lot.sku,
                    description,
                    lot.expiry_date.isoformat() if lot.expiry_date else "No expiry",
                    lot.qty_on_hand,
                    lot.receipt_date.isoformat(),
                ),
            )
    
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
        
        # Toggle for showing out-of-assortment SKUs
        self.show_out_of_assortment_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar_frame, 
            text="Mostra fuori assortimento", 
            variable=self.show_out_of_assortment_var,
            command=self._refresh_admin_tab
        ).pack(side="left", padx=20)
        
        # SKU table
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill="both", expand=True)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.admin_treeview = ttk.Treeview(
            table_frame,
            columns=("SKU", "Description", "EAN", "Famiglia", "Sotto-famiglia", "Status"),
            height=20,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.admin_treeview.yview)
        
        self.admin_treeview.column("#0", width=0, stretch=tk.NO)
        self.admin_treeview.column("SKU", anchor=tk.W, width=110)
        self.admin_treeview.column("Description", anchor=tk.W, width=260)
        self.admin_treeview.column("EAN", anchor=tk.W, width=110)
        self.admin_treeview.column("Famiglia", anchor=tk.W, width=120)
        self.admin_treeview.column("Sotto-famiglia", anchor=tk.W, width=130)
        self.admin_treeview.column("Status", anchor=tk.CENTER, width=110)
        
        self.admin_treeview.heading("SKU", text="Codice SKU", anchor=tk.W)
        self.admin_treeview.heading("Description", text="Descrizione", anchor=tk.W)
        self.admin_treeview.heading("EAN", text="EAN", anchor=tk.W)
        self.admin_treeview.heading("Famiglia", text="Famiglia", anchor=tk.W)
        self.admin_treeview.heading("Sotto-famiglia", text="Sotto-famiglia", anchor=tk.W)
        self.admin_treeview.heading("Status", text="Stato", anchor=tk.CENTER)
        
        self.admin_treeview.pack(fill="both", expand=True)
        
        # Bind double-click to edit
        self.admin_treeview.bind("<Double-1>", lambda e: self._edit_sku())
    
    def _refresh_admin_tab(self):
        """Refresh SKU table with all SKUs (filtered by assortment toggle)."""
        self.admin_treeview.delete(*self.admin_treeview.get_children())
        
        skus = self.csv_layer.read_skus()
        show_out = self.show_out_of_assortment_var.get()
        
        for sku in skus:
            # Filter by assortment status
            if not show_out and not sku.in_assortment:
                continue
            
            status = "In assortimento" if sku.in_assortment else "Fuori assortimento"
            # Store the actual SKU code in tags to preserve leading zeros
            # Prefix with 'sku_' to prevent tkinter from converting to number
            item_id = self.admin_treeview.insert(
                "",
                "end",
                values=(sku.sku, sku.description, sku.ean or "", sku.department, sku.category, status),
                tags=(f"sku_{sku.sku}",)  # Store original SKU in tags with prefix
            )
    
    def _search_skus(self):
        """Search SKUs by code or description (respects assortment filter)."""
        query = self.search_var.get()
        self.admin_treeview.delete(*self.admin_treeview.get_children())
        
        skus = self.csv_layer.search_skus(query)
        show_out = self.show_out_of_assortment_var.get()
        
        for sku in skus:
            # Filter by assortment status
            if not show_out and not sku.in_assortment:
                continue
            
            status = "In assortimento" if sku.in_assortment else "Fuori assortimento"
            # Store the actual SKU code in tags to preserve leading zeros
            # Prefix with 'sku_' to prevent tkinter from converting to number
            self.admin_treeview.insert(
                "",
                "end",
                values=(sku.sku, sku.description, sku.ean or "", sku.department, sku.category, status),
                tags=(f"sku_{sku.sku}",)  # Store original SKU in tags with prefix
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
        
        # Get selected SKU data - use tags to preserve original SKU (with leading zeros)
        item = self.admin_treeview.item(selected[0])
        tags = item.get("tags", [])
        
        if tags and tags[0].startswith("sku_"):
            selected_sku = tags[0][4:]  # Remove 'sku_' prefix to get original SKU
        else:
            # Fallback to values if tags not available
            values = item["values"]
            selected_sku = values[0]  # SKU code
        
        self._show_sku_form(mode="edit", sku_code=selected_sku)
    
    def _delete_sku(self):
        """Delete selected SKU after confirmation."""
        selected = self.admin_treeview.selection()
        if not selected:
            messagebox.showwarning("Nessuna Selezione", "Seleziona uno SKU da eliminare.")
            return
        
        # Get selected SKU data - use tags to preserve original SKU (with leading zeros)
        item = self.admin_treeview.item(selected[0])
        tags = item.get("tags", [])
        if tags and tags[0].startswith("sku_"):
            sku_code = tags[0][4:]  # Remove 'sku_' prefix to get original SKU
        else:
            # Fallback to values if tags not available
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
        popup.geometry("700x800")
        popup.resizable(True, True)
        
        # Center popup
        popup.transient(self.root)
        popup.grab_set()
        
        # Main container
        main_container = ttk.Frame(popup, padding=10)
        main_container.pack(fill="both", expand=True)
        
        # Search field
        search_frame = ttk.Frame(main_container)
        search_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(search_frame, text="🔍 Cerca campo:", font=("Helvetica", 10)).pack(side="left", padx=(0, 5))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var, width=40)
        search_entry.pack(side="left", padx=5)
        
        # Scrollable container
        scroll_container = ttk.Frame(main_container)
        scroll_container.pack(fill="both", expand=True, pady=10)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass  # Widget destroyed, ignore
        
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        # Unbind mousewheel when popup is destroyed
        def _on_popup_destroy():
            try:
                canvas.unbind_all("<MouseWheel>")
            except:
                pass
        popup.bind("<Destroy>", lambda e: _on_popup_destroy())
        
        # Form frame
        form_frame = ttk.Frame(scrollable_frame, padding=10)
        form_frame.pack(fill="both", expand=True)
        
        # Load existing SKU data if editing
        current_sku = None
        if mode == "edit" and sku_code:
            skus = self.csv_layer.read_skus()
            # Normalize SKU code for comparison (handle both string and numeric SKUs)
            sku_code_normalized = str(sku_code).strip()
            
            current_sku = next((s for s in skus if str(s.sku).strip() == sku_code_normalized), None)
            if not current_sku:
                messagebox.showerror("Error", f"SKU '{sku_code}' not found.")
                popup.destroy()
                return
        
        # Storage for field rows (for search filtering)
        field_rows = []
        
        def add_field_row(parent, row_num, label, description, value_var, widget_type="entry", choices=None, **kwargs):
            """Helper to add a field row with search support."""
            row_frame = ttk.Frame(parent)
            row_frame.grid(row=row_num, column=0, columnspan=2, sticky="ew", pady=5)
            
            # Configure grid
            row_frame.columnconfigure(0, weight=1, minsize=200)
            row_frame.columnconfigure(1, weight=2, minsize=300)
            
            # Label
            ttk.Label(
                row_frame,
                text=label,
                font=("Helvetica", 10, "bold")
            ).grid(row=0, column=0, sticky="w", padx=(0, 10))
            
            # Description (if provided)
            if description:
                ttk.Label(
                    row_frame,
                    text=description,
                    font=("Helvetica", 8),
                    foreground="gray",
                    wraplength=250
                ).grid(row=1, column=0, sticky="w", padx=(0, 10))
            
            # Widget
            if widget_type == "entry":
                widget = ttk.Entry(row_frame, textvariable=value_var, width=40)
                widget.grid(row=0, column=1, rowspan=2 if description else 1, sticky="ew", padx=(10, 0))
            elif widget_type == "combobox":
                widget = ttk.Combobox(row_frame, textvariable=value_var, values=choices, state="readonly", width=37)  # type: ignore[arg-type]
                widget.grid(row=0, column=1, rowspan=2 if description else 1, sticky="ew", padx=(10, 0))
            elif widget_type == "combobox_editable":
                # Editable combobox: free text + dropdown suggestions (ibrido)
                widget = ttk.Combobox(row_frame, textvariable=value_var, values=choices or [], state="normal", width=37)  # type: ignore[arg-type]
                widget.grid(row=0, column=1, rowspan=2 if description else 1, sticky="ew", padx=(10, 0))
            elif widget_type == "autocomplete":
                widget = kwargs.get("autocomplete_widget")
                widget.entry.grid(row=0, column=1, rowspan=2 if description else 1, sticky="ew", padx=(10, 0))  # type: ignore[union-attr]
            
            # Store for search
            field_rows.append({
                "frame": row_frame,
                "label": label.lower(),
                "description": description.lower() if description else ""
            })
            
            return widget
        
        # Filter function for search
        def filter_fields(*args):
            query = search_var.get().lower()
            for row_data in field_rows:
                if query in row_data["label"] or query in row_data["description"]:
                    row_data["frame"].grid()
                else:
                    row_data["frame"].grid_remove()
        
        search_var.trace_add("write", filter_fields)
        
        # ===== SECTION 1: Anagrafica (Basic Info) =====
        section_basic = CollapsibleFrame(form_frame, title="📋 Anagrafica", expanded=True)
        section_basic.pack(fill="x", pady=5)
        content_basic = section_basic.get_content_frame()
        
        # SKU Code
        sku_var = tk.StringVar(value=current_sku.sku if current_sku else "")
        sku_entry = add_field_row(
            content_basic, 0, "Codice SKU:", "Identificativo univoco prodotto",
            sku_var, "entry"
        )
        
        # Description
        desc_var = tk.StringVar(value=current_sku.description if current_sku else "")
        desc_entry = add_field_row(
            content_basic, 1, "Descrizione:", "Nome descrittivo prodotto",
            desc_var, "entry"
        )
        
        # EAN
        ean_var = tk.StringVar(value=current_sku.ean if current_sku and current_sku.ean else "")
        ean_entry = add_field_row(
            content_basic, 2, "EAN (opzionale):", "Codice a barre EAN-8/EAN-13",
            ean_var, "entry"
        )
        
        # EAN validation button
        ean_status_var = tk.StringVar(value="")
        ean_validate_frame = ttk.Frame(content_basic)
        ean_validate_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Button(
            ean_validate_frame,
            text="Valida EAN",
            command=lambda: self._validate_ean_field(ean_var.get(), ean_status_var)
        ).pack(side="left", padx=(0, 10))
        ean_status_label = ttk.Label(ean_validate_frame, textvariable=ean_status_var, foreground="green")
        ean_status_label.pack(side="left")
        
        # In Assortment checkbox
        in_assortment_var = tk.BooleanVar(value=current_sku.in_assortment if current_sku else True)
        assortment_frame = ttk.Frame(content_basic)
        assortment_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Checkbutton(
            assortment_frame,
            text="In assortimento (attivo per proposte d'ordine)",
            variable=in_assortment_var
        ).pack(side="left", padx=(0, 10))
        
        # ===== SECTION 2: Classificazione (Famiglia / Sotto-famiglia) =====
        section_class = CollapsibleFrame(form_frame, title="🏷️ Classificazione", expanded=True)
        section_class.pack(fill="x", pady=5)
        content_class = section_class.get_content_frame()
        
        # Collect unique existing dept/category values for dropdown suggestions
        _all_skus_for_cls = self.csv_layer.read_skus()
        existing_families = sorted({s.department for s in _all_skus_for_cls if s.department})
        existing_subfamilies = sorted({s.category for s in _all_skus_for_cls if s.category})
        
        famiglia_var = tk.StringVar(value=current_sku.department if current_sku else "")
        add_field_row(
            content_class, 0, "Famiglia:",
            "Raggruppamento principale (es. Verdura, Latticini, Bevande)",
            famiglia_var, "combobox_editable", choices=existing_families,
        )
        
        sottofamiglia_var = tk.StringVar(value=current_sku.category if current_sku else "")
        add_field_row(
            content_class, 1, "Sotto-famiglia:",
            "Sotto-categoria specifica (es. Radicchio, Formaggi, Succhi)",
            sottofamiglia_var, "combobox_editable", choices=existing_subfamilies,
        )
        
        # ===== SECTION 3: Ordine & Stock =====
        section_order = CollapsibleFrame(form_frame, title="📦 Ordine & Stock", expanded=False)
        section_order.pack(fill="x", pady=5)
        content_order = section_order.get_content_frame()
        
        moq_var = tk.StringVar(value=str(current_sku.moq) if current_sku else "1")
        add_field_row(content_order, 0, "Q.tà Minima Ordine (MOQ):", "Multiplo minimo per ordini", moq_var, "entry")
        
        pack_size_var = tk.StringVar(value=str(current_sku.pack_size) if current_sku else "1")
        add_field_row(content_order, 1, "Confezione (Pack Size):", "Multiplo arrotondamento colli", pack_size_var, "entry")
        
        lead_time_var = tk.StringVar(value=str(current_sku.lead_time_days) if current_sku else "7")
        add_field_row(content_order, 2, "Lead Time (giorni):", "Tempo attesa ordine→ricezione (0=globale)", lead_time_var, "entry")
        
        review_period_var = tk.StringVar(value=str(current_sku.review_period) if current_sku else "7")
        add_field_row(content_order, 3, "Periodo Revisione (giorni):", "Finestra revisione target S", review_period_var, "entry")
        
        safety_stock_var = tk.StringVar(value=str(current_sku.safety_stock) if current_sku else "0")
        add_field_row(content_order, 4, "Scorta Sicurezza:", "Stock buffer aggiunto a target", safety_stock_var, "entry")
        
        shelf_life_var = tk.StringVar(value=str(current_sku.shelf_life_days) if current_sku else "0")
        add_field_row(content_order, 5, "Shelf Life (giorni):", "0 = nessuna scadenza", shelf_life_var, "entry")
        
        max_stock_var = tk.StringVar(value=str(current_sku.max_stock) if current_sku else "999")
        add_field_row(content_order, 6, "Stock Massimo:", "Limite massimo stock desiderato", max_stock_var, "entry")
        
        reorder_point_var = tk.StringVar(value=str(current_sku.reorder_point) if current_sku else "10")
        add_field_row(content_order, 7, "Punto di Riordino:", "Livello attivazione riordino", reorder_point_var, "entry")
        
        demand_var = tk.StringVar(value=current_sku.demand_variability.value if current_sku else "STABLE")
        add_field_row(content_order, 8, "Variabilità Domanda:", "Livello variabilità vendite", demand_var, "combobox", choices=["STABLE", "LOW", "HIGH", "SEASONAL"])
        
        target_csl_var = tk.StringVar(value=str(current_sku.target_csl) if current_sku else "0")
        add_field_row(content_order, 9, "CSL Target (0-1, 0=cluster):", "0=usa cluster/globale, oppure 0.01-0.99 per override", target_csl_var, "entry")
        
        # ===== SECTION 3: OOS (Out of Stock) =====
        section_oos = CollapsibleFrame(form_frame, title="⚠️ Out of Stock (OOS)", expanded=False)
        section_oos.pack(fill="x", pady=5)
        content_oos = section_oos.get_content_frame()
        
        oos_boost_var = tk.StringVar(value=str(current_sku.oos_boost_percent) if current_sku else "0")
        add_field_row(content_oos, 0, "OOS Boost %:", "0 = usa valore globale", oos_boost_var, "entry")
        
        oos_mode_var = tk.StringVar(value=current_sku.oos_detection_mode if current_sku else "")
        add_field_row(content_oos, 1, "Modalità OOS:", "strict/relaxed/vuoto=globale", oos_mode_var, "combobox", choices=["", "strict", "relaxed"])
        
        oos_popup_var = tk.StringVar(value=current_sku.oos_popup_preference if current_sku else "ask")
        add_field_row(content_oos, 2, "Popup OOS:", "ask/always_yes/always_no", oos_popup_var, "combobox", choices=["ask", "always_yes", "always_no"])
        
        # ===== SECTION 4: Shelf Life Policy =====
        section_shelf_life = CollapsibleFrame(form_frame, title="♻️ Shelf Life & Scadenze", expanded=False)
        section_shelf_life.pack(fill="x", pady=5)
        content_shelf_life = section_shelf_life.get_content_frame()
        
        # has_expiry_label checkbox
        has_expiry_label_var = tk.BooleanVar(value=current_sku.has_expiry_label if current_sku else False)
        expiry_label_frame = ttk.Frame(content_shelf_life)
        expiry_label_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Checkbutton(
            expiry_label_frame,
            text="Ha etichetta scadenza (data scadenza manuale al ricevimento)",
            variable=has_expiry_label_var,
        ).pack(side="left", padx=(0, 10))
        ttk.Label(
            expiry_label_frame,
            text="Se disattivo: la shelf life viene calcolata automaticamente dal motore",
            font=("Helvetica", 8),
            foreground="gray",
        ).pack(side="left")

        min_shelf_life_var = tk.StringVar(value=str(current_sku.min_shelf_life_days) if (current_sku and current_sku.min_shelf_life_days > 0) else "0")
        add_field_row(content_shelf_life, 1, "Shelf Life Minima (giorni):", "Giorni minimi shelf life accettabile (0=globale)", min_shelf_life_var, "entry")
        
        waste_penalty_mode_var = tk.StringVar(value=current_sku.waste_penalty_mode if current_sku else "")
        add_field_row(content_shelf_life, 2, "Modalità Penalità Spreco:", "soft/hard/vuoto=globale", waste_penalty_mode_var, "combobox", choices=["", "soft", "hard"])
        
        waste_penalty_factor_var = tk.StringVar(value=str(current_sku.waste_penalty_factor) if (current_sku and current_sku.waste_penalty_factor > 0) else "0")
        add_field_row(content_shelf_life, 3, "Fattore Penalità:", "0-1 per % o qty fissa (0=globale)", waste_penalty_factor_var, "entry")
        
        waste_risk_threshold_var = tk.StringVar(value=str(current_sku.waste_risk_threshold) if (current_sku and current_sku.waste_risk_threshold > 0) else "0")
        add_field_row(content_shelf_life, 4, "Soglia Rischio Spreco (%):", "0-100, 0=globale", waste_risk_threshold_var, "entry")
        
        # ===== SECTION 5: Monte Carlo =====
        section_mc = CollapsibleFrame(form_frame, title="🎲 Forecast Monte Carlo", expanded=False)
        section_mc.pack(fill="x", pady=5)
        content_mc = section_mc.get_content_frame()
        
        forecast_method_var = tk.StringVar(value=current_sku.forecast_method if current_sku else "")
        add_field_row(content_mc, 0, "Metodo Forecast:", "simple/monte_carlo/croston/sba/tsb/intermittent_auto/vuoto=globale", forecast_method_var, "combobox", choices=["", "simple", "monte_carlo", "croston", "sba", "tsb", "intermittent_auto"])
        
        mc_distribution_var = tk.StringVar(value=current_sku.mc_distribution if current_sku else "")
        add_field_row(content_mc, 1, "MC Distribuzione:", "empirical/normal/lognormal/residuals/vuoto=globale", mc_distribution_var, "combobox", choices=["", "empirical", "normal", "lognormal", "residuals"])
        
        mc_n_sims_var = tk.StringVar(value=str(current_sku.mc_n_simulations) if current_sku else "0")
        add_field_row(content_mc, 2, "MC N Simulazioni:", "100-10000, 0=globale", mc_n_sims_var, "entry")
        
        mc_seed_var = tk.StringVar(value=str(current_sku.mc_random_seed) if current_sku else "0")
        add_field_row(content_mc, 3, "MC Random Seed:", "0=globale/casuale", mc_seed_var, "entry")
        
        mc_output_stat_var = tk.StringVar(value=current_sku.mc_output_stat if current_sku else "")
        add_field_row(content_mc, 4, "MC Stat Output:", "mean/percentile/vuoto=globale", mc_output_stat_var, "combobox", choices=["", "mean", "percentile"])
        
        mc_percentile_var = tk.StringVar(value=str(current_sku.mc_output_percentile) if current_sku else "0")
        add_field_row(content_mc, 5, "MC Percentile:", "50-99, 0=globale", mc_percentile_var, "entry")
        
        mc_horizon_mode_var = tk.StringVar(value=current_sku.mc_horizon_mode if current_sku else "")
        add_field_row(content_mc, 6, "MC Orizzonte Mode:", "auto/custom/vuoto=globale", mc_horizon_mode_var, "combobox", choices=["", "auto", "custom"])
        
        mc_horizon_days_var = tk.StringVar(value=str(current_sku.mc_horizon_days) if current_sku else "0")
        add_field_row(content_mc, 7, "MC Orizzonte Giorni:", "1-365, 0=globale", mc_horizon_days_var, "entry")
        
        # Configure grid
        form_frame.columnconfigure(0, weight=1)
        
        # Button frame
        button_frame = ttk.Frame(main_container, padding=10)
        button_frame.pack(side="bottom", fill="x")
        
        ttk.Button(
            button_frame,
            text="Salva",
            command=lambda: self._save_sku_form(
                popup, mode, sku_var.get(), desc_var.get(), ean_var.get(),
                moq_var.get(), pack_size_var.get(), lead_time_var.get(), 
                review_period_var.get(), safety_stock_var.get(), shelf_life_var.get(),
                max_stock_var.get(), reorder_point_var.get(), 
                demand_var.get(), oos_boost_var.get(), oos_mode_var.get(), 
                oos_popup_var.get(),
                min_shelf_life_var.get(), waste_penalty_mode_var.get(), 
                waste_penalty_factor_var.get(), waste_risk_threshold_var.get(),
                forecast_method_var.get(), mc_distribution_var.get(), mc_n_sims_var.get(),
                mc_seed_var.get(), mc_output_stat_var.get(), mc_percentile_var.get(),
                mc_horizon_mode_var.get(), mc_horizon_days_var.get(),
                in_assortment_var.get(),
                target_csl_var.get(),
                famiglia_var.get(),
                sottofamiglia_var.get(),
                has_expiry_label_var.get(),
                current_sku
            ),
        ).pack(side="right", padx=5)
        
        ttk.Button(button_frame, text="Annulla", command=popup.destroy).pack(side="right", padx=5)
        
        # Focus on first field
        if mode == "new":
            sku_entry.focus()  # type: ignore[union-attr]
        else:
            desc_entry.focus()  # type: ignore[union-attr]
    
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
                        demand_variability_str, oos_boost_str, oos_mode_str, 
                        oos_popup_pref,
                        min_shelf_life_str, waste_penalty_mode_str, waste_penalty_factor_str, 
                        waste_risk_threshold_str,
                        forecast_method_str, mc_distribution_str, mc_n_sims_str,
                        mc_seed_str, mc_output_stat_str, mc_percentile_str,
                        mc_horizon_mode_str, mc_horizon_days_str,
                        in_assortment,
                        target_csl_str,
                        famiglia_str,
                        sottofamiglia_str,
                        has_expiry_label,
                        current_sku):
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
            target_csl = float(target_csl_str)
        except ValueError:
            messagebox.showerror("Errore di Validazione", "Tutti i campi numerici devono essere numeri validi.", parent=popup)
            return
        
        # Validate MOQ specifically (must be >= 1)
        if moq < 1:
            messagebox.showerror("Errore di Validazione", "MOQ (Quantità Minima Ordine) deve essere almeno 1.", parent=popup)
            return
        
        # Validate positive values (allow 0 for review_period, safety_stock, shelf_life, reorder_point)
        if any(v < 0 for v in [moq, pack_size, lead_time_days, review_period, safety_stock, shelf_life_days, max_stock, reorder_point]):
            messagebox.showerror("Errore di Validazione", "I valori numerici non possono essere negativi.", parent=popup)
            return
        
        if pack_size < 1:
            messagebox.showerror("Errore di Validazione", "Pack Size deve essere almeno 1.", parent=popup)
            return
        
        if lead_time_days < 0:
            messagebox.showerror("Errore di Validazione", "Lead Time non può essere negativo.", parent=popup)
            return
        
        # Validate target_csl range (0 = use resolver, or 0 < value < 1)
        if target_csl < 0.0 or target_csl >= 1.0:
            messagebox.showerror("Errore di Validazione", "CSL Target deve essere 0 (usa resolver) oppure un valore tra 0 e 1 (es. 0.95).", parent=popup)
            return
        
        if max_stock < 1:
            messagebox.showerror("Errore di Validazione", "Max Stock deve essere almeno 1.", parent=popup)
            return
        
        if oos_boost_percent < 0 or oos_boost_percent > 100:
            messagebox.showerror("Errore di Validazione", "OOS Boost deve essere tra 0 e 100.", parent=popup)
            return
        
        oos_detection_mode = (oos_mode_str or "").strip()
        if oos_detection_mode not in ["", "strict", "relaxed"]:
            messagebox.showerror("Errore di Validazione", "Modalità OOS non valida. Usa: strict, relaxed o vuoto.", parent=popup)
            return
        
        oos_popup_preference = (oos_popup_pref or "ask").strip()
        if oos_popup_preference not in ["ask", "always_yes", "always_no"]:
            messagebox.showerror("Errore di Validazione", "Preferenza popup OOS non valida. Usa: ask, always_yes o always_no.", parent=popup)
            return
        
        # Parse and validate shelf life policy parameters
        try:
            min_shelf_life_days = int(min_shelf_life_str)
            waste_penalty_factor = float(waste_penalty_factor_str)
            waste_risk_threshold = float(waste_risk_threshold_str)
        except ValueError:
            messagebox.showerror("Errore di Validazione", "Parametri shelf life devono essere numeri validi.", parent=popup)
            return
        
        waste_penalty_mode = (waste_penalty_mode_str or "").strip()
        
        if min_shelf_life_days < 0 or min_shelf_life_days > 365:
            messagebox.showerror("Errore di Validazione", "Min Shelf Life deve essere 0 (globale) o 1-365.", parent=popup)
            return
        
        if waste_penalty_mode and waste_penalty_mode not in ["soft", "hard"]:
            messagebox.showerror("Errore di Validazione", "Modalità penalità non valida: usa '' (globale), 'soft', o 'hard'.", parent=popup)
            return
        
        if waste_penalty_factor < 0 or waste_penalty_factor > 1:
            messagebox.showerror("Errore di Validazione", "Fattore penalità deve essere tra 0 e 1.", parent=popup)
            return
        
        if waste_risk_threshold < 0 or waste_risk_threshold > 100:
            messagebox.showerror("Errore di Validazione", "Soglia rischio spreco deve essere tra 0 e 100.", parent=popup)
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
        
        # Parse Monte Carlo parameters
        forecast_method = (forecast_method_str or "").strip()
        mc_distribution = (mc_distribution_str or "").strip()
        mc_output_stat = (mc_output_stat_str or "").strip()
        mc_horizon_mode = (mc_horizon_mode_str or "").strip()
        
        try:
            mc_n_simulations = int(mc_n_sims_str)
            mc_random_seed = int(mc_seed_str)
            mc_output_percentile = int(mc_percentile_str)
            mc_horizon_days = int(mc_horizon_days_str)
        except ValueError:
            messagebox.showerror("Errore di Validazione", "Parametri Monte Carlo devono essere numeri validi.", parent=popup)
            return
        
        # Validate MC parameters if non-default
        if forecast_method not in ["", "simple", "monte_carlo"]:
            messagebox.showerror("Errore di Validazione", "Metodo forecast non valido: usa '', 'simple', o 'monte_carlo'.", parent=popup)
            return
        
        if mc_distribution and mc_distribution not in ["empirical", "normal", "lognormal", "residuals"]:
            messagebox.showerror("Errore di Validazione", "Distribuzione MC non valida.", parent=popup)
            return
        
        if mc_output_stat and mc_output_stat not in ["mean", "percentile"]:
            messagebox.showerror("Errore di Validazione", "Statistica MC non valida: usa '' (globale), 'mean', o 'percentile'.", parent=popup)
            return
        
        if mc_horizon_mode and mc_horizon_mode not in ["auto", "custom"]:
            messagebox.showerror("Errore di Validazione", "Modalità orizzonte MC non valida: usa '' (globale), 'auto', o 'custom'.", parent=popup)
            return
        
        if mc_n_simulations < 0 or mc_n_simulations > 10000:
            messagebox.showerror("Errore di Validazione", "MC N Simulazioni deve essere 0 (globale) o 1-10000.", parent=popup)
            return
        
        if mc_output_percentile != 0 and (mc_output_percentile < 50 or mc_output_percentile > 99):
            messagebox.showerror("Errore di Validazione", "MC Percentile deve essere 0 (globale) o 50-99.", parent=popup)
            return
        
        if mc_horizon_days < 0 or mc_horizon_days > 365:
            messagebox.showerror("Errore di Validazione", "MC Orizzonte Giorni deve essere 0 (globale) o 1-365.", parent=popup)
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
                    demand_variability=demand_variability,
                    oos_boost_percent=oos_boost_percent,
                    oos_detection_mode=oos_detection_mode,
                    oos_popup_preference=oos_popup_preference,
                    min_shelf_life_days=min_shelf_life_days,
                    waste_penalty_mode=waste_penalty_mode,
                    waste_penalty_factor=waste_penalty_factor,
                    waste_risk_threshold=waste_risk_threshold,
                    forecast_method=forecast_method,
                    mc_distribution=mc_distribution,
                    mc_n_simulations=mc_n_simulations,
                    mc_random_seed=mc_random_seed,
                    mc_output_stat=mc_output_stat,
                    mc_output_percentile=mc_output_percentile,
                    mc_horizon_mode=mc_horizon_mode,
                    mc_horizon_days=mc_horizon_days,
                    in_assortment=in_assortment,
                    target_csl=target_csl,
                    has_expiry_label=has_expiry_label,
                    department=famiglia_str.strip(),
                    category=sottofamiglia_str.strip(),
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
                    demand_variability, oos_boost_percent, oos_detection_mode,
                    oos_popup_preference,
                    min_shelf_life_days, waste_penalty_mode, waste_penalty_factor, 
                    waste_risk_threshold,
                    forecast_method, mc_distribution, mc_n_simulations, mc_random_seed,
                    mc_output_stat, mc_output_percentile, mc_horizon_mode, mc_horizon_days,
                    in_assortment, target_csl,
                    has_expiry_label=has_expiry_label,
                    category=sottofamiglia_str.strip(),
                    department=famiglia_str.strip(),
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
            initialvalue=int(on_hand) if on_hand else 0,
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
                writer.writerow(["SKU", "Description", "EAN", "Famiglia", "Sotto-famiglia"])
                
                for sku in skus:
                    writer.writerow([sku.sku, sku.description, sku.ean, sku.department, sku.category])
            
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
    
    def _export_order_kpi_breakdown(self):
        """
        Export comprehensive CSV with Order Proposals + KPI + Explainability Breakdown.
        Live KPI recalculation for operational audit.
        """
        try:
            # Generate timestamp filename
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_filename = f"order_kpi_breakdown_{timestamp}.csv"
            
            # Create export/ folder if not exists
            export_dir = self.csv_layer.data_dir / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            
            file_path = filedialog.asksaveasfilename(
                title="Export Ordini + KPI + Breakdown",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=suggested_filename,
                initialdir=export_dir,
            )
            
            if not file_path:
                return
            
            # Load data
            all_skus = self.csv_layer.read_skus()
            transactions = self.csv_layer.read_transactions()
            sales_records = self.csv_layer.read_sales()
            settings = self.csv_layer.read_settings()
            
            # Build proposals map (use current proposals if available, else generate fresh)
            proposal_map = {}
            if hasattr(self, 'current_proposals') and self.current_proposals:
                for prop in self.current_proposals:
                    proposal_map[prop.sku] = prop
            else:
                # Generate fresh proposals for export (loop over all SKUs)
                from src.workflows.order import OrderWorkflow
                from src.domain.ledger import StockCalculator
                order_workflow = OrderWorkflow(self.csv_layer)
                for sku_obj in all_skus:
                    try:
                        # Calculate current stock
                        stock = StockCalculator.calculate_asof(
                            sku=sku_obj.sku,
                            asof_date=date.today() + timedelta(days=1),
                            transactions=transactions,
                            sales_records=None,
                        )
                        
                        # Calculate daily sales avg
                        sku_sales = [s for s in sales_records if s.sku == sku_obj.sku]
                        if sku_sales:
                            total_sales = sum(s.qty_sold for s in sku_sales)
                            days = len(sku_sales)
                            daily_sales_avg = total_sales / days if days > 0 else 0.0
                        else:
                            daily_sales_avg = 0.0
                        
                        prop = order_workflow.generate_proposal(
                            sku=sku_obj.sku,
                            description=sku_obj.description,
                            current_stock=stock,
                            daily_sales_avg=daily_sales_avg,
                            sku_obj=sku_obj,
                            transactions=transactions,
                            sales_records=sales_records,
                        )
                        if prop:
                            proposal_map[sku_obj.sku] = prop
                    except Exception as e:
                        logging.warning(f"Failed to generate proposal for SKU {sku_obj.sku} during export: {e}")
            
            # Live KPI calculation for each SKU
            from src.analytics.kpi import compute_oos_kpi, compute_forecast_accuracy, compute_supplier_proxy_kpi
            
            kpi_lookback_days = settings.get("kpi_metrics", {}).get("oos_lookback_days", {}).get("value", 90)
            oos_mode = settings.get("kpi_metrics", {}).get("oos_detection_mode", {}).get("value", "strict")
            kpi_map = {}  # Map SKU -> KPI dict
            
            for sku_obj in all_skus:
                sku = sku_obj.sku
                
                # OOS KPI
                oos_kpi = compute_oos_kpi(
                    sku=sku,
                    lookback_days=kpi_lookback_days,
                    mode=oos_mode,
                    csv_layer=self.csv_layer,
                    asof_date=date.today(),
                )
                
                # Forecast accuracy KPI
                forecast_kpi = compute_forecast_accuracy(
                    sku=sku,
                    lookback_days=kpi_lookback_days,
                    mode="mape",
                    csv_layer=self.csv_layer,
                    asof_date=date.today(),
                )
                
                # Supplier/OTIF proxy KPI
                supplier_kpi = compute_supplier_proxy_kpi(
                    sku=sku,
                    lookback_days=kpi_lookback_days,
                    csv_layer=self.csv_layer,
                    asof_date=date.today(),
                )
                
                # Waste rate (from analytics if available, else 0)
                waste_rate = 0.0  # TODO: integrate waste_rate calculation if available
                
                kpi_map[sku] = {
                    "oos_rate": oos_kpi.get("oos_rate", 0.0),
                    "oos_days": oos_kpi.get("oos_days", 0),
                    "wmape": forecast_kpi.get("wmape", 0.0),
                    "mae": forecast_kpi.get("mae", 0.0),
                    "otif": supplier_kpi.get("otif", 100.0),
                    "qty_unfulfilled": supplier_kpi.get("qty_unfulfilled", 0),
                    "n_unfulfilled_events": supplier_kpi.get("n_unfulfilled_events", 0),
                    "waste_rate": waste_rate,
                }
            
            # Write CSV
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # Header row
                writer.writerow([
                    "SKU",
                    "Descrizione",
                    "Qty Proposta",
                    "Receipt Date",
                    "Policy Mode",
                    "Forecast Method",
                    "Target CSL",
                    "Sigma Horizon",
                    "Reorder Point",
                    "Inventory Position",
                    "Pack Size",
                    "MOQ",
                    "Max Stock",
                    "Constraint Pack",
                    "Constraint MOQ",
                    "Constraint Max",
                    "Constraint Details",
                    "OOS Days Count",
                    "OOS Boost Applied",
                    "Shelf Life Days",
                    "Usable Stock",
                    "Waste Risk %",
                    "KPI: OOS Rate %",
                    "KPI: OOS Days",
                    "KPI: WMAPE %",
                    "KPI: MAE",
                    "KPI: OTIF %",
                    "KPI: Unfulfilled Qty",
                    "KPI: Unfulfilled Events",
                    "KPI: Waste Rate %",
                    "Notes",
                ])
                
                # Data rows
                row_count = 0
                for sku_obj in all_skus:
                    sku = sku_obj.sku
                    prop = proposal_map.get(sku)
                    kpi = kpi_map.get(sku, {})
                    
                    if prop:
                        # Full row from proposal + KPI
                        writer.writerow([
                            sku,
                            sku_obj.description,
                            prop.proposed_qty,
                            prop.receipt_date.isoformat() if prop.receipt_date else "",
                            prop.policy_mode,
                            prop.forecast_method,
                            f"{prop.target_csl:.3f}" if prop.target_csl > 0 else "",
                            f"{prop.sigma_horizon:.2f}" if prop.sigma_horizon > 0 else "",
                            prop.reorder_point,
                            prop.inventory_position,
                            prop.pack_size,
                            prop.moq,
                            prop.max_stock,
                            "YES" if prop.constraints_applied_pack else "NO",
                            "YES" if prop.constraints_applied_moq else "NO",
                            "YES" if prop.constraints_applied_max else "NO",
                            prop.constraint_details,
                            prop.oos_days_count,
                            "YES" if prop.oos_boost_applied else "NO",
                            prop.shelf_life_days if prop.shelf_life_days > 0 else "",
                            prop.usable_stock,
                            f"{prop.waste_risk_percent:.1f}" if prop.waste_risk_percent > 0 else "",
                            f"{kpi.get('oos_rate', 0.0):.2f}",
                            kpi.get('oos_days', 0),
                            f"{kpi.get('wmape', 0.0):.2f}",
                            f"{kpi.get('mae', 0.0):.2f}",
                            f"{kpi.get('otif', 100.0):.2f}",
                            kpi.get('qty_unfulfilled', 0),
                            kpi.get('n_unfulfilled_events', 0),
                            f"{kpi.get('waste_rate', 0.0):.2f}",
                            prop.notes,
                        ])
                        row_count += 1
                    else:
                        # SKU without proposal (KPI only)
                        writer.writerow([
                            sku,
                            sku_obj.description,
                            0,  # No proposal
                            "",
                            "",
                            "",
                            "",
                            "",
                            0,
                            0,
                            sku_obj.pack_size,
                            sku_obj.moq,
                            sku_obj.max_stock,
                            "",
                            "",
                            "",
                            "",
                            0,
                            "",
                            sku_obj.shelf_life_days if sku_obj.shelf_life_days else "",
                            0,
                            "",
                            f"{kpi.get('oos_rate', 0.0):.2f}",
                            kpi.get('oos_days', 0),
                            f"{kpi.get('wmape', 0.0):.2f}",
                            f"{kpi.get('mae', 0.0):.2f}",
                            f"{kpi.get('otif', 100.0):.2f}",
                            kpi.get('qty_unfulfilled', 0),
                            kpi.get('n_unfulfilled_events', 0),
                            f"{kpi.get('waste_rate', 0.0):.2f}",
                            "No proposal generated",
                        ])
                        row_count += 1
            
            messagebox.showinfo(
                "Successo",
                f"Export completato:\n{file_path}\n\n{row_count} SKU esportati con KPI live e breakdown."
            )
            
            # Log export operation (EXPORT_LOG audit trail)
            policy_mode = settings.get("reorder_engine", {}).get("policy_mode", {}).get("value", "legacy")
            kpi_params = {
                "lookback_days": kpi_lookback_days,
                "export_timestamp": timestamp,
                "policy_mode": policy_mode,
            }
            
            self.csv_layer.log_audit(
                operation="EXPORT_LOG",
                details=f"Order+KPI+Breakdown export: {row_count} SKUs, file={Path(file_path).name}, policy={policy_mode}, kpi_lookback={kpi_lookback_days}d",
                sku=None,
            )
        
        except Exception as e:
            import traceback
            messagebox.showerror("Errore Esportazione", f"Impossibile esportare Order+KPI+Breakdown:\n{str(e)}\n\n{traceback.format_exc()}")

    def _export_order_explain(self):
        """
        Export order_explain CSV: one row per SKU with the full forecast→policy
        audit chain (mu_P, sigma_P, IP, modifiers, constraints, Q).

        Uses explain_order() from the new facade layer so the output is
        guaranteed to reflect the canonical pipeline, not ad-hoc fields.
        """
        try:
            from datetime import datetime as _dt
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            suggested_filename = f"order_explain_{timestamp}.csv"

            export_dir = self.csv_layer.data_dir / "export"
            export_dir.mkdir(parents=True, exist_ok=True)

            file_path = filedialog.asksaveasfilename(
                title="Export Order Explain (Audit Trail)",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=suggested_filename,
                initialdir=export_dir,
            )
            if not file_path:
                return

            all_skus = self.csv_layer.read_skus()
            transactions = self.csv_layer.read_transactions()
            sales_records = self.csv_layer.read_sales()
            settings = self.csv_layer.read_settings()
            promo_calendar = self.csv_layer.read_promo_calendar()
            event_rules = self.csv_layer.read_event_uplift_rules()

            from src.workflows.order import explain_order, calculate_daily_sales_average
            from src.domain.ledger import StockCalculator
            from src.analytics.pipeline import build_open_pipeline
            from src.domain.calendar import next_receipt_date, calculate_protection_period_days, Lane
            from src.domain.contracts import OrderExplain

            # CSV header from OrderExplain column spec
            columns = OrderExplain.CSV_COLUMNS + ["error"]

            today = date.today()
            row_count = 0

            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()

                for sku_obj in all_skus:
                    row: dict = {"sku": sku_obj.sku, "asof_date": today.isoformat(), "error": ""}
                    try:
                        stock = StockCalculator.calculate_asof(
                            sku=sku_obj.sku,
                            asof_date=today + timedelta(days=1),
                            transactions=transactions,
                            sales_records=None,
                        )
                        pipeline = build_open_pipeline(self.csv_layer, sku_obj.sku, today)
                        history = [
                            {"date": s.date, "qty_sold": s.qty_sold}
                            for s in sales_records if s.sku == sku_obj.sku
                        ]

                        # Use STANDARD lane for export (representative)
                        receipt_dt = next_receipt_date(today, Lane.STANDARD)
                        pp_days = calculate_protection_period_days(today, Lane.STANDARD)

                        explain_dict = explain_order(
                            sku_id=sku_obj.sku,
                            asof_date=today,
                            history=history,
                            stock=stock,
                            pipeline=pipeline,
                            target_receipt_date=receipt_dt,
                            protection_period_days=pp_days,
                            settings=settings,
                            sku_obj=sku_obj,
                            promo_calendar=promo_calendar,
                            event_uplift_rules=event_rules,
                            sales_records=sales_records,
                            transactions=transactions,
                            all_skus=all_skus,
                        )
                        row.update(explain_dict)
                    except Exception as exc:
                        row["error"] = str(exc)[:200]

                    writer.writerow(row)
                    row_count += 1

            messagebox.showinfo(
                "Successo",
                f"Order Explain export completato:\n{file_path}\n\n{row_count} SKU esportati."
            )
            self.csv_layer.log_audit(
                operation="EXPORT_LOG",
                details=f"order_explain export: {row_count} SKUs, file={Path(file_path).name}",
                sku=None,
            )
        except Exception as e:
            import traceback
            messagebox.showerror("Errore Export Explain", f"Impossibile esportare:\n{str(e)}\n\n{traceback.format_exc()}")

    # === IMPORT FUNCTIONALITY ===

    def _import_sku_from_csv(self):
        """
        Import SKUs from external CSV file with preview and validation wizard.
        
        Steps:
        1. File picker for CSV selection
        2. Parse and validate (auto-detect delimiter, map columns)
        3. Show preview with valid/discarded counts and first ~50 rows
        4. Allow column remapping
        5. Choose UPSERT or REPLACE mode
        6. Confirm import (with extra confirmation if REPLACE has discards)
        7. Execute import with backup and atomic write
        8. Log to audit_log.csv and export error details if discarded rows exist
        """
        from pathlib import Path
        from src.workflows.sku_import import SKUImporter
        from datetime import datetime
        
        try:
            # Step 1: File picker
            file_path = filedialog.askopenfilename(
                title="Seleziona CSV per Import SKU",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            
            if not file_path:
                return  # User cancelled
            
            file_path = Path(file_path)
            
            # Step 2: Parse and validate
            importer = SKUImporter(self.csv_layer)
            
            try:
                preview = importer.parse_csv_with_preview(
                    filepath=file_path,
                    column_mapping=None,  # Auto-detect
                    preview_limit=50,
                )
            except Exception as e:
                messagebox.showerror(
                    "Errore Parsing CSV",
                    f"Impossibile leggere il file CSV:\n{str(e)}\n\n"
                    f"Verifica che il file sia un CSV valido e prova nuovamente."
                )
                return
            
            # Step 3: Show preview wizard
            self._show_import_preview_wizard(importer, preview, file_path)
        
        except Exception as e:
            import traceback
            messagebox.showerror(
                "Errore Import",
                f"Errore durante import SKU:\n{str(e)}\n\n{traceback.format_exc()}"
            )
    
    def _show_import_preview_wizard(self, importer, preview, source_file):
        """
        Show import preview wizard with validation results and confirmation.
        
        Args:
            importer: SKUImporter instance
            preview: ImportPreview object
            source_file: Path to source CSV file
        """
        # Create wizard window
        wizard = tk.Toplevel(self.root)
        wizard.title(f"Import SKU da CSV: {source_file.name}")
        wizard.geometry("1000x700")
        wizard.transient(self.root)
        wizard.grab_set()
        
        # Header with summary
        header_frame = ttk.Frame(wizard, padding=10)
        header_frame.pack(fill="x", side="top")
        
        ttk.Label(
            header_frame,
            text=f"File: {source_file.name}",
            font=("Helvetica", 12, "bold")
        ).pack(anchor="w")
        
        summary_text = (
            f"Totale righe: {preview.total_rows}  |  "
            f"✅ Valide: {preview.valid_rows}  |  "
            f"❌ Scartate: {preview.discarded_rows}"
        )
        
        if preview.discarded_rows > 0:
            summary_text += f"\nMotivo principale scarti: {preview.primary_discard_reason}"
        
        summary_label = ttk.Label(
            header_frame,
            text=summary_text,
            font=("Helvetica", 10),
            foreground="blue" if preview.valid_rows > 0 else "red"
        )
        summary_label.pack(anchor="w", pady=(5, 0))
        
        # Column mapping info
        mapping_frame = ttk.LabelFrame(wizard, text="Mapping Colonne", padding=10)
        mapping_frame.pack(fill="x", padx=10, pady=5)
        
        mapping_text = ", ".join([f"{csv_col} → {field}" for csv_col, field in preview.column_mapping.items()])
        ttk.Label(
            mapping_frame,
            text=mapping_text if mapping_text else "Nessuna colonna mappata",
            font=("Courier", 9),
            wraplength=900
        ).pack(anchor="w")
        
        # Preview table (first 50 rows)
        preview_frame = ttk.LabelFrame(wizard, text="Anteprima Righe (prime 50)", padding=10)
        preview_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Scrollable table
        table_scroll_y = ttk.Scrollbar(preview_frame, orient="vertical")
        table_scroll_x = ttk.Scrollbar(preview_frame, orient="horizontal")
        
        preview_table = ttk.Treeview(
            preview_frame,
            yscrollcommand=table_scroll_y.set,
            xscrollcommand=table_scroll_x.set,
            height=15
        )
        
        table_scroll_y.config(command=preview_table.yview)
        table_scroll_x.config(command=preview_table.xview)
        
        table_scroll_y.pack(side="right", fill="y")
        table_scroll_x.pack(side="bottom", fill="x")
        preview_table.pack(fill="both", expand=True)
        
        # Configure columns
        preview_table["columns"] = ("row", "status", "sku", "description", "errors")
        preview_table.column("#0", width=0, stretch=False)
        preview_table.column("row", width=50, anchor="center")
        preview_table.column("status", width=80, anchor="center")
        preview_table.column("sku", width=150, anchor="w")
        preview_table.column("description", width=300, anchor="w")
        preview_table.column("errors", width=400, anchor="w")
        
        preview_table.heading("row", text="Riga")
        preview_table.heading("status", text="Stato")
        preview_table.heading("sku", text="SKU")
        preview_table.heading("description", text="Descrizione")
        preview_table.heading("errors", text="Errori/Avvisi")
        
        # Populate table
        for row in preview.rows[:50]:
            status = "✅ OK" if row.is_valid else "❌ ERRORE"
            sku = row.mapped_data.get("sku", "")
            desc = row.mapped_data.get("description", "")
            errors_text = "; ".join(row.errors) if row.errors else "; ".join(row.warnings)
            
            tag = "valid" if row.is_valid else "invalid"
            preview_table.insert(
                "",
                "end",
                values=(row.row_number, status, sku, desc, errors_text),
                tags=(tag,)
            )
        
        # Tag colors
        preview_table.tag_configure("valid", background="#e8f5e9")
        preview_table.tag_configure("invalid", background="#ffebee")
        
        # Mode selection
        mode_frame = ttk.Frame(wizard, padding=10)
        mode_frame.pack(fill="x", side="bottom")
        
        mode_var = tk.StringVar(value="UPSERT")
        
        ttk.Label(
            mode_frame,
            text="Modalità Import:",
            font=("Helvetica", 10, "bold")
        ).pack(side="left", padx=(0, 10))
        
        ttk.Radiobutton(
            mode_frame,
            text="UPSERT (Aggiorna esistenti + Aggiungi nuovi)",
            variable=mode_var,
            value="UPSERT"
        ).pack(side="left", padx=5)
        
        ttk.Radiobutton(
            mode_frame,
            text="REPLACE (Sostituisci tutto il file SKU)",
            variable=mode_var,
            value="REPLACE"
        ).pack(side="left", padx=5)
        
        # Action buttons
        button_frame = ttk.Frame(wizard, padding=10)
        button_frame.pack(fill="x", side="bottom")
        
        def on_confirm():
            """Execute import after confirmation."""
            mode = mode_var.get()
            
            # Check if there are no valid rows
            if preview.valid_rows == 0:
                messagebox.showerror(
                    "Impossibile Importare",
                    "Nessuna riga valida da importare. Correggi gli errori e riprova.",
                    parent=wizard
                )
                return
            
            # Confirmation for REPLACE mode
            if mode == "REPLACE":
                confirm_msg = (
                    f"⚠️  ATTENZIONE ⚠️\n\n"
                    f"Modalità REPLACE: Tutti gli SKU esistenti verranno sostituiti con i {preview.valid_rows} SKU validi dal file.\n\n"
                    f"SKU esistenti non presenti nel file verranno RIMOSSI.\n\n"
                    f"Vuoi procedere?"
                )
                
                # Extra confirmation if there are discarded rows
                if preview.discarded_rows > 0:
                    confirm_msg += (
                        f"\n\n⚠️  ATTENZIONE: {preview.discarded_rows} righe verranno scartate.\n"
                        f"Motivo principale: {preview.primary_discard_reason}\n\n"
                        f"Procedere comunque con REPLACE?"
                    )
                
                if not messagebox.askyesno("Conferma REPLACE", confirm_msg, parent=wizard):
                    return
            else:
                # UPSERT confirmation
                confirm_msg = (
                    f"Conferma Import UPSERT:\n\n"
                    f"Righe valide da importare: {preview.valid_rows}\n"
                    f"Righe scartate: {preview.discarded_rows}\n\n"
                    f"Gli SKU esistenti verranno aggiornati, i nuovi verranno aggiunti.\n\n"
                    f"Procedere?"
                )
                
                if not messagebox.askyesno("Conferma Import", confirm_msg, parent=wizard):
                    return
            
            # Execute import
            try:
                result = importer.execute_import(
                    preview=preview,
                    mode=mode,
                    require_confirmation_on_discards=(mode == "REPLACE")  # Already confirmed above
                )
                
                if result.get("confirmation_required"):
                    # Should not happen since we already confirmed
                    messagebox.showwarning(
                        "Conferma Richiesta",
                        "Conferma aggiuntiva richiesta per REPLACE con scarti.",
                        parent=wizard
                    )
                    return
                
                if result["success"]:
                    # Export discard details if any
                    error_detail_file = ""
                    if preview.discarded_rows > 0:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        error_detail_file = f"import_sku_errors_{timestamp}.csv"
                        error_path = self.csv_layer.data_dir / error_detail_file
                        importer.export_discard_details(preview, error_path)
                    
                    # Log to audit
                    self.csv_layer.log_import_audit(
                        source_file=source_file.name,
                        mode=mode,
                        total_rows=preview.total_rows,
                        imported=result["imported"],
                        discarded=preview.discarded_rows,
                        primary_discard_reason=preview.primary_discard_reason,
                        error_detail_file=error_detail_file,
                        user="GUI"
                    )
                    
                    # Success message
                    success_msg = (
                        f"✅ Import completato con successo!\n\n"
                        f"Modalità: {mode}\n"
                        f"Righe importate: {result['imported']}\n"
                        f"Righe scartate: {result['discarded']}\n"
                    )
                    
                    if mode == "UPSERT":
                        success_msg += f"Aggiornati: {result['updated']}\nAggiunti: {result['added']}\n"
                    
                    if error_detail_file:
                        success_msg += f"\nDettagli scarti salvati in: {error_detail_file}"
                    
                    messagebox.showinfo("Import Completato", success_msg, parent=wizard)
                    
                    # Refresh GUI
                    self._refresh_all()
                    wizard.destroy()
                else:
                    error_msg = "Errori:\n" + "\n".join(result.get("errors", ["Errore sconosciuto"]))
                    messagebox.showerror("Import Fallito", error_msg, parent=wizard)
            
            except Exception as e:
                import traceback
                messagebox.showerror(
                    "Errore Import",
                    f"Errore durante esecuzione import:\n{str(e)}\n\n{traceback.format_exc()}",
                    parent=wizard
                )
        
        def on_cancel():
            """Close wizard without importing."""
            wizard.destroy()
        
        ttk.Button(
            button_frame,
            text="❌ Annulla",
            command=on_cancel
        ).pack(side="right", padx=5)
        
        ttk.Button(
            button_frame,
            text="✅ Conferma Import",
            command=on_confirm
        ).pack(side="right", padx=5)
        
        # Export errors button (if discards exist)
        if preview.discarded_rows > 0:
            def export_errors():
                """Export discarded rows to CSV for analysis."""
                error_file = filedialog.asksaveasfilename(
                    title="Salva Dettagli Scarti",
                    defaultextension=".csv",
                    filetypes=[("CSV files", "*.csv")],
                    initialfile=f"import_errors_{source_file.stem}.csv",
                    parent=wizard
                )
                
                if error_file:
                    importer.export_discard_details(preview, Path(error_file))
                    messagebox.showinfo(
                        "Esportazione Completata",
                        f"Dettagli scarti esportati in:\n{error_file}",
                        parent=wizard
                    )
            
            ttk.Button(
                button_frame,
                text="📄 Esporta Scarti",
                command=export_errors
            ).pack(side="left", padx=5)
    
    def _filter_settings(self, *args):
        """Filter visible settings rows based on search query."""
        query = self.settings_search_var.get().lower()
        
        for row_data in self.settings_rows:
            row_frame = row_data["frame"]
            label = row_data["label"].lower()
            description = row_data["description"].lower()
            
            # Show/hide based on match
            if query in label or query in description:
                row_frame.pack(fill="x", pady=8)
            else:
                row_frame.pack_forget()
    
    def _create_param_rows(self, parent_frame, parameters, section_key):
        """
        Create parameter rows with grid layout.
        
        Args:
            parent_frame: Parent frame to place rows in
            parameters: List of parameter dicts
            section_key: Section key for settings storage
        """
        for param in parameters:
            row_frame = ttk.Frame(parent_frame)
            row_frame.pack(fill="x", pady=8)
            
            # Configure grid columns: [label: 25%, description: 45%, input: 30%]
            row_frame.columnconfigure(0, weight=25, minsize=200)
            row_frame.columnconfigure(1, weight=45, minsize=350)
            row_frame.columnconfigure(2, weight=30, minsize=250)
            
            # Label (bold)
            ttk.Label(
                row_frame,
                text=param["label"],
                font=("Helvetica", 10, "bold")
            ).grid(row=0, column=0, sticky="w", padx=(0, 10))
            
            # Description (gray, wrapped)
            desc_label = ttk.Label(
                row_frame,
                text=param["description"],
                font=("Helvetica", 9),
                foreground="gray",
                wraplength=300
            )
            desc_label.grid(row=0, column=1, sticky="w", padx=(0, 10))
            
            # Value input based on type
            if param["type"] == "bool":
                value_var = tk.BooleanVar()
                value_check = ttk.Checkbutton(
                    row_frame,
                    text="Abilitato",
                    variable=value_var
                )
                value_check.grid(row=0, column=2, sticky="w")
            elif param["type"] == "int":
                value_var = tk.IntVar()
                value_entry = ttk.Spinbox(
                    row_frame,
                    from_=param["min"],
                    to=param["max"],
                    textvariable=value_var,
                    width=10
                )
                value_entry.grid(row=0, column=2, sticky="w")
            elif param["type"] == "float":
                value_var = tk.DoubleVar()
                value_entry = ttk.Spinbox(
                    row_frame,
                    from_=param["min"],
                    to=param["max"],
                    increment=0.1,
                    textvariable=value_var,
                    width=10,
                    format="%.2f"
                )
                value_entry.grid(row=0, column=2, sticky="w")
            elif param["type"] == "choice":
                value_var = tk.StringVar()
                value_entry = ttk.Combobox(
                    row_frame,
                    textvariable=value_var,
                    values=param["choices"],
                    state="readonly",
                    width=15
                )
                value_entry.grid(row=0, column=2, sticky="w")
            
            # Store widget
            self.settings_widgets[param["key"]] = {
                "value_var": value_var,
                "section": section_key
            }
            
            # Add trace to mark as modified when changed
            value_var.trace_add("write", lambda *args: self._mark_settings_modified())
            
            # Store for search
            self.settings_rows.append({
                "frame": row_frame,
                "label": param["label"],
                "description": param["description"]
            })
    
    def _refresh_all(self):
        """Refresh all tabs."""
        self._refresh_dashboard()
        self._refresh_stock_tab()
        self._refresh_pending_orders()
        self._refresh_receiving_history()
        self._refresh_admin_tab()
        self._refresh_exception_tab()
        self._refresh_smart_exceptions()  # Populate smart exception filters on startup
        self._refresh_promo_tab()
        self._refresh_settings_tab()
    
    def _build_promo_tab(self):
        """Build Promo Calendar tab (add/edit/remove promotional windows)."""
        main_frame = ttk.Frame(self.promo_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="📅 Calendario Promo", font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Label(title_frame, text="(Gestisci periodi promozionali per SKU)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
        # === PROMO WINDOW ENTRY FORM ===
        form_frame = ttk.LabelFrame(main_frame, text="Aggiungi Finestra Promo (campi obbligatori marcati con *)", padding=15)
        form_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Grid configuration
        form_frame.columnconfigure(1, weight=1)
        form_frame.columnconfigure(3, weight=1)
        
        # ROW 0: SKU (obbligatorio)
        ttk.Label(form_frame, text="SKU: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=8)
        self.promo_sku_var = tk.StringVar()
        
        # Autocomplete per SKU
        self.promo_sku_entry = AutocompleteEntry(
            form_frame,
            textvariable=self.promo_sku_var,
            items_callback=self._filter_promo_sku_items,
            width=35,
        )
        self.promo_sku_entry.grid(row=0, column=1, sticky="w", pady=8)
        self.promo_sku_var.trace('w', lambda *args: self._validate_promo_form())
        
        # ROW 0 col 2: Store ID (opzionale)
        ttk.Label(form_frame, text="Store ID:", font=("Helvetica", 9)).grid(row=0, column=2, sticky="e", padx=(20, 8), pady=8)
        self.promo_store_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.promo_store_var, width=15).grid(row=0, column=3, sticky="w", pady=8)
        ttk.Label(form_frame, text="(vuoto = tutti i negozi)", font=("Helvetica", 8, "italic"), foreground="#777").grid(row=0, column=4, sticky="w", padx=(5, 0), pady=8)
        
        # ROW 1: Data Inizio (obbligatorio)
        ttk.Label(form_frame, text="Data Inizio: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=8)
        self.promo_start_var = tk.StringVar(value=date.today().isoformat())
        if TKCALENDAR_AVAILABLE:
            DateEntry(  # type: ignore[misc]
                form_frame,
                textvariable=self.promo_start_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).grid(row=1, column=1, sticky="w", pady=8)
        else:
            ttk.Entry(form_frame, textvariable=self.promo_start_var, width=15).grid(row=1, column=1, sticky="w", pady=8)
        self.promo_start_var.trace('w', lambda *args: self._validate_promo_form())
        
        # ROW 1 col 2: Data Fine (obbligatorio)
        ttk.Label(form_frame, text="Data Fine: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=1, column=2, sticky="e", padx=(20, 8), pady=8)
        self.promo_end_var = tk.StringVar(value=(date.today() + timedelta(days=7)).isoformat())
        if TKCALENDAR_AVAILABLE:
            DateEntry(  # type: ignore[misc]
                form_frame,
                textvariable=self.promo_end_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).grid(row=1, column=3, sticky="w", pady=8)
        else:
            ttk.Entry(form_frame, textvariable=self.promo_end_var, width=15).grid(row=1, column=3, sticky="w", pady=8)
        self.promo_end_var.trace('w', lambda *args: self._validate_promo_form())
        
        # ROW 2: Buttons
        button_frame = ttk.Frame(form_frame)
        button_frame.grid(row=2, column=0, columnspan=5, sticky="w", pady=(10, 0))
        
        self.promo_submit_btn = ttk.Button(button_frame, text="✓ Aggiungi Promo", command=self._add_promo_window, state="disabled")
        self.promo_submit_btn.pack(side="left", padx=5)
        ttk.Button(button_frame, text="✗ Cancella Modulo", command=self._clear_promo_form).pack(side="left", padx=5)
        
        # Validation status label
        self.promo_validation_label = ttk.Label(button_frame, text="", font=("Helvetica", 8), foreground="#d9534f")
        self.promo_validation_label.pack(side="left", padx=15)
        
        # === PROMO WINDOWS TABLE ===
        table_controls_frame = ttk.Frame(main_frame)
        table_controls_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Label(table_controls_frame, text="Finestre Promo Esistenti", font=("Helvetica", 11, "bold")).pack(side="left", padx=5)
        ttk.Button(table_controls_frame, text="🗑️ Rimuovi Selezionata", command=self._remove_promo_window).pack(side="left", padx=20)
        ttk.Button(table_controls_frame, text="🔄 Aggiorna", command=self._refresh_promo_tab).pack(side="left", padx=5)
        
        # Search filter
        ttk.Label(table_controls_frame, text="Filtra SKU:").pack(side="left", padx=(20, 5))
        self.promo_filter_var = tk.StringVar()
        self.promo_filter_var.trace('w', lambda *args: self._filter_promo_table())
        ttk.Entry(table_controls_frame, textvariable=self.promo_filter_var, width=20).pack(side="left", padx=5)
        
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(table_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.promo_treeview = ttk.Treeview(
            table_frame,
            columns=("SKU", "Data Inizio", "Data Fine", "Durata", "Store ID", "Stato"),
            height=15,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.promo_treeview.yview)
        
        self.promo_treeview.column("#0", width=0, stretch=tk.NO)
        self.promo_treeview.column("SKU", anchor=tk.W, width=120)
        self.promo_treeview.column("Data Inizio", anchor=tk.CENTER, width=110)
        self.promo_treeview.column("Data Fine", anchor=tk.CENTER, width=110)
        self.promo_treeview.column("Durata", anchor=tk.CENTER, width=80)
        self.promo_treeview.column("Store ID", anchor=tk.CENTER, width=100)
        self.promo_treeview.column("Stato", anchor=tk.CENTER, width=120)
        
        self.promo_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.promo_treeview.heading("Data Inizio", text="Data Inizio", anchor=tk.CENTER)
        self.promo_treeview.heading("Data Fine", text="Data Fine", anchor=tk.CENTER)
        self.promo_treeview.heading("Durata", text="Durata (gg)", anchor=tk.CENTER)
        self.promo_treeview.heading("Store ID", text="Store ID", anchor=tk.CENTER)
        self.promo_treeview.heading("Stato", text="Stato", anchor=tk.CENTER)
        
        self.promo_treeview.pack(fill="both", expand=True)
        
        # Tag for expired/active/future windows
        self.promo_treeview.tag_configure("expired", foreground="gray")
        self.promo_treeview.tag_configure("active", background="#d4edda", foreground="green")
        self.promo_treeview.tag_configure("future", foreground="blue")
        
        # === UPLIFT REPORT SECTION ===
        uplift_section = ttk.LabelFrame(main_frame, text="📊 Analisi Uplift Promo (Stima Fattore Uplift per SKU)", padding=10)
        uplift_section.pack(side="bottom", fill="both", expand=False, pady=(15, 0))
        
        # Controls for uplift report
        uplift_controls_frame = ttk.Frame(uplift_section)
        uplift_controls_frame.pack(side="top", fill="x", pady=(0, 10))
        
        ttk.Label(uplift_controls_frame, text="Report Uplift (basato su eventi storici)", font=("Helvetica", 10, "bold")).pack(side="left", padx=5)
        ttk.Button(uplift_controls_frame, text="🔄 Calcola Report Uplift", command=self._refresh_uplift_report).pack(side="left", padx=20)
        
        # Filter for uplift table
        ttk.Label(uplift_controls_frame, text="Filtra SKU:").pack(side="left", padx=(20, 5))
        self.uplift_filter_var = tk.StringVar()
        self.uplift_filter_var.trace('w', lambda *args: self._filter_uplift_table())
        ttk.Entry(uplift_controls_frame, textvariable=self.uplift_filter_var, width=20).pack(side="left", padx=5)
        
        # Uplift report table (TreeView)
        uplift_table_frame = ttk.Frame(uplift_section)
        uplift_table_frame.pack(fill="both", expand=True)
        
        uplift_scrollbar = ttk.Scrollbar(uplift_table_frame)
        uplift_scrollbar.pack(side="right", fill="y")
        
        self.uplift_treeview = ttk.Treeview(
            uplift_table_frame,
            columns=("SKU", "Eventi", "Uplift", "Confidence", "Pooling", "Giorni Validi"),
            height=8,
            yscrollcommand=uplift_scrollbar.set,
        )
        uplift_scrollbar.config(command=self.uplift_treeview.yview)
        
        self.uplift_treeview.column("#0", width=0, stretch=tk.NO)
        self.uplift_treeview.column("SKU", anchor=tk.W, width=120)
        self.uplift_treeview.column("Eventi", anchor=tk.CENTER, width=80)
        self.uplift_treeview.column("Uplift", anchor=tk.CENTER, width=100)
        self.uplift_treeview.column("Confidence", anchor=tk.CENTER, width=100)
        self.uplift_treeview.column("Pooling", anchor=tk.W, width=150)
        self.uplift_treeview.column("Giorni Validi", anchor=tk.CENTER, width=120)
        
        self.uplift_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.uplift_treeview.heading("Eventi", text="N. Eventi", anchor=tk.CENTER)
        self.uplift_treeview.heading("Uplift", text="Uplift Finale", anchor=tk.CENTER)
        self.uplift_treeview.heading("Confidence", text="Confidence", anchor=tk.CENTER)
        self.uplift_treeview.heading("Pooling", text="Pooling Source", anchor=tk.W)
        self.uplift_treeview.heading("Giorni Validi", text="Totale Giorni", anchor=tk.CENTER)
        
        self.uplift_treeview.pack(fill="both", expand=True)
        
        # Tag for confidence levels
        self.uplift_treeview.tag_configure("confidence_A", foreground="green", font=("Helvetica", 9, "bold"))
        self.uplift_treeview.tag_configure("confidence_B", foreground="orange")
        self.uplift_treeview.tag_configure("confidence_C", foreground="red")
        
        # === CANNIBALIZATION (DOWNLIFT) REPORT SECTION ===
        cannib_section = ttk.LabelFrame(main_frame, text="📉 Analisi Cannibalizzazione (Downlift per Sostituti in Promo)", padding=10)
        cannib_section.pack(side="bottom", fill="both", expand=False, pady=(15, 0))
        
        # Controls for cannibalization report
        cannib_controls_frame = ttk.Frame(cannib_section)
        cannib_controls_frame.pack(side="top", fill="x", pady=(0, 10))
        
        ttk.Label(cannib_controls_frame, text="Report Cannibalizzazione (riduzione forecast per target non-promo)", font=("Helvetica", 10, "bold")).pack(side="left", padx=5)
        ttk.Button(cannib_controls_frame, text="🔄 Calcola Report Downlift", command=self._refresh_cannibalization_report).pack(side="left", padx=20)
        
        # Filter for cannibalization table
        ttk.Label(cannib_controls_frame, text="Filtra SKU:").pack(side="left", padx=(20, 5))
        self.cannib_filter_var = tk.StringVar()
        self.cannib_filter_var.trace('w', lambda *args: self._filter_cannibalization_table())
        ttk.Entry(cannib_controls_frame, textvariable=self.cannib_filter_var, width=20).pack(side="left", padx=5)
        
        # Cannibalization report table (TreeView)
        cannib_table_frame = ttk.Frame(cannib_section)
        cannib_table_frame.pack(fill="both", expand=True)
        
        cannib_scrollbar = ttk.Scrollbar(cannib_table_frame)
        cannib_scrollbar.pack(side="right", fill="y")
        
        self.cannib_treeview = ttk.Treeview(
            cannib_table_frame,
            columns=("Target SKU", "Driver SKU", "Downlift", "Riduzione %", "Confidence", "Eventi"),
            height=8,
            yscrollcommand=cannib_scrollbar.set,
        )
        cannib_scrollbar.config(command=self.cannib_treeview.yview)
        
        self.cannib_treeview.column("#0", width=0, stretch=tk.NO)
        self.cannib_treeview.column("Target SKU", anchor=tk.W, width=120)
        self.cannib_treeview.column("Driver SKU", anchor=tk.W, width=120)
        self.cannib_treeview.column("Downlift", anchor=tk.CENTER, width=100)
        self.cannib_treeview.column("Riduzione %", anchor=tk.CENTER, width=100)
        self.cannib_treeview.column("Confidence", anchor=tk.CENTER, width=100)
        self.cannib_treeview.column("Eventi", anchor=tk.CENTER, width=80)
        
        self.cannib_treeview.heading("Target SKU", text="Target SKU", anchor=tk.W)
        self.cannib_treeview.heading("Driver SKU", text="Driver Promo", anchor=tk.W)
        self.cannib_treeview.heading("Downlift", text="Fattore Downlift", anchor=tk.CENTER)
        self.cannib_treeview.heading("Riduzione %", text="Riduzione %", anchor=tk.CENTER)
        self.cannib_treeview.heading("Confidence", text="Confidence", anchor=tk.CENTER)
        self.cannib_treeview.heading("Eventi", text="N. Eventi", anchor=tk.CENTER)
        
        self.cannib_treeview.pack(fill="both", expand=True)
        
        # Tag for confidence levels
        self.cannib_treeview.tag_configure("confidence_A", foreground="green", font=("Helvetica", 9, "bold"))
        self.cannib_treeview.tag_configure("confidence_B", foreground="orange")
        self.cannib_treeview.tag_configure("confidence_C", foreground="red")
    
    def _build_event_uplift_tab(self):
        """Build Event/Uplift tab for delivery-date-driven demand adjustments."""
        main_frame = ttk.Frame(self.event_uplift_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="📈 Eventi/Uplift per Data Consegna", font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Label(title_frame, text="(Driver di domanda basato su eventi futuri)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
        # === EVENT UPLIFT RULE ENTRY FORM ===
        form_frame = ttk.LabelFrame(main_frame, text="Aggiungi Regola Uplift (campi obbligatori marcati con *)", padding=15)
        form_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Grid configuration
        form_frame.columnconfigure(1, weight=1)
        form_frame.columnconfigure(3, weight=1)
        
        # ROW 0: Delivery Date (obbligatorio) + Reason (obbligatorio)
        ttk.Label(form_frame, text="Data Consegna: *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=8)
        self.event_delivery_date_var = tk.StringVar(value=date.today().isoformat())
        if TKCALENDAR_AVAILABLE:
            DateEntry(  # type: ignore[misc]
                form_frame,
                textvariable=self.event_delivery_date_var,
                width=12,
                date_pattern="yyyy-mm-dd",
            ).grid(row=0, column=1, sticky="w", pady=8)
        else:
            ttk.Entry(form_frame, textvariable=self.event_delivery_date_var, width=15).grid(row=0, column=1, sticky="w", pady=8)
        self.event_delivery_date_var.trace('w', lambda *args: self._validate_event_uplift_form())
        
        ttk.Label(form_frame, text="Motivo:", font=("Helvetica", 9)).grid(row=0, column=2, sticky="e", padx=(20, 8), pady=8)
        self.event_reason_var = tk.StringVar()
        reason_combo = ttk.Combobox(
            form_frame,
            textvariable=self.event_reason_var,
            values=["", "holiday", "local_event", "weather", "payday", "closure"],
            width=25,
            state="readonly",
        )
        reason_combo.grid(row=0, column=3, sticky="w", pady=8)
        reason_combo.current(0)  # Default: empty (optional)
        self.event_reason_var.trace('w', lambda *args: self._validate_event_uplift_form())
        
        # ROW 1: Strength (obbligatorio) + Scope Type
        ttk.Label(form_frame, text="Intensità (0.0-1.0): *", font=("Helvetica", 9, "bold"), foreground="#d9534f").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=8)
        self.event_strength_var = tk.StringVar(value="0.5")
        strength_entry = ttk.Entry(form_frame, textvariable=self.event_strength_var, width=15)
        strength_entry.grid(row=1, column=1, sticky="w", pady=8)
        self.event_strength_var.trace('w', lambda *args: self._validate_event_uplift_form())
        ttk.Label(form_frame, text="(0.0=nessun effetto, 1.0=massimo)", font=("Helvetica", 8, "italic"), foreground="#777").grid(row=1, column=1, sticky="w", padx=(120, 0), pady=8)
        
        ttk.Label(form_frame, text="Ambito:", font=("Helvetica", 9)).grid(row=1, column=2, sticky="e", padx=(20, 8), pady=8)
        self.event_scope_type_var = tk.StringVar(value="ALL")
        scope_frame = ttk.Frame(form_frame)
        scope_frame.grid(row=1, column=3, columnspan=2, sticky="w", pady=8)
        ttk.Radiobutton(scope_frame, text="Tutto", variable=self.event_scope_type_var, value="ALL", command=self._on_event_scope_change).pack(side="left", padx=5)
        ttk.Radiobutton(scope_frame, text="Department", variable=self.event_scope_type_var, value="department", command=self._on_event_scope_change).pack(side="left", padx=5)
        ttk.Radiobutton(scope_frame, text="Category", variable=self.event_scope_type_var, value="category", command=self._on_event_scope_change).pack(side="left", padx=5)
        ttk.Radiobutton(scope_frame, text="SKU", variable=self.event_scope_type_var, value="sku", command=self._on_event_scope_change).pack(side="left", padx=5)
        
        # ROW 2: Scope Key (conditional) + Notes
        ttk.Label(form_frame, text="Scope Key:", font=("Helvetica", 9)).grid(row=2, column=0, sticky="e", padx=(0, 8), pady=8)
        self.event_scope_key_var = tk.StringVar()
        self.event_scope_key_entry = ttk.Entry(form_frame, textvariable=self.event_scope_key_var, width=25, state="disabled")
        self.event_scope_key_entry.grid(row=2, column=1, sticky="w", pady=8)
        ttk.Label(form_frame, text="(richiesto se ambito != Tutto)", font=("Helvetica", 8, "italic"), foreground="#777").grid(row=2, column=1, sticky="w", padx=(190, 0), pady=8)
        
        ttk.Label(form_frame, text="Note:", font=("Helvetica", 9)).grid(row=2, column=2, sticky="ne", padx=(20, 8), pady=8)
        self.event_notes_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.event_notes_var, width=40).grid(row=2, column=3, columnspan=2, sticky="w", pady=8)
        
        # ROW 3: Buttons
        button_frame = ttk.Frame(form_frame)
        button_frame.grid(row=3, column=0, columnspan=5, sticky="w", pady=(10, 0))
        
        self.event_submit_btn = ttk.Button(button_frame, text="✓ Aggiungi Regola", command=self._add_event_uplift_rule, state="disabled")
        self.event_submit_btn.pack(side="left", padx=5)
        ttk.Button(button_frame, text="✗ Cancella Modulo", command=self._clear_event_uplift_form).pack(side="left", padx=5)
        
        # Validation status label
        self.event_validation_label = ttk.Label(button_frame, text="", font=("Helvetica", 8), foreground="#d9534f")
        self.event_validation_label.pack(side="left", padx=15)
        
        # Edit mode indicator (hidden by default)
        self.event_edit_mode = False
        self.event_edit_key = None
        
        # === EVENT UPLIFT RULES TABLE ===
        table_controls_frame = ttk.Frame(main_frame)
        table_controls_frame.pack(side="top", fill="x", pady=(0, 5))
        
        ttk.Label(table_controls_frame, text="Regole Uplift Esistenti", font=("Helvetica", 11, "bold")).pack(side="left", padx=5)
        ttk.Button(table_controls_frame, text="✏️ Modifica Selezionata", command=self._edit_event_uplift_rule).pack(side="left", padx=5)
        ttk.Button(table_controls_frame, text="🗑️ Rimuovi Selezionata", command=self._remove_event_uplift_rule).pack(side="left", padx=5)
        ttk.Button(table_controls_frame, text="🔄 Aggiorna", command=self._refresh_event_uplift_tab).pack(side="left", padx=5)
        
        # Search filter
        ttk.Label(table_controls_frame, text="Filtra:").pack(side="left", padx=(20, 5))
        self.event_filter_var = tk.StringVar()
        self.event_filter_var.trace('w', lambda *args: self._filter_event_uplift_table())
        ttk.Entry(table_controls_frame, textvariable=self.event_filter_var, width=20).pack(side="left", padx=5)
        
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(table_frame)
        scrollbar.pack(side="right", fill="y")
        
        self.event_uplift_treeview = ttk.Treeview(
            table_frame,
            columns=("Data Consegna", "Motivo", "Intensità", "Ambito", "Note", "Status"),
            height=15,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.event_uplift_treeview.yview)
        
        self.event_uplift_treeview.column("#0", width=0, stretch=tk.NO)
        self.event_uplift_treeview.column("Data Consegna", anchor=tk.CENTER, width=120)
        self.event_uplift_treeview.column("Motivo", anchor=tk.W, width=150)
        self.event_uplift_treeview.column("Intensità", anchor=tk.CENTER, width=90)
        self.event_uplift_treeview.column("Ambito", anchor=tk.W, width=200)
        self.event_uplift_treeview.column("Note", anchor=tk.W, width=250)
        self.event_uplift_treeview.column("Status", anchor=tk.CENTER, width=100)
        
        self.event_uplift_treeview.heading("Data Consegna", text="Data Consegna", anchor=tk.CENTER)
        self.event_uplift_treeview.heading("Motivo", text="Motivo", anchor=tk.W)
        self.event_uplift_treeview.heading("Intensità", text="Intensità", anchor=tk.CENTER)
        self.event_uplift_treeview.heading("Ambito", text="Ambito", anchor=tk.W)
        self.event_uplift_treeview.heading("Note", text="Note", anchor=tk.W)
        self.event_uplift_treeview.heading("Status", text="Status", anchor=tk.CENTER)
        
        self.event_uplift_treeview.pack(fill="both", expand=True)
        
        # Tag for past/active/future events
        self.event_uplift_treeview.tag_configure("past", foreground="gray")
        self.event_uplift_treeview.tag_configure("active", background="#d4edda", foreground="green")
        self.event_uplift_treeview.tag_configure("future", foreground="blue")
        
        # Load initial data
        self._refresh_event_uplift_tab()
    
    def _build_settings_tab(self):
        """Build Settings tab for reorder engine configuration."""
        main_frame = ttk.Frame(self.settings_tab, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(
            title_frame,
            text="⚙️ Impostazioni Motore di Riordino",
            font=("Helvetica", 16, "bold")
        ).pack(side="left")
        ttk.Label(title_frame, text="(Parametri globali per ordini automatici)", font=("Helvetica", 9, "italic"), foreground="gray").pack(side="left", padx=(10, 0))
        
        # Search field
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(search_frame, text="🔍 Cerca parametro:", font=("Helvetica", 10)).pack(side="left", padx=(0, 5))
        self.settings_search_var = tk.StringVar()
        self.settings_search_var.trace_add("write", self._filter_settings)
        search_entry = ttk.Entry(search_frame, textvariable=self.settings_search_var, width=40)
        search_entry.pack(side="left", padx=5)
        
        # Storage for widgets
        self.settings_widgets = {}
        self.settings_section_widgets = {}  # For section auto-apply checkboxes
        self.settings_rows = []  # For search filtering
        
        # Sub-tabs notebook
        self.settings_notebook = ttk.Notebook(main_frame)
        self.settings_notebook.pack(fill="both", expand=True, pady=10)
        
        # Create sub-tabs
        self._build_storage_backend_tab()  # New: Storage backend selection
        self._build_reorder_settings_tab()
        self._build_auto_variability_settings_tab()
        self._build_monte_carlo_settings_tab()
        self._build_intermittent_settings_tab()  # New: Intermittent forecast (Croston/SBA/TSB)
        self._build_expiry_alerts_settings_tab()
        self._build_shelf_life_settings_tab()
        self._build_dashboard_settings_tab()
        self._build_event_uplift_settings_tab()
        self._build_service_level_settings_tab()
        self._build_closed_loop_settings_tab()
        self._build_holidays_settings_tab()
        self._build_promo_cannibalization_settings_tab()
        
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
    
    def _build_storage_backend_tab(self):
        """Build Storage Backend selection sub-tab."""
        from config import get_storage_backend, set_storage_backend, is_sqlite_available
        
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="💾 Storage")
        
        # Title and description
        title_label = ttk.Label(
            tab_frame,
            text="Storage Backend Configuration",
            font=("Helvetica", 14, "bold")
        )
        title_label.pack(anchor="w", pady=(0, 5))
        
        desc_label = ttk.Label(
            tab_frame,
            text="Seleziona il backend di storage per i dati. CSV è sempre disponibile, SQLite offre prestazioni migliori.",
            font=("Helvetica", 9),
            foreground="gray"
        )
        desc_label.pack(anchor="w", pady=(0, 15))
        
        # Backend selection frame
        backend_frame = ttk.LabelFrame(tab_frame, text="Backend Selection", padding=15)
        backend_frame.pack(fill="x", pady=(0, 15))
        
        # Radio buttons for backend selection
        self.storage_backend_var = tk.StringVar(value=get_storage_backend())
        
        csv_radio = ttk.Radiobutton(
            backend_frame,
            text="📄 CSV Files (Predefinito)",
            variable=self.storage_backend_var,
            value="csv"
        )
        csv_radio.pack(anchor="w", pady=5)
        
        csv_desc = ttk.Label(
            backend_frame,
            text="  → Archiviazione in file CSV/JSON. Sempre disponibile, facile da ispezionare.",
            font=("Helvetica", 9),
            foreground="gray"
        )
        csv_desc.pack(anchor="w", padx=(20, 0), pady=(0, 10))
        
        sqlite_radio = ttk.Radiobutton(
            backend_frame,
            text="🗄️ SQLite Database",
            variable=self.storage_backend_var,
            value="sqlite"
        )
        sqlite_radio.pack(anchor="w", pady=5)
        
        sqlite_desc = ttk.Label(
            backend_frame,
            text="  → Database SQLite. Prestazioni migliori, transazioni atomiche, query complesse.",
            font=("Helvetica", 9),
            foreground="gray"
        )
        sqlite_desc.pack(anchor="w", padx=(20, 0))
        
        # Status frame
        status_frame = ttk.LabelFrame(tab_frame, text="Status", padding=15)
        status_frame.pack(fill="x", pady=(0, 15))
        
        # Check SQLite availability
        sqlite_available = is_sqlite_available()
        
        if sqlite_available:
            status_text = "✓ Database SQLite inizializzato e pronto"
            status_color = "green"
        else:
            status_text = "⚠ Database SQLite non inizializzato (eseguire migrazione)"
            status_color = "orange"
        
        status_label = ttk.Label(
            status_frame,
            text=status_text,
            font=("Helvetica", 10, "bold"),
            foreground=status_color
        )
        status_label.pack(anchor="w")
        
        # Migration frame
        migration_frame = ttk.LabelFrame(tab_frame, text="Migrazione Dati", padding=15)
        migration_frame.pack(fill="x", pady=(0, 15))
        
        migration_desc = ttk.Label(
            migration_frame,
            text=(
                "Migra i dati esistenti da CSV/JSON a SQLite. Questa operazione:\n"
                "• Legge tutti i dati dai file CSV/JSON\n"
                "• Valida i dati e verifica la coerenza\n"
                "• Crea il database SQLite e popola le tabelle\n"
                "• Genera un report dettagliato\n"
                "\n"
                "⚠ Backup raccomandato prima della migrazione."
            ),
            font=("Helvetica", 9),
            foreground="gray",
            justify="left"
        )
        migration_desc.pack(anchor="w", pady=(0, 10))
        
        migrate_button = ttk.Button(
            migration_frame,
            text="🚀 Avvia Migrazione CSV → SQLite",
            command=self._run_migration_wizard
        )
        migrate_button.pack(anchor="w")
        
        # Apply button
        action_frame = ttk.Frame(tab_frame)
        action_frame.pack(fill="x", pady=(15, 0))
        
        apply_button = ttk.Button(
            action_frame,
            text="💾 Applica Cambiamenti Backend",
            command=self._apply_storage_backend_change
        )
        apply_button.pack(side="left", padx=5)
        
        info_label = ttk.Label(
            action_frame,
            text="ℹ️ Riavvio richiesto dopo il cambio backend",
            font=("Helvetica", 9),
            foreground="blue"
        )
        info_label.pack(side="left", padx=10)
    
    def _apply_storage_backend_change(self):
        """Apply storage backend change (requires restart)."""
        from config import get_storage_backend, set_storage_backend
        from typing import Literal
        
        new_backend_str = self.storage_backend_var.get()
        current_backend = get_storage_backend()
        
        # Validate backend choice
        if new_backend_str not in ('csv', 'sqlite'):
            messagebox.showerror(
                "Errore",
                f"Backend non valido: '{new_backend_str}'"
            )
            return
        
        # Type-safe cast (validated above)
        new_backend: Literal['csv', 'sqlite'] = new_backend_str  # type: ignore
        
        if new_backend == current_backend:
            messagebox.showinfo(
                "Nessun Cambio",
                f"Backend già impostato su '{new_backend}'."
            )
            return
        
        # Confirm change
        confirm = messagebox.askyesno(
            "Conferma Cambio Backend",
            f"Cambiare backend da '{current_backend}' a '{new_backend}'?\n\n"
            f"⚠ L'applicazione dovrà essere riavviata per applicare le modifiche."
        )
        
        if not confirm:
            return
        
        # Validate SQLite availability if switching to sqlite
        if new_backend == 'sqlite':
            from config import is_sqlite_available
            if not is_sqlite_available():
                messagebox.showerror(
                    "Database Non Disponibile",
                    "Database SQLite non inizializzato.\n\n"
                    "Eseguire prima la migrazione CSV → SQLite."
                )
                return
        
        # Save backend choice
        set_storage_backend(new_backend)
        
        messagebox.showinfo(
            "Backend Cambiato",
            f"Backend aggiornato a '{new_backend}'.\n\n"
            f"⚠ Riavviare l'applicazione per applicare le modifiche."
        )
        
        logger.info(f"Storage backend changed: {current_backend} → {new_backend}")
    
    def _run_migration_wizard(self):
        """Launch migration wizard dialog."""
        from .migration_wizard import MigrationWizardDialog
        
        def on_migration_complete(success: bool):
            """Callback when migration completes"""
            if success:
                logger.info("CSV → SQLite migration completed successfully")
                # Refresh storage backend tab status
                messagebox.showinfo(
                    "Migrazione Completata",
                    "Database SQLite creato con successo!\n\n"
                    "È ora possibile selezionare 'SQLite Database' "
                    "come backend di storage."
                )
            else:
                logger.error("CSV → SQLite migration failed")
        
        # Launch wizard dialog
        wizard = MigrationWizardDialog(self.root, on_complete=on_migration_complete)
        wizard.show()
    
    def _build_reorder_settings_tab(self):
        """Build Reorder Engine Parameters sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="⚙️ Parametri Base")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Auto-apply checkbox
        reorder_auto_frame = ttk.Frame(scrollable_frame)
        reorder_auto_frame.pack(fill="x", pady=(0, 10))
        reorder_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(reorder_auto_frame, text="✓ Auto-applica tutti i parametri di questa sezione ai nuovi SKU", variable=reorder_auto_var).pack(anchor="w")
        self.settings_section_widgets["reorder_engine"] = reorder_auto_var
        
        # Parameters
        reorder_params = [
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
                "key": "forecast_method",
                "label": "🎲 Metodo Forecast",
                "description": "Metodo di previsione domanda: simple (livello + DOW) o monte_carlo (simulazione)",
                "type": "choice",
                "choices": ["simple", "monte_carlo"]
            },
            {
                "key": "policy_mode",
                "label": "🎯 Modalità Policy Ordini",
                "description": "legacy (formula classica S=forecast+safety) o csl (Target Service Level con calcolo IP ottimale)",
                "type": "choice",
                "choices": ["legacy", "csl"]
            },
            {
                "key": "oos_boost_percent",
                "label": "OOS Boost (%)",
                "description": "Percentuale di incremento ordine per SKU con giorni OOS",
                "type": "int",
                "min": 0,
                "max": 100
            },
            {
                "key": "oos_lookback_days",
                "label": "Giorni Storico OOS",
                "description": "Numero giorni passati da analizzare per rilevare OOS",
                "type": "int",
                "min": 7,
                "max": 90
            },
            {
                "key": "oos_detection_mode",
                "label": "Modalità Rilevamento OOS",
                "description": "strict = on_hand=0 (più conservativo), relaxed = on_hand+on_order=0",
                "type": "choice",
                "choices": ["strict", "relaxed"]
            },
        ]
        
        self._create_param_rows(scrollable_frame, reorder_params, "reorder_engine")
    
    def _build_auto_variability_settings_tab(self):
        """Build Auto-Variability Parameters sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="⚡ Auto-Variabilità")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Auto-apply checkbox
        auto_var_auto_frame = ttk.Frame(scrollable_frame)
        auto_var_auto_frame.pack(fill="x", pady=(0, 10))
        auto_var_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(auto_var_auto_frame, text="✓ Auto-applica tutti i parametri di questa sezione ai nuovi SKU", variable=auto_var_auto_var).pack(anchor="w")
        self.settings_section_widgets["auto_variability"] = auto_var_auto_var
        
        # Parameters
        auto_var_params = [
            {
                "key": "auto_variability_enabled",
                "label": "⚡ Auto-classificazione Abilitata",
                "description": "Abilita classificazione automatica variabilità domanda al salvataggio SKU",
                "type": "bool"
            },
            {
                "key": "auto_variability_min_observations",
                "label": "Min. Osservazioni (giorni)",
                "description": "Minimo giorni vendita richiesti per auto-classificazione",
                "type": "int",
                "min": 7,
                "max": 365
            },
            {
                "key": "auto_variability_stable_percentile",
                "label": "Percentile STABLE (Q)",
                "description": "Percentile per soglia STABLE (es. 25 = primo quartile)",
                "type": "int",
                "min": 1,
                "max": 50
            },
            {
                "key": "auto_variability_high_percentile",
                "label": "Percentile HIGH (Q)",
                "description": "Percentile per soglia HIGH (es. 75 = terzo quartile)",
                "type": "int",
                "min": 50,
                "max": 99
            },
            {
                "key": "auto_variability_seasonal_threshold",
                "label": "Soglia Autocorrelazione SEASONAL",
                "description": "Soglia autocorrelazione settimanale per rilevare pattern stagionali (0-1)",
                "type": "float",
                "min": 0.0,
                "max": 1.0
            },
            {
                "key": "auto_variability_fallback_category",
                "label": "Categoria Fallback",
                "description": "Categoria assegnata se dati insufficienti",
                "type": "choice",
                "choices": ["LOW", "STABLE", "SEASONAL", "HIGH"]
            },
        ]
        
        self._create_param_rows(scrollable_frame, auto_var_params, "auto_variability")
    
    def _build_monte_carlo_settings_tab(self):
        """Build Monte Carlo Simulation Parameters sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="🎲 Monte Carlo")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Auto-apply checkbox
        mc_auto_frame = ttk.Frame(scrollable_frame)
        mc_auto_frame.pack(fill="x", pady=(0, 10))
        mc_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mc_auto_frame, text="✓ Auto-applica tutti i parametri di questa sezione ai nuovi SKU", variable=mc_auto_var).pack(anchor="w")
        self.settings_section_widgets["monte_carlo"] = mc_auto_var
        
        # Parameters
        mc_params = [
            {
                "key": "mc_distribution",
                "label": "Distribuzione",
                "description": "Tipo distribuzione per campionamento Monte Carlo",
                "type": "choice",
                "choices": ["empirical", "normal", "lognormal", "residuals"]
            },
            {
                "key": "mc_n_simulations",
                "label": "Numero Simulazioni",
                "description": "Numero traiettorie simulate (default 1000, max 10000)",
                "type": "int",
                "min": 100,
                "max": 10000
            },
            {
                "key": "mc_random_seed",
                "label": "Random Seed",
                "description": "Seed RNG (0 = casuale, >0 = deterministico)",
                "type": "int",
                "min": 0,
                "max": 999999
            },
            {
                "key": "mc_output_stat",
                "label": "Statistica Output",
                "description": "Metodo aggregazione risultati: mean (media) o percentile",
                "type": "choice",
                "choices": ["mean", "percentile"]
            },
            {
                "key": "mc_output_percentile",
                "label": "Output Percentile",
                "description": "Percentile se output_stat = percentile (50-99, default 80)",
                "type": "int",
                "min": 50,
                "max": 99
            },
            {
                "key": "mc_horizon_mode",
                "label": "Modalità Orizzonte",
                "description": "auto = lead_time + review_period, custom = manuale",
                "type": "choice",
                "choices": ["auto", "custom"]
            },
            {
                "key": "mc_horizon_days",
                "label": "Orizzonte Giorni (custom)",
                "description": "Giorni orizzonte forecast se mc_horizon_mode = custom",
                "type": "int",
                "min": 1,
                "max": 365
            },
            {
                "key": "mc_show_comparison",
                "label": "📊 Mostra Confronto MC",
                "description": "Mostra risultati Monte Carlo come colonna informativa nella proposta ordini (anche se forecast_method=simple)",
                "type": "bool"
            },
        ]
        
        self._create_param_rows(scrollable_frame, mc_params, "monte_carlo")
    
    def _build_intermittent_settings_tab(self):
        """Build Intermittent Forecast Parameters sub-tab (Croston/SBA/TSB)."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="🔢 Intermittente")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Info label with description
        info_frame = ttk.Frame(scrollable_frame)
        info_frame.pack(fill="x", pady=(0, 10))
        info_label = ttk.Label(
            info_frame,
            text="ℹ️ Metodi forecast per domanda intermittente (molti zeri): Croston, SBA, TSB",
            font=("Helvetica", 9, "italic"),
            foreground="gray"
        )
        info_label.pack(anchor="w")
        
        # Parameters
        intermittent_params = [
            {
                "key": "intermittent_enabled",
                "label": "✓ Abilita Forecast Intermittente",
                "description": "Abilita rilevamento e forecast per domanda intermittente (Croston/SBA/TSB)",
                "type": "bool"
            },
            {
                "key": "intermittent_adi_threshold",
                "label": "Soglia ADI",
                "description": "Average Demand Interval: >1.32 = intermittente (Syntetos et al.)",
                "type": "float",
                "min": 1.0,
                "max": 10.0
            },
            {
                "key": "intermittent_cv2_threshold",
                "label": "Soglia CV²",
                "description": "Squared Coefficient of Variation: >0.49 = variabile (Syntetos et al.)",
                "type": "float",
                "min": 0.0,
                "max": 5.0
            },
            {
                "key": "intermittent_alpha_default",
                "label": "Alpha Smoothing",
                "description": "Parametro smoothing per Croston/SBA/TSB (0 < alpha <= 1, default 0.1)",
                "type": "float",
                "min": 0.01,
                "max": 1.0
            },
            {
                "key": "intermittent_lookback_days",
                "label": "Lookback Giorni",
                "description": "Giorni lookback per classificazione e fitting (min 56, raccomandato 90)",
                "type": "int",
                "min": 28,
                "max": 365
            },
            {
                "key": "intermittent_min_nonzero_observations",
                "label": "Min Osservazioni Non-Zero",
                "description": "Minimo numero osservazioni non-zero richieste per fitting affidabile",
                "type": "int",
                "min": 3,
                "max": 50
            },
            {
                "key": "intermittent_backtest_enabled",
                "label": "✓ Abilita Backtest",
                "description": "Abilita backtest rolling per selezione metodo intermittente",
                "type": "bool"
            },
            {
                "key": "intermittent_backtest_periods",
                "label": "Periodi Backtest",
                "description": "Numero periodi test nel rolling origin backtest",
                "type": "int",
                "min": 2,
                "max": 12
            },
            {
                "key": "intermittent_backtest_metric",
                "label": "Metrica Backtest",
                "description": "Metrica per selezione metodo: wmape (MAPE pesato) o bias (errore medio)",
                "type": "choice",
                "choices": ["wmape", "bias"]
            },
            {
                "key": "intermittent_backtest_min_history",
                "label": "Min Storico Backtest",
                "description": "Giorni minimi storici richiesti per eseguire backtest (se <, usa default method)",
                "type": "int",
                "min": 14,
                "max": 180
            },
            {
                "key": "intermittent_default_method",
                "label": "Metodo Default",
                "description": "Metodo default per intermittenti quando backtest non disponibile (SBA raccomandato)",
                "type": "choice",
                "choices": ["croston", "sba", "tsb"]
            },
            {
                "key": "intermittent_fallback_to_simple",
                "label": "✓ Fallback a Simple",
                "description": "Fallback a simple se dati insufficienti per intermittente (raccomandato)",
                "type": "bool"
            },
            {
                "key": "intermittent_obsolescence_window",
                "label": "Finestra Obsolescenza",
                "description": "Finestra giorni per rilevare obsolescenza (declining trend), favorisce TSB",
                "type": "int",
                "min": 7,
                "max": 60
            },
            {
                "key": "intermittent_sigma_estimation_mode",
                "label": "Modalità Stima Sigma",
                "description": "Modalità stima sigma_P: rolling (residui rolling), bootstrap, o fallback (proxy da z_t)",
                "type": "choice",
                "choices": ["rolling", "bootstrap", "fallback"]
            },
        ]
        
        self._create_param_rows(scrollable_frame, intermittent_params, "intermittent_forecast")
    
    def _build_expiry_alerts_settings_tab(self):
        """Build Expiry Alerts Thresholds sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="⏰ Alert Scadenze")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Info label (no auto-apply for global settings)
        expiry_auto_frame = ttk.Frame(scrollable_frame)
        expiry_auto_frame.pack(fill="x", pady=(0, 10))
        expiry_auto_var = tk.BooleanVar(value=False)  # Not auto-applied to SKU
        ttk.Label(expiry_auto_frame, text="ℹ️ Impostazioni globali per color-coding lotti in scadenza", font=("Helvetica", 9, "italic"), foreground="gray").pack(anchor="w")
        self.settings_section_widgets["expiry_alerts"] = expiry_auto_var
        
        # Parameters
        expiry_params = [
            {
                "key": "expiry_critical_threshold_days",
                "label": "🔴 Giorni CRITICO (arancione)",
                "description": "Giorni alla scadenza per stato CRITICO (arancione)",
                "type": "int",
                "min": 1,
                "max": 30
            },
            {
                "key": "expiry_warning_threshold_days",
                "label": "🟡 Giorni ATTENZIONE (giallo)",
                "description": "Giorni alla scadenza per stato ATTENZIONE (giallo)",
                "type": "int",
                "min": 1,
                "max": 60
            }
        ]
        
        self._create_param_rows(scrollable_frame, expiry_params, "expiry_alerts")
    
    def _build_shelf_life_settings_tab(self):
        """Build Shelf Life Policy Parameters sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="♻️ Shelf Life")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Auto-apply checkbox
        shelf_life_auto_frame = ttk.Frame(scrollable_frame)
        shelf_life_auto_frame.pack(fill="x", pady=(0, 10))
        shelf_life_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(shelf_life_auto_frame, text="✓ Auto-applica tutti i parametri di questa sezione ai nuovi SKU", variable=shelf_life_auto_var).pack(anchor="w")
        self.settings_section_widgets["shelf_life_policy"] = shelf_life_auto_var
        
        # Parameters
        shelf_life_params = [
            {
                "key": "enabled",
                "label": "🔧 Abilita Shelf Life",
                "description": "Attiva controllo shelf life nel motore riordino (usa stock usabile invece di on_hand)",
                "type": "bool"
            },
            {
                "key": "min_shelf_life_global",
                "label": "Shelf Life Minima Globale (giorni)",
                "description": "Giorni minimi shelf life accettabile (default per nuovi SKU)",
                "type": "int",
                "min": 1,
                "max": 365
            },
            {
                "key": "waste_penalty_mode",
                "label": "Modalità Penalità Spreco",
                "description": "Come penalizzare prodotti a rischio spreco",
                "type": "choice",
                "choices": ["soft", "hard"]
            },
            {
                "key": "waste_penalty_factor",
                "label": "Fattore Penalità",
                "description": "Riduzione % o qty fissa se waste_risk > threshold",
                "type": "float",
                "min": 0.0,
                "max": 1.0
            },
            {
                "key": "waste_risk_threshold",
                "label": "Soglia Rischio Spreco (%)",
                "description": "Percentuale rischio oltre cui applicare penalità (0-100)",
                "type": "float",
                "min": 0.0,
                "max": 100.0
            },
            {
                "key": "waste_horizon_days",
                "label": "Orizzonte Valutazione Spreco (giorni)",
                "description": "Giorni futuri per calcolare % stock a rischio",
                "type": "int",
                "min": 1,
                "max": 90
            },
            {
                "key": "waste_realization_factor",
                "label": "Fattore Realizzazione Spreco",
                "description": "Moltiplicatore per convertire waste_risk in expected_waste_rate per Monte Carlo (0-1)",
                "type": "float",
                "min": 0.0,
                "max": 1.0
            }
        ]
        
        self._create_param_rows(scrollable_frame, shelf_life_params, "shelf_life_policy")
    
    def _build_dashboard_settings_tab(self):
        """Build Dashboard Parameters sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="📊 Dashboard")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Auto-apply checkbox
        dashboard_auto_frame = ttk.Frame(scrollable_frame)
        dashboard_auto_frame.pack(fill="x", pady=(0, 10))
        dashboard_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(dashboard_auto_frame, text="✓ Auto-applica tutti i parametri di questa sezione ai nuovi SKU", variable=dashboard_auto_var).pack(anchor="w")
        self.settings_section_widgets["dashboard"] = dashboard_auto_var
        
        # Parameters
        dashboard_params = [
            {
                "key": "stock_unit_price",
                "label": "Prezzo Unitario Stock (€)",
                "description": "Prezzo medio unitario per calcolo valore stock in Dashboard",
                "type": "int",
                "min": 1,
                "max": 10000
            }
        ]
        
        self._create_param_rows(scrollable_frame, dashboard_params, "dashboard")

    def _build_event_uplift_settings_tab(self):
        """Build Event Uplift Parameters sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="📈 Event Uplift")

        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)

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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # Auto-apply checkbox
        event_auto_frame = ttk.Frame(scrollable_frame)
        event_auto_frame.pack(fill="x", pady=(0, 10))
        event_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(event_auto_frame, text="✓ Auto-applica tutti i parametri di questa sezione ai nuovi SKU", variable=event_auto_var).pack(anchor="w")
        self.settings_section_widgets["event_uplift"] = event_auto_var

        # Parameters
        event_uplift_params = [
            {
                "key": "event_uplift_enabled",
                "label": "🔧 Abilita Event Uplift",
                "description": "Attiva aggiustamento domanda basato su eventi sulla data di consegna",
                "type": "bool"
            },
            {
                "key": "event_default_quantile",
                "label": "Quantile Default",
                "description": "Quantile per stima U_store_day (es. 0.70 = P70)",
                "type": "float",
                "min": 0.0,
                "max": 1.0
            },
            {
                "key": "event_min_factor",
                "label": "Fattore Minimo",
                "description": "Limite inferiore moltiplicatore event uplift (1.0 = neutro)",
                "type": "float",
                "min": 0.1,
                "max": 10.0
            },
            {
                "key": "event_max_factor",
                "label": "Fattore Massimo",
                "description": "Limite superiore moltiplicatore event uplift",
                "type": "float",
                "min": 0.1,
                "max": 10.0
            },
            {
                "key": "event_perishables_exclude_threshold",
                "label": "Soglia Esclusione Deperibili (giorni)",
                "description": "Escludi SKU con shelf_life <= soglia dall'event uplift",
                "type": "int",
                "min": 0,
                "max": 365
            },
            {
                "key": "event_perishables_cap_extra_days",
                "label": "Cap Extra Coverage Deperibili (giorni)",
                "description": "Limite giorni extra per deperibili (politica anti-overstock)",
                "type": "int",
                "min": 0,
                "max": 30
            },
            {
                "key": "event_apply_to",
                "label": "Modalità Applicazione",
                "description": "Applica a sola media forecast o anche alla variabilità",
                "type": "choice",
                "choices": ["forecast_only", "forecast_and_sigma"]
            },
            {
                "key": "event_similar_days_window",
                "label": "Finestra Giorni Simili (± giorni)",
                "description": "Finestra stagionale per ricerca giorni simili",
                "type": "int",
                "min": 1,
                "max": 365
            },
            {
                "key": "event_min_samples_u",
                "label": "Min Campioni U_store_day",
                "description": "Numero minimo campioni per stimare U_store_day",
                "type": "int",
                "min": 1,
                "max": 365
            },
            {
                "key": "event_min_samples_beta",
                "label": "Min Campioni Beta_i",
                "description": "Numero minimo campioni per stimare beta_i",
                "type": "int",
                "min": 1,
                "max": 365
            },
            {
                "key": "event_beta_normalization_mode",
                "label": "Normalizzazione Beta",
                "description": "Modalità normalizzazione beta_i",
                "type": "choice",
                "choices": ["mean_one", "weighted_sum_one", "none"]
            }
        ]

        self._create_param_rows(scrollable_frame, event_uplift_params, "event_uplift")
    
    def _build_holidays_settings_tab(self):
        """Build Holidays and Calendar Management sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="📅 Festività")
        
        # Instructions
        instructions = ttk.Label(
            tab_frame,
            text="Gestisci festività e chiusure che bloccano ordini e/o ricevimenti.\n"
                 "Le festività italiane ufficiali (Natale, Pasqua, ecc.) sono sempre incluse automaticamente.",
            foreground="gray",
            font=("Helvetica", 9, "italic")
        )
        instructions.pack(fill="x", pady=(0, 10))

        # Order weekdays selector
        order_days_frame = ttk.LabelFrame(tab_frame, text="Giorni validi per ordine", padding=8)
        order_days_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(
            order_days_frame,
            text="Seleziona i giorni in cui è consentito generare ordini.",
            foreground="gray",
            font=("Helvetica", 9, "italic")
        ).pack(anchor="w", pady=(0, 6))

        self.order_days_vars = {
            0: tk.BooleanVar(value=True),   # Lun
            1: tk.BooleanVar(value=True),   # Mar
            2: tk.BooleanVar(value=True),   # Mer
            3: tk.BooleanVar(value=True),   # Gio
            4: tk.BooleanVar(value=True),   # Ven
            5: tk.BooleanVar(value=False),  # Sab
            6: tk.BooleanVar(value=False),  # Dom
        }

        days_row = ttk.Frame(order_days_frame)
        days_row.pack(fill="x")

        for weekday, label in [(0, "Lun"), (1, "Mar"), (2, "Mer"), (3, "Gio"), (4, "Ven"), (5, "Sab"), (6, "Dom")]:
            ttk.Checkbutton(days_row, text=label, variable=self.order_days_vars[weekday]).pack(side="left", padx=(0, 10))
        
        # Holidays toolbar
        holidays_toolbar = ttk.Frame(tab_frame)
        holidays_toolbar.pack(fill="x", pady=(0, 5))
        
        ttk.Button(
            holidays_toolbar,
            text="➕ Aggiungi Festività",
            command=self._add_holiday
        ).pack(side="left", padx=2)
        
        ttk.Button(
            holidays_toolbar,
            text="✏️ Modifica",
            command=self._edit_holiday
        ).pack(side="left", padx=2)
        
        ttk.Button(
            holidays_toolbar,
            text="🗑️ Elimina",
            command=self._delete_holiday
        ).pack(side="left", padx=2)
        
        ttk.Button(
            holidays_toolbar,
            text="🔄 Ricarica",
            command=self._refresh_holidays_table
        ).pack(side="left", padx=2)
        
        # Holidays table
        holidays_table_frame = ttk.Frame(tab_frame)
        holidays_table_frame.pack(fill="both", expand=True, pady=5)
        
        # Scrollbar
        holidays_scrollbar = ttk.Scrollbar(holidays_table_frame)
        holidays_scrollbar.pack(side="right", fill="y")
        
        self.holidays_treeview = ttk.Treeview(
            holidays_table_frame,
            columns=("Nome", "Tipo", "Date", "Scope", "Effetto"),
            height=10,
            yscrollcommand=holidays_scrollbar.set,
        )
        holidays_scrollbar.config(command=self.holidays_treeview.yview)
        
        self.holidays_treeview.column("#0", width=0, stretch=tk.NO)
        self.holidays_treeview.column("Nome", anchor=tk.W, width=150)
        self.holidays_treeview.column("Tipo", anchor=tk.CENTER, width=80)
        self.holidays_treeview.column("Date", anchor=tk.W, width=180)
        self.holidays_treeview.column("Scope", anchor=tk.CENTER, width=100)
        self.holidays_treeview.column("Effetto", anchor=tk.CENTER, width=100)
        
        self.holidays_treeview.heading("Nome", text="Nome", anchor=tk.W)
        self.holidays_treeview.heading("Tipo", text="Tipo", anchor=tk.CENTER)
        self.holidays_treeview.heading("Date", text="Date", anchor=tk.W)
        self.holidays_treeview.heading("Scope", text="Ambito", anchor=tk.CENTER)
        self.holidays_treeview.heading("Effetto", text="Effetto", anchor=tk.CENTER)
        
        self.holidays_treeview.pack(fill="both", expand=True)
        
        # Bind double-click to edit
        self.holidays_treeview.bind("<Double-1>", lambda e: self._edit_holiday())
        
        # Load holidays
        self._refresh_holidays_table()
    
    def _build_service_level_settings_tab(self):
        """Build Service Level Metrics & KPIs Configuration sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="🎯 Service Level")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Instructions
        instructions = ttk.Label(
            scrollable_frame,
            text="Configura metrica e parametri per calcolo Service Level (KPI). "
                 "Nessun impatto sulla logica ordini in questa fase (solo impostazione framework).",
            foreground="gray",
            font=("Helvetica", 9, "italic"),
            wraplength=700,
            justify="left"
        )
        instructions.pack(fill="x", pady=(0, 15))
        
        # Service Level Parameters
        parameters = [
            {
                "key": "sl_metric",
                "label": "Metrica Service Level",
                "description": "Metodo di misurazione: 'csl' (Cycle Service Level, probabilità nessun stockout) o 'fill_rate_proxy' (stima via tracciamento OOS)",
                "type": "choice",
                "choices": ["csl", "fill_rate_proxy"]
            },
            {
                "key": "sl_default_csl",
                "label": "CSL Target Default",
                "description": "Livello di servizio target per CSL (es. 0.95 = 95% probabilità nessun stockout per ciclo riordino)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            },
            {
                "key": "sl_fill_rate_target",
                "label": "Fill Rate Target",
                "description": "Target fill-rate quando si usa metrica 'fill_rate_proxy' (% domanda servita da stock, es. 0.98 = 98%)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            },
            {
                "key": "sl_lookback_days",
                "label": "Periodo Lookback (giorni)",
                "description": "Finestra storica per calcolo KPI service level (minimo 7 giorni)",
                "type": "int",
                "min": 7,
                "max": 365
            },
            {
                "key": "sl_oos_mode",
                "label": "Modalità Rilevamento OOS",
                "description": "Strictness rilevamento OOS per KPI: 'strict' (IP=0 esatto) o 'relaxed' (sales=0 + IP basso)",
                "type": "choice",
                "choices": ["strict", "relaxed"]
            },
            {
                "key": "sl_cluster_high",
                "label": "CSL Target - HIGH Variability",
                "description": "Target CSL per SKU con variabilità domanda HIGH (alta volatilità vendite)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            },
            {
                "key": "sl_cluster_stable",
                "label": "CSL Target - STABLE Variability",
                "description": "Target CSL per SKU con variabilità domanda STABLE (vendite costanti)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            },
            {
                "key": "sl_cluster_low",
                "label": "CSL Target - LOW Variability",
                "description": "Target CSL per SKU con variabilità domanda LOW (vendite molto basse/sporadiche)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            },
            {
                "key": "sl_cluster_seasonal",
                "label": "CSL Target - SEASONAL Variability",
                "description": "Target CSL per SKU con variabilità domanda SEASONAL (pattern stagionali)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            },
            {
                "key": "sl_cluster_perishable",
                "label": "CSL Target - PERISHABLE",
                "description": "Target CSL per SKU deperibili (shelf_life <= 7 giorni)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            }
        ]
        
        self._create_param_rows(scrollable_frame, parameters, "service_level")
    
    def _build_closed_loop_settings_tab(self):
        """Build Closed-Loop KPI-Driven Tuning Configuration sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="🔄 Closed-Loop")
        
        # Scrollable container
        scroll_container = ttk.Frame(tab_frame)
        scroll_container.pack(fill="both", expand=True)
        
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
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
        
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        # Instructions
        instructions = ttk.Label(
            scrollable_frame,
            text="Sistema closed-loop che usa KPI (OOS rate, forecast accuracy, waste) per proporre/applicare "
                 "aggiustamenti controllati a target CSL per SKU. Abilita con cautela (mode='suggest' consigliato per test).",
            foreground="gray",
            font=("Helvetica", 9, "italic"),
            wraplength=700,
            justify="left"
        )
        instructions.pack(fill="x", pady=(0, 15))
        
        # Closed-Loop Parameters
        parameters = [
            {
                "key": "cl_enabled",
                "label": "✓ Abilitato",
                "description": "Abilita closed-loop tuning automatico (False = nessun aggiustamento automatico)",
                "type": "bool"
            },
            {
                "key": "cl_review_frequency_days",
                "label": "Frequenza Review (giorni)",
                "description": "Ogni quanti giorni eseguire analisi closed-loop (consigliato: 7-14 giorni)",
                "type": "int",
                "min": 1,
                "max": 90
            },
            {
                "key": "cl_max_alpha_step_per_review",
                "label": "Max Step CSL per Review",
                "description": "Massimo incremento/decremento CSL per ciclo review (es. 0.02 = max ±2%)",
                "type": "float",
                "min": 0.001,
                "max": 0.10
            },
            {
                "key": "cl_oos_rate_threshold",
                "label": "Soglia OOS Rate",
                "description": "Soglia % OOS per triggerare aumento CSL (es. 0.05 = 5% stockout rate)",
                "type": "float",
                "min": 0.0,
                "max": 1.0
            },
            {
                "key": "cl_wmape_threshold",
                "label": "Soglia WMAPE (Reliability)",
                "description": "Soglia WMAPE max per forecast affidabile (blocca CSL change se superata)",
                "type": "float",
                "min": 0.0,
                "max": 2.0
            },
            {
                "key": "cl_waste_rate_threshold",
                "label": "Soglia Waste Rate (Perishable)",
                "description": "Soglia % waste per deperibili che triggera riduzione CSL (es. 0.10 = 10%)",
                "type": "float",
                "min": 0.0,
                "max": 1.0
            },
            {
                "key": "cl_min_waste_events",
                "label": "Min Eventi WASTE",
                "description": "Numero minimo eventi WASTE nel lookback per decisioni robuste waste-based",
                "type": "int",
                "min": 1,
                "max": 50
            },
            {
                "key": "cl_action_mode",
                "label": "Modalità Azione",
                "description": "Mode: 'suggest' (solo report, no auto-update) o 'apply' (aggiorna automaticamente SKU.target_csl)",
                "type": "choice",
                "choices": ["suggest", "apply"]
            },
            {
                "key": "cl_min_csl_absolute",
                "label": "CSL Min Assoluto",
                "description": "Floor assoluto per CSL (hard limit, override resolver MIN_CSL)",
                "type": "float",
                "min": 0.01,
                "max": 0.999
            },
            {
                "key": "cl_max_csl_absolute",
                "label": "CSL Max Assoluto",
                "description": "Ceiling assoluto per CSL (hard limit, override resolver MAX_CSL)",
                "type": "float",
                "min": 0.01,
                "max": 0.9999
            }
        ]
        
        self._create_param_rows(scrollable_frame, parameters, "closed_loop")
        
        # Add action button section
        action_frame = ttk.LabelFrame(scrollable_frame, text="⚙️ Esecuzione Analisi", padding=10)
        action_frame.pack(fill="x", pady=(20, 10))
        
        ttk.Label(
            action_frame,
            text="Esegui analisi closed-loop manualmente per visualizzare decisioni proposte:",
            font=("Helvetica", 9)
        ).pack(anchor="w", pady=(0, 10))
        
        btn_frame = ttk.Frame(action_frame)
        btn_frame.pack(fill="x")
        
        ttk.Button(
            btn_frame,
            text="▶ Esegui Analisi Closed-Loop",
            command=self._run_closed_loop_analysis
        ).pack(side="left", padx=5)
        
        # Results treeview
        results_frame = ttk.LabelFrame(scrollable_frame, text="📊 Risultati Ultima Analisi", padding=10)
        results_frame.pack(fill="both", expand=True, pady=(10, 0))
        
        # Create treeview
        columns = ("SKU", "CSL Attuale", "CSL Suggerito", "Delta", "Azione", "Ragione", "OOS%", "WMAPE%", "Waste%")
        self.closed_loop_treeview = ttk.Treeview(results_frame, columns=columns, show="headings", height=12)
        
        # Configure columns
        col_widths = {
            "SKU": 100,
            "CSL Attuale": 80,
            "CSL Suggerito": 90,
            "Delta": 60,
            "Azione": 80,
            "Ragione": 180,
            "OOS%": 60,
            "WMAPE%": 70,
            "Waste%": 65
        }
        
        for col in columns:
            self.closed_loop_treeview.heading(col, text=col)
            self.closed_loop_treeview.column(col, width=col_widths.get(col, 100), anchor="center")
        
        # Scrollbar for treeview
        tree_scroll = ttk.Scrollbar(results_frame, orient="vertical", command=self.closed_loop_treeview.yview)
        self.closed_loop_treeview.configure(yscrollcommand=tree_scroll.set)
        
        self.closed_loop_treeview.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        
        # Configure tags for actions
        self.closed_loop_treeview.tag_configure("increase", foreground="blue")
        self.closed_loop_treeview.tag_configure("decrease", foreground="orange")
        self.closed_loop_treeview.tag_configure("blocked", foreground="red")
        self.closed_loop_treeview.tag_configure("hold", foreground="gray")
    
    def _build_promo_cannibalization_settings_tab(self):
        """Build Promo Cannibalization (Downlift) Configuration sub-tab."""
        tab_frame = ttk.Frame(self.settings_notebook, padding=10)
        self.settings_notebook.add(tab_frame, text="📉 Cannibalizzazione")
        
        # Instructions
        instructions = ttk.Label(
            tab_frame,
            text="Configura riduzione forecast per SKU non-promo quando sostituti sono in promo.\n"
                 "Gruppi sostituti: formato JSON {\"group_id\": [\"sku_1\", \"sku_2\", ...]}",
            foreground="gray",
            font=("Helvetica", 9, "italic"),
            justify="left"
        )
        instructions.pack(fill="x", pady=(0, 10))
        
        # Enable checkbox
        self.cannibalization_enabled_var = tk.BooleanVar()
        enable_check = ttk.Checkbutton(
            tab_frame,
            text="✓ Abilita cannibalizzazione (downlift automatico)",
            variable=self.cannibalization_enabled_var
        )
        enable_check.pack(anchor="w", pady=(0, 10))
        
        # Parameters frame
        params_frame = ttk.LabelFrame(tab_frame, text="Parametri Downlift", padding=10)
        params_frame.pack(fill="x", pady=(0, 10))
        
        # Downlift clamp min/max
        ttk.Label(params_frame, text="Downlift Min (es. 0.6 = max -40%):").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.downlift_min_var = tk.DoubleVar(value=0.6)
        ttk.Entry(params_frame, textvariable=self.downlift_min_var, width=10).grid(row=0, column=1, sticky="w", padx=5, pady=2)
        
        ttk.Label(params_frame, text="Downlift Max (es. 1.0 = neutro):").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.downlift_max_var = tk.DoubleVar(value=1.0)
        ttk.Entry(params_frame, textvariable=self.downlift_max_var, width=10).grid(row=1, column=1, sticky="w", padx=5, pady=2)
        
        ttk.Label(params_frame, text="Eventi minimi per stima:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.downlift_min_events_var = tk.IntVar(value=2)
        ttk.Entry(params_frame, textvariable=self.downlift_min_events_var, width=10).grid(row=2, column=1, sticky="w", padx=5, pady=2)
        
        ttk.Label(params_frame, text="Giorni validi minimi:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.downlift_min_valid_days_var = tk.IntVar(value=7)
        ttk.Entry(params_frame, textvariable=self.downlift_min_valid_days_var, width=10).grid(row=3, column=1, sticky="w", padx=5, pady=2)
        
        # Substitute groups editor
        groups_frame = ttk.LabelFrame(tab_frame, text="Gruppi Sostituti (JSON)", padding=10)
        groups_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Text widget for JSON editing
        groups_text_frame = ttk.Frame(groups_frame)
        groups_text_frame.pack(fill="both", expand=True)
        
        groups_scrollbar = ttk.Scrollbar(groups_text_frame)
        groups_scrollbar.pack(side="right", fill="y")
        
        self.substitute_groups_text = tk.Text(
            groups_text_frame,
            wrap="word",
            width=60,
            height=12,
            font=("Courier", 9),
            yscrollcommand=groups_scrollbar.set
        )
        self.substitute_groups_text.pack(side="left", fill="both", expand=True)
        groups_scrollbar.config(command=self.substitute_groups_text.yview)
        
        # Example hint
        example_label = ttk.Label(
            groups_frame,
            text='Esempio: {\"GRUPPO_A\": [\"SKU001\", \"SKU002\"], \"GRUPPO_B\": [\"SKU003\", \"SKU004\", \"SKU005\"]}',
            foreground="gray",
            font=("Courier", 8, "italic")
        )
        example_label.pack(fill="x", pady=(5, 0))
        
        # Buttons
        buttons_frame = ttk.Frame(tab_frame)
        buttons_frame.pack(fill="x", pady=(5, 0))
        
        ttk.Button(
            buttons_frame,
            text="💾 Salva Cannibalizzazione",
            command=self._save_cannibalization_settings
        ).pack(side="left", padx=5)
        
        ttk.Button(
            buttons_frame,
            text="🔄 Ricarica",
            command=self._refresh_cannibalization_settings
        ).pack(side="left", padx=5)
        
        # Load current settings
        self._refresh_cannibalization_settings()
    
    def _refresh_cannibalization_settings(self):
        """Load cannibalization settings from config into UI widgets."""
        settings = self.csv_layer.read_settings()
        cannib_settings = settings.get("promo_cannibalization", {})
        
        # Load enabled flag
        enabled = cannib_settings.get("enabled", {}).get("value", False)
        self.cannibalization_enabled_var.set(enabled)
        
        # Load parameters
        self.downlift_min_var.set(cannib_settings.get("downlift_min", {}).get("value", 0.6))
        self.downlift_max_var.set(cannib_settings.get("downlift_max", {}).get("value", 1.0))
        self.downlift_min_events_var.set(cannib_settings.get("min_events_target_sku", {}).get("value", 2))
        self.downlift_min_valid_days_var.set(cannib_settings.get("min_valid_days", {}).get("value", 7))
        
        # Load substitute groups as formatted JSON
        groups = cannib_settings.get("substitute_groups", {}).get("value", {})
        self.substitute_groups_text.delete("1.0", "end")
        import json
        groups_json = json.dumps(groups, indent=2, ensure_ascii=False)
        self.substitute_groups_text.insert("1.0", groups_json)
    
    def _save_cannibalization_settings(self):
        """Save cannibalization settings from UI to config."""
        import json
        
        # Validate substitute_groups JSON
        groups_str = self.substitute_groups_text.get("1.0", "end-1c").strip()
        try:
            if groups_str:
                substitute_groups = json.loads(groups_str)
                if not isinstance(substitute_groups, dict):
                    raise ValueError("Deve essere un dizionario JSON")
                # Validate structure: {group_id: [sku...]}
                for group_id, skus in substitute_groups.items():
                    if not isinstance(skus, list):
                        raise ValueError(f"Gruppo '{group_id}' deve contenere una lista di SKU")
            else:
                substitute_groups = {}
        except (json.JSONDecodeError, ValueError) as e:
            messagebox.showerror(
                "Errore JSON",
                f"Formato non valido per gruppi sostituti:\n{e}\n\n"
                "Usa formato: {\"group_id\": [\"sku1\", \"sku2\"]}"
            )
            return
        
        # Prepare settings dict
        settings = self.csv_layer.read_settings()
        if "promo_cannibalization" not in settings:
            settings["promo_cannibalization"] = {}
        
        cannib = settings["promo_cannibalization"]
        cannib["enabled"] = {"value": self.cannibalization_enabled_var.get()}
        cannib["downlift_min"] = {"value": self.downlift_min_var.get()}
        cannib["downlift_max"] = {"value": self.downlift_max_var.get()}
        cannib["min_events_target_sku"] = {"value": self.downlift_min_events_var.get()}
        cannib["min_valid_days"] = {"value": self.downlift_min_valid_days_var.get()}
        cannib["substitute_groups"] = {"value": substitute_groups}
        
        # Write settings
        self.csv_layer.write_settings(settings)
        messagebox.showinfo("Salvato", "Impostazioni cannibalizzazione salvate.")
    
    def _refresh_settings_tab(self):
        """Refresh settings tab with current values."""
        try:
            settings = self.csv_layer.read_settings()
            
            # Parameter mapping
            param_map = {
                "lead_time_days": ("reorder_engine", "lead_time_days"),
                "moq": ("reorder_engine", "moq"),
                "pack_size": ("reorder_engine", "pack_size"),
                "review_period": ("reorder_engine", "review_period"),
                "safety_stock": ("reorder_engine", "safety_stock"),
                "max_stock": ("reorder_engine", "max_stock"),
                "reorder_point": ("reorder_engine", "reorder_point"),
                "demand_variability": ("reorder_engine", "demand_variability"),
                "forecast_method": ("reorder_engine", "forecast_method"),
                "policy_mode": ("reorder_engine", "policy_mode"),
                "oos_boost_percent": ("reorder_engine", "oos_boost_percent"),
                "oos_lookback_days": ("reorder_engine", "oos_lookback_days"),
                "oos_detection_mode": ("reorder_engine", "oos_detection_mode"),
                "auto_variability_enabled": ("auto_variability", "enabled"),
                "auto_variability_min_observations": ("auto_variability", "min_observations"),
                "auto_variability_stable_percentile": ("auto_variability", "stable_percentile"),
                "auto_variability_high_percentile": ("auto_variability", "high_percentile"),
                "auto_variability_seasonal_threshold": ("auto_variability", "seasonal_threshold"),
                "auto_variability_fallback_category": ("auto_variability", "fallback_category"),
                "mc_distribution": ("monte_carlo", "distribution"),
                "mc_n_simulations": ("monte_carlo", "n_simulations"),
                "mc_random_seed": ("monte_carlo", "random_seed"),
                "mc_output_stat": ("monte_carlo", "output_stat"),
                "mc_output_percentile": ("monte_carlo", "output_percentile"),
                "mc_horizon_mode": ("monte_carlo", "horizon_mode"),
                "mc_horizon_days": ("monte_carlo", "horizon_days"),
                "mc_show_comparison": ("monte_carlo", "show_comparison"),
                "expiry_critical_threshold_days": ("expiry_alerts", "critical_threshold_days"),
                "expiry_warning_threshold_days": ("expiry_alerts", "warning_threshold_days"),
                "stock_unit_price": ("dashboard", "stock_unit_price"),
                "event_uplift_enabled": ("event_uplift", "enabled"),
                "event_default_quantile": ("event_uplift", "default_quantile"),
                "event_min_factor": ("event_uplift", "min_factor"),
                "event_max_factor": ("event_uplift", "max_factor"),
                "event_perishables_exclude_threshold": ("event_uplift", "perishables_policy_exclude_if_shelf_life_days_lte"),
                "event_perishables_cap_extra_days": ("event_uplift", "perishables_policy_cap_extra_cover_days_per_sku"),
                "event_apply_to": ("event_uplift", "apply_to"),
                "event_similar_days_window": ("event_uplift", "similar_days_seasonal_window"),
                "event_min_samples_u": ("event_uplift", "min_samples_u_estimation"),
                "event_min_samples_beta": ("event_uplift", "min_samples_beta_estimation"),
                "event_beta_normalization_mode": ("event_uplift", "beta_normalization_mode"),
                "sl_metric": ("service_level", "metric"),
                "sl_default_csl": ("service_level", "default_csl"),
                "sl_fill_rate_target": ("service_level", "fill_rate_target"),
                "sl_lookback_days": ("service_level", "lookback_days"),
                "sl_oos_mode": ("service_level", "oos_mode"),
                "sl_cluster_high": ("service_level", "cluster_csl_high"),
                "sl_cluster_stable": ("service_level", "cluster_csl_stable"),
                "sl_cluster_low": ("service_level", "cluster_csl_low"),
                "sl_cluster_seasonal": ("service_level", "cluster_csl_seasonal"),
                "sl_cluster_perishable": ("service_level", "cluster_csl_perishable"),
                "cl_enabled": ("closed_loop", "enabled"),
                "cl_review_frequency_days": ("closed_loop", "review_frequency_days"),
                "cl_max_alpha_step_per_review": ("closed_loop", "max_alpha_step_per_review"),
                "cl_oos_rate_threshold": ("closed_loop", "oos_rate_threshold"),
                "cl_wmape_threshold": ("closed_loop", "wmape_threshold"),
                "cl_waste_rate_threshold": ("closed_loop", "waste_rate_threshold"),
                "cl_min_waste_events": ("closed_loop", "min_waste_events"),
                "cl_action_mode": ("closed_loop", "action_mode"),
                "cl_min_csl_absolute": ("closed_loop", "min_csl_absolute"),
                "cl_max_csl_absolute": ("closed_loop", "max_csl_absolute"),
            }
            
            # Load widget values
            for param_key, widget_data in self.settings_widgets.items():
                if param_key not in param_map:
                    continue
                section, settings_key = param_map[param_key]
                param_config = settings.get(section, {}).get(settings_key, {})
                widget_data["value_var"].set(param_config.get("value", 0))
            
            # Load section auto-apply checkboxes
            for section_key, section_var in self.settings_section_widgets.items():
                # Check if any parameter in this section has auto_apply_to_new_sku = True
                section_params = [k for k, v in param_map.items() if v[0] == section_key]
                if section_params:
                    first_param = section_params[0]
                    section, settings_key = param_map[first_param]
                    param_config = settings.get(section, {}).get(settings_key, {})
                    section_var.set(param_config.get("auto_apply_to_new_sku", True))

            # Load configured order weekdays (0=Mon ... 6=Sun)
            configured_days = settings.get("calendar", {}).get("order_days", {}).get("value", [0, 1, 2, 3, 4])
            active_days = {
                int(day)
                for day in configured_days
                if isinstance(day, int) or (isinstance(day, str) and str(day).isdigit())
            }
            if hasattr(self, "order_days_vars"):
                for weekday, var in self.order_days_vars.items():
                    var.set(weekday in active_days)
            
            # Reset modified flag after loading
            self.settings_modified = False
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile caricare impostazioni: {str(e)}")
    
    def _save_settings(self):
        """Save settings to JSON file."""
        try:
            # Reset modified flag
            self.settings_modified = False
            
            settings = self.csv_layer.read_settings()
            
            # Parameter mapping (same as _refresh)
            param_map = {
                "lead_time_days": ("reorder_engine", "lead_time_days"),
                "moq": ("reorder_engine", "moq"),
                "pack_size": ("reorder_engine", "pack_size"),
                "review_period": ("reorder_engine", "review_period"),
                "safety_stock": ("reorder_engine", "safety_stock"),
                "max_stock": ("reorder_engine", "max_stock"),
                "reorder_point": ("reorder_engine", "reorder_point"),
                "demand_variability": ("reorder_engine", "demand_variability"),
                "forecast_method": ("reorder_engine", "forecast_method"),
                "policy_mode": ("reorder_engine", "policy_mode"),
                "oos_boost_percent": ("reorder_engine", "oos_boost_percent"),
                "oos_lookback_days": ("reorder_engine", "oos_lookback_days"),
                "oos_detection_mode": ("reorder_engine", "oos_detection_mode"),
                "auto_variability_enabled": ("auto_variability", "enabled"),
                "auto_variability_min_observations": ("auto_variability", "min_observations"),
                "auto_variability_stable_percentile": ("auto_variability", "stable_percentile"),
                "auto_variability_high_percentile": ("auto_variability", "high_percentile"),
                "auto_variability_seasonal_threshold": ("auto_variability", "seasonal_threshold"),
                "auto_variability_fallback_category": ("auto_variability", "fallback_category"),
                "mc_distribution": ("monte_carlo", "distribution"),
                "mc_n_simulations": ("monte_carlo", "n_simulations"),
                "mc_random_seed": ("monte_carlo", "random_seed"),
                "mc_output_stat": ("monte_carlo", "output_stat"),
                "mc_output_percentile": ("monte_carlo", "output_percentile"),
                "mc_horizon_mode": ("monte_carlo", "horizon_mode"),
                "mc_horizon_days": ("monte_carlo", "horizon_days"),
                "mc_show_comparison": ("monte_carlo", "show_comparison"),
                "expiry_critical_threshold_days": ("expiry_alerts", "critical_threshold_days"),
                "expiry_warning_threshold_days": ("expiry_alerts", "warning_threshold_days"),
                "stock_unit_price": ("dashboard", "stock_unit_price"),
                "event_uplift_enabled": ("event_uplift", "enabled"),
                "event_default_quantile": ("event_uplift", "default_quantile"),
                "event_min_factor": ("event_uplift", "min_factor"),
                "event_max_factor": ("event_uplift", "max_factor"),
                "event_perishables_exclude_threshold": ("event_uplift", "perishables_policy_exclude_if_shelf_life_days_lte"),
                "event_perishables_cap_extra_days": ("event_uplift", "perishables_policy_cap_extra_cover_days_per_sku"),
                "event_apply_to": ("event_uplift", "apply_to"),
                "event_similar_days_window": ("event_uplift", "similar_days_seasonal_window"),
                "event_min_samples_u": ("event_uplift", "min_samples_u_estimation"),
                "event_min_samples_beta": ("event_uplift", "min_samples_beta_estimation"),
                "event_beta_normalization_mode": ("event_uplift", "beta_normalization_mode"),
                "sl_metric": ("service_level", "metric"),
                "sl_default_csl": ("service_level", "default_csl"),
                "sl_fill_rate_target": ("service_level", "fill_rate_target"),
                "sl_lookback_days": ("service_level", "lookback_days"),
                "sl_oos_mode": ("service_level", "oos_mode"),
                "sl_cluster_high": ("service_level", "cluster_csl_high"),
                "sl_cluster_stable": ("service_level", "cluster_csl_stable"),
                "sl_cluster_low": ("service_level", "cluster_csl_low"),
                "sl_cluster_seasonal": ("service_level", "cluster_csl_seasonal"),
                "sl_cluster_perishable": ("service_level", "cluster_csl_perishable"),
                "cl_enabled": ("closed_loop", "enabled"),
                "cl_review_frequency_days": ("closed_loop", "review_frequency_days"),
                "cl_max_alpha_step_per_review": ("closed_loop", "max_alpha_step_per_review"),
                "cl_oos_rate_threshold": ("closed_loop", "oos_rate_threshold"),
                "cl_wmape_threshold": ("closed_loop", "wmape_threshold"),
                "cl_waste_rate_threshold": ("closed_loop", "waste_rate_threshold"),
                "cl_min_waste_events": ("closed_loop", "min_waste_events"),
                "cl_action_mode": ("closed_loop", "action_mode"),
                "cl_min_csl_absolute": ("closed_loop", "min_csl_absolute"),
                "cl_max_csl_absolute": ("closed_loop", "max_csl_absolute"),
            }
            
            # Update settings from widgets
            for param_key, widget_data in self.settings_widgets.items():
                if param_key not in param_map:
                    continue
                section, settings_key = param_map[param_key]
                
                # Ensure section exists
                if section not in settings:
                    settings[section] = {}
                
                # Get auto-apply from section checkbox
                auto_apply = self.settings_section_widgets.get(section, tk.BooleanVar(value=True)).get()
                
                settings[section][settings_key] = {
                    "value": widget_data["value_var"].get(),
                    "auto_apply_to_new_sku": auto_apply
                }

            # Save configured order weekdays (0=Mon ... 6=Sun)
            selected_order_days = []
            if hasattr(self, "order_days_vars"):
                selected_order_days = [weekday for weekday, var in self.order_days_vars.items() if var.get()]

            if not selected_order_days:
                messagebox.showerror("Errore di Validazione", "Seleziona almeno un giorno valido per ordine.")
                return

            if "calendar" not in settings:
                settings["calendar"] = {}
            settings["calendar"]["order_days"] = {
                "value": sorted(selected_order_days),
                "description": "Valid order weekdays (0=Mon ... 6=Sun)"
            }
            
            # Write to file
            self.csv_layer.write_settings(settings)
            
            # Update OrderWorkflow lead_time if changed
            lead_time = settings["reorder_engine"]["lead_time_days"]["value"]
            self.order_workflow = OrderWorkflow(self.csv_layer, lead_time_days=lead_time)
            
            # Update expiry thresholds if changed
            self.expiry_critical_days = settings.get("expiry_alerts", {}).get("critical_threshold_days", {}).get("value", 7)
            self.expiry_warning_days = settings.get("expiry_alerts", {}).get("warning_threshold_days", {}).get("value", 14)
            
            # Refresh expiry tab with new thresholds
            self._refresh_expiry_alerts()
            
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
    
    def _mark_settings_modified(self):
        """Mark settings as modified (called by widget trace)."""
        self.settings_modified = True
    
    def _on_tab_changed(self, event):
        """Handle tab change event - check for unsaved settings."""
        # This event fires AFTER the tab has changed
        # We need to track the previous tab to detect leaving settings tab
        
        # Get current (new) tab index
        try:
            new_tab_index = self.notebook.index(self.notebook.select())
        except Exception:
            return
        
        # Find settings tab index
        settings_tab_index = None
        for i in range(self.notebook.index("end")):
            try:
                if self.notebook.tab(i, "text") == "⚙️ Impostazioni":
                    settings_tab_index = i
                    break
            except:
                continue
        
        if settings_tab_index is None:
            return
        
        # Get previous tab index from stored state
        if not hasattr(self, '_previous_tab_index'):
            self._previous_tab_index = new_tab_index
            return
        
        previous_tab_index = self._previous_tab_index
        self._previous_tab_index = new_tab_index
        
        # If we just LEFT the settings tab with unsaved changes
        if previous_tab_index == settings_tab_index and self.settings_modified and new_tab_index != settings_tab_index:
            # Ask user what to do
            response = messagebox.askyesnocancel(
                "Impostazioni Non Salvate",
                "Ci sono modifiche non salvate nelle impostazioni.\n\nVuoi salvare prima di cambiare tab?",
                icon='warning'
            )
            
            if response is True:  # Yes - save and continue
                self._save_settings()
                self.settings_modified = False
            elif response is False:  # No - discard and continue
                self.settings_modified = False
                # Reload original values when user returns to settings
            else:  # Cancel - go back to settings tab
                self.notebook.select(settings_tab_index)
                self._previous_tab_index = settings_tab_index
                return
    
    def _check_settings_save_on_tab_change(self, target_tab_index):
        """Check if settings need saving before changing tabs.
        
        Args:
            target_tab_index: The tab index we want to switch to
            
        Returns:
            True if tab change should proceed, False otherwise
        """
        # Get settings tab index
        settings_tab_index = None
        for i in range(self.notebook.index("end")):
            if self.notebook.tab(i, "text") == "⚙️ Impostazioni":
                settings_tab_index = i
                break
        
        if settings_tab_index is None:
            return True
        
        # Get current tab
        try:
            current_tab_index = self.notebook.index(self.notebook.select())
        except Exception:
            return True
        
        # If we're on settings tab and have unsaved changes
        if current_tab_index == settings_tab_index and self.settings_modified:
            # Ask user what to do
            response = messagebox.askyesnocancel(
                "Impostazioni Non Salvate",
                "Ci sono modifiche non salvate nelle impostazioni.\n\nVuoi salvare prima di cambiare tab?",
                icon='warning'
            )
            
            if response is True:  # Yes - save
                self._save_settings()
                self.settings_modified = False
                return True
            elif response is False:  # No - discard
                self.settings_modified = False
                self._refresh_settings_tab()  # Reload original values
                return True
            else:  # Cancel - stay on current tab
                return False
        
        return True
    
    def _on_tab_press(self, event):
        """Handle mouse press on tab for drag-and-drop reordering."""
        # Check if click is on a tab
        try:
            clicked = self.notebook.tk.call(self.notebook._w, "identify", "tab", event.x, event.y)  # type: ignore[attr-defined]
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
            target = self.notebook.tk.call(self.notebook._w, "identify", "tab", event.x, event.y)  # type: ignore[attr-defined]
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
        default_order = ["stock", "order", "receiving", "exception", "expiry", "promo", "event_uplift", "dashboard", "admin", "settings"]
        
        try:
            settings = self.csv_layer.read_settings()
            if "ui" in settings and "tab_order" in settings["ui"]:
                saved_order = settings["ui"]["tab_order"]
                
                # Migration: If saved_order is missing new tabs, add them before dashboard
                if set(saved_order) != set(default_order):
                    # Find missing tabs
                    missing_tabs = [t for t in default_order if t not in saved_order]
                    
                    if missing_tabs:
                        # Insert missing tabs before "dashboard" (or at end if dashboard not found)
                        if "dashboard" in saved_order:
                            dashboard_idx = saved_order.index("dashboard")
                            for tab in missing_tabs:
                                saved_order.insert(dashboard_idx, tab)
                                dashboard_idx += 1
                        else:
                            saved_order.extend(missing_tabs)
                        
                        # Save migrated order
                        settings["ui"]["tab_order"] = saved_order
                        self.csv_layer.write_settings(settings)
                        logger.info(f"Tab order migrated: added {missing_tabs}")
                
                # Validate: all tabs must be present
                if set(saved_order) == set(default_order):
                    return saved_order
        except Exception as e:
            logger.warning(f"Failed to load tab order: {e}")
        
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
    
    # ============ Holiday Management ============
    
    def _refresh_holidays_table(self):
        """Refresh holidays table from holidays.json."""
        self.holidays_treeview.delete(*self.holidays_treeview.get_children())
        
        holidays = self.csv_layer.read_holidays()
        
        for holiday in holidays:
            name = holiday.get("name", "")
            holiday_type = holiday.get("type", "")
            scope = holiday.get("scope", "")
            effect = holiday.get("effect", "")
            params = holiday.get("params", {})
            
            # Format dates based on type
            if holiday_type == "single":
                date_str = params.get("date", "")
            elif holiday_type == "range":
                start = params.get("start", "")
                end = params.get("end", "")
                date_str = f"{start} → {end}"
            elif holiday_type == "fixed":
                day = params.get("day", "")
                date_str = f"Giorno {day} di ogni mese"
            else:
                date_str = str(params)
            
            # Format effect
            effect_labels = {
                "no_order": "No Ordini",
                "no_receipt": "No Ricevimenti",
                "both": "Entrambi"
            }
            effect_label = effect_labels.get(effect, effect)
            
            # Format scope
            scope_labels = {
                "logistics": "Logistica",
                "orders": "Ordini",
                "receipts": "Ricevimenti"
            }
            scope_label = scope_labels.get(scope, scope)
            
            self.holidays_treeview.insert(
                "",
                "end",
                values=(name, holiday_type, date_str, scope_label, effect_label)
            )
    
    def _add_holiday(self):
        """Open dialog to add a new holiday."""
        self._show_holiday_dialog(mode="add")
    
    def _edit_holiday(self):
        """Open dialog to edit selected holiday."""
        selection = self.holidays_treeview.selection()
        if not selection:
            messagebox.showwarning("Nessuna selezione", "Seleziona una festività da modificare.")
            return
        
        # Get index of selected holiday
        index = self.holidays_treeview.index(selection[0])
        holidays = self.csv_layer.read_holidays()
        
        if 0 <= index < len(holidays):
            self._show_holiday_dialog(mode="edit", index=index, holiday=holidays[index])
    
    def _delete_holiday(self):
        """Delete selected holiday."""
        selection = self.holidays_treeview.selection()
        if not selection:
            messagebox.showwarning("Nessuna selezione", "Seleziona una festività da eliminare.")
            return
        
        # Get index of selected holiday
        index = self.holidays_treeview.index(selection[0])
        holidays = self.csv_layer.read_holidays()
        
        if 0 <= index < len(holidays):
            holiday = holidays[index]
            confirm = messagebox.askyesno(
                "Conferma eliminazione",
                f"Eliminare la festività '{holiday.get('name', '')}'?"
            )
            if confirm:
                try:
                    self.csv_layer.delete_holiday(index)
                    self._refresh_holidays_table()
                    self._reload_calendar()
                    messagebox.showinfo("Successo", "Festività eliminata con successo.")
                except Exception as e:
                    messagebox.showerror("Errore", f"Errore durante l'eliminazione: {str(e)}")
    
    def _show_holiday_dialog(self, mode="add", index=None, holiday=None):
        """
        Show dialog to add or edit a holiday.
        
        Args:
            mode: "add" or "edit"
            index: Index of holiday to edit (for mode="edit")
            holiday: Holiday data to edit (for mode="edit")
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Aggiungi Festività" if mode == "add" else "Modifica Festività")
        dialog.geometry("500x450")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Main frame
        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Name
        ttk.Label(main_frame, text="Nome festività:", font=("Helvetica", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
        name_var = tk.StringVar(value=holiday.get("name", "") if holiday else "")
        ttk.Entry(main_frame, textvariable=name_var, width=40).grid(row=0, column=1, pady=5, sticky="ew")
        
        # Type
        ttk.Label(main_frame, text="Tipo:", font=("Helvetica", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
        type_var = tk.StringVar(value=holiday.get("type", "single") if holiday else "single")
        type_combo = ttk.Combobox(main_frame, textvariable=type_var, values=["single", "range", "fixed"], state="readonly", width=37)
        type_combo.grid(row=1, column=1, pady=5, sticky="ew")
        
        # Scope
        ttk.Label(main_frame, text="Ambito:", font=("Helvetica", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5)
        scope_var = tk.StringVar(value=holiday.get("scope", "logistics") if holiday else "logistics")
        scope_combo = ttk.Combobox(main_frame, textvariable=scope_var, values=["logistics", "orders", "receipts"], state="readonly", width=37)
        scope_combo.grid(row=2, column=1, pady=5, sticky="ew")
        
        # Effect
        ttk.Label(main_frame, text="Effetto:", font=("Helvetica", 10, "bold")).grid(row=3, column=0, sticky="w", pady=5)
        effect_var = tk.StringVar(value=holiday.get("effect", "both") if holiday else "both")
        effect_combo = ttk.Combobox(main_frame, textvariable=effect_var, values=["no_order", "no_receipt", "both"], state="readonly", width=37)
        effect_combo.grid(row=3, column=1, pady=5, sticky="ew")
        
        # Date parameters frame (changes based on type)
        params_frame = ttk.LabelFrame(main_frame, text="Parametri Data", padding=10)
        params_frame.grid(row=4, column=0, columnspan=2, pady=10, sticky="ew")
        
        # Variables for different parameter types
        date_var = tk.StringVar()
        start_var = tk.StringVar()
        end_var = tk.StringVar()
        day_var = tk.StringVar()
        
        # Set initial values if editing
        if holiday:
            params = holiday.get("params", {})
            if holiday.get("type") == "single":
                date_var.set(params.get("date", ""))
            elif holiday.get("type") == "range":
                start_var.set(params.get("start", ""))
                end_var.set(params.get("end", ""))
            elif holiday.get("type") == "fixed":
                day_var.set(str(params.get("day", "1")))
        
        def update_params_ui(*args):
            """Update parameters UI based on selected type."""
            # Clear frame
            for widget in params_frame.winfo_children():
                widget.destroy()
            
            current_type = type_var.get()
            
            if current_type == "single":
                ttk.Label(params_frame, text="Data (YYYY-MM-DD):").grid(row=0, column=0, sticky="w", pady=5)
                ttk.Entry(params_frame, textvariable=date_var, width=30).grid(row=0, column=1, pady=5, sticky="ew")
                ttk.Label(params_frame, text="Esempio: 2026-12-25", foreground="gray", font=("Helvetica", 8)).grid(row=1, column=0, columnspan=2, sticky="w")
            
            elif current_type == "range":
                ttk.Label(params_frame, text="Data inizio (YYYY-MM-DD):").grid(row=0, column=0, sticky="w", pady=5)
                ttk.Entry(params_frame, textvariable=start_var, width=30).grid(row=0, column=1, pady=5, sticky="ew")
                
                ttk.Label(params_frame, text="Data fine (YYYY-MM-DD):").grid(row=1, column=0, sticky="w", pady=5)
                ttk.Entry(params_frame, textvariable=end_var, width=30).grid(row=1, column=1, pady=5, sticky="ew")
                
                ttk.Label(params_frame, text="Esempio: 2026-08-10 → 2026-08-25", foreground="gray", font=("Helvetica", 8)).grid(row=2, column=0, columnspan=2, sticky="w")
            
            elif current_type == "fixed":
                ttk.Label(params_frame, text="Giorno del mese (1-31):").grid(row=0, column=0, sticky="w", pady=5)
                ttk.Entry(params_frame, textvariable=day_var, width=30).grid(row=0, column=1, pady=5, sticky="ew")
                ttk.Label(params_frame, text="Blocca questo giorno ogni mese (es: 1 = primo del mese)", foreground="gray", font=("Helvetica", 8)).grid(row=1, column=0, columnspan=2, sticky="w")
        
        # Bind type change to update UI
        type_var.trace_add("write", update_params_ui)
        
        # Initial UI update
        update_params_ui()
        
        # Expand column 1
        main_frame.columnconfigure(1, weight=1)
        params_frame.columnconfigure(1, weight=1)
        
        # Buttons
        button_frame = ttk.Frame(dialog)
        button_frame.pack(side="bottom", fill="x", pady=10, padx=10)
        
        def save_holiday():
            """Validate and save holiday."""
            try:
                # Validate name
                name = name_var.get().strip()
                if not name:
                    messagebox.showerror("Errore", "Inserisci un nome per la festività.")
                    return
                
                # Build holiday dict
                new_holiday = {
                    "name": name,
                    "scope": scope_var.get(),
                    "effect": effect_var.get(),
                    "type": type_var.get(),
                    "params": {}
                }
                
                # Build params based on type
                if type_var.get() == "single":
                    date_str = date_var.get().strip()
                    if not date_str:
                        messagebox.showerror("Errore", "Inserisci una data (YYYY-MM-DD).")
                        return
                    # Validate date format
                    try:
                        from datetime import datetime
                        datetime.strptime(date_str, "%Y-%m-%d")
                    except ValueError:
                        messagebox.showerror("Errore", "Formato data non valido. Usa YYYY-MM-DD.")
                        return
                    new_holiday["params"]["date"] = date_str
                
                elif type_var.get() == "range":
                    start_str = start_var.get().strip()
                    end_str = end_var.get().strip()
                    if not start_str or not end_str:
                        messagebox.showerror("Errore", "Inserisci entrambe le date (YYYY-MM-DD).")
                        return
                    # Validate dates
                    try:
                        from datetime import datetime
                        start_date = datetime.strptime(start_str, "%Y-%m-%d")
                        end_date = datetime.strptime(end_str, "%Y-%m-%d")
                        if start_date > end_date:
                            messagebox.showerror("Errore", "La data di inizio deve essere precedente alla data di fine.")
                            return
                    except ValueError:
                        messagebox.showerror("Errore", "Formato data non valido. Usa YYYY-MM-DD.")
                        return
                    new_holiday["params"]["start"] = start_str
                    new_holiday["params"]["end"] = end_str
                
                elif type_var.get() == "fixed":
                    day_str = day_var.get().strip()
                    if not day_str:
                        messagebox.showerror("Errore", "Inserisci un giorno (1-31).")
                        return
                    try:
                        day = int(day_str)
                        if day < 1 or day > 31:
                            messagebox.showerror("Errore", "Il giorno deve essere tra 1 e 31.")
                            return
                    except ValueError:
                        messagebox.showerror("Errore", "Il giorno deve essere un numero.")
                        return
                    new_holiday["params"]["day"] = day
                
                # Save holiday
                if mode == "add":
                    self.csv_layer.add_holiday(new_holiday)
                else:
                    if index is not None:
                        self.csv_layer.update_holiday(index, new_holiday)
                
                # Reload table and calendar
                self._refresh_holidays_table()
                self._reload_calendar()
                
                messagebox.showinfo("Successo", "Festività salvata con successo.")
                dialog.destroy()
                
            except Exception as e:
                messagebox.showerror("Errore", f"Errore durante il salvataggio: {str(e)}")
        
        ttk.Button(button_frame, text="💾 Salva", command=save_holiday).pack(side="left", padx=5)
        ttk.Button(button_frame, text="❌ Annulla", command=dialog.destroy).pack(side="left", padx=5)
    
    def _reload_calendar(self):
        """Reload calendar configuration after holiday changes."""
        from ..domain.calendar import create_calendar_with_holidays
        
        try:
            # Reload calendar with new holidays
            new_calendar = create_calendar_with_holidays(self.csv_layer.data_dir)
            
            # Note: OrderWorkflow and ReceivingWorkflow don't store calendar as attribute.
            # Calendar is loaded during order proposal/confirmation.
            # This method exists for future extension if workflows need calendar caching.
            
            logger.info("Calendar reloaded with updated holidays")
            
        except Exception as e:
            logger.error(f"Failed to reload calendar: {str(e)}", exc_info=True)
            messagebox.showwarning("Avviso", f"Calendario non ricaricato: {str(e)}")
    
    # === PROMO CALENDAR TAB METHODS ===
    
    def _filter_promo_sku_items(self, typed_text: str) -> list:
        """Filter SKU items for promo autocomplete."""
        if not typed_text:
            return []
        
        all_skus = self.csv_layer.get_all_sku_ids()
        typed_lower = typed_text.lower()
        return [sku for sku in all_skus if typed_lower in sku.lower()]
    
    def _validate_promo_form(self):
        """Validate promo form and enable/disable submit button."""
        try:
            # Get form values
            sku = self.promo_sku_var.get().strip()
            start_str = self.promo_start_var.get().strip()
            end_str = self.promo_end_var.get().strip()
            
            # Check required fields
            if not sku:
                self.promo_validation_label.config(text="SKU obbligatorio")
                self.promo_submit_btn.config(state="disabled")
                return
            
            if not start_str or not end_str:
                self.promo_validation_label.config(text="Date obbligatorie")
                self.promo_submit_btn.config(state="disabled")
                return
            
            # Validate SKU exists
            all_skus = self.csv_layer.get_all_sku_ids()
            if sku not in all_skus:
                self.promo_validation_label.config(text="SKU non valido")
                self.promo_submit_btn.config(state="disabled")
                return
            
            # Validate dates
            try:
                start_date = date.fromisoformat(start_str)
                end_date = date.fromisoformat(end_str)
            except ValueError:
                self.promo_validation_label.config(text="Formato data non valido (YYYY-MM-DD)")
                self.promo_submit_btn.config(state="disabled")
                return
            
            # Check end >= start
            if end_date < start_date:
                self.promo_validation_label.config(text="Data fine deve essere >= data inizio")
                self.promo_submit_btn.config(state="disabled")
                return
            
            # All validations passed
            self.promo_validation_label.config(text="✓ Pronto per invio", foreground="green")
            self.promo_submit_btn.config(state="normal")
        
        except Exception as e:
            self.promo_validation_label.config(text=f"Errore: {str(e)}", foreground="#d9534f")
            self.promo_submit_btn.config(state="disabled")
    
    def _add_promo_window(self):
        """Add promo window with automatic overlap merge (user preference)."""
        try:
            # Get form values
            sku = self.promo_sku_var.get().strip()
            start_date_obj = date.fromisoformat(self.promo_start_var.get().strip())
            end_date_obj = date.fromisoformat(self.promo_end_var.get().strip())
            store_id = self.promo_store_var.get().strip() or None
            
            # Create PromoWindow
            new_window = PromoWindow(
                sku=sku,
                start_date=start_date_obj,
                end_date=end_date_obj,
                store_id=store_id,
                promo_flag=1  # Always 1 for active promo
            )
            
            # Add with automatic overlap merge (user preference: merge automatically)
            success = promo_calendar.add_promo_window(
                csv_layer=self.csv_layer,
                window=new_window,
                allow_overlap=True  # User preference: auto-merge by allowing overlap
            )
            
            if not success:
                messagebox.showwarning("Sovrapposizione", "La finestra promo si sovrappone con una esistente e non è stata aggiunta.")
                return
            
            # Auto-sync sales with promo calendar (user preference)
            promo_calendar.enrich_sales_with_promo_calendar(csv_layer=self.csv_layer)
            
            # Refresh table
            self._refresh_promo_tab()
            
            # Clear form
            self._clear_promo_form()
            
            messagebox.showinfo("Successo", f"Finestra promo aggiunta per {sku} dal {start_date_obj} al {end_date_obj}!")
            
            # Log operation
            self.csv_layer.log_audit(
                operation="PROMO_WINDOW_ADD",
                details=f"Added promo window: {sku} from {start_date_obj} to {end_date_obj}",
                sku=sku
            )
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile aggiungere finestra promo:\n{str(e)}")
    
    def _remove_promo_window(self):
        """Remove selected promo window with confirmation."""
        try:
            # Get selected item
            selected = self.promo_treeview.selection()
            if not selected:
                messagebox.showwarning("Nessuna Selezione", "Seleziona una finestra promo da rimuovere.")
                return
            
            # Get window details from treeview
            item = self.promo_treeview.item(selected[0])
            values = item["values"]
            sku = values[0]
            start_str = values[1]
            end_str = values[2]
            store_id_display = values[4]
            
            # Parse dates
            start_date_obj = date.fromisoformat(start_str)
            end_date_obj = date.fromisoformat(end_str)
            store_id = None if store_id_display == "Tutti" else store_id_display
            
            # Confirm deletion
            confirm = messagebox.askyesno(
                "Conferma Rimozione",
                f"Rimuovere finestra promo?\n\nSKU: {sku}\nPeriodo: {start_str} - {end_str}\nStore: {store_id_display}"
            )
            
            if not confirm:
                return
            
            # Remove window using csv_layer API
            removed = promo_calendar.remove_promo_window(
                csv_layer=self.csv_layer,
                sku=sku,
                start_date=start_date_obj,
                end_date=end_date_obj,
                store_id=store_id
            )
            
            if removed:
                # Auto-sync sales with promo calendar (user preference)
                promo_calendar.enrich_sales_with_promo_calendar(csv_layer=self.csv_layer)
                
                # Refresh table
                self._refresh_promo_tab()
                
                messagebox.showinfo("Successo", "Finestra promo rimossa con successo!")
                
                # Log operation
                self.csv_layer.log_audit(
                    operation="PROMO_WINDOW_REMOVE",
                    details=f"Removed promo window: {sku} from {start_date_obj} to {end_date_obj}",
                    sku=sku
                )
            else:
                messagebox.showwarning("Avviso", "Finestra promo non trovata (potrebbe essere già stata rimossa).")
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile rimuovere finestra promo:\n{str(e)}")
    
    def _clear_promo_form(self):
        """Clear promo form fields."""
        self.promo_sku_var.set("")
        self.promo_start_var.set(date.today().isoformat())
        self.promo_end_var.set((date.today() + timedelta(days=7)).isoformat())
        self.promo_store_var.set("")
        self.promo_validation_label.config(text="")
        self.promo_submit_btn.config(state="disabled")
    
    def _refresh_promo_tab(self):
        """Refresh promo calendar table."""
        try:
            # Clear all items
            for item in self.promo_treeview.get_children():
                self.promo_treeview.delete(item)
            
            # Read all promo windows
            windows = self.csv_layer.read_promo_calendar()
            
            # Get filter text
            filter_text = self.promo_filter_var.get().strip().lower()
            
            # Today for status calculation
            today = date.today()
            
            # Populate table
            for window in windows:
                # Apply SKU filter
                if filter_text and filter_text not in window.sku.lower():
                    continue
                
                # Calculate duration
                duration_days = window.duration_days()
                
                # Store ID display
                store_display = window.store_id if window.store_id else "Tutti"
                
                # Calculate status
                if window.end_date < today:
                    status = "Scaduta"
                    tag = "expired"
                elif window.start_date <= today <= window.end_date:
                    status = "Attiva"
                    tag = "active"
                else:
                    status = "Futura"
                    tag = "future"
                
                # Insert row
                self.promo_treeview.insert(
                    "",
                    "end",
                    values=(
                        window.sku,
                        window.start_date.isoformat(),
                        window.end_date.isoformat(),
                        duration_days,
                        store_display,
                        status
                    ),
                    tags=(tag,)
                )
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile aggiornare tabella promo:\n{str(e)}")
    
    def _filter_promo_table(self):
        """Filter promo table by SKU."""
        self._refresh_promo_tab()
    
    def _refresh_uplift_report(self):
        """Calculate and display uplift estimation report for all SKUs with promo events."""
        try:
            # Clear all items
            for item in self.uplift_treeview.get_children():
                self.uplift_treeview.delete(item)
            
            # Read data
            all_skus = self.csv_layer.read_skus()
            promo_windows = self.csv_layer.read_promo_calendar()
            sales_records = self.csv_layer.read_sales()
            transactions = self.csv_layer.read_transactions()
            settings = self.csv_layer.read_settings()
            
            # Get filter text
            filter_text = self.uplift_filter_var.get().strip().lower()
            
            # Calculate uplift for each SKU that has promo windows
            skus_with_promo = set(w.sku for w in promo_windows)
            
            for sku_id in sorted(skus_with_promo):
                # Apply SKU filter
                if filter_text and filter_text not in sku_id.lower():
                    continue
                
                # Estimate uplift
                try:
                    report = estimate_uplift(
                        sku_id=sku_id,
                        all_skus=all_skus,
                        promo_windows=promo_windows,
                        sales_records=sales_records,
                        transactions=transactions,
                        settings=settings,
                    )
                    
                    # Format uplift with 2 decimals
                    uplift_display = f"{report.uplift_factor:.2f}x"
                    
                    # Determine tag for confidence color
                    tag = f"confidence_{report.confidence}"
                    
                    # Insert row
                    self.uplift_treeview.insert(
                        "",
                        "end",
                        values=(
                            report.sku,
                            report.n_events,
                            uplift_display,
                            report.confidence,
                            report.pooling_source,
                            report.n_valid_days_total
                        ),
                        tags=(tag,)
                    )
                
                except Exception as e:
                    logging.error(f"Uplift estimation failed for {sku_id}: {e}")
                    # Show row with error
                    self.uplift_treeview.insert(
                        "",
                        "end",
                        values=(
                            sku_id,
                            0,
                            "Error",
                            "C",
                            str(e)[:40],
                            0
                        ),
                        tags=("confidence_C",)
                    )
            
            messagebox.showinfo("Report Completato", f"Report uplift calcolato per {len(skus_with_promo)} SKU con eventi promo.")
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile generare report uplift:\n{str(e)}")
    
    def _filter_uplift_table(self):
        """Filter uplift report table by SKU."""
        self._refresh_uplift_report()
    
    def _refresh_cannibalization_report(self):
        """Calculate and display cannibalization (downlift) report for all substitute groups."""
        try:
            # Clear all items
            for item in self.cannib_treeview.get_children():
                self.cannib_treeview.delete(item)
            
            # Read data
            all_skus = self.csv_layer.read_skus()
            promo_windows = self.csv_layer.read_promo_calendar()
            sales_records = self.csv_layer.read_sales()
            transactions = self.csv_layer.read_transactions()
            settings = self.csv_layer.read_settings()
            
            # Check if cannibalization enabled
            cannib_settings = settings.get("promo_cannibalization", {})
            enabled = cannib_settings.get("enabled", {}).get("value", False)
            if not enabled:
                messagebox.showinfo("Cannibalizzazione Disabilitata", "Attiva cannibalizzazione nelle Impostazioni per generare il report.")
                return
            
            # Get substitute groups
            substitute_groups = cannib_settings.get("substitute_groups", {}).get("value", {})
            if not substitute_groups:
                messagebox.showinfo("Nessun Gruppo", "Configura gruppi di sostituti nelle Impostazioni per generare il report.")
                return
            
            # Get filter text
            filter_text = self.cannib_filter_var.get().strip().lower()
            
            # Calculate downlift for each target SKU in each group
            try:
                from src.domain.promo_uplift import estimate_cannibalization_downlift
            except ImportError:
                from domain.promo_uplift import estimate_cannibalization_downlift

            downlift_min = cannib_settings.get("downlift_min", {}).get("value", 0.6)
            downlift_max = cannib_settings.get("downlift_max", {}).get("value", 1.0)
            min_events = cannib_settings.get("min_events_target_sku", {}).get("value", 2)
            min_valid_days = cannib_settings.get("min_valid_days", {}).get("value", 7)
            epsilon = cannib_settings.get("denominator_epsilon", {}).get("value", 0.1)
            
            for group_id, sku_list in substitute_groups.items():
                for target_sku in sku_list:
                    # Apply SKU filter
                    if filter_text and filter_text not in target_sku.lower():
                        continue
                    
                    # Estimate downlift
                    try:
                        report = estimate_cannibalization_downlift(
                            target_sku=target_sku,
                            substitute_groups=substitute_groups,
                            promo_windows=promo_windows,
                            sales_records=sales_records,
                            transactions=transactions,
                            all_skus=all_skus,
                            downlift_min=downlift_min,
                            downlift_max=downlift_max,
                            min_events=min_events,
                            min_valid_days=min_valid_days,
                            epsilon=epsilon,
                            asof_date=date.today(),
                        )
                        
                        if report is None:
                            # No downlift data available
                            continue
                        
                        # Only show if downlift < 1.0 (actual reduction)
                        if report.downlift_factor >= 1.0:
                            continue
                        
                        # Format downlift with 2 decimals
                        downlift_display = f"{report.downlift_factor:.2f}x"
                        reduction_pct = int((1.0 - report.downlift_factor) * 100)
                        reduction_display = f"-{reduction_pct}%"
                        
                        # Determine tag for confidence color
                        tag = f"confidence_{report.confidence}"
                        
                        # Insert row
                        self.cannib_treeview.insert(
                            "",
                            "end",
                            values=(
                                report.target_sku,
                                report.driver_sku,
                                downlift_display,
                                reduction_display,
                                report.confidence,
                                report.n_events,
                            ),
                            tags=(tag,)
                        )
                    
                    except Exception as e:
                        logging.error(f"Downlift estimation failed for {target_sku}: {e}")
                        # Show row with error
                        self.cannib_treeview.insert(
                            "",
                            "end",
                            values=(
                                target_sku,
                                "Error",
                                "N/A",
                                "N/A",
                                "C",
                                0,
                            ),
                            tags=("confidence_C",)
                        )
            
            messagebox.showinfo("Report Completato", f"Report cannibalizzazione calcolato per {len(substitute_groups)} gruppi di sostituti.")
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile generare report cannibalizzazione:\n{str(e)}")
    
    def _filter_cannibalization_table(self):
        """Filter cannibalization report table by SKU."""
        self._refresh_cannibalization_report()
    
    # === EVENT UPLIFT TAB HANDLERS ===
    
    def _validate_event_uplift_form(self):
        """Validate event uplift form and enable/disable submit button."""
        errors = []
        
        # Delivery date validation
        try:
            delivery_date_str = self.event_delivery_date_var.get().strip()
            if not delivery_date_str:
                errors.append("Data consegna richiesta")
            else:
                date.fromisoformat(delivery_date_str)
        except ValueError:
            errors.append("Data consegna non valida (formato: YYYY-MM-DD)")
        
        # Reason validation (optional, no check needed)
        
        # Strength validation
        try:
            strength_str = self.event_strength_var.get().strip()
            if not strength_str:
                errors.append("Intensità richiesta")
            else:
                strength = float(strength_str)
                if not (0.0 <= strength <= 1.0):
                    errors.append("Intensità deve essere tra 0.0 e 1.0")
        except ValueError:
            errors.append("Intensità non valida (numero decimale)")
        
        # Scope key validation (required if scope != ALL)
        scope_type = self.event_scope_type_var.get()
        scope_key = self.event_scope_key_var.get().strip()
        if scope_type != "ALL" and not scope_key:
            errors.append(f"Scope Key richiesto per ambito '{scope_type}'")
        
        # Update UI
        if errors:
            self.event_submit_btn.config(state="disabled")
            self.event_validation_label.config(text=" | ".join(errors), foreground="#d9534f")
        else:
            self.event_submit_btn.config(state="normal")
            self.event_validation_label.config(text="✓ Form valido", foreground="green")
    
    def _on_event_scope_change(self):
        """Enable/disable scope key entry based on scope type selection."""
        scope_type = self.event_scope_type_var.get()
        if scope_type == "ALL":
            self.event_scope_key_entry.config(state="disabled")
            self.event_scope_key_var.set("")
        else:
            self.event_scope_key_entry.config(state="normal")
        self._validate_event_uplift_form()
    
    def _add_event_uplift_rule(self):
        """Add or update event uplift rule."""
        try:
            # Parse form values
            delivery_date_str = self.event_delivery_date_var.get().strip()
            delivery_date_parsed = date.fromisoformat(delivery_date_str)
            
            reason = self.event_reason_var.get().strip()
            strength = float(self.event_strength_var.get().strip())
            scope_type = self.event_scope_type_var.get().strip().upper()  # Normalize to uppercase
            scope_key = self.event_scope_key_var.get().strip() if scope_type != "ALL" else ""
            notes = self.event_notes_var.get().strip()
            
            # Check for duplicate (unless in edit mode with same key)
            existing_rules = self.csv_layer.read_event_uplift_rules()
            for rule in existing_rules:
                if rule.delivery_date == delivery_date_parsed and rule.scope_type == scope_type and rule.scope_key == scope_key:
                    if not self.event_edit_mode or (delivery_date_parsed, scope_type, scope_key) != self.event_edit_key:
                        if not messagebox.askyesno(
                            "Duplicato Rilevato",
                            f"Esiste già una regola per {delivery_date_str} / {scope_type} / {scope_key or 'ALL'}.\nSovrascrivere?",
                            icon="warning"
                        ):
                            return
                        # If yes, delete old rule (will be replaced)
                        self.csv_layer.delete_event_uplift_rule(delivery_date_parsed, scope_type, scope_key)
                        break
            
            # If in edit mode and key changed, delete old rule first
            if self.event_edit_mode and self.event_edit_key is not None and self.event_edit_key != (delivery_date_parsed, scope_type, scope_key):
                old_date, old_scope_type, old_scope_key = self.event_edit_key
                self.csv_layer.delete_event_uplift_rule(old_date, old_scope_type, old_scope_key)
            
            # Create rule object
            from src.domain.models import EventUpliftRule
            rule = EventUpliftRule(
                delivery_date=delivery_date_parsed,
                reason=reason,
                strength=strength,
                scope_type=scope_type,
                scope_key=scope_key,
                notes=notes,
            )
            
            # Save rule
            self.csv_layer.write_event_uplift_rule(rule)
            
            # Show confirmation
            action = "aggiornata" if self.event_edit_mode else "aggiunta"
            messagebox.showinfo("Successo", f"Regola uplift {action} con successo!")
            
            # Reset form and refresh table
            self._clear_event_uplift_form()
            self._refresh_event_uplift_tab()
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile salvare regola uplift:\n{str(e)}")
    
    def _edit_event_uplift_rule(self):
        """Load selected rule into form for editing."""
        selection = self.event_uplift_treeview.selection()
        if not selection:
            messagebox.showwarning("Nessuna Selezione", "Seleziona una regola da modificare.")
            return
        
        try:
            # Get selected item data
            item = selection[0]
            values = self.event_uplift_treeview.item(item, 'values')
            
            # Parse values: (Data Consegna, Motivo, Intensità, Ambito, Note, Status)
            delivery_date_str = values[0]
            reason = values[1]
            strength_str = values[2]
            ambito_str = values[3]  # Format: "ALL" or "dept:XXX" or "category:YYY" or "sku:ZZZ"
            notes = values[4]
            
            # Parse ambito into scope_type and scope_key
            if ambito_str == "ALL":
                scope_type = "ALL"
                scope_key = ""
            else:
                scope_type, scope_key = ambito_str.split(":", 1)
            
            # Populate form
            self.event_delivery_date_var.set(delivery_date_str)
            self.event_reason_var.set(reason)
            self.event_strength_var.set(strength_str)
            self.event_scope_type_var.set(scope_type)
            self.event_scope_key_var.set(scope_key)
            self.event_notes_var.set(notes)
            
            # Enable scope key entry if needed
            self._on_event_scope_change()
            
            # Enter edit mode
            self.event_edit_mode = True
            self.event_edit_key = (date.fromisoformat(delivery_date_str), scope_type, scope_key)
            self.event_submit_btn.config(text="✓ Aggiorna Regola")
            
            # Validate form
            self._validate_event_uplift_form()
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile caricare regola per modifica:\n{str(e)}")
    
    def _remove_event_uplift_rule(self):
        """Remove selected event uplift rule."""
        selection = self.event_uplift_treeview.selection()
        if not selection:
            messagebox.showwarning("Nessuna Selezione", "Seleziona una regola da rimuovere.")
            return
        
        try:
            # Get selected item data
            item = selection[0]
            values = self.event_uplift_treeview.item(item, 'values')
            
            # Parse key: (Data Consegna, Motivo, Intensità, Ambito, Note, Status)
            delivery_date_str = values[0]
            ambito_str = values[3]
            
            # Parse ambito
            if ambito_str == "ALL":
                scope_type = "ALL"
                scope_key = ""
            else:
                scope_type, scope_key = ambito_str.split(":", 1)
            
            # Confirm deletion
            if not messagebox.askyesno(
                "Conferma Rimozione",
                f"Rimuovere regola uplift del {delivery_date_str} ({ambito_str})?",
                icon="warning"
            ):
                return
            
            # Delete rule
            delivery_date_parsed = date.fromisoformat(delivery_date_str)
            self.csv_layer.delete_event_uplift_rule(delivery_date_parsed, scope_type, scope_key)
            
            # Refresh table
            self._refresh_event_uplift_tab()
            messagebox.showinfo("Successo", "Regola uplift rimossa con successo!")
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile rimuovere regola uplift:\n{str(e)}")
    
    def _clear_event_uplift_form(self):
        """Reset event uplift form to default values."""
        self.event_delivery_date_var.set(date.today().isoformat())
        self.event_reason_var.set("")  # Optional field
        self.event_strength_var.set("0.5")
        self.event_scope_type_var.set("ALL")
        self.event_scope_key_var.set("")
        self.event_notes_var.set("")
        
        # Disable scope key entry
        self.event_scope_key_entry.config(state="disabled")
        
        # Exit edit mode
        self.event_edit_mode = False
        self.event_edit_key = None
        self.event_submit_btn.config(text="✓ Aggiungi Regola")
        
        # Revalidate form
        self._validate_event_uplift_form()
    
    def _refresh_event_uplift_tab(self):
        """Refresh event uplift rules table."""
        try:
            # Clear all items
            for item in self.event_uplift_treeview.get_children():
                self.event_uplift_treeview.delete(item)
            
            # Read rules
            rules = self.csv_layer.read_event_uplift_rules()
            
            # Get filter text
            filter_text = self.event_filter_var.get().strip().lower()
            
            # Populate table
            today = date.today()
            for rule in rules:
                # Build ambito display
                if rule.scope_type == "ALL":
                    ambito_display = "ALL"
                else:
                    ambito_display = f"{rule.scope_type}:{rule.scope_key}"
                
                # Apply filter
                if filter_text:
                    combined_text = f"{rule.delivery_date} {rule.reason} {ambito_display} {rule.notes}".lower()
                    if filter_text not in combined_text:
                        continue
                
                # Determine status
                if rule.delivery_date < today:
                    status = "Passato"
                    tag = "past"
                elif rule.delivery_date == today:
                    status = "Oggi"
                    tag = "active"
                else:
                    delta_days = (rule.delivery_date - today).days
                    status = f"Tra {delta_days}g"
                    tag = "future"
                
                # Insert row
                self.event_uplift_treeview.insert(
                    "",
                    "end",
                    values=(
                        rule.delivery_date.isoformat(),
                        rule.reason,
                        f"{rule.strength:.2f}",
                        ambito_display,
                        rule.notes[:50] + ("..." if len(rule.notes) > 50 else ""),
                        status,
                    ),
                    tags=(tag,)
                )
        
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile aggiornare tabella event uplift:\n{str(e)}")
    
    def _filter_event_uplift_table(self):
        """Filter event uplift table by search text."""
        self._refresh_event_uplift_tab()


def main():
    """Entry point for GUI."""
    root = tk.Tk()
    app = DesktopOrderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
