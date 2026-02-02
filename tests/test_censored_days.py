"""
Test censored days detection and handling in forecast/uncertainty.

Verifies:
1. is_day_censored correctly identifies OOS days
2. Censored days excluded from forecast model
3. Censored days excluded from sigma calculation
4. Sigma doesn't collapse when many days are censored
5. Alpha boost applied when censored days present
6. Order quantities don't artificially drop due to censored data
"""
import pytest
from datetime import date, timedelta
from typing import List, Dict, Any

from src.domain.ledger import StockCalculator, is_day_censored
from src.domain.models import Transaction, EventType, SalesRecord, Stock
from src.forecast import fit_forecast_model, predict
from src.uncertainty import (
    calculate_forecast_residuals,
    estimate_demand_uncertainty,
    robust_sigma,
)
from src.replenishment_policy import compute_order, OrderConstraints
from src.domain.calendar import Lane


# ============================================================================
# Test is_day_censored detection
# ============================================================================

def test_is_day_censored_oh_zero_sales_zero():
    """Day is censored when OH=0 and sales=0 (stockout)."""
    txns = [
        Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
        Transaction(date=date(2026, 1, 15), sku="SKU001", event=EventType.SALE, qty=100),  # OH goes to 0
    ]
    sales = []  # No sales on 1/15 (censored observation)
    
    check_date = date(2026, 1, 15)
    is_censored, reason = is_day_censored("SKU001", check_date, txns, sales)
    
    assert is_censored is True
    assert "OH=0 and sales=0" in reason


def test_is_day_censored_unfulfilled_event():
    """Day is censored when UNFULFILLED event exists within lookback."""
    txns = [
        Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
        Transaction(date=date(2026, 1, 10), sku="SKU001", event=EventType.UNFULFILLED, qty=5),
    ]
    sales = []
    
    # Check day after UNFULFILLED (within 3-day lookback)
    check_date = date(2026, 1, 12)
    is_censored, reason = is_day_censored("SKU001", check_date, txns, sales, lookback_days=3)
    
    assert is_censored is True
    assert "UNFULFILLED" in reason
    assert "2026-01-10" in reason


def test_is_day_censored_normal_day():
    """Normal day with stock and sales is NOT censored."""
    txns = [
        Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=100),
    ]
    sales = [
        SalesRecord(date=date(2026, 1, 5), sku="SKU001", qty_sold=10),
    ]
    
    check_date = date(2026, 1, 5)
    is_censored, reason = is_day_censored("SKU001", check_date, txns, sales)
    
    assert is_censored is False
    assert "Normal" in reason


def test_is_day_censored_oh_zero_but_sales_positive():
    """Day NOT censored if sales occurred (even if OH=0 at EOD)."""
    txns = [
        Transaction(date=date(2026, 1, 1), sku="SKU001", event=EventType.SNAPSHOT, qty=10),
    ]
    sales = [
        SalesRecord(date=date(2026, 1, 2), sku="SKU001", qty_sold=10),  # Sold all stock
    ]
    
    check_date = date(2026, 1, 2)
    is_censored, reason = is_day_censored("SKU001", check_date, txns, sales)
    
    assert is_censored is False
    assert "Normal" in reason


# ============================================================================
# Test forecast model with censored days
# ============================================================================

def test_fit_forecast_model_excludes_censored_days():
    """Forecast model should exclude censored days from training."""
    history = [
        {"date": date(2026, 1, 1), "qty_sold": 10},
        {"date": date(2026, 1, 2), "qty_sold": 0},   # Censored (OOS)
        {"date": date(2026, 1, 3), "qty_sold": 0},   # Censored (OOS)
        {"date": date(2026, 1, 4), "qty_sold": 12},
        {"date": date(2026, 1, 5), "qty_sold": 11},
    ]
    censored = [False, True, True, False, False]
    
    model = fit_forecast_model(history, censored_flags=censored)
    
    assert model["n_samples"] == 3  # Only non-censored days
    assert model["n_censored"] == 2
    assert model["level"] > 0  # Should be based on 10, 12, 11 only


def test_fit_forecast_model_alpha_boost_for_censored():
    """Alpha should be boosted when censored days present."""
    history = [
        {"date": date(2026, 1, i), "qty_sold": 10} for i in range(1, 20)
    ]
    censored = [False] * 15 + [True] * 4  # Last 4 days censored
    
    alpha_boost = 0.1
    model = fit_forecast_model(
        history,
        alpha=0.3,
        censored_flags=censored,
        alpha_boost_for_censored=alpha_boost
    )
    
    assert model["n_censored"] == 4
    assert model["alpha_eff"] == min(0.99, 0.3 + alpha_boost)
    assert model["alpha_eff"] > 0.3  # Boosted


def test_fit_forecast_model_no_boost_without_censored():
    """Alpha should NOT be boosted if no censored days."""
    history = [
        {"date": date(2026, 1, i), "qty_sold": 10} for i in range(1, 20)
    ]
    
    model = fit_forecast_model(history, alpha=0.3, alpha_boost_for_censored=0.1)
    
    assert model["n_censored"] == 0
    assert model["alpha_eff"] == 0.3  # No boost


# ============================================================================
# Test uncertainty calculation with censored days
# ============================================================================

def test_calculate_forecast_residuals_excludes_censored():
    """Residuals should exclude censored days."""
    # Create history with clear pattern: demand = 10 every day
    history = []
    for i in range(1, 60):  # 60 days
        history.append({"date": date(2026, 1, 1) + timedelta(days=i-1), "qty_sold": 10})
    
    # Mark days 20-25 as censored (6 days)
    censored = [False] * 60
    for i in range(19, 25):  # Days 20-25
        censored[i] = True
        history[i]["qty_sold"] = 0  # Simulate OOS (no sales)
    
    def forecast_func(hist, horizon):
        model = fit_forecast_model(hist)
        return predict(model, horizon)
    
    # Without censored filtering
    residuals_no_filter, _ = calculate_forecast_residuals(history, forecast_func, window_weeks=4)
    
    # With censored filtering
    residuals_filtered, n_censored = calculate_forecast_residuals(
        history, forecast_func, window_weeks=4, censored_flags=censored
    )
    
    # Should exclude censored days
    assert n_censored == 6  # 6 days marked censored in evaluation window
    assert len(residuals_filtered) < len(residuals_no_filter)


def test_sigma_does_not_collapse_with_censored():
    """Sigma should NOT collapse when censored days are properly excluded."""
    # Scenario: SKU with normal demand 10±2, but frequent OOS (OH=0, sales=0)
    history = []
    for i in range(1, 90):  # 90 days
        d = date(2026, 1, 1) + timedelta(days=i-1)
        # Normal demand: 10 ± random variation
        if i % 7 == 0:  # Every 7th day: OOS (censored)
            qty = 0
        else:
            qty = 10 + (i % 3) - 1  # 9, 10, 11 pattern
        history.append({"date": d, "qty_sold": qty})
    
    # Mark OOS days as censored
    censored = [h["qty_sold"] == 0 for h in history]
    
    def forecast_func(hist, horizon):
        model = fit_forecast_model(hist, censored_flags=censored)
        return predict(model, horizon)
    
    # Calculate sigma WITH censored filtering
    sigma_with_filter, meta = estimate_demand_uncertainty(
        history, forecast_func, window_weeks=8, censored_flags=censored
    )
    
    # Calculate sigma WITHOUT censored filtering (simulates old behavior)
    def forecast_func_no_filter(hist, horizon):
        model = fit_forecast_model(hist)
        return predict(model, horizon)
    
    sigma_without_filter, meta_no_filter = estimate_demand_uncertainty(
        history, forecast_func_no_filter, window_weeks=8
    )
    
    # Sigma with filter should be HIGHER (or at least not artificially low)
    # Because we exclude low-demand censored days
    assert sigma_with_filter >= sigma_without_filter * 0.8  # Allow some variance
    assert meta["n_censored_excluded"] > 0  # Confirmed censored days excluded


# ============================================================================
# Test order computation with censored days
# ============================================================================

def test_compute_order_with_censored_days():
    """Order quantity should not drop artificially due to censored data."""
    # Create sales history with OOS periods
    history = []
    for i in range(1, 60):
        d = date(2026, 1, 1) + timedelta(days=i-1)
        # Normal demand: 20 units/day
        # But days 15-18: OOS (OH=0, sales=0)
        if 15 <= i <= 18:
            qty = 0  # OOS
        else:
            qty = 20
        history.append({"date": d, "qty_sold": qty})
    
    # Mark OOS days as censored
    censored = [h["qty_sold"] == 0 for h in history]
    
    constraints = OrderConstraints(pack_size=10, moq=0, max_stock=1000)
    
    # Compute order WITH censored handling
    result_with_censored = compute_order(
        sku="SKU001",
        order_date=date(2026, 3, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=10,
        pipeline=[],
        constraints=constraints,
        history=history,
        censored_flags=censored,
        alpha_boost_for_censored=0.05,
    )
    
    # Compute order WITHOUT censored handling (old behavior)
    result_without_censored = compute_order(
        sku="SKU001",
        order_date=date(2026, 3, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=10,
        pipeline=[],
        constraints=constraints,
        history=history,
        censored_flags=None,  # No censoring
    )
    
    # Order WITH censored handling should be HIGHER or similar
    # (not artificially reduced by OOS days)
    assert result_with_censored["n_censored"] == 4
    assert result_with_censored["alpha_eff"] > result_with_censored["alpha"]  # Boosted
    assert result_with_censored["order_final"] >= result_without_censored["order_final"] * 0.9


def test_compute_order_censored_metadata():
    """compute_order should return comprehensive censored metadata."""
    history = [
        {"date": date(2026, 1, i), "qty_sold": 10 if i % 5 != 0 else 0}
        for i in range(1, 30)
    ]
    censored = [h["qty_sold"] == 0 for h in history]
    
    constraints = OrderConstraints(pack_size=5, moq=10)
    
    result = compute_order(
        sku="SKU_TEST",
        order_date=date(2026, 2, 1),
        lane=Lane.STANDARD,
        alpha=0.95,
        on_hand=50,
        pipeline=[],
        constraints=constraints,
        history=history,
        censored_flags=censored,
    )
    
    # Check metadata presence
    assert "n_censored" in result
    assert "censored_reasons" in result
    assert "alpha_eff" in result
    assert "forecast_n_censored" in result
    assert "n_censored_excluded_from_sigma" in result
    
    # Verify values
    assert result["n_censored"] > 0
    assert result["alpha_eff"] >= result["alpha"]
    assert len(result["censored_reasons"]) > 0


# ============================================================================
# Regression test: sigma collapse scenario
# ============================================================================

def test_regression_sigma_collapse_prevented():
    """
    Regression test: Prevent sigma collapse when ~2% of days are OOS.
    
    Scenario from requirements:
    - SKU with normal demand variability
    - ~2% of days have OOS (OH=0, sales=0)
    - Without censoring: sigma collapses → safety stock drops → more stockouts
    - WITH censoring: sigma remains stable → adequate safety stock
    """
    # Generate 365 days of sales
    import random
    random.seed(42)  # Deterministic
    
    history = []
    for i in range(1, 366):
        d = date(2025, 1, 1) + timedelta(days=i-1)
        # Normal demand: mean=30, sigma=10 (CV ≈ 33%)
        if random.random() < 0.02:  # 2% OOS days
            qty = 0
        else:
            qty = max(0, random.gauss(30, 10))
        history.append({"date": d, "qty_sold": qty})
    
    # Identify censored days (OOS)
    censored = [h["qty_sold"] == 0 for h in history]
    n_oos = sum(censored)
    
    # Ensure ~2% OOS
    assert 5 <= n_oos <= 10  # ~2% of 365 days
    
    def forecast_func(hist, horizon):
        model = fit_forecast_model(hist, censored_flags=censored)
        return predict(model, horizon)
    
    # Calculate sigma WITH censored filtering
    sigma_with_filter, meta_with = estimate_demand_uncertainty(
        history, forecast_func, window_weeks=8, censored_flags=censored
    )
    
    # Calculate sigma WITHOUT censored filtering
    def forecast_func_no_filter(hist, horizon):
        model = fit_forecast_model(hist)
        return predict(model, horizon)
    
    sigma_without_filter, meta_without = estimate_demand_uncertainty(
        history, forecast_func_no_filter, window_weeks=8
    )
    
    # Key assertion: Sigma WITH filter should be >= sigma WITHOUT
    # (Excluding OOS days prevents underestimation of variability)
    print(f"\nSigma WITH censored filter: {sigma_with_filter:.2f}")
    print(f"Sigma WITHOUT filter: {sigma_without_filter:.2f}")
    print(f"Censored days excluded: {meta_with['n_censored_excluded']}")
    
    assert sigma_with_filter > 0, "Sigma should not be zero"
    assert sigma_with_filter >= sigma_without_filter * 0.85, \
        "Sigma with filter should not be significantly lower than without"


# ============================================================================
# Edge cases
# ============================================================================

def test_censored_all_days():
    """Handle case where all days are censored."""
    history = [
        {"date": date(2026, 1, i), "qty_sold": 0} for i in range(1, 10)
    ]
    censored = [True] * len(history)
    
    model = fit_forecast_model(history, censored_flags=censored)
    
    # Should fallback gracefully
    assert model["n_samples"] == 0
    assert model["n_censored"] == 9
    assert model["method"] == "fallback"


def test_censored_no_days():
    """Handle case where no days are censored."""
    history = [
        {"date": date(2026, 1, i), "qty_sold": 10} for i in range(1, 20)
    ]
    censored = [False] * len(history)
    
    model = fit_forecast_model(history, censored_flags=censored)
    
    assert model["n_samples"] == len(history)
    assert model["n_censored"] == 0
    assert model["alpha_eff"] == 0.3  # No boost (default alpha)


def test_censored_flags_length_mismatch():
    """Should raise error if censored_flags length != history length."""
    history = [{"date": date(2026, 1, i), "qty_sold": 10} for i in range(1, 10)]
    censored = [False] * 5  # Wrong length
    
    with pytest.raises(ValueError, match="censored_flags length"):
        fit_forecast_model(history, censored_flags=censored)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
