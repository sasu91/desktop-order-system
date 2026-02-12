"""
Unit tests for promo-adjusted forecast feature.

Tests cover:
- Baseline vs adjusted forecast calculation
- Promo adjustment enable/disable logic
- Uplift factor application
- Smoothing (ramp-in/ramp-out) at promo calendar borders
- Store_id scoping (global only per user decision)
- Error handling and fallback to baseline
"""

import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, SalesRecord, PromoWindow, Transaction, EventType
from src.persistence.csv_layer import CSVLayer
from src.forecast import promo_adjusted_forecast


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for tests."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def csv_layer(temp_data_dir):
    """Create CSVLayer with test data."""
    layer = CSVLayer(data_dir=temp_data_dir)
    
    # Create test SKUs
    skus = [
        SKU(sku="SKU001", description="Test Product", ean="", category="A", department="Dept1"),
        SKU(sku="SKU002", description="Another Product", ean="", category="B", department="Dept2"),
    ]
    for sku in skus:
        layer.write_sku(sku)
    
    # Create test sales records (baseline history for forecast)
    today = date.today()
    sales = []
    for i in range(30, 0, -1):  # 30 days of history
        sales.append(SalesRecord(
            date=today - timedelta(days=i),
            sku="SKU001",
            qty_sold=10,  # Stable 10 units/day
            promo_flag=0,  # Non-promo baseline
        ))
    layer.write_sales(sales)
    
    # Transactions file auto-created (empty is fine for these tests)
    
    # Create test promo windows
    promo_windows = [
        PromoWindow(
            sku="SKU001",
            start_date=today + timedelta(days=5),
            end_date=today + timedelta(days=10),
            store_id=None,  # Global promo
        ),
    ]
    layer.write_promo_calendar(promo_windows)
    
    return layer


@pytest.fixture
def settings_promo_disabled(csv_layer):
    """Settings with promo adjustment disabled."""
    settings = csv_layer.read_settings()
    settings["promo_adjustment"]["enabled"]["value"] = False
    csv_layer.write_settings(settings)
    return settings


@pytest.fixture
def settings_promo_enabled(csv_layer):
    """Settings with promo adjustment enabled, smoothing disabled."""
    settings = csv_layer.read_settings()
    settings["promo_adjustment"]["enabled"]["value"] = True
    settings["promo_adjustment"]["smoothing_enabled"]["value"] = False
    csv_layer.write_settings(settings)
    return settings


@pytest.fixture
def settings_promo_smoothing_enabled(csv_layer):
    """Settings with promo adjustment + smoothing enabled."""
    settings = csv_layer.read_settings()
    settings["promo_adjustment"]["enabled"]["value"] = True
    settings["promo_adjustment"]["smoothing_enabled"]["value"] = True
    settings["promo_adjustment"]["ramp_in_days"]["value"] = 2
    settings["promo_adjustment"]["ramp_out_days"]["value"] = 2
    csv_layer.write_settings(settings)
    return settings


def test_promo_adjustment_disabled(csv_layer, settings_promo_disabled):
    """
    Invariant Test 1: If promo_adjustment.enabled = false, adjusted = baseline for all dates.
    """
    today = date.today()
    horizon = [today + timedelta(days=i) for i in range(1, 15)]  # 14 days ahead
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        settings=settings_promo_disabled,
    )
    
    # Verify adjustment is disabled
    assert result["adjustment_enabled"] is False
    
    # Verify adjusted = baseline for all dates
    for forecast_date in horizon:
        baseline = result["baseline_forecast"][forecast_date]
        adjusted = result["adjusted_forecast"][forecast_date]
        assert adjusted == baseline, f"Date {forecast_date}: adjusted ({adjusted}) != baseline ({baseline}) when disabled"
        
        # Verify no uplift applied
        assert result["uplift_factor"][forecast_date] == 1.0
        assert result["promo_active"][forecast_date] is False  # Reported as False when disabled


def test_no_promo_in_horizon(csv_layer, settings_promo_enabled):
    """
    Invariant Test 2: If no promo active in horizon, adjusted = baseline for all dates.
    """
    today = date.today()
    # Select horizon BEFORE promo window (promo starts at today+5)
    horizon = [today + timedelta(days=i) for i in range(1, 4)]  # Days 1-3 (before promo)
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        settings=settings_promo_enabled,
    )
    
    # Verify adjustment is enabled
    assert result["adjustment_enabled"] is True
    
    # Verify no promo active in horizon
    assert all(not result["promo_active"][d] for d in horizon)
    
    # Verify adjusted = baseline for all dates
    for forecast_date in horizon:
        baseline = result["baseline_forecast"][forecast_date]
        adjusted = result["adjusted_forecast"][forecast_date]
        assert adjusted == baseline, f"Date {forecast_date}: adjusted != baseline when no promo"
        assert result["uplift_factor"][forecast_date] == 1.0


def test_promo_active_with_uplift(csv_layer, settings_promo_enabled):
    """
    Core Test: Promo active in horizon → adjusted = baseline × uplift_factor.
    
    Note: This test assumes uplift estimation returns uplift > 1.0.
    If insufficient promo history, uplift may be 1.0 (fallback).
    """
    today = date.today()
    # Select horizon covering promo window (days 5-10)
    horizon = [today + timedelta(days=i) for i in range(1, 12)]  # Days 1-11
    
    # Add historical promo sales to enable uplift estimation
    promo_sales = []
    for i in range(60, 30, -1):  # 30 days of promo history
        promo_sales.append(SalesRecord(
            date=today - timedelta(days=i),
            sku="SKU001",
            qty_sold=15,  # 15 units during promo (vs 10 baseline) → uplift ~1.5x
            promo_flag=1,
        ))
    csv_layer.write_sales(csv_layer.read_sales() + promo_sales)
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        settings=settings_promo_enabled,
    )
    
    # Verify adjustment is enabled
    assert result["adjustment_enabled"] is True
    
    # Verify uplift report exists
    assert result["uplift_report"] is not None
    uplift_factor = result["uplift_report"].uplift_factor
    
    # Verify promo active for days 5-10
    for day_offset in range(5, 11):
        forecast_date = today + timedelta(days=day_offset)
        assert result["promo_active"][forecast_date] is True
        
        # Verify uplift applied
        baseline = result["baseline_forecast"][forecast_date]
        adjusted = result["adjusted_forecast"][forecast_date]
        expected = baseline * uplift_factor
        assert abs(adjusted - expected) < 0.01, f"Date {forecast_date}: adjusted ({adjusted}) != baseline × uplift ({expected})"
        
        # Verify uplift factor stored
        assert result["uplift_factor"][forecast_date] == uplift_factor
    
    # Verify no promo for days before and after window
    for day_offset in [1, 2, 3, 4, 11]:
        forecast_date = today + timedelta(days=day_offset)
        assert result["promo_active"][forecast_date] is False
        baseline = result["baseline_forecast"][forecast_date]
        adjusted = result["adjusted_forecast"][forecast_date]
        assert adjusted == baseline


def test_smoothing_disabled_no_ramp(csv_layer, settings_promo_enabled):
    """
    Smoothing Test 1: With smoothing_enabled=false, smoothing_multiplier=1.0 for all dates.
    """
    today = date.today()
    horizon = [today + timedelta(days=i) for i in range(1, 12)]
    
    # Add promo history for uplift
    promo_sales = []
    for i in range(60, 30, -1):
        promo_sales.append(SalesRecord(
            date=today - timedelta(days=i),
            sku="SKU001",
            qty_sold=15,
            promo_flag=1,
        ))
    csv_layer.write_sales(csv_layer.read_sales() + promo_sales)
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        settings=settings_promo_enabled,
    )
    
    # Verify smoothing is disabled
    assert result["smoothing_enabled"] is False
    
    # Verify all smoothing multipliers = 1.0
    for forecast_date in horizon:
        assert result["smoothing_multiplier"][forecast_date] == 1.0


def test_smoothing_enabled_ramp_in_out(csv_layer, settings_promo_smoothing_enabled):
    """
    Smoothing Test 2: With smoothing_enabled=true, ramp-in/ramp-out applied at promo borders.
    
    Promo window: days 5-10 (6 days total)
    Ramp-in: 2 days (days 5-6)
    Ramp-out: 2 days (days 9-10)
    
    Expected smoothing multipliers:
    - Day 5 (1st day): (0+1)/(2+1) = 0.33
    - Day 6 (2nd day): (1+1)/(2+1) = 0.67
    - Day 7-8 (middle): 1.0
    - Day 9 (2nd-to-last): (1+1)/(2+1) = 0.67
    - Day 10 (last): (0+1)/(2+1) = 0.33
    """
    today = date.today()
    horizon = [today + timedelta(days=i) for i in range(1, 12)]
    
    # Add promo history
    promo_sales = []
    for i in range(60, 30, -1):
        promo_sales.append(SalesRecord(
            date=today - timedelta(days=i),
            sku="SKU001",
            qty_sold=15,
            promo_flag=1,
        ))
    csv_layer.write_sales(csv_layer.read_sales() + promo_sales)
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        settings=settings_promo_smoothing_enabled,
    )
    
    # Verify smoothing is enabled
    assert result["smoothing_enabled"] is True
    
    # Verify ramp-in (days 5-6)
    day5 = today + timedelta(days=5)
    day6 = today + timedelta(days=6)
    assert abs(result["smoothing_multiplier"][day5] - 0.33) < 0.01, f"Day 5 ramp-in: {result['smoothing_multiplier'][day5]}"
    assert abs(result["smoothing_multiplier"][day6] - 0.67) < 0.01, f"Day 6 ramp-in: {result['smoothing_multiplier'][day6]}"
    
    # Verify middle days (7-8): full multiplier
    day7 = today + timedelta(days=7)
    day8 = today + timedelta(days=8)
    assert result["smoothing_multiplier"][day7] == 1.0
    assert result["smoothing_multiplier"][day8] == 1.0
    
    # Verify ramp-out (days 9-10)
    day9 = today + timedelta(days=9)
    day10 = today + timedelta(days=10)
    assert abs(result["smoothing_multiplier"][day9] - 0.67) < 0.01, f"Day 9 ramp-out: {result['smoothing_multiplier'][day9]}"
    assert abs(result["smoothing_multiplier"][day10] - 0.33) < 0.01, f"Day 10 ramp-out: {result['smoothing_multiplier'][day10]}"
    
    # Verify non-promo days have multiplier=1.0 (no smoothing outside promo)
    for day_offset in [1, 2, 3, 4, 11]:
        forecast_date = today + timedelta(days=day_offset)
        assert result["smoothing_multiplier"][forecast_date] == 1.0


def test_store_id_global_only(csv_layer, settings_promo_enabled):
    """
    Store Scope Test: With store_id=None, only global promos (store_id=None) are considered.
    Store-specific promos (store_id != None) are ignored.
    """
    today = date.today()
    horizon = [today + timedelta(days=i) for i in range(1, 8)]
    
    # Add store-specific promo window (should be IGNORED)
    store_promo = PromoWindow(
        sku="SKU001",
        start_date=today + timedelta(days=1),
        end_date=today + timedelta(days=3),
        store_id="STORE123",  # Store-specific
    )
    csv_layer.write_promo_calendar(csv_layer.read_promo_calendar() + [store_promo])
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        store_id=None,  # Global only (user decision)
        settings=settings_promo_enabled,
    )
    
    # Verify store-specific promo is NOT detected (days 1-3 should show promo_active=False)
    for day_offset in [1, 2, 3]:
        forecast_date = today + timedelta(days=day_offset)
        assert result["promo_active"][forecast_date] is False, f"Store promo should be ignored for day {day_offset}"
        
        # Verify baseline = adjusted (no uplift)
        baseline = result["baseline_forecast"][forecast_date]
        adjusted = result["adjusted_forecast"][forecast_date]
        assert adjusted == baseline


def test_uplift_estimation_failure_fallback(csv_layer, settings_promo_enabled):
    """
    Error Handling Test: If uplift estimation fails, fallback to baseline (uplift=1.0).
    
    Simulate failure by using SKU with no promo history.
    """
    today = date.today()
    horizon = [today + timedelta(days=i) for i in range(1, 12)]
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    # No promo history added → uplift estimation will fail or return 1.0
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        settings=settings_promo_enabled,
    )
    
    # Verify uplift report exists but uplift~1.0 (no promo history)
    # OR uplift report is None (estimation failed)
    if result["uplift_report"]:
        uplift_factor = result["uplift_report"].uplift_factor
        # With no promo history, uplift should fallback to 1.0 (or very close)
        assert abs(uplift_factor - 1.0) < 0.1, f"Expected uplift~1.0 with no history, got {uplift_factor}"
    
    # Verify adjusted = baseline for all dates (safety fallback)
    for forecast_date in horizon:
        baseline = result["baseline_forecast"][forecast_date]
        adjusted = result["adjusted_forecast"][forecast_date]
        # With uplift=1.0, adjusted should equal baseline
        assert abs(adjusted - baseline) < 0.01


def test_promo_adjusted_forecast_returns_all_keys(csv_layer, settings_promo_enabled):
    """
    Contract Test: Verify promo_adjusted_forecast returns all expected keys.
    """
    today = date.today()
    horizon = [today + timedelta(days=i) for i in range(1, 8)]
    
    sales = csv_layer.read_sales()
    transactions = csv_layer.read_transactions()
    promo_windows = csv_layer.read_promo_calendar()
    all_skus = csv_layer.read_skus()
    
    result = promo_adjusted_forecast(
        sku_id="SKU001",
        horizon_dates=horizon,
        sales_records=sales,
        transactions=transactions,
        promo_windows=promo_windows,
        all_skus=all_skus,
        csv_layer=csv_layer,
        settings=settings_promo_enabled,
    )
    
    # Verify all required keys present
    required_keys = [
        "baseline_forecast",
        "adjusted_forecast",
        "promo_active",
        "uplift_factor",
        "smoothing_multiplier",
        "adjustment_enabled",
        "smoothing_enabled",
        "uplift_report",
    ]
    
    for key in required_keys:
        assert key in result, f"Missing key: {key}"
    
    # Verify dict keys match horizon dates
    for forecast_date in horizon:
        assert forecast_date in result["baseline_forecast"]
        assert forecast_date in result["adjusted_forecast"]
        assert forecast_date in result["promo_active"]
        assert forecast_date in result["uplift_factor"]
        assert forecast_date in result["smoothing_multiplier"]
