"""
Example usage of the logistics calendar module in order workflows.

This demonstrates how to integrate calendar logic into the order proposal
and reorder point calculation workflows.
"""
from datetime import date as Date
from src.domain.calendar import (
    Lane,
    next_receipt_date,
    protection_window,
    get_friday_lanes,
    calculate_protection_period_days,
)


def example_basic_usage():
    """Basic calendar usage examples."""
    print("=" * 70)
    print("BASIC CALENDAR USAGE")
    print("=" * 70)
    
    # Monday order
    monday = Date(2026, 2, 2)
    r1 = next_receipt_date(monday, Lane.STANDARD)
    print(f"Monday order ({monday}) -> Receipt: {r1} ({r1.strftime('%A')})")
    
    # Wednesday order
    wednesday = Date(2026, 2, 4)
    r1 = next_receipt_date(wednesday, Lane.STANDARD)
    print(f"Wednesday order ({wednesday}) -> Receipt: {r1} ({r1.strftime('%A')})")
    
    # Friday orders (dual lanes)
    friday = Date(2026, 2, 6)
    r1_sat = next_receipt_date(friday, Lane.SATURDAY)
    r1_mon = next_receipt_date(friday, Lane.MONDAY)
    print(f"\nFriday order ({friday}):")
    print(f"  - SATURDAY lane -> {r1_sat} ({r1_sat.strftime('%A')})")
    print(f"  - MONDAY lane   -> {r1_mon} ({r1_mon.strftime('%A')})")


def example_protection_periods():
    """Protection period calculation examples."""
    print("\n" + "=" * 70)
    print("PROTECTION PERIOD CALCULATION")
    print("=" * 70)
    
    # Wednesday
    wednesday = Date(2026, 2, 4)
    r1, r2, P = protection_window(wednesday, Lane.STANDARD)
    print(f"\nWednesday order ({wednesday}):")
    print(f"  - First delivery (r1): {r1} ({r1.strftime('%A')})")
    print(f"  - Next delivery (r2):  {r2} ({r2.strftime('%A')})")
    print(f"  - Protection period:   {P} days")
    
    # Friday Saturday lane
    friday = Date(2026, 2, 6)
    r1, r2, P = protection_window(friday, Lane.SATURDAY)
    print(f"\nFriday SATURDAY lane ({friday}):")
    print(f"  - First delivery (r1): {r1} ({r1.strftime('%A')})")
    print(f"  - Next delivery (r2):  {r2} ({r2.strftime('%A')})")
    print(f"  - Protection period:   {P} days (covers weekend!)")
    
    # Friday Monday lane
    r1, r2, P = protection_window(friday, Lane.MONDAY)
    print(f"\nFriday MONDAY lane ({friday}):")
    print(f"  - First delivery (r1): {r1} ({r1.strftime('%A')})")
    print(f"  - Next delivery (r2):  {r2} ({r2.strftime('%A')})")
    print(f"  - Protection period:   {P} days")


def example_friday_comparison():
    """Compare Friday lanes side-by-side."""
    print("\n" + "=" * 70)
    print("FRIDAY DUAL-LANE COMPARISON")
    print("=" * 70)
    
    friday = Date(2026, 2, 6)
    (r1_sat, r2_sat, P_sat), (r1_mon, r2_mon, P_mon) = get_friday_lanes(friday)
    
    print(f"\nFriday order date: {friday}")
    print("\nSATURDAY Lane:")
    print(f"  Receipt: {r1_sat} ({r1_sat.strftime('%A')})")
    print(f"  Next:    {r2_sat} ({r2_sat.strftime('%A')})")
    print(f"  P = {P_sat} days")
    
    print("\nMONDAY Lane:")
    print(f"  Receipt: {r1_mon} ({r1_mon.strftime('%A')})")
    print(f"  Next:    {r2_mon} ({r2_mon.strftime('%A')})")
    print(f"  P = {P_mon} days")
    
    print(f"\nConclusion: Saturday lane has {P_sat - P_mon} more days of protection")
    print("(covers Saturday + Sunday before next order cycle)")


def example_integration_with_order_workflow():
    """
    Example: How to use calendar in order proposal generation.
    
    This shows the pattern for integrating calendar logic into
    the existing OrderWorkflow class.
    """
    print("\n" + "=" * 70)
    print("INTEGRATION WITH ORDER WORKFLOW")
    print("=" * 70)
    
    # Scenario: Generate order proposal for a SKU
    order_date = Date(2026, 2, 6)  # Friday
    daily_sales_avg = 10.0
    current_on_hand = 50
    current_on_order = 0
    safety_stock = 20
    
    print(f"\nScenario: Order on {order_date.strftime('%A, %Y-%m-%d')}")
    print(f"Current stock: on_hand={current_on_hand}, on_order={current_on_order}")
    print(f"Daily sales average: {daily_sales_avg} units/day")
    print(f"Safety stock: {safety_stock} units")
    
    # Compare Saturday vs Monday lanes
    print("\n--- SATURDAY Lane Analysis ---")
    r1_sat, r2_sat, P_sat = protection_window(order_date, Lane.SATURDAY)
    forecast_sat = daily_sales_avg * P_sat
    target_S_sat = forecast_sat + safety_stock
    inventory_position_sat = current_on_hand + current_on_order
    proposed_qty_sat = max(0, target_S_sat - inventory_position_sat)
    
    print(f"Receipt date: {r1_sat}")
    print(f"Protection period: {P_sat} days")
    print(f"Forecast demand (P × daily_avg): {forecast_sat} units")
    print(f"Target S (forecast + safety): {target_S_sat} units")
    print(f"Inventory position: {inventory_position_sat} units")
    print(f"Proposed order qty: {proposed_qty_sat} units")
    
    print("\n--- MONDAY Lane Analysis ---")
    r1_mon, r2_mon, P_mon = protection_window(order_date, Lane.MONDAY)
    forecast_mon = daily_sales_avg * P_mon
    target_S_mon = forecast_mon + safety_stock
    inventory_position_mon = current_on_hand + current_on_order
    proposed_qty_mon = max(0, target_S_mon - inventory_position_mon)
    
    print(f"Receipt date: {r1_mon}")
    print(f"Protection period: {P_mon} days")
    print(f"Forecast demand (P × daily_avg): {forecast_mon} units")
    print(f"Target S (forecast + safety): {target_S_mon} units")
    print(f"Inventory position: {inventory_position_mon} units")
    print(f"Proposed order qty: {proposed_qty_mon} units")
    
    print("\n--- Recommendation ---")
    if proposed_qty_sat > proposed_qty_mon:
        print(f"SATURDAY lane requires {proposed_qty_sat - proposed_qty_mon} more units")
        print("due to longer protection period (covers weekend)")
    else:
        print("MONDAY lane and SATURDAY lane have same/similar requirements")


def example_calendar_aware_reorder_point():
    """
    Example: Calendar-aware reorder point calculation.
    
    Traditional reorder point: ROP = lead_time_demand + safety_stock
    Calendar-aware: ROP = (P × daily_avg) + safety_stock
    
    This ensures stock covers until next order cycle, not just lead time.
    """
    print("\n" + "=" * 70)
    print("CALENDAR-AWARE REORDER POINT")
    print("=" * 70)
    
    daily_sales_avg = 10.0
    safety_stock = 20
    
    # Wednesday order
    wednesday = Date(2026, 2, 4)
    P_wed = calculate_protection_period_days(wednesday, Lane.STANDARD)
    ROP_wed = (P_wed * daily_sales_avg) + safety_stock
    
    print(f"\nWednesday order:")
    print(f"  Protection period: {P_wed} days")
    print(f"  Reorder point (ROP): {ROP_wed} units")
    
    # Friday Saturday lane
    friday = Date(2026, 2, 6)
    P_sat = calculate_protection_period_days(friday, Lane.SATURDAY)
    ROP_sat = (P_sat * daily_sales_avg) + safety_stock
    
    print(f"\nFriday SATURDAY lane:")
    print(f"  Protection period: {P_sat} days")
    print(f"  Reorder point (ROP): {ROP_sat} units")
    
    # Friday Monday lane
    P_mon = calculate_protection_period_days(friday, Lane.MONDAY)
    ROP_mon = (P_mon * daily_sales_avg) + safety_stock
    
    print(f"\nFriday MONDAY lane:")
    print(f"  Protection period: {P_mon} days")
    print(f"  Reorder point (ROP): {ROP_mon} units")
    
    print("\nNote: Different lanes on same day require different ROPs!")


if __name__ == "__main__":
    example_basic_usage()
    example_protection_periods()
    example_friday_comparison()
    example_integration_with_order_workflow()
    example_calendar_aware_reorder_point()
    
    print("\n" + "=" * 70)
    print("For more details, see:")
    print("  - src/domain/calendar.py (module implementation)")
    print("  - tests/test_calendar.py (comprehensive test suite)")
    print("=" * 70)
