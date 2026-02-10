"""
CSV persistence layer with auto-create functionality.

Handles all file I/O for transactions, SKUs, sales, etc.
Auto-creates files with correct headers on first run.
"""
import csv
import os
import json
from datetime import date
from pathlib import Path
from typing import List, Dict, Optional, Any

from ..domain.models import Transaction, EventType, SKU, SalesRecord, AuditLog, DemandVariability, Lot


class CSVLayer:
    """Manages all CSV file operations with auto-create."""
    
    # Default data directory
    DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent / "data"
    
    # CSV file schemas (filename -> list of columns)
    SCHEMAS = {
        "skus.csv": ["sku", "description", "ean", "moq", "pack_size", "lead_time_days", 
                     "review_period", "safety_stock", "shelf_life_days", "max_stock", 
                     "reorder_point", "demand_variability", "oos_boost_percent", 
                     "oos_detection_mode", "oos_popup_preference", "forecast_method",
                     "mc_distribution", "mc_n_simulations", "mc_random_seed", "mc_output_stat",
                     "mc_output_percentile", "mc_horizon_mode", "mc_horizon_days", "in_assortment"],
        "transactions.csv": ["date", "sku", "event", "qty", "receipt_date", "note"],
        "sales.csv": ["date", "sku", "qty_sold"],
        "order_logs.csv": ["order_id", "date", "sku", "qty_ordered", "qty_received", "status", "receipt_date"],
        "receiving_logs.csv": ["document_id", "receipt_id", "date", "sku", "qty_received", "receipt_date", "order_ids"],
        "audit_log.csv": ["timestamp", "operation", "sku", "details", "user"],
        "lots.csv": ["lot_id", "sku", "expiry_date", "qty_on_hand", "receipt_id", "receipt_date"],
    }
    
    def __init__(self, data_dir: Optional[Path] = None):
        """
        Initialize CSV layer.
        
        Args:
            data_dir: Directory to store CSV files. Defaults to DEFAULT_DATA_DIR.
        """
        self.data_dir = data_dir or self.DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_all_files_exist()
    
    def _ensure_all_files_exist(self):
        """Create all CSV files with headers if they don't exist."""
        for filename, columns in self.SCHEMAS.items():
            self._ensure_file_exists(filename, columns)
    
    def _ensure_file_exists(self, filename: str, columns: List[str]):
        """Create a CSV file with headers if it doesn't exist."""
        filepath = self.data_dir / filename
        if not filepath.exists():
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
    
    def _read_csv(self, filename: str) -> List[Dict[str, str]]:
        """Read CSV file and return list of dicts."""
        filepath = self.data_dir / filename
        if not filepath.exists():
            return []
        
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader) if reader else []
    
    def _write_csv(self, filename: str, rows: List[Dict[str, str]]):
        """Write list of dicts to CSV file (overwrites)."""
        if not self.SCHEMAS.get(filename):
            raise ValueError(f"Unknown CSV file: {filename}")
        
        columns = self.SCHEMAS[filename]
        filepath = self.data_dir / filename
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    
    def _append_csv(self, filename: str, row: Dict[str, str]):
        """Append a single row to CSV file."""
        filepath = self.data_dir / filename
        
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.SCHEMAS[filename])
            writer.writerow(row)
    
    # ============ SKU Operations ============
    
    def read_skus(self) -> List[SKU]:
        """Read all SKUs from skus.csv (with backward-compatibility for legacy files)."""
        rows = self._read_csv("skus.csv")
        skus = []
        for row in rows:
            try:
                # Parse demand_variability with fallback
                demand_var_str = row.get("demand_variability", "STABLE").strip().upper()
                try:
                    demand_var = DemandVariability[demand_var_str]
                except KeyError:
                    demand_var = DemandVariability.STABLE
                
                sku = SKU(
                    sku=row.get("sku", "").strip(),
                    description=row.get("description", "").strip(),
                    ean=row.get("ean", "").strip() or None,
                    # New parameters with defaults for backward-compatibility
                    moq=int(row.get("moq", "1")),
                    pack_size=int(row.get("pack_size", "1")),
                    lead_time_days=int(row.get("lead_time_days", "7")),
                    review_period=int(row.get("review_period", "7")),
                    safety_stock=int(row.get("safety_stock", "0")),
                    shelf_life_days=int(row.get("shelf_life_days", "0")),
                    max_stock=int(row.get("max_stock", "999")),
                    reorder_point=int(row.get("reorder_point", "10")),
                    demand_variability=demand_var,
                    oos_boost_percent=float(row.get("oos_boost_percent", "0")),
                    oos_detection_mode=row.get("oos_detection_mode", "").strip(),
                    oos_popup_preference=row.get("oos_popup_preference", "ask").strip() or "ask",
                    # Monte Carlo forecast parameters
                    forecast_method=row.get("forecast_method", "").strip(),
                    mc_distribution=row.get("mc_distribution", "").strip(),
                    mc_n_simulations=int(row.get("mc_n_simulations", "0")),
                    mc_random_seed=int(row.get("mc_random_seed", "0")),
                    mc_output_stat=row.get("mc_output_stat", "").strip(),
                    mc_output_percentile=int(row.get("mc_output_percentile", "0")),
                    mc_horizon_mode=row.get("mc_horizon_mode", "").strip(),
                    mc_horizon_days=int(row.get("mc_horizon_days", "0")),
                    # Assortment status (backward-compatible: missing → True)
                    in_assortment=row.get("in_assortment", "true").strip().lower() in ("true", "1", "yes", "t"),
                )
                skus.append(sku)
            except (ValueError, KeyError) as e:
                # Log but don't crash
                print(f"Warning: Invalid SKU in skus.csv: {e}")
        return skus
    
    def write_sku(self, sku: SKU):
        """
        Add a new SKU to skus.csv.
        
        Auto-applies default parameters from settings if configured.
        Auto-classifies demand variability based on historical sales.
        """
        from ..domain.models import auto_classify_variability
        import json
        
        # Load settings for auto-classification
        settings_path = self.data_dir / "settings.json"
        settings = {}
        if settings_path.exists():
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        
        # Check if auto-classification is enabled
        auto_settings = settings.get("auto_variability", {})
        enabled = auto_settings.get("enabled", {}).get("value", True)
        
        # Auto-classify if enabled and SKU has STABLE (default) variability
        # This preserves manual classifications while auto-classifying new/default SKUs
        if enabled and sku.demand_variability == DemandVariability.STABLE:
            try:
                # Load sales records for classification
                sales_records = self.read_sales()
                
                # Perform auto-classification
                classified_variability = auto_classify_variability(
                    sku=sku.sku,
                    sales_records=sales_records,
                    settings=settings
                )
                
                # Create updated SKU with auto-classified variability
                sku = SKU(
                    sku=sku.sku,
                    description=sku.description,
                    ean=sku.ean,
                    moq=sku.moq,
                    pack_size=sku.pack_size,
                    lead_time_days=sku.lead_time_days,
                    review_period=sku.review_period,
                    safety_stock=sku.safety_stock,
                    shelf_life_days=sku.shelf_life_days,
                    max_stock=sku.max_stock,
                    reorder_point=sku.reorder_point,
                    demand_variability=classified_variability,  # Auto-classified
                    oos_boost_percent=sku.oos_boost_percent,
                    oos_detection_mode=sku.oos_detection_mode,
                    oos_popup_preference=sku.oos_popup_preference,
                )
            except Exception as e:
                # Fallback to original if auto-classification fails
                import logging
                logging.warning(f"Auto-classification failed for {sku.sku}: {e}")
        
        # Get defaults from settings
        defaults = self.get_default_sku_params()
        
        # Apply defaults if SKU parameters are not explicitly set (using default values)
        final_sku = SKU(
            sku=sku.sku,
            description=sku.description,
            ean=sku.ean,
            moq=defaults.get("moq", sku.moq) if sku.moq == 1 else sku.moq,
            pack_size=defaults.get("pack_size", sku.pack_size) if sku.pack_size == 1 else sku.pack_size,
            lead_time_days=defaults.get("lead_time_days", sku.lead_time_days) if sku.lead_time_days == 7 else sku.lead_time_days,
            review_period=defaults.get("review_period", sku.review_period) if sku.review_period == 7 else sku.review_period,
            safety_stock=defaults.get("safety_stock", sku.safety_stock) if sku.safety_stock == 0 else sku.safety_stock,
            max_stock=defaults.get("max_stock", sku.max_stock) if sku.max_stock == 999 else sku.max_stock,
            reorder_point=defaults.get("reorder_point", sku.reorder_point) if sku.reorder_point == 10 else sku.reorder_point,
            demand_variability=DemandVariability[defaults.get("demand_variability", sku.demand_variability.value)] if sku.demand_variability == DemandVariability.STABLE else sku.demand_variability,
            shelf_life_days=sku.shelf_life_days,
            oos_boost_percent=sku.oos_boost_percent,
            oos_detection_mode=sku.oos_detection_mode,
            oos_popup_preference=sku.oos_popup_preference,
            forecast_method=sku.forecast_method,
            mc_distribution=sku.mc_distribution,
            mc_n_simulations=sku.mc_n_simulations,
            mc_random_seed=sku.mc_random_seed,
            mc_output_stat=sku.mc_output_stat,
            mc_output_percentile=sku.mc_output_percentile,
            mc_horizon_mode=sku.mc_horizon_mode,
            mc_horizon_days=sku.mc_horizon_days,
        )
        
        rows = self._read_csv("skus.csv")
        rows.append({
            "sku": final_sku.sku,
            "description": final_sku.description,
            "ean": final_sku.ean or "",
            "moq": str(final_sku.moq),
            "pack_size": str(final_sku.pack_size),
            "lead_time_days": str(final_sku.lead_time_days),
            "review_period": str(final_sku.review_period),
            "safety_stock": str(final_sku.safety_stock),
            "shelf_life_days": str(final_sku.shelf_life_days),
            "max_stock": str(final_sku.max_stock),
            "reorder_point": str(final_sku.reorder_point),
            "demand_variability": final_sku.demand_variability.value,
            "oos_boost_percent": str(final_sku.oos_boost_percent),
            "oos_detection_mode": final_sku.oos_detection_mode,
            "oos_popup_preference": final_sku.oos_popup_preference,
            "forecast_method": final_sku.forecast_method,
            "mc_distribution": final_sku.mc_distribution,
            "mc_n_simulations": str(final_sku.mc_n_simulations),
            "mc_random_seed": str(final_sku.mc_random_seed),
            "mc_output_stat": final_sku.mc_output_stat,
            "mc_output_percentile": str(final_sku.mc_output_percentile),
            "mc_horizon_mode": final_sku.mc_horizon_mode,
            "mc_horizon_days": str(final_sku.mc_horizon_days),
            "in_assortment": "true" if final_sku.in_assortment else "false",
        })
        self._write_csv("skus.csv", rows)
    
    def get_all_sku_ids(self) -> List[str]:
        """Get list of all SKU identifiers."""
        skus = self.read_skus()
        return [s.sku for s in skus]
    
    def sku_exists(self, sku_id: str) -> bool:
        """Check if SKU exists in skus.csv."""
        return sku_id in self.get_all_sku_ids()
    
    def search_skus(self, query: str) -> List[SKU]:
        """
        Search SKUs by SKU code or description (case-insensitive, client-side).
        
        Args:
            query: Search query string
            
        Returns:
            List of SKUs matching the query
        """
        if not query or not query.strip():
            return self.read_skus()
        
        query_lower = query.strip().lower()
        all_skus = self.read_skus()
        
        return [
            sku for sku in all_skus
            if query_lower in sku.sku.lower() or query_lower in sku.description.lower()
        ]
    
    def update_sku(
        self, 
        old_sku_id: str, 
        new_sku_id: str, 
        new_description: str, 
        new_ean: Optional[str],
        moq: int = 1,
        pack_size: int = 1,
        lead_time_days: int = 7,
        review_period: int = 7,
        safety_stock: int = 0,
        shelf_life_days: int = 0,
        max_stock: int = 999,
        reorder_point: int = 10,
        demand_variability: DemandVariability = DemandVariability.STABLE,
        oos_boost_percent: float = 0.0,
        oos_detection_mode: str = "",
        oos_popup_preference: str = "ask",
        forecast_method: str = "",
        mc_distribution: str = "",
        mc_n_simulations: int = 0,
        mc_random_seed: int = 0,
        mc_output_stat: str = "",
        mc_output_percentile: int = 0,
        mc_horizon_mode: str = "",
        mc_horizon_days: int = 0,
        in_assortment: bool = True
    ) -> bool:
        """
        Update SKU (code, description, EAN, and parameters).
        If SKU code changes, automatically updates all ledger references.
        Auto-classifies demand variability if enabled.
        
        Args:
            old_sku_id: Current SKU identifier
            new_sku_id: New SKU identifier (can be same as old)
            new_description: New description
            new_ean: New EAN (or None)
            moq: Minimum Order Quantity
            pack_size: Pack size for order rounding
            lead_time_days: Lead time in days
            review_period: Review period in days
            safety_stock: Safety stock quantity
            shelf_life_days: Shelf life in days (0 = no expiry)
            max_stock: Maximum stock level
            reorder_point: Reorder trigger point
            demand_variability: Demand variability enum
            
        Returns:
            True if updated, False if not found
        """
        from ..domain.models import auto_classify_variability
        import json
        
        # Load settings for auto-classification
        settings_path = self.data_dir / "settings.json"
        settings = {}
        if settings_path.exists():
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        
        # Check if auto-classification is enabled
        auto_settings = settings.get("auto_variability", {})
        enabled = auto_settings.get("enabled", {}).get("value", True)
        
        # Auto-classify if enabled and demand_variability is STABLE (default)
        if enabled and demand_variability == DemandVariability.STABLE:
            try:
                # Load sales records for classification
                sales_records = self.read_sales()
                
                # Perform auto-classification
                demand_variability = auto_classify_variability(
                    sku=new_sku_id,  # Use new SKU ID for classification
                    sales_records=sales_records,
                    settings=settings
                )
            except Exception as e:
                # Fallback to STABLE if auto-classification fails
                import logging
                logging.warning(f"Auto-classification failed for {new_sku_id}: {e}")
        
        rows = self._read_csv("skus.csv")
        updated = False
        old_in_assortment = None  # Track if assortment status changed
        
        # Normalize all rows to ensure they have all required fields with defaults
        normalized_rows = []
        for row in rows:
            # Ensure all fields exist with proper defaults
            normalized_row = {
                "sku": row.get("sku", "").strip(),
                "description": row.get("description", "").strip(),
                "ean": row.get("ean", "").strip(),
                "moq": row.get("moq", "1").strip() or "1",
                "pack_size": row.get("pack_size", "1").strip() or "1",
                "lead_time_days": row.get("lead_time_days", "7").strip() or "7",
                "review_period": row.get("review_period", "7").strip() or "7",
                "safety_stock": row.get("safety_stock", "0").strip() or "0",
                "shelf_life_days": row.get("shelf_life_days", "0").strip() or "0",
                "max_stock": row.get("max_stock", "999").strip() or "999",
                "reorder_point": row.get("reorder_point", "10").strip() or "10",
                "demand_variability": row.get("demand_variability", "STABLE").strip() or "STABLE",
                "oos_boost_percent": row.get("oos_boost_percent", "0").strip() or "0",
                "oos_detection_mode": row.get("oos_detection_mode", "").strip(),
                "oos_popup_preference": row.get("oos_popup_preference", "ask").strip() or "ask",
                "forecast_method": row.get("forecast_method", "").strip(),
                "mc_distribution": row.get("mc_distribution", "").strip(),
                "mc_n_simulations": row.get("mc_n_simulations", "0").strip() or "0",
                "mc_random_seed": row.get("mc_random_seed", "0").strip() or "0",
                "mc_output_stat": row.get("mc_output_stat", "").strip(),
                "mc_output_percentile": row.get("mc_output_percentile", "0").strip() or "0",
                "mc_horizon_mode": row.get("mc_horizon_mode", "").strip(),
                "mc_horizon_days": row.get("mc_horizon_days", "0").strip() or "0",
                "in_assortment": row.get("in_assortment", "true").strip() or "true",
            }
            
            # Update the target row with new values
            if normalized_row["sku"] == old_sku_id:
                # Capture old assortment status before update
                old_in_assortment = normalized_row["in_assortment"].lower() in ("true", "1", "yes", "t")
                
                normalized_row["sku"] = new_sku_id
                normalized_row["description"] = new_description
                normalized_row["ean"] = new_ean or ""
                normalized_row["moq"] = str(moq)
                normalized_row["pack_size"] = str(pack_size)
                normalized_row["lead_time_days"] = str(lead_time_days)
                normalized_row["review_period"] = str(review_period)
                normalized_row["safety_stock"] = str(safety_stock)
                normalized_row["shelf_life_days"] = str(shelf_life_days)
                normalized_row["max_stock"] = str(max_stock)
                normalized_row["reorder_point"] = str(reorder_point)
                normalized_row["demand_variability"] = demand_variability.value
                normalized_row["oos_boost_percent"] = str(oos_boost_percent)
                normalized_row["oos_detection_mode"] = oos_detection_mode
                normalized_row["oos_popup_preference"] = oos_popup_preference
                normalized_row["forecast_method"] = forecast_method
                normalized_row["mc_distribution"] = mc_distribution
                normalized_row["mc_n_simulations"] = str(mc_n_simulations)
                normalized_row["mc_random_seed"] = str(mc_random_seed)
                normalized_row["mc_output_stat"] = mc_output_stat
                normalized_row["mc_output_percentile"] = str(mc_output_percentile)
                normalized_row["mc_horizon_mode"] = mc_horizon_mode
                normalized_row["mc_horizon_days"] = str(mc_horizon_days)
                normalized_row["in_assortment"] = "true" if in_assortment else "false"
                updated = True
            
            normalized_rows.append(normalized_row)
        
        if not updated:
            return False
        
        self._write_csv("skus.csv", normalized_rows)
        
        # Log assortment status change in ledger
        if old_in_assortment is not None and old_in_assortment != in_assortment:
            from datetime import date as dt_date
            from ..domain.models import Transaction, EventType
            
            event = EventType.ASSORTMENT_IN if in_assortment else EventType.ASSORTMENT_OUT
            txn = Transaction(
                date=dt_date.today(),
                sku=new_sku_id,  # Use new SKU code if changed
                event=event,
                qty=0,  # No stock impact
                receipt_date=None,
                note=f"Assortment status changed: {'IN' if in_assortment else 'OUT'}"
            )
            self.write_transaction(txn)
        
        # If SKU code changed, update all ledger references
        if old_sku_id != new_sku_id:
            self._update_sku_references_in_ledger(old_sku_id, new_sku_id)
        
        return True
    
    def delete_sku(self, sku_id: str) -> bool:
        """
        Hard delete SKU from skus.csv.
        
        WARNING: Does NOT check if SKU is referenced in ledger.
        Use can_delete_sku() first to validate.
        
        Args:
            sku_id: SKU identifier to delete
            
        Returns:
            True if deleted, False if not found
        """
        rows = self._read_csv("skus.csv")
        filtered = [row for row in rows if row.get("sku") != sku_id]
        
        if len(filtered) < len(rows):
            self._write_csv("skus.csv", filtered)
            return True
        
        return False
    
    def can_delete_sku(self, sku_id: str) -> tuple[bool, str]:
        """
        Check if SKU can be safely deleted (no ledger references).
        
        Args:
            sku_id: SKU identifier to check
            
        Returns:
            (can_delete, reason_if_not)
        """
        # Check transactions
        txns = self.read_transactions()
        if any(t.sku == sku_id for t in txns):
            return False, f"SKU {sku_id} has transactions in ledger"
        
        # Check sales
        sales = self.read_sales()
        if any(s.sku == sku_id for s in sales):
            return False, f"SKU {sku_id} has sales records"
        
        # Check order logs
        orders = self.read_order_logs()
        if any(o.get("sku") == sku_id for o in orders):
            return False, f"SKU {sku_id} has order history"
        
        # Check receiving logs
        receives = self.read_receiving_logs()
        if any(r.get("sku") == sku_id for r in receives):
            return False, f"SKU {sku_id} has receiving history"
        
        return True, ""
    
    def _update_sku_references_in_ledger(self, old_sku: str, new_sku: str):
        """
        Update all references to old SKU with new SKU in ledger files.
        
        Args:
            old_sku: Old SKU identifier
            new_sku: New SKU identifier
        """
        # Update transactions
        txn_rows = self._read_csv("transactions.csv")
        for row in txn_rows:
            if row.get("sku") == old_sku:
                row["sku"] = new_sku
        if txn_rows:
            self._write_csv("transactions.csv", txn_rows)
        
        # Update sales
        sales_rows = self._read_csv("sales.csv")
        for row in sales_rows:
            if row.get("sku") == old_sku:
                row["sku"] = new_sku
        if sales_rows:
            self._write_csv("sales.csv", sales_rows)
        
        # Update order logs
        order_rows = self._read_csv("order_logs.csv")
        for row in order_rows:
            if row.get("sku") == old_sku:
                row["sku"] = new_sku
        if order_rows:
            self._write_csv("order_logs.csv", order_rows)
        
        # Update receiving logs
        recv_rows = self._read_csv("receiving_logs.csv")
        for row in recv_rows:
            if row.get("sku") == old_sku:
                row["sku"] = new_sku
        if recv_rows:
            self._write_csv("receiving_logs.csv", recv_rows)
    
    # ============ Transaction Operations ============
    
    def read_transactions(self) -> List[Transaction]:
        """Read all transactions from transactions.csv."""
        rows = self._read_csv("transactions.csv")
        transactions = []
        for row in rows:
            try:
                txn = Transaction(
                    date=date.fromisoformat(row.get("date", "")),
                    sku=row.get("sku", "").strip(),
                    event=EventType(row.get("event", "").strip()),
                    qty=int(row.get("qty", 0)),
                    receipt_date=date.fromisoformat(row.get("receipt_date", "")) if row.get("receipt_date") else None,
                    note=row.get("note", "").strip() or None,
                )
                transactions.append(txn)
            except (ValueError, KeyError) as e:
                print(f"Warning: Invalid transaction in transactions.csv: {e}")
        return transactions
    
    def write_transaction(self, txn: Transaction):
        """Add a new transaction to transactions.csv."""
        self._append_csv("transactions.csv", {
            "date": txn.date.isoformat(),
            "sku": txn.sku,
            "event": txn.event.value,
            "qty": str(txn.qty),
            "receipt_date": txn.receipt_date.isoformat() if txn.receipt_date else "",
            "note": txn.note or "",
        })
    
    def write_transactions_batch(self, txns: List[Transaction]):
        """Add multiple transactions at once (append mode)."""
        rows = self._read_csv("transactions.csv")
        for txn in txns:
            rows.append({
                "date": txn.date.isoformat(),
                "sku": txn.sku,
                "event": txn.event.value,
                "qty": str(txn.qty),
                "receipt_date": txn.receipt_date.isoformat() if txn.receipt_date else "",
                "note": txn.note or "",
            })
        self._write_csv_atomic("transactions.csv", rows)
    
    def overwrite_transactions(self, txns: List[Transaction]):
        """Overwrite entire transactions.csv with given list (atomic write with backup)."""
        rows = []
        for txn in txns:
            rows.append({
                "date": txn.date.isoformat(),
                "sku": txn.sku,
                "event": txn.event.value,
                "qty": str(txn.qty),
                "receipt_date": txn.receipt_date.isoformat() if txn.receipt_date else "",
                "note": txn.note or "",
            })
        self._write_csv_atomic("transactions.csv", rows)
    
    # ============ Sales Operations ============
    
    def read_sales(self) -> List[SalesRecord]:
        """Read all sales from sales.csv."""
        rows = self._read_csv("sales.csv")
        sales = []
        for row in rows:
            try:
                s = SalesRecord(
                    date=date.fromisoformat(row.get("date", "")),
                    sku=row.get("sku", "").strip(),
                    qty_sold=int(row.get("qty_sold", 0)),
                )
                sales.append(s)
            except (ValueError, KeyError) as e:
                print(f"Warning: Invalid sales record in sales.csv: {e}")
        return sales
    
    def write_sales_record(self, sale: SalesRecord):
        """Add a sales record to sales.csv."""
        self._append_csv("sales.csv", {
            "date": sale.date.isoformat(),
            "sku": sale.sku,
            "qty_sold": str(sale.qty_sold),
        })
    
    def append_sales(self, sale: SalesRecord):
        """Append a sales record to sales.csv (alias for write_sales_record)."""
        self.write_sales_record(sale)
    
    def write_sales(self, sales: List[SalesRecord]):
        """Overwrite entire sales.csv with given list (for bulk updates)."""
        file_path = self.data_dir / "sales.csv"
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["date", "sku", "qty_sold"])
            for sale in sales:
                writer.writerow([sale.date.isoformat(), sale.sku, str(sale.qty_sold)])
    
    # ============ Order Log Operations ============
    
    def read_order_logs(self) -> List[Dict]:
        """Read order logs."""
        return self._read_csv("order_logs.csv")
    
    def write_order_log(self, order_id: str, date_str: str, sku: str, qty: int, status: str, receipt_date: Optional[str] = None, qty_received: int = 0):
        """
        Write order log entry with expected receipt date and received quantity.
        
        Args:
            order_id: Unique order identifier
            date_str: Order date (ISO format)
            sku: SKU identifier
            qty: Quantity ordered
            status: Order status (PENDING, PARTIAL, RECEIVED)
            receipt_date: Expected receipt date (ISO format, optional)
            qty_received: Quantity already received (default 0)
        """
        self._append_csv("order_logs.csv", {
            "order_id": order_id,
            "date": date_str,
            "sku": sku,
            "qty_ordered": str(qty),
            "qty_received": str(qty_received),
            "status": status,
            "receipt_date": receipt_date or "",
        })
    
    def update_order_received_qty(self, order_id: str, qty_received: int, status: str):
        """
        Update qty_received and status for an existing order (atomic write with backup).
        
        Args:
            order_id: Order identifier to update
            qty_received: New total quantity received
            status: New status (PENDING, PARTIAL, RECEIVED)
        """
        import logging
        logger = logging.getLogger(__name__)
        
        orders = self.read_order_logs()
        updated = False
        
        for order in orders:
            if order.get("order_id") == order_id:
                order["qty_received"] = str(qty_received)
                order["status"] = status
                updated = True
                logger.info(f"Updated order {order_id}: qty_received={qty_received}, status={status}")
                break
        
        if not updated:
            logger.warning(f"Order {order_id} not found for update")
            return
        
        # Rewrite file with updated data (atomic)
        self._write_csv_atomic("order_logs.csv", orders)
    
    def get_unfulfilled_orders(self, sku: Optional[str] = None) -> List[Dict]:
        """
        Get orders with qty_received < qty_ordered.
        
        Args:
            sku: Optional SKU filter
        
        Returns:
            List of dicts with keys:
            - order_id, sku, date, qty_ordered, qty_received, qty_unfulfilled, status, receipt_date
        """
        orders = self.read_order_logs()
        unfulfilled = []
        
        for order in orders:
            order_sku = order.get("sku", "")
            if sku and order_sku != sku:
                continue
            
            qty_ordered = int(order.get("qty_ordered", 0))
            qty_received = int(order.get("qty_received", 0))
            
            if qty_received < qty_ordered:
                unfulfilled.append({
                    "order_id": order.get("order_id", ""),
                    "sku": order_sku,
                    "date": order.get("date", ""),
                    "qty_ordered": qty_ordered,
                    "qty_received": qty_received,
                    "qty_unfulfilled": qty_ordered - qty_received,
                    "status": order.get("status", ""),
                    "receipt_date": order.get("receipt_date", ""),
                })
        
        return unfulfilled
    
    # ============ Receiving Log Operations ============
    
    def read_receiving_logs(self) -> List[Dict]:
        """Read receiving logs."""
        return self._read_csv("receiving_logs.csv")
    
    def write_receiving_log(self, document_id: str, date_str: str, sku: str, qty: int, receipt_date: str, order_ids: str = "", receipt_id: Optional[str] = None):
        """
        Write receiving log entry with document and order traceability.
        
        Args:
            document_id: Document identifier (DDT/Invoice number)
            date_str: Processing date (ISO format)
            sku: SKU identifier
            qty: Quantity received
            receipt_date: Receipt date (ISO format)
            order_ids: Comma-separated list of order_ids fulfilled by this receipt
            receipt_id: Legacy receipt_id (for backward compatibility)
        """
        self._append_csv("receiving_logs.csv", {
            "document_id": document_id,
            "receipt_id": receipt_id or document_id,  # Backward compat
            "date": date_str,
            "sku": sku,
            "qty_received": str(qty),
            "receipt_date": receipt_date,
            "order_ids": order_ids,
        })
    
    # ============ Audit Log Operations ============
    
    def log_audit(self, operation: str, details: str, sku: Optional[str] = None, user: str = "system"):
        """
        Write audit log entry.
        
        Args:
            operation: Operation type (SKU_EDIT, EXPORT, etc.)
            details: Human-readable description
            sku: Affected SKU (optional)
            user: User/operator name
        """
        from datetime import datetime
        
        # Use microsecond precision for sorting accuracy in tests
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        
        row = {
            "timestamp": timestamp,
            "operation": operation,
            "sku": sku or "",
            "details": details,
            "user": user,
        }
        
        self._append_csv("audit_log.csv", row)
    
    def read_audit_log(self, sku: Optional[str] = None, limit: Optional[int] = None) -> List[AuditLog]:
        """
        Read audit log entries, optionally filtered by SKU.
        
        Args:
            sku: Filter by SKU (optional)
            limit: Max number of records to return (most recent first)
        
        Returns:
            List of AuditLog objects (sorted by timestamp desc)
        """
        rows = self._read_csv("audit_log.csv")
        
        # Filter by SKU if provided
        if sku:
            rows = [r for r in rows if r.get("sku") == sku]
        
        # Sort by timestamp descending (most recent first)
        rows = sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True)
        
        # Apply limit
        if limit:
            rows = rows[:limit]
        
        # Convert to AuditLog objects
        audit_logs = []
        for row in rows:
            audit_logs.append(AuditLog(
                timestamp=row.get("timestamp", ""),
                operation=row.get("operation", ""),
                sku=row.get("sku") if row.get("sku") else None,
                details=row.get("details", ""),
                user=row.get("user", "system"),
            ))
        
        return audit_logs
    
    # ============ Settings Operations ============
    
    def read_settings(self) -> Dict:
        """
        Read settings from settings.json.
        
        Returns default settings if file doesn't exist.
        """
        settings_file = self.data_dir / "settings.json"
        
        # Default settings
        default_settings = {
            "reorder_engine": {
                "lead_time_days": {
                    "value": 7,
                    "auto_apply_to_new_sku": True
                },
                "moq": {
                    "value": 1,
                    "auto_apply_to_new_sku": True
                },
                "pack_size": {
                    "value": 1,
                    "auto_apply_to_new_sku": True
                },
                "review_period": {
                    "value": 7,
                    "auto_apply_to_new_sku": True
                },
                "safety_stock": {
                    "value": 0,
                    "auto_apply_to_new_sku": True
                },
                "max_stock": {
                    "value": 999,
                    "auto_apply_to_new_sku": True
                },
                "reorder_point": {
                    "value": 10,
                    "auto_apply_to_new_sku": True
                },
                "demand_variability": {
                    "value": "STABLE",
                    "auto_apply_to_new_sku": True
                },
                "oos_boost_percent": {
                    "value": 20,
                    "auto_apply_to_new_sku": False
                },
                "oos_lookback_days": {
                    "value": 30,
                    "auto_apply_to_new_sku": False
                },
                "oos_detection_mode": {
                    "value": "strict",
                    "auto_apply_to_new_sku": False
                },
                "forecast_method": {
                    "value": "simple",
                    "auto_apply_to_new_sku": False
                }
            },
            "monte_carlo": {
                "distribution": {
                    "value": "empirical",
                    "auto_apply_to_new_sku": False
                },
                "n_simulations": {
                    "value": 1000,
                    "auto_apply_to_new_sku": False
                },
                "random_seed": {
                    "value": 42,
                    "auto_apply_to_new_sku": False
                },
                "output_stat": {
                    "value": "mean",
                    "auto_apply_to_new_sku": False
                },
                "output_percentile": {
                    "value": 80,
                    "auto_apply_to_new_sku": False
                },
                "horizon_mode": {
                    "value": "auto",
                    "auto_apply_to_new_sku": False
                },
                "horizon_days": {
                    "value": 14,
                    "auto_apply_to_new_sku": False
                },
                "show_comparison": {
                    "value": False,
                    "description": "Mostra risultati MC come colonna informativa nella proposta ordini"
                }
            },
            "dashboard": {
                "stock_unit_price": {
                    "value": 10,
                    "description": "Prezzo unitario medio per calcolo valore stock"
                }
            }
        }
        
        if not settings_file.exists():
            # Create with defaults
            self.write_settings(default_settings)
            return default_settings
        
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
                # Merge with defaults for missing keys
                for section, params in default_settings.items():
                    if section not in settings:
                        settings[section] = params
                    else:
                        for param, config in params.items():
                            if param not in settings[section]:
                                settings[section][param] = config
                return settings
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not read settings.json: {e}. Using defaults.")
            return default_settings
    
    def write_settings(self, settings: Dict):
        """Write settings to settings.json."""
        settings_file = self.data_dir / "settings.json"
        
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    
    def get_default_sku_params(self) -> Dict[str, Any]:
        """
        Get default SKU parameters from settings (for auto-apply to new SKUs).
        
        Returns:
            Dict with keys: moq, pack_size, lead_time_days, review_period, safety_stock, max_stock, reorder_point, demand_variability
        """
        settings = self.read_settings()
        engine = settings.get("reorder_engine", {})
        
        defaults = {}
        
        for param_name in ["moq", "pack_size", "lead_time_days", "review_period", "safety_stock", "max_stock", "reorder_point", "demand_variability"]:
            param_config = engine.get(param_name, {})
            if param_config.get("auto_apply_to_new_sku", False):
                defaults[param_name] = param_config.get("value")
        
        return defaults
    
    # ============ Atomic Write & Backup Operations ============
    
    def _backup_file(self, filename: str, max_backups: int = 5):
        """
        Create timestamped backup of file before modification.
        
        Args:
            filename: CSV filename to backup
            max_backups: Maximum number of backups to keep (oldest deleted)
        """
        import shutil
        import glob
        from datetime import datetime
        
        filepath = self.data_dir / filename
        if not filepath.exists():
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.data_dir / f"{filename}.backup.{timestamp}"
        
        try:
            shutil.copy2(filepath, backup_path)
            
            # Cleanup old backups
            backup_pattern = str(self.data_dir / f"{filename}.backup.*")
            backups = sorted(glob.glob(backup_pattern))
            
            if len(backups) > max_backups:
                for old_backup in backups[:-max_backups]:
                    try:
                        os.remove(old_backup)
                    except OSError:
                        pass  # Ignore errors on cleanup
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Backup failed for {filename}: {e}")
    
    def _write_csv_atomic(self, filename: str, rows: List[Dict[str, str]]):
        """
        Write CSV file atomically with auto-backup.
        
        Steps:
        1. Backup existing file
        2. Write to temporary file
        3. Atomic rename (replaces original)
        
        Args:
            filename: CSV filename
            rows: List of dicts to write
        """
        import tempfile
        import logging
        logger = logging.getLogger(__name__)
        
        if not self.SCHEMAS.get(filename):
            raise ValueError(f"Unknown CSV file: {filename}")
        
        # 1. Backup existing
        self._backup_file(filename)
        
        columns = self.SCHEMAS[filename]
        filepath = self.data_dir / filename
        
        # 2. Write to temporary file
        temp_fd, temp_path = tempfile.mkstemp(dir=self.data_dir, suffix=".tmp", text=True)
        
        try:
            with os.fdopen(temp_fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
            
            # 3. Atomic rename (replaces original)
            os.replace(temp_path, filepath)
            logger.debug(f"Atomic write completed for {filename}")
        except Exception as e:
            # Cleanup temp file on error
            try:
                os.remove(temp_path)
            except OSError:
                pass
            logger.error(f"Atomic write failed for {filename}: {e}")
            raise
    
    # ==================== LOT MANAGEMENT ====================
    
    def read_lots(self) -> List[Lot]:
        """
        Read all lots from lots.csv.
        
        Returns:
            List of Lot objects
        """
        lots = []
        filepath = self.data_dir / "lots.csv"
        
        if not filepath.exists():
            return []
        
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lot_id = row.get("lot_id", "").strip()
                sku = row.get("sku", "").strip()
                expiry_date_str = row.get("expiry_date", "").strip()
                qty_on_hand = int(row.get("qty_on_hand", 0))
                receipt_id = row.get("receipt_id", "").strip()
                receipt_date_str = row.get("receipt_date", "").strip()
                
                if not lot_id or not sku or not receipt_id:
                    continue  # Skip invalid rows
                
                expiry_date = date.fromisoformat(expiry_date_str) if expiry_date_str else None
                receipt_date = date.fromisoformat(receipt_date_str)
                
                lot = Lot(
                    lot_id=lot_id,
                    sku=sku,
                    expiry_date=expiry_date,
                    qty_on_hand=qty_on_hand,
                    receipt_id=receipt_id,
                    receipt_date=receipt_date,
                )
                lots.append(lot)
        
        return lots
    
    def write_lot(self, lot: Lot):
        """
        Write or update a single lot to lots.csv.
        
        Args:
            lot: Lot object to write
        """
        lots = self.read_lots()
        
        # Check if lot already exists
        existing_index = None
        for i, existing_lot in enumerate(lots):
            if existing_lot.lot_id == lot.lot_id:
                existing_index = i
                break
        
        # Update or append
        if existing_index is not None:
            lots[existing_index] = lot
        else:
            lots.append(lot)
        
        # Write all lots
        rows = []
        for lot_obj in lots:
            rows.append({
                "lot_id": lot_obj.lot_id,
                "sku": lot_obj.sku,
                "expiry_date": lot_obj.expiry_date.isoformat() if lot_obj.expiry_date else "",
                "qty_on_hand": str(lot_obj.qty_on_hand),
                "receipt_id": lot_obj.receipt_id,
                "receipt_date": lot_obj.receipt_date.isoformat(),
            })
        
        self._write_csv_atomic("lots.csv", rows)
    
    def update_lot_quantity(self, lot_id: str, new_qty: int):
        """
        Update lot quantity (for consumption via FEFO).
        
        Args:
            lot_id: Lot identifier
            new_qty: New quantity (can be 0 to deplete lot)
        """
        lots = self.read_lots()
        updated = False
        
        for i, lot in enumerate(lots):
            if lot.lot_id == lot_id:
                # Create new lot with updated quantity
                updated_lot = Lot(
                    lot_id=lot.lot_id,
                    sku=lot.sku,
                    expiry_date=lot.expiry_date,
                    qty_on_hand=new_qty,
                    receipt_id=lot.receipt_id,
                    receipt_date=lot.receipt_date,
                )
                lots[i] = updated_lot
                updated = True
                break
        
        if not updated:
            raise ValueError(f"Lot not found: {lot_id}")
        
        # Remove lots with qty = 0
        lots = [lot for lot in lots if lot.qty_on_hand > 0]
        
        # Write back
        rows = []
        for lot_obj in lots:
            rows.append({
                "lot_id": lot_obj.lot_id,
                "sku": lot_obj.sku,
                "expiry_date": lot_obj.expiry_date.isoformat() if lot_obj.expiry_date else "",
                "qty_on_hand": str(lot_obj.qty_on_hand),
                "receipt_id": lot_obj.receipt_id,
                "receipt_date": lot_obj.receipt_date.isoformat(),
            })
        
        self._write_csv_atomic("lots.csv", rows)
    
    def get_lots_by_sku(self, sku: str, sort_by_expiry: bool = True) -> List[Lot]:
        """
        Get all lots for a specific SKU.
        
        Args:
            sku: SKU identifier
            sort_by_expiry: If True, sort by expiry date (FEFO order)
        
        Returns:
            List of lots for the SKU
        """
        lots = self.read_lots()
        sku_lots = [lot for lot in lots if lot.sku == sku]
        
        if sort_by_expiry:
            # Sort: None (no expiry) last, then by expiry date ascending
            sku_lots.sort(key=lambda lot: (lot.expiry_date is None, lot.expiry_date or date.max))
        
        return sku_lots
    
    def get_expiring_lots(self, days_threshold: int, check_date: Optional[date] = None) -> List[Lot]:
        """
        Get lots expiring within days_threshold.
        
        Args:
            days_threshold: Days until expiry (e.g., 7 = expiring in next 7 days)
            check_date: Reference date (defaults to today)
        
        Returns:
            List of lots expiring soon
        """
        if check_date is None:
            check_date = date.today()
        
        lots = self.read_lots()
        expiring = []
        
        for lot in lots:
            if lot.expiry_date is None:
                continue
            
            days_left = (lot.expiry_date - check_date).days
            if 0 <= days_left <= days_threshold:
                expiring.append(lot)
        
        # Sort by expiry date (closest first)
        expiring.sort(key=lambda lot: lot.expiry_date)
        
        return expiring
    
    def get_expired_lots(self, check_date: Optional[date] = None) -> List[Lot]:
        """
        Get expired lots.
        
        Args:
            check_date: Reference date (defaults to today)
        
        Returns:
            List of expired lots
        """
        if check_date is None:
            check_date = date.today()
        
        lots = self.read_lots()
        expired = [lot for lot in lots if lot.is_expired(check_date)]
        
        return expired

