"""
Test suite for uncertainty estimation module.

Tests robust statistical estimators for demand uncertainty,
critical for safety stock calculations with CSL targets.

Test Coverage:
1. Robust sigma estimation (MAD, Winsorized)
2. Outlier resistance
3. Horizon scaling (√P formula)
4. Forecast residual calculation
5. Complete safety stock workflow

Author: Desktop Order System Team
Date: February 2026
"""

import pytest
from datetime import date, timedelta
from src.uncertainty import (
    robust_sigma,
    winsorized_sigma,
    sigma_over_horizon,
    calculate_forecast_residuals,
    estimate_demand_uncertainty,
    safety_stock_for_csl,
    calculate_safety_stock,
    MAD_TO_SIGMA_FACTOR,
)


class TestRobustSigma:
    """Test robust standard deviation estimation using MAD."""
    
    def test_clean_data_normal_distribution(self):
        """MAD should approximate std dev for clean normal-like data."""
        # Symmetric distribution around 0
        residuals = [-2, -1, 0, 1, 2]
        
        sigma = robust_sigma(residuals)
        
        # For this symmetric data: median=0, MAD=median([2,1,0,1,2])=1
        # σ_robust = 1.4826 × 1 = 1.4826
        expected = 1.4826
        assert abs(sigma - expected) < 0.01
    
    def test_outlier_resistance(self):
        """Core requirement: huge outlier should NOT multiply sigma disproportionately."""
        # Clean data: residuals around ±1
        clean = [1.0, 1.1, 0.9, 1.2, 0.8]
        sigma_clean = robust_sigma(clean)
        
        # Add massive outlier
        with_outlier = clean + [1000.0]
        sigma_outlier = robust_sigma(with_outlier)
        
        # MAD is outlier-resistant: sigma should barely change
        # Allow small change (median might shift slightly), but NOT proportional to outlier size
        ratio = sigma_outlier / sigma_clean
        
        # Ratio should be close to 1 (< 2x even with 1000x outlier)
        assert 0.5 < ratio < 2.0, f"Sigma changed by {ratio}x with outlier (should be ~1x)"
        
        # More specifically: should be very close to original
        assert abs(sigma_outlier - sigma_clean) < 1.0
    
    def test_multiple_outliers_resistance(self):
        """MAD breakdown point: up to 50% outliers should be handled."""
        # 8 clean values + 4 outliers = 33% contamination
        clean = [1, 2, 3, 4, 5, 6, 7, 8]
        outliers = [100, 200, 300, 400]
        
        combined = clean + outliers
        
        sigma = robust_sigma(combined)
        
        # Should be dominated by clean data (median-based)
        # Median of clean: 4.5, MAD of clean ≈ 2.0-3.0
        assert 2.0 < sigma < 6.0  # Reasonable range for clean data
    
    def test_empty_input(self):
        """Empty residuals should return 0."""
        assert robust_sigma([]) == 0.0
    
    def test_single_value(self):
        """Single value has no variability."""
        assert robust_sigma([5.0]) == 0.0
    
    def test_constant_values(self):
        """All identical values → zero variance."""
        residuals = [10.0] * 20
        sigma = robust_sigma(residuals)
        
        # Median = 10, all deviations = 0, MAD = 0
        assert sigma == 0.0
    
    def test_small_sample_fallback(self):
        """MAD works even for small samples (N < 7)."""
        residuals = [1, -1, 2, -2]  # N=4, symmetric
        
        sigma = robust_sigma(residuals)
        
        # Median = 0, deviations = [1, 1, 2, 2], MAD = median([1,1,2,2]) = 1.5
        # σ_robust = 1.4826 × 1.5 = 2.22
        expected = 1.4826 * 1.5
        assert abs(sigma - expected) < 0.01
    
    def test_asymmetric_distribution(self):
        """MAD handles asymmetric distributions (unlike std dev assumptions)."""
        # Skewed right: many small, few large
        residuals = [1, 1, 2, 2, 3, 10, 15]
        
        sigma = robust_sigma(residuals)
        
        # Should be finite and reasonable (not blown up by tail)
        assert 0 < sigma < 10


class TestWinsorizedSigma:
    """Test Winsorized standard deviation estimation."""
    
    def test_no_outliers_similar_to_stdev(self):
        """Clean data: Winsorized ≈ standard deviation."""
        import statistics
        residuals = [1, 2, 3, 4, 5]
        
        sigma_wins = winsorized_sigma(residuals, trim_proportion=0.1)
        sigma_std = statistics.stdev(residuals)
        
        # Should be very close (minimal trimming effect)
        assert abs(sigma_wins - sigma_std) < 0.5
    
    def test_outlier_mitigation(self):
        """Winsorized method reduces impact of outliers."""
        # Simple test: winsorized should not explode with outliers
        residuals = [1, 2, 2, 3, 3, 3, 4, 4, 5, 100, 200]
        
        sigma_wins = winsorized_sigma(residuals, trim_proportion=0.25)
        
        # Should produce finite, reasonable value (not crash)
        assert sigma_wins > 0
        assert sigma_wins < 100  # Much smaller than raw std (~60)
    
    def test_empty_input(self):
        """Empty list returns 0."""
        assert winsorized_sigma([]) == 0.0
    
    def test_constant_after_winsorizing(self):
        """If all values identical after trimming → zero variance."""
        residuals = [1, 1, 1, 1, 1]
        sigma = winsorized_sigma(residuals)
        assert sigma == 0.0


class TestSigmaOverHorizon:
    """Test horizon scaling formula σ_P = σ_day × √P."""
    
    def test_one_day_no_scaling(self):
        """P=1 day: no scaling."""
        sigma_day = 10.0
        sigma_horizon = sigma_over_horizon(1, sigma_day)
        
        assert sigma_horizon == sigma_day
    
    def test_four_days_double(self):
        """P=4 days: σ_P = σ_day × √4 = 2 × σ_day."""
        sigma_day = 10.0
        sigma_horizon = sigma_over_horizon(4, sigma_day)
        
        expected = 10.0 * 2.0  # √4 = 2
        assert abs(sigma_horizon - expected) < 0.01
    
    def test_nine_days_triple(self):
        """P=9 days: σ_P = 3 × σ_day."""
        sigma_day = 10.0
        sigma_horizon = sigma_over_horizon(9, sigma_day)
        
        expected = 10.0 * 3.0  # √9 = 3
        assert abs(sigma_horizon - expected) < 0.01
    
    def test_monotonically_increasing(self):
        """CRITICAL: σ_P must increase monotonically with P."""
        sigma_day = 10.0
        
        horizons = [1, 2, 3, 4, 5, 7, 10, 14, 21, 30]
        sigmas = [sigma_over_horizon(P, sigma_day) for P in horizons]
        
        # Check monotonicity: σ(P_i+1) > σ(P_i)
        for i in range(len(sigmas) - 1):
            assert sigmas[i + 1] > sigmas[i], f"Non-monotonic at P={horizons[i]}"
    
    def test_zero_daily_sigma(self):
        """Zero daily uncertainty → zero horizon uncertainty."""
        assert sigma_over_horizon(7, 0.0) == 0.0
    
    def test_zero_or_negative_period(self):
        """Invalid period returns 0."""
        assert sigma_over_horizon(0, 10.0) == 0.0
        assert sigma_over_horizon(-5, 10.0) == 0.0
    
    def test_large_horizon(self):
        """Large P: ensure formula holds."""
        sigma_day = 5.0
        P = 100
        
        sigma_horizon = sigma_over_horizon(P, sigma_day)
        expected = 5.0 * 10.0  # √100 = 10
        
        assert abs(sigma_horizon - expected) < 0.01


class TestCalculateForecastResiduals:
    """Test rolling window residual calculation."""
    
    def test_simple_forecast_residuals(self):
        """Generate residuals from known forecast function."""
        # Simple history: constant demand = 10
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(60)  # 60 days
        ]
        
        # Perfect forecast: always predicts 10
        def perfect_forecast(hist, horizon):
            return [10.0] * horizon
        
        residuals = calculate_forecast_residuals(
            history, perfect_forecast, window_weeks=4
        )
        
        # Perfect forecast → all residuals = 0
        assert len(residuals) > 0
        assert all(abs(r) < 0.01 for r in residuals)
    
    def test_biased_forecast_residuals(self):
        """Biased forecast → non-zero mean residuals."""
        # Actual demand: 10
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(60)
        ]
        
        # Biased forecast: always predicts 8 (under-forecasts by 2)
        def biased_forecast(hist, horizon):
            return [8.0] * horizon
        
        residuals = calculate_forecast_residuals(
            history, biased_forecast, window_weeks=4
        )
        
        # Residual = actual - predicted = 10 - 8 = 2
        assert len(residuals) > 0
        import statistics
        mean_residual = statistics.mean(residuals)
        assert abs(mean_residual - 2.0) < 0.1
    
    def test_insufficient_data(self):
        """Too little data → empty residuals."""
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(9)  # Only 9 days (need 8 weeks + 1 week = 63)
        ]
        
        def dummy_forecast(hist, horizon):
            return [10.0] * horizon
        
        residuals = calculate_forecast_residuals(
            history, dummy_forecast, window_weeks=8
        )
        
        assert residuals == []
    
    def test_forecast_function_integration(self):
        """Integration with actual forecast module."""
        from src.forecast import fit_forecast_model, predict
        
        # Generate synthetic data with DOW pattern
        history = []
        for i in range(90):  # 90 days ≈ 13 weeks
            d = date(2024, 1, 1) + timedelta(days=i)
            dow = d.weekday()
            # Mon=20, Tue-Fri=10, Weekend=5
            if dow == 0:
                qty = 20.0
            elif dow in [5, 6]:
                qty = 5.0
            else:
                qty = 10.0
            history.append({"date": d, "qty_sold": qty})
        
        def forecast_one_step(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        residuals = calculate_forecast_residuals(
            history, forecast_one_step, window_weeks=8
        )
        
        # Should have some residuals
        assert len(residuals) > 10
        
        # Residuals should be small (good forecast)
        sigma = robust_sigma(residuals)
        assert sigma < 5.0  # Most errors < 5 units


class TestEstimateDemandUncertainty:
    """Test end-to-end uncertainty estimation."""
    
    def test_estimate_with_mad(self):
        """Full workflow: history → residuals → sigma."""
        from src.forecast import fit_forecast_model, predict
        
        # Volatile demand
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0 + (i % 5)}
            for i in range(90)
        ]
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        sigma_day, residuals = estimate_demand_uncertainty(
            history, forecast_func, window_weeks=8, method="mad"
        )
        
        assert sigma_day > 0
        assert len(residuals) > 0
    
    def test_estimate_with_winsorized(self):
        """Test Winsorized method."""
        from src.forecast import fit_forecast_model, predict
        
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(90)
        ]
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        sigma_day, residuals = estimate_demand_uncertainty(
            history, forecast_func, method="winsorized"
        )
        
        assert sigma_day >= 0  # May be 0 for perfect forecast
    
    def test_invalid_method_raises(self):
        """Unknown method should raise ValueError."""
        history = [{"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0} for i in range(90)]
        
        def dummy_forecast(hist, horizon):
            return [10.0] * horizon
        
        try:
            estimate_demand_uncertainty(history, dummy_forecast, method="invalid")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unknown method" in str(e)


class TestSafetyStockForCSL:
    """Test safety stock calculation for CSL targets."""
    
    def test_95_percent_csl(self):
        """95% CSL: z = 1.645."""
        sigma_horizon = 20.0
        safety_stock = safety_stock_for_csl(sigma_horizon, target_csl=0.95)
        
        expected = 1.645 * 20.0  # 32.9
        assert abs(safety_stock - expected) < 0.1
    
    def test_99_percent_csl(self):
        """99% CSL: z = 2.326."""
        sigma_horizon = 20.0
        safety_stock = safety_stock_for_csl(sigma_horizon, target_csl=0.99)
        
        expected = 2.326 * 20.0  # 46.52
        assert abs(safety_stock - expected) < 0.1
    
    def test_90_percent_csl(self):
        """90% CSL: z = 1.282."""
        sigma_horizon = 20.0
        safety_stock = safety_stock_for_csl(sigma_horizon, target_csl=0.90)
        
        expected = 1.282 * 20.0  # 25.64
        assert abs(safety_stock - expected) < 0.1
    
    def test_zero_sigma(self):
        """Zero uncertainty → zero safety stock."""
        assert safety_stock_for_csl(0.0, target_csl=0.95) == 0.0
    
    def test_custom_csl_approximation(self):
        """Non-standard CSL uses closest z-score."""
        sigma_horizon = 10.0
        safety_stock = safety_stock_for_csl(sigma_horizon, target_csl=0.96)
        
        # Should approximate with closest (0.95 or 0.98)
        assert 16.0 < safety_stock < 21.0


class TestCalculateSafetyStock:
    """Test complete safety stock calculation workflow."""
    
    def test_full_workflow(self):
        """End-to-end: history → safety stock."""
        from src.forecast import fit_forecast_model, predict
        
        # Generate stable demand
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(90)
        ]
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        result = calculate_safety_stock(
            history=history,
            forecast_func=forecast_func,
            protection_period_days=7,
            target_csl=0.95,
            window_weeks=8,
            method="mad"
        )
        
        # Check all keys present
        assert "safety_stock" in result
        assert "sigma_daily" in result
        assert "sigma_horizon" in result
        assert "z_score" in result
        assert "n_residuals" in result
        assert "method" in result
        
        # Validate values
        assert result["safety_stock"] >= 0
        assert result["sigma_daily"] >= 0
        assert result["sigma_horizon"] >= result["sigma_daily"]  # Horizon ≥ daily
        assert result["protection_period_days"] == 7
        assert result["target_csl"] == 0.95
        assert result["method"] == "mad"
    
    def test_with_volatile_demand(self):
        """Volatile demand → higher safety stock."""
        from src.forecast import fit_forecast_model, predict
        
        # Volatile: ±50% variation
        import random
        random.seed(42)
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0 + random.uniform(-5, 5)}
            for i in range(90)
        ]
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        result = calculate_safety_stock(
            history, forecast_func, protection_period_days=7, target_csl=0.95
        )
        
        # Should have meaningful safety stock
        assert result["safety_stock"] > 0
        assert result["sigma_daily"] > 1.0  # Non-trivial uncertainty
    
    def test_longer_horizon_increases_safety_stock(self):
        """Longer protection period → higher safety stock."""
        from src.forecast import fit_forecast_model, predict
        
        # Use volatile demand to get non-zero sigma
        import random
        random.seed(123)
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0 + random.uniform(-2, 2)}
            for i in range(90)
        ]
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        result_3_days = calculate_safety_stock(
            history, forecast_func, protection_period_days=3
        )
        result_12_days = calculate_safety_stock(
            history, forecast_func, protection_period_days=12
        )
        
        # 12 days should have higher safety stock than 3 days
        assert result_12_days["safety_stock"] > result_3_days["safety_stock"]
        
        # Should scale roughly as √P: √12/√3 = 2
        if result_3_days["sigma_horizon"] > 0:  # Avoid 0/0
            ratio = result_12_days["sigma_horizon"] / result_3_days["sigma_horizon"]
            assert 1.8 < ratio < 2.2  # Approximately 2x


class TestOutlierResistanceIntegration:
    """Integration tests: verify outliers don't break safety stock calculation."""
    
    def test_single_huge_outlier_in_history(self):
        """Single massive outlier should not explode safety stock."""
        from src.forecast import fit_forecast_model, predict
        
        # Normal demand + 1 outlier
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(89)
        ]
        # Day 45: massive outlier (100x normal)
        history[44]["qty_sold"] = 1000.0
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        result = calculate_safety_stock(
            history, forecast_func, protection_period_days=7, method="mad"
        )
        
        # Safety stock should be reasonable (not 1000+ units)
        # For σ_day ≈ few units, P=7, CSL=95%:
        # Expected: < 50 units (very generous upper bound)
        assert result["safety_stock"] < 50.0
    
    def test_multiple_outliers_robustness(self):
        """Multiple outliers should still produce sensible safety stock."""
        from src.forecast import fit_forecast_model, predict
        
        # 85 normal days + 5 outliers (≈6% contamination)
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(85)
        ]
        outlier_days = [10, 25, 40, 60, 75]
        for idx in outlier_days:
            history.append({
                "date": date(2024, 1, 1) + timedelta(days=85 + outlier_days.index(idx)),
                "qty_sold": 500.0  # 50x normal
            })
        
        # Sort by date
        history.sort(key=lambda x: x["date"])
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        result = calculate_safety_stock(
            history, forecast_func, protection_period_days=7, method="mad"
        )
        
        # Should be dominated by normal demand
        assert result["safety_stock"] < 100.0


class TestEdgeCases:
    """Edge cases and boundary conditions."""
    
    def test_perfect_constant_demand(self):
        """Constant demand → zero uncertainty → zero safety stock."""
        from src.forecast import fit_forecast_model, predict
        
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(90)
        ]
        
        def forecast_func(hist, horizon):
            # Perfect forecast: always 10
            return [10.0] * horizon
        
        result = calculate_safety_stock(
            history, forecast_func, protection_period_days=7
        )
        
        # Perfect forecast → σ ≈ 0 → safety stock ≈ 0
        assert result["sigma_daily"] < 0.5
        assert result["safety_stock"] < 2.0
    
    def test_insufficient_data_graceful(self):
        """Too little data → returns zero (no crash)."""
        from src.forecast import fit_forecast_model, predict
        
        # Only 10 days (need 8 weeks + 1 week)
        history = [
            {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": 10.0}
            for i in range(10)
        ]
        
        def forecast_func(hist, horizon):
            model = fit_forecast_model(hist)
            return predict(model, horizon)
        
        result = calculate_safety_stock(
            history, forecast_func, protection_period_days=7
        )
        
        # Should return zeros gracefully (no residuals)
        assert result["safety_stock"] == 0.0
        assert result["sigma_daily"] == 0.0
        assert result["n_residuals"] == 0


class TestMonotonicity:
    """Verify monotonic properties."""
    
    def test_horizon_scaling_monotonic(self):
        """σ_horizon increases monotonically with P."""
        sigma_day = 5.0
        
        periods = range(1, 31)
        sigmas = [sigma_over_horizon(P, sigma_day) for P in periods]
        
        for i in range(len(sigmas) - 1):
            assert sigmas[i + 1] > sigmas[i], f"Non-monotonic at P={periods[i]}"
    
    def test_csl_increases_safety_stock(self):
        """Higher CSL → higher safety stock (for same σ)."""
        sigma_horizon = 20.0
        
        csl_levels = [0.80, 0.90, 0.95, 0.98, 0.99]
        safety_stocks = [
            safety_stock_for_csl(sigma_horizon, csl) for csl in csl_levels
        ]
        
        for i in range(len(safety_stocks) - 1):
            assert safety_stocks[i + 1] > safety_stocks[i], \
                f"Non-monotonic CSL at {csl_levels[i]}"
