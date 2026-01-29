#!/usr/bin/env python3
"""Test settings functionality."""

from src.persistence.csv_layer import CSVLayer
from src.domain.models import SKU, DemandVariability
import json

def test_settings():
    """Test settings read/write and auto-apply."""
    csv_layer = CSVLayer()
    
    # Test 1: Read settings
    print("=" * 60)
    print("TEST 1: Read settings")
    print("=" * 60)
    settings = csv_layer.read_settings()
    print(json.dumps(settings, indent=2))
    
    # Test 2: Get default SKU params
    print("\n" + "=" * 60)
    print("TEST 2: Get default SKU params")
    print("=" * 60)
    defaults = csv_layer.get_default_sku_params()
    print(f"Default params: {defaults}")
    
    # Test 3: Modify settings
    print("\n" + "=" * 60)
    print("TEST 3: Modify settings")
    print("=" * 60)
    settings["reorder_engine"]["lead_time_days"]["value"] = 14
    settings["reorder_engine"]["moq"]["value"] = 5
    csv_layer.write_settings(settings)
    print("Settings updated: lead_time=14, moq=5")
    
    # Verify changes
    updated_settings = csv_layer.read_settings()
    print(f"New lead_time: {updated_settings['reorder_engine']['lead_time_days']['value']}")
    print(f"New moq: {updated_settings['reorder_engine']['moq']['value']}")
    
    # Test 4: Auto-apply to new SKU
    print("\n" + "=" * 60)
    print("TEST 4: Auto-apply to new SKU")
    print("=" * 60)
    
    # Create SKU with default values
    test_sku = SKU(
        sku="AUTO_TEST",
        description="Test auto-apply",
        ean="9999999999999",
        moq=1,  # This should be replaced with 5
        lead_time_days=7,  # This should be replaced with 14
        max_stock=999,
        reorder_point=10,
        supplier="Test",
        demand_variability=DemandVariability.STABLE
    )
    
    print(f"Before write: moq={test_sku.moq}, lead_time={test_sku.lead_time_days}")
    
    # Write SKU (should auto-apply defaults)
    csv_layer.write_sku(test_sku)
    
    # Read back and verify
    skus = csv_layer.read_skus()
    written_sku = next((s for s in skus if s.sku == "AUTO_TEST"), None)
    
    if written_sku:
        print(f"After write: moq={written_sku.moq}, lead_time={written_sku.lead_time_days}")
        print(f"✓ Auto-apply successful!" if written_sku.moq == 5 and written_sku.lead_time_days == 14 else "✗ Auto-apply failed")
    else:
        print("✗ SKU not found after write")
    
    # Test 5: Reset to defaults
    print("\n" + "=" * 60)
    print("TEST 5: Reset to defaults")
    print("=" * 60)
    default_settings = {
        "reorder_engine": {
            "lead_time_days": {"value": 7, "auto_apply_to_new_sku": True},
            "min_stock": {"value": 10, "auto_apply_to_new_sku": True},
            "days_cover": {"value": 14, "auto_apply_to_new_sku": True},
            "moq": {"value": 1, "auto_apply_to_new_sku": True},
            "max_stock": {"value": 999, "auto_apply_to_new_sku": True},
            "reorder_point": {"value": 10, "auto_apply_to_new_sku": True},
            "demand_variability": {"value": "STABLE", "auto_apply_to_new_sku": True}
        }
    }
    csv_layer.write_settings(default_settings)
    print("Settings reset to defaults")
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    test_settings()
