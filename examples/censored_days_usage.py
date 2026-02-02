"""
Example: Censored Days Detection and Order Computation

This script demonstrates the complete workflow for handling censored days
(OOS/inevasi) in the order management system.

Workflow:
1. Build sales history with some OOS days
2. Detect censored days using is_day_censored()
3. Compute order with censored filtering
4. Compare with/without censored handling
"""
from datetime import date, timedelta
from typing import List, Dict

from src.domain.ledger import is_day_censored
from src.domain.models import Transaction, EventType, SalesRecord
from src.replenishment_policy import compute_order, OrderConstraints
from src.domain.calendar import Lane


def build_example_history() -> tuple[List[Dict], List[Transaction], List[SalesRecord]]:
    """
    Build example sales history with OOS periods.
    
    Scenario:
    - SKU with normal demand: ~20 units/day
    - Jan 10-12: OOS (OH=0, sales=0)
    - Jan 20: UNFULFILLED event (backorder)
    """
    history = []
    transactions = [
        Transaction(date=date(2026, 1, 1), sku="WIDGET-A", event=EventType.SNAPSHOT, qty=500)
    ]
    sales = []
    
    for i in range(1, 31):  # January 2026
        d = date(2026, 1, i)
        
        # OOS period: Jan 10-12
        if 10 <= i <= 12:
            qty_sold = 0
            # Simulate stock depletion before OOS
            if i == 10:
                transactions.append(
                    Transaction(date=d, sku="WIDGET-A", event=EventType.SALE, qty=500)
                )
        # Normal demand
        else:
            qty_sold = 18 + (i % 5)  # 18-22 units/day
            sales.append(SalesRecord(date=d, sku="WIDGET-A", qty_sold=qty_sold))
        
        history.append({"date": d, "qty_sold": qty_sold})
    
    # UNFULFILLED event on Jan 20 (backorder)
    transactions.append(
        Transaction(date=date(2026, 1, 20), sku="WIDGET-A", event=EventType.UNFULFILLED, qty=10)
    )
    
    # Replenish stock after OOS
    transactions.append(
        Transaction(
            date=date(2026, 1, 13),
            sku="WIDGET-A",
            event=EventType.RECEIPT,
            qty=300,
            receipt_date=date(2026, 1, 13)
        )
    )
    
    return history, transactions, sales


def detect_censored_days(
    sku: str,
    history: List[Dict],
    transactions: List[Transaction],
    sales: List[SalesRecord]
) -> List[bool]:
    """Detect which days should be censored."""
    censored_flags = []
    
    for h in history:
        check_date = h["date"]
        is_censored, reason = is_day_censored(sku, check_date, transactions, sales)
        censored_flags.append(is_censored)
        
        if is_censored:
            print(f"  📍 {check_date}: CENSORED - {reason}")
    
    return censored_flags


def main():
    print("=" * 80)
    print("CENSORED DAYS DETECTION & ORDER COMPUTATION EXAMPLE")
    print("=" * 80)
    
    # Build example data
    print("\n📦 Building example data...")
    history, transactions, sales = build_example_history()
    sku = "WIDGET-A"
    
    print(f"  ✓ {len(history)} days of sales history")
    print(f"  ✓ {len(transactions)} ledger transactions")
    print(f"  ✓ {len(sales)} sales records")
    
    # Detect censored days
    print("\n🔍 Detecting censored days...")
    censored_flags = detect_censored_days(sku, history, transactions, sales)
    n_censored = sum(censored_flags)
    print(f"\n  ✓ Found {n_censored} censored days out of {len(history)} total")
    
    # Define order parameters
    print("\n📋 Order parameters:")
    order_date = date(2026, 2, 1)
    constraints = OrderConstraints(pack_size=10, moq=20, max_stock=500)
    on_hand = 50
    pipeline = []
    alpha = 0.95
    
    print(f"  • Order date: {order_date}")
    print(f"  • On hand: {on_hand}")
    print(f"  • Target CSL: {alpha}")
    print(f"  • Pack size: {constraints.pack_size}")
    print(f"  • MOQ: {constraints.moq}")
    
    # Compute order WITH censored handling
    print("\n🔧 Computing order WITH censored handling...")
    result_with = compute_order(
        sku=sku,
        order_date=order_date,
        lane=Lane.STANDARD,
        alpha=alpha,
        on_hand=on_hand,
        pipeline=pipeline,
        constraints=constraints,
        history=history,
        censored_flags=censored_flags,
        alpha_boost_for_censored=0.05,
    )
    
    # Compute order WITHOUT censored handling (old behavior)
    print("\n🔧 Computing order WITHOUT censored handling (baseline)...")
    result_without = compute_order(
        sku=sku,
        order_date=order_date,
        lane=Lane.STANDARD,
        alpha=alpha,
        on_hand=on_hand,
        pipeline=pipeline,
        constraints=constraints,
        history=history,
        censored_flags=None,  # No filtering
    )
    
    # Display comparison
    print("\n" + "=" * 80)
    print("RESULTS COMPARISON")
    print("=" * 80)
    
    print(f"\n{'Metric':<40} {'WITH Censored':<20} {'WITHOUT Censored':<20}")
    print("-" * 80)
    
    metrics = [
        ("Censored days", "n_censored", "n/a"),
        ("Censored reasons (sample)", "censored_reasons", "n/a"),
        ("Alpha (original)", "alpha", "alpha"),
        ("Alpha effective", "alpha_eff", "alpha"),
        ("Forecast demand (μ_P)", "forecast_demand", "forecast_demand"),
        ("Forecast samples used", "forecast_n_samples", "forecast_n_samples"),
        ("Sigma daily (σ_day)", "sigma_daily", "sigma_daily"),
        ("Sigma horizon (σ_P)", "sigma_horizon", "sigma_horizon"),
        ("Sigma residuals used", "sigma_n_residuals", "sigma_n_residuals"),
        ("Z-score", "z_score", "z_score"),
        ("Reorder point (S)", "reorder_point", "reorder_point"),
        ("Inventory position (IP)", "inventory_position", "inventory_position"),
        ("Order raw (S - IP)", "order_raw", "order_raw"),
        ("Order final (after constraints)", "order_final", "order_final"),
    ]
    
    for label, key_with, key_without in metrics:
        val_with = result_with.get(key_with, "n/a")
        if key_without == "n/a":
            val_without = "n/a"
        else:
            val_without = result_without.get(key_without, "n/a")
        
        # Format values
        if isinstance(val_with, float):
            val_with = f"{val_with:.2f}"
        elif isinstance(val_with, list):
            val_with = ", ".join(str(v) for v in val_with[:2])  # Show first 2
            if len(result_with.get(key_with, [])) > 2:
                val_with += ", ..."
        
        if isinstance(val_without, float):
            val_without = f"{val_without:.2f}"
        
        print(f"{label:<40} {str(val_with):<20} {str(val_without):<20}")
    
    # Key insights
    print("\n" + "=" * 80)
    print("KEY INSIGHTS")
    print("=" * 80)
    
    delta_order = result_with["order_final"] - result_without["order_final"]
    delta_sigma = result_with["sigma_daily"] - result_without["sigma_daily"]
    pct_censored = (n_censored / len(history)) * 100
    
    print(f"\n✅ Censored days: {n_censored} ({pct_censored:.1f}% of history)")
    print(f"✅ Alpha boost: {result_with['alpha']} → {result_with['alpha_eff']:.3f}")
    print(f"✅ Sigma daily: {result_without['sigma_daily']:.2f} → {result_with['sigma_daily']:.2f} (Δ={delta_sigma:+.2f})")
    print(f"✅ Order quantity: {result_without['order_final']} → {result_with['order_final']} (Δ={delta_order:+d})")
    
    if delta_order > 0:
        print(f"\n🎯 Censored handling INCREASED order by {delta_order} units")
        print("   → Prevents artificial underordering due to OOS periods")
    elif delta_order < 0:
        print(f"\n⚠️  Censored handling DECREASED order by {abs(delta_order)} units")
        print("   → Check if censored detection is too aggressive")
    else:
        print(f"\n✓  Order quantity unchanged (censored impact minimal)")
    
    # Audit trail sample
    print("\n" + "=" * 80)
    print("AUDIT TRAIL (Sample)")
    print("=" * 80)
    print(f"\nSKU: {result_with['sku']}")
    print(f"Order Date: {result_with['order_date']}")
    print(f"Lane: {result_with['lane']}")
    print(f"Censored Days: {result_with['n_censored']}")
    if result_with['censored_reasons']:
        print(f"Censored Reasons:")
        for reason in result_with['censored_reasons'][:5]:
            print(f"  • {reason}")
    print(f"\nFinal Order: {result_with['order_final']} units")
    if result_with['constraints_applied']:
        print(f"Constraints Applied:")
        for constraint in result_with['constraints_applied']:
            print(f"  • {constraint}")
    
    print("\n" + "=" * 80)
    print("✅ Example completed successfully!")
    print("=" * 80)


if __name__ == "__main__":
    main()
