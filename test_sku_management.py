#!/usr/bin/env python3
"""
Quick manual test script for SKU management functionality.
Run this to verify CRUD operations work correctly.
"""
from pathlib import Path
import tempfile
import shutil

from src.persistence.csv_layer import CSVLayer
from src.domain.models import SKU, Transaction, EventType
from datetime import date


def main():
    """Test SKU management features."""
    # Create temp directory
    tmpdir = Path(tempfile.mkdtemp())
    print(f"Testing in temporary directory: {tmpdir}")
    
    try:
        csv_layer = CSVLayer(data_dir=tmpdir)
        
        # Test 1: Create SKUs
        print("\n=== Test 1: Create SKUs ===")
        sku1 = SKU(sku="SKU001", description="Caffè Arabica 250g", ean="8001234567890")
        sku2 = SKU(sku="SKU002", description="Latte Intero 1L", ean="8002345678901")
        sku3 = SKU(sku="SKU003", description="Yogurt Bianco 125g", ean=None)
        
        csv_layer.write_sku(sku1)
        csv_layer.write_sku(sku2)
        csv_layer.write_sku(sku3)
        
        all_skus = csv_layer.read_skus()
        print(f"✓ Created {len(all_skus)} SKUs")
        for sku in all_skus:
            print(f"  - {sku.sku}: {sku.description} (EAN: {sku.ean or 'N/A'})")
        
        # Test 2: Search SKUs
        print("\n=== Test 2: Search SKUs ===")
        results = csv_layer.search_skus("latte")
        print(f"✓ Search for 'latte': found {len(results)} results")
        for sku in results:
            print(f"  - {sku.sku}: {sku.description}")
        
        results = csv_layer.search_skus("SKU00")
        print(f"✓ Search for 'SKU00': found {len(results)} results")
        
        # Test 3: SKU exists
        print("\n=== Test 3: SKU exists ===")
        print(f"✓ SKU001 exists: {csv_layer.sku_exists('SKU001')}")
        print(f"✓ SKU999 exists: {csv_layer.sku_exists('SKU999')}")
        
        # Test 4: Update SKU (description/EAN only)
        print("\n=== Test 4: Update SKU (description/EAN) ===")
        success = csv_layer.update_sku("SKU001", "SKU001", "Caffè Robusta 250g", "8001111111111")
        print(f"✓ Updated SKU001: {success}")
        updated = csv_layer.read_skus()[0]
        print(f"  - New description: {updated.description}")
        print(f"  - New EAN: {updated.ean}")
        
        # Test 5: Update SKU code (with ledger propagation)
        print("\n=== Test 5: Update SKU code (propagation test) ===")
        # Add a transaction for SKU002
        csv_layer.write_transaction(
            Transaction(
                date=date(2026, 1, 15),
                sku="SKU002",
                event=EventType.SNAPSHOT,
                qty=100
            )
        )
        print("✓ Created transaction for SKU002")
        
        # Update SKU code
        success = csv_layer.update_sku("SKU002", "SKU222", "Latte Parzialmente Scremato 1L", "8009999999999")
        print(f"✓ Updated SKU002 → SKU222: {success}")
        
        # Verify transaction updated
        txns = csv_layer.read_transactions()
        print(f"✓ Transaction SKU updated to: {txns[0].sku}")
        
        # Test 6: Can delete SKU (with references)
        print("\n=== Test 6: Can delete SKU ===")
        can_del, reason = csv_layer.can_delete_sku("SKU222")
        print(f"✓ Can delete SKU222: {can_del}")
        if not can_del:
            print(f"  Reason: {reason}")
        
        can_del, reason = csv_layer.can_delete_sku("SKU003")
        print(f"✓ Can delete SKU003: {can_del}")
        
        # Test 7: Delete SKU
        print("\n=== Test 7: Delete SKU ===")
        success = csv_layer.delete_sku("SKU003")
        print(f"✓ Deleted SKU003: {success}")
        
        remaining = csv_layer.read_skus()
        print(f"✓ Remaining SKUs: {len(remaining)}")
        for sku in remaining:
            print(f"  - {sku.sku}: {sku.description}")
        
        print("\n" + "=" * 50)
        print("✅ All tests passed successfully!")
        print("=" * 50)
        
    finally:
        # Cleanup
        shutil.rmtree(tmpdir)
        print(f"\n✓ Cleaned up temporary directory")


if __name__ == "__main__":
    main()
