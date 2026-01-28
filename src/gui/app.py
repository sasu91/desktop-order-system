"""
Main GUI application for desktop-order-system.

Tkinter-based desktop UI with multiple tabs.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import date, timedelta
from pathlib import Path
import tempfile
import os
import csv

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
        
        # Order proposals storage
        self.current_proposals = []  # List[OrderProposal]
        
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
        
        # Export submenu
        export_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Export As CSV", menu=export_menu)
        export_menu.add_command(label="Stock Snapshot (AsOf Date)", command=self._export_stock_snapshot)
        export_menu.add_command(label="Ledger (Transactions)", command=self._export_ledger)
        export_menu.add_command(label="SKU List", command=self._export_sku_list)
        export_menu.add_command(label="Order Logs", command=self._export_order_logs)
        export_menu.add_command(label="Receiving Logs", command=self._export_receiving_logs)
        
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # Tab notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Create tabs
        self.stock_tab = ttk.Frame(self.notebook)
        self.order_tab = ttk.Frame(self.notebook)
        self.receiving_tab = ttk.Frame(self.notebook)
        self.exception_tab = ttk.Frame(self.notebook)
        self.admin_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.stock_tab, text="Stock (CalcolAto)")
        self.notebook.add(self.order_tab, text="Ordini")
        self.notebook.add(self.receiving_tab, text="Ricevimenti")
        self.notebook.add(self.exception_tab, text="Eccezioni")
        self.notebook.add(self.admin_tab, text="Admin")
        
        # Build tab contents
        self._build_stock_tab()
        self._build_order_tab()
        self._build_receiving_tab()
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
        main_frame = ttk.Frame(self.order_tab)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Title
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(title_frame, text="Order Management", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # === PARAMETERS & PROPOSAL GENERATION ===
        param_frame = ttk.LabelFrame(main_frame, text="Generate Order Proposals", padding=10)
        param_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Parameters row
        params_row = ttk.Frame(param_frame)
        params_row.pack(side="top", fill="x", pady=5)
        
        ttk.Label(params_row, text="Min Stock:", width=12).pack(side="left", padx=(0, 5))
        self.min_stock_var = tk.StringVar(value="10")
        ttk.Entry(params_row, textvariable=self.min_stock_var, width=10).pack(side="left", padx=(0, 20))
        
        ttk.Label(params_row, text="Days Cover:", width=12).pack(side="left", padx=(0, 5))
        self.days_cover_var = tk.StringVar(value="30")
        ttk.Entry(params_row, textvariable=self.days_cover_var, width=10).pack(side="left", padx=(0, 20))
        
        ttk.Label(params_row, text="Lead Time (days):", width=15).pack(side="left", padx=(0, 5))
        self.lead_time_var = tk.StringVar(value="7")
        ttk.Entry(params_row, textvariable=self.lead_time_var, width=10).pack(side="left", padx=(0, 20))
        
        # Buttons row
        buttons_row = ttk.Frame(param_frame)
        buttons_row.pack(side="top", fill="x", pady=5)
        
        ttk.Button(buttons_row, text="✓ Generate All Proposals", command=self._generate_all_proposals).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="🔄 Refresh Stock Data", command=self._refresh_order_stock_data).pack(side="left", padx=5)
        ttk.Button(buttons_row, text="✗ Clear Proposals", command=self._clear_proposals).pack(side="left", padx=5)
        
        # === PROPOSALS TABLE (EDITABLE) ===
        proposal_frame = ttk.LabelFrame(main_frame, text="Order Proposals (Double-click Proposed Qty to edit)", padding=5)
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
        self.proposal_treeview.heading("Description", text="Description", anchor=tk.W)
        self.proposal_treeview.heading("On Hand", text="On Hand", anchor=tk.CENTER)
        self.proposal_treeview.heading("On Order", text="On Order", anchor=tk.CENTER)
        self.proposal_treeview.heading("Avg Sales", text="Avg Sales/Day", anchor=tk.CENTER)
        self.proposal_treeview.heading("Proposed Qty", text="Proposed Qty", anchor=tk.CENTER)
        self.proposal_treeview.heading("Receipt Date", text="Receipt Date", anchor=tk.CENTER)
        
        self.proposal_treeview.pack(fill="both", expand=True)
        
        # Bind double-click to edit
        self.proposal_treeview.bind("<Double-1>", self._on_proposal_double_click)
        
        # === CONFIRMATION SECTION ===
        confirm_frame = ttk.LabelFrame(main_frame, text="Confirm Orders", padding=10)
        confirm_frame.pack(side="bottom", fill="x", pady=(10, 0))
        
        info_row = ttk.Frame(confirm_frame)
        info_row.pack(side="top", fill="x", pady=(0, 10))
        ttk.Label(info_row, text="Select proposals with Proposed Qty > 0 above, then click Confirm to create orders.").pack(side="left")
        
        buttons_row = ttk.Frame(confirm_frame)
        buttons_row.pack(side="top", fill="x")
        
        ttk.Button(buttons_row, text="✓ Confirm All Orders (Qty > 0)", command=self._confirm_orders).pack(side="left", padx=5)
    
    def _generate_all_proposals(self):
        """Generate order proposals for all SKUs."""
        try:
            min_stock = int(self.min_stock_var.get())
            days_cover = int(self.days_cover_var.get())
            lead_time = int(self.lead_time_var.get())
        except ValueError:
            messagebox.showerror("Validation Error", "Parameters must be integers.")
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
            
            # Generate proposal
            proposal = self.order_workflow.generate_proposal(
                sku=sku_id,
                description=description,
                current_stock=stock,
                daily_sales_avg=daily_sales,
                min_stock=min_stock,
                days_cover=days_cover,
            )
            self.current_proposals.append(proposal)
        
        # Populate table
        self._refresh_proposal_table()
        
        messagebox.showinfo(
            "Success",
            f"Generated {len(self.current_proposals)} order proposals.\nProposals with Qty > 0: {sum(1 for p in self.current_proposals if p.proposed_qty > 0)}",
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
        messagebox.showinfo("Info", "Stock data refreshed. Click 'Generate All Proposals' to recalculate.")
    
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
        ttk.Label(form_frame, text=f"Description: {proposal.description}").pack(anchor="w", pady=5)
        ttk.Label(form_frame, text=f"Current Proposed Qty: {proposal.proposed_qty}").pack(anchor="w", pady=5)
        
        ttk.Label(form_frame, text="New Proposed Qty:", font=("Helvetica", 10)).pack(anchor="w", pady=(15, 5))
        new_qty_var = tk.StringVar(value=str(proposal.proposed_qty))
        qty_entry = ttk.Entry(form_frame, textvariable=new_qty_var, width=20)
        qty_entry.pack(anchor="w", pady=(0, 15))
        qty_entry.focus()
        
        def save_qty():
            try:
                new_qty = int(new_qty_var.get())
                if new_qty < 0:
                    messagebox.showerror("Validation Error", "Quantity must be >= 0.", parent=popup)
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
                messagebox.showerror("Validation Error", "Quantity must be an integer.", parent=popup)
        
        # Buttons
        button_frame = ttk.Frame(form_frame)
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="Save", command=save_qty).pack(side="right", padx=5)
        ttk.Button(button_frame, text="Cancel", command=popup.destroy).pack(side="right", padx=5)
        
        # Bind Enter to save
        qty_entry.bind("<Return>", lambda e: save_qty())
    
    def _confirm_orders(self):
        """Confirm all orders with qty > 0."""
        if not self.current_proposals:
            messagebox.showwarning("No Proposals", "Please generate proposals first.")
            return
        
        # Filter proposals with qty > 0
        to_confirm = [p for p in self.current_proposals if p.proposed_qty > 0]
        
        if not to_confirm:
            messagebox.showwarning("No Orders", "No proposals with quantity > 0 to confirm.")
            return
        
        # Confirm with user
        confirm = messagebox.askyesno(
            "Confirm Orders",
            f"Confirm {len(to_confirm)} order(s)?\n\nThis will create ORDER events in the ledger.",
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
                "Success",
                f"Confirmed {len(confirmations)} order(s).\n\nOrder IDs: {', '.join(c.order_id for c in confirmations[:3])}{'...' if len(confirmations) > 3 else ''}",
            )
            
            # Show receipt window
            self._show_receipt_window(confirmations)
            
            # Clear proposals
            self._clear_proposals()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to confirm orders: {str(e)}")
    
    def _show_receipt_window(self, confirmations):
        """Show receipt window with order confirmations (5 items per page, barcode rendering)."""
        if not confirmations:
            return
        
        # Create popup
        popup = tk.Toplevel(self.root)
        popup.title("Order Confirmation - Receipt")
        popup.geometry("700x600")
        popup.transient(self.root)
        popup.grab_set()
        
        # Header
        header_frame = ttk.Frame(popup, padding=10)
        header_frame.pack(side="top", fill="x")
        
        ttk.Label(header_frame, text="Order Confirmation Receipt", font=("Helvetica", 14, "bold")).pack()
        
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
                text=f"Page {page_num + 1} of {total_pages}",
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
                
                ttk.Label(item_frame, text=f"Description: {description}").pack(anchor="w")
                ttk.Label(item_frame, text=f"Quantity Ordered: {confirmation.qty_ordered}").pack(anchor="w")
                ttk.Label(item_frame, text=f"Receipt Date: {confirmation.receipt_date.isoformat()}").pack(anchor="w")
                ttk.Label(item_frame, text=f"Order ID: {confirmation.order_id}", font=("Courier", 9)).pack(anchor="w")
                
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
                                ttk.Label(item_frame, text=f"Barcode error: {str(e)}", foreground="red").pack(anchor="w")
                        else:
                            ttk.Label(item_frame, text="(Barcode rendering disabled)", foreground="gray").pack(anchor="w")
                    else:
                        ttk.Label(item_frame, text=f"EAN: {ean} (Invalid - {error})", foreground="red").pack(anchor="w")
                else:
                    ttk.Label(item_frame, text="EAN: (empty - no barcode)", foreground="gray").pack(anchor="w")
            
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
        
        ttk.Button(nav_frame, text="◀ Previous", command=prev_page).pack(side="left", padx=5)
        ttk.Button(nav_frame, text="Next ▶", command=next_page).pack(side="left", padx=5)
        ttk.Label(nav_frame, text="(Press SPACE for next page)").pack(side="left", padx=20)
        ttk.Button(nav_frame, text="Close", command=popup.destroy).pack(side="right", padx=5)
        
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
        ttk.Label(title_frame, text="Receiving Management", font=("Helvetica", 14, "bold")).pack(side="left")
        
        # === PENDING ORDERS SECTION ===
        pending_frame = ttk.LabelFrame(main_frame, text="Pending Orders (Not Fully Received)", padding=5)
        pending_frame.pack(side="top", fill="both", expand=True, pady=(0, 10))
        
        # Toolbar
        pending_toolbar = ttk.Frame(pending_frame)
        pending_toolbar.pack(side="top", fill="x", pady=(0, 5))
        ttk.Button(pending_toolbar, text="🔄 Refresh Pending", command=self._refresh_pending_orders).pack(side="left", padx=5)
        ttk.Label(pending_toolbar, text="(Double-click to pre-fill receipt form)").pack(side="left", padx=20)
        
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
        
        self.pending_treeview.heading("Order ID", text="Order ID", anchor=tk.W)
        self.pending_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.pending_treeview.heading("Description", text="Description", anchor=tk.W)
        self.pending_treeview.heading("Qty Ordered", text="Qty Ordered", anchor=tk.CENTER)
        self.pending_treeview.heading("Qty Received", text="Qty Received", anchor=tk.CENTER)
        self.pending_treeview.heading("Pending", text="Pending", anchor=tk.CENTER)
        self.pending_treeview.heading("Receipt Date", text="Expected Date", anchor=tk.CENTER)
        
        self.pending_treeview.pack(fill="both", expand=True)
        self.pending_treeview.bind("<Double-1>", self._on_pending_order_double_click)
        
        # === CLOSE RECEIPT FORM (INLINE) ===
        form_frame = ttk.LabelFrame(main_frame, text="Close Receipt (Idempotent)", padding=10)
        form_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Row 1: Receipt ID (auto + manual override)
        row1 = ttk.Frame(form_frame)
        row1.pack(side="top", fill="x", pady=5)
        
        ttk.Label(row1, text="Receipt ID:", width=15).pack(side="left", padx=(0, 5))
        self.receipt_id_var = tk.StringVar()
        self.receipt_id_entry = ttk.Entry(row1, textvariable=self.receipt_id_var, width=30, state="disabled")
        self.receipt_id_entry.pack(side="left", padx=(0, 10))
        
        self.auto_receipt_id_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row1,
            text="Auto-generate",
            variable=self.auto_receipt_id_var,
            command=self._toggle_receipt_id_entry,
        ).pack(side="left", padx=5)
        
        ttk.Label(row1, text="Receipt Date:", width=15).pack(side="left", padx=(20, 5))
        self.receipt_date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(row1, textvariable=self.receipt_date_var, width=15).pack(side="left", padx=(0, 5))
        
        # Row 2: SKU, Quantity
        row2 = ttk.Frame(form_frame)
        row2.pack(side="top", fill="x", pady=5)
        
        ttk.Label(row2, text="SKU:", width=15).pack(side="left", padx=(0, 5))
        self.receipt_sku_var = tk.StringVar()
        self.receipt_sku_combo = ttk.Combobox(row2, textvariable=self.receipt_sku_var, width=20, state="readonly")
        self.receipt_sku_combo.pack(side="left", padx=(0, 10))
        
        ttk.Label(row2, text="Qty Received:", width=15).pack(side="left", padx=(20, 5))
        self.receipt_qty_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.receipt_qty_var, width=15).pack(side="left", padx=(0, 5))
        
        # Row 3: Notes
        row3 = ttk.Frame(form_frame)
        row3.pack(side="top", fill="x", pady=5)
        
        ttk.Label(row3, text="Notes:", width=15).pack(side="left", padx=(0, 5))
        self.receipt_notes_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.receipt_notes_var, width=60).pack(side="left", padx=(0, 5))
        
        # Row 4: Buttons
        row4 = ttk.Frame(form_frame)
        row4.pack(side="top", fill="x", pady=(10, 0))
        
        ttk.Button(row4, text="✓ Close Receipt", command=self._close_receipt).pack(side="left", padx=5)
        ttk.Button(row4, text="✗ Clear Form", command=self._clear_receipt_form).pack(side="left", padx=5)
        ttk.Button(row4, text="🔄 Refresh SKU List", command=self._refresh_receipt_sku_list).pack(side="left", padx=5)
        
        # === RECEIVING HISTORY ===
        history_frame = ttk.LabelFrame(main_frame, text="Receiving History", padding=5)
        history_frame.pack(side="top", fill="both", expand=True)
        
        # Toolbar
        history_toolbar = ttk.Frame(history_frame)
        history_toolbar.pack(side="top", fill="x", pady=(0, 5))
        ttk.Button(history_toolbar, text="🔄 Refresh History", command=self._refresh_receiving_history).pack(side="left", padx=5)
        
        ttk.Label(history_toolbar, text="Filter SKU:").pack(side="left", padx=(20, 5))
        self.history_filter_sku_var = tk.StringVar()
        ttk.Entry(history_toolbar, textvariable=self.history_filter_sku_var, width=15).pack(side="left", padx=(0, 5))
        ttk.Button(history_toolbar, text="Apply Filter", command=self._refresh_receiving_history).pack(side="left", padx=5)
        ttk.Button(history_toolbar, text="Clear Filter", command=self._clear_history_filter).pack(side="left", padx=5)
        
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
        
        self.receiving_history_treeview.heading("Receipt ID", text="Receipt ID", anchor=tk.W)
        self.receiving_history_treeview.heading("Date", text="Date Logged", anchor=tk.CENTER)
        self.receiving_history_treeview.heading("SKU", text="SKU", anchor=tk.W)
        self.receiving_history_treeview.heading("Qty Received", text="Qty Received", anchor=tk.CENTER)
        self.receiving_history_treeview.heading("Receipt Date", text="Receipt Date", anchor=tk.CENTER)
        self.receiving_history_treeview.heading("Notes", text="Notes", anchor=tk.W)
        
        self.receiving_history_treeview.pack(fill="both", expand=True)
        
        # Initial load
        self._refresh_receipt_sku_list()
        self._refresh_pending_orders()
        self._refresh_receiving_history()
    
    def _toggle_receipt_id_entry(self):
        """Toggle receipt ID entry based on auto-generate checkbox."""
        if self.auto_receipt_id_var.get():
            self.receipt_id_entry.config(state="disabled")
            self.receipt_id_var.set("")
        else:
            self.receipt_id_entry.config(state="normal")
    
    def _refresh_receipt_sku_list(self):
        """Refresh SKU dropdown list."""
        sku_ids = self.csv_layer.get_all_sku_ids()
        self.receipt_sku_combo['values'] = sku_ids
    
    def _refresh_pending_orders(self):
        """Calculate and display pending orders (qty_ordered - qty_received > 0)."""
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
    
    def _on_pending_order_double_click(self, event):
        """Pre-fill receipt form from pending order."""
        selected = self.pending_treeview.selection()
        if not selected:
            return
        
        item = self.pending_treeview.item(selected[0])
        values = item["values"]
        
        order_id = values[0]
        sku = values[1]
        pending_qty = values[5]
        receipt_date_str = values[6]
        
        # Pre-fill form
        self.receipt_sku_var.set(sku)
        self.receipt_qty_var.set(str(pending_qty))
        self.receipt_date_var.set(receipt_date_str if receipt_date_str else date.today().isoformat())
        self.receipt_notes_var.set(f"From order {order_id}")
        
        messagebox.showinfo(
            "Pre-filled",
            f"Form pre-filled for SKU {sku} from order {order_id}.\n\nAdjust quantity and notes as needed.",
        )
    
    def _close_receipt(self):
        """Close receipt and update ledger."""
        # Validate inputs
        try:
            receipt_date_obj = date.fromisoformat(self.receipt_date_var.get())
        except ValueError:
            messagebox.showerror("Validation Error", "Invalid receipt date format (use YYYY-MM-DD).")
            return
        
        sku = self.receipt_sku_var.get().strip()
        if not sku:
            messagebox.showerror("Validation Error", "Please select a SKU.")
            return
        
        try:
            qty = int(self.receipt_qty_var.get())
            if qty <= 0:
                messagebox.showerror("Validation Error", "Quantity must be > 0.")
                return
        except ValueError:
            messagebox.showerror("Validation Error", "Quantity must be an integer.")
            return
        
        # Generate or use manual receipt_id
        if self.auto_receipt_id_var.get():
            # Auto-generate
            receipt_id = ReceivingWorkflow.generate_receipt_id(
                receipt_date=receipt_date_obj,
                origin="MANUAL",  # Default origin for manual entries
                sku=sku,
            )
        else:
            receipt_id = self.receipt_id_var.get().strip()
            if not receipt_id:
                messagebox.showerror("Validation Error", "Please enter a receipt ID or enable auto-generate.")
                return
        
        notes = self.receipt_notes_var.get().strip()
        
        # Call workflow
        try:
            transactions, already_processed = self.receiving_workflow.close_receipt(
                receipt_id=receipt_id,
                receipt_date=receipt_date_obj,
                sku_quantities={sku: qty},
                notes=notes,
            )
            
            if already_processed:
                messagebox.showwarning(
                    "Already Processed",
                    f"Receipt {receipt_id} already processed (idempotent).\n\nNo changes made.",
                )
            else:
                messagebox.showinfo(
                    "Success",
                    f"Receipt closed successfully!\n\nReceipt ID: {receipt_id}\nSKU: {sku}\nQty: {qty}\n\n{len(transactions)} RECEIPT event(s) created.",
                )
                
                # Clear form
                self._clear_receipt_form()
                
                # Refresh views
                self._refresh_pending_orders()
                self._refresh_receiving_history()
        
        except Exception as e:
            messagebox.showerror("Error", f"Failed to close receipt: {str(e)}")
    
    def _clear_receipt_form(self):
        """Clear receipt form."""
        self.receipt_id_var.set("")
        self.receipt_sku_var.set("")
        self.receipt_qty_var.set("")
        self.receipt_date_var.set(date.today().isoformat())
        self.receipt_notes_var.set("")
    
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
            
            messagebox.showinfo("Success", f"Stock snapshot exported to:\n{file_path}\n\n{len(sku_ids)} SKUs exported.")
        
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export stock snapshot: {str(e)}")
    
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
            
            messagebox.showinfo("Success", f"Ledger exported to:\n{file_path}\n\n{len(transactions)} transactions exported.")
        
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export ledger: {str(e)}")
    
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
            
            messagebox.showinfo("Success", f"SKU list exported to:\n{file_path}\n\n{len(skus)} SKUs exported.")
        
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export SKU list: {str(e)}")
    
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
            
            messagebox.showinfo("Success", f"Order logs exported to:\n{file_path}\n\n{len(logs)} orders exported.")
        
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export order logs: {str(e)}")
    
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
            
            messagebox.showinfo("Success", f"Receiving logs exported to:\n{file_path}\n\n{len(logs)} receipts exported.")
        
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export receiving logs: {str(e)}")
    
    def _refresh_all(self):
        """Refresh all tabs."""
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
