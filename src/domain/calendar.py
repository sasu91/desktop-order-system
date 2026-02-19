"""
Logistics Calendar Module for Order and Delivery Planning.

This module handles:
- Valid order days (Monday-Friday)
- Valid delivery days (Monday-Saturday)
- Special Friday "lanes" (Saturday delivery vs Monday delivery)
- Protection period calculation (P) between consecutive deliveries

Usage Examples:
    from datetime import date
    from src.domain.calendar import next_receipt_date, protection_window, Lane
    
    # Standard lane (Monday-Thursday orders)
    order = date(2026, 2, 2)  # Monday
    r1 = next_receipt_date(order, Lane.STANDARD)  # Next delivery date
    r1, r2, P = protection_window(order, Lane.STANDARD)  # Protection period
    
    # Friday lanes
    friday = date(2026, 2, 6)
    r1_sat = next_receipt_date(friday, Lane.SATURDAY)  # Saturday delivery
    r1_mon = next_receipt_date(friday, Lane.MONDAY)    # Monday delivery
    
    # Protection period for Friday Saturday lane
    r1, r2, P_sat = protection_window(friday, Lane.SATURDAY)
    # Protection period for Friday Monday lane
    r1, r2, P_mon = protection_window(friday, Lane.MONDAY)
    # P_sat < P_mon (Saturday delivery has shorter protection)
"""
from datetime import date as Date, timedelta
import json
from enum import Enum
from typing import Tuple, Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from src.domain.holidays import HolidayCalendar


class Lane(Enum):
    """Order lane types."""
    STANDARD = "STANDARD"      # Monday-Thursday orders, standard delivery
    SATURDAY = "SATURDAY"      # Friday orders with Saturday delivery
    MONDAY = "MONDAY"          # Friday orders with Monday delivery


@dataclass(frozen=True)
class CalendarConfig:
    """
    Logistics calendar configuration.
    
    Attributes:
        order_days: Set of weekdays when orders can be placed (0=Monday, 6=Sunday)
        delivery_days: Set of weekdays when deliveries can be received
        lead_time_days: Standard lead time in days
        saturday_lane_lead_time: Lead time for Friday->Saturday lane
        holidays: Set of dates (DEPRECATED: use holiday_calendar instead)
        holiday_calendar: HolidayCalendar instance for effect-aware holiday management
    """
    order_days: set = None          # Default: {0,1,2,3,4} = Mon-Fri
    delivery_days: set = None       # Default: {0,1,2,3,4,5} = Mon-Sat
    lead_time_days: int = 1         # Default: next day delivery
    saturday_lane_lead_time: int = 1  # Friday->Saturday lead time
    holidays: set = None            # DEPRECATED: use holiday_calendar
    holiday_calendar: Optional['HolidayCalendar'] = None  # Effect-aware holiday management
    
    def __post_init__(self):
        # Set defaults using object.__setattr__ (frozen dataclass)
        if self.order_days is None:
            object.__setattr__(self, 'order_days', {0, 1, 2, 3, 4})  # Mon-Fri
        if self.delivery_days is None:
            object.__setattr__(self, 'delivery_days', {0, 1, 2, 3, 4, 5})  # Mon-Sat
        if self.holidays is None:
            object.__setattr__(self, 'holidays', set())


# Global default configuration (can be overridden)
DEFAULT_CONFIG = CalendarConfig()


def is_order_day(date: Date, config: CalendarConfig = DEFAULT_CONFIG) -> bool:
    """
    Check if a date is a valid order day.
    
    Checks:
    1. Holiday calendar for no_order effect (if configured)
    2. Deprecated holidays set (backward compatibility)
    3. Weekday restriction
    
    Args:
        date: Date to check
        config: Calendar configuration
        
    Returns:
        True if date is a valid order day
    """
    # Check holiday calendar for no_order effect
    if config.holiday_calendar is not None:
        effects = config.holiday_calendar.effects_on(date)
        if "no_order" in effects:
            return False
    
    # Backward compatibility: deprecated holidays set
    if date in config.holidays:
        return False
    
    return date.weekday() in config.order_days


def is_delivery_day(date: Date, config: CalendarConfig = DEFAULT_CONFIG) -> bool:
    """
    Check if a date is a valid delivery day.
    
    Checks:
    1. Holiday calendar for no_receipt effect (if configured)
    2. Deprecated holidays set (backward compatibility)
    3. Weekday restriction
    
    Args:
        date: Date to check
        config: Calendar configuration
        
    Returns:
        True if date is a valid delivery day
    """
    # Check holiday calendar for no_receipt effect
    if config.holiday_calendar is not None:
        effects = config.holiday_calendar.effects_on(date)
        if "no_receipt" in effects:
            return False
    
    # Backward compatibility: deprecated holidays set
    if date in config.holidays:
        return False
    
    return date.weekday() in config.delivery_days


def next_delivery_day(start_date: Date, config: CalendarConfig = DEFAULT_CONFIG) -> Date:
    """
    Find the next valid delivery day from a given date (inclusive).
    
    Skips Sundays and holidays.
    
    Args:
        start_date: Starting date (inclusive)
        config: Calendar configuration
        
    Returns:
        Next valid delivery date
    """
    current = start_date
    max_iterations = 14  # Safety limit: 2 weeks
    
    for _ in range(max_iterations):
        if is_delivery_day(current, config):
            return current
        current += timedelta(days=1)
    
    raise ValueError(f"Could not find delivery day within 2 weeks from {start_date}")


def next_receipt_date(
    order_date: Date,
    lane: Lane = Lane.STANDARD,
    config: CalendarConfig = DEFAULT_CONFIG
) -> Date:
    """
    Calculate the next receipt (delivery) date for an order.
    
    Rules:
    - STANDARD lane: order_date + lead_time, skip to next delivery day
    - SATURDAY lane: order_date + saturday_lane_lead_time (Friday->Saturday)
    - MONDAY lane: order_date + lead_time, skip Sunday to Monday
    
    Args:
        order_date: Date when order is placed
        lane: Order lane type
        config: Calendar configuration
        
    Returns:
        Expected receipt date
        
    Raises:
        ValueError: If order_date is not a valid order day
    """
    if not is_order_day(order_date, config):
        raise ValueError(f"{order_date} is not a valid order day (weekday={order_date.weekday()})")
    
    if lane == Lane.SATURDAY:
        # Friday -> Saturday delivery
        if order_date.weekday() != 4:  # 4 = Friday
            raise ValueError(f"SATURDAY lane only valid for Friday orders, got {order_date.strftime('%A')}")
        tentative = order_date + timedelta(days=config.saturday_lane_lead_time)
        return next_delivery_day(tentative, config)
    
    elif lane == Lane.MONDAY:
        # Friday -> Monday delivery (explicitly skip weekend)
        if order_date.weekday() != 4:  # 4 = Friday
            raise ValueError(f"MONDAY lane only valid for Friday orders, got {order_date.strftime('%A')}")
        # Friday + lead_time (1) = Saturday, skip to Monday
        tentative = order_date + timedelta(days=config.lead_time_days)
        # Force skip to Monday (weekday=0)
        while tentative.weekday() != 0:
            tentative += timedelta(days=1)
        return next_delivery_day(tentative, config)
    
    else:  # Lane.STANDARD
        # Standard: order_date + lead_time, skip to next delivery day
        tentative = order_date + timedelta(days=config.lead_time_days)
        return next_delivery_day(tentative, config)


def next_order_opportunity(
    after_date: Date,
    config: CalendarConfig = DEFAULT_CONFIG
) -> Date:
    """
    Find the next valid order date after a given date.
    
    Args:
        after_date: Date after which to find next order day (exclusive)
        config: Calendar configuration
        
    Returns:
        Next valid order date
    """
    current = after_date + timedelta(days=1)
    max_iterations = 14  # Safety limit
    
    for _ in range(max_iterations):
        if is_order_day(current, config):
            return current
        current += timedelta(days=1)
    
    raise ValueError(f"Could not find order day within 2 weeks from {after_date}")


def protection_window(
    order_date: Date,
    lane: Lane = Lane.STANDARD,
    config: CalendarConfig = DEFAULT_CONFIG
) -> Tuple[Date, Date, int]:
    """
    Calculate the protection period for an order.
    
    The protection period P is the time between:
    - r1: First possible receipt date (from this order)
    - r2: Next possible receipt date (from next order opportunity)
    
    This defines the "coverage period" that the order must protect.
    
    Args:
        order_date: Date when order is placed
        lane: Order lane type
        config: Calendar configuration
        
    Returns:
        Tuple (r1, r2, P_days) where:
            - r1: First receipt date (from this order)
            - r2: Next receipt date (from next order)
            - P_days: Protection period in days (r2 - r1)
            
    Examples:
        # Wednesday order (standard lane)
        wed = date(2026, 2, 4)
        r1, r2, P = protection_window(wed, Lane.STANDARD)
        # r1 = Thursday (wed + 1 day)
        # Next order = Thursday, r2 = Friday (thu + 1 day)
        # P = 1 day
        
        # Friday SATURDAY lane
        fri = date(2026, 2, 6)
        r1, r2, P = protection_window(fri, Lane.SATURDAY)
        # r1 = Saturday
        # Next order = Monday, r2 = Tuesday
        # P = 3 days (Sat -> Tue)
        
        # Friday MONDAY lane
        r1, r2, P = protection_window(fri, Lane.MONDAY)
        # r1 = Monday
        # Next order = Monday, r2 = Tuesday
        # P = 1 day (Mon -> Tue)
    """
    # Calculate first receipt date from this order
    r1 = next_receipt_date(order_date, lane, config)
    
    # Find next order opportunity after this order
    next_order = next_order_opportunity(order_date, config)
    
    # Calculate receipt date from next order (always STANDARD lane for next order)
    # (Next order could be same day if it's Mon-Thu, or Monday if after Friday)
    r2 = next_receipt_date(next_order, Lane.STANDARD, config)
    
    # Protection period in days
    P_days = (r2 - r1).days
    
    return r1, r2, P_days


def get_friday_lanes(
    friday: Date,
    config: CalendarConfig = DEFAULT_CONFIG
) -> Tuple[Tuple[Date, Date, int], Tuple[Date, Date, int]]:
    """
    Get protection windows for both Friday lanes.
    
    Convenience function to calculate both Saturday and Monday lane
    protection windows for a Friday order.
    
    Args:
        friday: Friday date
        config: Calendar configuration
        
    Returns:
        Tuple of ((r1_sat, r2_sat, P_sat), (r1_mon, r2_mon, P_mon))
        
    Raises:
        ValueError: If friday is not a Friday
    """
    if friday.weekday() != 4:
        raise ValueError(f"Expected Friday, got {friday.strftime('%A')}")
    
    saturday_window = protection_window(friday, Lane.SATURDAY, config)
    monday_window = protection_window(friday, Lane.MONDAY, config)
    
    return saturday_window, monday_window

# ============ Holiday Calendar Initialization ============

def load_holiday_calendar(data_dir) -> 'HolidayCalendar':
    """
    Load HolidayCalendar from holidays.json in data directory.
    
    Fallback to Italian public holidays only if file missing/invalid.
    
    Args:
        data_dir: Path to data directory containing holidays.json
        
    Returns:
        HolidayCalendar instance
    """
    from pathlib import Path
    from src.domain.holidays import HolidayCalendar
    
    config_path = Path(data_dir) / "holidays.json"
    return HolidayCalendar.from_config(config_path)


def create_calendar_with_holidays(data_dir) -> CalendarConfig:
    """
    Create CalendarConfig with HolidayCalendar loaded from data directory.
    
    This is the recommended way to initialize the calendar for production use.
    
    Args:
        data_dir: Path to data directory containing holidays.json
        
    Returns:
        CalendarConfig with holiday_calendar initialized
        
    Example:
        >>> from pathlib import Path
        >>> config = create_calendar_with_holidays(Path("data"))
        >>> # Now is_order_day() and is_delivery_day() respect holiday effects
    """
    holiday_cal = load_holiday_calendar(data_dir)

    order_days = {0, 1, 2, 3, 4}
    try:
        from pathlib import Path
        settings_path = Path(data_dir) / "settings.json"
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as settings_file:
                settings = json.load(settings_file)
            configured_days = settings.get("calendar", {}).get("order_days", {}).get("value", None)
            if isinstance(configured_days, list):
                valid_days = {int(day) for day in configured_days if isinstance(day, int) or (isinstance(day, str) and day.isdigit())}
                valid_days = {day for day in valid_days if 0 <= day <= 6}
                if valid_days:
                    order_days = valid_days
    except Exception:
        # Fallback to default Mon-Fri if settings are unavailable/invalid
        pass
    
    return CalendarConfig(
        order_days=order_days,
        delivery_days={0, 1, 2, 3, 4, 5},  # Mon-Sat
        lead_time_days=1,
        saturday_lane_lead_time=1,
        holidays=set(),  # Deprecated, kept for backward compat
        holiday_calendar=holiday_cal
    )

# Utility function for order proposal integration
def calculate_protection_period_days(
    order_date: Date,
    lane: Lane = Lane.STANDARD,
    config: CalendarConfig = DEFAULT_CONFIG
) -> int:
    """
    Calculate protection period P (in days) for an order.
    
    Shorthand for protection_window(...)[2]
    
    Args:
        order_date: Date when order is placed
        lane: Order lane type
        config: Calendar configuration
        
    Returns:
        Protection period in days
    """
    _, _, P_days = protection_window(order_date, lane, config)
    return P_days


def resolve_receipt_and_protection(
    order_date: Date,
    lane: Lane,
    config: CalendarConfig,
    receipt_override: Optional[Date] = None,
) -> Tuple[Date, int]:
    """
    Resolve receipt date (r1) and protection period P for an order.

    This is the single authoritative entry point for computing (r1, P):

    - Without override: delegates to protection_window(order_date, lane, config).
      r1 is the calendar-derived first delivery; P = r2 - r1 where r2 is the
      first delivery of the immediately following order opportunity.

    - With override: r1 = receipt_override (user-specified delivery date).
      r2 is derived by finding the first order opportunity after r1 (exclusive)
      and computing its STANDARD-lane delivery date.  If r2 <= r1 (e.g., holiday
      chain), the loop advances until r2 > r1, guaranteeing P >= 1.

    Formula:  P = r2 - r1

    Note: P is the "protection window" — the number of days the ordered stock
    must cover before the next replenishment arrives.  It is always >= 1.

    Args:
        order_date: Date the order is placed (YYYY-MM-DD); used only when
                    receipt_override is None.
        lane:       Lane.STANDARD / Lane.SATURDAY / Lane.MONDAY.
        config:     CalendarConfig with holidays and delivery rules.
        receipt_override: Explicit delivery date provided by the user.  When
                    given, bypasses calendar r1 derivation; r2 (and thus P) is
                    still computed via the standard rhythm.

    Returns:
        (r1, P_days) — first receipt date and protection period in days.

    Raises:
        ValueError: If order_date is not a valid order day (override=None) or
                    if calendar loop cannot converge (should never happen with
                    valid config).
    """
    if receipt_override is None:
        # Standard path: full calendar + lane logic
        r1, _r2, P = protection_window(order_date, lane, config)
        return r1, P

    # Override path: r1 is fixed by the caller
    r1 = receipt_override

    # Derive r2 via STANDARD rhythm: first order opportunity strictly after r1,
    # then its expected delivery.
    next_ord = next_order_opportunity(r1, config)
    r2 = next_receipt_date(next_ord, Lane.STANDARD, config)

    # Safety loop: guarantee r2 > r1 even in degenerate holiday chains
    max_iter = 30
    for _ in range(max_iter):
        if r2 > r1:
            break
        next_ord = next_order_opportunity(r2, config)
        r2 = next_receipt_date(next_ord, Lane.STANDARD, config)
    else:
        raise ValueError(
            f"resolve_receipt_and_protection: could not find r2 > r1={r1} "
            f"within {max_iter} iterations (check holiday config)"
        )

    P = (r2 - r1).days
    return r1, P
