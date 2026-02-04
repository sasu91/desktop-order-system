"""
Test per-SKU OOS boost configuration.

Verifica che il boost OOS sia configurabile per singolo SKU e prenda precedenza
sul setting globale.
"""

from datetime import date
from src.domain.models import SKU, Stock, DemandVariability
from src.workflows.order import OrderWorkflow
from src.persistence.csv_layer import CSVLayer
import os
import tempfile
import shutil


def test_oos_boost_per_sku():
    """Test that per-SKU boost overrides global setting."""
    
    # Create temporary directory for test data
    test_dir = tempfile.mkdtemp()
    
    try:
        # Initialize CSV layer (expects Path object)
        from pathlib import Path
        csv_layer = CSVLayer(Path(test_dir))
        
        # Create SKU with specific boost (25%)
        sku_with_boost = SKU(
            sku="TEST001",
            description="Test Product with Custom Boost",
            ean="",
            moq=10,
            pack_size=6,
            lead_time_days=7,
            review_period=7,
            safety_stock=20,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=50,
            supplier="Test Supplier",
            demand_variability=DemandVariability.STABLE,
            oos_boost_percent=25.0  # SKU-specific 25% boost
        )
        csv_layer.write_sku(sku_with_boost)
        
        # Create SKU without boost (uses global)
        sku_no_boost = SKU(
            sku="TEST002",
            description="Test Product using Global Boost",
            ean="",
            moq=10,
            pack_size=6,
            lead_time_days=7,
            review_period=7,
            safety_stock=20,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=50,
            supplier="Test Supplier",
            demand_variability=DemandVariability.STABLE,
            oos_boost_percent=0.0  # 0 = use global setting
        )
        csv_layer.write_sku(sku_no_boost)
        
        # Initialize order workflow
        workflow = OrderWorkflow(csv_layer, lead_time_days=7)
        
        # Current stock state (low stock scenario)
        current_stock_1 = Stock(sku="TEST001", on_hand=10, on_order=0)
        current_stock_2 = Stock(sku="TEST002", on_hand=10, on_order=0)
        
        # Daily sales average
        daily_sales = 5.0
        
        # Global OOS boost: 10%
        global_boost = 0.10
        
        # Simulate 3 OOS days
        oos_days = 3
        
        # TEST 1: SKU with custom boost (25%)
        proposal_custom = workflow.generate_proposal(
            sku="TEST001",
            description="Test Product with Custom Boost",
            current_stock=current_stock_1,
            daily_sales_avg=daily_sales,
            sku_obj=sku_with_boost,
            oos_days_count=oos_days,
            oos_boost_percent=global_boost  # Global setting (will be overridden)
        )
        
        # TEST 2: SKU using global boost (10%)
        proposal_global = workflow.generate_proposal(
            sku="TEST002",
            description="Test Product using Global Boost",
            current_stock=current_stock_2,
            daily_sales_avg=daily_sales,
            sku_obj=sku_no_boost,
            oos_days_count=oos_days,
            oos_boost_percent=global_boost  # Global setting (will be used)
        )
        
        # TEST 3: No OOS days (boost should not apply)
        proposal_no_oos = workflow.generate_proposal(
            sku="TEST001",
            description="Test Product with Custom Boost",
            current_stock=current_stock_1,
            daily_sales_avg=daily_sales,
            sku_obj=sku_with_boost,
            oos_days_count=0,  # No OOS days
            oos_boost_percent=global_boost
        )
        
        print("=== Test per-SKU OOS Boost ===\n")
        
        print(f"Setup:")
        print(f"  Current Stock: {current_stock_1.on_hand} on_hand, {current_stock_1.on_order} on_order")
        print(f"  Daily Sales: {daily_sales}")
        print(f"  OOS Days: {oos_days}")
        print(f"  Global Boost: {global_boost * 100}%")
        print(f"  SKU1 Custom Boost: {sku_with_boost.oos_boost_percent}%")
        print(f"  SKU2 Custom Boost: {sku_no_boost.oos_boost_percent}% (uses global)\n")
        
        print(f"TEST 1: SKU with custom 25% boost")
        print(f"  Proposed Qty: {proposal_custom.proposed_qty}")
        print(f"  Expected: Higher qty due to 25% boost\n")
        
        print(f"TEST 2: SKU using global 10% boost")
        print(f"  Proposed Qty: {proposal_global.proposed_qty}")
        print(f"  Expected: Lower qty than TEST 1 (only 10% boost)\n")
        
        print(f"TEST 3: No OOS days (no boost applied)")
        print(f"  Proposed Qty: {proposal_no_oos.proposed_qty}")
        print(f"  Expected: Base qty without any boost\n")
        
        # Verification
        assert proposal_custom.proposed_qty > proposal_global.proposed_qty, \
            "SKU with 25% boost should propose more than SKU with 10% boost"
        
        assert proposal_no_oos.proposed_qty < proposal_custom.proposed_qty, \
            "Proposal without OOS days should be less than with OOS boost"
        
        print("✓ All assertions passed!")
        print("✓ Per-SKU boost correctly overrides global setting")
        
        # Test CSV persistence
        print("\n=== Test CSV Persistence ===")
        reloaded_skus = csv_layer.read_skus()
        sku1_reloaded = next(s for s in reloaded_skus if s.sku == "TEST001")
        sku2_reloaded = next(s for s in reloaded_skus if s.sku == "TEST002")
        
        print(f"TEST001 boost from CSV: {sku1_reloaded.oos_boost_percent}%")
        print(f"TEST002 boost from CSV: {sku2_reloaded.oos_boost_percent}%")
        
        assert sku1_reloaded.oos_boost_percent == 25.0, "TEST001 boost should be 25%"
        assert sku2_reloaded.oos_boost_percent == 0.0, "TEST002 boost should be 0%"
        
        print("✓ OOS boost persisted correctly in CSV")
        
    finally:
        # Cleanup
        shutil.rmtree(test_dir)
        print("\n✓ Test completed successfully")


if __name__ == "__main__":
    test_oos_boost_per_sku()
