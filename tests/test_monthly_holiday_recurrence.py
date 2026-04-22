"""
Test monthly holiday recurrence feature.

Verifies that FIXED_DATE holiday rules support both:
- Monthly recurrence (day only): applies to same day every month
- Annual recurrence (month + day): applies to same date every year
"""
from datetime import date
from src.domain.holidays import HolidayCalendar, HolidayRule, HolidayType, HolidayEffect


def test_monthly_recurrence():
    """Test that FIXED_DATE rule with only 'day' param applies monthly."""
    # Given: Monthly recurrence rule (1st of every month)
    rule = HolidayRule(
        name="Monthly Inventory",
        scope="orders",
        effect=HolidayEffect.NO_ORDER,
        type=HolidayType.FIXED_DATE,
        params={"day": 1}
    )
    
    # When: Check multiple first days of different months
    # Then: Should apply to all of them
    assert rule.applies_to_date(date(2026, 1, 1)) is True   # Jan 1
    assert rule.applies_to_date(date(2026, 2, 1)) is True   # Feb 1
    assert rule.applies_to_date(date(2026, 6, 1)) is True   # Jun 1
    assert rule.applies_to_date(date(2026, 12, 1)) is True  # Dec 1
    
    # And: Should NOT apply to other days
    assert rule.applies_to_date(date(2026, 1, 2)) is False  # Jan 2
    assert rule.applies_to_date(date(2026, 2, 15)) is False # Feb 15


def test_annual_recurrence():
    """Test that FIXED_DATE rule with 'month' + 'day' applies annually."""
    # Given: Annual recurrence rule (December 25 every year)
    rule = HolidayRule(
        name="Christmas",
        scope="system",
        effect=HolidayEffect.BOTH,
        type=HolidayType.FIXED_DATE,
        params={"month": 12, "day": 25}
    )
    
    # When: Check December 25 in different years
    # Then: Should apply
    assert rule.applies_to_date(date(2026, 12, 25)) is True
    assert rule.applies_to_date(date(2027, 12, 25)) is True
    
    # And: Should NOT apply to same day in other months
    assert rule.applies_to_date(date(2026, 1, 25)) is False  # Jan 25
    assert rule.applies_to_date(date(2026, 6, 25)) is False  # Jun 25
    
    # And: Should NOT apply to other days in December
    assert rule.applies_to_date(date(2026, 12, 24)) is False
    assert rule.applies_to_date(date(2026, 12, 26)) is False


def test_monthly_recurrence_day_31():
    """Test monthly recurrence with day=31 (only applies to months with 31 days)."""
    # Given: Monthly recurrence on 31st
    rule = HolidayRule(
        name="End of Month",
        scope="warehouse",
        effect=HolidayEffect.NO_RECEIPT,
        type=HolidayType.FIXED_DATE,
        params={"day": 31}
    )
    
    # When: Check 31st in months with 31 days
    # Then: Should apply
    assert rule.applies_to_date(date(2026, 1, 31)) is True   # Jan has 31 days
    assert rule.applies_to_date(date(2026, 3, 31)) is True   # Mar has 31 days
    assert rule.applies_to_date(date(2026, 5, 31)) is True   # May has 31 days
    
    # When: Check if Feb 28 matches (Feb doesn't have 31 days)
    # Then: Should NOT apply (day mismatch)
    assert rule.applies_to_date(date(2026, 2, 28)) is False
    
    # Note: Calendar validation handles month/day validity (e.g., Feb 31 is invalid)
    # but applies_to_date simply checks if the date's day matches the rule's day


def test_calendar_with_mixed_recurrence_rules():
    """Test HolidayCalendar with both monthly and annual recurrence rules."""
    # Given: Calendar with both types of recurrence
    rules = [
        HolidayRule(
            name="First Monday",
            scope="orders",
            effect=HolidayEffect.NO_ORDER,
            type=HolidayType.FIXED_DATE,
            params={"day": 1}  # Monthly: 1st of every month
        ),
        HolidayRule(
            name="New Year",
            scope="system",
            effect=HolidayEffect.BOTH,
            type=HolidayType.FIXED_DATE,
            params={"month": 1, "day": 1}  # Annual: Jan 1 only
        ),
    ]
    calendar = HolidayCalendar(rules=rules)
    
    # When: Check Jan 1 (matches BOTH rules)
    # Then: Should be a holiday
    assert calendar.is_holiday(date(2026, 1, 1)) is True
    
    # When: Check Feb 1 (matches only monthly rule)
    # Then: Should be a holiday
    assert calendar.is_holiday(date(2026, 2, 1)) is True
    
    # When: Check Jan 2 (matches no rules)
    # Then: Should NOT be a holiday
    assert calendar.is_holiday(date(2026, 1, 2)) is False


def test_monthly_recurrence_scope_filtering():
    """Test that scope filtering works with monthly recurrence."""
    # Given: Monthly recurrence rule with specific scope
    rule = HolidayRule(
        name="Warehouse Inventory",
        scope="warehouse",
        effect=HolidayEffect.NO_RECEIPT,
        type=HolidayType.FIXED_DATE,
        params={"day": 1}
    )
    calendar = HolidayCalendar(rules=[rule])
    
    # When: Check with matching scope
    # Then: Should be a holiday
    assert calendar.is_holiday(date(2026, 3, 1), scope="warehouse") is True
    
    # When: Check with non-matching scope
    # Then: Should NOT be a holiday
    assert calendar.is_holiday(date(2026, 3, 1), scope="store") is False
    
    # When: Check without scope filter
    # Then: Should be a holiday (any scope)
    assert calendar.is_holiday(date(2026, 3, 1)) is True
