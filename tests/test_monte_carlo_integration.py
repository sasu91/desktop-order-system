"""
Integration tests for Monte Carlo forecast system.

Tests:
1. Monte Carlo forecast engine (forecast.py)
2. Settings persistence (MC global parameters)
3. SKU override persistence (MC per-SKU parameters)
4. Order workflow integration (method selection + parameter resolution)
"""
import pytest
from datetime import date, timedelta
import tempfile
import os
import json
from pathlib import Path

from src.domain.models import SKU, DemandVariability, SalesRecord
from src.persistence.csv_layer import CSVLayer
from src.forecast import monte_carlo_forecast, monte_carlo_forecast_with_stats
from src.workflows.order import OrderWorkflow


def test_monte_carlo_forecast_empirical():
    """Test Monte Carlo forecast with empirical bootstrap."""
    # Historical sales (30 days, ~10 units/day with some variation)
    history = [
        {"date": date(2024, 1, i), "qty_sold": 10 + (i % 3)}
        for i in range(1, 31)
    ]
    
    # Run MC forecast (7-day horizon)
    forecast_values = monte_carlo_forecast(
        history=history,
        horizon_days=7,
        distribution="empirical",
        n_simulations=1000,
        random_seed=42,
        output_stat="mean",
    )
    
    # Validate output
    assert len(forecast_values) == 7
    assert all(v >= 0 for v in forecast_values)
    
    # Mean should be close to historical average (~10-12)
    avg_forecast = sum(forecast_values) / len(forecast_values)
    assert 8 <= avg_forecast <= 14  # Reasonable range


def test_monte_carlo_forecast_normal():
    """Test Monte Carlo forecast with normal distribution."""
    history = [
        {"date": date(2024, 1, i), "qty_sold": 20 + i % 5}
        for i in range(1, 31)
    ]
    
    forecast_values = monte_carlo_forecast(
        history=history,
        horizon_days=14,
        distribution="normal",
        n_simulations=500,
        random_seed=123,
        output_stat="percentile",
        output_percentile=80,
    )
    
    assert len(forecast_values) == 14
    assert all(v >= 0 for v in forecast_values)
    
    # P80 should be higher than mean (~20-25)
    avg_forecast = sum(forecast_values) / len(forecast_values)
    assert 18 <= avg_forecast <= 30


def test_monte_carlo_forecast_with_stats():
    """Test Monte Carlo forecast with full statistical output."""
    history = [
        {"date": date(2024, 1, i), "qty_sold": 15}
        for i in range(1, 21)
    ]
    
    result = monte_carlo_forecast_with_stats(
        history=history,
        horizon_days=7,
        distribution="empirical",
        n_simulations=1000,
        random_seed=42,
    )
    
    # Validate structure
    assert "mean" in result
    assert "median" in result
    assert "p10" in result
    assert "p90" in result
    
    # All outputs should be length 7
    assert len(result["mean"]) == 7
    assert len(result["p90"]) == 7
    
    # P90 should be >= mean >= P10
    for i in range(7):
        assert result["p90"][i] >= result["mean"][i] >= result["p10"][i]


def test_monte_carlo_settings_persistence():
    """Test MC global parameters persist in settings.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_layer = CSVLayer(data_dir=Path(tmpdir))
        
        # Read default settings
        settings = csv_layer.read_settings()
        
        # Verify MC section exists with defaults
        assert "monte_carlo" in settings
        mc = settings["monte_carlo"]
        
        assert mc["distribution"]["value"] == "empirical"
        assert mc["n_simulations"]["value"] == 1000
        assert mc["random_seed"]["value"] == 42
        assert mc["output_stat"]["value"] == "mean"
        assert mc["output_percentile"]["value"] == 80
        assert mc["horizon_mode"]["value"] == "auto"
        assert mc["horizon_days"]["value"] == 14
        
        # Modify and save
        settings["monte_carlo"]["distribution"]["value"] = "lognormal"
        settings["monte_carlo"]["n_simulations"]["value"] = 5000
        csv_layer.write_settings(settings)
        
        # Re-read and verify
        settings2 = csv_layer.read_settings()
        assert settings2["monte_carlo"]["distribution"]["value"] == "lognormal"
        assert settings2["monte_carlo"]["n_simulations"]["value"] == 5000


def test_sku_mc_override_persistence():
    """Test per-SKU MC override fields persist in CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_layer = CSVLayer(data_dir=Path(tmpdir))
        
        # Create SKU with MC overrides
        sku = SKU(
            sku="TEST001",
            description="Test Product",
            ean=None,
            moq=10,
            pack_size=5,
            lead_time_days=7,
            review_period=7,
            safety_stock=50,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=100,
            demand_variability=DemandVariability.HIGH,
            oos_boost_percent=0.0,
            oos_detection_mode="",
            oos_popup_preference="ask",
            forecast_method="monte_carlo",  # Override to MC
            mc_distribution="lognormal",
            mc_n_simulations=2000,
            mc_random_seed=999,
            mc_output_stat="percentile",
            mc_output_percentile=90,
            mc_horizon_mode="custom",
            mc_horizon_days=21,
        )
        
        csv_layer.write_sku(sku)
        
        # Re-read SKU
        skus = csv_layer.read_skus()
        assert len(skus) == 1
        
        sku2 = skus[0]
        assert sku2.forecast_method == "monte_carlo"
        assert sku2.mc_distribution == "lognormal"
        assert sku2.mc_n_simulations == 2000
        assert sku2.mc_random_seed == 999
        assert sku2.mc_output_stat == "percentile"
        assert sku2.mc_output_percentile == 90
        assert sku2.mc_horizon_mode == "custom"
        assert sku2.mc_horizon_days == 21


def test_order_workflow_mc_integration():
    """Test order workflow uses Monte Carlo when configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_layer = CSVLayer(data_dir=Path(tmpdir))
        
        # Set global forecast method to monte_carlo
        settings = csv_layer.read_settings()
        settings["reorder_engine"]["forecast_method"] = {"value": "monte_carlo"}
        csv_layer.write_settings(settings)
        
        # Create SKU without MC override (will use global)
        sku = SKU(
            sku="MC001",
            description="Monte Carlo Test SKU",
            ean=None,
            moq=1,
            pack_size=1,
            lead_time_days=7,
            review_period=7,
            safety_stock=10,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=50,
            demand_variability=DemandVariability.STABLE,
            forecast_method="",  # Use global
        )
        csv_layer.write_sku(sku)
        
        # Add historical sales (30 days @ ~20 units/day)
        for i in range(1, 31):
            sale = SalesRecord(
                date=date(2024, 1, i),
                sku="MC001",
                qty_sold=20 + (i % 5)
            )
            csv_layer.write_sales([sale])
        
        # Create order workflow
        workflow = OrderWorkflow(csv_layer, lead_time_days=7)
        
        # Generate proposal (should use Monte Carlo)
        from src.domain.models import Stock
        stock = Stock(sku="MC001", on_hand=50, on_order=0, unfulfilled_qty=0)
        
        proposal = workflow.generate_proposal(
            sku="MC001",
            description="Monte Carlo Test SKU",
            current_stock=stock,
            daily_sales_avg=21.0,  # Approximate average
            sku_obj=sku,
        )
        
        # Verify proposal generated (exact qty will vary due to MC randomness)
        assert proposal.sku == "MC001"
        assert proposal.proposed_qty >= 0
        
        # With 14 days horizon @ 21 units/day, target ~294 units
        # Current IP = 50, so should propose ~244 units
        # (exact value depends on MC simulation)
        assert 150 <= proposal.proposed_qty <= 400  # Reasonable range


def test_order_workflow_simple_vs_mc():
    """Test order workflow switches between simple and MC forecast."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_layer = CSVLayer(data_dir=Path(tmpdir))
        
        # Create SKU
        sku = SKU(
            sku="TEST002",
            description="Dual Method Test",
            ean=None,
            moq=1,
            pack_size=1,
            lead_time_days=7,
            review_period=7,
            safety_stock=10,
            forecast_method="",  # Will use global setting
        )
        csv_layer.write_sku(sku)
        
        # Add sales history
        for i in range(1, 31):
            csv_layer.write_sales([SalesRecord(date=date(2024, 1, i), sku="TEST002", qty_sold=15)])
        
        from src.domain.models import Stock
        stock = Stock(sku="TEST002", on_hand=100, on_order=0, unfulfilled_qty=0)
        
        # Test 1: Simple forecast
        settings = csv_layer.read_settings()
        settings["reorder_engine"]["forecast_method"] = {"value": "simple"}
        csv_layer.write_settings(settings)
        
        workflow1 = OrderWorkflow(csv_layer, lead_time_days=7)
        proposal1 = workflow1.generate_proposal(
            sku="TEST002",
            description="Dual Method Test",
            current_stock=stock,
            daily_sales_avg=15.0,
            sku_obj=sku,
        )
        
        # Test 2: Monte Carlo forecast
        settings["reorder_engine"]["forecast_method"] = {"value": "monte_carlo"}
        csv_layer.write_settings(settings)
        
        workflow2 = OrderWorkflow(csv_layer, lead_time_days=7)
        proposal2 = workflow2.generate_proposal(
            sku="TEST002",
            description="Dual Method Test",
            current_stock=stock,
            daily_sales_avg=15.0,
            sku_obj=sku,
        )
        
        # Both should propose reasonable quantities (may differ slightly)
        # Target S = 15 units/day × 14 days + 10 safety = 220
        # IP = 100, so propose ~120
        assert 80 <= proposal1.proposed_qty <= 200
        assert 80 <= proposal2.proposed_qty <= 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
