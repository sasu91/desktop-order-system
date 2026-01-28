"""
CSV persistence layer with auto-create functionality.

Handles all file I/O for transactions, SKUs, sales, etc.
Auto-creates files with correct headers on first run.
"""
import csv
import os
from datetime import date
from pathlib import Path
from typing import List, Dict, Optional

from ..domain.models import Transaction, EventType, SKU, SalesRecord


class CSVLayer:
    """Manages all CSV file operations with auto-create."""
    
    # Default data directory
    DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent / "data"
    
    # CSV file schemas (filename -> list of columns)
    SCHEMAS = {
        "skus.csv": ["sku", "description", "ean"],
        "transactions.csv": ["date", "sku", "event", "qty", "receipt_date", "note"],
        "sales.csv": ["date", "sku", "qty_sold"],
        "order_logs.csv": ["order_id", "date", "sku", "qty_ordered", "status"],
        "receiving_logs.csv": ["receipt_id", "date", "sku", "qty_received", "receipt_date"],
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
        """Read all SKUs from skus.csv."""
        rows = self._read_csv("skus.csv")
        skus = []
        for row in rows:
            try:
                sku = SKU(
                    sku=row.get("sku", "").strip(),
                    description=row.get("description", "").strip(),
                    ean=row.get("ean", "").strip() or None,
                )
                skus.append(sku)
            except ValueError as e:
                # Log but don't crash
                print(f"Warning: Invalid SKU in skus.csv: {e}")
        return skus
    
    def write_sku(self, sku: SKU):
        """Add a new SKU to skus.csv."""
        rows = self._read_csv("skus.csv")
        rows.append({
            "sku": sku.sku,
            "description": sku.description,
            "ean": sku.ean or "",
        })
        self._write_csv("skus.csv", rows)
    
    def get_all_sku_ids(self) -> List[str]:
        """Get list of all SKU identifiers."""
        skus = self.read_skus()
        return [s.sku for s in skus]
    
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
        """Add multiple transactions at once."""
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
        self._write_csv("transactions.csv", rows)
    
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
    
    # ============ Order Log Operations ============
    
    def read_order_logs(self) -> List[Dict]:
        """Read order logs."""
        return self._read_csv("order_logs.csv")
    
    def write_order_log(self, order_id: str, date_str: str, sku: str, qty: int, status: str):
        """Write order log entry."""
        self._append_csv("order_logs.csv", {
            "order_id": order_id,
            "date": date_str,
            "sku": sku,
            "qty_ordered": str(qty),
            "status": status,
        })
    
    # ============ Receiving Log Operations ============
    
    def read_receiving_logs(self) -> List[Dict]:
        """Read receiving logs."""
        return self._read_csv("receiving_logs.csv")
    
    def write_receiving_log(self, receipt_id: str, date_str: str, sku: str, qty: int, receipt_date: str):
        """Write receiving log entry."""
        self._append_csv("receiving_logs.csv", {
            "receipt_id": receipt_id,
            "date": date_str,
            "sku": sku,
            "qty_received": str(qty),
            "receipt_date": receipt_date,
        })
