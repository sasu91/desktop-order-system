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
    backup_path = None  # Initialize outside try block
    
    if not skus_path.exists():
        print(f"✓ File {skus_path} non esiste (sarà creato con schema aggiornato)")
        return
    
    try:
        # Create backup
        backup_path = data_dir / f"skus_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        shutil.copy(skus_path, backup_path)
        print(f"✓ Backup creato: {backup_path}")
        
        # Read existing data
        with open(skus_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            old_fieldnames = reader.fieldnames  # MUST read before consuming reader
            
            # Handle empty CSV
            if not old_fieldnames:
                print(f"⚠️  File CSV vuoto o senza header, skip migrazione")
                return
            
            rows = list(reader)
        
        print(f"✓ Letti {len(rows)} SKU dal file esistente")
        
        # Check if column already exists
        if 'oos_popup_preference' in old_fieldnames:
            print(f"✓ Colonna 'oos_popup_preference' già presente, nessuna migrazione necessaria")
            return
        
        # Add new column in correct position (after oos_detection_mode, before forecast_method)
        # This matches the schema in csv_layer.py
        new_fieldnames = list(old_fieldnames)
        
        # Find insertion point
        if 'oos_detection_mode' in new_fieldnames:
            insert_idx = new_fieldnames.index('oos_detection_mode') + 1
        elif 'oos_boost_percent' in new_fieldnames:
            insert_idx = new_fieldnames.index('oos_boost_percent') + 1
        else:
            # Fallback: insert before forecast_method or at end
            insert_idx = new_fieldnames.index('forecast_method') if 'forecast_method' in new_fieldnames else len(new_fieldnames)
        
        new_fieldnames.insert(insert_idx, 'oos_popup_preference')
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
        
        # Validation: verify migration
        with open(skus_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if 'oos_popup_preference' not in reader.fieldnames:
                # Validation failed: file written but column missing
                print(f"❌ ERRORE CRITICO: Validazione fallita - colonna non trovata nel file migrato")
                if backup_path and backup_path.exists():
                    print(f"⚠️  Ripristino file originale da backup...")
                    shutil.copy(backup_path, skus_path)
                    print(f"✓ File originale ripristinato")
                raise ValueError("Migrazione fallita: colonna 'oos_popup_preference' non trovata nel file aggiornato")
        
        print(f"✓ Validazione: colonna presente nel file migrato")
        
    except FileNotFoundError as e:
        print(f"❌ Errore: File non trovato - {e}")
        raise
    except PermissionError as e:
        print(f"❌ Errore: Permessi insufficienti - {e}")
        raise
    except Exception as e:
        print(f"❌ Errore durante migrazione: {e}")
        if backup_path and backup_path.exists():
            print(f"⚠️  Ripristino da backup: {backup_path}")
            try:
                shutil.copy(backup_path, skus_path)
                print(f"✓ File ripristinato da backup")
            except Exception as restore_error:
                print(f"❌ Errore durante ripristino: {restore_error}")
        raise

if __name__ == "__main__":
    print("=== MIGRATION: Add oos_popup_preference to skus.csv ===\n")
    migrate_skus_csv()
    print("\n✅ Migrazione completata con successo!")
