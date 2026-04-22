"""
Integration test: Verify calendar module works with existing domain models.

This test ensures calendar module is compatible with Transaction, SKU, Stock
and can be integrated into OrderWorkflow without breaking changes.
"""
import pytest
from datetime import date as Date
from src.domain.calendar import (
    Lane,
    next_receipt_date,
    protection_window,
    calculate_protection_period_days,
)
from src.domain.models import Transaction, EventType, SKU, Stock, OrderProposal


class TestCalendarIntegration:
    """Test calendar integration with existing domain models."""
    
    def test_calendar_receipt_date_matches_transaction_date(self):
        """Calendar receipt_date can be used as Transaction.date."""
        order_date = Date(2024, 2, 7)  # Wednesday (past date)
        receipt_date = next_receipt_date(order_date, Lane.STANDARD)
        
        # Create ORDER transaction with calendar receipt_date
        txn = Transaction(
            date=order_date,
            sku="SKU001",
            event=EventType.ORDER,
            qty=100,
            receipt_date=receipt_date,
            note="Order with calendar-calculated receipt_date"
        )
        
        assert txn.date == order_date
        assert txn.receipt_date == receipt_date
        assert receipt_date == Date(2024, 2, 8)  # Thursday
    
    def test_protection_period_in_order_proposal(self):
        """OrderProposal can store protection period from calendar."""
        order_date = Date(2024, 2, 9)  # Friday (past date)
        r1, r2, P = protection_window(order_date, Lane.SATURDAY)
        
        proposal = OrderProposal(
            sku="SKU001",
            description="Test Product",
            current_on_hand=50,
            current_on_order=0,
            daily_sales_avg=10.0,
            proposed_qty=30,
            receipt_date=r1,
            forecast_period_days=P,
            forecast_qty=P * 10.0,
            notes=f"Protection period: {P} days (Saturday lane)"
        )
        
        assert proposal.receipt_date == r1
        assert proposal.forecast_period_days == P
        assert proposal.forecast_period_days == 3  # Saturday lane P
    
    def test_calendar_aware_forecast_calculation(self):
        """Forecast calculation using calendar P instead of fixed lead_time."""
        daily_sales_avg = 10.0
        safety_stock = 20
        
        # Wednesday: P=1
        wed = Date(2024, 2, 7)
        P_wed = calculate_protection_period_days(wed, Lane.STANDARD)
        forecast_wed = daily_sales_avg * P_wed
        target_S_wed = forecast_wed + safety_stock
        
        assert P_wed == 1
        assert forecast_wed == 10.0
        assert target_S_wed == 30.0
        
        # Friday Saturday lane: P=3
        fri = Date(2024, 2, 9)
        P_sat = calculate_protection_period_days(fri, Lane.SATURDAY)
        forecast_sat = daily_sales_avg * P_sat
        target_S_sat = forecast_sat + safety_stock
        
        assert P_sat == 3
        assert forecast_sat == 30.0
        assert target_S_sat == 50.0
        
        # Same daily sales, different P -> different targets
        assert target_S_sat > target_S_wed
    
    def test_sku_model_compatible_with_calendar(self):
        """SKU model attributes work with calendar calculations."""
        sku = SKU(
            sku="SKU001",
            description="Test Product",
            ean="8001234567890",
            moq=10,
            lead_time_days=1,  # This becomes less important with calendar
            safety_stock=20,
        )
        
        # Use SKU.lead_time_days as default, but calendar P takes precedence
        order_date = Date(2024, 2, 9)  # Friday (past date)
        
        # Traditional: lead_time_demand = lead_time_days * daily_avg
        # Calendar-aware: forecast = P * daily_avg
        
        daily_sales_avg = 10.0
        traditional_forecast = sku.lead_time_days * daily_sales_avg  # 1 * 10 = 10
        
        P_sat = calculate_protection_period_days(order_date, Lane.SATURDAY)
        calendar_forecast = P_sat * daily_sales_avg  # 3 * 10 = 30
        
        # Calendar-aware forecast accounts for weekend coverage
        assert calendar_forecast > traditional_forecast
        assert calendar_forecast == 30.0
        assert traditional_forecast == 10.0
    
    def test_stock_model_unchanged_by_calendar(self):
        """Stock calculation remains independent of calendar (as it should)."""
        # Calendar only affects ORDER planning, not stock calculation
        stock = Stock(
            sku="SKU001",
            on_hand=100,
            on_order=50,
            unfulfilled_qty=0,
            asof_date=Date(2024, 2, 7)
        )
        
        # Stock values are calendar-independent
        assert stock.on_hand == 100
        assert stock.on_order == 50
        assert stock.available() == 150
        
        # Calendar is used ONLY when generating new orders
        order_date = Date(2024, 2, 7)
        receipt_date = next_receipt_date(order_date, Lane.STANDARD)
        
        # Receipt date affects WHEN stock arrives, not current stock state
        assert receipt_date > order_date
    
    def test_friday_lane_choice_affects_proposal(self):
        """Different Friday lanes generate different order proposals."""
        friday = Date(2024, 2, 9)
        daily_sales_avg = 10.0
        current_inventory_position = 50
        safety_stock = 20
        
        # Saturday lane (P=3)
        P_sat = calculate_protection_period_days(friday, Lane.SATURDAY)
        target_S_sat = (P_sat * daily_sales_avg) + safety_stock
        proposed_qty_sat = max(0, target_S_sat - current_inventory_position)
        
        # Monday lane (P=1)
        P_mon = calculate_protection_period_days(friday, Lane.MONDAY)
        target_S_mon = (P_mon * daily_sales_avg) + safety_stock
        proposed_qty_mon = max(0, target_S_mon - current_inventory_position)
        
        # Saturday lane requires more stock (if IP < target_S_sat)
        assert target_S_sat == 50.0  # 30 + 20
        assert target_S_mon == 30.0  # 10 + 20
        
        # With IP=50, Saturday lane exactly meets target, Monday has surplus
        assert proposed_qty_sat == 0  # IP exactly at target
        assert proposed_qty_mon == 0  # IP > target
        
        # If IP was lower (e.g., 40):
        current_inventory_position_low = 40
        proposed_qty_sat_low = max(0, target_S_sat - current_inventory_position_low)
        proposed_qty_mon_low = max(0, target_S_mon - current_inventory_position_low)
        
        assert proposed_qty_sat_low == 10  # Need 10 more for Saturday lane
        assert proposed_qty_mon_low == 0   # Still OK for Monday lane


class TestBackwardCompatibility:
    """Ensure calendar doesn't break existing code."""
    
    def test_transaction_creation_without_calendar(self):
        """Existing Transaction creation still works."""
        txn = Transaction(
            date=Date(2024, 2, 7),
            sku="SKU001",
            event=EventType.SNAPSHOT,
            qty=100
        )
        assert txn.date == Date(2024, 2, 7)
        assert txn.receipt_date is None  # Optional
    
    def test_sku_creation_unchanged(self):
        """SKU creation works without calendar."""
        sku = SKU(
            sku="SKU001",
            description="Test",
            lead_time_days=7  # Still valid, just optional for calendar
        )
        assert sku.lead_time_days == 7
    
    def test_order_proposal_without_calendar_fields(self):
        """OrderProposal can be created without calendar-specific fields."""
        proposal = OrderProposal(
            sku="SKU001",
            description="Test",
            current_on_hand=50,
            current_on_order=0,
            daily_sales_avg=10.0,
            proposed_qty=100
        )
        assert proposal.proposed_qty == 100
        # Calendar fields are optional (have defaults)
        assert proposal.forecast_period_days == 0  # Default


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
