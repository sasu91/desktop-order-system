#!/usr/bin/env python3
"""
Example: Holiday Management via GUI (Simulation)

This script demonstrates the holiday GUI workflow programmatically.
In production, users interact with the GUI in the Settings tab.
"""
import sys
from pathlib import Path
from datetime import date

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.persistence.csv_layer import CSVLayer
from src.domain.calendar import create_calendar_with_holidays


def main():
    """Demonstrate holiday GUI workflow."""
    print("=" * 70)
    print("Holiday Management GUI - Workflow Simulation")
    print("=" * 70)
    print()
    
    # Setup
    csv_layer = CSVLayer()
    print(f"📂 Data directory: {csv_layer.data_dir}")
    print()
    
    # Step 1: Read existing holidays
    print("Step 1: Read existing holidays")
    print("-" * 70)
    holidays = csv_layer.read_holidays()
    
    if not holidays:
        print("No holidays configured (only automatic Italian holidays active)")
    else:
        print(f"Found {len(holidays)} custom holidays:")
        for i, h in enumerate(holidays):
            print(f"  [{i}] {h['name']} ({h['type']}) - Effect: {h['effect']}")
    print()
    
    # Step 2: Add new holiday (simulates GUI "Add Holiday" dialog)
    print("Step 2: Add new holiday - Summer Closure")
    print("-" * 70)
    new_holiday = {
        "name": "Ferie Estive 2026",
        "scope": "logistics",
        "effect": "both",
        "type": "range",
        "params": {
            "start": "2026-08-10",
            "end": "2026-08-25"
        }
    }
    
    # Check if already exists
    existing_names = [h.get("name") for h in holidays]
    if new_holiday["name"] in existing_names:
        print(f"⚠️  Holiday '{new_holiday['name']}' already exists. Skipping add.")
    else:
        csv_layer.add_holiday(new_holiday)
        print(f"✅ Added: {new_holiday['name']}")
        print(f"   Type: {new_holiday['type']}")
        print(f"   Dates: {new_holiday['params']['start']} → {new_holiday['params']['end']}")
        print(f"   Effect: {new_holiday['effect']}")
    print()
    
    # Step 3: Add another holiday - Patron Saint Day
    print("Step 3: Add patron saint day - Milano")
    print("-" * 70)
    patron_holiday = {
        "name": "Sant'Ambrogio (Milano)",
        "scope": "logistics",
        "effect": "both",
        "type": "single",
        "params": {
            "date": "2026-12-07"
        }
    }
    
    # Refresh holidays list
    holidays = csv_layer.read_holidays()
    existing_names = [h.get("name") for h in holidays]
    
    if patron_holiday["name"] in existing_names:
        print(f"⚠️  Holiday '{patron_holiday['name']}' already exists. Skipping add.")
    else:
        csv_layer.add_holiday(patron_holiday)
        print(f"✅ Added: {patron_holiday['name']}")
        print(f"   Type: {patron_holiday['type']}")
        print(f"   Date: {patron_holiday['params']['date']}")
        print(f"   Effect: {patron_holiday['effect']}")
    print()
    
    # Step 4: Add fixed day holiday - Monthly inventory
    print("Step 4: Add fixed-day holiday - Monthly Inventory")
    print("-" * 70)
    fixed_holiday = {
        "name": "Inventario Mensile",
        "scope": "orders",
        "effect": "no_order",
        "type": "fixed",
        "params": {
            "day": 1
        }
    }
    
    holidays = csv_layer.read_holidays()
    existing_names = [h.get("name") for h in holidays]
    
    if fixed_holiday["name"] in existing_names:
        print(f"⚠️  Holiday '{fixed_holiday['name']}' already exists. Skipping add.")
    else:
        csv_layer.add_holiday(fixed_holiday)
        print(f"✅ Added: {fixed_holiday['name']}")
        print(f"   Type: {fixed_holiday['type']}")
        print(f"   Day: {fixed_holiday['params']['day']} (first of every month)")
        print(f"   Effect: {fixed_holiday['effect']} (orders blocked, receipts OK)")
    print()
    
    # Step 5: Display all configured holidays
    print("Step 5: Current holiday configuration")
    print("-" * 70)
    holidays = csv_layer.read_holidays()
    
    if not holidays:
        print("No custom holidays configured.")
    else:
        print(f"Total custom holidays: {len(holidays)}\n")
        for i, h in enumerate(holidays):
            print(f"[{i}] {h['name']}")
            print(f"    Type: {h['type']}")
            print(f"    Scope: {h.get('scope', 'N/A')}")
            print(f"    Effect: {h.get('effect', 'N/A')}")
            
            # Format dates
            if h['type'] == 'single':
                print(f"    Date: {h['params'].get('date', 'N/A')}")
            elif h['type'] == 'range':
                print(f"    Range: {h['params'].get('start', 'N/A')} → {h['params'].get('end', 'N/A')}")
            elif h['type'] == 'fixed':
                print(f"    Day: {h['params'].get('day', 'N/A')} (every month)")
            print()
    
    # Step 6: Reload calendar with new holidays
    print("Step 6: Reload calendar with new holidays")
    print("-" * 70)
    calendar = create_calendar_with_holidays(csv_layer.data_dir)
    
    if calendar.holiday_calendar:
        print("✅ Calendar reloaded successfully")
        print(f"   Holiday calendar loaded: Yes")
        
        # Test some dates
        test_dates = [
            date(2026, 8, 15),  # Ferragosto (automatic Italian holiday)
            date(2026, 8, 12),  # Within summer closure range
            date(2026, 12, 7),  # Sant'Ambrogio
            date(2026, 9, 1),   # First of month (inventory day - no orders)
        ]
        
        print("\n   Testing specific dates:")
        for d in test_dates:
            is_hol = calendar.holiday_calendar.is_holiday(d)
            effects = calendar.holiday_calendar.effects_on(d)
            
            if is_hol:
                print(f"   • {d.strftime('%Y-%m-%d (%A)')}: HOLIDAY")
                if effects:
                    print(f"     Effects active: {', '.join(effects)}")
            else:
                print(f"   • {d.strftime('%Y-%m-%d (%A)')}: Working day")
    else:
        print("⚠️  No holiday calendar loaded (validation may have failed)")
    print()
    
    # Step 7: Example edit operation
    print("Step 7: Example - Edit holiday")
    print("-" * 70)
    holidays = csv_layer.read_holidays()
    
    if len(holidays) > 0:
        print(f"Editing first holiday: {holidays[0]['name']}")
        
        # Simulate user changing effect from "both" to "no_receipt"
        edited = holidays[0].copy()
        original_effect = edited.get("effect")
        edited["effect"] = "no_receipt"
        edited["name"] = f"{edited['name']} (modificato)"
        
        csv_layer.update_holiday(0, edited)
        print(f"✅ Updated holiday 0")
        print(f"   Name: {edited['name']}")
        print(f"   Effect changed: {original_effect} → {edited['effect']}")
    else:
        print("No holidays to edit.")
    print()
    
    # Step 8: Example delete operation  (commented out to preserve data)
    print("Step 8: Example - Delete holiday (SIMULATION ONLY)")
    print("-" * 70)
    holidays = csv_layer.read_holidays()
    
    if len(holidays) > 0:
        print(f"Would delete: {holidays[-1]['name']}")
        print("(Skipped in this demo to preserve data)")
        # csv_layer.delete_holiday(len(holidays) - 1)
        # print(f"✅ Deleted holiday {len(holidays) - 1}")
    else:
        print("No holidays to delete.")
    print()
    
    # Summary
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print("✅ Holiday GUI workflow complete!")
    print()
    print("In the actual GUI (⚙️ Impostazioni tab → 📅 Calendario e Festività):")
    print("  • View all holidays in table")
    print("  • Click ➕ to add new holiday")
    print("  • Select + ✏️ to edit")
    print("  • Select + 🗑️ to delete")
    print("  • Changes saved to data/holidays.json")
    print("  • Calendar reloads automatically")
    print()
    print(f"📂 Configuration file: {csv_layer.data_dir / 'holidays.json'}")
    print()


if __name__ == "__main__":
    main()
