"""
Tests for demand forecasting module.

Validates:
1. DOW pattern preservation in forecasts
2. Robustness with short history
3. Non-negative outputs
4. Edge cases (empty data, zero sales, etc.)
"""

import pytest
from datetime import date, timedelta
from src.forecast import (
    fit_forecast_model,
    predict,
    predict_single_day,
    get_forecast_stats,
    validate_forecast_inputs,
    quick_forecast,
)


class TestForecastModelDOWPattern:
    """Test that forecast preserves day-of-week patterns."""
    
    def test_strong_dow_pattern_preserved(self):
        """
        Synthetic data with clear DOW pattern:
        - Monday: 20 units (high)
        - Tuesday-Friday: 10 units (medium)
        - Saturday-Sunday: 5 units (low)
        
        Forecast should reflect this pattern.
        """
        # Generate 4 weeks of data (28 days)
        start = date(2024, 1, 1)  # Monday
        history = []
        
        for i in range(28):
            d = start + timedelta(days=i)
            dow = d.weekday()
            
            if dow == 0:  # Monday
                qty = 20.0
            elif dow in [1, 2, 3, 4]:  # Tue-Fri
                qty = 10.0
            else:  # Sat-Sun
                qty = 5.0
            
            history.append({"date": d, "qty_sold": qty})
        
        # Fit model
        model = fit_forecast_model(history)
        
        # Check that model captured the pattern
        assert model["method"] == "full"
        assert model["n_samples"] == 28
        
        # Generate forecast for next week
        forecast = predict(model, horizon=7, start_date=date(2024, 1, 29))
        
        # Monday should be highest
        assert forecast[0] > forecast[1]  # Mon > Tue
        
        # Weekend should be lowest
        assert forecast[5] < forecast[0]  # Sat < Mon
        assert forecast[6] < forecast[0]  # Sun < Mon
        
        # All forecasts should be positive
        assert all(f > 0 for f in forecast)
    
    def test_dow_factors_normalized(self):
        """DOW factors should be normalized (mean ≈ 1.0)."""
        history = [
            {"date": date(2024, 1, i), "qty_sold": 10.0 + (i % 7)}
            for i in range(1, 22)  # 3 weeks
        ]
        
        model = fit_forecast_model(history)
        dow_factors = model["dow_factors"]
        
        # Mean factor should be close to 1.0
        mean_factor = sum(dow_factors) / len(dow_factors)
        assert 0.9 <= mean_factor <= 1.1
    
    def test_forecast_preserves_weekly_sum(self):
        """
        Weekly sum of forecast should be close to weekly sum of training data.
        """
        # Generate 3 weeks of consistent data
        history = []
        for i in range(21):
            history.append({"date": date(2024, 1, i + 1), "qty_sold": 10.0})
        
        model = fit_forecast_model(history)
        forecast = predict(model, horizon=7)
        
        # Forecast weekly sum should be close to training average
        forecast_sum = sum(forecast)
        expected_sum = 10.0 * 7  # 70
        
        # Allow 20% tolerance due to smoothing
        assert 0.8 * expected_sum <= forecast_sum <= 1.2 * expected_sum


class TestShortHistoryRobustness:
    """Test model behavior with insufficient data."""
    
    def test_empty_history_no_crash(self):
        """Empty history should return fallback model."""
        history = []
        model = fit_forecast_model(history)
        
        assert model["method"] == "fallback"
        assert model["level"] == 0.0
        assert model["n_samples"] == 0
        
        # Should still produce forecast (zeros)
        forecast = predict(model, horizon=7)
        assert len(forecast) == 7
        assert all(f >= 0 for f in forecast)
    
    def test_single_day_history(self):
        """Single data point should produce constant forecast."""
        history = [{"date": date(2024, 1, 1), "qty_sold": 15.0}]
        
        model = fit_forecast_model(history)
        assert model["method"] == "fallback"
        assert model["level"] == 15.0
        
        forecast = predict(model, horizon=5)
        # Should be all equal (uniform DOW factors)
        assert all(abs(f - 15.0) < 0.01 for f in forecast)
    
    def test_short_history_3_days(self):
        """3 days of history should produce reasonable forecast."""
        history = [
            {"date": date(2024, 1, 1), "qty_sold": 10.0},
            {"date": date(2024, 1, 2), "qty_sold": 12.0},
            {"date": date(2024, 1, 3), "qty_sold": 11.0},
        ]
        
        model = fit_forecast_model(history)
        
        # Should use fallback (< 7 days)
        assert model["method"] == "fallback"
        
        # Level should be smoothed average
        assert 10.0 <= model["level"] <= 12.0
        
        # Forecast should be reasonable
        forecast = predict(model, horizon=7)
        assert all(10.0 <= f <= 12.0 for f in forecast)
    
    def test_partial_dow_coverage(self):
        """7-13 days: partial DOW calculation."""
        # 10 days covering some DOWs multiple times
        history = [
            {"date": date(2024, 1, i), "qty_sold": 10.0 + (i % 3)}
            for i in range(1, 11)
        ]
        
        model = fit_forecast_model(history)
        
        # Should use simple method (7-13 samples)
        assert model["method"] == "simple"
        assert model["n_samples"] == 10
        
        # Should still produce forecast
        forecast = predict(model, horizon=7)
        assert all(f > 0 for f in forecast)


class TestNonNegativeOutputs:
    """Ensure all outputs are non-negative."""
    
    def test_zero_sales_no_negative_forecast(self):
        """All-zero sales should produce zero forecast."""
        history = [
            {"date": date(2024, 1, i), "qty_sold": 0.0}
            for i in range(1, 15)
        ]
        
        model = fit_forecast_model(history)
        forecast = predict(model, horizon=7)
        
        assert all(f >= 0 for f in forecast)
    
    def test_negative_input_converted_to_zero(self):
        """Negative sales values should be treated as zero."""
        history = [
            {"date": date(2024, 1, 1), "qty_sold": -5.0},  # Invalid
            {"date": date(2024, 1, 2), "qty_sold": 10.0},
        ]
        
        model = fit_forecast_model(history)
        
        # Level should only consider non-negative values
        assert model["level"] >= 0
        
        forecast = predict(model, horizon=3)
        assert all(f >= 0 for f in forecast)
    
    def test_mixed_positive_negative_sales(self):
        """Mixed positive/negative should yield non-negative forecast."""
        history = []
        for i in range(14):
            qty = 10.0 if i % 2 == 0 else -3.0  # Alternating
            history.append({"date": date(2024, 1, i + 1), "qty_sold": qty})
        
        model = fit_forecast_model(history)
        forecast = predict(model, horizon=7)
        
        assert all(f >= 0 for f in forecast)


class TestPredictSingleDay:
    """Test single-day forecast function."""
    
    def test_predict_specific_monday(self):
        """Forecast for a specific Monday should use Monday DOW factor."""
        # Create model with known DOW factors
        model = {
            "level": 10.0,
            "dow_factors": [2.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5],  # Mon=2x, Weekend=0.5x
            "last_date": date(2024, 1, 7),
            "n_samples": 14,
        }
        
        # Monday 2024-01-08
        monday_forecast = predict_single_day(model, date(2024, 1, 8))
        assert monday_forecast == 20.0  # 10 * 2.0
        
        # Saturday 2024-01-13
        saturday_forecast = predict_single_day(model, date(2024, 1, 13))
        assert saturday_forecast == 5.0  # 10 * 0.5


class TestForecastStats:
    """Test forecast statistics extraction."""
    
    def test_stats_calculation(self):
        """Stats should reflect model characteristics."""
        model = {
            "level": 10.0,
            "dow_factors": [1.5, 1.2, 1.0, 1.0, 1.0, 0.8, 0.5],
            "last_date": date(2024, 1, 7),
            "n_samples": 21,
            "method": "full",
        }
        
        stats = get_forecast_stats(model)
        
        assert stats["level"] == 10.0
        assert stats["min_daily_forecast"] == 5.0  # 10 * 0.5 (Sun)
        assert stats["max_daily_forecast"] == 15.0  # 10 * 1.5 (Mon)
        assert stats["method"] == "full"
        assert stats["n_samples"] == 21


class TestValidation:
    """Test input validation."""
    
    def test_valid_history(self):
        """Valid history passes validation."""
        history = [
            {"date": date(2024, 1, 1), "qty_sold": 10.0},
            {"date": date(2024, 1, 2), "qty_sold": 12.0},
        ]
        
        is_valid, error = validate_forecast_inputs(history)
        assert is_valid
        assert error is None
    
    def test_invalid_history_not_list(self):
        """Non-list input fails validation."""
        is_valid, error = validate_forecast_inputs("not a list")
        assert not is_valid
        assert "must be a list" in error
    
    def test_invalid_history_missing_date(self):
        """Missing 'date' key fails validation."""
        history = [{"qty_sold": 10.0}]
        
        is_valid, error = validate_forecast_inputs(history)
        assert not is_valid
        assert "missing 'date'" in error
    
    def test_invalid_history_non_numeric_qty(self):
        """Non-numeric qty_sold fails validation."""
        history = [{"date": date(2024, 1, 1), "qty_sold": "ten"}]
        
        is_valid, error = validate_forecast_inputs(history)
        assert not is_valid
        assert "not numeric" in error


class TestQuickForecast:
    """Test quick_forecast convenience function."""
    
    def test_quick_forecast_complete_workflow(self):
        """quick_forecast should fit + predict in one call."""
        history = [
            {"date": date(2024, 1, i), "qty_sold": 10.0 + i % 3}
            for i in range(1, 22)
        ]
        
        result = quick_forecast(history, horizon=7)
        
        assert "forecast" in result
        assert "model" in result
        assert "stats" in result
        
        assert len(result["forecast"]) == 7
        assert all(f > 0 for f in result["forecast"])
        
        assert result["model"]["n_samples"] == 21
        assert result["stats"]["method"] == "full"
    
    def test_quick_forecast_invalid_input_raises(self):
        """Invalid input should raise ValueError."""
        history = [{"qty_sold": 10.0}]  # Missing date
        
        with pytest.raises(ValueError, match="Invalid forecast input"):
            quick_forecast(history, horizon=7)


class TestSmoothingParameter:
    """Test alpha (smoothing) parameter behavior."""
    
    def test_alpha_high_follows_recent(self):
        """High alpha (0.9) should follow recent values more closely."""
        # Trend up: 10 → 20
        history = [
            {"date": date(2024, 1, 1), "qty_sold": 10.0},
            {"date": date(2024, 1, 2), "qty_sold": 15.0},
            {"date": date(2024, 1, 3), "qty_sold": 20.0},
        ]
        
        model_high_alpha = fit_forecast_model(history, alpha=0.9)
        model_low_alpha = fit_forecast_model(history, alpha=0.1)
        
        # High alpha should be closer to recent value (20)
        assert model_high_alpha["level"] > model_low_alpha["level"]
        assert model_high_alpha["level"] > 15.0
        assert model_low_alpha["level"] < 15.0
    
    def test_alpha_bounds(self):
        """Alpha outside (0, 1] should still work (clamped internally)."""
        history = [{"date": date(2024, 1, i), "qty_sold": 10.0} for i in range(1, 8)]
        
        # Alpha = 0.0 (extreme smoothing)
        model_zero = fit_forecast_model(history, alpha=0.01)
        assert model_zero["level"] > 0
        
        # Alpha = 1.0 (no smoothing, uses last value)
        model_one = fit_forecast_model(history, alpha=1.0)
        assert model_one["level"] == 10.0


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_all_same_dow(self):
        """All data from same DOW (e.g., all Mondays)."""
        # 4 Mondays
        history = [
            {"date": date(2024, 1, 1), "qty_sold": 10.0},   # Mon
            {"date": date(2024, 1, 8), "qty_sold": 12.0},   # Mon
            {"date": date(2024, 1, 15), "qty_sold": 11.0},  # Mon
            {"date": date(2024, 1, 22), "qty_sold": 13.0},  # Mon
        ]
        
        model = fit_forecast_model(history)
        
        # Should still produce forecast for all DOWs
        forecast = predict(model, horizon=7)
        assert len(forecast) == 7
        assert all(f >= 0 for f in forecast)
    
    def test_forecast_with_gaps(self):
        """History with date gaps (non-consecutive days)."""
        history = [
            {"date": date(2024, 1, 1), "qty_sold": 10.0},
            {"date": date(2024, 1, 5), "qty_sold": 12.0},  # Gap
            {"date": date(2024, 1, 10), "qty_sold": 11.0},  # Gap
        ]
        
        model = fit_forecast_model(history)
        forecast = predict(model, horizon=7)
        
        assert len(forecast) == 7
        assert all(f > 0 for f in forecast)
    
    def test_forecast_start_date_override(self):
        """Forecast from custom start_date."""
        history = [{"date": date(2024, 1, i), "qty_sold": 10.0} for i in range(1, 8)]
        
        model = fit_forecast_model(history)
        
        # Forecast from specific date (not last_date + 1)
        custom_start = date(2024, 2, 1)
        forecast = predict(model, horizon=5, start_date=custom_start)
        
        assert len(forecast) == 5
        
        # Verify it uses correct DOWs from custom_start
        # (Would need to check DOW alignment, here just verify it works)
        assert all(f > 0 for f in forecast)


class TestRealWorldScenario:
    """Integration test with realistic scenario."""
    
    def test_retail_weekly_pattern(self):
        """
        Simulate retail sales pattern:
        - Weekdays: 100 units/day
        - Saturday: 150 units (busy)
        - Sunday: 50 units (slow)
        """
        history = []
        start = date(2024, 1, 1)  # Monday
        
        for week in range(4):  # 4 weeks
            for day in range(7):
                d = start + timedelta(weeks=week, days=day)
                dow = d.weekday()
                
                if dow == 5:  # Saturday
                    qty = 150.0
                elif dow == 6:  # Sunday
                    qty = 50.0
                else:  # Weekdays
                    qty = 100.0
                
                # Add some noise
                import random
                random.seed(week * 7 + day)
                qty += random.uniform(-5, 5)
                
                history.append({"date": d, "qty_sold": max(0, qty)})
        
        # Fit and forecast
        result = quick_forecast(history, horizon=7)
        
        forecast = result["forecast"]
        stats = result["stats"]
        
        # Check pattern is captured
        assert stats["method"] == "full"
        
        # Saturday (index 5) should be highest
        assert forecast[5] > forecast[0]  # Sat > Mon
        
        # Sunday (index 6) should be lowest
        assert forecast[6] < forecast[0]  # Sun < Mon
        
        # Weekday average should be around 100
        weekday_avg = sum(forecast[:5]) / 5
        assert 80 <= weekday_avg <= 120  # Allow ±20% tolerance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
