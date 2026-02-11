"""
Test demand-adjusted waste risk calculations.

Validates that the new demand-adjusted waste risk algorithm correctly
accounts for expected demand consumption before lot expiry.
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, Transaction, EventType, Lot
from src.domain.ledger import ShelfLifeCalculator
from src.persistence.csv_layer import CSVLayer


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def csv_layer(temp_data_dir):
    """Create CSV layer with temp directory."""
    return CSVLayer(temp_data_dir)


def test_high_rotation_sku_scenario(csv_layer):
    """
    Test scenario from user requirements:
    - SKU sells 10 units/day
    - On hand: 70 units (10 exp +2d, 10 exp +3d, 50 exp +6d)
    - Lead time: 4 days
    - Order: 40 units (to reach target stock 70)
    
    Traditional forward risk: 30/70 = 42.9%
    Demand-adjusted: waste = max(0, 30 - 20) = 10 → 10/70 = 14.3%
    """
    # Setup
    daily_demand = 10.0
    lead_time = 4
    receipt_date = date.today() + timedelta(days=lead_time)
    
    # Create lots as they will be at receipt (after 4 days of FEFO consumption)
    # Original: 10@+2d, 10@+3d, 50@+6d
    # After day 0: 0@+2d, 10@+3d, 50@+6d (consumed 10 from first lot)
    # After day 1: 0@+2d, 0@+3d, 50@+6d (consumed 10 from second lot)
    # After day 2: 0@+2d, 0@+3d, 40@+6d (consumed 10 from third lot)
    # After day 3: 0@+2d, 0@+3d, 30@+6d (consumed 10 from third lot)
    # At receipt (day 4): 30 units remaining, expiring in 2 days (6-4=2)
    
    lot = Lot(
        lot_id="LOT_EXPIRING",
        sku="TEST_HIGH_ROT",
        expiry_date=receipt_date + timedelta(days=2),  # 2 days after receipt
        qty_on_hand=30,
        receipt_id="REC_1",
        receipt_date=receipt_date - timedelta(days=10)
    )
    
    lots = [lot]
    proposed_qty = 40  # Order to reach target 70
    sku_shelf_life_days = 60
    min_shelf_life_days = 1
    waste_horizon_days = 14
    
    # Calculate traditional forward waste risk
    trad_risk, trad_total, trad_expiring = ShelfLifeCalculator.calculate_forward_waste_risk(
        lots=lots,
        current_date=date.today(),
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=sku_shelf_life_days,
        min_shelf_life_days=min_shelf_life_days,
        waste_horizon_days=waste_horizon_days
    )
    
    # Calculate demand-adjusted waste risk
    (
        adj_risk,
        adj_total,
        adj_expiring,
        expected_waste
    ) = ShelfLifeCalculator.calculate_forward_waste_risk_demand_adjusted(
        lots=lots,
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=sku_shelf_life_days,
        min_shelf_life_days=min_shelf_life_days,
        waste_horizon_days=waste_horizon_days,
        forecast_daily_demand=daily_demand
    )
    
    # Assertions
    assert trad_total == 70, "Total stock should be 70 (30 existing + 40 incoming)"
    assert trad_expiring == 30, "Traditional: 30 units expiring within horizon"
    assert abs(trad_risk - 42.9) < 1.0, f"Traditional risk should be ~42.9%, got {trad_risk:.1f}%"
    
    # Demand-adjusted: 30 units expiring in 2 days, demand = 10*2 = 20
    # Expected waste = 30 - 20 = 10
    assert expected_waste == 10, f"Expected waste should be 10, got {expected_waste}"
    assert abs(adj_risk - 14.3) < 1.0, f"Adjusted risk should be ~14.3%, got {adj_risk:.1f}%"
    
    # Adjusted risk should be significantly lower than traditional
    assert adj_risk < trad_risk / 2, "Adjusted risk should be less than half of traditional risk"


def test_low_rotation_sku_no_change(csv_layer):
    """
    Test that low-rotation SKU still shows high waste risk.
    
    Scenario:
    - SKU sells 2 units/day
    - 30 units expiring in 5 days
    - Expected demand in 5 days = 2 * 5 = 10
    - Expected waste = 30 - 10 = 20 still high
    """
    receipt_date = date.today()
    daily_demand = 2.0
    
    lot = Lot(
        lot_id="LOT_SLOW",
        sku="TEST_LOW_ROT",
        expiry_date=receipt_date + timedelta(days=5),
        qty_on_hand=30,
        receipt_id="REC_SLOW",
        receipt_date=receipt_date - timedelta(days=30)
    )
    
    lots = [lot]
    proposed_qty = 20
    
    (
        adj_risk,
        adj_total,
        adj_expiring,
        expected_waste
    ) = ShelfLifeCalculator.calculate_forward_waste_risk_demand_adjusted(
        lots=lots,
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=60,
        min_shelf_life_days=1,
        waste_horizon_days=14,
        forecast_daily_demand=daily_demand
    )
    
    # Expected waste = 30 - (2 * 5) = 20
    assert expected_waste == 20, f"Expected waste should be 20, got {expected_waste}"
    
    # Risk = 20/50 = 40%
    assert adj_total == 50
    assert abs(adj_risk - 40.0) < 1.0, f"Adjusted risk should be ~40%, got {adj_risk:.1f}%"


def test_multi_lot_fefo_simulation(csv_layer):
    """
    Test FEFO simulation with multiple lots expiring at different times.
    
    Scenario:
    - Lot 1: 20 units, expiring in 2 days
    - Lot 2: 30 units, expiring in 5 days  
    - Lot 3: 50 units, expiring in 10 days
    - Daily demand: 15 units
    
    FEFO consumption:
    - Day 0-2: Consume lot 1 (20 units) + 10 from lot 2 = 30 units in 2 days
    - Day 2-5: Consume remaining 20 from lot 2 = 20 units in 3 days (but only 15*3=45 demand)
    - Lot 1: fully consumed before expiry (0 waste)
    - Lot 2: 30 - 15*3 = -15 → fully consumed (0 waste)
    - Lot 3: expires in 10 days, demand = 15*10 = 150 → fully consumed (0 waste)
    """
    receipt_date = date.today()
    daily_demand = 15.0
    
    lots = [
        Lot(
            lot_id="LOT_1",
            sku="TEST_MULTI",
            expiry_date=receipt_date + timedelta(days=2),
            qty_on_hand=20,
            receipt_id="REC_1",
            receipt_date=receipt_date - timedelta(days=50)
        ),
        Lot(
            lot_id="LOT_2",
            sku="TEST_MULTI",
            expiry_date=receipt_date + timedelta(days=5),
            qty_on_hand=30,
            receipt_id="REC_2",
            receipt_date=receipt_date - timedelta(days=40)
        ),
        Lot(
            lot_id="LOT_3",
            sku="TEST_MULTI",
            expiry_date=receipt_date + timedelta(days=10),
            qty_on_hand=50,
            receipt_id="REC_3",
            receipt_date=receipt_date - timedelta(days=30)
        ),
    ]
    
    proposed_qty = 50
    
    (
        adj_risk,
        adj_total,
        adj_expiring,
        expected_waste
    ) = ShelfLifeCalculator.calculate_forward_waste_risk_demand_adjusted(
        lots=lots,
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=60,
        min_shelf_life_days=1,
        waste_horizon_days=14,
        forecast_daily_demand=daily_demand
    )
    
    # All lots within 14 days → 100 units expiring soon
    assert adj_expiring == 100
    
    # Total stock = 100 + 50 incoming = 150
    assert adj_total == 150
    
    # With high demand (15/day), all lots should be consumed
    # Lot 1: 2 days * 15 = 30 demand → 20 consumed, 0 waste
    # Lot 2: consumed after lot 1, ~1.3 days of demand left → fully consumed
    # Lot 3: 10 days window → fully consumed
    # Total expected waste should be very low
    assert expected_waste < 10, f"Expected waste should be minimal, got {expected_waste}"


def test_zero_demand_fallback(csv_layer):
    """
    Test that with zero demand, algorithm falls back to traditional risk.
    """
    receipt_date = date.today()
    daily_demand = 0.0  # No forecast
    
    lot = Lot(
        lot_id="LOT_ZERO",
        sku="TEST_ZERO",
        expiry_date=receipt_date + timedelta(days=3),
        qty_on_hand=40,
        receipt_id="REC_ZERO",
        receipt_date=receipt_date - timedelta(days=30)
    )
    
    lots = [lot]
    proposed_qty = 30
    
    # Traditional risk
    trad_risk, trad_total, trad_expiring = ShelfLifeCalculator.calculate_forward_waste_risk(
        lots=lots,
        current_date=date.today(),
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=60,
        min_shelf_life_days=1,
        waste_horizon_days=14
    )
    
    # Demand-adjusted with zero demand
    (
        adj_risk,
        adj_total,
        adj_expiring,
        expected_waste
    ) = ShelfLifeCalculator.calculate_forward_waste_risk_demand_adjusted(
        lots=lots,
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=60,
        min_shelf_life_days=1,
        waste_horizon_days=14,
        forecast_daily_demand=daily_demand
    )
    
    # With zero demand, expected waste = all expiring stock
    assert expected_waste == trad_expiring, "Zero demand should result in all expiring stock as waste"
    assert abs(adj_risk - trad_risk) < 0.1, "Risk should match traditional when demand is zero"


def test_penalty_avoidance_with_demand_adjustment(csv_layer):
    """
    Test that penalty is avoided with demand adjustment for high-rotation SKU.
    
    Scenario similar to user's case:
    - Would trigger penalty with traditional risk (42.9% > 40% threshold)
    - Should NOT trigger with demand-adjusted (14.3% < 40%)
    """
    daily_demand = 10.0
    receipt_date = date.today() + timedelta(days=4)
    threshold = 40.0  # Common threshold
    
    # Stock at receipt: 30 units expiring in 2 days
    lot = Lot(
        lot_id="LOT_PENALTY",
        sku="TEST_PENALTY",
        expiry_date=receipt_date + timedelta(days=2),
        qty_on_hand=30,
        receipt_id="REC_P",
        receipt_date=receipt_date - timedelta(days=50)
    )
    
    lots = [lot]
    proposed_qty = 40
    
    # Traditional risk
    trad_risk, _, _ = ShelfLifeCalculator.calculate_forward_waste_risk(
        lots=lots,
        current_date=date.today(),
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=60,
        min_shelf_life_days=1,
        waste_horizon_days=14
    )
    
    # Demand-adjusted risk
    (adj_risk, _, _, _) = ShelfLifeCalculator.calculate_forward_waste_risk_demand_adjusted(
        lots=lots,
        receipt_date=receipt_date,
        proposed_qty=proposed_qty,
        sku_shelf_life_days=60,
        min_shelf_life_days=1,
        waste_horizon_days=14,
        forecast_daily_demand=daily_demand
    )
    
    # Traditional should trigger penalty
    assert trad_risk > threshold, f"Traditional risk {trad_risk:.1f}% should exceed threshold {threshold}%"
    
    # Adjusted should NOT trigger penalty
    assert adj_risk < threshold, f"Adjusted risk {adj_risk:.1f}% should be below threshold {threshold}%"
    
    # Verify significant difference
    improvement = trad_risk - adj_risk
    assert improvement > 20, f"Improvement should be >20%, got {improvement:.1f}%"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
