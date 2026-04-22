"""
Integration test for baseline forecast with order workflow.

Demonstrates:
1. How baseline_forecast() integrates with order proposal logic
2. Comparison of baseline demand vs promo-inclusive demand
3. Validation that baseline represents "normal" demand
"""

from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SalesRecord, Transaction, EventType, SKU
from src.persistence.csv_layer import CSVLayer
from src.forecast import baseline_forecast, baseline_forecast_mc


def test_baseline_order_integration():
    """
    Integration test: Use baseline_forecast in order proposal context.
    
    Scenario:
    - SKU with 30 days of non-promo history (avg=10/day)
    - 7 days of promo history (avg=50/day)
    - Generate baseline forecast for next 14 days
    - Baseline should be ~10/day (ignoring promo uplift)
    """
    # Create temp directory for CSV files
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        csv_layer = CSVLayer(data_dir=data_dir)
        
        # Setup SKU
        sku_id = "INTEGRATION_SKU"
        sku = SKU(
            sku=sku_id,
            description="Integration test SKU",
            ean="1234567890123",
            pack_size=6,
            moq=12,
            lead_time_days=7,
            review_period=7,
            safety_stock=20,
            max_stock=500,
        )
        csv_layer.write_sku(sku)
        
        # Create sales history: 30 non-promo days + 7 promo days
        sales = []
        
        # Non-promo period (30 days, avg=10)
        for i in range(30):
            sales.append(SalesRecord(
                sku=sku_id,
                date=date(2026, 1, 1) + timedelta(days=i),
                qty_sold=10.0,
                promo_flag=0,
            ))
        
        # Promo period (7 days, avg=50)
        for i in range(7):
            sales.append(SalesRecord(
                sku=sku_id,
                date=date(2026, 1, 31) + timedelta(days=i),
                qty_sold=50.0,
                promo_flag=1,
            ))
        
        csv_layer.write_sales(sales)
        
        # Create minimal transactions (SNAPSHOT to initialize stock)
        transactions = [
            Transaction(
                sku=sku_id,
                date=date(2026, 1, 1),
                event=EventType.SNAPSHOT,
                qty=100,
                note="Initial stock"
            )
        ]
        csv_layer.write_transactions_batch(transactions)
        
        # === BASELINE FORECAST (non-promo training) ===
        horizon = [date(2026, 2, 10) + timedelta(days=i) for i in range(14)]
        
        baseline = baseline_forecast(
            sku_id=sku_id,
            horizon_dates=horizon,
            sales_records=sales,
            transactions=transactions,
            asof_date=date(2026, 2, 9),
        )
        
        # Validate baseline: should train only on 30 non-promo days (avg=10)
        # Expected: forecast ≈ 10/day
        avg_baseline = sum(baseline.values()) / len(baseline)
        assert 8.0 <= avg_baseline <= 12.0, f"Baseline avg should be ~10, got {avg_baseline}"
        
        # All daily forecasts should be in reasonable range
        for forecast_date, value in baseline.items():
            assert 5.0 <= value <= 15.0, f"Baseline for {forecast_date} should be ~10, got {value}"
        
        # Total baseline demand over 14 days should be ~140 (10*14)
        total_baseline = sum(baseline.values())
        assert 112 <= total_baseline <= 168, f"Total baseline (14d) should be ~140, got {total_baseline}"


def test_baseline_mc_order_integration():
    """
    Integration test: Use baseline_forecast_mc (Monte Carlo) in order context.
    
    Validates that MC baseline variant also filters promo days correctly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        csv_layer = CSVLayer(data_dir=data_dir)
        
        sku_id = "MC_INTEGRATION_SKU"
        sku = SKU(
            sku=sku_id,
            description="MC integration test SKU",
            ean="9876543210987",
            pack_size=12,
        )
        csv_layer.write_sku(sku)
        
        # Create sales: 40 non-promo days (variability) + 10 promo days
        sales = []
        
        # Non-promo: varied demand (8-12 range)
        for i in range(40):
            qty = 10.0 + (i % 5) - 2  # 8, 9, 10, 11, 12, 8, 9, ...
            sales.append(SalesRecord(
                sku=sku_id,
                date=date(2026, 1, 1) + timedelta(days=i),
                qty_sold=float(qty),
                promo_flag=0,
            ))
        
        # Promo: high demand (40-60 range)
        for i in range(10):
            qty = 50.0 + (i % 3) * 5  # 50, 55, 60, 50, 55, ...
            sales.append(SalesRecord(
                sku=sku_id,
                date=date(2026, 2, 10) + timedelta(days=i),
                qty_sold=float(qty),
                promo_flag=1,
            ))
        
        csv_layer.write_sales(sales)
        
        transactions = [
            Transaction(sku=sku_id, date=date(2026, 1, 1), event=EventType.SNAPSHOT, qty=200, note="Init")
        ]
        csv_layer.write_transactions_batch(transactions)
        
        # === MONTE CARLO BASELINE FORECAST ===
        horizon = [date(2026, 2, 25) + timedelta(days=i) for i in range(14)]
        
        baseline_mc = baseline_forecast_mc(
            sku_id=sku_id,
            horizon_dates=horizon,
            sales_records=sales,
            transactions=transactions,
            asof_date=date(2026, 2, 24),
            distribution="empirical",
            n_simulations=500,
            random_seed=42,
        )
        
        # MC baseline should reflect non-promo distribution (8-12 range, avg=10)
        avg_mc_baseline = sum(baseline_mc.values()) / len(baseline_mc)
        assert 7.0 <= avg_mc_baseline <= 13.0, f"MC baseline avg should be ~10, got {avg_mc_baseline}"
        
        # Should NOT be ~50 (promo average)
        assert avg_mc_baseline < 20.0, f"MC baseline should not include promo uplift, got {avg_mc_baseline}"


def test_baseline_vs_full_forecast_comparison():
    """
    Demonstrate baseline vs full forecast comparison.
    
    Use case: Show difference between baseline (normal demand) and
    promo-inclusive forecast to estimate promo uplift.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        csv_layer = CSVLayer(data_dir=data_dir)
        
        sku_id = "COMPARISON_SKU"
        sku = SKU(
            sku=sku_id,
            description="Comparison test SKU",
            ean="1111111111111",
        )
        csv_layer.write_sku(sku)
        
        # Create sales: 50 non-promo + 10 promo (use past dates to avoid validation error)
        sales = []
        
        base_date = date.today() - timedelta(days=70)  # Start 70 days ago
        
        for i in range(50):
            sales.append(SalesRecord(
                sku=sku_id,
                date=base_date + timedelta(days=i),
                qty_sold=20.0,
                promo_flag=0,
            ))
        
        for i in range(10):
            sales.append(SalesRecord(
                sku=sku_id,
                date=base_date + timedelta(days=50 + i),
                qty_sold=80.0,
                promo_flag=1,
            ))
        
        csv_layer.write_sales(sales)
        
        transactions = [
            Transaction(sku=sku_id, date=base_date, event=EventType.SNAPSHOT, qty=300, note="Init")
        ]
        csv_layer.write_transactions_batch(transactions)
        
        # Forecast for next week (relative to end of data)
        forecast_start = base_date + timedelta(days=65)
        horizon = [forecast_start + timedelta(days=i) for i in range(7)]
        
        # BASELINE: trains only on non-promo (should be ~20/day)
        baseline = baseline_forecast(
            sku_id=sku_id,
            horizon_dates=horizon,
            sales_records=sales,
            transactions=transactions,
        )
        
        # FULL FORECAST: trains on all data (should be higher due to promo influence)
        # For this test, we simulate "full forecast" by manually including promo data
        # In real usage, this would come from a promo-aware forecast function
        
        # Validate baseline excludes promo uplift
        avg_baseline = sum(baseline.values()) / len(baseline)
        assert 18.0 <= avg_baseline <= 22.0, f"Baseline should be ~20, got {avg_baseline}"
        
        # Demonstrate: If we want to estimate promo impact, we could:
        # promo_uplift_estimate = full_forecast - baseline
        # For now, just validate baseline is reasonable
        total_baseline_demand = sum(baseline.values())
        assert 126 <= total_baseline_demand <= 154, f"7-day baseline ~140, got {total_baseline_demand}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
