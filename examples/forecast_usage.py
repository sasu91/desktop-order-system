"""
Example: Demand Forecasting Usage

Demonstrates:
1. Basic forecasting workflow
2. Working with short history
3. DOW pattern analysis
4. Integration with existing order system
"""

from datetime import date, timedelta
from src.forecast import (
    fit_forecast_model,
    predict,
    predict_single_day,
    get_forecast_stats,
    quick_forecast,
)


def basic_forecast_example():
    """Simple forecasting example with synthetic data."""
    print("=" * 60)
    print("BASIC FORECAST EXAMPLE")
    print("=" * 60)
    
    # Generate 3 weeks of sales history
    history = []
    start_date = date(2024, 1, 1)  # Monday
    
    for i in range(21):
        d = start_date + timedelta(days=i)
        dow = d.weekday()
        
        # Pattern: Mon=15, Tue-Fri=10, Weekend=5
        if dow == 0:
            qty = 15.0
        elif dow in [1, 2, 3, 4]:
            qty = 10.0
        else:
            qty = 5.0
        
        history.append({"date": d, "qty_sold": qty})
    
    print(f"\nTraining data: {len(history)} days")
    print(f"Date range: {history[0]['date']} to {history[-1]['date']}")
    
    # Fit model
    model = fit_forecast_model(history)
    
    print(f"\nModel fitted:")
    print(f"  Method: {model['method']}")
    print(f"  Level: {model['level']:.2f}")
    print(f"  DOW factors: {[f'{f:.2f}' for f in model['dow_factors']]}")
    
    # Generate 7-day forecast
    forecast = predict(model, horizon=7)
    
    print(f"\n7-Day Forecast:")
    forecast_start = model['last_date'] + timedelta(days=1)
    for i, value in enumerate(forecast):
        forecast_date = forecast_start + timedelta(days=i)
        dow_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][forecast_date.weekday()]
        print(f"  {forecast_date} ({dow_name}): {value:.1f} units")
    
    # Get statistics
    stats = get_forecast_stats(model)
    print(f"\nForecast Statistics:")
    print(f"  Min daily: {stats['min_daily_forecast']:.1f}")
    print(f"  Max daily: {stats['max_daily_forecast']:.1f}")
    print(f"  Mean daily: {stats['mean_daily_forecast']:.1f}")


def short_history_example():
    """Forecasting with limited data."""
    print("\n\n" + "=" * 60)
    print("SHORT HISTORY EXAMPLE")
    print("=" * 60)
    
    # Only 5 days of data
    history = [
        {"date": date(2024, 1, 1), "qty_sold": 8.0},
        {"date": date(2024, 1, 2), "qty_sold": 10.0},
        {"date": date(2024, 1, 3), "qty_sold": 12.0},
        {"date": date(2024, 1, 4), "qty_sold": 9.0},
        {"date": date(2024, 1, 5), "qty_sold": 11.0},
    ]
    
    print(f"\nTraining data: {len(history)} days (SHORT)")
    
    # Fit model with fallback
    model = fit_forecast_model(history)
    
    print(f"\nModel fitted:")
    print(f"  Method: {model['method']} (fallback mode)")
    print(f"  Level: {model['level']:.2f}")
    
    # Still produces reasonable forecast
    forecast = predict(model, horizon=7)
    
    print(f"\n7-Day Forecast (uniform, no DOW pattern):")
    for i, value in enumerate(forecast):
        print(f"  Day {i+1}: {value:.1f} units")
    
    print(f"\nNote: With more data (14+ days), DOW patterns will be detected")


def dow_pattern_analysis():
    """Analyze detected DOW patterns."""
    print("\n\n" + "=" * 60)
    print("DOW PATTERN ANALYSIS")
    print("=" * 60)
    
    # Retail pattern: busy weekends, slower weekdays
    history = []
    start_date = date(2024, 1, 1)
    
    for week in range(4):  # 4 weeks
        for day in range(7):
            d = start_date + timedelta(weeks=week, days=day)
            dow = d.weekday()
            
            # Pattern: Sat/Sun busy, Mon/Tue slow, Wed-Fri medium
            if dow in [5, 6]:  # Weekend
                qty = 25.0
            elif dow in [0, 1]:  # Mon/Tue
                qty = 8.0
            else:  # Wed-Fri
                qty = 15.0
            
            history.append({"date": d, "qty_sold": qty})
    
    print(f"\nSimulated retail pattern (4 weeks)")
    
    # Fit model
    model = fit_forecast_model(history)
    
    print(f"\nDetected DOW Factors:")
    dow_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for dow, (name, factor) in enumerate(zip(dow_names, model['dow_factors'])):
        expected_qty = model['level'] * factor
        print(f"  {name:9s}: {factor:.2f}x (≈ {expected_qty:.1f} units)")
    
    # Show forecast
    forecast = predict(model, horizon=7)
    print(f"\nNext week forecast:")
    for i, value in enumerate(forecast):
        print(f"  {dow_names[i]:9s}: {value:.1f} units")


def integration_with_order_system():
    """
    Integration example: Use forecast for reorder calculations.
    
    Simulates using forecast with existing order workflow.
    """
    print("\n\n" + "=" * 60)
    print("INTEGRATION WITH ORDER SYSTEM")
    print("=" * 60)
    
    # Historical sales
    history = [
        {"date": date(2024, 1, i), "qty_sold": 10.0 + (i % 7)}
        for i in range(1, 29)  # 4 weeks
    ]
    
    # Quick forecast
    result = quick_forecast(history, horizon=7)
    
    forecast = result["forecast"]
    stats = result["stats"]
    
    print(f"\nForecast Summary:")
    print(f"  Method: {stats['method']}")
    print(f"  Training samples: {stats['n_samples']}")
    print(f"  Expected daily demand: {stats['mean_daily_forecast']:.1f} ± {(stats['max_daily_forecast'] - stats['min_daily_forecast'])/2:.1f}")
    
    # Calculate reorder quantity
    # Use forecast for protection period P (e.g., 3 days)
    protection_period = 3
    forecast_demand = sum(forecast[:protection_period])
    
    safety_stock = 20  # Units
    current_on_hand = 50
    current_on_order = 30
    
    inventory_position = current_on_hand + current_on_order
    target_stock = forecast_demand + safety_stock
    reorder_qty = max(0, target_stock - inventory_position)
    
    print(f"\nReorder Calculation:")
    print(f"  Protection period: {protection_period} days")
    print(f"  Forecast demand ({protection_period} days): {forecast_demand:.1f}")
    print(f"  Safety stock: {safety_stock}")
    print(f"  Target stock (S): {target_stock:.1f}")
    print(f"  Current IP: {inventory_position}")
    print(f"  Reorder quantity: {reorder_qty:.0f} units")
    
    if reorder_qty > 0:
        print(f"\n✅ ORDER RECOMMENDED: {reorder_qty:.0f} units")
    else:
        print(f"\n✗ No order needed (sufficient stock)")


def forecast_specific_future_date():
    """Forecast for specific dates (e.g., next Monday)."""
    print("\n\n" + "=" * 60)
    print("FORECAST SPECIFIC DATES")
    print("=" * 60)
    
    # Build model
    history = [
        {"date": date(2024, 1, i), "qty_sold": 10.0 + (i % 3)}
        for i in range(1, 22)
    ]
    
    model = fit_forecast_model(history)
    
    # Forecast specific dates
    target_dates = [
        date(2024, 1, 29),  # Monday
        date(2024, 2, 3),   # Saturday
        date(2024, 2, 4),   # Sunday
    ]
    
    print(f"\nForecasts for specific dates:")
    for target_date in target_dates:
        forecast_value = predict_single_day(model, target_date)
        dow_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][target_date.weekday()]
        print(f"  {target_date} ({dow_name}): {forecast_value:.1f} units")


if __name__ == "__main__":
    basic_forecast_example()
    short_history_example()
    dow_pattern_analysis()
    integration_with_order_system()
    forecast_specific_future_date()
    
    print("\n\n" + "=" * 60)
    print("END OF EXAMPLES")
    print("=" * 60)
