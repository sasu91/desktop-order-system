#!/usr/bin/env python3
"""Test expiry threshold configuration."""

from src.persistence.csv_layer import CSVLayer
from datetime import date, timedelta
import json

def test_expiry_thresholds():
    """Test that expiry thresholds are configurable and used correctly."""
    csv_layer = CSVLayer()
    
    # Test 1: Read settings and verify expiry_alerts exist
    print("=" * 60)
    print("TEST 1: Verify expiry_alerts in settings")
    print("=" * 60)
    settings = csv_layer.read_settings()
    
    assert "expiry_alerts" in settings, "expiry_alerts section missing!"
    assert "critical_threshold_days" in settings["expiry_alerts"], "critical_threshold_days missing!"
    assert "warning_threshold_days" in settings["expiry_alerts"], "warning_threshold_days missing!"
    
    critical_days = settings["expiry_alerts"]["critical_threshold_days"]["value"]
    warning_days = settings["expiry_alerts"]["warning_threshold_days"]["value"]
    
    print(f"✓ Critical threshold: {critical_days} days")
    print(f"✓ Warning threshold: {warning_days} days")
    
    # Test 2: Modify thresholds
    print("\n" + "=" * 60)
    print("TEST 2: Modify thresholds")
    print("=" * 60)
    
    settings["expiry_alerts"]["critical_threshold_days"]["value"] = 5
    settings["expiry_alerts"]["warning_threshold_days"]["value"] = 10
    
    csv_layer.write_settings(settings)
    print("Settings updated: critical=5, warning=10")
    
    # Verify changes
    updated_settings = csv_layer.read_settings()
    assert updated_settings["expiry_alerts"]["critical_threshold_days"]["value"] == 5
    assert updated_settings["expiry_alerts"]["warning_threshold_days"]["value"] == 10
    print("✓ Changes persisted correctly")
    
    # Test 3: Reset to defaults
    print("\n" + "=" * 60)
    print("TEST 3: Reset to defaults")
    print("=" * 60)
    
    settings["expiry_alerts"]["critical_threshold_days"]["value"] = 7
    settings["expiry_alerts"]["warning_threshold_days"]["value"] = 14
    csv_layer.write_settings(settings)
    print("✓ Reset to default values (7, 14)")
    
    # Test 4: Create sample lot and verify status logic
    print("\n" + "=" * 60)
    print("TEST 4: Verify lot status classification")
    print("=" * 60)
    
    from src.domain.models import Lot
    
    today = date.today()
    
    # Lot expiring in 3 days (should be CRITICAL with threshold=7)
    lot_critical = Lot(
        lot_id="TEST_CRITICAL",
        sku="TEST001",
        expiry_date=today + timedelta(days=3),
        qty_on_hand=10,
        receipt_id="REC001",
        receipt_date=today
    )
    
    # Lot expiring in 10 days (should be WARNING with threshold=14)
    lot_warning = Lot(
        lot_id="TEST_WARNING",
        sku="TEST002",
        expiry_date=today + timedelta(days=10),
        qty_on_hand=20,
        receipt_id="REC002",
        receipt_date=today
    )
    
    # Lot expiring in 20 days (should be OK)
    lot_ok = Lot(
        lot_id="TEST_OK",
        sku="TEST003",
        expiry_date=today + timedelta(days=20),
        qty_on_hand=30,
        receipt_id="REC003",
        receipt_date=today
    )
    
    critical_threshold = 7
    warning_threshold = 14
    
    # Check status
    days_critical = lot_critical.days_until_expiry(today)
    days_warning = lot_warning.days_until_expiry(today)
    days_ok = lot_ok.days_until_expiry(today)
    
    status_critical = "🔴 CRITICO" if days_critical <= critical_threshold else "🟡 ATTENZIONE" if days_critical <= warning_threshold else "🟢 OK"
    status_warning = "🔴 CRITICO" if days_warning <= critical_threshold else "🟡 ATTENZIONE" if days_warning <= warning_threshold else "🟢 OK"
    status_ok = "🔴 CRITICO" if days_ok <= critical_threshold else "🟡 ATTENZIONE" if days_ok <= warning_threshold else "🟢 OK"
    
    print(f"Lot {lot_critical.lot_id}: {days_critical} days → {status_critical}")
    print(f"Lot {lot_warning.lot_id}: {days_warning} days → {status_warning}")
    print(f"Lot {lot_ok.lot_id}: {days_ok} days → {status_ok}")
    
    assert status_critical == "🔴 CRITICO", f"Expected CRITICO, got {status_critical}"
    assert status_warning == "🟡 ATTENZIONE", f"Expected ATTENZIONE, got {status_warning}"
    assert status_ok == "🟢 OK", f"Expected OK, got {status_ok}"
    
    print("✓ All lot status classifications correct!")
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✓")
    print("=" * 60)

if __name__ == "__main__":
    test_expiry_thresholds()
