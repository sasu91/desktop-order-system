"""
Test suite for CSL-based replenishment policy.

Tests verify critical policy behaviors:
1. Higher CSL → Higher/equal order quantity (monotonicity)
2. Pack size, MOQ, cap constraints respected
3. Inventory position changes propagate correctly
4. Deterministic calculations (no randomness)

Author: Desktop Order System Team
Date: February 2026
"""

import pytest
from datetime import date, timedelta
from src.replenishment_policy import (
    compute_order,
    compute_order_batch,
    OrderConstraints,
    _z_score_for_csl,
    _apply_pack_size,
    _apply_moq,
    _apply_cap,
    _calculate_inventory_position,
)
from src.domain.calendar import Lane


# Test data: simple stable demand
def _generate_stable_history(days: int = 90, daily_qty: float = 10.0):
    """Generate stable demand history for testing."""
    return [
        {"date": date(2024, 1, 1) + timedelta(days=i), "qty_sold": daily_qty}
        for i in range(days)
    ]


# Test data: volatile demand
def _generate_volatile_history(days: int = 90):
    """Generate volatile demand history for testing."""
    import random
    random.seed(42)
    return [
        {"date": date(2024, 1, 1) + timedelta(days=i), 
         "qty_sold": 10.0 + random.uniform(-3, 3)}
        for i in range(days)
    ]


class TestZScoreLookup:
    """Test z-score lookup for CSL."""
    
    def test_common_csl_values(self):
        """Test common CSL → z-score mappings."""
        assert _z_score_for_csl(0.50) == 0.000
        assert _z_score_for_csl(0.90) == 1.282
        assert _z_score_for_csl(0.95) == 1.645
        assert _z_score_for_csl(0.99) == 2.326
    
    def test_custom_csl_approximation(self):
        """Non-standard CSL uses closest value."""
        z = _z_score_for_csl(0.96)  # Between 0.95 and 0.98
        assert 1.6 < z < 2.1  # Should be close to one of them


class TestPackSizeRounding:
    """Test pack size constraint."""
    
    def test_exact_multiple(self):
        """Exact multiple → no rounding."""
        assert _apply_pack_size(20.0, 10) == 20
    
    def test_round_up(self):
        """Fractional → round up to next pack."""
        assert _apply_pack_size(21.0, 10) == 30  # 21 → 30 (3 packs)
        assert _apply_pack_size(10.1, 5) == 15   # 10.1 → 15 (3 packs)
    
    def test_zero_quantity(self):
        """Zero quantity → zero packs."""
        assert _apply_pack_size(0.0, 10) == 0
    
    def test_pack_size_one(self):
        """Pack size = 1 → no rounding effect."""
        assert _apply_pack_size(10.7, 1) == 11


class TestMOQConstraint:
    """Test Minimum Order Quantity constraint."""
    
    def test_above_moq(self):
        """Quantity above MOQ → unchanged."""
        assert _apply_moq(50, 10) == 50
        assert _apply_moq(10, 10) == 10  # Exactly at MOQ
    
    def test_below_moq(self):
        """Quantity below MOQ → zero (don't order)."""
        assert _apply_moq(5, 10) == 0
        assert _apply_moq(9, 10) == 0
    
    def test_zero_moq(self):
        """MOQ = 0 → no constraint."""
        assert _apply_moq(1, 0) == 1
        assert _apply_moq(5, 0) == 5


class TestCapConstraint:
    """Test maximum stock cap constraint."""
    
    def test_no_cap(self):
        """None cap → no limit."""
        assert _apply_cap(100, 50, None) == 100
    
    def test_within_cap(self):
        """Order within cap → unchanged."""
        # IP=50, cap=200, available=150, order=100 → 100 OK
        assert _apply_cap(100, 50, 200) == 100
    
    def test_exceeds_cap(self):
        """Order exceeds cap → reduce to available."""
        # IP=50, cap=120, available=70, order=100 → 70
        assert _apply_cap(100, 50, 120) == 70
    
    def test_already_over_cap(self):
        """Already over cap → zero order."""
        # IP=150, cap=100, available=0 (negative capped to 0)
        assert _apply_cap(100, 150, 100) == 0


class TestInventoryPosition:
    """Test inventory position calculation."""
    
    def test_on_hand_only(self):
        """No pipeline → IP = on-hand."""
        ip = _calculate_inventory_position(
            on_hand=50,
            pipeline=[],
            forecast_date=date(2024, 2, 10)
        )
        assert ip == 50.0
    
    def test_with_pipeline_before(self):
        """Pipeline arriving before forecast date → included."""
        ip = _calculate_inventory_position(
            on_hand=50,
            pipeline=[
                {"receipt_date": date(2024, 2, 5), "qty": 20},
                {"receipt_date": date(2024, 2, 15), "qty": 30}
            ],
            forecast_date=date(2024, 2, 10)
        )
        # 50 + 20 (arrives before) = 70
        assert ip == 70.0
    
    def test_pipeline_after_excluded(self):
        """Pipeline arriving after forecast date → excluded."""
        ip = _calculate_inventory_position(
            on_hand=50,
            pipeline=[
                {"receipt_date": date(2024, 2, 20), "qty": 100}
            ],
            forecast_date=date(2024, 2, 10)
        )
        assert ip == 50.0  # Pipeline not counted


class TestCSLMonotonicity:
    """Test CRITICAL requirement: Higher CSL → Higher/equal order quantity."""
    
    def test_alpha_increase_monotonic(self):
        """Higher α → order_final does not decrease."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=1, moq=0, max_stock=None)
        
        # Compute for α=0.90
        result_90 = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.90,
            on_hand=20,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # Compute for α=0.95 (higher CSL)
        result_95 = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=20,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # CRITICAL: Higher CSL → higher or equal order
        assert result_95["order_final"] >= result_90["order_final"], \
            f"α=0.95 order ({result_95['order_final']}) < α=0.90 order ({result_90['order_final']})"
        
        # Also check reorder point monotonicity
        assert result_95["reorder_point"] >= result_90["reorder_point"]
    
    def test_alpha_sequence_monotonic(self):
        """Test monotonicity across sequence of α values."""
        history = _generate_volatile_history(days=90)
        constraints = OrderConstraints(pack_size=1, moq=0, max_stock=None)
        
        alphas = [0.80, 0.85, 0.90, 0.95, 0.98]
        orders = []
        
        for alpha in alphas:
            result = compute_order(
                sku="TEST",
                order_date=date(2024, 2, 1),
                lane=Lane.STANDARD,
                alpha=alpha,
                on_hand=50,
                pipeline=[],
                constraints=constraints,
                history=history
            )
            orders.append(result["order_final"])
        
        # Verify monotonicity: each order >= previous
        for i in range(len(orders) - 1):
            assert orders[i + 1] >= orders[i], \
                f"Non-monotonic at α={alphas[i]}: {orders[i]} → {orders[i+1]}"


class TestPackSizeCompliance:
    """Test that order_final respects pack size multiples."""
    
    def test_pack_size_10(self):
        """Pack size 10 → order_final must be multiple of 10."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=10, moq=0)
        
        result = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=5,  # Low stock → likely order
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # Check multiple of pack size
        assert result["order_final"] % 10 == 0, \
            f"order_final {result['order_final']} not multiple of pack_size 10"
    
    def test_pack_size_25(self):
        """Pack size 25 → order_final must be multiple of 25."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=25, moq=0)
        
        result = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=10,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        if result["order_final"] > 0:
            assert result["order_final"] % 25 == 0


class TestMOQCompliance:
    """Test that order_final respects MOQ constraint."""
    
    def test_moq_respected(self):
        """Order below MOQ → zero, above MOQ → unchanged."""
        history = _generate_stable_history(days=90, daily_qty=5.0)  # Low demand
        
        # Case 1: High MOQ → likely zero order
        constraints_high = OrderConstraints(pack_size=1, moq=100)
        result_high = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=80,
            pipeline=[],
            constraints=constraints_high,
            history=history
        )
        
        # Either 0 (below MOQ) or >= MOQ
        assert result_high["order_final"] == 0 or result_high["order_final"] >= 100
        
        # Case 2: Low MOQ → likely order
        constraints_low = OrderConstraints(pack_size=1, moq=5)
        result_low = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=5,  # Low stock
            pipeline=[],
            constraints=constraints_low,
            history=history
        )
        
        if result_low["order_final"] > 0:
            assert result_low["order_final"] >= 5


class TestCapCompliance:
    """Test that order_final respects max_stock cap."""
    
    def test_cap_enforced(self):
        """order_final + IP must not exceed max_stock."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=1, moq=0, max_stock=100)
        
        # High demand scenario
        result = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=20,
            pipeline=[{"receipt_date": date(2024, 2, 10), "qty": 30}],
            constraints=constraints,
            history=history
        )
        
        # IP = 20 + 30 = 50, cap = 100, max order = 50
        ip = result["inventory_position"]
        order = result["order_final"]
        
        assert ip + order <= 100, \
            f"IP({ip}) + order({order}) = {ip + order} exceeds cap(100)"
    
    def test_no_cap_unlimited(self):
        """max_stock=None → no limit."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=1, moq=0, max_stock=None)
        
        result = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=5,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # No cap constraint in applied list
        cap_constraints = [c for c in result["constraints_applied"] if "max_stock" in c]
        assert len(cap_constraints) == 0


class TestInventoryPositionImpact:
    """Test CRITICAL requirement: Higher IP → Lower/equal order."""
    
    def test_on_hand_increase_reduces_order(self):
        """Higher on-hand → lower or equal order."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=1, moq=0)
        
        # Low on-hand
        result_low = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=10,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # High on-hand
        result_high = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=100,  # Much higher
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # Higher on-hand → lower or equal order
        assert result_high["order_final"] <= result_low["order_final"], \
            f"Higher on-hand ({result_high['on_hand']}) produced higher order ({result_high['order_final']})"
    
    def test_pipeline_increase_reduces_order(self):
        """Higher on-order → lower or equal order."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=1, moq=0)
        
        # No pipeline
        result_no_pipe = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=20,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # With pipeline
        result_with_pipe = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=20,
            pipeline=[{"receipt_date": date(2024, 2, 5), "qty": 50}],
            constraints=constraints,
            history=history
        )
        
        # Pipeline increases IP → reduces order
        assert result_with_pipe["order_final"] <= result_no_pipe["order_final"]


class TestBreakdownCompleteness:
    """Test that result includes all required breakdown fields."""
    
    def test_all_fields_present(self):
        """Result must include comprehensive breakdown."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=10, moq=20, max_stock=500)
        
        result = compute_order(
            sku="TEST_SKU",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=30,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # Check required fields
        required_fields = [
            "sku", "order_date", "receipt_date", "lane", "alpha",
            "protection_period", "forecast_demand",
            "sigma_daily", "sigma_horizon", "z_score",
            "reorder_point", "on_hand", "on_order",
            "inventory_position", "order_raw",
            "order_after_pack", "order_after_moq", "order_final",
            "constraints_applied", "service_level_target"
        ]
        
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"
        
        # Check types
        assert isinstance(result["sku"], str)
        assert isinstance(result["order_date"], date)
        assert isinstance(result["protection_period"], int)
        assert isinstance(result["order_final"], int)
        assert isinstance(result["constraints_applied"], list)


class TestDeterminism:
    """Test that policy is deterministic (same input → same output)."""
    
    def test_same_input_same_output(self):
        """Multiple calls with same inputs produce same result."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=5, moq=10)
        
        kwargs = {
            "sku": "TEST",
            "order_date": date(2024, 2, 1),
            "lane": Lane.STANDARD,
            "alpha": 0.95,
            "on_hand": 25,
            "pipeline": [{"receipt_date": date(2024, 2, 5), "qty": 15}],
            "constraints": constraints,
            "history": history
        }
        
        result1 = compute_order(**kwargs)
        result2 = compute_order(**kwargs)
        result3 = compute_order(**kwargs)
        
        # All results identical
        assert result1["order_final"] == result2["order_final"] == result3["order_final"]
        assert result1["reorder_point"] == result2["reorder_point"] == result3["reorder_point"]


class TestBatchComputation:
    """Test batch order computation."""
    
    def test_batch_multiple_skus(self):
        """Compute orders for multiple SKUs."""
        history1 = _generate_stable_history(days=90, daily_qty=10.0)
        history2 = _generate_stable_history(days=90, daily_qty=5.0)
        
        results = compute_order_batch(
            skus=["SKU001", "SKU002"],
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            inventory_data={
                "SKU001": {"on_hand": 50, "pipeline": []},
                "SKU002": {"on_hand": 30, "pipeline": []}
            },
            constraints_map={
                "SKU001": OrderConstraints(pack_size=10),
                "SKU002": OrderConstraints(pack_size=5)
            },
            history_map={
                "SKU001": history1,
                "SKU002": history2
            }
        )
        
        assert "SKU001" in results
        assert "SKU002" in results
        assert results["SKU001"]["sku"] == "SKU001"
        assert results["SKU002"]["sku"] == "SKU002"
    
    def test_batch_missing_data_skipped(self):
        """SKUs with missing data are skipped gracefully."""
        history1 = _generate_stable_history(days=90, daily_qty=10.0)
        
        results = compute_order_batch(
            skus=["SKU001", "SKU_MISSING"],
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            inventory_data={
                "SKU001": {"on_hand": 50, "pipeline": []}
                # SKU_MISSING not in inventory_data
            },
            constraints_map={},
            history_map={
                "SKU001": history1
                # SKU_MISSING not in history_map
            }
        )
        
        assert "SKU001" in results
        assert "SKU_MISSING" not in results  # Skipped


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_zero_demand_history(self):
        """Zero demand → zero order."""
        history = _generate_stable_history(days=90, daily_qty=0.0)
        constraints = OrderConstraints(pack_size=1, moq=0)
        
        result = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=100,
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        # Zero demand → reorder point low → likely zero order
        assert result["order_final"] == 0
    
    def test_very_high_on_hand(self):
        """Very high on-hand → zero order."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints(pack_size=1, moq=0)
        
        result = compute_order(
            sku="TEST",
            order_date=date(2024, 2, 1),
            lane=Lane.STANDARD,
            alpha=0.95,
            on_hand=10000,  # Extremely high
            pipeline=[],
            constraints=constraints,
            history=history
        )
        
        assert result["order_final"] == 0
    
    def test_invalid_alpha_raises(self):
        """Invalid α (outside 0-1) raises ValueError."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints()
        
        with pytest.raises(ValueError, match="alpha must be in"):
            compute_order(
                sku="TEST",
                order_date=date(2024, 2, 1),
                lane=Lane.STANDARD,
                alpha=1.5,  # Invalid: > 1
                on_hand=50,
                pipeline=[],
                constraints=constraints,
                history=history
            )
    
    def test_negative_on_hand_raises(self):
        """Negative on-hand raises ValueError."""
        history = _generate_stable_history(days=90, daily_qty=10.0)
        constraints = OrderConstraints()
        
        with pytest.raises(ValueError, match="on_hand must be"):
            compute_order(
                sku="TEST",
                order_date=date(2024, 2, 1),
                lane=Lane.STANDARD,
                alpha=0.95,
                on_hand=-10,  # Invalid
                pipeline=[],
                constraints=constraints,
                history=history
            )
