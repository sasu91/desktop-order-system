"""
Realistic order scenario demonstrating actual reordering decisions.

Scenario: Weekly order cycle with multiple SKUs at different
inventory positions and volatility levels.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta
import random
from src.replenishment_policy import compute_order, OrderConstraints
from src.domain.calendar import Lane


def generate_realistic_demand(base_demand, days=90, volatility=0.2):
    """Generate realistic demand with weekly seasonality and noise."""
    history = []
    random.seed(42)
    
    for i in range(days):
        day_date = date(2024, 1, 1) + timedelta(days=i)
        dow = day_date.weekday()
        
        # Weekly pattern: lower on weekends
        seasonal_factor = 1.2 if dow < 5 else 0.6
        
        # Add noise
        noise = random.uniform(-volatility, volatility)
        
        qty = base_demand * seasonal_factor * (1 + noise)
        history.append({"date": day_date, "qty_sold": max(0, qty)})
    
    return history


def scenario_low_stock_high_demand():
    """Scenario 1: Low stock with high demand → Order needed."""
    print("\n" + "=" * 70)
    print("SCENARIO 1: Low Stock + High Demand")
    print("=" * 70)
    
    history = generate_realistic_demand(base_demand=25, volatility=0.15)
    
    result = compute_order(
        sku="FAST-MOVER",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=15,        # Low stock!
        pipeline=[],
        constraints=OrderConstraints(pack_size=10, moq=50, max_stock=500),
        history=history
    )
    
    print(f"\n📦 SKU: {result['sku']}")
    print(f"📅 Protection Period: {result['protection_period']} days")
    print(f"📊 Daily Demand: {result['forecast_demand'] / result['protection_period']:.1f} units/day")
    print(f"🎯 Forecast (P={result['protection_period']}d): {result['forecast_demand']:.1f} units")
    print(f"📉 Current Inventory:")
    print(f"   On-Hand: {result['on_hand']} units")
    print(f"   On-Order: {result['on_order']} units")
    print(f"   Total IP: {result['inventory_position']} units")
    print(f"\n📈 Uncertainty:")
    print(f"   σ_daily: {result['sigma_daily']:.2f} units/day")
    print(f"   σ_horizon: {result['sigma_horizon']:.2f} units")
    print(f"   Safety Stock (95% CSL): {result['z_score'] * result['sigma_horizon']:.1f} units")
    print(f"\n🎲 Reorder Point: {result['reorder_point']:.1f} units")
    print(f"   = {result['forecast_demand']:.1f} + {result['z_score']:.3f} × {result['sigma_horizon']:.2f}")
    print(f"\n✅ ORDER DECISION: {result['order_final']} units")
    
    if result['constraints_applied']:
        print(f"\n📋 Constraints Applied:")
        for constraint in result['constraints_applied']:
            print(f"   • {constraint}")
    
    return result


def scenario_adequate_pipeline():
    """Scenario 2: Low stock but strong pipeline → No order."""
    print("\n" + "=" * 70)
    print("SCENARIO 2: Low Stock BUT Strong Pipeline")
    print("=" * 70)
    
    history = generate_realistic_demand(base_demand=20, volatility=0.1)
    
    pipeline = [
        {"receipt_date": date(2024, 4, 3), "qty": 150},
        {"receipt_date": date(2024, 4, 8), "qty": 100}
    ]
    
    result = compute_order(
        sku="STEADY-ITEM",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=25,
        pipeline=pipeline,
        constraints=OrderConstraints(pack_size=25, moq=100, max_stock=None),
        history=history
    )
    
    print(f"\n📦 SKU: {result['sku']}")
    print(f"📊 Forecast Demand: {result['forecast_demand']:.1f} units")
    print(f"📉 Current Inventory:")
    print(f"   On-Hand: {result['on_hand']} units (low!)")
    print(f"   Pending Orders:")
    for i, order in enumerate(pipeline, 1):
        print(f"     {i}. {order['qty']} units → {order['receipt_date']}")
    print(f"   Total On-Order: {result['on_order']} units")
    print(f"   → Inventory Position: {result['inventory_position']} units")
    print(f"\n🎲 Reorder Point: {result['reorder_point']:.1f} units")
    print(f"\n✅ ORDER DECISION: {result['order_final']} units")
    print(f"   → Pipeline sufficient, no order needed")
    
    return result


def scenario_volatile_demand():
    """Scenario 3: Volatile demand → Higher safety stock."""
    print("\n" + "=" * 70)
    print("SCENARIO 3: Volatile Demand Pattern")
    print("=" * 70)
    
    # High volatility
    history = generate_realistic_demand(base_demand=15, volatility=0.4)
    
    result = compute_order(
        sku="ERRATIC-SKU",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.98,  # Higher CSL for volatile item
        on_hand=30,
        pipeline=[],
        constraints=OrderConstraints(pack_size=5, moq=20, max_stock=300),
        history=history
    )
    
    print(f"\n📦 SKU: {result['sku']} (High Volatility)")
    print(f"📊 Forecast: {result['forecast_demand']:.1f} units")
    print(f"📈 Uncertainty:")
    print(f"   σ_daily: {result['sigma_daily']:.2f} units/day (HIGH)")
    print(f"   σ_horizon: {result['sigma_horizon']:.2f} units")
    print(f"   CSL Target: {result['alpha']*100:.0f}% (higher for volatile SKU)")
    print(f"   Safety Stock: {result['z_score'] * result['sigma_horizon']:.1f} units")
    print(f"\n📉 Inventory Position: {result['inventory_position']} units")
    print(f"🎲 Reorder Point: {result['reorder_point']:.1f} units")
    print(f"\n✅ ORDER DECISION: {result['order_final']} units")
    
    return result


def scenario_near_capacity():
    """Scenario 4: Order capped by max_stock constraint."""
    print("\n" + "=" * 70)
    print("SCENARIO 4: Capacity-Constrained Order")
    print("=" * 70)
    
    history = generate_realistic_demand(base_demand=50, volatility=0.1)
    
    result = compute_order(
        sku="SPACE-LIMITED",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=120,
        pipeline=[{"receipt_date": date(2024, 4, 5), "qty": 80}],
        constraints=OrderConstraints(pack_size=20, moq=50, max_stock=300),
        history=history
    )
    
    print(f"\n📦 SKU: {result['sku']}")
    print(f"📊 Forecast: {result['forecast_demand']:.1f} units")
    print(f"🎲 Reorder Point: {result['reorder_point']:.1f} units")
    print(f"\n📉 Current Position:")
    print(f"   On-Hand: {result['on_hand']} units")
    print(f"   On-Order: {result['on_order']} units")
    print(f"   Total IP: {result['inventory_position']} units")
    print(f"\n🏭 Capacity Constraint: max_stock = 300 units")
    print(f"   Available Space: {300 - result['inventory_position']} units")
    print(f"\n💡 Unconstrained Order: {result['order_raw']:.1f} units")
    print(f"   After Pack Rounding: {result['order_after_pack']} units")
    print(f"   After MOQ Check: {result['order_after_moq']} units")
    print(f"\n✅ FINAL ORDER (capped): {result['order_final']} units")
    
    if "Capped by max_stock" in result['constraints_applied']:
        print(f"   ⚠️  Order reduced to fit capacity limit")
    
    return result


def scenario_csl_comparison():
    """Scenario 5: Compare service levels for same SKU."""
    print("\n" + "=" * 70)
    print("SCENARIO 5: CSL Impact on Order Quantity")
    print("=" * 70)
    
    history = generate_realistic_demand(base_demand=18, volatility=0.2)
    
    csl_targets = [
        (0.85, "Bronze"),
        (0.90, "Silver"),
        (0.95, "Gold"),
        (0.98, "Platinum")
    ]
    
    print(f"\n📦 SKU: MULTI-TIER")
    print(f"📊 Base Demand: ~18 units/day")
    print(f"📉 Current Stock: 40 units (on-hand), 0 units (pipeline)")
    
    print(f"\n{'Service Level':15} | {'Z-score':>8} | {'Safety Stock':>13} | {'Order Qty':>10}")
    print("-" * 60)
    
    for alpha, tier in csl_targets:
        result = compute_order(
            sku="MULTI-TIER",
            order_date=date(2024, 4, 1),
            lane=Lane.STANDARD,
            alpha=alpha,
            on_hand=40,
            pipeline=[],
            constraints=OrderConstraints(pack_size=10, moq=20, max_stock=None),
            history=history
        )
        
        safety_stock = result['z_score'] * result['sigma_horizon']
        
        print(f"{tier:10} ({alpha*100:>3.0f}%) | {result['z_score']:8.3f} | "
              f"{safety_stock:13.1f} | {result['order_final']:10}")
    
    print(f"\n💡 Insight: Higher service level → more safety stock → larger orders")


def main():
    """Run all realistic scenarios."""
    print("\n" + "=" * 70)
    print(" REALISTIC REPLENISHMENT SCENARIOS")
    print(" Real-world order decisions with CSL-based policy")
    print("=" * 70)
    
    r1 = scenario_low_stock_high_demand()
    r2 = scenario_adequate_pipeline()
    r3 = scenario_volatile_demand()
    r4 = scenario_near_capacity()
    scenario_csl_comparison()
    
    print("\n" + "=" * 70)
    print(" SUMMARY OF ORDER DECISIONS")
    print("=" * 70)
    
    scenarios = [
        ("Low Stock + High Demand", r1),
        ("Low Stock + Strong Pipeline", r2),
        ("Volatile Demand", r3),
        ("Capacity Constrained", r4)
    ]
    
    print(f"\n{'Scenario':30} | {'Forecast':>10} | {'IP':>6} | {'Order':>8}")
    print("-" * 60)
    
    for name, result in scenarios:
        print(f"{name:30} | {result['forecast_demand']:10.1f} | "
              f"{result['inventory_position']:6} | {result['order_final']:8}")
    
    total_order = sum(r['order_final'] for _, r in scenarios)
    print(f"\nTotal Planned Orders: {total_order} units")
    
    print("\n" + "=" * 70)
    print(" ✅ All scenarios completed successfully!")
    print("=" * 70)


if __name__ == "__main__":
    main()
