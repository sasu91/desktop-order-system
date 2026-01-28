"""
Legacy inventory migration: convert old snapshot CSV to ledger events.
"""
from datetime import date
from typing import List, Dict
from pathlib import Path

from ..domain.models import SKU, Transaction, EventType
from ..persistence.csv_layer import CSVLayer


class LegacyMigration:
    """Handle migration from legacy inventory snapshot to ledger."""
    
    @staticmethod
    def migrate_from_legacy_csv(
        legacy_csv_path: Path,
        csv_layer: CSVLayer,
        snapshot_date: date,
        force: bool = False,
    ) -> Dict[str, any]:
        """
        Migrate from legacy inventory CSV to ledger.
        
        Strategy:
        1. Check if ledger is already populated (if yes, skip unless force=True)
        2. Read legacy inventory file
        3. For each SKU, create SNAPSHOT event
        4. Write to transactions.csv
        
        Args:
            legacy_csv_path: Path to legacy inventory CSV
            csv_layer: CSV layer instance
            snapshot_date: Date to use for SNAPSHOT events
            force: Force migration even if ledger has events
        
        Returns:
            {
                "success": bool,
                "migrated_skus": int,
                "message": str,
                "errors": [...]
            }
        """
        result = {
            "success": False,
            "migrated_skus": 0,
            "message": "",
            "errors": [],
        }
        
        # Check if ledger is already populated
        existing_txns = csv_layer.read_transactions()
        if existing_txns and not force:
            result["message"] = "Ledger already populated; skipping migration (use force=True to override)"
            return result
        
        # Check if legacy file exists
        if not legacy_csv_path.exists():
            result["message"] = f"Legacy file not found: {legacy_csv_path}"
            return result
        
        # Read legacy inventory
        import csv
        legacy_inventory = {}
        try:
            with open(legacy_csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sku = row.get("sku", "").strip()
                    description = row.get("description", "").strip()
                    qty = int(row.get("quantity", 0))
                    ean = row.get("ean", "").strip() or None
                    
                    if sku:
                        legacy_inventory[sku] = {
                            "description": description,
                            "qty": qty,
                            "ean": ean,
                        }
        except Exception as e:
            result["errors"].append(f"Error reading legacy CSV: {e}")
            return result
        
        # Create SNAPSHOT transactions
        snapshot_txns = []
        for sku, data in legacy_inventory.items():
            try:
                txn = Transaction(
                    date=snapshot_date,
                    sku=sku,
                    event=EventType.SNAPSHOT,
                    qty=data["qty"],
                    note=f"Migrated from legacy inventory: {data['description']}",
                )
                snapshot_txns.append(txn)
                
                # Also add SKU if not already present
                existing_skus = csv_layer.get_all_sku_ids()
                if sku not in existing_skus:
                    sku_obj = SKU(
                        sku=sku,
                        description=data["description"],
                        ean=data["ean"],
                    )
                    csv_layer.write_sku(sku_obj)
                
                result["migrated_skus"] += 1
            except Exception as e:
                result["errors"].append(f"Error migrating SKU {sku}: {e}")
        
        # Write all snapshot transactions
        if snapshot_txns:
            try:
                csv_layer.write_transactions_batch(snapshot_txns)
                result["success"] = True
                result["message"] = f"Successfully migrated {result['migrated_skus']} SKUs"
            except Exception as e:
                result["errors"].append(f"Error writing transactions: {e}")
                result["success"] = False
        
        return result


def validate_legacy_migration(
    csv_layer: CSVLayer,
    snapshot_date: date,
) -> bool:
    """
    Validate that migration was successful by checking stock calculation.
    
    Strategy: For each SKU, calculate stock AsOf migration date and verify > 0.
    
    Args:
        csv_layer: CSV layer instance
        snapshot_date: Date used for migration
    
    Returns:
        True if validation passes
    """
    from ..domain.ledger import StockCalculator
    
    skus = csv_layer.get_all_sku_ids()
    transactions = csv_layer.read_transactions()
    
    for sku in skus:
        stock = StockCalculator.calculate_asof(
            sku,
            snapshot_date + date.fromisoformat("0001-01-01").year,  # Day after migration
            transactions,
        )
        # Expect stock > 0 for migrated SKUs
        if stock.on_hand <= 0 and stock.on_order <= 0:
            return False
    
    return True
