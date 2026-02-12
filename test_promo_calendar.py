"""
Test suite for promo_calendar module.

Tests:
1. PromoWindow model validation
2. Query functions (is_promo, promo_windows_for_sku)
3. Mutation functions (add, remove, validate_no_overlap)
4. Sales data integration (apply_promo_flags)
5. Reporting functions (stats, active/upcoming promos)
"""
from datetime import date, timedelta

from src.domain.models import PromoWindow, SalesRecord
from src.persistence.csv_layer import CSVLayer
from src.promo_calendar import (
    is_promo,
    promo_windows_for_sku,
    add_promo_window,
    remove_promo_window,
    validate_no_overlap,
    apply_promo_flags_to_sales,
    enrich_sales_with_promo_calendar,
    get_promo_stats,
    get_active_promos,
    get_upcoming_promos,
)


def run_test():
    """Run comprehensive promo calendar test."""
    print("=" * 60)
    print("TEST: Promo Calendar")
    print("=" * 60)
    print()
    
    # === TEST 1: PromoWindow Model Validation ===
    print("1. PROMOWINDOW MODEL VALIDATION")
    print("-" * 60)
    
    # Valid window
    window1 = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 2, 10),
        end_date=date(2026, 2, 15),
        store_id="STORE_A",
    )
    print(f"✓ Valid window created: {window1.sku}, {window1.start_date} to {window1.end_date}")
    print(f"  Duration: {window1.duration_days()} days")
    
    # Test contains_date
    assert window1.contains_date(date(2026, 2, 10))  # Inclusive start
    assert window1.contains_date(date(2026, 2, 15))  # Inclusive end
    assert window1.contains_date(date(2026, 2, 12))  # Middle
    assert not window1.contains_date(date(2026, 2, 9))  # Before
    assert not window1.contains_date(date(2026, 2, 16))  # After
    print("✓ contains_date() works correctly (inclusive boundaries)")
    
    # Test overlap detection
    window2 = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 2, 14),
        end_date=date(2026, 2, 20),
        store_id="STORE_A",
    )
    assert window1.overlaps_with(window2)
    print(f"✓ Overlap detected between {window1.start_date}-{window1.end_date} and {window2.start_date}-{window2.end_date}")
    
    # Non-overlapping windows
    window3 = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 2, 16),
        end_date=date(2026, 2, 20),
        store_id="STORE_A",
    )
    assert not window1.overlaps_with(window3)
    print(f"✓ No overlap between {window1.start_date}-{window1.end_date} and {window3.start_date}-{window3.end_date}")
    
    # Different SKU = no overlap
    window4 = PromoWindow(
        sku="SKU002",
        start_date=date(2026, 2, 12),
        end_date=date(2026, 2, 18),
        store_id="STORE_A",
    )
    assert not window1.overlaps_with(window4)
    print("✓ Different SKU = no overlap (even if dates overlap)")
    
    # Different store = no overlap
    window5 = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 2, 12),
        end_date=date(2026, 2, 18),
        store_id="STORE_B",
    )
    assert not window1.overlaps_with(window5)
    print("✓ Different store = no overlap (even if dates overlap)")
    
    # Invalid window (start > end) - should raise error
    try:
        PromoWindow(sku="SKU001", start_date=date(2026, 2, 20), end_date=date(2026, 2, 10))
        assert False, "Should have raised ValueError for invalid dates"
    except ValueError as e:
        print(f"✓ Invalid date range rejected: {e}")
    
    print()
    
    # === TEST 2: Query Functions ===
    print("2. QUERY FUNCTIONS")
    print("-" * 60)
    
    # Create sample promo windows
    windows = [
        PromoWindow(sku="SKU001", start_date=date(2026, 2, 10), end_date=date(2026, 2, 15)),
        PromoWindow(sku="SKU001", start_date=date(2026, 3, 1), end_date=date(2026, 3, 7)),
        PromoWindow(sku="SKU002", start_date=date(2026, 2, 12), end_date=date(2026, 2, 18)),
        PromoWindow(sku="SKU001", start_date=date(2026, 2, 20), end_date=date(2026, 2, 25), store_id="STORE_B"),
    ]
    
    # Test is_promo()
    assert is_promo(date(2026, 2, 12), "SKU001", windows) is True
    assert is_promo(date(2026, 2, 19), "SKU001", windows) is False
    assert is_promo(date(2026, 3, 5), "SKU001", windows) is True
    assert is_promo(date(2026, 2, 15), "SKU002", windows) is True
    print("✓ is_promo() correctly identifies promo days")
    
    # Test with store filter
    assert is_promo(date(2026, 2, 22), "SKU001", windows, store_id="STORE_B") is True
    assert is_promo(date(2026, 2, 22), "SKU001", windows, store_id="STORE_A") is False
    print("✓ is_promo() respects store_id filter")
    
    # Test promo_windows_for_sku()
    sku001_windows = promo_windows_for_sku("SKU001", windows)
    assert len(sku001_windows) == 3
    assert sku001_windows[0].start_date == date(2026, 2, 10)  # Sorted by start_date
    print(f"✓ promo_windows_for_sku() found {len(sku001_windows)} windows for SKU001")
    
    # When filtering by store_id, include:
    # 1. Windows with matching store_id
    # 2. Global windows (store_id=None)
    # Expected for STORE_A: 2 global windows (None) + 0 specific (STORE_A) = 2
    sku001_store_a = promo_windows_for_sku("SKU001", windows, store_id="STORE_A")
    assert len(sku001_store_a) == 2  # 2 global windows (excludes STORE_B specific)
    print(f"✓ promo_windows_for_sku() with store filter: {len(sku001_store_a)} windows (global + specific)")
    
    # For STORE_B, should include 2 global + 1 specific = 3
    sku001_store_b = promo_windows_for_sku("SKU001", windows, store_id="STORE_B")
    assert len(sku001_store_b) == 3
    print(f"✓ promo_windows_for_sku() for STORE_B: {len(sku001_store_b)} windows (includes STORE_B specific)")
    
    print()
    
    # === TEST 3: Mutation Functions (CSV Integration) ===
    print("3. MUTATION FUNCTIONS (CSV INTEGRATION)")
    print("-" * 60)
    
    # Use temp directory for testing
    import tempfile
    import shutil
    from pathlib import Path
    
    temp_dir = Path(tempfile.mkdtemp())
    csv_layer = CSVLayer(data_dir=temp_dir)
    
    # Add promo window (no overlap check)
    new_window = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 5),
    )
    success = add_promo_window(csv_layer, new_window, allow_overlap=True)
    assert success is True
    print(f"✓ Added promo window: {new_window.sku}, {new_window.start_date} to {new_window.end_date}")
    
    # Read back
    saved_windows = csv_layer.read_promo_calendar()
    assert len(saved_windows) == 1
    assert saved_windows[0].sku == "SKU001"
    print(f"✓ Successfully read back {len(saved_windows)} window(s) from CSV")
    
    # Add overlapping window (should fail)
    overlap_window = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 4, 3),
        end_date=date(2026, 4, 7),
    )
    success = add_promo_window(csv_layer, overlap_window, allow_overlap=False)
    assert success is False
    print("✓ Overlap validation prevented adding overlapping window")
    
    # Add non-overlapping window (should succeed)
    non_overlap_window = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 4, 10),
        end_date=date(2026, 4, 15),
    )
    success = add_promo_window(csv_layer, non_overlap_window, allow_overlap=False)
    assert success is True
    print("✓ Non-overlapping window added successfully")
    
    # Validate no overlap
    all_windows = csv_layer.read_promo_calendar()
    overlaps = validate_no_overlap(all_windows)
    assert len(overlaps) == 0
    print(f"✓ validate_no_overlap() found {len(overlaps)} overlaps (expected 0)")
    
    # Remove window
    removed = remove_promo_window(
        csv_layer,
        sku="SKU001",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 5),
    )
    assert removed is True
    remaining = csv_layer.read_promo_calendar()
    assert len(remaining) == 1
    print(f"✓ Removed window, {len(remaining)} remaining")
    
    # Cleanup temp dir
    shutil.rmtree(temp_dir)
    
    print()
    
    # === TEST 4: Sales Data Integration ===
    print("4. SALES DATA INTEGRATION")
    print("-" * 60)
    
    # Create sample sales
    sales = [
        SalesRecord(date=date(2026, 2, 10), sku="SKU001", qty_sold=10, promo_flag=0),
        SalesRecord(date=date(2026, 2, 12), sku="SKU001", qty_sold=15, promo_flag=0),
        SalesRecord(date=date(2026, 2, 16), sku="SKU001", qty_sold=8, promo_flag=0),
        SalesRecord(date=date(2026, 2, 14), sku="SKU002", qty_sold=20, promo_flag=0),
    ]
    
    # Apply promo flags
    updated_sales = apply_promo_flags_to_sales(sales, windows)
    
    # Check results
    assert updated_sales[0].promo_flag == 1  # 2026-02-10, SKU001 -> In promo
    assert updated_sales[1].promo_flag == 1  # 2026-02-12, SKU001 -> In promo
    assert updated_sales[2].promo_flag == 0  # 2026-02-16, SKU001 -> Not in promo
    assert updated_sales[3].promo_flag == 1  # 2026-02-14, SKU002 -> In promo
    
    promo_count = sum(s.promo_flag for s in updated_sales)
    print(f"✓ Applied promo flags: {promo_count}/{len(updated_sales)} sales marked as promo")
    
    print()
    
    # === TEST 5: Reporting Functions ===
    print("5. REPORTING FUNCTIONS")
    print("-" * 60)
    
    # Get stats
    stats = get_promo_stats(windows)
    print(f"Total windows: {stats['total_windows']}")
    print(f"Total promo days: {stats['total_promo_days']}")
    print(f"Avg window duration: {stats['avg_window_duration']:.1f} days")
    print(f"SKU count: {stats['sku_count']}")
    
    assert stats['total_windows'] == 4
    assert stats['sku_count'] == 2
    print("✓ get_promo_stats() calculated correctly")
    
    # Get active promos
    active = get_active_promos(windows, date(2026, 2, 14))
    assert len(active) == 2  # SKU001 and SKU002 both have promos on 2026-02-14
    print(f"✓ get_active_promos() found {len(active)} active promos on 2026-02-14")
    
    # Get upcoming promos
    upcoming = get_upcoming_promos(windows, date(2026, 2, 1), days_ahead=20)
    assert len(upcoming) >= 1  # At least SKU001 promo starting 2026-02-10
    print(f"✓ get_upcoming_promos() found {len(upcoming)} upcoming promos in next 20 days")
    
    print()
    
    # === TEST 6: Full Enrich Workflow ===
    print("6. FULL ENRICH WORKFLOW")
    print("-" * 60)
    
    temp_dir = Path(tempfile.mkdtemp())
    csv_layer = CSVLayer(data_dir=temp_dir)
    
    # Write sample sales
    sample_sales = [
        SalesRecord(date=date(2026, 2, 10), sku="SKU001", qty_sold=10, promo_flag=0),
        SalesRecord(date=date(2026, 2, 12), sku="SKU001", qty_sold=15, promo_flag=0),
        SalesRecord(date=date(2026, 2, 16), sku="SKU001", qty_sold=8, promo_flag=0),
    ]
    csv_layer.write_sales(sample_sales)
    
    # Write promo calendar
    promo_windows = [
        PromoWindow(sku="SKU001", start_date=date(2026, 2, 10), end_date=date(2026, 2, 15)),
    ]
    csv_layer.write_promo_calendar(promo_windows)
    
    # Enrich sales with promo calendar
    enrich_sales_with_promo_calendar(csv_layer)
    
    # Read back enriched sales
    enriched_sales = csv_layer.read_sales()
    assert enriched_sales[0].promo_flag == 1  # 2026-02-10 -> In promo
    assert enriched_sales[1].promo_flag == 1  # 2026-02-12 -> In promo
    assert enriched_sales[2].promo_flag == 0  # 2026-02-16 -> Not in promo
    
    print(f"✓ Enriched {len(enriched_sales)} sales records with promo calendar")
    print(f"  Promo days: {sum(s.promo_flag for s in enriched_sales)}/{len(enriched_sales)}")
    
    # Cleanup
    shutil.rmtree(temp_dir)
    
    print()
    
    # === SUMMARY ===
    print("=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    print("All validation checks passed:")
    print("  ✓ PromoWindow model with validation")
    print("  ✓ Query functions (is_promo, promo_windows_for_sku)")
    print("  ✓ Mutation functions (add, remove, validate_no_overlap)")
    print("  ✓ Sales data integration (apply_promo_flags)")
    print("  ✓ Reporting functions (stats, active/upcoming)")
    print("  ✓ Full enrich workflow (CSV round-trip)")


if __name__ == "__main__":
    run_test()
