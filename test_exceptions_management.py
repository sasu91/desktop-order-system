#!/usr/bin/env python3
"""
Quick manual test script for Exception management functionality.
Run this to verify exception record/revert operations work correctly.
"""
from pathlib import Path
import tempfile
import shutil

from src.persistence.csv_layer import CSVLayer
from src.domain.models import SKU, EventType
from src.workflows.receiving import ExceptionWorkflow
from datetime import date


def main():
    """Test exception management features."""
    # Create temp directory
    tmpdir = Path(tempfile.mkdtemp())
    print(f"Testing in temporary directory: {tmpdir}")
    
    try:
        csv_layer = CSVLayer(data_dir=tmpdir)
        exception_workflow = ExceptionWorkflow(csv_layer)
        
        # Setup: Create some SKUs
        csv_layer.write_sku(SKU(sku="SKU001", description="Caffè Arabica"))
        csv_layer.write_sku(SKU(sku="SKU002", description="Latte Intero"))
        csv_layer.write_sku(SKU(sku="SKU003", description="Yogurt Bianco"))
        print(f"✓ Created 3 SKUs")
        
        # Test 1: Record WASTE exception
        print("\n=== Test 1: Record WASTE exception ===")
        txn1, already1 = exception_workflow.record_exception(
            event_type=EventType.WASTE,
            sku="SKU001",
            qty=10,
            event_date=date(2026, 1, 28),
            notes="Damaged goods",
        )
        print(f"✓ Recorded WASTE for SKU001: already_recorded={already1}")
        assert already1 is False, "First record should not be already recorded"
        
        # Test 2: Idempotency - record same exception again
        print("\n=== Test 2: Idempotency test ===")
        txn2, already2 = exception_workflow.record_exception(
            event_type=EventType.WASTE,
            sku="SKU001",
            qty=10,
            event_date=date(2026, 1, 28),
            notes="Different notes",
        )
        print(f"✓ Tried to record same exception again: already_recorded={already2}")
        assert already2 is True, "Second record should be already recorded"
        
        # Verify only 1 entry in ledger
        all_txns = csv_layer.read_transactions()
        waste_txns = [t for t in all_txns if t.event == EventType.WASTE]
        print(f"✓ WASTE transactions in ledger: {len(waste_txns)}")
        assert len(waste_txns) == 1, "Should have only 1 WASTE transaction"
        
        # Test 3: Record ADJUST exception (signed quantity)
        print("\n=== Test 3: Record ADJUST exception (negative) ===")
        txn3, already3 = exception_workflow.record_exception(
            event_type=EventType.ADJUST,
            sku="SKU002",
            qty=-5,
            event_date=date(2026, 1, 28),
            notes="Count mismatch",
        )
        print(f"✓ Recorded ADJUST for SKU002: qty={txn3.qty}, already_recorded={already3}")
        assert txn3.qty == -5, "ADJUST qty should be signed"
        
        # Test 4: Record UNFULFILLED exception
        print("\n=== Test 4: Record UNFULFILLED exception ===")
        txn4, already4 = exception_workflow.record_exception(
            event_type=EventType.UNFULFILLED,
            sku="SKU003",
            qty=3,
            event_date=date(2026, 1, 28),
            notes="Out of stock",
        )
        print(f"✓ Recorded UNFULFILLED for SKU003: already_recorded={already4}")
        
        # Test 5: Record multiple exceptions same SKU, different types
        print("\n=== Test 5: Multiple exception types for same SKU ===")
        exception_workflow.record_exception(
            EventType.WASTE, "SKU001", 5, date(2026, 1, 28), "Expired batch"
        )
        exception_workflow.record_exception(
            EventType.ADJUST, "SKU001", 2, date(2026, 1, 28), "Correction"
        )
        
        all_txns = csv_layer.read_transactions()
        sku001_exceptions = [
            t for t in all_txns 
            if t.sku == "SKU001" and t.event in [EventType.WASTE, EventType.ADJUST, EventType.UNFULFILLED]
        ]
        print(f"✓ SKU001 exceptions (should be 2, not 3 due to idempotency): {len(sku001_exceptions)}")
        # First WASTE already recorded, so we have: original WASTE + ADJUST = 2
        
        # Test 6: Filter exceptions by date
        print("\n=== Test 6: Filter exceptions by date ===")
        all_txns = csv_layer.read_transactions()
        today_exceptions = [
            t for t in all_txns
            if t.event in [EventType.WASTE, EventType.ADJUST, EventType.UNFULFILLED]
            and t.date == date(2026, 1, 28)
        ]
        print(f"✓ Exceptions on 2026-01-28: {len(today_exceptions)}")
        for exc in today_exceptions:
            print(f"  - {exc.event.value}: {exc.sku}, qty={exc.qty}")
        
        # Test 7: Revert specific exception type for SKU
        print("\n=== Test 7: Revert WASTE for SKU001 ===")
        reverted_count = exception_workflow.revert_exception_day(
            event_date=date(2026, 1, 28),
            sku="SKU001",
            event_type=EventType.WASTE,
        )
        print(f"✓ Reverted {reverted_count} WASTE exception(s) for SKU001")
        
        # Verify WASTE removed but ADJUST remains
        all_txns = csv_layer.read_transactions()
        sku001_waste = [
            t for t in all_txns
            if t.sku == "SKU001" and t.event == EventType.WASTE
        ]
        sku001_adjust = [
            t for t in all_txns
            if t.sku == "SKU001" and t.event == EventType.ADJUST
        ]
        print(f"✓ SKU001 WASTE transactions after revert: {len(sku001_waste)} (should be 0)")
        print(f"✓ SKU001 ADJUST transactions after revert: {len(sku001_adjust)} (should be 1)")
        assert len(sku001_waste) == 0, "All WASTE should be reverted"
        assert len(sku001_adjust) == 1, "ADJUST should remain"
        
        # Test 8: Revert returns 0 if no matching exceptions
        print("\n=== Test 8: Revert non-existent exception ===")
        reverted_count = exception_workflow.revert_exception_day(
            event_date=date(2026, 1, 28),
            sku="SKU999",  # Non-existent SKU
            event_type=EventType.WASTE,
        )
        print(f"✓ Attempted revert for non-existent SKU: {reverted_count} reverted (should be 0)")
        assert reverted_count == 0, "Should return 0 for non-existent"
        
        # Test 9: Verify notes format
        print("\n=== Test 9: Verify notes format ===")
        all_txns = csv_layer.read_transactions()
        adjust_txn = next(t for t in all_txns if t.event == EventType.ADJUST and t.sku == "SKU002")
        print(f"✓ ADJUST transaction note: {adjust_txn.note}")
        assert "2026-01-28_SKU002_ADJUST" in adjust_txn.note, "Note should contain exception key"
        assert "Count mismatch" in adjust_txn.note, "Note should contain user notes"
        
        print("\n" + "=" * 60)
        print("✅ All exception management tests passed successfully!")
        print("=" * 60)
        
        # Summary
        print("\n📊 Summary:")
        all_txns = csv_layer.read_transactions()
        exception_txns = [
            t for t in all_txns
            if t.event in [EventType.WASTE, EventType.ADJUST, EventType.UNFULFILLED]
        ]
        print(f"  Total exceptions recorded: {len(exception_txns)}")
        for event_type in [EventType.WASTE, EventType.ADJUST, EventType.UNFULFILLED]:
            count = len([t for t in exception_txns if t.event == event_type])
            print(f"    - {event_type.value}: {count}")
        
    finally:
        # Cleanup
        shutil.rmtree(tmpdir)
        print(f"\n✓ Cleaned up temporary directory")


if __name__ == "__main__":
    main()
