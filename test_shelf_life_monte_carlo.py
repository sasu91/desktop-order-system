"""
Test Shelf Life Integration in Monte Carlo Forecast (Fase 3)
Verifica che expected_waste_rate riduca il forecast MC correttamente.
"""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from src.forecast import monte_carlo_forecast
from src.uncertainty import WasteUncertainty


def test_waste_uncertainty_calculations():
    """Test WasteUncertainty static methods."""
    print("\n" + "="*80)
    print("TEST 1: WasteUncertainty Static Methods")
    print("="*80 + "\n")
    
    # Test 1: Variance multiplier
    print("📊 Test variance_multiplier:")
    mult_20 = WasteUncertainty.calculate_waste_variance_multiplier(20.0, base_multiplier=0.3)
    mult_50 = WasteUncertainty.calculate_waste_variance_multiplier(50.0, base_multiplier=0.5)
    print(f"   waste_risk=20%, base=0.3 → multiplier={mult_20:.2f} (expected: 1.06)")
    print(f"   waste_risk=50%, base=0.5 → multiplier={mult_50:.2f} (expected: 1.25)")
    
    assert abs(mult_20 - 1.06) < 0.01, f"FAIL: Expected 1.06, got {mult_20}"
    assert abs(mult_50 - 1.25) < 0.01, f"FAIL: Expected 1.25, got {mult_50}"
    print("   ✅ Variance multiplier OK\n")
    
    # Test 2: Expected waste rate
    print("📊 Test expected_waste_rate:")
    rate_20 = WasteUncertainty.calculate_expected_waste_rate(20.0, waste_realization_factor=0.5)
    rate_40 = WasteUncertainty.calculate_expected_waste_rate(40.0, waste_realization_factor=0.7)
    print(f"   waste_risk=20%, realization=0.5 → expected_rate={rate_20:.2f} (expected: 0.10 = 10%)")
    print(f"   waste_risk=40%, realization=0.7 → expected_rate={rate_40:.2f} (expected: 0.28 = 28%)")
    
    assert abs(rate_20 - 0.10) < 0.01, f"FAIL: Expected 0.10, got {rate_20}"
    assert abs(rate_40 - 0.28) < 0.01, f"FAIL: Expected 0.28, got {rate_40}"
    print("   ✅ Expected waste rate OK\n")
    
    # Test 3: Safety stock adjustment
    print("📊 Test safety_stock_adjustment:")
    ss_adj_30 = WasteUncertainty.adjust_safety_stock_for_waste(100, waste_risk_percent=30.0, safety_buffer_factor=0.2)
    ss_adj_50 = WasteUncertainty.adjust_safety_stock_for_waste(100, waste_risk_percent=50.0, safety_buffer_factor=0.3)
    print(f"   base_ss=100, waste_risk=30%, buffer=0.2 → adjusted_ss={ss_adj_30} (expected: 106)")
    print(f"   base_ss=100, waste_risk=50%, buffer=0.3 → adjusted_ss={ss_adj_50} (expected: 115)")
    
    assert ss_adj_30 == 106, f"FAIL: Expected 106, got {ss_adj_30}"
    assert ss_adj_50 == 115, f"FAIL: Expected 115, got {ss_adj_50}"
    print("   ✅ Safety stock adjustment OK\n")
    
    print("="*80)
    print("✅ WASTE UNCERTAINTY CALCULATIONS - ALL TESTS PASSED")
    print("="*80 + "\n")


def test_monte_carlo_with_waste_rate():
    """Test monte_carlo_forecast with expected_waste_rate parameter."""
    print("\n" + "="*80)
    print("TEST 2: Monte Carlo Forecast with Shelf Life Waste")
    print("="*80 + "\n")
    
    # Create synthetic history: 30 days, avg ~10 units/day
    history = [
        {"date": date(2026, 1, i), "qty_sold": 10 + (i % 3)}
        for i in range(1, 31)
    ]
    
    print("📦 Historical sales: 30 days, avg ~10 units/day\n")
    
    # Test 1: NO waste (baseline)
    print("🔬 Test 1: Baseline forecast (NO waste)")
    fc_no_waste = monte_carlo_forecast(
        history=history,
        horizon_days=7,
        distribution="empirical",
        n_simulations=1000,
        random_seed=42,
        output_stat="mean",
        expected_waste_rate=0.0,  # NO waste
    )
    
    avg_daily_no_waste = sum(fc_no_waste) / len(fc_no_waste)
    total_no_waste = sum(fc_no_waste)
    print(f"   Total forecast (7d): {total_no_waste:.1f}")
    print(f"   Avg daily forecast: {avg_daily_no_waste:.2f}")
    print(f"   Expected: ~10 units/day → ~70 total\n")
    
    # Test 2: 10% waste
    print("🔬 Test 2: Forecast with 10% expected waste")
    fc_10_waste = monte_carlo_forecast(
        history=history,
        horizon_days=7,
        distribution="empirical",
        n_simulations=1000,
        random_seed=42,
        output_stat="mean",
        expected_waste_rate=0.10,  # 10% waste
    )
    
    avg_daily_10_waste = sum(fc_10_waste) / len(fc_10_waste)
    total_10_waste = sum(fc_10_waste)
    reduction_10 = (total_no_waste - total_10_waste) / total_no_waste * 100
    print(f"   Total forecast (7d): {total_10_waste:.1f}")
    print(f"   Avg daily forecast: {avg_daily_10_waste:.2f}")
    print(f"   Reduction from baseline: {reduction_10:.1f}%")
    print(f"   Expected: ~10% reduction → ~63 total\n")
    
    # Test 3: 30% waste
    print("🔬 Test 3: Forecast with 30% expected waste")
    fc_30_waste = monte_carlo_forecast(
        history=history,
        horizon_days=7,
        distribution="empirical",
        n_simulations=1000,
        random_seed=42,
        output_stat="mean",
        expected_waste_rate=0.30,  # 30% waste
    )
    
    avg_daily_30_waste = sum(fc_30_waste) / len(fc_30_waste)
    total_30_waste = sum(fc_30_waste)
    reduction_30 = (total_no_waste - total_30_waste) / total_no_waste * 100
    print(f"   Total forecast (7d): {total_30_waste:.1f}")
    print(f"   Avg daily forecast: {avg_daily_30_waste:.2f}")
    print(f"   Reduction from baseline: {reduction_30:.1f}%")
    print(f"   Expected: ~30% reduction → ~49 total\n")
    
    # Verifiche
    print("🔍 VERIFICHE:")
    
    # Verifica 1: 10% waste → ~10% reduction
    assert abs(reduction_10 - 10.0) < 2.0, \
        f"FAIL: Expected ~10% reduction, got {reduction_10:.1f}%"
    print(f"   ✅ 10% waste → {reduction_10:.1f}% reduction (within 2% tolerance)")
    
    # Verifica 2: 30% waste → ~30% reduction
    assert abs(reduction_30 - 30.0) < 2.0, \
        f"FAIL: Expected ~30% reduction, got {reduction_30:.1f}%"
    print(f"   ✅ 30% waste → {reduction_30:.1f}% reduction (within 2% tolerance)")
    
    # Verifica 3: Forecast monotonically decreasing with waste rate
    assert total_no_waste > total_10_waste > total_30_waste, \
        f"FAIL: Forecast should decrease with waste rate"
    print(f"   ✅ Forecast decreases monotonically: {total_no_waste:.0f} > {total_10_waste:.0f} > {total_30_waste:.0f}")
    
    print("\n" + "="*80)
    print("✅ MONTE CARLO WITH WASTE RATE - ALL TESTS PASSED")
    print("="*80 + "\n")


def test_integration_waste_rate_calculation():
    """Test expected_waste_rate calculation from waste_risk_percent."""
    print("\n" + "="*80)
    print("TEST 3: Integration - waste_risk → expected_waste_rate")
    print("="*80 + "\n")
    
    # Scenario: SKU with 25% waste risk
    waste_risk_percent = 25.0
    waste_realization_factor = 0.5  # 50% of at-risk stock becomes waste
    
    expected_waste_rate = WasteUncertainty.calculate_expected_waste_rate(
        waste_risk_percent=waste_risk_percent,
        waste_realization_factor=waste_realization_factor
    )
    
    print(f"📊 Scenario:")
    print(f"   Waste risk: {waste_risk_percent}%")
    print(f"   Realization factor: {waste_realization_factor} (50% of at-risk becomes waste)")
    print(f"   → Expected waste rate: {expected_waste_rate:.2%}\n")
    
    # Use this rate in MC forecast
    history = [
        {"date": date(2026, 1, i), "qty_sold": 20 + (i % 5)}
        for i in range(1, 31)
    ]
    
    fc_baseline = monte_carlo_forecast(
        history=history,
        horizon_days=7,
        distribution="empirical",
        n_simulations=1000,
        random_seed=42,
        output_stat="mean",
        expected_waste_rate=0.0,
    )
    
    fc_with_waste = monte_carlo_forecast(
        history=history,
        horizon_days=7,
        distribution="empirical",
        n_simulations=1000,
        random_seed=42,
        output_stat="mean",
        expected_waste_rate=expected_waste_rate,
    )
    
    total_baseline = sum(fc_baseline)
    total_with_waste = sum(fc_with_waste)
    actual_reduction = (total_baseline - total_with_waste) / total_baseline * 100
    
    print(f"📦 Forecast results (7d horizon):")
    print(f"   Baseline (no waste): {total_baseline:.1f}")
    print(f"   With waste adjustment: {total_with_waste:.1f}")
    print(f"   Reduction: {actual_reduction:.1f}%")
    print(f"   Expected reduction: {expected_waste_rate * 100:.1f}%\n")
    
    # Verifica
    assert abs(actual_reduction - expected_waste_rate * 100) < 1.0, \
        f"FAIL: Expected {expected_waste_rate * 100:.1f}% reduction, got {actual_reduction:.1f}%"
    print(f"   ✅ Waste rate applied correctly (within 1% tolerance)\n")
    
    print("="*80)
    print("✅ INTEGRATION TEST - PASSED")
    print("="*80 + "\n")


if __name__ == "__main__":
    test_waste_uncertainty_calculations()
    test_monte_carlo_with_waste_rate()
    test_integration_waste_rate_calculation()
    
    print("\n" + "🎉" * 40)
    print("✅ ALL PHASE 3 TESTS PASSED - MONTE CARLO SHELF LIFE INTEGRATION OK!")
    print("🎉" * 40 + "\n")
