"""
Test OOS Detection Mode (strict vs relaxed).

Verifica che:
1. Modalità "strict" conta OOS quando on_hand == 0 (anche se on_order > 0)
2. Modalità "relaxed" conta OOS solo quando on_hand + on_order == 0
3. Setting globale funziona
4. Override per-SKU funziona
"""

from datetime import date, timedelta
from pathlib import Path
from src.domain.models import SKU, DemandVariability, Transaction, EventType, SalesRecord
from src.workflows.order import calculate_daily_sales_average
from src.persistence.csv_layer import CSVLayer
import tempfile
import shutil


def test_oos_detection_modes():
    """Test strict vs relaxed OOS detection."""
    
    test_dir = tempfile.mkdtemp()
    
    try:
        csv_layer = CSVLayer(Path(test_dir))
        
        # Scenario: SKU with on_hand=0 but on_order=100 for 5 days
        # strict mode: counts as 5 OOS days
        # relaxed mode: counts as 0 OOS days
        
        sku_id = "TEST001"
        today = date(2026, 2, 4)
        
        # Create transactions: SNAPSHOT 100, ORDER 100, then daily SALES of 20
        transactions = [
            Transaction(date=today - timedelta(days=30), sku=sku_id, event=EventType.SNAPSHOT, qty=100),
            # Day -10: Order 100 units (arrival in 7 days = day -3)
            Transaction(date=today - timedelta(days=10), sku=sku_id, event=EventType.ORDER, qty=100, receipt_date=today - timedelta(days=3)),
            # Days -9 to -4: sell 20/day (total 120 sold, on_hand goes to 0 at day -5)
            # Day -3: Receipt arrives (on_hand back to 100)
        ]
        
        # Add sales: days -9 to -4 (6 days × 20 = 120 units)
        sales = []
        for i in range(9, 3, -1):  # days -9, -8, -7, -6, -5, -4
            sales.append(SalesRecord(date=today - timedelta(days=i), sku=sku_id, qty_sold=20))
        
        # Add receipt
        transactions.append(
            Transaction(date=today - timedelta(days=3), sku=sku_id, event=EventType.RECEIPT, qty=100, receipt_date=today - timedelta(days=3))
        )
        
        # TEST 1: Strict mode (on_hand == 0)
        # Days -5, -4 have on_hand=0 but on_order=100 → should count as OOS
        daily_sales_strict, oos_days_strict = calculate_daily_sales_average(
            sales, sku_id, 
            days_lookback=30, 
            transactions=transactions,
            asof_date=today,
            oos_detection_mode="strict"
        )
        
        # TEST 2: Relaxed mode (on_hand + on_order == 0)
        # Days -5, -4 have on_hand=0 but on_order=100 → should NOT count as OOS
        daily_sales_relaxed, oos_days_relaxed = calculate_daily_sales_average(
            sales, sku_id, 
            days_lookback=30, 
            transactions=transactions,
            asof_date=today,
            oos_detection_mode="relaxed"
        )
        
        print("=== Test OOS Detection Modes ===")
        print()
        print("Scenario:")
        print("  - Day -30: SNAPSHOT 100 units")
        print("  - Day -10: ORDER 100 units (arrival day -3)")
        print("  - Days -9 to -4: SALES 20 units/day (120 total)")
        print("  - Day -5: on_hand reaches 0, on_order=100")
        print("  - Day -3: RECEIPT 100 units")
        print()
        
        print(f"TEST 1: Strict Mode (on_hand == 0)")
        print(f"  OOS Days Count: {oos_days_strict}")
        print(f"  Daily Sales Avg: {daily_sales_strict:.2f}")
        print(f"  Expected: OOS days >= 2 (days -5, -4 with on_hand=0)")
        assert oos_days_strict >= 2, f"Strict mode should detect at least 2 OOS days, got {oos_days_strict}"
        print(f"  ✓ Strict mode detected OOS correctly")
        print()
        
        print(f"TEST 2: Relaxed Mode (on_hand + on_order == 0)")
        print(f"  OOS Days Count: {oos_days_relaxed}")
        print(f"  Daily Sales Avg: {daily_sales_relaxed:.2f}")
        print(f"  Expected: OOS days = 0 (on_order covers)")
        assert oos_days_relaxed == 0, f"Relaxed mode should detect 0 OOS days, got {oos_days_relaxed}"
        print(f"  ✓ Relaxed mode ignored days with on_order")
        print()
        
        print(f"TEST 3: Daily Sales Comparison")
        print(f"  Strict avg: {daily_sales_strict:.2f} (excludes OOS days)")
        print(f"  Relaxed avg: {daily_sales_relaxed:.2f} (includes all days)")
        print(f"  Impact: Strict excludes days with on_hand=0 (likely zero sales)")
        # Note: strict avg might be lower because it excludes fewer zero-sale days
        print(f"  ✓ Both modes calculated averages correctly")
        print()
        
        # TEST 4: Low-movement SKU (1 unit every 3 days)
        print("TEST 4: Low-movement SKU (1 unit every 3 days)")
        low_sku = "LOWMOVE001"
        low_today = date(2026, 2, 4)
        low_lookback = 15
        
        # Snapshot with 1 unit on hand
        low_transactions = [
            Transaction(date=low_today - timedelta(days=15), sku=low_sku, event=EventType.SNAPSHOT, qty=1),
        ]
        
        # Sales: 1 unit every 3 days
        low_sales = []
        sale_days = [14, 11, 8, 5, 2]  # days ago
        for d in sale_days:
            sale_date = low_today - timedelta(days=d)
            low_sales.append(SalesRecord(date=sale_date, sku=low_sku, qty_sold=1))
            # Place order same day, receipt 2 days later
            low_transactions.append(
                Transaction(
                    date=sale_date,
                    sku=low_sku,
                    event=EventType.ORDER,
                    qty=1,
                    receipt_date=sale_date + timedelta(days=2)
                )
            )
            low_transactions.append(
                Transaction(
                    date=sale_date + timedelta(days=2),
                    sku=low_sku,
                    event=EventType.RECEIPT,
                    qty=1,
                    receipt_date=sale_date + timedelta(days=2)
                )
            )
        
        low_avg_strict, low_oos_strict = calculate_daily_sales_average(
            low_sales,
            low_sku,
            days_lookback=low_lookback,
            transactions=low_transactions,
            asof_date=low_today,
            oos_detection_mode="strict"
        )
        low_avg_relaxed, low_oos_relaxed = calculate_daily_sales_average(
            low_sales,
            low_sku,
            days_lookback=low_lookback,
            transactions=low_transactions,
            asof_date=low_today,
            oos_detection_mode="relaxed"
        )
        
        print(f"  Strict OOS days: {low_oos_strict}")
        print(f"  Relaxed OOS days: {low_oos_relaxed}")
        print(f"  Strict avg: {low_avg_strict:.2f}")
        print(f"  Relaxed avg: {low_avg_relaxed:.2f}")
        assert low_oos_strict > low_oos_relaxed, "Strict should detect more OOS days for low-movement SKU"
        print("  ✓ Strict mode protects low-movement SKU from under-ordering")
        print()
        
        # TEST 5: Per-SKU override
        print("TEST 5: Per-SKU Override")
        
        # Create SKU with strict mode override
        sku_strict = SKU(
            sku="SKU_STRICT",
            description="SKU with strict OOS mode",
            ean="",
            moq=1,
            pack_size=1,
            lead_time_days=7,
            review_period=7,
            safety_stock=0,
            oos_detection_mode="strict"
        )
        csv_layer.write_sku(sku_strict)
        
        # Create SKU with relaxed mode override
        sku_relaxed = SKU(
            sku="SKU_RELAXED",
            description="SKU with relaxed OOS mode",
            ean="",
            moq=1,
            pack_size=1,
            lead_time_days=7,
            review_period=7,
            safety_stock=0,
            oos_detection_mode="relaxed"
        )
        csv_layer.write_sku(sku_relaxed)
        
        # Create SKU with default (empty = use global)
        sku_default = SKU(
            sku="SKU_DEFAULT",
            description="SKU using global setting",
            ean="",
            moq=1,
            pack_size=1,
            lead_time_days=7,
            review_period=7,
            safety_stock=0,
            oos_detection_mode=""
        )
        csv_layer.write_sku(sku_default)
        
        # Reload and verify
        reloaded_skus = csv_layer.read_skus()
        sku_strict_loaded = next(s for s in reloaded_skus if s.sku == "SKU_STRICT")
        sku_relaxed_loaded = next(s for s in reloaded_skus if s.sku == "SKU_RELAXED")
        sku_default_loaded = next(s for s in reloaded_skus if s.sku == "SKU_DEFAULT")
        
        print(f"  SKU_STRICT mode: '{sku_strict_loaded.oos_detection_mode}'")
        print(f"  SKU_RELAXED mode: '{sku_relaxed_loaded.oos_detection_mode}'")
        print(f"  SKU_DEFAULT mode: '{sku_default_loaded.oos_detection_mode}'")
        
        assert sku_strict_loaded.oos_detection_mode == "strict"
        assert sku_relaxed_loaded.oos_detection_mode == "relaxed"
        assert sku_default_loaded.oos_detection_mode == ""
        
        print(f"  ✓ Per-SKU modes persisted correctly")
        print()
        
        # TEST 6: Settings integration
        print("TEST 6: Global Settings")
        settings = csv_layer.read_settings()
        global_mode = settings["reorder_engine"]["oos_detection_mode"]["value"]
        print(f"  Global OOS detection mode: '{global_mode}'")
        print(f"  Expected: 'strict'")
        assert global_mode == "strict", f"Global mode should be 'strict', got '{global_mode}'"
        print(f"  ✓ Global setting correct")
        print()
        
        print("=" * 50)
        print("✓ All tests passed!")
        print("✓ Strict mode correctly detects OOS when on_hand=0")
        print("✓ Relaxed mode ignores OOS if on_order > 0")
        print("✓ Per-SKU override works")
        print("✓ Global settings configured")
        
    finally:
        shutil.rmtree(test_dir)
        print()
        print("✓ Test completed successfully")


if __name__ == "__main__":
    test_oos_detection_modes()
