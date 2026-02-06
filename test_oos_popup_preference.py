#!/usr/bin/env python3
"""
Test OOS Popup Preference Feature

Verifica:
1. Creazione SKU con preferenza popup
2. Lettura/scrittura preferenza da CSV
3. Validazione valori preferenza
"""

from src.domain.models import SKU, DemandVariability
from src.persistence.csv_layer import CSVLayer
import tempfile
import os
from pathlib import Path

def test_oos_popup_preference():
    """Test completo feature preferenza popup OOS."""
    
    # Create temporary directory for test data
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_layer = CSVLayer(Path(tmpdir))
        
        print("=== TEST: OOS POPUP PREFERENCE FEATURE ===\n")
        
        # Test 1: Create SKU with "ask" (default)
        print("1. Create SKU with default preference (ask)...")
        sku_ask = SKU(
            sku="TEST_ASK",
            description="Test SKU Ask",
            oos_popup_preference="ask"
        )
        csv_layer.write_sku(sku_ask)
        
        # Read back
        skus = csv_layer.read_skus()
        sku_ask_read = next((s for s in skus if s.sku == "TEST_ASK"), None)
        assert sku_ask_read is not None, "SKU not found"
        assert sku_ask_read.oos_popup_preference == "ask", f"Expected 'ask', got '{sku_ask_read.oos_popup_preference}'"
        print("   ✓ SKU created with preference 'ask'\n")
        
        # Test 2: Create SKU with "always_yes"
        print("2. Create SKU with preference 'always_yes'...")
        sku_yes = SKU(
            sku="TEST_YES",
            description="Test SKU Always Yes",
            oos_popup_preference="always_yes"
        )
        csv_layer.write_sku(sku_yes)
        
        skus = csv_layer.read_skus()
        sku_yes_read = next((s for s in skus if s.sku == "TEST_YES"), None)
        assert sku_yes_read.oos_popup_preference == "always_yes", f"Expected 'always_yes', got '{sku_yes_read.oos_popup_preference}'"
        print("   ✓ SKU created with preference 'always_yes'\n")
        
        # Test 3: Create SKU with "always_no"
        print("3. Create SKU with preference 'always_no'...")
        sku_no = SKU(
            sku="TEST_NO",
            description="Test SKU Always No",
            oos_popup_preference="always_no"
        )
        csv_layer.write_sku(sku_no)
        
        skus = csv_layer.read_skus()
        sku_no_read = next((s for s in skus if s.sku == "TEST_NO"), None)
        assert sku_no_read.oos_popup_preference == "always_no", f"Expected 'always_no', got '{sku_no_read.oos_popup_preference}'"
        print("   ✓ SKU created with preference 'always_no'\n")
        
        # Test 4: Update SKU preference (ask → always_yes)
        print("4. Update SKU preference (ask → always_yes)...")
        success = csv_layer.update_sku(
            old_sku_id="TEST_ASK",
            new_sku_id="TEST_ASK",
            new_description="Test SKU Ask",
            new_ean=None,
            oos_popup_preference="always_yes"
        )
        assert success, "Update failed"
        
        skus = csv_layer.read_skus()
        sku_updated = next((s for s in skus if s.sku == "TEST_ASK"), None)
        assert sku_updated.oos_popup_preference == "always_yes", f"Expected 'always_yes', got '{sku_updated.oos_popup_preference}'"
        print("   ✓ Preference updated successfully\n")
        
        # Test 5: Update SKU preference (always_yes → always_no)
        print("5. Update SKU preference (always_yes → always_no)...")
        success = csv_layer.update_sku(
            old_sku_id="TEST_YES",
            new_sku_id="TEST_YES",
            new_description="Test SKU Always Yes",
            new_ean=None,
            oos_popup_preference="always_no"
        )
        assert success, "Update failed"
        
        skus = csv_layer.read_skus()
        sku_updated2 = next((s for s in skus if s.sku == "TEST_YES"), None)
        assert sku_updated2.oos_popup_preference == "always_no", f"Expected 'always_no', got '{sku_updated2.oos_popup_preference}'"
        print("   ✓ Preference updated successfully\n")
        
        # Test 6: Reversibility (always_no → ask)
        print("6. Test reversibility (always_no → ask)...")
        success = csv_layer.update_sku(
            old_sku_id="TEST_NO",
            new_sku_id="TEST_NO",
            new_description="Test SKU Always No",
            new_ean=None,
            oos_popup_preference="ask"
        )
        assert success, "Update failed"
        
        skus = csv_layer.read_skus()
        sku_reverted = next((s for s in skus if s.sku == "TEST_NO"), None)
        assert sku_reverted.oos_popup_preference == "ask", f"Expected 'ask', got '{sku_reverted.oos_popup_preference}'"
        print("   ✓ Preference reverted to 'ask' (reversibility OK)\n")
        
        # Test 7: Invalid preference validation
        print("7. Test invalid preference validation...")
        try:
            invalid_sku = SKU(
                sku="TEST_INVALID",
                description="Test Invalid",
                oos_popup_preference="invalid_value"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "OOS popup preference" in str(e), f"Unexpected error: {e}"
            print("   ✓ Invalid preference rejected correctly\n")
        
        # Test 8: Backward compatibility (missing field defaults to "ask")
        print("8. Test backward compatibility (CSV without oos_popup_preference)...")
        # Manually write CSV without the new field
        import csv
        csv_path = os.path.join(tmpdir, "skus.csv")
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['sku', 'description', 'ean'])
            writer.writeheader()
            writer.writerow({'sku': 'OLD_SKU', 'description': 'Old Format SKU', 'ean': ''})
        
        # Read back (should default to "ask")
        skus = csv_layer.read_skus()
        old_sku = next((s for s in skus if s.sku == "OLD_SKU"), None)
        assert old_sku is not None, "Old SKU not found"
        assert old_sku.oos_popup_preference == "ask", f"Expected default 'ask', got '{old_sku.oos_popup_preference}'"
        print("   ✓ Backward compatibility OK (defaults to 'ask')\n")
        
        print("="*50)
        print("✅ ALL TESTS PASSED")
        print("\nFeature Summary:")
        print("  • Campo 'oos_popup_preference' aggiunto al modello SKU")
        print("  • Valori supportati: 'ask', 'always_yes', 'always_no'")
        print("  • Validazione attiva in __post_init__")
        print("  • Persistenza in skus.csv (lettura/scrittura)")
        print("  • Backward compatibility (default='ask')")
        print("  • Reversibilità OK (può tornare a 'ask' in qualsiasi momento)")

if __name__ == "__main__":
    test_oos_popup_preference()
