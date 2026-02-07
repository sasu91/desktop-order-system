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
    
    except UnicodeDecodeError as e:
        print(f"❌ Errore: File con encoding non UTF-8 - {e}")
        print(f"⚠️  Suggerimento: Converti il file in UTF-8 prima della migrazione")
        raise
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
