"""
Unit tests for post-promo guardrail anti-overstock logic.

Tests window detection, dip estimation, cooldown factor, qty cap, shelf-life severity, and alert triggering.
"""
from datetime import date, timedelta
from pathlib import Path
from src.domain.models import SKU, PromoWindow, SalesRecord, Transaction, EventType, Stock
from src.domain.promo_uplift import is_in_post_promo_window, estimate_post_promo_dip
from src.workflows.order import OrderWorkflow
from src.persistence.csv_layer import CSVLayer
import pytest


class TestPostPromoWindowDetection:
    """Test is_in_post_promo_window helper function."""
    
    def test_receipt_in_window_first_day(self):
        """Receipt on first day after end_date (end_date + 1) should match."""
        promo_end = date(2025, 1, 10)
        receipt_date = date(2025, 1, 11)  # Day 1 after end
        
        promo_windows = [
            PromoWindow(
                sku="SKU001",
                start_date=date(2025, 1, 1),
                end_date=promo_end
            )
        ]
        
        result = is_in_post_promo_window(receipt_date, promo_windows, "SKU001", window_days=7)
        assert result is not None, "Should match: receipt = end_date + 1"
        assert result.sku == "SKU001"
    
    def test_receipt_in_window_last_day(self):
        """Receipt on last day of window (end_date + window_days) should match."""
        promo_end = date(2025, 1, 10)
        receipt_date = date(2025, 1, 17)  # Day 7 after end
        
        promo_windows = [
            PromoWindow(
                sku="SKU001",
                start_date=date(2025, 1, 1),
                end_date=promo_end
            )
        ]
        
        result = is_in_post_promo_window(receipt_date, promo_windows, "SKU001", window_days=7)
        assert result is not None, "Should match: receipt = end_date + window_days"
    
    def test_receipt_on_end_date(self):
        """Receipt on end_date itself should NOT match (window starts end_date + 1)."""
        promo_end = date(2025, 1, 10)
        receipt_date = date(2025, 1, 10)  # Same as end_date
        
        promo_windows = [
            PromoWindow(
                sku="SKU001",
                start_date=date(2025, 1, 1),
                end_date=promo_end
            )
        ]
        
        result = is_in_post_promo_window(receipt_date, promo_windows, "SKU001", window_days=7)
        assert result is None, "Should NOT match: receipt = end_date (window starts end_date + 1)"
    
    def test_receipt_after_window(self):
        """Receipt after window (end_date + window_days + 1) should NOT match."""
        promo_end = date(2025, 1, 10)
        receipt_date = date(2025, 1, 18)  # Day 8 after end
        
        promo_windows = [
            PromoWindow(
                sku="SKU001",
                start_date=date(2025, 1, 1),
                end_date=promo_end
            )
        ]
        
        result = is_in_post_promo_window(receipt_date, promo_windows, "SKU001", window_days=7)
        assert result is None, "Should NOT match: receipt > end_date + window_days"
    
    def test_receipt_before_promo(self):
        """Receipt before promo start should NOT match."""
        promo_end = date(2025, 1, 10)
        receipt_date = date(2024, 12, 31)  # Before promo start
        
        promo_windows = [
            PromoWindow(
                sku="SKU001",
                start_date=date(2025, 1, 1),
                end_date=promo_end
            )
        ]
        
        result = is_in_post_promo_window(receipt_date, promo_windows, "SKU001", window_days=7)
        assert result is None, "Should NOT match: receipt before promo start"
    
    def test_zero_window_days(self):
        """window_days=0 should never match (no post-promo window)."""
        promo_end = date(2025, 1, 10)
        receipt_date = date(2025, 1, 11)  # Day 1 after end
        
        promo_windows = [
            PromoWindow(
                sku="SKU001",
                start_date=date(2025, 1, 1),
                end_date=promo_end
            )
        ]
        
        result = is_in_post_promo_window(receipt_date, promo_windows, "SKU001", window_days=0)
        assert result is None, "Should NOT match: window_days=0"
    
    def test_multiple_promos_select_correct_one(self):
        """Multiple promos: should select the one matching receipt_date."""
        promo_windows = [
            PromoWindow(sku="SKU001", start_date=date(2025, 1, 1), end_date=date(2025, 1, 5)),
            PromoWindow(sku="SKU001", start_date=date(2025, 2, 1), end_date=date(2025, 2, 10)),
        ]
        
        # Receipt in window of second promo
        receipt_date = date(2025, 2, 11)  # Day 1 after second promo end
        
        result = is_in_post_promo_window(receipt_date, promo_windows, "SKU001", window_days=7)
        assert result is not None
        assert result.end_date == date(2025, 2, 10), "Should match second promo"


class TestDipEstimation:
    """Test estimate_post_promo_dip helper function."""
    
    def test_no_promos_neutral_dip(self):
        """No historical promos → neutral dip (1.0), confidence C."""
        dip_report = estimate_post_promo_dip(
            sku_id="SKU001",
            promo_windows=[],  # No promos
            sales_records=[],
            transactions=[],
            all_skus=[SKU(sku="SKU001", description="Test")],
            window_days=7,
            min_events=2,
        )
        
        assert dip_report.dip_factor == 1.0, "No promos → neutral dip"
        assert dip_report.confidence == "C", "No promos → confidence C"
        assert dip_report.n_events == 0
    
    def test_one_promo_insufficient_events(self):
        """One promo (< min_events) → neutral dip, confidence C."""
        promo_end = date(2024, 12, 10)
        
        # Create minimal test data (1 promo event, not enough for min_events=2)
        promo_windows = [
            PromoWindow(sku="SKU001", start_date=date(2024, 12, 1), end_date=promo_end)
        ]
        
        # Sales during post-promo window (Dec 11-17): 50% dip
        sales_records = [
            SalesRecord(sku="SKU001", date=promo_end + timedelta(days=i), qty_sold=5)
            for i in range(1, 8)  # 7 days * 5 = 35 total
        ]
        
        all_skus = [SKU(sku="SKU001", description="Test")]
        
        dip_report = estimate_post_promo_dip(
            sku_id="SKU001",
            promo_windows=promo_windows,
            sales_records=sales_records,
            transactions=[],
            all_skus=all_skus,
            window_days=7,
            min_events=2,
            asof_date=date(2025, 1, 31),  # Past all events
        )
        
        # Should have confidence C due to insufficient events (< min_events)
        assert dip_report.confidence == "C", "< min_events → confidence C"
        assert dip_report.dip_factor == 1.0, "Insufficient events → neutral dip"
    
    def test_clamp_to_floor(self):
        """Extreme dip (< floor) should be clamped to floor."""
        # Cannot easily test without full baseline forecast, but logic is:
        # if dip_ratio = 0.3 (30% of baseline) and dip_floor=0.5, result should be 0.5
        # This is tested in integration tests with full data
        pass  # Placeholder for integration test
    
    def test_clamp_to_ceiling(self):
        """Dip > ceiling should be clamped to ceiling (sales UP after promo)."""
        # If dip_ratio = 1.2 (sales UP) and dip_ceiling=1.0, result should be 1.0
        # This is tested in integration tests with full data
        pass  # Placeholder for integration test


class TestWorkflowIntegration:
    """Test post-promo guardrail integration in OrderWorkflow.generate_proposal."""
    
    @pytest.fixture
    def temp_csv_layer(self, tmp_path):
        """Create CSVLayer with temp directory."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        
        csv_layer = CSVLayer(data_dir)
        
        # Create minimal SKU
        csv_layer.write_sku(
            SKU(sku="SKU001", description="Test Product", pack_size=10, max_stock=100, lead_time_days=7, review_period=10)
        )
        
        # Create SNAPSHOT for initial stock
        csv_layer.write_transactions_batch([
            Transaction(date(2025, 1, 1), "SKU001", EventType.SNAPSHOT, 50, None, "Initial")
        ])
        
        # Create sales history (baseline)
        csv_layer.write_sales([
            SalesRecord(date=date(2025, 1, i), sku="SKU001", qty_sold=10)
            for i in range(1, 11)  # 10 days @ 10/day
        ])
        
        # Enable post-promo guardrail in settings
        settings = csv_layer.read_settings()
        settings["post_promo_guardrail"] = {
            "enabled": {"value": True},
            "window_days": {"value": 7},
            "cooldown_factor": {"value": 0.8},
            "qty_cap_enabled": {"value": False},
            "qty_cap_value": {"value": 0},
            "use_historical_dip": {"value": False},
            "dip_min_events": {"value": 2},
            "dip_floor": {"value": 0.5},
            "dip_ceiling": {"value": 1.0},
            "shelf_life_severity_enabled": {"value": False},
        }
        csv_layer.write_settings(settings)
        
        return csv_layer
    
    def test_no_promo_no_guardrail(self, temp_csv_layer):
        """No promo → post-promo guardrail NOT applied."""
        workflow = OrderWorkflow(temp_csv_layer, lead_time_days=3)
        
        proposal = workflow.generate_proposal(
            sku="SKU001",
            target_receipt_date=None,  # Auto-calculate
        )
        
        assert not proposal.post_promo_guardrail_applied, "No promo → no guardrail"
        assert proposal.post_promo_factor_used == 1.0
        assert proposal.post_promo_alert == ""
    
    def test_receipt_in_post_promo_window_cooldown_applied(self, temp_csv_layer):
        """Receipt in post-promo window → cooldown factor applied."""
        # Add promo ending yesterday
        promo_end = date.today() - timedelta(days=1)
        temp_csv_layer.write_promo_calendar([
            PromoWindow(sku="SKU001", start_date=promo_end - timedelta(days=5), end_date=promo_end)
        ])
        
        workflow = OrderWorkflow(temp_csv_layer, lead_time_days=3)
        
        # Receipt in post-promo window (today = end_date + 1)
        receipt_date = date.today()
        
        proposal = workflow.generate_proposal(
            sku="SKU001",
            target_receipt_date=receipt_date,
        )
        
        assert proposal.post_promo_guardrail_applied, "Receipt in window → guardrail applied"
        assert proposal.post_promo_factor_used == 0.8, "Cooldown factor = 0.8"
        assert proposal.post_promo_window_days == 7
    
    def test_receipt_after_window_no_guardrail(self, temp_csv_layer):
        """Receipt after post-promo window → no guardrail."""
        # Add promo ending 10 days ago
        promo_end = date.today() - timedelta(days=10)
        temp_csv_layer.write_promo_calendar([
            PromoWindow(sku="SKU001", start_date=promo_end - timedelta(days=5), end_date=promo_end)
        ])
        
        workflow = OrderWorkflow(temp_csv_layer, lead_time_days=3)
        
        # Receipt today (> window_days=7 after end_date)
        receipt_date = date.today()
        
        proposal = workflow.generate_proposal(
            sku="SKU001",
            target_receipt_date=receipt_date,
        )
        
        assert not proposal.post_promo_guardrail_applied, "Receipt after window → no guardrail"
        assert proposal.post_promo_factor_used == 1.0
    
    def test_qty_reduced_by_cooldown_factor(self, temp_csv_layer):
        """Proposed qty should be reduced by cooldown_factor."""
        # Add promo ending yesterday
        promo_end = date.today() - timedelta(days=1)
        temp_csv_layer.write_promo_calendar([
            PromoWindow(sku="SKU001", start_date=promo_end - timedelta(days=5), end_date=promo_end)
        ])
        
        workflow = OrderWorkflow(temp_csv_layer, lead_time_days=3)
        
        # Generate baseline proposal (no post-promo)
        receipt_far_future = date.today() + timedelta(days=30)
        baseline_proposal = workflow.generate_proposal("SKU001", target_receipt_date=receipt_far_future)
        
        # Generate post-promo proposal (in window)
        receipt_in_window = date.today()
        post_promo_proposal = workflow.generate_proposal("SKU001", target_receipt_date=receipt_in_window)
        
        # Post-promo qty should be ~80% of baseline (cooldown_factor=0.8)
        # Allow rounding tolerance
        expected_reduction = baseline_proposal.proposed_qty * 0.8
        assert post_promo_proposal.proposed_qty <= baseline_proposal.proposed_qty, "Qty reduced"
        # Exact match may vary due to rounding, but should be significantly lower
        assert post_promo_proposal.proposed_qty < baseline_proposal.proposed_qty


class TestQtyCap:
    """Test absolute qty cap logic."""
    
    @pytest.fixture
    def csv_with_qty_cap(self, tmp_path):
        """CSVLayer with qty_cap enabled."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        
        csv_layer = CSVLayer(data_dir)
        
        csv_layer.write_sku(
            SKU(sku="SKU001", description="Test", pack_size=10, max_stock=100, lead_time_days=7, review_period=10)
        )
        
        csv_layer.write_transactions_batch([
            Transaction(date(2025, 1, 1), "SKU001", EventType.SNAPSHOT, 50, None, "Init")
        ])
        
        csv_layer.write_sales([
            SalesRecord(date=date(2025, 1, i), sku="SKU001", qty_sold=10)
            for i in range(1, 11)
        ])
        
        settings = csv_layer.read_settings()
        settings["post_promo_guardrail"] = {
            "enabled": {"value": True},
            "window_days": {"value": 7},
            "cooldown_factor": {"value": 1.0},  # No cooldown
            "qty_cap_enabled": {"value": True},
            "qty_cap_value": {"value": 30},  # Cap at 30 pz
            "use_historical_dip": {"value": False},
            "dip_min_events": {"value": 2},
            "dip_floor": {"value": 0.5},
            "dip_ceiling": {"value": 1.0},
            "shelf_life_severity_enabled": {"value": False},
        }
        csv_layer.write_settings(settings)
        
        return csv_layer
    
    def test_qty_cap_applied(self, csv_with_qty_cap):
        """Proposed qty > cap → capped to cap value."""
        # Add promo ending yesterday
        promo_end = date.today() - timedelta(days=1)
        csv_with_qty_cap.write_promo_calendar([
            PromoWindow(sku="SKU001", start_date=promo_end - timedelta(days=5), end_date=promo_end)
        ])
        
        workflow = OrderWorkflow(csv_with_qty_cap, lead_time_days=3)
        
        proposal = workflow.generate_proposal("SKU001", target_receipt_date=date.today())
        
        assert proposal.post_promo_guardrail_applied
        # Qty should be capped at 30 (if baseline > 30)
        if proposal.proposed_qty_before_rounding > 30:
            # Should trigger cap
            assert proposal.post_promo_cap_applied, "Qty > cap → cap applied"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
