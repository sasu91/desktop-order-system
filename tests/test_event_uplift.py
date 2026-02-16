"""
Tests for event-aware demand driver (src/domain/event_uplift.py).

Validates:
- U_store_day estimation with similar days filtering
- beta_i estimation with hierarchical fallback
- m_i calculation and clamping
- Perishables policy (exclude/cap)
- Explainability completeness
- Fallback scenarios (no data, insufficient samples)
"""

import pytest
from datetime import date, timedelta
from src.domain.event_uplift import (
    apply_event_uplift_to_forecast,
    filter_similar_days,
    estimate_u_store_day,
    estimate_beta_i,
    EventUpliftExplain,
)
from src.domain.models import SKU, SalesRecord, EventUpliftRule


class TestSimilarDaysFiltering:
    """Test similar days filtering logic."""
    
    def test_filter_same_weekday(self):
        """Filter should return only same weekday as target."""
        target = date(2024, 3, 15)  # Friday
        
        sales = [
            SalesRecord(sku="SKU001", date=date(2024, 3, 14), qty_sold=10),  # Thursday
            SalesRecord(sku="SKU001", date=date(2024, 3, 15), qty_sold=15),  # Friday (same day)
            SalesRecord(sku="SKU001", date=date(2024, 3, 8), qty_sold=12),   # Friday (week before)
            SalesRecord(sku="SKU001", date=date(2024, 3, 16), qty_sold=8),   # Saturday
        ]
        
        similar = filter_similar_days(target, sales, sku_id="SKU001")
        
        # Should only return Fridays
        assert all(s.date.weekday() == target.weekday() for s in similar)
        assert len(similar) == 2  # 3/15 and 3/8
    
    def test_seasonal_window(self):
        """Filter should respect seasonal window (±N days)."""
        target = date(2024, 12, 25)  # Christmas
        
        sales = [
            # Same weekday (Wed), within seasonal window
            SalesRecord(sku="SKU001", date=date(2023, 12, 27), qty_sold=20),  # Previous year, +2 days
            SalesRecord(sku="SKU001", date=date(2024, 12, 18), qty_sold=18),  # -7 days (same weekday)
            SalesRecord(sku="SKU001", date=date(2024, 12, 25), qty_sold=25),  # Same day
            
            # Same weekday but outside seasonal window
            SalesRecord(sku="SKU001", date=date(2024, 11, 27), qty_sold=10),  # -28 days (too far)
        ]
        
        similar = filter_similar_days(target, sales, sku_id="SKU001", seasonal_window_days=10)
        
        # Should return 12/25 and 12/27 (within ±10 days)
        assert len(similar) >= 2
        assert all(abs((s.date.month - target.month)) <= 1 for s in similar)


class TestUStoreDayEstimation:
    """Test U_store_day (store-level event factor) estimation."""
    
    def test_u_estimation_with_sufficient_data(self):
        """With sufficient similar days, should estimate U > 1.0 for high-sales events."""
        target = date(2024, 12, 25)  # Wednesday (weekday=2)
        
        # Historical similar days (same weekday + within seasonal window):
        # Mix of regular and high-sales Wednesdays near Christmas season
        sales = []
        
        # Add regular Wednesdays in early December (within ±30 days of 12-25)
        regular_december_wednesdays = [
            date(2023, 12, 6),   # Wed, day_diff = 19
            date(2023, 12, 13),  # Wed, day_diff = 12
            date(2022, 12, 7),   # Wed, day_diff = 18
        ]
        for d in regular_december_wednesdays:
            sales.append(SalesRecord(sku="SKU001", date=d, qty_sold=10))
        
        # Add high-sales Wednesdays near Christmas (within seasonal window)
        christmas_wednesdays = [
            date(2023, 12, 27),  # Wed, day_diff = 2
            date(2022, 12, 28),  # Wed, day_diff = 3
            date(2021, 12, 22),  # Wed, day_diff = 3
            date(2020, 12, 23),  # Wed, day_diff = 2
            date(2019, 12, 25),  # Wed, day_diff = 0
        ]
        for d in christmas_wednesdays:
            if d < target:
                sales.append(SalesRecord(sku="SKU001", date=d, qty_sold=25))
        
        settings = {
            "event_uplift": {
                "default_quantile": {"value": 0.70},
                "min_factor": {"value": 1.0},
                "max_factor": {"value": 3.0},
                "similar_days_seasonal_window": {"value": 30},
                "min_samples_u_estimation": {"value": 3},
            }
        }
        
        u, level, n_samples = estimate_u_store_day(
            target_date=target,
            sales_records=sales,
            settings=settings,
        )
        
        assert u > 1.0  # Should detect uplift
        assert n_samples >= 3  # Sufficient samples
    
    def test_u_fallback_insufficient_samples(self):
        """With insufficient samples, should return neutral factor 1.0."""
        target = date(2024, 12, 25)
        
        # Only 2 similar days (below min_samples=5)
        sales = [
            SalesRecord(sku="SKU001", date=date(2024, 12, 18), qty_sold=10),
            SalesRecord(sku="SKU001", date=date(2024, 12, 11), qty_sold=12),
        ]
        
        settings = {
            "event_uplift": {
                "default_quantile": {"value": 0.70},
                "min_factor": {"value": 1.0},
                "max_factor": {"value": 3.0},
                "similar_days_seasonal_window": {"value": 30},
                "min_samples_u_estimation": {"value": 5},
            }
        }
        
        u, level, n_samples = estimate_u_store_day(
            target_date=target,
            sales_records=sales,
            settings=settings,
        )
        
        assert u == 1.0  # Neutral fallback
        assert "fallback" in level.lower()


class TestBetaIEstimation:
    """Test beta_i (SKU sensitivity) estimation with hierarchical fallback."""
    
    def test_beta_sku_level(self):
        """With sufficient SKU data, should estimate beta at SKU level."""
        sku = SKU(sku="SKU001", description="Test SKU", category="FOOD", department="FRESH")
        
        # Sufficient SKU sales (>10)
        sales = []
        for i in range(20):
            sales.append(SalesRecord(sku="SKU001", date=date(2024, 1, 1) + timedelta(days=i), qty_sold=10 + i % 5))
        
        settings = {
            "event_uplift": {
                "min_samples_beta_estimation": {"value": 10},
                "beta_normalization_mode": {"value": "mean_one"},
            }
        }
        
        beta, level = estimate_beta_i(sku, [sku], sales, settings)
        
        assert level == "SKU"
        assert beta > 0.0  # Should have positive beta
    
    def test_beta_fallback_category(self):
        """With insufficient SKU data, should fall back to category."""
        sku_target = SKU(sku="SKU_NEW", description="New SKU", category="SNACKS", department="FOOD")
        sku_sister = SKU(sku="SKU_SISTER", description="Sister SKU", category="SNACKS", department="FOOD")
        
        # No sales for target SKU
        # Sufficient sales for sister SKU
        sales = []
        for i in range(15):
            sales.append(SalesRecord(sku="SKU_SISTER", date=date(2024, 1, 1) + timedelta(days=i), qty_sold=8 + i % 3))
        
        settings = {
            "event_uplift": {
                "min_samples_beta_estimation": {"value": 10},
                "beta_normalization_mode": {"value": "mean_one"},
            }
        }
        
        beta, level = estimate_beta_i(sku_target, [sku_target, sku_sister], sales, settings)
        
        assert "category:SNACKS" in level
        assert beta > 0.0
    
    def test_beta_fallback_global(self):
        """With no category/dept data, should fall back to global (neutral)."""
        sku = SKU(sku="SKU_ORPHAN", description="Orphan SKU", category="", department="")
        
        settings = {
            "event_uplift": {
                "min_samples_beta_estimation": {"value": 10},
                "beta_normalization_mode": {"value": "mean_one"},
            }
        }
        
        beta, level = estimate_beta_i(sku, [sku], [], settings)
        
        assert level == "global"
        assert beta == 1.0  # Neutral


class TestApplyEventUplift:
    """Test full apply_event_uplift_to_forecast function."""
    
    def test_happy_path_with_rule_match(self):
        """With matching rule, should apply uplift to baseline."""
        sku = SKU(sku="SKU001", description="Test SKU", category="FOOD", department="FRESH", lead_time_days=3, review_period=7)
        
        delivery_date = date(2024, 12, 25)
        horizon = [delivery_date + timedelta(days=i) for i in range(10)]
        
        baseline = {d: 10.0 for d in horizon}
        
        rule = EventUpliftRule(
            delivery_date=delivery_date,
            reason="holiday",
            strength=50.0,  # 50% uplift
            scope_type="ALL",
            scope_key="",
        )
        
        # Minimal sales for beta/U estimation
        sales = []
        
        # Add regular Wednesdays in early December (within seasonal window)
        regular_december_wednesdays = [
            date(2023, 12, 6),   # Wed
            date(2023, 12, 13),  # Wed
            date(2022, 12, 7),   # Wed
        ]
        for d in regular_december_wednesdays:
            sales.append(SalesRecord(sku="SKU001", date=d, qty_sold=10))
        
        # Add high-sales days matching weekday + seasonal window for delivery_date
        # delivery_date = 2024-12-25 (Wednesday), need similar Wednesdays near Christmas in past years
        christmas_wednesdays = [
            date(2023, 12, 27),  # Wed near Christmas, within ±30 days
            date(2022, 12, 28),  # Wed near Christmas
            date(2021, 12, 22),  # Wed near Christmas
            date(2020, 12, 23),  # Wed
            date(2019, 12, 25),  # Wed
        ]
        for d in christmas_wednesdays:
            sales.append(SalesRecord(sku="SKU001", date=d, qty_sold=20))  # Higher sales for event
        
        # Add more sales throughout the year for overall CV (not same weekday, filtered out)
        for i in range(30):
            sales.append(SalesRecord(sku="SKU001", date=date(2024, 1, 1) + timedelta(days=i), qty_sold=12))
        
        settings = {
            "event_uplift": {
                "enabled": {"value": True},
                "default_quantile": {"value": 0.70},
                "min_factor": {"value": 1.0},
                "max_factor": {"value": 2.0},
                "perishables_policy_exclude_if_shelf_life_days_lte": {"value": 3},
                "perishables_policy_cap_extra_cover_days_per_sku": {"value": 1},
                "similar_days_seasonal_window": {"value": 30},
                "min_samples_u_estimation": {"value": 5},
                "min_samples_beta_estimation": {"value": 10},
                "beta_normalization_mode": {"value": "mean_one"},
            }
        }
        
        adjusted, explain = apply_event_uplift_to_forecast(
            sku_obj=sku,
            delivery_date=delivery_date,
            horizon_dates=horizon,
            baseline_forecast=baseline,
            event_rules=[rule],
            all_skus=[sku],
            sales_records=sales,
            settings=settings,
        )
        
        # Assertions
        assert explain.sku == "SKU001"
        assert explain.rule_matched == rule
        assert explain.m_i > 1.0  # Should have uplift
        assert explain.impact_days_count > 0  # Should impact some days
        assert not explain.perishable_excluded
        
        # Adjusted forecast should differ from baseline for impacted days
        impacted_dates = [d for d in horizon if explain.impact_start_date <= d <= explain.impact_end_date]
        for d in impacted_dates:
            assert adjusted[d] > baseline[d]
    
    def test_perishable_exclusion(self):
        """Perishable SKUs with short shelf life should be excluded."""
        sku = SKU(
            sku="SKU_MILK",
            description="Fresh Milk",
            category="DAIRY",
            department="FRESH",
            shelf_life_days=2,  # Very short shelf life
            lead_time_days=1,
            review_period=3,
        )
        
        delivery_date = date(2024, 12, 25)
        horizon = [delivery_date + timedelta(days=i) for i in range(5)]
        baseline = {d: 10.0 for d in horizon}
        
        rule = EventUpliftRule(
            delivery_date=delivery_date,
            reason="holiday",
            strength=80.0,
            scope_type="ALL",
            scope_key="",
        )
        
        settings = {
            "event_uplift": {
                "enabled": {"value": True},
                "default_quantile": {"value": 0.70},
                "min_factor": {"value": 1.0},
                "max_factor": {"value": 2.0},
                "perishables_policy_exclude_if_shelf_life_days_lte": {"value": 3},  # Exclude <= 3 days
            }
        }
        
        adjusted, explain = apply_event_uplift_to_forecast(
            sku_obj=sku,
            delivery_date=delivery_date,
            horizon_dates=horizon,
            baseline_forecast=baseline,
            event_rules=[rule],
            all_skus=[sku],
            sales_records=[],
            settings=settings,
        )
        
        # Assertions
        assert explain.perishable_excluded
        assert explain.m_i == 1.0  # No uplift
        assert "shelf_life" in explain.exclusion_reason.lower()
        
        # Forecast should be unchanged
        for d in horizon:
            assert adjusted[d] == baseline[d]
    
    def test_no_rule_matched(self):
        """With no matching rule, should return baseline unchanged."""
        sku = SKU(sku="SKU001", description="Test SKU", category="FOOD", department="FRESH", lead_time_days=3, review_period=7)
        
        delivery_date = date(2024, 12, 25)
        horizon = [delivery_date + timedelta(days=i) for i in range(5)]
        baseline = {d: 10.0 for d in horizon}
        
        # Rule for different date
        rule = EventUpliftRule(
            delivery_date=date(2024, 12, 31),  # Different date
            reason="holiday",
            strength=50.0,
            scope_type="ALL",
            scope_key="",
        )
        
        settings = {
            "event_uplift": {
                "enabled": {"value": True},
                "default_quantile": {"value": 0.70},
            }
        }
        
        adjusted, explain = apply_event_uplift_to_forecast(
            sku_obj=sku,
            delivery_date=delivery_date,
            horizon_dates=horizon,
            baseline_forecast=baseline,
            event_rules=[rule],
            all_skus=[sku],
            sales_records=[],
            settings=settings,
        )
        
        # Assertions
        assert explain.rule_matched is None
        assert explain.m_i == 1.0
        assert explain.impact_days_count == 0
        
        # Forecast unchanged
        for d in horizon:
            assert adjusted[d] == baseline[d]
    
    def test_explainability_completeness(self):
        """Explainability should always be complete (all fields populated)."""
        sku = SKU(sku="SKU001", description="Test", category="", department="", lead_time_days=3, review_period=7)
        
        delivery_date = date(2024, 12, 25)
        horizon = [delivery_date]
        baseline = {delivery_date: 10.0}
        
        settings = {
            "event_uplift": {
                "enabled": {"value": True},
                "default_quantile": {"value": 0.70},
            }
        }
        
        adjusted, explain = apply_event_uplift_to_forecast(
            sku_obj=sku,
            delivery_date=delivery_date,
            horizon_dates=horizon,
            baseline_forecast=baseline,
            event_rules=[],
            all_skus=[sku],
            sales_records=[],
            settings=settings,
        )
        
        # All explainability fields should be populated (not None)
        assert explain.sku is not None
        assert explain.delivery_date is not None
        assert explain.u_store_day is not None
        assert explain.u_quantile is not None
        assert explain.u_fallback_level is not None
        assert explain.beta_i is not None
        assert explain.beta_fallback_level is not None
        assert explain.m_i is not None
        assert explain.impact_start_date is not None
        assert explain.impact_end_date is not None
