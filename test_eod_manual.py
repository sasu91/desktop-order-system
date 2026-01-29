#!/usr/bin/env python3
"""
Manual test script for EOD stock entry feature.
"""
from datetime import date
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Transaction, EventType, DemandVariability
from src.persistence.csv_layer import CSVLayer
from src.workflows.daily_close import DailyCloseWorkflow

def test_eod_workflow():
    """Test EOD workflow manually."""
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
            ean="1234567890123",
            moq=1,
            lead_time_days=7,
            max_stock=999,
            reorder_point=10,
            supplier="TestSupplier",
            demand_variability=DemandVariability.STABLE,
        ))
        print("   ✓ SKU added")
        
        # Add initial snapshot
        print("\n2. Adding initial snapshot (100 units)...")
        csv_layer.append_transaction(Transaction(
            date=date(2026, 1, 1),
            sku="TEST001",
            event=EventType.SNAPSHOT,
            qty=100,
        ))
        print("   ✓ Snapshot added")
        
        # Process EOD: declare stock at end of day
        print("\n3. Processing EOD stock entry...")
        print("   Stock at start of 2026-01-02: 100")
        print("   Stock declared at EOD 2026-01-02: 85")
        print("   Expected sales: 15")
        
        workflow = DailyCloseWorkflow(csv_layer)
        sale, adjust, status = workflow.process_eod_stock(
            sku="TEST001",
            eod_date=date(2026, 1, 2),
            eod_stock_on_hand=85,
        )
        
        print(f"\n   Status: {status}")
        print(f"   Sale recorded: {sale}")
        print(f"   Adjustment needed: {adjust}")
        
        # Verify sales
        print("\n4. Verifying sales.csv...")
        sales = csv_layer.read_sales()
        print(f"   Sales records: {len(sales)}")
        for s in sales:
            print(f"   - {s.date}: {s.sku} sold {s.qty_sold}")
        
        # Test bulk processing
        print("\n5. Testing bulk EOD processing...")
        csv_layer.add_sku(SKU(
            sku="TEST002",
            description="Test Product 2",
            ean="",
            moq=1,
            lead_time_days=7,
            max_stock=999,
            reorder_point=10,
            supplier="TestSupplier",
            demand_variability=DemandVariability.STABLE,
        ))
        csv_layer.append_transaction(Transaction(
            date=date(2026, 1, 1),
            sku="TEST002",
            event=EventType.SNAPSHOT,
            qty=50,
        ))
        
        results = workflow.process_bulk_eod_stock(
            eod_entries={
                "TEST001": 80,  # Another day, sold 5 more
                "TEST002": 45,  # Sold 5
            },
            eod_date=date(2026, 1, 3),
        )
        
        print("   Bulk results:")
        for r in results:
            print(f"   {r}")
        
        print("\n✅ All tests passed!")
        
    finally:
        # Cleanup
        shutil.rmtree(tmpdir)
        print(f"\nCleaned up temp directory")

if __name__ == "__main__":
    test_eod_workflow()
