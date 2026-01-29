#!/usr/bin/env python3
"""
Test script to verify revert_exception_day fix.
"""
from datetime import date
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Transaction, EventType, DemandVariability
from src.persistence.csv_layer import CSVLayer
from src.workflows.receiving import ExceptionWorkflow

def test_revert_fix():
    """Test that revert_exception_day doesn't duplicate entries."""
    # Create temp directory
    tmpdir = tempfile.mkdtemp()
    print(f"Using temp directory: {tmpdir}")
    
    try:
        # Initialize CSV layer
        csv_layer = CSVLayer(data_dir=Path(tmpdir))
        
        # Add a test SKU
        print("\n1. Adding test SKU...")
        csv_layer.add_sku(SKU(
            sku="TEST001",
            description="Test Product",
            ean="",
            moq=1,
            lead_time_days=7,
            max_stock=999,
            reorder_point=10,
            supplier="TestSupplier",
            demand_variability=DemandVariability.STABLE,
        ))
        print("   ✓ SKU added")
        
        # Create exception workflow
        exception_workflow = ExceptionWorkflow(csv_layer)
        
        # Record some WASTE exceptions
        print("\n2. Recording WASTE exceptions...")
        exception_workflow.record_exception(
            event_type=EventType.WASTE,
            sku="TEST001",
            qty=5,
            event_date=date(2026, 1, 29),
            notes="Test waste 1"
        )
        exception_workflow.record_exception(
            event_type=EventType.WASTE,
            sku="TEST001",
            qty=3,
            event_date=date(2026, 1, 29),
            notes="Test waste 2"
        )
        
        # Add a different type to verify filtering
        exception_workflow.record_exception(
            event_type=EventType.ADJUST,
            sku="TEST001",
            qty=10,
            event_date=date(2026, 1, 29),
            notes="Test adjust"
        )
        
        print("   ✓ Exceptions recorded")
        
        # Check initial count
        txns_before = csv_layer.read_transactions()
        print(f"\n3. Transactions before revert: {len(txns_before)}")
        for t in txns_before:
            print(f"   - {t.date} | {t.sku} | {t.event.value} | qty={t.qty}")
        
        # Revert WASTE exceptions
        print("\n4. Reverting WASTE exceptions for TEST001 on 2026-01-29...")
        reverted_count = exception_workflow.revert_exception_day(
            event_date=date(2026, 1, 29),
            sku="TEST001",
            event_type=EventType.WASTE,
        )
        print(f"   ✓ Reverted {reverted_count} exception(s)")
        
        # Check final count
        txns_after = csv_layer.read_transactions()
        print(f"\n5. Transactions after revert: {len(txns_after)}")
        for t in txns_after:
            print(f"   - {t.date} | {t.sku} | {t.event.value} | qty={t.qty}")
        
        # Verify
        waste_count_after = sum(1 for t in txns_after if t.event == EventType.WASTE)
        adjust_count_after = sum(1 for t in txns_after if t.event == EventType.ADJUST)
        
        print(f"\n6. Verification:")
        print(f"   WASTE events remaining: {waste_count_after} (expected: 0)")
        print(f"   ADJUST events remaining: {adjust_count_after} (expected: 1)")
        
        if waste_count_after == 0 and adjust_count_after == 1:
            print("\n✅ TEST PASSED! Revert works correctly without duplicates.")
        else:
            print("\n❌ TEST FAILED! Unexpected transaction counts.")
            return False
        
        # Test idempotency: revert again should have no effect
        print("\n7. Testing idempotency (revert again)...")
        reverted_count_2 = exception_workflow.revert_exception_day(
            event_date=date(2026, 1, 29),
            sku="TEST001",
            event_type=EventType.WASTE,
        )
        print(f"   Reverted {reverted_count_2} exception(s) (expected: 0)")
        
        txns_final = csv_layer.read_transactions()
        print(f"   Final transaction count: {len(txns_final)} (should equal previous: {len(txns_after)})")
        
        if reverted_count_2 == 0 and len(txns_final) == len(txns_after):
            print("\n✅ IDEMPOTENCY TEST PASSED!")
        else:
            print("\n❌ IDEMPOTENCY TEST FAILED!")
            return False
        
        return True
        
    finally:
        # Cleanup
        shutil.rmtree(tmpdir)
        print(f"\nCleaned up temp directory")

if __name__ == "__main__":
    success = test_revert_fix()
    exit(0 if success else 1)
