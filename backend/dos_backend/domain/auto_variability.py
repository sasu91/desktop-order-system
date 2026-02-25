"""
Auto-classification of Demand Variability

This module implements adaptive threshold-based classification of demand
variability using historical sales data and statistical metrics (CV).

Key Features:
- Quartile-based adaptive thresholds (not fixed)
- Configurable via settings.json
- Minimum sample requirements
- Seasonal pattern detection via autocorrelation
- Fallback for data-scarce SKUs

Classification Logic:
    1. Calculate CV (Coefficient of Variation) for each SKU
    2. Compute quartiles (Q1, Q3) across all SKUs
    3. Apply adaptive rules:
       - STABLE: CV <= Q1 (25th percentile)
       - HIGH: CV >= Q3 (75th percentile)
       - SEASONAL: autocorrelation > threshold
       - LOW: insufficient data OR between Q1-Q3 (fallback)

Author: Desktop Order System Team
Date: February 2026
"""

from typing import List, Dict, Tuple, Optional, Sequence
from datetime import date, timedelta
from dataclasses import dataclass
import statistics
import math

from .models import DemandVariability, SalesRecord


@dataclass
class VariabilityMetrics:
    """Metrics for a single SKU demand variability analysis."""
    sku: str
    mean_daily_sales: float
    std_daily_sales: float
    cv: float  # Coefficient of Variation
    autocorr_lag7: Optional[float]  # Autocorrelation at lag 7 (weekly pattern)
    observations: int
    has_sufficient_data: bool


def calculate_cv(sales_data: Sequence[float]) -> float:
    """
    Calculate Coefficient of Variation (CV).
    
    CV = σ / μ (for μ > 0)
    
    CV is a normalized measure of dispersion:
    - CV < 0.3: Low variability (stable demand)
    - 0.3 ≤ CV < 0.7: Moderate variability
    - CV ≥ 0.7: High variability (volatile demand)
    
    Args:
        sales_data: List of daily sales quantities
    
    Returns:
        float: CV value (0 if mean is zero or data insufficient)
    """
    if not sales_data or len(sales_data) < 2:
        return 0.0
    
    mean = statistics.mean(sales_data)
    if mean == 0:
        return 0.0
    
    stdev = statistics.stdev(sales_data)
    return stdev / mean


def calculate_autocorrelation(sales_data: Sequence[float], lag: int = 7) -> Optional[float]:
    """
    Calculate autocorrelation at specified lag.
    
    Used to detect seasonal patterns:
    - lag=7: Weekly patterns
    - High positive autocorrelation → seasonal demand
    
    Args:
        sales_data: Time series of daily sales
        lag: Lag period (default 7 for weekly)
    
    Returns:
        float: Autocorrelation coefficient [-1, 1], or None if insufficient data
    """
    if len(sales_data) < lag + 10:  # Need sufficient data for lag
        return None
    
    n = len(sales_data)
    mean = statistics.mean(sales_data)
    
    # Numerator: covariance at lag
    numerator = sum(
        (sales_data[i] - mean) * (sales_data[i + lag] - mean)
        for i in range(n - lag)
    )
    
    # Denominator: variance
    denominator = sum((x - mean) ** 2 for x in sales_data)
    
    if denominator == 0:
        return 0.0
    
    return numerator / denominator


def compute_sku_metrics(
    sku: str,
    sales_records: List[SalesRecord],
    min_observations: int = 30
) -> VariabilityMetrics:
    """
    Compute variability metrics for a single SKU.
    
    Args:
        sku: SKU identifier
        sales_records: All sales records
        min_observations: Minimum days required for analysis
    
    Returns:
        VariabilityMetrics: Computed metrics
    """
    # Filter sales for this SKU
    sku_sales = [s for s in sales_records if s.sku == sku]
    
    # Extract daily quantities
    daily_sales = [s.qty_sold for s in sku_sales]
    
    observations = len(daily_sales)
    has_sufficient_data = observations >= min_observations
    
    if not has_sufficient_data or observations < 2:
        return VariabilityMetrics(
            sku=sku,
            mean_daily_sales=0.0,
            std_daily_sales=0.0,
            cv=0.0,
            autocorr_lag7=None,
            observations=observations,
            has_sufficient_data=False
        )
    
    mean = statistics.mean(daily_sales)
    stdev = statistics.stdev(daily_sales) if len(daily_sales) > 1 else 0.0
    cv = calculate_cv(daily_sales)
    autocorr = calculate_autocorrelation(daily_sales, lag=7)
    
    return VariabilityMetrics(
        sku=sku,
        mean_daily_sales=mean,
        std_daily_sales=stdev,
        cv=cv,
        autocorr_lag7=autocorr,
        observations=observations,
        has_sufficient_data=True
    )


def compute_adaptive_thresholds(
    all_metrics: List[VariabilityMetrics],
    stable_percentile: int = 25,
    high_percentile: int = 75
) -> Tuple[float, float]:
    """
    Compute adaptive CV thresholds based on quartiles.
    
    Args:
        all_metrics: Metrics for all SKUs
        stable_percentile: Percentile for STABLE threshold (default Q1 = 25)
        high_percentile: Percentile for HIGH threshold (default Q3 = 75)
    
    Returns:
        (stable_threshold, high_threshold): Adaptive CV thresholds
    
    Example:
        If CVs are [0.1, 0.2, 0.3, 0.5, 0.9]:
        - Q1 (25th) = 0.2 → STABLE threshold
        - Q3 (75th) = 0.5 → HIGH threshold
    """
    # Filter SKUs with sufficient data
    valid_cvs = [m.cv for m in all_metrics if m.has_sufficient_data and m.cv > 0]
    
    if len(valid_cvs) < 4:  # Need at least 4 SKUs for quartiles
        # Fallback to fixed thresholds
        return (0.3, 0.7)
    
    # Calculate percentiles
    sorted_cvs = sorted(valid_cvs)
    n = len(sorted_cvs)
    
    # Percentile calculation (linear interpolation)
    def percentile(data: List[float], p: int) -> float:
        """Calculate p-th percentile."""
        k = (len(data) - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return data[int(k)]
        d0 = data[int(f)] * (c - k)
        d1 = data[int(c)] * (k - f)
        return d0 + d1
    
    stable_threshold = percentile(sorted_cvs, stable_percentile)
    high_threshold = percentile(sorted_cvs, high_percentile)
    
    return (stable_threshold, high_threshold)


def classify_demand_variability(
    metrics: VariabilityMetrics,
    stable_threshold: float,
    high_threshold: float,
    seasonal_autocorr_threshold: float = 0.3,
    fallback_category: DemandVariability = DemandVariability.LOW
) -> DemandVariability:
    """
    Classify demand variability for a SKU.
    
    Decision Tree:
    1. If insufficient data → fallback_category
    2. If autocorrelation > seasonal_threshold → SEASONAL
    3. If CV <= stable_threshold → STABLE
    4. If CV >= high_threshold → HIGH
    5. Else → LOW (moderate variability)
    
    Args:
        metrics: Computed metrics for SKU
        stable_threshold: CV threshold for STABLE (from quartiles)
        high_threshold: CV threshold for HIGH (from quartiles)
        seasonal_autocorr_threshold: Autocorrelation threshold for SEASONAL
        fallback_category: Category for insufficient data
    
    Returns:
        DemandVariability: Classified category
    """
    # Rule 1: Insufficient data
    if not metrics.has_sufficient_data:
        return fallback_category
    
    # Rule 2: Seasonal pattern detection
    if metrics.autocorr_lag7 is not None and metrics.autocorr_lag7 > seasonal_autocorr_threshold:
        return DemandVariability.SEASONAL
    
    # Rule 3: STABLE (low CV, predictable)
    if metrics.cv <= stable_threshold:
        return DemandVariability.STABLE
    
    # Rule 4: HIGH (high CV, volatile)
    if metrics.cv >= high_threshold:
        return DemandVariability.HIGH
    
    # Rule 5: Moderate variability → LOW
    return DemandVariability.LOW


def classify_all_skus(
    sales_records: List[SalesRecord],
    min_observations: int = 30,
    stable_percentile: int = 25,
    high_percentile: int = 75,
    seasonal_threshold: float = 0.3,
    fallback_category: DemandVariability = DemandVariability.LOW
) -> Dict[str, DemandVariability]:
    """
    Classify demand variability for all SKUs using adaptive thresholds.
    
    Two-pass algorithm:
    1. Compute metrics for all SKUs
    2. Calculate adaptive thresholds from quartiles
    3. Classify each SKU using adaptive thresholds
    
    Args:
        sales_records: All sales records
        min_observations: Minimum days required
        stable_percentile: Percentile for STABLE threshold
        high_percentile: Percentile for HIGH threshold
        seasonal_threshold: Autocorrelation threshold for SEASONAL
        fallback_category: Category for insufficient data
    
    Returns:
        Dict[sku, DemandVariability]: Classification for each SKU
    """
    # Extract unique SKUs
    unique_skus = list(set(s.sku for s in sales_records))
    
    # Pass 1: Compute metrics
    all_metrics = [
        compute_sku_metrics(sku, sales_records, min_observations)
        for sku in unique_skus
    ]
    
    # Pass 2: Compute adaptive thresholds
    stable_threshold, high_threshold = compute_adaptive_thresholds(
        all_metrics, stable_percentile, high_percentile
    )
    
    # Pass 3: Classify
    classifications = {}
    for metrics in all_metrics:
        classifications[metrics.sku] = classify_demand_variability(
            metrics,
            stable_threshold,
            high_threshold,
            seasonal_threshold,
            fallback_category
        )
    
    return classifications


def get_classification_summary(
    classifications: Dict[str, DemandVariability]
) -> Dict[str, int]:
    """
    Get summary statistics of classifications.
    
    Args:
        classifications: Classification results
    
    Returns:
        Dict[category, count]: Count per category
    """
    summary = {
        "STABLE": 0,
        "LOW": 0,
        "SEASONAL": 0,
        "HIGH": 0
    }
    
    for variability in classifications.values():
        summary[variability.value] += 1
    
    return summary
