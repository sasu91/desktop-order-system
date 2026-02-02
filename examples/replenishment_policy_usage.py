"""
Example usage of CSL-based replenishment policy.

Demonstrates how to compute order quantities with full breakdown
including forecast, uncertainty, and constraint application.

Author: Desktop Order System Team
Date: February 2026
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta
from src.replenishment_policy import compute_order, OrderConstraints, compute_order_batch
from src.domain.calendar import Lane


def example_basic_order():
    """Example 1: Basic order computation with stable demand."""
    print("=" * 70)
    print("EXAMPLE 1: Basic Order Computation")
    print("=" * 70)
    
    # Generate 90 days of stable demand (10 units/day)
    history = [
        {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
        for i in range(90)
    ]
    
    # Define constraints
    constraints = OrderConstraints(
        pack_size=10,      # Order in packs of 10
        moq=20,            # Minimum order 20 units
        max_stock=500      # Maximum total stock
    )
    
    # Compute order
    result = compute_order(
        sku="WIDGET-001",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,        # 95% service level
        on_hand=30,
        pipeline=[],       # No pending orders
        constraints=constraints,
        history=history
    )
    
    # Display breakdown
    print(f"\nSKU: {result['sku']}")
    print(f"Order Date: {result['order_date']}")
    print(f"Target CSL: {result['alpha'] * 100:.0f}%")
    print(f"\n--- DEMAND FORECAST ---")
    print(f"Protection Period: {result['protection_period']} days")
    print(f"Forecast Demand (μ_P): {result['forecast_demand']:.1f} units")
    print(f"\n--- UNCERTAINTY ---")
    print(f"Daily σ: {result['sigma_daily']:.2f} units/day")
    print(f"Horizon σ: {result['sigma_horizon']:.2f} units")
    print(f"Z-score (α={result['alpha']}): {result['z_score']:.3f}")
    print(f"\n--- INVENTORY POSITION ---")
    print(f"On-Hand: {result['on_hand']:.0f} units")
    print(f"On-Order: {result['on_order']:.0f} units")
    print(f"Total IP: {result['inventory_position']:.0f} units")
    print(f"\n--- ORDER CALCULATION ---")
    print(f"Reorder Point (S): {result['reorder_point']:.1f} units")
    print(f"  = μ_P ({result['forecast_demand']:.1f}) + z×σ_P ({result['z_score']:.3f}×{result['sigma_horizon']:.2f})")
    print(f"\nRaw Order (S - IP): {result['order_raw']:.1f} units")
    print(f"After Pack Rounding: {result['order_after_pack']} units")
    print(f"After MOQ Check: {result['order_after_moq']} units")
    print(f"\n>>> FINAL ORDER: {result['order_final']} units <<<")
    
    if result['constraints_applied']:
        print(f"\nConstraints Applied:")
        for constraint in result['constraints_applied']:
            print(f"  - {constraint}")
    
    return result


def example_volatile_demand():
    """Example 2: Order with volatile demand (higher uncertainty)."""
    print("\n" + "=" * 70)
    print("EXAMPLE 2: Volatile Demand Pattern")
    print("=" * 70)
    
    # Generate 90 days of volatile demand
    import random
    random.seed(123)
    history = [
        {"date": date(2024, 1, 1) + timedelta(days=i), 
         "qty_sold": 10.0 + random.uniform(-5, 5)}
        for i in range(90)
    ]
    
    constraints = OrderConstraints(pack_size=5, moq=10, max_stock=None)
    
    result = compute_order(
        sku="GADGET-002",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=25,
        pipeline=[],
        constraints=constraints,
        history=history
    )
    
    print(f"\nSKU: {result['sku']} (Volatile Demand)")
    print(f"Forecast Demand: {result['forecast_demand']:.1f} units")
    print(f"Daily σ: {result['sigma_daily']:.2f} units/day (higher due to volatility)")
    print(f"Horizon σ: {result['sigma_horizon']:.2f} units")
    print(f"Safety Stock: {result['z_score'] * result['sigma_horizon']:.1f} units")
    print(f"\nFinal Order: {result['order_final']} units")
    
    return result


def example_with_pipeline():
    """Example 3: Order with existing pipeline (on-order inventory)."""
    print("\n" + "=" * 70)
    print("EXAMPLE 3: Order with Existing Pipeline")
    print("=" * 70)
    
    history = [
        {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 15.0}
        for i in range(90)
    ]
    
    # Existing orders in pipeline
    pipeline = [
        {"receipt_date": date(2024, 4, 5), "qty": 50},   # Arrives soon
        {"receipt_date": date(2024, 4, 15), "qty": 100}  # Arrives later
    ]
    
    constraints = OrderConstraints(pack_size=10, moq=20)
    
    result = compute_order(
        sku="TOOL-003",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=20,
        pipeline=pipeline,
        constraints=constraints,
        history=history
    )
    
    print(f"\nSKU: {result['sku']}")
    print(f"On-Hand: {result['on_hand']:.0f} units")
    print(f"On-Order: {result['on_order']:.0f} units")
    print(f"  Pipeline details:")
    for i, order in enumerate(pipeline, 1):
        print(f"    {i}. {order['qty']} units arriving {order['receipt_date']}")
    print(f"\nTotal IP: {result['inventory_position']:.0f} units")
    print(f"Reorder Point: {result['reorder_point']:.1f} units")
    print(f"\nFinal Order: {result['order_final']} units")
    
    if result['order_final'] == 0:
        print("  → No order needed (pipeline sufficient)")
    
    return result


def example_csl_comparison():
    """Example 4: Compare different CSL targets."""
    print("\n" + "=" * 70)
    print("EXAMPLE 4: CSL Sensitivity Analysis")
    print("=" * 70)
    
    history = [
        {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 12.0}
        for i in range(90)
    ]
    
    constraints = OrderConstraints(pack_size=1, moq=0, max_stock=None)
    
    csl_levels = [0.80, 0.90, 0.95, 0.98, 0.99]
    
    print(f"\n{'CSL':>6} | {'Z-score':>8} | {'Safety Stock':>12} | {'Order Qty':>10}")
    print("-" * 50)
    
    for alpha in csl_levels:
        result = compute_order(
            sku="ITEM-004",
            order_date=date(2024, 4, 1),
            lane=Lane.STANDARD,
            alpha=alpha,
            on_hand=50,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        safety_stock = result['z_score'] * result['sigma_horizon']
        
        print(f"{alpha*100:5.0f}% | {result['z_score']:8.3f} | "
              f"{safety_stock:12.1f} | {result['order_final']:10}")
    
    print("\nObservation: Higher CSL → Higher safety stock → Higher order quantity")


def example_constraint_effects():
    """Example 5: Demonstrate constraint application."""
    print("\n" + "=" * 70)
    print("EXAMPLE 5: Constraint Effects")
    print("=" * 70)
    
    history = [
        {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 8.0}
        for i in range(90)
    ]
    
    # Scenario 1: No constraints
    result_none = compute_order(
        sku="PART-005",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=10,
        pipeline=[],
        constraints=OrderConstraints(pack_size=1, moq=0, max_stock=None),
        history=history
    )
    
    # Scenario 2: Pack size only
    result_pack = compute_order(
        sku="PART-005",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=10,
        pipeline=[],
        constraints=OrderConstraints(pack_size=25, moq=0, max_stock=None),
        history=history
    )
    
    # Scenario 3: Pack + MOQ
    result_moq = compute_order(
        sku="PART-005",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=10,
        pipeline=[],
        constraints=OrderConstraints(pack_size=25, moq=100, max_stock=None),
        history=history
    )
    
    # Scenario 4: All constraints
    result_all = compute_order(
        sku="PART-005",
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=10,
        pipeline=[],
        constraints=OrderConstraints(pack_size=25, moq=100, max_stock=200),
        history=history
    )
    
    print(f"\n{'Constraint Set':30} | {'Raw':>10} | {'Final':>10}")
    print("-" * 55)
    print(f"{'None (baseline)':30} | {result_none['order_raw']:10.1f} | {result_none['order_final']:10}")
    print(f"{'Pack=25':30} | {result_pack['order_raw']:10.1f} | {result_pack['order_final']:10}")
    print(f"{'Pack=25, MOQ=100':30} | {result_moq['order_raw']:10.1f} | {result_moq['order_final']:10}")
    print(f"{'Pack=25, MOQ=100, Cap=200':30} | {result_all['order_raw']:10.1f} | {result_all['order_final']:10}")


def example_batch_processing():
    """Example 6: Batch order computation for multiple SKUs."""
    print("\n" + "=" * 70)
    print("EXAMPLE 6: Batch Order Processing")
    print("=" * 70)
    
    # Generate history for 3 SKUs
    history_map = {
        "SKU-A": [{"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 20.0} for i in range(90)],
        "SKU-B": [{"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 5.0} for i in range(90)],
        "SKU-C": [{"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 50.0} for i in range(90)],
    }
    
    # Inventory data
    inventory_data = {
        "SKU-A": {"on_hand": 100, "pipeline": []},
        "SKU-B": {"on_hand": 10, "pipeline": [{"receipt_date": date(2024, 4, 5), "qty": 20}]},
        "SKU-C": {"on_hand": 250, "pipeline": []},
    }
    
    # Constraints per SKU
    constraints_map = {
        "SKU-A": OrderConstraints(pack_size=10, moq=20),
        "SKU-B": OrderConstraints(pack_size=5, moq=10),
        "SKU-C": OrderConstraints(pack_size=50, moq=100),
    }
    
    # Batch compute
    results = compute_order_batch(
        skus=["SKU-A", "SKU-B", "SKU-C"],
        order_date=date(2024, 4, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        inventory_data=inventory_data,
        constraints_map=constraints_map,
        history_map=history_map
    )
    
    # Display summary
    print(f"\n{'SKU':10} | {'Forecast':>10} | {'On-Hand':>10} | {'On-Order':>10} | {'Order':>10}")
    print("-" * 65)
    
    for sku, result in results.items():
        print(f"{sku:10} | {result['forecast_demand']:10.1f} | "
              f"{result['on_hand']:10.0f} | {result['on_order']:10.0f} | "
              f"{result['order_final']:10}")
    
    total_order_value = sum(r['order_final'] for r in results.values())
    print(f"\nTotal Order Quantity: {total_order_value} units across {len(results)} SKUs")


def main():
    """Run all examples."""
    print("\n" + "=" * 70)
    print(" CSL-BASED REPLENISHMENT POLICY - USAGE EXAMPLES")
    print("=" * 70)
    
    example_basic_order()
    example_volatile_demand()
    example_with_pipeline()
    example_csl_comparison()
    example_constraint_effects()
    example_batch_processing()
    
    print("\n" + "=" * 70)
    print(" All examples completed successfully!")
    print("=" * 70)


if __name__ == "__main__":
    main()
