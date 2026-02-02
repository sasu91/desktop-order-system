"""
Weekly order planning scenario with EXPRESS lane (longer protection period).

Demonstrates realistic order decisions for weekly replenishment cycle.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta
import random
from src.replenishment_policy import compute_order, OrderConstraints, compute_order_batch
from src.domain.calendar import Lane


def generate_demand_with_trend(base_demand, days=90, trend=0.0, volatility=0.15):
    """Generate demand with optional trend and seasonality."""
    history = []
    random.seed(42)
    
    for i in range(days):
        day_date = date(2024, 1, 1) + timedelta(days=i)
        dow = day_date.weekday()
        
        # Trend component
        trend_factor = 1 + (trend * i / days)
        
        # Weekly seasonality
        seasonal = 1.2 if dow < 5 else 0.6
        
        # Random noise
        noise = random.uniform(-volatility, volatility)
        
        qty = base_demand * trend_factor * seasonal * (1 + noise)
        history.append({"date": day_date, "qty_sold": max(0, qty)})
    
    return history


def weekly_order_planning():
    """
    Simulate weekly order planning for a portfolio of SKUs.
    
    Context: Monday order session with EXPRESS lane (7-day protection).
    """
    print("\n" + "=" * 75)
    print(" WEEKLY ORDER PLANNING SESSION")
    print(" Date: Monday, April 1, 2024")
    print(" Lane: STANDARD (Monday-Thursday orders)")
    print("=" * 75)
    
    # Portfolio of SKUs with different characteristics
    # Using lower on_hand to trigger actual orders
    portfolio = {
        "WIDGET-A": {
            "description": "Fast mover, stable demand",
            "base_demand": 25,
            "trend": 0.1,  # 10% growth
            "volatility": 0.10,
            "on_hand": 20,  # Low stock
            "pipeline": [],
            "constraints": OrderConstraints(pack_size=20, moq=100, max_stock=800)
        },
        "GADGET-B": {
            "description": "Medium seller, volatile",
            "base_demand": 12,
            "trend": 0.0,
            "volatility": 0.30,
            "on_hand": 15,  # Low stock
            "pipeline": [{"receipt_date": date(2024, 4, 5), "qty": 60}],
            "constraints": OrderConstraints(pack_size=10, moq=50, max_stock=500)
        },
        "TOOL-C": {
            "description": "Slow mover, predictable",
            "base_demand": 5,
            "trend": -0.05,  # Declining
            "volatility": 0.08,
            "on_hand": 8,  # Low stock
            "pipeline": [],
            "constraints": OrderConstraints(pack_size=5, moq=20, max_stock=200)
        },
        "PART-D": {
            "description": "High volume, capacity limited",
            "base_demand": 40,
            "trend": 0.15,  # Strong growth
            "volatility": 0.12,
            "on_hand": 80,  # Low stock
            "pipeline": [{"receipt_date": date(2024, 4, 4), "qty": 200}],
            "constraints": OrderConstraints(pack_size=50, moq=200, max_stock=1000)
        },
        "ITEM-E": {
            "description": "New product, uncertain demand",
            "base_demand": 8,
            "trend": 0.25,  # Rapid growth
            "volatility": 0.40,
            "on_hand": 10,  # Low stock
            "pipeline": [],
            "constraints": OrderConstraints(pack_size=6, moq=30, max_stock=300)
        }
    }
    
    # Generate demand history for each SKU
    history_map = {}
    for sku, params in portfolio.items():
        history_map[sku] = generate_demand_with_trend(
            base_demand=params["base_demand"],
            trend=params["trend"],
            volatility=params["volatility"]
        )
    
    # Compute orders
    results = {}
    order_date = date(2024, 4, 1)  # Monday
    
    print(f"\n📅 Order Date: {order_date.strftime('%A, %B %d, %Y')}")
    print(f"🚚 Lane: STANDARD → Next delivery: Tuesday")
    print(f"🎯 Target CSL: 95%\n")
    
    for sku, params in portfolio.items():
        result = compute_order(
            sku=sku,
            order_date=order_date,
            lane=Lane.STANDARD,  # Standard lane
            alpha=0.95,
            on_hand=params["on_hand"],
            pipeline=params["pipeline"],
            constraints=params["constraints"],
            history=history_map[sku]
        )
        results[sku] = result
    
    # Display detailed breakdown
    print("=" * 75)
    print(" SKU-BY-SKU ANALYSIS")
    print("=" * 75)
    
    for sku, result in results.items():
        params = portfolio[sku]
        
        print(f"\n📦 {sku}: {params['description']}")
        print(f"   {'─' * 70}")
        
        # Demand metrics
        daily_demand = result['forecast_demand'] / result['protection_period']
        print(f"   📊 Demand Forecast:")
        print(f"      • Daily: {daily_demand:.1f} units/day")
        print(f"      • {result['protection_period']}-day: {result['forecast_demand']:.1f} units")
        print(f"      • Volatility (σ): {result['sigma_daily']:.2f} units/day")
        
        # Inventory status
        print(f"\n   📉 Inventory Position:")
        print(f"      • On-Hand: {result['on_hand']} units")
        print(f"      • On-Order: {result['on_order']} units", end="")
        if params['pipeline']:
            print(f" (arriving {params['pipeline'][0]['receipt_date']})")
        else:
            print()
        print(f"      • Total IP: {result['inventory_position']} units")
        
        # Reorder logic
        safety_stock = result['z_score'] * result['sigma_horizon']
        print(f"\n   🎲 Reorder Calculation:")
        print(f"      • Safety Stock (95%): {safety_stock:.1f} units")
        print(f"      • Reorder Point (S): {result['reorder_point']:.1f} units")
        print(f"      • Gap (S - IP): {result['order_raw']:.1f} units")
        
        # Order decision
        print(f"\n   ✅ ORDER DECISION: {result['order_final']} units")
        
        if result['order_final'] > 0:
            print(f"      💰 Estimated value: ${result['order_final'] * 10:.0f}")
            
            if result['constraints_applied']:
                print(f"      📋 Constraints:")
                for constraint in result['constraints_applied']:
                    print(f"         • {constraint}")
        else:
            if result['inventory_position'] >= result['reorder_point']:
                print(f"      ✓ Stock adequate (IP ≥ S)")
            else:
                print(f"      ⚠️  Below reorder point but constraints blocked order")
    
    # Summary table
    print("\n" + "=" * 75)
    print(" ORDER SUMMARY")
    print("=" * 75)
    
    print(f"\n{'SKU':12} | {'Forecast':>10} | {'On-Hand':>9} | {'Pipeline':>9} | {'Order':>8} | {'Value':>10}")
    print("─" * 75)
    
    total_order_qty = 0
    total_value = 0
    
    for sku, result in results.items():
        order_qty = result['order_final']
        value = order_qty * 10  # Assume $10/unit
        
        total_order_qty += order_qty
        total_value += value
        
        print(f"{sku:12} | {result['forecast_demand']:10.1f} | "
              f"{result['on_hand']:9} | {result['on_order']:9} | "
              f"{order_qty:8} | ${value:9.0f}")
    
    print("─" * 75)
    print(f"{'TOTAL':12} | {'':<10} | {'':<9} | {'':<9} | "
          f"{total_order_qty:8} | ${total_value:9.0f}")
    
    # Key insights
    print("\n" + "=" * 75)
    print(" KEY INSIGHTS")
    print("=" * 75)
    
    skus_to_order = sum(1 for r in results.values() if r['order_final'] > 0)
    avg_service_days = sum(r['inventory_position'] / (r['forecast_demand'] / r['protection_period']) 
                           for r in results.values()) / len(results)
    
    print(f"\n📈 Portfolio Metrics:")
    print(f"   • SKUs requiring orders: {skus_to_order}/{len(results)}")
    print(f"   • Total order quantity: {total_order_qty} units")
    print(f"   • Total order value: ${total_value:,.0f}")
    print(f"   • Avg days of stock: {avg_service_days:.1f} days")
    
    # Recommendations
    print(f"\n💡 Recommendations:")
    
    low_stock = [sku for sku, r in results.items() 
                 if r['inventory_position'] < r['forecast_demand']]
    if low_stock:
        print(f"   ⚠️  Critical stock levels: {', '.join(low_stock)}")
    
    high_growth = [sku for sku, params in portfolio.items() if params['trend'] > 0.15]
    if high_growth:
        print(f"   🚀 Monitor growth trends: {', '.join(high_growth)}")
    
    volatile = [sku for sku, r in results.items() if r['sigma_daily'] > 3.0]
    if volatile:
        print(f"   📊 Review forecast models: {', '.join(volatile)} (high volatility)")
    
    print("\n" + "=" * 75)
    print(" ✅ Order planning completed successfully!")
    print("=" * 75)
    
    return results


if __name__ == "__main__":
    weekly_order_planning()
