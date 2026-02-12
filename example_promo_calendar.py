"""
Example usage of Promo Calendar feature.

This script demonstrates how to:
1. Add promo windows to calendar
2. Check if dates are promo days
3. Enrich sales data with promo flags
4. Generate reports
"""
from datetime import date, timedelta
from src.persistence.csv_layer import CSVLayer
from src.domain.models import PromoWindow, SalesRecord
from src.promo_calendar import (
    add_promo_window,
    is_promo,
    promo_windows_for_sku,
    enrich_sales_with_promo_calendar,
    get_promo_stats,
    get_active_promos,
    get_upcoming_promos,
    validate_no_overlap,
)


def main():
    print("=" * 60)
    print("PROMO CALENDAR EXAMPLE")
    print("=" * 60)
    print()
    
    # Initialize CSV layer (auto-creates promo_calendar.csv)
    csv_layer = CSVLayer()
    
    # === STEP 1: Add Promo Windows ===
    print("1. ADDING PROMO WINDOWS")
    print("-" * 60)
    
    # Promo 1: SKU001 Valentine's Week (Global)
    valentines_promo = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 2, 10),
        end_date=date(2026, 2, 16),
        store_id=None,  # Global (all stores)
    )
    
    success = add_promo_window(csv_layer, valentines_promo, allow_overlap=False)
    if success:
        print(f"✓ Added Valentine's promo: SKU001, {valentines_promo.duration_days()} days (Global)")
    
    # Promo 2: SKU002 Spring Sale (Global)
    spring_promo = PromoWindow(
        sku="SKU002",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 7),
        store_id=None,
    )
    
    success = add_promo_window(csv_layer, spring_promo, allow_overlap=False)
    if success:
        print(f"✓ Added Spring promo: SKU002, {spring_promo.duration_days()} days (Global)")
    
    # Promo 3: SKU001 Store-Specific Promo (STORE_B only)
    store_b_promo = PromoWindow(
        sku="SKU001",
        start_date=date(2026, 3, 10),
        end_date=date(2026, 3, 15),
        store_id="STORE_B",
    )
    
    success = add_promo_window(csv_layer, store_b_promo, allow_overlap=False)
    if success:
        print(f"✓ Added Store-B promo: SKU001, {store_b_promo.duration_days()} days (STORE_B only)")
    
    print()
    
    # === STEP 2: Validate No Overlaps ===
    print("2. VALIDATING NO OVERLAPS")
    print("-" * 60)
    
    windows = csv_layer.read_promo_calendar()
    overlaps = validate_no_overlap(windows)
    
    if overlaps:
        print(f"⚠️ Found {len(overlaps)} overlap(s):")
        for w1, w2 in overlaps:
            print(f"  {w1.sku}: {w1.start_date}-{w1.end_date} vs {w2.start_date}-{w2.end_date}")
    else:
        print(f"✓ No overlaps detected ({len(windows)} windows)")
    
    print()
    
    # === STEP 3: Query Promo Status ===
    print("3. QUERYING PROMO STATUS")
    print("-" * 60)
    
    # Check if specific dates are promo days
    check_dates = [
        date(2026, 2, 12),  # Valentine's promo (SKU001)
        date(2026, 2, 20),  # Not in promo
        date(2026, 3, 5),   # Spring promo (SKU002)
        date(2026, 3, 12),  # Store-B promo (SKU001)
    ]
    
    for check_date in check_dates:
        sku001_promo = is_promo(check_date, "SKU001", windows)
        sku002_promo = is_promo(check_date, "SKU002", windows)
        
        print(f"{check_date.isoformat()}: SKU001={'✓' if sku001_promo else '✗'}, SKU002={'✓' if sku002_promo else '✗'}")
    
    print()
    
    # === STEP 4: Get Promo Windows for Specific SKU ===
    print("4. PROMO WINDOWS FOR SKU (STORE FILTERS)")
    print("-" * 60)
    
    # All windows for SKU001
    sku001_all = promo_windows_for_sku("SKU001", windows)
    print(f"SKU001 (all stores): {len(sku001_all)} windows")
    for w in sku001_all:
        store_label = w.store_id or "All stores"
        print(f"  {w.start_date} to {w.end_date} ({store_label})")
    
    # Windows for SKU001 in STORE_A (includes global + STORE_A specific)
    sku001_store_a = promo_windows_for_sku("SKU001", windows, store_id="STORE_A")
    print(f"\nSKU001 (STORE_A): {len(sku001_store_a)} windows (global only)")
    
    # Windows for SKU001 in STORE_B (includes global + STORE_B specific)
    sku001_store_b = promo_windows_for_sku("SKU001", windows, store_id="STORE_B")
    print(f"SKU001 (STORE_B): {len(sku001_store_b)} windows (global + STORE_B specific)")
    
    print()
    
    # === STEP 5: Enrich Sales Data ===
    print("5. ENRICHING SALES DATA WITH PROMO FLAGS")
    print("-" * 60)
    
    # Create sample sales if not exist
    sample_sales = [
        SalesRecord(date=date(2026, 2, 10), sku="SKU001", qty_sold=10, promo_flag=0),
        SalesRecord(date=date(2026, 2, 12), sku="SKU001", qty_sold=15, promo_flag=0),
        SalesRecord(date=date(2026, 2, 20), sku="SKU001", qty_sold=8, promo_flag=0),
        SalesRecord(date=date(2026, 3, 5), sku="SKU002", qty_sold=20, promo_flag=0),
    ]
    
    print(f"Sample sales created: {len(sample_sales)} records")
    
    # Apply promo flags
    from src.promo_calendar import apply_promo_flags_to_sales
    enriched_sales = apply_promo_flags_to_sales(sample_sales, windows)
    
    promo_count = sum(s.promo_flag for s in enriched_sales)
    print(f"✓ Enriched sales: {promo_count}/{len(enriched_sales)} marked as promo")
    
    for s in enriched_sales:
        promo_label = "PROMO" if s.promo_flag else "Regular"
        print(f"  {s.date}: {s.sku}, qty={s.qty_sold} [{promo_label}]")
    
    print()
    
    # === STEP 6: Reporting ===
    print("6. REPORTING & ANALYTICS")
    print("-" * 60)
    
    # Overall stats
    stats = get_promo_stats(windows)
    print(f"Total promo windows: {stats['total_windows']}")
    print(f"Total promo days: {stats['total_promo_days']}")
    print(f"Avg window duration: {stats['avg_window_duration']:.1f} days")
    print(f"SKUs with promos: {stats['sku_count']}")
    
    print()
    
    # Active promos today
    today = date.today()
    active = get_active_promos(windows, today)
    print(f"Active promos today ({today.isoformat()}): {len(active)}")
    if active:
        for w in active:
            print(f"  {w.sku}: {w.start_date} to {w.end_date}")
    
    print()
    
    # Upcoming promos in next 30 days
    upcoming = get_upcoming_promos(windows, today, days_ahead=30)
    print(f"Upcoming promos (next 30 days): {len(upcoming)}")
    if upcoming:
        for w in upcoming:
            days_until = (w.start_date - today).days
            print(f"  {w.sku}: starts in {days_until} days ({w.start_date})")
    
    print()
    
    # === SUMMARY ===
    print("=" * 60)
    print("EXAMPLE COMPLETE")
    print("=" * 60)
    print("Promo calendar is ready to use!")
    print(f"  - {len(windows)} promo windows defined")
    print(f"  - Sales data can be enriched with promo flags")
    print(f"  - Reports available (stats, active, upcoming)")


if __name__ == "__main__":
    main()
