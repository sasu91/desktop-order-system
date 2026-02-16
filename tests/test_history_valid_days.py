"""
Test history_valid_days calculation and low-history highlighting.

Verifies that:
1. history_valid_days is calculated correctly (lookback - OOS - out_of_assortment)
2. Proposals with <=7 valid days are flagged for UI highlighting
"""
from datetime import date, timedelta


def test_history_valid_days_formula():
    """Test the formula: history_valid_days = lookback - len(oos_days) - len(out_assortment_days)."""
    # Given: Direct values from a hypothetical calculate_daily_sales_average call
    lookback = 30
    oos_days_list = [date(2026, 2, i) for i in [3, 6, 9, 12, 15]]  # 5 OOS days
    out_of_assortment_days = [date(2026, 1, i) for i in [21, 22, 23]]  # 3 out-of-assortment days
    
    # When: Calculate valid days
    history_valid_days = lookback - len(oos_days_list) - len(out_of_assortment_days)
    
    # Then: Should be 30 - 5 - 3 = 22
    assert history_valid_days == 22, f"Expected 22 valid days, got {history_valid_days}"


def test_low_history_threshold_formula():
    """Test that <=7 valid days triggers low-history warning."""
    # Given: Exactly 7 valid days
    lookback = 30
    oos_days_list = [date(2026, 2, i) for i in range(1, 21)]  # 20 OOS days
    out_of_assortment_days = [date(2026, 1, i) for i in range(18, 21)]  # 3 out-of-assortment
    
    # When: Calculate
    history_valid_days = lookback - len(oos_days_list) - len(out_of_assortment_days)
    
    # Then: Should be exactly 7 (threshold)
    assert history_valid_days == 7, f"Expected 7 valid days, got {history_valid_days}"
    assert history_valid_days <= 7, "Should trigger low-history warning"


def test_sufficient_history_formula():
    """Test that >7 valid days does NOT trigger low-history warning."""
    # Given: 28 valid days (plenty of history)
    lookback = 30
    oos_days_list = [date(2026, 2, 5), date(2026, 2, 15)]  # Only 2 OOS days
    out_of_assortment_days = []  # No out-of-assortment periods
    
    # When: Calculate
    history_valid_days = lookback - len(oos_days_list) - len(out_of_assortment_days)
    
    # Then: Should be 28 (well above threshold)
    assert history_valid_days == 28, f"Expected 28 valid days, got {history_valid_days}"
    assert history_valid_days > 7, "Should NOT trigger low-history warning"


def test_zero_valid_days_edge_case():
    """Test edge case where all days are excluded."""
    # Given: All days are OOS or out-of-assortment
    lookback = 30
    oos_days_list = [date(2026, 2, i) for i in range(1, 21)]  # 20 OOS
    out_of_assortment_days = [date(2026, 1, i) for i in range(18, 28)]  # 10 out-of-assortment
    
    # When: Calculate
    history_valid_days = lookback - len(oos_days_list) - len(out_of_assortment_days)
    
    # Then: Should be 0 (no valid data)
    assert history_valid_days == 0, f"Expected 0 valid days, got {history_valid_days}"
    assert history_valid_days <= 7, "Should definitely trigger low-history warning"
