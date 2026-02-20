"""
Storage Adapter Layer - Routes between CSV and SQLite backends

FASE 5: GUI Integration
- Adapter pattern: wraps CSVLayer and SQLite repositories
- Routes operations based on config.STORAGE_BACKEND
- Maintains backward compatibility with existing code
- Graceful fallback to CSV if SQLite unavailable

Design:
- Same interface as CSVLayer (drop-in replacement)
- Transparent routing (caller doesn't know which backend)
- SQLite-specific features available via adapter methods
- Migration helper (CSV → SQLite) built-in
"""

from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import date
import sqlite3

from ..domain.models import (
    Transaction, EventType, SKU, SalesRecord, AuditLog, 
    DemandVariability, Lot, PromoWindow, EventUpliftRule
)
from .csv_layer import CSVLayer

# Import config from project root
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import (
    get_storage_backend, is_sqlite_available, 
    DATABASE_PATH, DATA_DIR
)

# Import SQLite components (conditional)
try:
    from ..db import open_connection, transaction, apply_migrations
    from ..repositories import RepositoryFactory
    SQLITE_AVAILABLE = True
except ImportError as e:
    SQLITE_AVAILABLE = False
    print(f"⚠ SQLite backend not available: {e}")


class StorageAdapter(CSVLayer):
    """
    Storage adapter that routes between CSV and SQLite backends.
    
    Inherits from CSVLayer to maintain full type compatibility with existing code.
    Overrides methods to route to SQLite when backend='sqlite', otherwise delegates
    to parent CSVLayer implementation.
    
    Provides transparent access to storage layer with automatic backend routing.
    Maintains full backward compatibility with CSVLayer interface.
    
    Usage:
        storage = StorageAdapter()  # Auto-detects backend from config
        skus = storage.read_skus()  # Routes to CSV or SQLite
        storage.write_sku(sku)      # Routes to CSV or SQLite
        
    Note: Inheritance is for type compatibility. In future, refactor to Protocol.
    """
    
    def __init__(self, data_dir: Optional[Path] = None, force_backend: Optional[str] = None):
        """
        Initialize storage adapter.
        
        Args:
            data_dir: Data directory (default: config.DATA_DIR)
            force_backend: Force specific backend ('csv' or 'sqlite'), 
                          overrides config (useful for testing)
        """
        # Initialize parent CSVLayer (always available as fallback)
        super().__init__(data_dir=data_dir)
        
        self.data_dir = data_dir or DATA_DIR
        
        # Keep separate CSV layer reference for explicit delegation in overrides
        # (slight redundancy but clearer code and backward compatibility)
        self.csv_layer = CSVLayer(data_dir=self.data_dir)
        
        # Determine backend
        if force_backend:
            self.backend = force_backend if force_backend in ('csv', 'sqlite') else 'csv'
        else:
            self.backend = get_storage_backend()
        
        # Initialize SQLite connection and repositories (if backend is SQLite)
        self.conn: Optional[sqlite3.Connection] = None
        self.repos: Optional[RepositoryFactory] = None
        
        if self.backend == 'sqlite':
            if not SQLITE_AVAILABLE:
                print("⚠ SQLite modules not available, falling back to CSV")
                self.backend = 'csv'
            elif not is_sqlite_available():
                print("⚠ SQLite database not initialized, falling back to CSV")
                self.backend = 'csv'
            else:
                try:
                    self.conn = open_connection(DATABASE_PATH)
                    apply_migrations(self.conn)  # apply any pending schema migrations
                    self.repos = RepositoryFactory(self.conn)
                except Exception as e:
                    print(f"⚠ SQLite init failed, falling back to CSV: {e}")
                    self.backend = 'csv'
    
    def get_backend(self) -> str:
        """Get current backend ('csv' or 'sqlite')"""
        return self.backend
    
    def is_sqlite_mode(self) -> bool:
        """Check if currently using SQLite backend"""
        return self.backend == 'sqlite' and self.conn is not None
    
    def close(self):
        """Close database connection (if open)"""
        if self.conn:
            self.conn.close()
            self.conn = None
            self.repos = None
    
    # ============================================================
    # SKU Operations
    # ============================================================
    
    def read_skus(self) -> List[SKU]:
        """Read all SKUs from storage"""
        if self.is_sqlite_mode():
            # SQLite: Use repository
            assert self.repos is not None
            try:
                skus_dict = self.repos.skus().list()
                return [self._dict_to_sku(s) for s in skus_dict]
            except Exception as e:
                print(f"⚠ SQLite read_skus failed, falling back to CSV: {e}")
                return self.csv_layer.read_skus()
        else:
            # CSV: Delegate to CSV layer
            return self.csv_layer.read_skus()
    
    def write_sku(self, sku: SKU):
        """Write SKU to storage"""
        if self.is_sqlite_mode():
            # SQLite: Use repository
            assert self.repos is not None
            try:
                sku_dict = self._sku_to_dict(sku)
                self.repos.skus().upsert(sku_dict)
            except Exception as e:
                print(f"⚠ SQLite write_sku failed, falling back to CSV: {e}")
                self.csv_layer.write_sku(sku)
        else:
            # CSV: Delegate to CSV layer
            self.csv_layer.write_sku(sku)
    
    def get_all_sku_ids(self) -> List[str]:
        """Get list of all SKU identifiers"""
        if self.is_sqlite_mode():
            assert self.repos is not None
            try:
                skus = self.repos.skus().list()
                return [s['sku'] for s in skus]
            except Exception as e:
                print(f"⚠ SQLite get_all_sku_ids failed, falling back to CSV: {e}")
                return self.csv_layer.get_all_sku_ids()
        else:
            return self.csv_layer.get_all_sku_ids()
    
    def sku_exists(self, sku_id: str) -> bool:
        """Check if SKU exists"""
        if self.is_sqlite_mode():
            assert self.repos is not None
            try:
                return self.repos.skus().exists(sku_id)
            except Exception as e:
                print(f"⚠ SQLite sku_exists failed, falling back to CSV: {e}")
                return self.csv_layer.sku_exists(sku_id)
        else:
            return self.csv_layer.sku_exists(sku_id)
    
    def search_skus(self, query: str) -> List[SKU]:
        """Search SKUs by query string"""
        # Always use CSV for search (SQLite full-text search not implemented yet)
        return self.csv_layer.search_skus(query)
    
    def update_sku_object(self, old_sku_id: str, sku_object: SKU) -> bool:
        """Update SKU (with potential SKU ID change)"""
        if self.is_sqlite_mode():
            assert self.repos is not None
            try:
                sku_dict = self._sku_to_dict(sku_object)
                
                # If SKU ID changed, need to update transactions too
                if old_sku_id != sku_object.sku:
                    # TODO: Implement SKU rename in SQLite (cascade update)
                    print(f"⚠ SKU rename not yet supported in SQLite, using CSV")
                    return self.csv_layer.update_sku_object(old_sku_id, sku_object)
                
                self.repos.skus().upsert(sku_dict)
                return True
            except Exception as e:
                print(f"⚠ SQLite update_sku failed, falling back to CSV: {e}")
                return self.csv_layer.update_sku_object(old_sku_id, sku_object)
        else:
            return self.csv_layer.update_sku_object(old_sku_id, sku_object)
    
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
        min_shelf_life_days: int = 0,
        waste_penalty_mode: str = "",
        waste_penalty_factor: float = 0.0,
        waste_risk_threshold: float = 0.0,
        forecast_method: str = "",
        mc_distribution: str = "",
        mc_n_simulations: int = 0,
        mc_random_seed: int = 0,
        mc_output_stat: str = "",
        mc_output_percentile: int = 0,
        mc_horizon_mode: str = "",
        mc_horizon_days: int = 0,
        in_assortment: bool = True,
        target_csl: float = 0.0,
        has_expiry_label: bool = False,
        category: Optional[str] = None,
        department: Optional[str] = None,
    ) -> bool:
        """Update SKU - routes to appropriate backend."""
        if self.is_sqlite_mode():
            # Preserve existing category/department if caller passes None sentinel
            existing_sku: Optional[SKU] = next(
                (s for s in self.read_skus() if s.sku == old_sku_id), None
            )
            sku_object = SKU(
                sku=new_sku_id,
                description=new_description,
                ean=new_ean,
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
                category=category if category is not None else (existing_sku.category if existing_sku else ""),
                department=department if department is not None else (existing_sku.department if existing_sku else ""),
            )
            return self.update_sku_object(old_sku_id, sku_object)
        else:
            return self.csv_layer.update_sku(
                old_sku_id, new_sku_id, new_description, new_ean,
                moq, pack_size, lead_time_days, review_period,
                safety_stock, shelf_life_days, max_stock, reorder_point,
                demand_variability, oos_boost_percent, oos_detection_mode,
                oos_popup_preference, min_shelf_life_days, waste_penalty_mode,
                waste_penalty_factor, waste_risk_threshold, forecast_method,
                mc_distribution, mc_n_simulations, mc_random_seed,
                mc_output_stat, mc_output_percentile, mc_horizon_mode,
                mc_horizon_days, in_assortment, target_csl,
                has_expiry_label=has_expiry_label,
                category=category,
                department=department,
            )

    def delete_sku(self, sku_id: str) -> bool:
        """Delete SKU"""
        if self.is_sqlite_mode():
            assert self.repos is not None
            try:
                self.repos.skus().delete(sku_id)
                return True
            except Exception as e:
                print(f"⚠ SQLite delete_sku failed, falling back to CSV: {e}")
                return self.csv_layer.delete_sku(sku_id)
        else:
            return self.csv_layer.delete_sku(sku_id)
    
    def can_delete_sku(self, sku_id: str) -> tuple[bool, str]:
        """Check if SKU can be deleted (no dependent transactions)"""
        # Always check in CSV (SQLite FK constraints will enforce)
        return self.csv_layer.can_delete_sku(sku_id)
    
    # ============================================================
    # Transaction Operations
    # ============================================================
    
    def read_transactions(self) -> List[Transaction]:
        """Read all transactions"""
        if self.is_sqlite_mode():
            assert self.repos is not None
            try:
                txns_dict = self.repos.ledger().list_transactions(limit=10000)
                return [self._dict_to_transaction(t) for t in txns_dict]
            except Exception as e:
                print(f"⚠ SQLite read_transactions failed, falling back to CSV: {e}")
                return self.csv_layer.read_transactions()
        else:
            return self.csv_layer.read_transactions()
    
    def write_transaction(self, txn: Transaction):
        """Write single transaction"""
        if self.is_sqlite_mode():
            assert self.repos is not None
            try:
                self.repos.ledger().append_transaction(
                    date=txn.date.isoformat(),
                    sku=txn.sku,
                    event=txn.event.value,
                    qty=txn.qty,
                    receipt_date=txn.receipt_date.isoformat() if txn.receipt_date else None,
                    note=txn.note or ''
                )
            except Exception as e:
                print(f"⚠ SQLite write_transaction failed, falling back to CSV: {e}")
                self.csv_layer.write_transaction(txn)
        else:
            self.csv_layer.write_transaction(txn)
    
    def write_transactions_batch(self, txns: List[Transaction]):
        """Write multiple transactions (batch mode)"""
        if self.is_sqlite_mode():
            assert self.repos is not None
            try:
                batch = []
                for txn in txns:
                    batch.append({
                        'date': txn.date.isoformat(),
                        'sku': txn.sku,
                        'event': txn.event.value,
                        'qty': txn.qty,
                        'receipt_date': txn.receipt_date.isoformat() if txn.receipt_date else None,
                        'note': txn.note or ''
                    })
                self.repos.ledger().append_batch(batch)
            except Exception as e:
                print(f"⚠ SQLite write_transactions_batch failed, falling back to CSV: {e}")
                self.csv_layer.write_transactions_batch(txns)
        else:
            self.csv_layer.write_transactions_batch(txns)
    
    # ============================================================
    # Sales Operations
    # ============================================================
    
    def read_sales(self, sku: Optional[str] = None, 
                   start_date: Optional[date] = None,
                   end_date: Optional[date] = None) -> List[SalesRecord]:
        """Read sales records (with optional filters)"""
        # Always use CSV for sales read (filtering not implemented in SQLite yet)
        all_sales = self.csv_layer.read_sales()
        
        # Apply filters if provided
        if sku or start_date or end_date:
            filtered = []
            for sale in all_sales:
                if sku and sale.sku != sku:
                    continue
                if start_date and sale.date < start_date:
                    continue
                if end_date and sale.date > end_date:
                    continue
                filtered.append(sale)
            return filtered
        
        return all_sales
    
    def write_sales_record(self, sale: SalesRecord):
        """Write single sales record"""
        if self.is_sqlite_mode():
            # SQLite sales insertion via SQL (no repository method yet)
            assert self.conn is not None
            try:
                cursor = self.conn.cursor()
                cursor.execute("""
                    INSERT INTO sales (date, sku, qty_sold, promo_flag)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(date, sku) DO UPDATE SET
                        qty_sold = excluded.qty_sold,
                        promo_flag = excluded.promo_flag
                """, (sale.date.isoformat(), sale.sku, sale.qty_sold, sale.promo_flag))
                self.conn.commit()
            except Exception as e:
                print(f"⚠ SQLite write_sales_record failed, falling back to CSV: {e}")
                self.csv_layer.write_sales_record(sale)
        else:
            self.csv_layer.write_sales_record(sale)
    
    def append_sales(self, sale: SalesRecord):
        """Append sales record (alias for write_sales_record)"""
        self.write_sales_record(sale)
    
    # ============================================================
    # Settings & Holidays (Always use CSV for now)
    # ============================================================
    
    def read_settings(self) -> Dict:
        """Read settings (always from CSV/JSON for now)"""
        return self.csv_layer.read_settings()
    
    def write_settings(self, settings: Dict):
        """Write settings (always to CSV/JSON for now)"""
        self.csv_layer.write_settings(settings)
    
    def get_default_sku_params(self) -> Dict[str, Any]:
        """Get default SKU parameters"""
        return self.csv_layer.get_default_sku_params()
    
    def read_holidays(self) -> List[Dict[str, Any]]:
        """Read holidays (always from CSV/JSON for now)"""
        return self.csv_layer.read_holidays()
    
    def write_holidays(self, holidays: List[Dict[str, Any]]):
        """Write holidays (always to CSV/JSON for now)"""
        self.csv_layer.write_holidays(holidays)
    
    def add_holiday(self, holiday: Dict[str, Any]):
        """Add holiday"""
        self.csv_layer.add_holiday(holiday)
    
    def update_holiday(self, index: int, holiday: Dict[str, Any]):
        """Update holiday by index"""
        self.csv_layer.update_holiday(index, holiday)
    
    def delete_holiday(self, index: int):
        """Delete holiday by index"""
        self.csv_layer.delete_holiday(index)
    
    # ============================================================
    # Delegation methods (always use CSV)
    # ============================================================
    
    # These methods delegate directly to CSV layer (not yet implemented in SQLite)
    
    def read_order_logs(self):
        return self.csv_layer.read_order_logs()
    
    def write_order_log(self, *args, **kwargs):
        self.csv_layer.write_order_log(*args, **kwargs)
    
    def read_receiving_logs(self):
        return self.csv_layer.read_receiving_logs()
    
    def write_receiving_log(self, *args, **kwargs):
        self.csv_layer.write_receiving_log(*args, **kwargs)
    
    def read_audit_log(self, sku: Optional[str] = None, limit: Optional[int] = None):
        return self.csv_layer.read_audit_log(sku, limit)
    
    def write_audit_log(self, audit_log: AuditLog):
        self.csv_layer.log_audit(
            operation=audit_log.operation,
            details=audit_log.details if hasattr(audit_log, 'details') else '',
            sku=audit_log.sku if hasattr(audit_log, 'sku') else None,
            user=audit_log.user if hasattr(audit_log, 'user') else 'system',
        )
    
    def read_lots(self) -> List[Lot]:
        return self.csv_layer.read_lots()
    
    def write_lot(self, lot: Lot):
        self.csv_layer.write_lot(lot)
    
    def read_promo_calendar(self) -> List[PromoWindow]:
        return self.csv_layer.read_promo_calendar()
    
    def write_promo_window(self, promo: PromoWindow):
        self.csv_layer.write_promo_window(promo)
    
    def read_event_uplift_rules(self) -> List[EventUpliftRule]:
        return self.csv_layer.read_event_uplift_rules()
    
    def write_event_uplift_rule(self, rule: EventUpliftRule):
        self.csv_layer.write_event_uplift_rule(rule)
    
    # ============================================================
    # Helper: Domain Model Conversions
    # ============================================================
    
    @staticmethod
    def _dict_to_sku(d: Dict) -> SKU:
        """Convert repository dict to SKU domain model"""
        return SKU(
            sku=d['sku'],
            description=d.get('description', ''),
            ean=d.get('ean'),
            moq=d.get('moq', 1),
            pack_size=d.get('pack_size', 1),
            lead_time_days=d.get('lead_time_days', 7),
            review_period=d.get('review_period', 7),
            safety_stock=d.get('safety_stock', 0),
            shelf_life_days=d.get('shelf_life_days', 0),
            min_shelf_life_days=d.get('min_shelf_life_days', 0),
            waste_penalty_mode=d.get('waste_penalty_mode', ''),
            waste_penalty_factor=d.get('waste_penalty_factor', 1.0),
            waste_risk_threshold=d.get('waste_risk_threshold', 0.0),
            max_stock=d.get('max_stock', 500),
            reorder_point=d.get('reorder_point', 10),
            demand_variability=DemandVariability(d.get('demand_variability', 'MEDIUM').upper()),
            category=d.get('category', ''),
            department=d.get('department', ''),
            oos_boost_percent=d.get('oos_boost_percent', 0.0),
            oos_detection_mode=d.get('oos_detection_mode', ''),
            oos_popup_preference=d.get('oos_popup_preference', 'ask'),
            forecast_method=d.get('forecast_method', ''),
            mc_distribution=d.get('mc_distribution', 'normal'),
            mc_n_simulations=d.get('mc_n_simulations', 1000),
            mc_random_seed=d.get('mc_random_seed') or 0,
            mc_output_stat=d.get('mc_output_stat', 'mean'),
            mc_output_percentile=d.get('mc_output_percentile', 50),
            mc_horizon_mode=d.get('mc_horizon_mode', ''),
            mc_horizon_days=d.get('mc_horizon_days', 30),
            in_assortment=d.get('in_assortment', 1),
            target_csl=d.get('target_csl', 0.0),
            has_expiry_label=bool(d.get('has_expiry_label', False)),
        )
    
    @staticmethod
    def _sku_to_dict(sku: SKU) -> Dict:
        """Convert SKU domain model to repository dict"""
        return {
            'sku': sku.sku,
            'description': sku.description,
            'ean': sku.ean,
            'moq': sku.moq,
            'pack_size': sku.pack_size,
            'lead_time_days': sku.lead_time_days,
            'review_period': sku.review_period,
            'safety_stock': sku.safety_stock,
            'shelf_life_days': sku.shelf_life_days,
            'min_shelf_life_days': sku.min_shelf_life_days,
            'waste_penalty_mode': sku.waste_penalty_mode,
            'waste_penalty_factor': sku.waste_penalty_factor,
            'waste_risk_threshold': sku.waste_risk_threshold,
            'max_stock': sku.max_stock,
            'reorder_point': sku.reorder_point,
            'demand_variability': sku.demand_variability.value.upper(),
            'category': sku.category,
            'department': sku.department,
            'oos_boost_percent': sku.oos_boost_percent,
            'oos_detection_mode': sku.oos_detection_mode,
            'oos_popup_preference': sku.oos_popup_preference,
            'forecast_method': sku.forecast_method,
            'mc_distribution': sku.mc_distribution,
            'mc_n_simulations': sku.mc_n_simulations,
            'mc_random_seed': sku.mc_random_seed,
            'mc_output_stat': sku.mc_output_stat,
            'mc_output_percentile': sku.mc_output_percentile,
            'mc_horizon_mode': sku.mc_horizon_mode,
            'mc_horizon_days': sku.mc_horizon_days,
            'in_assortment': sku.in_assortment,
            'target_csl': sku.target_csl,
            'has_expiry_label': sku.has_expiry_label,
        }
    
    @staticmethod
    def _dict_to_transaction(d: Dict) -> Transaction:
        """Convert repository dict to Transaction domain model"""
        return Transaction(
            date=date.fromisoformat(d['date']),
            sku=d['sku'],
            event=EventType(d['event']),
            qty=d['qty'],
            receipt_date=date.fromisoformat(d['receipt_date']) if d.get('receipt_date') else None,
            note=d.get('note'),
        )
