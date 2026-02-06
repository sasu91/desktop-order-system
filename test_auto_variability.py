#!/usr/bin/env python3
"""
Test Auto-classification of Demand Variability

Testa il sistema di classificazione automatica della variabilità domanda
basato su quartili adattivi calcolati dallo storico vendite.
"""

from datetime import date, timedelta
from src.domain.models import SalesRecord, DemandVariability
from src.domain.auto_variability import (
    calculate_cv,
    calculate_autocorrelation,
    compute_sku_metrics,
    compute_adaptive_thresholds,
    classify_demand_variability,
    classify_all_skus,
    get_classification_summary
)


def test_calculate_cv():
    """Test CV calculation."""
    # Stable demand (low CV)
    stable_sales = [10, 11, 10, 12, 10, 11, 10]
    cv_stable = calculate_cv(stable_sales)
    assert cv_stable < 0.2, f"Stable demand should have low CV, got {cv_stable}"
    
    # High variability (high CV)
    volatile_sales = [5, 50, 10, 80, 15, 100, 20]
    cv_volatile = calculate_cv(volatile_sales)
    assert cv_volatile > 0.7, f"Volatile demand should have high CV, got {cv_volatile}"
    
    # Edge case: empty or single value
    assert calculate_cv([]) == 0.0
    assert calculate_cv([10]) == 0.0


def test_calculate_autocorrelation():
    """Test autocorrelation for seasonal pattern detection."""
    # Weekly pattern (strong autocorrelation at lag 7)
    # Pattern: [10, 20, 30, 40, 50, 60, 70] repeated
    weekly_pattern = [10, 20, 30, 40, 50, 60, 70] * 4
    autocorr = calculate_autocorrelation(weekly_pattern, lag=7)
    
    # Strong positive autocorrelation expected
    assert autocorr is not None
    assert autocorr > 0.3, f"Weekly pattern should show high autocorrelation, got {autocorr}"
    
    # Random data (low autocorrelation)
    random_sales = [i % 13 for i in range(30)]  # Pseudo-random
    autocorr_random = calculate_autocorrelation(random_sales, lag=7)
    assert autocorr_random is not None
    # Should be lower than weekly pattern (but not guaranteed to be < 0.3)


def test_compute_sku_metrics():
    """Test metric computation for single SKU."""
    sales = [
        SalesRecord(date=date(2026, 1, i), sku="TEST001", qty_sold=10)
        for i in range(1, 31)
    ]
    
    metrics = compute_sku_metrics("TEST001", sales, min_observations=30)
    
    assert metrics.sku == "TEST001"
    assert metrics.observations == 30
    assert metrics.has_sufficient_data is True
    assert metrics.mean_daily_sales == 10.0
    assert metrics.cv == 0.0  # All same values → CV=0


def test_compute_adaptive_thresholds():
    """Test adaptive threshold calculation from quartiles."""
    # Create metrics for 10 SKUs with varying CVs
    # CV values: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    from src.domain.auto_variability import VariabilityMetrics
    
    metrics_list = []
    for i in range(10):
        cv = (i + 1) * 0.1
        metrics = VariabilityMetrics(
            sku=f"SKU{i:03d}",
            mean_daily_sales=10.0,
            std_daily_sales=cv * 10.0,
            cv=cv,
            autocorr_lag7=None,
            observations=50,
            has_sufficient_data=True
        )
        metrics_list.append(metrics)
    
    # Calculate Q1 (25th) and Q3 (75th)
    stable_thresh, high_thresh = compute_adaptive_thresholds(
        metrics_list, stable_percentile=25, high_percentile=75
    )
    
    # Expected: Q1 ≈ 0.25-0.3, Q3 ≈ 0.7-0.8
    assert 0.2 <= stable_thresh <= 0.35, f"Q1 should be ~0.25-0.3, got {stable_thresh}"
    assert 0.65 <= high_thresh <= 0.85, f"Q3 should be ~0.7-0.8, got {high_thresh}"
    assert stable_thresh < high_thresh, "Q1 must be < Q3"


def test_classify_demand_variability():
    """Test classification logic for individual SKU."""
    from src.domain.auto_variability import VariabilityMetrics
    
    # Case 1: STABLE (CV below Q1)
    stable_metrics = VariabilityMetrics(
        sku="STABLE_SKU",
        mean_daily_sales=20.0,
        std_daily_sales=2.0,
        cv=0.1,
        autocorr_lag7=0.1,
        observations=50,
        has_sufficient_data=True
    )
    
    category = classify_demand_variability(
        stable_metrics,
        stable_threshold=0.3,
        high_threshold=0.7,
        seasonal_autocorr_threshold=0.3
    )
    assert category == DemandVariability.STABLE, f"Expected STABLE, got {category}"
    
    # Case 2: HIGH (CV above Q3)
    high_metrics = VariabilityMetrics(
        sku="HIGH_SKU",
        mean_daily_sales=20.0,
        std_daily_sales=18.0,
        cv=0.9,
        autocorr_lag7=0.1,
        observations=50,
        has_sufficient_data=True
    )
    
    category = classify_demand_variability(
        high_metrics,
        stable_threshold=0.3,
        high_threshold=0.7,
        seasonal_autocorr_threshold=0.3
    )
    assert category == DemandVariability.HIGH, f"Expected HIGH, got {category}"
    
    # Case 3: SEASONAL (high autocorrelation)
    seasonal_metrics = VariabilityMetrics(
        sku="SEASONAL_SKU",
        mean_daily_sales=20.0,
        std_daily_sales=10.0,
        cv=0.5,
        autocorr_lag7=0.7,  # Strong weekly pattern
        observations=50,
        has_sufficient_data=True
    )
    
    category = classify_demand_variability(
        seasonal_metrics,
        stable_threshold=0.3,
        high_threshold=0.7,
        seasonal_autocorr_threshold=0.3
    )
    assert category == DemandVariability.SEASONAL, f"Expected SEASONAL, got {category}"
    
    # Case 4: LOW (moderate CV, between Q1 and Q3)
    low_metrics = VariabilityMetrics(
        sku="LOW_SKU",
        mean_daily_sales=20.0,
        std_daily_sales=10.0,
        cv=0.5,
        autocorr_lag7=0.1,
        observations=50,
        has_sufficient_data=True
    )
    
    category = classify_demand_variability(
        low_metrics,
        stable_threshold=0.3,
        high_threshold=0.7,
        seasonal_autocorr_threshold=0.3
    )
    assert category == DemandVariability.LOW, f"Expected LOW, got {category}"
    
    # Case 5: Insufficient data → fallback
    insufficient_metrics = VariabilityMetrics(
        sku="NODATA_SKU",
        mean_daily_sales=0.0,
        std_daily_sales=0.0,
        cv=0.0,
        autocorr_lag7=None,
        observations=5,
        has_sufficient_data=False
    )
    
    category = classify_demand_variability(
        insufficient_metrics,
        stable_threshold=0.3,
        high_threshold=0.7,
        seasonal_autocorr_threshold=0.3,
        fallback_category=DemandVariability.LOW
    )
    assert category == DemandVariability.LOW, f"Expected fallback LOW, got {category}"


def test_classify_all_skus_integration():
    """Test end-to-end classification for multiple SKUs."""
    # Create realistic sales data for 5 SKUs
    sales_records = []
    base_date = date(2026, 1, 1)
    
    # SKU001: Stable (CV ~0.1)
    for day in range(60):
        qty = 10 + (day % 3)  # 10, 11, 12 pattern
        sales_records.append(
            SalesRecord(date=base_date + timedelta(days=day), sku="SKU001", qty_sold=qty)
        )
    
    # SKU002: High volatility (CV ~1.0)
    for day in range(60):
        qty = [5, 50, 10, 80, 15][day % 5]
        sales_records.append(
            SalesRecord(date=base_date + timedelta(days=day), sku="SKU002", qty_sold=qty)
        )
    
    # SKU003: Seasonal (weekly pattern)
    for week in range(10):
        for day_of_week in range(7):
            qty = (day_of_week + 1) * 5  # 5, 10, 15, 20, 25, 30, 35
            day = week * 7 + day_of_week
            if day < 60:
                sales_records.append(
                    SalesRecord(date=base_date + timedelta(days=day), sku="SKU003", qty_sold=qty)
                )
    
    # SKU004: Moderate (CV ~0.5)
    for day in range(60):
        qty = 20 + (day % 10)
        sales_records.append(
            SalesRecord(date=base_date + timedelta(days=day), sku="SKU004", qty_sold=qty)
        )
    
    # SKU005: Insufficient data
    for day in range(10):  # Only 10 days
        sales_records.append(
            SalesRecord(date=base_date + timedelta(days=day), sku="SKU005", qty_sold=10)
        )
    
    # Classify
    classifications = classify_all_skus(
        sales_records=sales_records,
        min_observations=30,
        stable_percentile=25,
        high_percentile=75,
        seasonal_threshold=0.3,
        fallback_category=DemandVariability.LOW
    )
    
    # Verify results
    assert "SKU001" in classifications
    assert "SKU002" in classifications
    assert "SKU003" in classifications
    assert "SKU004" in classifications
    assert "SKU005" in classifications
    
    # SKU001 should be STABLE (low CV)
    assert classifications["SKU001"] == DemandVariability.STABLE, \
        f"SKU001 expected STABLE, got {classifications['SKU001']}"
    
    # SKU002 should be HIGH (high CV)
    assert classifications["SKU002"] == DemandVariability.HIGH, \
        f"SKU002 expected HIGH, got {classifications['SKU002']}"
    
    # SKU003 should be SEASONAL (autocorrelation)
    assert classifications["SKU003"] == DemandVariability.SEASONAL, \
        f"SKU003 expected SEASONAL, got {classifications['SKU003']}"
    
    # SKU005 should be fallback (insufficient data)
    assert classifications["SKU005"] == DemandVariability.LOW, \
        f"SKU005 expected fallback LOW, got {classifications['SKU005']}"
    
    # Summary
    summary = get_classification_summary(classifications)
    assert summary["STABLE"] == 1
    assert summary["HIGH"] == 1
    assert summary["SEASONAL"] == 1
    assert summary["LOW"] >= 1  # SKU004 + SKU005


def test_adaptive_thresholds_with_few_skus():
    """Test fallback to fixed thresholds when < 4 SKUs."""
    from src.domain.auto_variability import VariabilityMetrics
    
    # Only 2 SKUs (< 4 required for quartiles)
    metrics_list = [
        VariabilityMetrics("SKU1", 10.0, 1.0, 0.1, None, 50, True),
        VariabilityMetrics("SKU2", 10.0, 8.0, 0.8, None, 50, True),
    ]
    
    stable_thresh, high_thresh = compute_adaptive_thresholds(metrics_list)
    
    # Should fallback to fixed thresholds (0.3, 0.7)
    assert stable_thresh == 0.3
    assert high_thresh == 0.7


if __name__ == "__main__":
    # Run tests
    print("=== TEST: Auto-classification of Demand Variability ===\n")
    
    test_calculate_cv()
    print("✅ Test CV calculation passed")
    
    test_calculate_autocorrelation()
    print("✅ Test autocorrelation passed")
    
    test_compute_sku_metrics()
    print("✅ Test SKU metrics computation passed")
    
    test_compute_adaptive_thresholds()
    print("✅ Test adaptive thresholds passed")
    
    test_classify_demand_variability()
    print("✅ Test classification logic passed")
    
    test_classify_all_skus_integration()
    print("✅ Test end-to-end classification passed")
    
    test_adaptive_thresholds_with_few_skus()
    print("✅ Test fallback thresholds passed")
    
    print("\n" + "="*60)
    print("✅ ALL AUTO-CLASSIFICATION TESTS PASSED")
    print("\nFeatures verified:")
    print("  • CV calculation for variability measurement")
    print("  • Autocorrelation detection for seasonal patterns")
    print("  • Adaptive quartile-based thresholds (Q1, Q3)")
    print("  • Classification: STABLE, LOW, SEASONAL, HIGH")
    print("  • Fallback for insufficient data")
    print("  • End-to-end integration with multiple SKUs")
