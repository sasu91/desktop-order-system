#!/usr/bin/env python3
"""
Migration script: Add oos_popup_preference column to existing skus.csv

Safely adds the new column with default value "ask" to all existing SKU records.
Creates backup before modification.
"""

import csv
import shutil
from pathlib import Path
from datetime import datetime

def migrate_skus_csv():
    """Add oos_popup_preference column to skus.csv."""
    
    data_dir = Path(__file__).parent / "data"
    skus_path = data_dir / "skus.csv"
    
    if not skus_path.exists():
        print(f"✓ File {skus_path} non esiste (sarà creato con schema aggiornato)")
        return
    
    # Create backup
    backup_path = data_dir / f"skus_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    shutil.copy(skus_path, backup_path)
    print(f"✓ Backup creato: {backup_path}")
    
    # Read existing data
    with open(skus_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        old_fieldnames = reader.fieldnames
    
    print(f"✓ Letti {len(rows)} SKU dal file esistente")
    
    # Check if column already exists
    if 'oos_popup_preference' in old_fieldnames:
        print(f"✓ Colonna 'oos_popup_preference' già presente, nessuna migrazione necessaria")
        return
    
    # Add new column to all rows
    new_fieldnames = list(old_fieldnames) + ['oos_popup_preference']
    for row in rows:
        row['oos_popup_preference'] = 'ask'  # Default value
    
    # Write updated data
    with open(skus_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=new_fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"✓ Migrazione completata: aggiunta colonna 'oos_popup_preference' (default='ask')")
    print(f"✓ File aggiornato: {skus_path}")
    print(f"\nRiepilogo:")
    print(f"  - SKU migrati: {len(rows)}")
    print(f"  - Valore default: 'ask'")
    print(f"  - Backup disponibile: {backup_path}")

if __name__ == "__main__":
    print("=== MIGRATION: Add oos_popup_preference to skus.csv ===\n")
    migrate_skus_csv()
    print("\n✅ Migrazione completata con successo!")
