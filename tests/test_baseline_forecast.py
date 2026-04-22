"""
Unit tests for baseline_forecast() and baseline_forecast_mc().

Tests verify:
1. Baseline filters out promo_flag=1 days from training
2. Baseline filters out censored days (OOS) from training
3. Baseline generates per-day forecast for all horizon dates
4. Monte Carlo baseline variant works similarly
5. Invariant: If no promos/censored days exist, baseline == full forecast
"""

from datetime import date, timedelta
from dataclasses import dataclass
import pytest

from src.forecast import baseline_forecast, baseline_forecast_mc, fit_forecast_model, predict_single_day
from src.domain.models import SalesRecord, Transaction, EventType


@dataclass
class MockTransaction:
    """Minimal transaction for censoring tests."""
    sku: str
    date: date
    event: EventType  # Corrected field name
    qty: int


def test_baseline_forecast_filters_promo_days():
    """Test that baseline_forecast excludes promo_flag=1 days from training."""
    sku_id = "TEST001"
    
    # Create sales: 10 non-promo days + 5 promo days
    sales = []
    for i in range(10):
        sales.append(SalesRecord(
            sku=sku_id,
            date=date(2026, 1, 1) + timedelta(days=i),
            qty_sold=10.0,
            promo_flag=0,  # Non-promo
        ))
    
    for i in range(5):
        sales.append(SalesRecord(
            sku=sku_id,
            date=date(2026, 1, 11) + timedelta(days=i),
            qty_sold=50.0,  # Much higher during promo
            promo_flag=1,  # PROMO
        ))
    
    # Generate baseline forecast (should ignore promo days)
    horizon = [date(2026, 1, 20) + timedelta(days=i) for i in range(7)]
    
    baseline = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=sales,
        transactions=[],
        asof_date=date(2026, 1, 20),
    )
    
    # Baseline should train only on 10 days with qty_sold=10
    # Expected: level ≈ 10, forecast ≈ 10 per day
    assert len(baseline) == 7
    for forecast_date, value in baseline.items():
        assert value >= 8.0  # Allow DOW variation
        assert value <= 12.0
        # Should NOT be 50 (promo level) or 30 (average of all days)


def test_baseline_forecast_filters_censored_days():
    """Test that baseline_forecast excludes censored days (OOS) from training."""
    sku_id = "TEST002"
    
    # Create sales: 10 normal days + 3 OOS days (sales=0)
    sales = []
    for i in range(10):
        sales.append(SalesRecord(
            sku=sku_id,
            date=date(2026, 1, 1) + timedelta(days=i),
            qty_sold=15.0,
            promo_flag=0,
        ))
    
    # OOS days: sales=0 (will be detected as censored by is_day_censored)
    oos_dates = [date(2026, 1, 11), date(2026, 1, 12), date(2026, 1, 13)]
    for oos_date in oos_dates:
        sales.append(SalesRecord(
            sku=sku_id,
            date=oos_date,
            qty_sold=0.0,  # Zero sales (censored)
            promo_flag=0,
        ))
    
    # Create transactions to simulate OOS (on_hand=0 on those dates)
    # For simplicity, we'll use SNAPSHOT with qty=0 to force OOS state
    transactions = [
        Transaction(sku=sku_id, date=date(2026, 1, 10), event=EventType.SNAPSHOT, qty=0, note="OOS start")
    ]
    
    horizon = [date(2026, 1, 20) + timedelta(days=i) for i in range(7)]
    
    baseline = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        asof_date=date(2026, 1, 20),
    )
    
    # Baseline should train on 10 days with qty_sold=15 (exclude OOS days)
    # Expected: level ≈ 15, forecast ≈ 15 per day
    assert len(baseline) == 7
    for forecast_date, value in baseline.items():
        assert value >= 13.0  # Allow smoothing variation
        assert value <= 17.0
        # Should NOT be 11.5 (average including OOS zeros)


def test_baseline_forecast_returns_per_day_predictions():
    """Test that baseline_forecast returns exactly one prediction per horizon date."""
    sku_id = "TEST003"
    
    sales = [
        SalesRecord(sku=sku_id, date=date(2026, 1, i), qty_sold=20.0, promo_flag=0)
        for i in range(1, 15)
    ]
    
    horizon = [date(2026, 2, 1) + timedelta(days=i) for i in range(14)]
    
    baseline = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=sales,
        transactions=[],
        asof_date=date(2026, 1, 31),
    )
    
    # Check that all horizon dates are present
    assert len(baseline) == 14
    for forecast_date in horizon:
        assert forecast_date in baseline
        assert isinstance(baseline[forecast_date], float)
        assert baseline[forecast_date] >= 0.0  # Non-negative


def test_baseline_forecast_empty_history():
    """Test baseline_forecast with no sales data."""
    sku_id = "EMPTY_SKU"
    
    horizon = [date(2026, 2, 1) + timedelta(days=i) for i in range(7)]
    
    baseline = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=[],
        transactions=[],
        asof_date=date(2026, 1, 31),
    )
    
    # Should return zeros (fallback model)
    assert len(baseline) == 7
    for forecast_date, value in baseline.items():
        assert value == 0.0


def test_baseline_forecast_all_days_filtered():
    """Test baseline_forecast when ALL days are promo or censored."""
    sku_id = "TEST_ALL_FILTERED"
    
    # All sales are promo
    sales = [
        SalesRecord(sku=sku_id, date=date(2026, 1, i), qty_sold=30.0, promo_flag=1)
        for i in range(1, 10)
    ]
    
    horizon = [date(2026, 2, 1) + timedelta(days=i) for i in range(7)]
    
    baseline = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=sales,
        transactions=[],
        asof_date=date(2026, 1, 31),
    )
    
    # Should return zeros (no training data after filtering)
    assert len(baseline) == 7
    for forecast_date, value in baseline.items():
        assert value == 0.0


def test_baseline_forecast_mc_filters_promo():
    """Test that baseline_forecast_mc (Monte Carlo variant) also filters promo days."""
    sku_id = "TEST_MC_001"
    
    # 20 non-promo days + 5 promo days
    sales = []
    for i in range(20):
        sales.append(SalesRecord(
            sku=sku_id,
            date=date(2026, 1, 1) + timedelta(days=i),
            qty_sold=12.0,
            promo_flag=0,
        ))
    
    for i in range(5):
        sales.append(SalesRecord(
            sku=sku_id,
            date=date(2026, 1, 21) + timedelta(days=i),
            qty_sold=60.0,  # High promo sales
            promo_flag=1,
        ))
    
    horizon = [date(2026, 2, 1) + timedelta(days=i) for i in range(7)]
    
    baseline_mc = baseline_forecast_mc(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=sales,
        transactions=[],
        asof_date=date(2026, 1, 31),
        distribution="empirical",
        n_simulations=100,
        random_seed=42,
    )
    
    # MC baseline should train on 20 days with qty_sold=12
    # Expected: forecast ≈ 12 per day (with MC sampling variation)
    assert len(baseline_mc) == 7
    for forecast_date, value in baseline_mc.items():
        assert value >= 8.0  # Allow MC simulation variance
        assert value <= 16.0
        # Should NOT be 24 (average of all 25 days)


def test_baseline_forecast_invariant_no_promo():
    """
    INVARIANT TEST: If no promo days exist, baseline should match full forecast.
    
    This is the critical invariant: baseline represents "normal" demand,
    so when promo_flag=0 for all days, baseline == forecast.
    """
    sku_id = "INVARIANT_TEST"
    
    # All sales are non-promo
    sales = [
        SalesRecord(sku=sku_id, date=date(2026, 1, i), qty_sold=18.0, promo_flag=0)
        for i in range(1, 22)
    ]
    
    horizon = [date(2026, 2, 1) + timedelta(days=i) for i in range(7)]
    
    # Baseline forecast (filters promo_flag=1, but there are none)
    baseline = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=sales,
        transactions=[],
        asof_date=date(2026, 1, 31),
    )
    
    # Full forecast (uses all data, same as baseline in this case)
    history = [{"date": s.date, "qty_sold": s.qty_sold} for s in sales if s.sku == sku_id]
    model = fit_forecast_model(history)
    full_forecast = {
        fd: predict_single_day(model, fd)
        for fd in horizon
    }
    
    # Baseline should equal full forecast (within floating point tolerance)
    for forecast_date in horizon:
        assert abs(baseline[forecast_date] - full_forecast[forecast_date]) < 0.01


def test_baseline_forecast_dow_patterns():
    """Test that baseline_forecast respects day-of-week patterns."""
    sku_id = "DOW_TEST"
    
    # Create sales with clear DOW pattern: Monday=30, other days=10
    sales = []
    for i in range(28):  # 4 weeks
        sale_date = date(2026, 1, 1) + timedelta(days=i)  # Jan 1, 2026 is Thursday
        qty = 30.0 if sale_date.weekday() == 0 else 10.0  # Monday = 30
        sales.append(SalesRecord(
            sku=sku_id,
            date=sale_date,
            qty_sold=qty,
            promo_flag=0,
        ))
    
    # Forecast for next week (includes a Monday)
    horizon = [date(2026, 2, 2) + timedelta(days=i) for i in range(7)]  # Feb 2 = Monday
    
    baseline = baseline_forecast(
        sku_id=sku_id,
        horizon_dates=horizon,
        sales_records=sales,
        transactions=[],
        asof_date=date(2026, 2, 1),
    )
    
    # Check that Monday (Feb 2) has higher forecast
    monday_forecast = baseline[date(2026, 2, 2)]
    tuesday_forecast = baseline[date(2026, 2, 3)]
    
    assert monday_forecast > tuesday_forecast * 1.5  # Monday should be significantly higher


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
