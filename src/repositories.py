"""
Repository/DAL Layer for SQLite Storage

FASE 3: Data Access Layer with Idempotency and Atomicity
- SKURepository: Product master data CRUD
- LedgerRepository: Transaction log append-only operations
- OrdersRepository: Order lifecycle management
- ReceivingRepository: Receipt processing with document_id idempotency

Design Principles:
- All write operations wrapped in database transactions
- Idempotency enforced via UNIQUE constraints + pre-checks
- Error handling: IntegrityError mapped to business exceptions
- No business logic: Pure data access layer
"""

import sqlite3
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from contextlib import contextmanager

from .db import transaction


# ============================================================
# Custom Exceptions
# ============================================================

class RepositoryError(Exception):
    """Base exception for repository operations"""
    pass


class DuplicateKeyError(RepositoryError):
    """Raised when UNIQUE constraint is violated"""
    pass


class ForeignKeyError(RepositoryError):
    """Raised when FOREIGN KEY constraint is violated"""
    pass


class NotFoundError(RepositoryError):
    """Raised when entity not found"""
    pass


class BusinessRuleError(RepositoryError):
    """Raised when CHECK constraint is violated"""
    pass


# ============================================================
# SKU Repository
# ============================================================

class SKURepository:
    """
    Repository for SKU (product master data) operations.
    
    Responsibilities:
    - CRUD operations on skus table
    - Assortment status management
    - Batch listing with filters
    """
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
    
    def get(self, sku: str) -> Optional[Dict[str, Any]]:
        """
        Get SKU by primary key.
        
        Args:
            sku: SKU code
        
        Returns:
            Dict with SKU data or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM skus WHERE sku = ?", (sku,))
        row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
    
    def exists(self, sku: str) -> bool:
        """Check if SKU exists."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM skus WHERE sku = ? LIMIT 1", (sku,))
        return cursor.fetchone() is not None
    
    def upsert(self, sku_data: Dict[str, Any]) -> str:
        """
        Insert or update SKU.
        
        Args:
            sku_data: Dictionary with SKU fields (must include 'sku', 'description')
        
        Returns:
            SKU code
        
        Raises:
            ForeignKeyError: If referenced entity doesn't exist
            BusinessRuleError: If CHECK constraint violated
        """
        sku = sku_data.get('sku')
        if not sku:
            raise ValueError("sku field is required")
        
        # Check if exists
        existing = self.get(sku)
        
        try:
            with transaction(self.conn) as cur:
                if existing:
                    # UPDATE
                    set_clauses = []
                    values = []
                    
                    # Exclude primary key and audit fields from UPDATE
                    exclude_fields = {'sku', 'created_at'}
                    
                    for key, value in sku_data.items():
                        if key not in exclude_fields:
                            set_clauses.append(f"{key} = ?")
                            values.append(value)
                    
                    # Always update updated_at
                    set_clauses.append("updated_at = datetime('now')")
                    
                    values.append(sku)  # WHERE clause
                    
                    sql = f"UPDATE skus SET {', '.join(set_clauses)} WHERE sku = ?"
                    cur.execute(sql, values)
                
                else:
                    # INSERT
                    # Set defaults
                    defaults = {
                        'moq': 1,
                        'pack_size': 1,
                        'lead_time_days': 7,
                        'review_period': 7,
                        'safety_stock': 0,
                        'shelf_life_days': 0,
                        'min_shelf_life_days': 0,
                        'waste_penalty_mode': '',
                        'waste_penalty_factor': 0.0,
                        'waste_risk_threshold': 0.0,
                        'max_stock': 999,
                        'reorder_point': 10,
                        'demand_variability': 'STABLE',
                        'category': '',
                        'department': '',
                        'oos_boost_percent': 0.0,
                        'oos_detection_mode': '',
                        'oos_popup_preference': 'ask',
                        'forecast_method': '',
                        'mc_distribution': '',
                        'mc_n_simulations': 0,
                        'mc_random_seed': 0,
                        'mc_output_stat': '',
                        'mc_output_percentile': 0,
                        'mc_horizon_mode': '',
                        'mc_horizon_days': 0,
                        'in_assortment': 1,
                        'target_csl': 0.0,
                        'has_expiry_label': 0,
                    }
                    
                    # Merge with provided data
                    insert_data = {**defaults, **sku_data}
                    
                    columns = list(insert_data.keys())
                    placeholders = ', '.join(['?'] * len(columns))
                    values = [insert_data[col] for col in columns]
                    
                    sql = f"INSERT INTO skus ({', '.join(columns)}) VALUES ({placeholders})"
                    cur.execute(sql, values)
            
            return sku
        
        except (RuntimeError, sqlite3.IntegrityError) as e:
            error_msg = str(e).lower()
            if "foreign key" in error_msg:
                raise ForeignKeyError(f"Foreign key constraint failed for SKU {sku}") from e
            elif "check constraint" in error_msg:
                raise BusinessRuleError(f"Business rule violated for SKU {sku}: {e}") from e
            elif "unique" in error_msg:
                raise DuplicateKeyError(f"SKU {sku} already exists") from e
            raise
    
    def list(self, filters: Optional[Dict[str, Any]] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        List SKUs with optional filters.
        
        Args:
            filters: Dict with filter conditions (e.g., {'in_assortment': 1, 'category': 'DAIRY'})
            limit: Maximum number of rows to return
        
        Returns:
            List of SKU dictionaries
        """
        cursor = self.conn.cursor()
        
        where_clauses = []
        values = []
        
        if filters:
            for key, value in filters.items():
                where_clauses.append(f"{key} = ?")
                values.append(value)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"SELECT * FROM skus {where_sql} ORDER BY sku LIMIT ?"
        values.append(limit)
        
        cursor.execute(sql, values)
        return [dict(row) for row in cursor.fetchall()]
    
    def toggle_assortment(self, sku: str, in_assortment: bool) -> bool:
        """
        Toggle assortment status (soft delete/restore).
        
        Args:
            sku: SKU code
            in_assortment: True to include, False to exclude
        
        Returns:
            True if updated, False if SKU not found
        """
        if not self.exists(sku):
            return False
        
        with transaction(self.conn) as cur:
            cur.execute("""
                UPDATE skus 
                SET in_assortment = ?, updated_at = datetime('now')
                WHERE sku = ?
            """, (1 if in_assortment else 0, sku))
        
        return True
    
    def delete(self, sku: str) -> bool:
        """
        Delete SKU (hard delete).
        
        Args:
            sku: SKU code
        
        Returns:
            True if deleted, False if not found
        
        Raises:
            ForeignKeyError: If SKU has transaction history (ON DELETE RESTRICT)
        """
        try:
            with transaction(self.conn) as cur:
                cur.execute("DELETE FROM skus WHERE sku = ?", (sku,))
                return cur.rowcount > 0
        
        except RuntimeError as e:
            # Transaction context manager wraps IntegrityError as RuntimeError
            if "foreign key" in str(e).lower():
                raise ForeignKeyError(
                    f"Cannot delete SKU {sku}: has transaction history. Use toggle_assortment() instead."
                ) from e
            raise
        
        except sqlite3.IntegrityError as e:
            # Fallback if IntegrityError not wrapped
            if "foreign key" in str(e).lower():
                raise ForeignKeyError(
                    f"Cannot delete SKU {sku}: has transaction history. Use toggle_assortment() instead."
                ) from e
            raise


# ============================================================
# Ledger Repository
# ============================================================

class LedgerRepository:
    """
    Repository for transaction ledger (append-only log).
    
    Responsibilities:
    - Append transactions to ledger
    - List transactions with filters (sku, date range, event type)
    - Delete specific transaction by ID (resolves Risk #1)
    """
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
    
    def append_transaction(
        self,
        date: str,
        sku: str,
        event: str,
        qty: int,
        receipt_date: Optional[str] = None,
        note: str = ''
    ) -> int:
        """
        Append single transaction to ledger.
        
        Args:
            date: Transaction date (YYYY-MM-DD)
            sku: SKU code
            event: Event type (SNAPSHOT, ORDER, RECEIPT, SALE, WASTE, ADJUST, UNFULFILLED, etc.)
            qty: Quantity (signed integer)
            receipt_date: Optional receipt date for ORDER/RECEIPT events
            note: Optional note
        
        Returns:
            transaction_id (AUTOINCREMENT)
        
        Raises:
            ForeignKeyError: If SKU doesn't exist
            BusinessRuleError: If event type invalid or qty violates business rules
        """
        try:
            with transaction(self.conn) as cur:
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (date, sku, event, qty, receipt_date, note))
                
                tx_id = cur.lastrowid
                assert tx_id is not None, "lastrowid should be set after INSERT"
                return tx_id
        
        except (RuntimeError, sqlite3.IntegrityError) as e:
            error_msg = str(e).lower()
            if "foreign key" in error_msg:
                raise ForeignKeyError(f"SKU {sku} does not exist") from e
            elif "check constraint" in error_msg:
                raise BusinessRuleError(f"Invalid event type or business rule violated: {e}") from e
            raise
    
    def append_batch(self, transactions: List[Dict[str, Any]]) -> List[int]:
        """
        Append multiple transactions atomically.
        
        Args:
            transactions: List of transaction dicts (date, sku, event, qty, receipt_date, note)
        
        Returns:
            List of transaction_ids
        
        Raises:
            ForeignKeyError, BusinessRuleError: On constraint violation (entire batch rolled back)
        """
        transaction_ids = []
        
        try:
            with transaction(self.conn, isolation_level="IMMEDIATE") as cur:
                for txn in transactions:
                    cur.execute("""
                        INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        txn['date'],
                        txn['sku'],
                        txn['event'],
                        txn['qty'],
                        txn.get('receipt_date'),
                        txn.get('note', '')
                    ))
                    transaction_ids.append(cur.lastrowid)
            
            return transaction_ids
        
        except (RuntimeError, sqlite3.IntegrityError) as e:
            error_msg = str(e).lower()
            if "foreign key" in error_msg:
                raise ForeignKeyError(f"One or more SKUs do not exist") from e
            elif "check constraint" in error_msg:
                raise BusinessRuleError(f"Invalid transaction data: {e}") from e
            raise
    
    def list_transactions(
        self,
        sku: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        event: Optional[str] = None,
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """
        List transactions with filters.
        
        Args:
            sku: Filter by SKU
            date_from: Filter by date >= date_from (inclusive)
            date_to: Filter by date <= date_to (inclusive)
            event: Filter by event type
            limit: Maximum rows to return
        
        Returns:
            List of transaction dictionaries (sorted by date ASC, transaction_id ASC)
        """
        cursor = self.conn.cursor()
        
        where_clauses = []
        values = []
        
        if sku:
            where_clauses.append("sku = ?")
            values.append(sku)
        
        if date_from:
            where_clauses.append("date >= ?")
            values.append(date_from)
        
        if date_to:
            where_clauses.append("date <= ?")
            values.append(date_to)
        
        if event:
            where_clauses.append("event = ?")
            values.append(event)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
            SELECT * FROM transactions 
            {where_sql}
            ORDER BY date ASC, transaction_id ASC
            LIMIT ?
        """
        values.append(limit)
        
        cursor.execute(sql, values)
        return [dict(row) for row in cursor.fetchall()]
    
    def get_by_id(self, transaction_id: int) -> Optional[Dict[str, Any]]:
        """
        Get transaction by ID.
        
        Args:
            transaction_id: Primary key
        
        Returns:
            Transaction dict or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,))
        row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
    
    def delete_by_id(self, transaction_id: int) -> bool:
        """
        Delete transaction by ID (resolves Risk #1: revert specific event).
        
        Args:
            transaction_id: Primary key
        
        Returns:
            True if deleted, False if not found
        
        Warning: Breaks ledger immutability. Use only for exception reversal.
        """
        with transaction(self.conn) as cur:
            cur.execute("DELETE FROM transactions WHERE transaction_id = ?", (transaction_id,))
            return cur.rowcount > 0
    
    def count_by_sku(self, sku: str) -> int:
        """Count transactions for a SKU."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM transactions WHERE sku = ?", (sku,))
        return cursor.fetchone()[0]


# ============================================================
# Orders Repository
# ============================================================

class OrdersRepository:
    """
    Repository for order lifecycle management.
    
    Responsibilities:
    - Create order logs
    - Update received quantities
    - Query unfulfilled orders
    """
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
    
    def create_order_log(self, order_data: Dict[str, Any]) -> str:
        """
        Create order log entry.
        
        Args:
            order_data: Dict with order fields (must include order_id, date, sku, qty_ordered)
        
        Returns:
            order_id
        
        Raises:
            DuplicateKeyError: If order_id already exists
            ForeignKeyError: If SKU doesn't exist
            BusinessRuleError: If qty_ordered <= 0 or qty_received > qty_ordered
        """
        required_fields = ['order_id', 'date', 'sku', 'qty_ordered']
        for field in required_fields:
            if field not in order_data:
                raise ValueError(f"Required field missing: {field}")
        
        # Set defaults
        defaults = {
            'qty_received': 0,
            'status': 'PENDING',
            'receipt_date': None,
            'promo_prebuild_enabled': 0,
            'promo_start_date': None,
            'target_open_qty': 0,
            'projected_stock_on_promo_start': 0,
            'prebuild_delta_qty': 0,
            'prebuild_qty': 0,
            'prebuild_coverage_days': 0,
            'prebuild_distribution_note': '',
            'event_uplift_active': 0,
            'event_delivery_date': None,
            'event_reason': '',
            'event_u_store_day': 1.0,
            'event_quantile': 0.0,
            'event_fallback_level': '',
            'event_beta_i': 1.0,
            'event_beta_fallback_level': '',
            'event_m_i': 1.0,
            'event_explain_short': '',
        }
        
        insert_data = {**defaults, **order_data}
        
        try:
            with transaction(self.conn) as cur:
                columns = list(insert_data.keys())
                placeholders = ', '.join(['?'] * len(columns))
                values = [insert_data[col] for col in columns]
                
                sql = f"INSERT INTO order_logs ({', '.join(columns)}) VALUES ({placeholders})"
                cur.execute(sql, values)
            
            return order_data['order_id']
        
        except (RuntimeError, sqlite3.IntegrityError) as e:
            error_msg = str(e).lower()
            if "unique constraint" in error_msg:
                raise DuplicateKeyError(f"Order ID {order_data['order_id']} already exists") from e
            elif "foreign key" in error_msg:
                raise ForeignKeyError(f"SKU {order_data['sku']} does not exist") from e
            elif "check constraint" in error_msg:
                raise BusinessRuleError(f"Business rule violated: {e}") from e
            raise
    
    def get(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order log by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM order_logs WHERE order_id = ?", (order_id,))
        row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
    
    def update_qty_received(
        self,
        order_id: str,
        qty_received: int,
        status: Optional[str] = None,
        receipt_date: Optional[str] = None
    ) -> bool:
        """
        Update received quantity for an order.
        
        Args:
            order_id: Order ID
            qty_received: New quantity received (cumulative)
            status: Optional new status (PENDING, PARTIAL, RECEIVED)
            receipt_date: Optional receipt date
        
        Returns:
            True if updated, False if order not found
        
        Raises:
            BusinessRuleError: If qty_received > qty_ordered
        """
        # Get current order
        order = self.get(order_id)
        if not order:
            return False
        
        # Auto-determine status if not provided
        if status is None:
            if qty_received >= order['qty_ordered']:
                status = 'RECEIVED'
            elif qty_received > 0:
                status = 'PARTIAL'
            else:
                status = 'PENDING'
        
        try:
            with transaction(self.conn) as cur:
                cur.execute("""
                    UPDATE order_logs
                    SET qty_received = ?,
                        status = ?,
                        receipt_date = ?,
                        updated_at = datetime('now')
                    WHERE order_id = ?
                """, (qty_received, status, receipt_date, order_id))
            
            return True
        
        except (RuntimeError, sqlite3.IntegrityError) as e:
            if "check constraint" in str(e).lower():
                raise BusinessRuleError(
                    f"qty_received ({qty_received}) exceeds qty_ordered ({order['qty_ordered']})"
                ) from e
            raise
    
    def get_unfulfilled_orders(
        self,
        sku: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get orders with status PENDING or PARTIAL.
        
        Args:
            sku: Filter by SKU (optional)
            limit: Maximum rows
        
        Returns:
            List of order dictionaries (sorted by date ASC)
        """
        cursor = self.conn.cursor()
        
        where_clauses = ["status IN ('PENDING', 'PARTIAL')"]
        values = []
        
        if sku:
            where_clauses.append("sku = ?")
            values.append(sku)
        
        values.append(limit)
        
        sql = f"""
            SELECT * FROM order_logs
            WHERE {' AND '.join(where_clauses)}
            ORDER BY date ASC
            LIMIT ?
        """
        
        cursor.execute(sql, values)
        return [dict(row) for row in cursor.fetchall()]
    
    def list(
        self,
        sku: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """List orders with filters."""
        cursor = self.conn.cursor()
        
        where_clauses = []
        values = []
        
        if sku:
            where_clauses.append("sku = ?")
            values.append(sku)
        
        if status:
            where_clauses.append("status = ?")
            values.append(status)
        
        if date_from:
            where_clauses.append("date >= ?")
            values.append(date_from)
        
        if date_to:
            where_clauses.append("date <= ?")
            values.append(date_to)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
            SELECT * FROM order_logs
            {where_sql}
            ORDER BY date DESC, order_id DESC
            LIMIT ?
        """
        values.append(limit)
        
        cursor.execute(sql, values)
        return [dict(row) for row in cursor.fetchall()]


# ============================================================
# Receiving Repository
# ============================================================

class ReceivingRepository:
    """
    Repository for receiving operations with document_id idempotency.
    
    Responsibilities:
    - Close receipts with idempotency check (resolves Risk #3)
    - Link orders to receipts via junction table (resolves Risk #6)
    - Query receiving logs
    """
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
    
    def close_receipt_idempotent(
        self,
        document_id: str,
        receipt_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Close receipt with idempotency check.
        
        Args:
            document_id: Unique document identifier (idempotency key)
            receipt_data: Dict with fields (date, sku, qty_received, receipt_date, order_ids, receipt_id)
        
        Returns:
            Dict with status:
            - {"status": "already_processed", "document_id": ...} if already exists
            - {"status": "success", "document_id": ..., "transaction_id": ...} if new
        
        Raises:
            ForeignKeyError: If SKU doesn't exist
            BusinessRuleError: If qty_received <= 0
        
        Process (ATOMIC via transaction):
        1. Check if document_id already processed (UNIQUE constraint)
        2. Insert receiving_logs
        3. Link orders via order_receipts junction table
        4. Create RECEIPT transaction in ledger
        5. Update order_logs.qty_received (if order_ids provided)
        """
        required_fields = ['date', 'sku', 'qty_received', 'receipt_date']
        for field in required_fields:
            if field not in receipt_data:
                raise ValueError(f"Required field missing: {field}")
        
        # Check if already processed (idempotency)
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM receiving_logs WHERE document_id = ?", (document_id,))
        if cursor.fetchone():
            return {"status": "already_processed", "document_id": document_id}
        
        # Process receipt atomically
        try:
            with transaction(self.conn, isolation_level="IMMEDIATE") as cur:
                # 1. Insert receiving_logs
                cur.execute("""
                    INSERT INTO receiving_logs (document_id, receipt_id, date, sku, qty_received, receipt_date, order_ids)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    document_id,
                    receipt_data.get('receipt_id'),
                    receipt_data['date'],
                    receipt_data['sku'],
                    receipt_data['qty_received'],
                    receipt_data['receipt_date'],
                    receipt_data.get('order_ids', '')
                ))
                
                # 2. Link orders via junction table (resolves Risk #6)
                order_ids = receipt_data.get('order_ids', '')
                if order_ids:
                    # Parse comma-separated order IDs
                    order_id_list = [oid.strip() for oid in order_ids.split(',') if oid.strip()]
                    
                    for order_id in order_id_list:
                        cur.execute("""
                            INSERT INTO order_receipts (order_id, document_id)
                            VALUES (?, ?)
                        """, (order_id, document_id))
                
                # 3. Create RECEIPT transaction in ledger
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, 'RECEIPT', ?, ?, ?)
                """, (
                    receipt_data['date'],
                    receipt_data['sku'],
                    receipt_data['qty_received'],
                    receipt_data['receipt_date'],
                    f"Document: {document_id}"
                ))
                
                transaction_id = cur.lastrowid
                
                # 4. Update order_logs.qty_received (if order_ids provided)
                if order_ids:
                    order_id_list = [oid.strip() for oid in order_ids.split(',') if oid.strip()]
                    
                    for order_id in order_id_list:
                        # Get current order
                        cur.execute("SELECT qty_ordered, qty_received FROM order_logs WHERE order_id = ?", (order_id,))
                        order_row = cur.fetchone()
                        
                        if order_row:
                            qty_ordered = order_row[0]
                            current_qty_received = order_row[1]
                            
                            # Calculate new qty_received (distribute evenly, simplified)
                            # Real app should track per-order allocations
                            new_qty_received = min(
                                current_qty_received + receipt_data['qty_received'] // len(order_id_list),
                                qty_ordered
                            )
                            
                            # Determine status
                            if new_qty_received >= qty_ordered:
                                status = 'RECEIVED'
                            elif new_qty_received > 0:
                                status = 'PARTIAL'
                            else:
                                status = 'PENDING'
                            
                            cur.execute("""
                                UPDATE order_logs
                                SET qty_received = ?,
                                    status = ?,
                                    receipt_date = ?,
                                    updated_at = datetime('now')
                                WHERE order_id = ?
                            """, (new_qty_received, status, receipt_data['receipt_date'], order_id))
            
            return {
                "status": "success",
                "document_id": document_id,
                "transaction_id": transaction_id
            }
        
        except (RuntimeError, sqlite3.IntegrityError) as e:
            error_msg = str(e).lower()
            if "unique constraint" in error_msg:
                # Race condition: another process processed same document_id
                return {"status": "already_processed", "document_id": document_id}
            elif "foreign key" in error_msg:
                raise ForeignKeyError(f"SKU or order_id does not exist: {e}") from e
            elif "check constraint" in error_msg:
                raise BusinessRuleError(f"Business rule violated: {e}") from e
            raise
    
    def get(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Get receiving log by document_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM receiving_logs WHERE document_id = ?", (document_id,))
        row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
    
    def list(
        self,
        sku: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """List receiving logs with filters."""
        cursor = self.conn.cursor()
        
        where_clauses = []
        values = []
        
        if sku:
            where_clauses.append("sku = ?")
            values.append(sku)
        
        if date_from:
            where_clauses.append("date >= ?")
            values.append(date_from)
        
        if date_to:
            where_clauses.append("date <= ?")
            values.append(date_to)
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        sql = f"""
            SELECT * FROM receiving_logs
            {where_sql}
            ORDER BY date DESC, document_id DESC
            LIMIT ?
        """
        values.append(limit)
        
        cursor.execute(sql, values)
        return [dict(row) for row in cursor.fetchall()]
    
    def get_linked_orders(self, document_id: str) -> List[str]:
        """
        Get order IDs linked to a receipt via junction table.
        
        Args:
            document_id: Receipt document ID
        
        Returns:
            List of order IDs
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT order_id FROM order_receipts
            WHERE document_id = ?
            ORDER BY order_id
        """, (document_id,))
        
        return [row[0] for row in cursor.fetchall()]


# ============================================================
# Repository Factory (Convenience)
# ============================================================

class RepositoryFactory:
    """
    Factory for creating repository instances sharing a connection.
    
    Usage:
        >>> from db import open_connection
        >>> conn = open_connection()
        >>> repos = RepositoryFactory(conn)
        >>> sku_repo = repos.skus()
        >>> ledger_repo = repos.ledger()
    """
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
    
    def skus(self) -> SKURepository:
        return SKURepository(self.conn)
    
    def ledger(self) -> LedgerRepository:
        return LedgerRepository(self.conn)
    
    def orders(self) -> OrdersRepository:
        return OrdersRepository(self.conn)
    
    def receiving(self) -> ReceivingRepository:
        return ReceivingRepository(self.conn)
