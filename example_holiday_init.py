#!/usr/bin/env python3
"""
Example: Loading holiday calendar at application startup.

This shows how to integrate the holiday system into your application.
You can add this logic to main.py or src/gui/app.py startup.
"""
from pathlib import Path
from datetime import date
from src.domain.calendar import create_calendar_with_holidays, is_order_day, is_delivery_day
from src.domain import calendar


def initialize_holiday_calendar(data_dir: Path):
    """
    Initialize the global calendar configuration with holidays.
    
    This should be called once at application startup, before any
    calendar operations.
    
    Args:
        data_dir: Path to data directory containing holidays.json
    """
    print(f"Loading holiday calendar from {data_dir}...")
    
    # Load calendar with holidays
    config = create_calendar_with_holidays(data_dir)
    
    # Set as global default
    calendar.DEFAULT_CONFIG = config
    
    # Verify it loaded correctly
    if config.holiday_calendar is not None:
        # List holidays for current year
        current_year = date.today().year
        holidays = config.holiday_calendar.list_holidays(current_year)
        print(f"✓ Holiday calendar loaded: {len(holidays)} holidays for {current_year}")
        
        # Show first few holidays
        for holiday_date in holidays[:5]:
            effects = config.holiday_calendar.effects_on(holiday_date)
            print(f"  - {holiday_date}: {', '.join(effects)}")
        
        if len(holidays) > 5:
            print(f"  ... and {len(holidays) - 5} more")
    else:
        print("⚠ No holiday calendar loaded (using default)")
    
    return config


def example_usage():
    """Example: Using holiday calendar in your workflow."""
    
    # Initialize (do this once at startup)
    data_dir = Path(__file__).parent / "data"
    config = initialize_holiday_calendar(data_dir)
    
    # Now calendar functions respect holidays automatically
    print("\n--- Example: Checking dates ---")
    
    # Check if today is a valid order/receipt day
    today = date.today()
    print(f"\nToday ({today}):")
    print(f"  Can place orders: {is_order_day(today, config)}")
    print(f"  Can receive: {is_delivery_day(today, config)}")
    
    # Check Christmas 2026
    christmas = date(2026, 12, 25)
    print(f"\nChristmas ({christmas}):")
    print(f"  Can place orders: {is_order_day(christmas, config)}")
    print(f"  Can receive: {is_delivery_day(christmas, config)}")
    
    # Check effects on a date
    if config.holiday_calendar:
        effects = config.holiday_calendar.effects_on(christmas)
        print(f"  Effects: {effects}")
    
    # Check Easter 2026
    from src.domain.holidays import easter_sunday
    easter = easter_sunday(2026)
    print(f"\nEaster 2026 ({easter}):")
    print(f"  Can place orders: {is_order_day(easter, config)}")
    print(f"  Can receive: {is_delivery_day(easter, config)}")


if __name__ == "__main__":
    example_usage()
