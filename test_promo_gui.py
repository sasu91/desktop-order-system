"""
Manual GUI test for Promo Calendar tab.

Run this script to test the Promo Calendar tab in the GUI.
"""
import tkinter as tk
from pathlib import Path
from src.gui.app import DesktopOrderApp

def main():
    """Launch GUI with promo calendar tab."""
    print("=" * 60)
    print("Promo Calendar GUI Test")
    print("=" * 60)
    print("\nInstructions:")
    print("1. Navigate to the '📅 Calendario Promo' tab")
    print("2. Try adding a promo window:")
    print("   - Select a SKU from autocomplete")
    print("   - Choose start and end dates")
    print("   - Optionally specify store ID")
    print("   - Click '✓ Aggiungi Promo'")
    print("3. Verify the window appears in the table")
    print("4. Try removing a window:")
    print("   - Select a row in the table")
    print("   - Click '🗑️ Rimuovi Selezionata'")
    print("   - Confirm deletion")
    print("5. Verify filtering by SKU works")
    print("\nExpected Features:")
    print("✓ Form validation (SKU required, dates required)")
    print("✓ Autocomplete for SKU selection")
    print("✓ Date pickers (if tkcalendar installed)")
    print("✓ Table shows all promo windows with status coloring")
    print("✓ Auto-merge overlapping windows (user preference)")
    print("✓ Auto-sync sales.csv on every add/remove (user preference)")
    print("✓ Tab positioned before Settings")
    print("\n" + "=" * 60)
    print("Starting GUI...")
    print("=" * 60 + "\n")
    
    # Launch GUI
    root = tk.Tk()
    
    # Use test data directory if it exists, otherwise default
    test_data_dir = Path("test_data")
    if test_data_dir.exists():
        app = DesktopOrderApp(root, data_dir=test_data_dir)
    else:
        app = DesktopOrderApp(root)
    
    root.mainloop()


if __name__ == "__main__":
    main()
