"""
Unit tests for Promo Prebuild Anticipation Engine.

Tests the promo_prebuild feature that calculates target opening stock
at promo start and distributes prebuild orders across pre-start dates.

Key scenarios:
1. Basic prebuild calculation (target > projected → delta > 0)
2. No prebuild needed (target <= projected → delta = 0)
3. Edge cases: no promo, promo too far, target_date >= promo_start
4. Coverage days validation (0 = use lead_time)
5. Safety component modes (multiplier vs absolute)
6. Distribution note generation
"""

import pytest
from datetime import date, timedelta
from src.workflows.order import OrderWorkflow, calculate_prebuild_target
from src.persistence.csv_layer import CSVLayer
from src.domain.models import SKU, Transaction, EventType, SalesRecord, PromoWindow, DemandVariability
from src.domain.ledger import StockCalculator
from pathlib import Path
import tempfile


@pytest.fixture
def temp_csv_layer():
    """Create temporary CSV layer for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_layer = CSVLayer(data_dir=Path(tmpdir))
        yield csv_layer


@pytest.fixture
def sample_sku(temp_csv_layer):
    """Create sample SKU with typical parameters."""
    sku = SKU(
        sku="SKU001",
        description="Test Product",
        ean="1234567890123",
        moq=10,
        pack_size=5,
        lead_time_days=7,
        review_period=3,
        safety_stock=20,
        shelf_life_days=30,
        max_stock=500,
        reorder_point=50,
        demand_variability=DemandVariability.STABLE,  # Use enum
        oos_boost_percent=0.0,
        oos_detection_mode="strict",
        oos_popup_preference="ask",
    )
    temp_csv_layer.write_sku(sku)
    return sku


@pytest.fixture
def sample_sales(temp_csv_layer):
    """Create sample sales history (30 days of 10 pz/day)."""
    sales = []
    today = date.today()
    for i in range(1, 31):
        sales.append(SalesRecord(
            date=today - timedelta(days=i),
            sku="SKU001",
            qty_sold=10
        ))
    temp_csv_layer.write_sales(sales)
    return sales


@pytest.fixture
def sample_promo_upcoming(temp_csv_layer):
    """Create upcoming promo starting 10 days from now."""
    promo_start = date.today() + timedelta(days=10)
    promo_end = promo_start + timedelta(days=7)
    promo = PromoWindow(
        sku="SKU001",
        start_date=promo_start,
        end_date=promo_end,
        store_id=None,
        promo_flag=1  # Must be 1 for PromoWindow
    )
    temp_csv_layer.write_promo_calendar([promo])
    return promo


@pytest.fixture
def sample_stock_state(temp_csv_layer, sample_sku):
    """Create sample stock state: on_hand=50, on_order=30."""
    today = date.today()
    transactions = [
        Transaction(date=today - timedelta(days=5), sku="SKU001", event=EventType.SNAPSHOT, qty=50),
        Transaction(date=today - timedelta(days=2), sku="SKU001", event=EventType.ORDER, qty=30, receipt_date=today + timedelta(days=5)),
    ]
    temp_csv_layer.write_transactions_batch(transactions)
    
    stock = StockCalculator.calculate_asof("SKU001", today, transactions, [])
    return stock, transactions


def test_calculate_prebuild_target_basic(temp_csv_layer, sample_sku, sample_sales, sample_promo_upcoming):
    """Test basic prebuild target calculation with valid promo."""
    settings = temp_csv_layer.read_settings()
    promo_start = sample_promo_upcoming.start_date
    
    # Calculate target for 7-day coverage with 20% safety multiplier
    target_open, coverage_used, forecast_total = calculate_prebuild_target(
        sku="SKU001",
        promo_start_date=promo_start,
        coverage_days=7,
        safety_component_mode="multiplier",
        safety_component_value=0.2,
        promo_windows=temp_csv_layer.read_promo_calendar(),
        sales_records=temp_csv_layer.read_sales(),
        transactions=temp_csv_layer.read_transactions(),
        all_skus=temp_csv_layer.read_skus(),
        csv_layer=temp_csv_layer,
        settings=settings,
    )
    
    # With baseline 10 pz/day and promo uplift (if configured), expect forecast_total >= 70
    # Target = forecast + 20% safety
    assert coverage_used == 7
    assert forecast_total >= 70  # Baseline at minimum
    assert target_open == forecast_total + int(forecast_total * 0.2)


def test_calculate_prebuild_target_auto_coverage(temp_csv_layer, sample_sku, sample_sales, sample_promo_upcoming):
    """Test prebuild target with coverage_days=0 (use lead_time)."""
    settings = temp_csv_layer.read_settings()
    promo_start = sample_promo_upcoming.start_date
    
    # coverage_days=0 should use lead_time from settings (default 7)
    target_open, coverage_used, forecast_total = calculate_prebuild_target(
        sku="SKU001",
        promo_start_date=promo_start,
        coverage_days=0,  # Auto: use lead_time
        safety_component_mode="absolute",
        safety_component_value=10,
        promo_windows=temp_csv_layer.read_promo_calendar(),
        sales_records=temp_csv_layer.read_sales(),
        transactions=temp_csv_layer.read_transactions(),
        all_skus=temp_csv_layer.read_skus(),
        csv_layer=temp_csv_layer,
        settings=settings,
    )
    
    # Should use lead_time (default 7)
    assert coverage_used == 7
    assert target_open == forecast_total + 10  # Absolute safety


def test_prebuild_in_proposal_no_promo(temp_csv_layer, sample_sku, sample_sales, sample_stock_state):
    """Test proposal generation without upcoming promo: no prebuild."""
    stock, transactions = sample_stock_state
    
    # Enable prebuild in settings
    settings = temp_csv_layer.read_settings()
    settings["promo_prebuild"]["enabled"]["value"] = True
    
    workflow = OrderWorkflow(temp_csv_layer, lead_time_days=7)
    
    proposal = workflow.generate_proposal(
        sku="SKU001",
        description="Test Product",
        current_stock=stock,
        daily_sales_avg=10.0,
        sku_obj=sample_sku,
        target_receipt_date=date.today() + timedelta(days=7),
        transactions=transactions,
        sales_records=temp_csv_layer.read_sales(),
    )
    
    # No promo → no prebuild
    assert proposal.promo_prebuild_enabled is False
    assert proposal.prebuild_qty == 0


def test_prebuild_in_proposal_with_upcoming_promo(temp_csv_layer, sample_sku, sample_sales, sample_stock_state, sample_promo_upcoming):
    """Test proposal with upcoming promo: prebuild should be calculated."""
    stock, transactions = sample_stock_state
    
    # Enable prebuild in settings
    settings = temp_csv_layer.read_settings()
    settings["promo_prebuild"]["enabled"]["value"] = True
    settings["promo_prebuild"]["coverage_days"]["value"] = 5
    settings["promo_prebuild"]["safety_component_mode"]["value"] = "multiplier"
    settings["promo_prebuild"]["safety_component_value"]["value"] = 0.2
    settings["promo_prebuild"]["min_days_to_promo_start"]["value"] = 3
    temp_csv_layer.write_settings(settings)  # Write settings to CSV
    
    workflow = OrderWorkflow(temp_csv_layer, lead_time_days=7)
    
    # Order arriving 7 days from now (3 days before promo start)
    target_receipt = date.today() + timedelta(days=7)
    
    proposal = workflow.generate_proposal(
        sku="SKU001",
        description="Test Product",
        current_stock=stock,
        daily_sales_avg=10.0,
        sku_obj=sample_sku,
        target_receipt_date=target_receipt,
        transactions=transactions,
        sales_records=temp_csv_layer.read_sales(),
    )
    
    # Promo exists and target_receipt < promo_start → prebuild should activate
    assert proposal.promo_prebuild_enabled is True
    assert proposal.promo_start_date == sample_promo_upcoming.start_date
    assert proposal.target_open_qty > 0
    assert proposal.prebuild_coverage_days == 5
    
    # If delta > 0, prebuild_qty should be added
    if proposal.prebuild_delta_qty > 0:
        assert proposal.prebuild_qty == proposal.prebuild_delta_qty
        assert proposal.prebuild_distribution_note != ""
    else:
        # Projected >= target, no prebuild needed
        assert proposal.prebuild_qty == 0


def test_prebuild_not_activated_if_target_after_promo_start(temp_csv_layer, sample_sku, sample_sales, sample_stock_state, sample_promo_upcoming):
    """Test prebuild NOT activated if order arrives AFTER promo start."""
    stock, transactions = sample_stock_state
    
    # Enable prebuild
    settings = temp_csv_layer.read_settings()
    settings["promo_prebuild"]["enabled"]["value"] = True
    
    workflow = OrderWorkflow(temp_csv_layer, lead_time_days=7)
    
    # Order arriving AFTER promo starts
    target_receipt = sample_promo_upcoming.start_date + timedelta(days=1)
    
    proposal = workflow.generate_proposal(
        sku="SKU001",
        description="Test Product",
        current_stock=stock,
        daily_sales_avg=10.0,
        sku_obj=sample_sku,
        target_receipt_date=target_receipt,
        transactions=transactions,
        sales_records=temp_csv_layer.read_sales(),
    )
    
    # target_receipt >= promo_start → no prebuild
    assert proposal.promo_prebuild_enabled is False
    assert proposal.prebuild_qty == 0


def test_prebuild_min_days_to_promo_start_constraint(temp_csv_layer, sample_sku, sample_sales, sample_stock_state):
    """Test min_days_to_promo_start constraint: too close to promo start."""
    # Create promo starting only 2 days from now
    promo_start = date.today() + timedelta(days=2)
    promo_end = promo_start + timedelta(days=7)
    promo = PromoWindow(
        sku="SKU001",
        start_date=promo_start,
        end_date=promo_end,
        store_id=None,
        promo_flag=1
    )
    temp_csv_layer.write_promo_calendar([promo])
    
    stock, transactions = sample_stock_state
    
    # Enable prebuild with min_days=3
    settings = temp_csv_layer.read_settings()
    settings["promo_prebuild"]["enabled"]["value"] = True
    settings["promo_prebuild"]["min_days_to_promo_start"]["value"] = 3
    
    workflow = OrderWorkflow(temp_csv_layer, lead_time_days=7)
    
    # Order arriving 1 day before promo (only 1 day gap, < min 3)
    target_receipt = promo_start - timedelta(days=1)
    
    proposal = workflow.generate_proposal(
        sku="SKU001",
        description="Test Product",
        current_stock=stock,
        daily_sales_avg=10.0,
        sku_obj=sample_sku,
        target_receipt_date=target_receipt,
        transactions=transactions,
        sales_records=temp_csv_layer.read_sales(),
    )
    
    # Gap < min_days_to_promo_start → no prebuild
    assert proposal.promo_prebuild_enabled is False
    assert proposal.prebuild_qty == 0


def test_prebuild_max_horizon_constraint(temp_csv_layer, sample_sku, sample_sales, sample_stock_state):
    """Test max_prebuild_horizon_days constraint: promo too far in future."""
    # Create promo starting 40 days from now (exceeds max_horizon=30)
    promo_start = date.today() + timedelta(days=40)
    promo_end = promo_start + timedelta(days=7)
    promo = PromoWindow(
        sku="SKU001",
        start_date=promo_start,
        end_date=promo_end,
        store_id=None,
        promo_flag=1
    )
    temp_csv_layer.write_promo_calendar([promo])
    
    stock, transactions = sample_stock_state
    
    # Enable prebuild with max_horizon=30
    settings = temp_csv_layer.read_settings()
    settings["promo_prebuild"]["enabled"]["value"] = True
    settings["promo_prebuild"]["max_prebuild_horizon_days"]["value"] = 30
    
    workflow = OrderWorkflow(temp_csv_layer, lead_time_days=7)
    
    # Order arriving before promo but promo is > 30 days away
    target_receipt = date.today() + timedelta(days=7)
    
    proposal = workflow.generate_proposal(
        sku="SKU001",
        description="Test Product",
        current_stock=stock,
        daily_sales_avg=10.0,
        sku_obj=sample_sku,
        target_receipt_date=target_receipt,
        transactions=transactions,
        sales_records=temp_csv_layer.read_sales(),
    )
    
    # Promo too far → no prebuild
    assert proposal.promo_prebuild_enabled is False
    assert proposal.prebuild_qty == 0


def test_prebuild_delta_zero_when_projected_exceeds_target(temp_csv_layer, sample_sku, sample_sales, sample_promo_upcoming):
    """Test prebuild delta=0 when projected stock already sufficient."""
    # Create high on_hand + on_order so projected >> target
    today = date.today()
    transactions = [
        Transaction(date=today - timedelta(days=5), sku="SKU001", event=EventType.SNAPSHOT, qty=500),  # Very high stock
        Transaction(date=today - timedelta(days=2), sku="SKU001", event=EventType.ORDER, qty=200, receipt_date=today + timedelta(days=5)),
    ]
    temp_csv_layer.write_transactions_batch(transactions)
    
    stock = StockCalculator.calculate_asof("SKU001", today, transactions, [])
    
    # Enable prebuild
    settings = temp_csv_layer.read_settings()
    settings["promo_prebuild"]["enabled"]["value"] = True
    settings["promo_prebuild"]["coverage_days"]["value"] = 5
    temp_csv_layer.write_settings(settings)  # Write settings to CSV
    
    workflow = OrderWorkflow(temp_csv_layer, lead_time_days=7)
    
    target_receipt = date.today() + timedelta(days=7)
    
    proposal = workflow.generate_proposal(
        sku="SKU001",
        description="Test Product",
        current_stock=stock,
        daily_sales_avg=10.0,
        sku_obj=sample_sku,
        target_receipt_date=target_receipt,
        transactions=transactions,
        sales_records=temp_csv_layer.read_sales(),
    )
    
    # Prebuild activated but delta should be 0 (projected >> target)
    assert proposal.promo_prebuild_enabled is True
    assert proposal.projected_stock_on_promo_start > proposal.target_open_qty
    assert proposal.prebuild_delta_qty == 0
    assert proposal.prebuild_qty == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
