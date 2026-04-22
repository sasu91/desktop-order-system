"""
Unit tests for promo uplift estimation (event-level ratios + hierarchical pooling).

Tests cover:
- Event extraction with window merging
- Per-event uplift calculation
- Winsorized mean aggregation
- Guardrail clipping [min, max]
- Hierarchical pooling fallback (SKU → category → department → global)
- Confidence scoring (A/B/C)
- Anti-leakage baseline training (data < event_start)
"""
import pytest
from datetime import date, timedelta
from src.domain.promo_uplift import (
    extract_promo_events,
    calculate_uplift_for_event,
    winsorized_mean,
    aggregate_uplift_events,
    hierarchical_pooling,
    estimate_uplift,
    UpliftEvent,
    UpliftReport,
)
from src.domain.models import SKU, SalesRecord, PromoWindow, Transaction, EventType


class TestExtractPromoEvents:
    """Test promo event extraction with window merging."""
    
    def test_no_promo_windows(self):
        """No promo windows → empty events."""
        events = extract_promo_events("SKU001", [], [], [])
        assert events == []
    
    def test_single_promo_window(self):
        """Single promo window → one event."""
        windows = [
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 10), end_date=date(2024, 1, 15))
        ]
        events = extract_promo_events("SKU001", windows, [], [])
        assert len(events) == 1
        assert events[0] == (date(2024, 1, 10), date(2024, 1, 15))
    
    def test_overlapping_windows_merged(self):
        """Overlapping promo windows → merged into single event."""
        windows = [
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 10), end_date=date(2024, 1, 15)),
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 14), end_date=date(2024, 1, 20)),
        ]
        events = extract_promo_events("SKU001", windows, [], [])
        assert len(events) == 1
        assert events[0] == (date(2024, 1, 10), date(2024, 1, 20))  # Merged
    
    def test_adjacent_windows_merged(self):
        """Adjacent promo windows (gap <= 1 day) → merged."""
        windows = [
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 10), end_date=date(2024, 1, 15)),
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 16), end_date=date(2024, 1, 20)),  # gap=1
        ]
        events = extract_promo_events("SKU001", windows, [], [])
        assert len(events) == 1  # Merged
    
    def test_separate_windows_not_merged(self):
        """Separate promo windows (gap > 1 day) → separate events."""
        windows = [
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 10), end_date=date(2024, 1, 15)),
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 20), end_date=date(2024, 1, 25)),  # gap=4
        ]
        events = extract_promo_events("SKU001", windows, [], [])
        assert len(events) == 2  # Separate
        assert events[0] == (date(2024, 1, 10), date(2024, 1, 15))
        assert events[1] == (date(2024, 1, 20), date(2024, 1, 25))
    
    def test_future_events_excluded(self):
        """Future promo events (end_date >= asof_date) → excluded."""
        windows = [
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 10), end_date=date(2024, 1, 15)),  # past
            PromoWindow(sku="SKU001", start_date=date(2025, 6, 1), end_date=date(2025, 6, 10)),  # future
        ]
        asof = date(2024, 2, 1)
        events = extract_promo_events("SKU001", windows, [], [], asof_date=asof)
        assert len(events) == 1
        assert events[0] == (date(2024, 1, 10), date(2024, 1, 15))


class TestWinsorizedMean:
    """Test robust aggregation with winsorized mean."""
    
    def test_empty_list(self):
        """Empty list → 0.0."""
        assert winsorized_mean([]) == 0.0
    
    def test_single_value(self):
        """Single value → return as-is."""
        assert winsorized_mean([5.0]) == 5.0
    
    def test_no_trimming_small_list(self):
        """Small list (< 10 values) → regular mean (no effective trimming)."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = winsorized_mean(values, trim_percent=10.0)
        assert result == pytest.approx(3.0)  # Regular mean
    
    def test_winsorized_trimming_outliers(self):
        """Large list with outliers → winsorized mean clips extremes."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 100.0]  # 100 is outlier
        # 10% trim → trim 1 value from each tail
        # Lower bound = 2.0, upper bound = 9.0
        # 1.0 → 2.0, 100.0 → 9.0
        # Trimmed values: [2, 2, 3, 4, 5, 6, 7, 8, 9, 9]
        result = winsorized_mean(values, trim_percent=10.0)
        expected_mean = (2 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 9) / 10
        assert result == pytest.approx(expected_mean)
    
    def test_zero_trim_percent(self):
        """0% trim → regular mean."""
        values = [1.0, 2.0, 100.0]
        result = winsorized_mean(values, trim_percent=0.0)
        assert result == pytest.approx(34.333, abs=0.01)  # (1+2+100)/3


class TestAggregateUpliftEvents:
    """Test event aggregation with guardrails."""
    
    def test_no_events(self):
        """No events → neutral uplift 1.0."""
        result = aggregate_uplift_events([])
        assert result == 1.0
    
    def test_single_event(self):
        """Single event → return its uplift."""
        events = [
            UpliftEvent(
                sku="SKU001",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                actual_sales=100.0,
                baseline_pred=50.0,
                uplift_ratio=2.0,
                valid_days=5,
            )
        ]
        result = aggregate_uplift_events(events, trim_percent=10.0, min_uplift=1.0, max_uplift=3.0)
        assert result == 2.0
    
    def test_guardrail_clipping_min(self):
        """Uplift < min_uplift → clipped to min."""
        events = [
            UpliftEvent(
                sku="SKU001",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                actual_sales=10.0,
                baseline_pred=20.0,
                uplift_ratio=0.5,  # < 1.0
                valid_days=5,
            )
        ]
        result = aggregate_uplift_events(events, min_uplift=1.0, max_uplift=3.0)
        assert result == 1.0  # Clipped to min
    
    def test_guardrail_clipping_max(self):
        """Uplift > max_uplift → clipped to max."""
        events = [
            UpliftEvent(
                sku="SKU001",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 5),
                actual_sales=200.0,
                baseline_pred=50.0,
                uplift_ratio=4.0,  # > 3.0
                valid_days=5,
            )
        ]
        result = aggregate_uplift_events(events, min_uplift=1.0, max_uplift=3.0)
        assert result == 3.0  # Clipped to max
    
    def test_winsorized_aggregation_multiple_events(self):
        """Multiple events → winsorized mean applied."""
        events = [
            UpliftEvent("SKU001", date(2024, 1, 1), date(2024, 1, 5), 100, 50, 2.0, 5),
            UpliftEvent("SKU001", date(2024, 2, 1), date(2024, 2, 5), 90, 60, 1.5, 5),
            UpliftEvent("SKU001", date(2024, 3, 1), date(2024, 3, 5), 120, 80, 1.5, 5),
        ]
        result = aggregate_uplift_events(events, trim_percent=10.0, min_uplift=1.0, max_uplift=3.0)
        # Winsorized mean of [2.0, 1.5, 1.5] ≈ 1.67 (no clipping needed)
        expected = (2.0 + 1.5 + 1.5) / 3
        assert result == pytest.approx(expected, abs=0.01)


class TestCalculateUpliftForEvent:
    """Test per-event uplift calculation with anti-leakage baseline."""
    
    def test_no_historical_sales(self):
        """No historical sales before event → return None (cannot train baseline)."""
        event_start = date(2024, 1, 10)
        event_end = date(2024, 1, 15)
        
        # Sales only during/after event (no pre-event history)
        sales = [
            SalesRecord(sku="SKU001", date=date(2024, 1, 12), qty_sold=10, promo_flag=1),
        ]
        
        result = calculate_uplift_for_event("SKU001", event_start, event_end, sales, [])
        assert result is None  # Insufficient data
    
    def test_all_days_censored(self):
        """All event days censored (OOS) → return None."""
        event_start = date(2024, 1, 10)
        event_end = date(2024, 1, 12)
        
        # Historical sales (before event)
        sales = [
            SalesRecord(sku="SKU001", date=date(2024, 1, 1), qty_sold=10, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 2), qty_sold=12, promo_flag=0),
        ]
        
        # Transactions: UNFULFILLED on all event days (causes censoring)
        txns = [
            Transaction(sku="SKU001", date=date(2024, 1, 10), event=EventType.UNFULFILLED, qty=5),
            Transaction(sku="SKU001", date=date(2024, 1, 11), event=EventType.UNFULFILLED, qty=3),
            Transaction(sku="SKU001", date=date(2024, 1, 12), event=EventType.UNFULFILLED, qty=2),
        ]
        
        result = calculate_uplift_for_event("SKU001", event_start, event_end, sales, txns)
        assert result is None  # No valid days
    
    def test_baseline_sum_below_epsilon(self):
        """Baseline sum < epsilon → return None (avoid div-by-zero)."""
        event_start = date(2024, 1, 10)
        event_end = date(2024, 1, 11)
        
        # Historical sales very low (baseline will be near-zero)
        sales = [
            SalesRecord(sku="SKU001", date=date(2024, 1, 1), qty_sold=0.001, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 2), qty_sold=0.002, promo_flag=0),
        ]
        
        result = calculate_uplift_for_event("SKU001", event_start, event_end, sales, [], epsilon=1.0)
        # Baseline likely < 1.0, should return None
        assert result is None or result.baseline_pred < 1.0
    
    def test_valid_uplift_calculation(self):
        """Valid event with sales and baseline → uplift calculated correctly."""
        event_start = date(2024, 1, 10)
        event_end = date(2024, 1, 12)
        
        # Historical sales (before event, non-promo, non-censored)
        sales = [
            SalesRecord(sku="SKU001", date=date(2024, 1, 1), qty_sold=10, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 2), qty_sold=12, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 3), qty_sold=11, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 4), qty_sold=9, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 5), qty_sold=10, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 6), qty_sold=11, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 7), qty_sold=12, promo_flag=0),
            SalesRecord(sku="SKU001", date=date(2024, 1, 8), qty_sold=10, promo_flag=0),
        ]
        
        # Actual sales during promo event (higher than baseline)
        sales += [
            SalesRecord(sku="SKU001", date=date(2024, 1, 10), qty_sold=20, promo_flag=1),
            SalesRecord(sku="SKU001", date=date(2024, 1, 11), qty_sold=22, promo_flag=1),
            SalesRecord(sku="SKU001", date=date(2024, 1, 12), qty_sold=18, promo_flag=1),
        ]
        
        result = calculate_uplift_for_event("SKU001", event_start, event_end, sales, [])
        
        assert result is not None
        assert result.sku == "SKU001"
        assert result.start_date == event_start
        assert result.end_date == event_end
        assert result.valid_days == 3
        assert result.actual_sales == 60.0  # 20 + 22 + 18
        assert result.baseline_pred > 0  # Should have non-zero baseline
        assert result.uplift_ratio > 1.0  # Promo increased sales


class TestHierarchicalPooling:
    """Test hierarchical pooling fallback (SKU → category → department → global)."""
    
    def test_category_pooling(self):
        """SKU lacks data, but category has sufficient events → category pooling."""
        # Setup: 3 SKUs in same category, target SKU has no promo events
        all_skus = [
            SKU(sku="SKU001", description="Product 1", category="CAT_A", department="DEPT_X"),
            SKU(sku="SKU002", description="Product 2", category="CAT_A", department="DEPT_X"),
            SKU(sku="SKU003", description="Product 3", category="CAT_A", department="DEPT_X"),
        ]
        
        # Promo windows: only SKU002 and SKU003 have promo history
        promo_windows = [
            PromoWindow(sku="SKU002", start_date=date(2024, 3, 1), end_date=date(2024, 3, 5)),
            PromoWindow(sku="SKU002", start_date=date(2024, 4, 1), end_date=date(2024, 4, 5)),
            PromoWindow(sku="SKU003", start_date=date(2024, 3, 10), end_date=date(2024, 3, 15)),
            PromoWindow(sku="SKU003", start_date=date(2024, 4, 10), end_date=date(2024, 4, 15)),
            PromoWindow(sku="SKU003", start_date=date(2024, 5, 1), end_date=date(2024, 5, 5)),
        ]
        
        # Historical sales for SKU002, SKU003 (BEFORE promo events, sufficient for training)
        sales = []
        for sku_id in ["SKU002", "SKU003"]:
            # January-February 2024: non-promo baseline sales
            for day in range(1, 60):  # 60 days of history
                sales.append(
                    SalesRecord(sku=sku_id, date=date(2024, 1, 1) + timedelta(days=day), qty_sold=10, promo_flag=0)
                )
            
            # March-May: promo period sales (higher than baseline)
            for window in promo_windows:
                if window.sku == sku_id:
                    current = window.start_date
                    while current <= window.end_date:
                        sales.append(SalesRecord(sku=sku_id, date=current, qty_sold=20, promo_flag=1))
                        current += timedelta(days=1)
        
        settings = {
            "promo_uplift": {
                "min_events_category": {"value": 3},  # Category threshold
                "min_events_department": {"value": 10},
                "denominator_epsilon": {"value": 0.1},
            }
        }
        
        sku_obj = all_skus[0]  # SKU001 (target)
        pooled_events, source = hierarchical_pooling(
            "SKU001", sku_obj, all_skus, promo_windows, sales, [], settings
        )
        
        # Should pool from category (5 events from SKU002+SKU003 >= 3 threshold)
        assert source == "category:CAT_A"
        assert len(pooled_events) >= 3  # At least some events from category
    
    def test_global_pooling_fallback(self):
        """No category/department data → fallback to global pooling."""
        # SKU without category/department
        all_skus = [
            SKU(sku="SKU001", description="Product 1", category="", department=""),
            SKU(sku="SKU002", description="Product 2", category="DIFF_CAT", department="DIFF_DEPT"),
        ]
        
        promo_windows = [
            PromoWindow(sku="SKU002", start_date=date(2024, 1, 1), end_date=date(2024, 1, 5)),
        ]
        
        sales = [
            SalesRecord(sku="SKU002", date=date(2023, 12, 1), qty_sold=10, promo_flag=0),
            SalesRecord(sku="SKU002", date=date(2024, 1, 3), qty_sold=20, promo_flag=1),
        ]
        
        settings = {
            "promo_uplift": {
                "min_events_category": {"value": 5},
                "min_events_department": {"value": 10},
                "denominator_epsilon": {"value": 0.1},
            }
        }
        
        sku_obj = all_skus[0]  # SKU001
        pooled_events, source = hierarchical_pooling(
            "SKU001", sku_obj, all_skus, promo_windows, sales, [], settings
        )
        
        # Should fallback to global (no category/dept match)
        assert source == "global"


class TestEstimateUplift:
    """Integration test for full uplift estimation workflow."""
    
    def test_sku_with_sufficient_data(self):
        """SKU has sufficient promo events → SKU-level estimate with confidence A."""
        all_skus = [
            SKU(sku="SKU001", description="Product 1", category="CAT_A", department="DEPT_X"),
        ]
        
        # Multiple promo events for SKU001
        promo_windows = [
            PromoWindow(sku="SKU001", start_date=date(2024, 1, 1), end_date=date(2024, 1, 5)),
            PromoWindow(sku="SKU001", start_date=date(2024, 2, 1), end_date=date(2024, 2, 5)),
            PromoWindow(sku="SKU001", start_date=date(2024, 3, 1), end_date=date(2024, 3, 5)),
        ]
        
        # Historical sales (before promo)
        sales = []
        for day_offset in range(-60, 0):
            sales.append(
                SalesRecord(sku="SKU001", date=date(2024, 1, 1) + timedelta(days=day_offset), qty_sold=10, promo_flag=0)
            )
        
        # Promo period sales (2x baseline)
        for event in promo_windows:
            current = event.start_date
            while current <= event.end_date:
                sales.append(SalesRecord(sku="SKU001", date=current, qty_sold=20, promo_flag=1))
                current += timedelta(days=1)
        
        settings = {
            "promo_uplift": {
                "min_uplift": {"value": 1.0},
                "max_uplift": {"value": 3.0},
                "min_events_sku": {"value": 3},
                "min_valid_days_sku": {"value": 7},
                "min_events_category": {"value": 5},
                "min_events_department": {"value": 10},
                "winsorize_trim_percent": {"value": 10.0},
                "denominator_epsilon": {"value": 0.1},
                "confidence_threshold_a": {"value": 3},
                "confidence_threshold_b": {"value": 5},
            }
        }
        
        report = estimate_uplift("SKU001", all_skus, promo_windows, sales, [], settings)
        
        assert report.sku == "SKU001"
        assert report.uplift_factor >= 1.0  # Should be elevated due to promo
        assert report.uplift_factor <= 3.0  # Within guardrails
        assert report.confidence == "A"  # SKU has >= 3 events
        assert report.pooling_source == "SKU"
        assert report.n_events >= 3
        assert report.n_valid_days_total >= 7
    
    def test_sku_not_found(self):
        """SKU not in all_skus → return neutral uplift with confidence C."""
        report = estimate_uplift("UNKNOWN_SKU", [], [], [], [], {})
        
        assert report.sku == "UNKNOWN_SKU"
        assert report.uplift_factor == 1.0  # Neutral
        assert report.confidence == "C"
        assert report.pooling_source == "not_found"
        assert report.n_events == 0
    
    def test_no_promo_events(self):
        """SKU has no promo events → pooling fallback or neutral uplift."""
        all_skus = [
            SKU(sku="SKU001", description="Product 1", category="", department=""),
        ]
        
        sales = [
            SalesRecord(sku="SKU001", date=date(2024, 1, 1), qty_sold=10, promo_flag=0),
        ]
        
        settings = {
            "promo_uplift": {
                "min_uplift": {"value": 1.0},
                "max_uplift": {"value": 3.0},
                "min_events_sku": {"value": 3},
                "min_valid_days_sku": {"value": 7},
                "min_events_category": {"value": 5},
                "min_events_department": {"value": 10},
                "winsorize_trim_percent": {"value": 10.0},
                "denominator_epsilon": {"value": 0.1},
                "confidence_threshold_a": {"value": 3},
                "confidence_threshold_b": {"value": 5},
            }
        }
        
        report = estimate_uplift("SKU001", all_skus, [], sales, [], settings)
        
        # No promo windows → pooling to global (empty) → neutral uplift
        assert report.uplift_factor == 1.0
        assert report.confidence == "C"  # Low confidence
        assert report.n_events == 0
