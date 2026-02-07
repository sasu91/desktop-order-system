#!/usr/bin/env python
"""Manual test to verify Monte Carlo details enhancement."""

from datetime import date
from src.workflows.order import OrderWorkflow, calculate_daily_sales_average
from src.persistence.csv_layer import CSVLayer
from src.domain.ledger import StockCalculator

def test_mc_details():
    """Test Monte Carlo parameter population in order proposals."""
    csv_layer = CSVLayer()
    order_workflow = OrderWorkflow(csv_layer)
    
    # Load data
    skus_list = csv_layer.read_skus()
    transactions = csv_layer.read_transactions()
    sales_records = csv_layer.read_sales()
    
    # Test SKU_HIGH (configured with monte_carlo forecast method)
    sku_high = next((s for s in skus_list if s.sku == 'SKU_HIGH'), None)
    
    if not sku_high:
        print("❌ SKU_HIGH not found in dataset")
        return
    
    # Calculate stock
    asof = date(2026, 2, 8)
    stocks = StockCalculator.calculate_all_skus([sku_high.sku], asof, transactions, sales_records)
    stock = stocks[sku_high.sku]
    
    # Calculate daily sales
    daily_sales, oos_days = calculate_daily_sales_average(
        sales_records, sku_high.sku, days_lookback=30, 
        transactions=transactions, asof_date=asof, oos_detection_mode="strict"
    )
    
    # Generate proposal
    proposal = order_workflow.generate_proposal(
        sku=sku_high.sku,
        description=sku_high.description,
        current_stock=stock,
        daily_sales_avg=daily_sales,
        sku_obj=sku_high,
        oos_days_count=oos_days,
        oos_boost_percent=0.0
    )
    
    print("\n" + "="*70)
    print("MONTE CARLO DETAILS TEST")
    print("="*70)
    
    print(f"\n📦 SKU: {proposal.sku}")
    print(f"📋 Description: {proposal.description}")
    print(f"📊 Proposed Qty: {proposal.proposed_qty} pz")
    print(f"🎯 MC Method Used: {proposal.mc_method_used or '(empty)'}")
    
    print("\n" + "-"*70)
    print("MONTE CARLO PARAMETERS")
    print("-"*70)
    
    # Check main MC method fields
    if proposal.mc_method_used == "monte_carlo":
        print(f"✅ MC Method Used: {proposal.mc_method_used} (MAIN METHOD)")
    elif proposal.mc_method_used:
        print(f"⚠️  MC Method Used: {proposal.mc_method_used} (UNEXPECTED)")
    else:
        print(f"❌ MC Method Used: (empty) - Expected 'monte_carlo'")
    
    print(f"\n📈 Distribution: {proposal.mc_distribution or '(empty)'}")
    print(f"🔢 Simulations: {proposal.mc_n_simulations}")
    print(f"🎲 Random Seed: {proposal.mc_random_seed}")
    print(f"📊 Output Statistic: {proposal.mc_output_stat or '(empty)'}")
    
    if proposal.mc_output_stat == "percentile":
        print(f"📌 Percentile: P{proposal.mc_output_percentile}")
    elif proposal.mc_output_percentile > 0:
        print(f"⚠️  Percentile: {proposal.mc_output_percentile} (but stat is '{proposal.mc_output_stat}')")
    
    print(f"\n📅 Horizon Mode: {proposal.mc_horizon_mode or '(empty)'}")
    print(f"📅 Horizon Days: {proposal.mc_horizon_days}")
    print(f"📉 Forecast Summary: {proposal.mc_forecast_values_summary or '(empty)'}")
    
    # Check comparison fields
    print("\n" + "-"*70)
    print("COMPARISON FIELDS")
    print("-"*70)
    
    if proposal.mc_comparison_qty is not None:
        print(f"🔄 MC Comparison Qty: {proposal.mc_comparison_qty} pz")
        print(f"📊 Difference: {proposal.mc_comparison_qty - proposal.proposed_qty:+d} pz")
    else:
        print(f"⚠️  MC Comparison Qty: None (MC is main method)")
    
    # Validate fields are populated
    print("\n" + "-"*70)
    print("VALIDATION")
    print("-"*70)
    
    checks = {
        "mc_method_used == 'monte_carlo'": proposal.mc_method_used == "monte_carlo",
        "mc_distribution populated": bool(proposal.mc_distribution),
        "mc_n_simulations > 0": proposal.mc_n_simulations > 0,
        "mc_random_seed set": proposal.mc_random_seed != 0,
        "mc_output_stat populated": bool(proposal.mc_output_stat),
        "mc_horizon_mode populated": bool(proposal.mc_horizon_mode),
        "mc_horizon_days > 0": proposal.mc_horizon_days > 0,
        "mc_forecast_summary populated": bool(proposal.mc_forecast_values_summary),
    }
    
    passed = sum(checks.values())
    total = len(checks)
    
    for check, result in checks.items():
        status = "✅" if result else "❌"
        print(f"{status} {check}")
    
    print(f"\n{'✅' if passed == total else '⚠️ '} SCORE: {passed}/{total} checks passed")
    
    # Test SKU_STABLE (configured with simple forecast method but mc_show_comparison=true)
    print("\n\n" + "="*70)
    print("COMPARISON MODE TEST (SIMPLE + MC COMPARISON)")
    print("="*70)
    
    sku_stable = next((s for s in skus_list if s.sku == 'SKU_STABLE'), None)
    if sku_stable:
        stocks_stable = StockCalculator.calculate_all_skus([sku_stable.sku], asof, transactions, sales_records)
        stock_stable = stocks_stable[sku_stable.sku]
        
        daily_sales_stable, oos_days_stable = calculate_daily_sales_average(
            sales_records, sku_stable.sku, days_lookback=30,
            transactions=transactions, asof_date=asof, oos_detection_mode="strict"
        )
        
        proposal_stable = order_workflow.generate_proposal(
            sku=sku_stable.sku,
            description=sku_stable.description,
            current_stock=stock_stable,
            daily_sales_avg=daily_sales_stable,
            sku_obj=sku_stable,
            oos_days_count=oos_days_stable,
            oos_boost_percent=0.0
        )
        
        print(f"\n📦 SKU: {proposal_stable.sku}")
        print(f"🎯 MC Method Used: {proposal_stable.mc_method_used or '(empty)'}")
        print(f"📊 Proposed Qty: {proposal_stable.proposed_qty} pz")
        
        if proposal_stable.mc_comparison_qty is not None:
            print(f"✅ MC Comparison Qty: {proposal_stable.mc_comparison_qty} pz")
            print(f"   Difference: {proposal_stable.mc_comparison_qty - proposal_stable.proposed_qty:+d} pz")
            
            # Check MC params for comparison mode
            print(f"\n📈 MC Distribution (comparison): {proposal_stable.mc_distribution or '(empty)'}")
            print(f"📊 MC Output Stat (comparison): {proposal_stable.mc_output_stat or '(empty)'}")
            
            if proposal_stable.mc_output_stat == "percentile":
                print(f"📌 MC Percentile: P{proposal_stable.mc_output_percentile}")
            
            if not proposal_stable.mc_distribution:
                print(f"⚠️  WARNING: MC comparison active but distribution not populated")
        else:
            print(f"❌ MC Comparison Qty: None (expected comparison for mc_show_comparison=true)")
    
    print("\n" + "="*70)
    print("TEST COMPLETE")
    print("="*70 + "\n")

if __name__ == "__main__":
    test_mc_details()
