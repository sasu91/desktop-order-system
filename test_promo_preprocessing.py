"""
Test per promo preprocessing module.

Verifica:
1. Creazione dataset con separazione promo/non-promo
2. Censoring di giorni OOS/assortment gaps
3. Logging chiaro di esclusioni
4. Stima uplift promo semplice
"""
import sys
from pathlib import Path
from datetime import date, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.domain.models import SalesRecord, Transaction, EventType
from src.promo_preprocessing import (
    prepare_promo_training_data,
    estimate_promo_uplift_simple,
    get_promo_summary_stats,
)

print("=" * 60)
print("TEST: Promo Preprocessing")
print("=" * 60)

# Setup test data
today = date.today()
sku = "TEST_SKU"

# Sales history: 60 days
sales = []
for i in range(60):
    day = today - timedelta(days=60 - i)
    
    # Simulate promo pattern: every 10th day is promo
    is_promo = (i % 10 == 0)
    promo_flag = 1 if is_promo else 0
    
    # Promo days have higher sales (uplift ~50%)
    base_sales = 20
    qty = int(base_sales * 1.5) if is_promo else base_sales
    
    # Simulate some stock-outs (days 15-17: OH=0, sales=0)
    if 15 <= i <= 17:
        qty = 0  # Stock-out period
    
    sales.append(SalesRecord(
        date=day,
        sku=sku,
        qty_sold=qty,
        promo_flag=promo_flag,
    ))

# Transactions: simulate stock-outs
transactions = [
    # Initial stock
    Transaction(
        date=today - timedelta(days=70),
        sku=sku,
        event=EventType.SNAPSHOT,
        qty=500,
    ),
    # Sales will deplete stock around day 15
    # Add WASTE event to simulate explicit OOS
    Transaction(
        date=today - timedelta(days=45),
        sku=sku,
        event=EventType.ADJUST,
        qty=0,  # Force OH=0
        note="Simulated stock-out",
    ),
    # Receipt to restore stock
    Transaction(
        date=today - timedelta(days=42),
        sku=sku,
        event=EventType.SNAPSHOT,
        qty=500,
        note="Restocked after OOS",
    ),
]

print("\n1. PROMO SUMMARY STATS (raw data, no censoring)")
print("-" * 60)
stats = get_promo_summary_stats(sku, sales, lookback_days=60, asof_date=today)
print(f"Total days: {stats['total_days']}")
print(f"Promo days: {stats['promo_days']} ({stats['promo_frequency']:.1f}%)")
print(f"Non-promo days: {stats['non_promo_days']}")
print(f"Avg promo sales: {stats['avg_promo_sales']:.1f} units/day")
print(f"Avg non-promo sales: {stats['avg_non_promo_sales']:.1f} units/day")

print("\n2. PREPARE TRAINING DATA (with censoring)")
print("-" * 60)
dataset = prepare_promo_training_data(
    sku=sku,
    sales_records=sales,
    transactions=transactions,
    lookback_days=60,
    asof_date=today,
)

print(f"SKU: {dataset.sku}")
print(f"Total days available: {dataset.total_days_available}")
print(f"Promo observations (valid): {len(dataset.promo_observations)}")
print(f"Non-promo observations (valid): {len(dataset.non_promo_observations)}")
print(f"Censored days: {dataset.censored_days_count} ({dataset.censored_days_count/dataset.total_days_available*100:.1f}%)")
print(f"Censored reasons: {dataset.censored_reasons}")

print("\n3. ESTIMATE PROMO UPLIFT")
print("-" * 60)
uplift = estimate_promo_uplift_simple(dataset, min_promo_days=3, min_non_promo_days=10)

if uplift:
    print(f"✓ Uplift estimation successful")
    print(f"  Avg promo sales: {uplift['avg_promo_sales']:.1f} units/day")
    print(f"  Avg non-promo sales: {uplift['avg_non_promo_sales']:.1f} units/day")
    print(f"  Uplift: +{uplift['uplift_percent']:.1f}%")
    print(f"  Sample size: {uplift['n_promo_days']} promo days, {uplift['n_non_promo_days']} non-promo days")
else:
    print("✗ Insufficient data for uplift estimation")

print("\n4. VALIDATION CHECKS")
print("-" * 60)

# Check 1: Censored days should include stock-out period
expected_censored = 3  # Days 15-17
actual_censored = dataset.censored_days_count
print(f"Check 1: Expected ~{expected_censored} censored days, got {actual_censored}")
if actual_censored >= expected_censored:
    print("  ✓ PASS: Stock-out days correctly censored")
else:
    print(f"  ✗ FAIL: Expected at least {expected_censored} censored days")

# Check 2: Promo observations should be ~6 (60 days / 10)
expected_promo = 6
actual_promo = len(dataset.promo_observations)
print(f"\nCheck 2: Expected ~{expected_promo} promo days (excluding censored), got {actual_promo}")
if abs(actual_promo - expected_promo) <= 2:
    print("  ✓ PASS: Promo days count reasonable")
else:
    print(f"  ✗ FAIL: Expected {expected_promo} ± 2 promo days")

# Check 3: Non-promo observations should be majority
actual_non_promo = len(dataset.non_promo_observations)
print(f"\nCheck 3: Non-promo days: {actual_non_promo}")
if actual_non_promo > actual_promo:
    print("  ✓ PASS: More non-promo than promo days (expected)")
else:
    print("  ✗ FAIL: Non-promo should be > promo")

# Check 4: Uplift should be positive (promo boosts sales)
if uplift and uplift['uplift_percent'] > 0:
    print(f"\nCheck 4: Uplift is positive ({uplift['uplift_percent']:.1f}%)")
    print("  ✓ PASS: Promo increases sales")
else:
    print("\nCheck 4: Uplift calculation")
    print("  ✗ FAIL: Expected positive uplift")

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
