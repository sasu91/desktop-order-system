"""
Project configuration and constants.
"""
from pathlib import Path
from datetime import timedelta
import json
from typing import Literal

# Project root
PROJECT_ROOT = Path(__file__).parent

# Data directory
DATA_DIR = PROJECT_ROOT / "data"

# Storage backend configuration
STORAGE_BACKEND: Literal['csv', 'sqlite'] = 'csv'  # Default: CSV mode
DATABASE_PATH = DATA_DIR / "app.db"
SETTINGS_FILE = DATA_DIR / "settings.json"

# Default parameters (can be overridden via config file)
DEFAULT_LEAD_TIME_DAYS = 7
DEFAULT_DAYS_COVER = 30
DEFAULT_MIN_STOCK = 10
DEFAULT_MAX_STOCK = 500

# UI Constants
ITEMS_PER_RECEIPT_PAGE = 5


# ============================================================
# Storage Backend Management
# ============================================================

def get_storage_backend() -> Literal['csv', 'sqlite']:
    """
    Get current storage backend from settings.json.
    
    Returns:
        'csv' or 'sqlite'
    """
    global STORAGE_BACKEND
    
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                backend = settings.get('storage_backend', 'csv')
                if backend in ('csv', 'sqlite'):
                    STORAGE_BACKEND = backend
                    return backend
        except (json.JSONDecodeError, IOError):
            pass  # Fallback to default
    
    return STORAGE_BACKEND


def set_storage_backend(backend: Literal['csv', 'sqlite']) -> bool:
    """
    Set storage backend in settings.json.
    
    Args:
        backend: 'csv' or 'sqlite'
    
    Returns:
        True if successful, False otherwise
    """
    global STORAGE_BACKEND
    
    if backend not in ('csv', 'sqlite'):
        return False
    
    # Load existing settings
    settings = {}
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass  # Start with empty settings
    
    # Update backend
    settings['storage_backend'] = backend
    
    # Save settings
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        
        STORAGE_BACKEND = backend
        return True
    except IOError:
        return False


def is_sqlite_available() -> bool:
    """
    Check if SQLite database is initialized and accessible.
    
    Returns:
        True if database exists and is valid
    """
    if not DATABASE_PATH.exists():
        return False
    
    try:
        import sqlite3
        conn = sqlite3.connect(str(DATABASE_PATH), timeout=1.0)
        cursor = conn.cursor()
        # Check if schema_version table exists (indicator of initialized DB)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
        has_schema = cursor.fetchone() is not None
        conn.close()
        return has_schema
    except Exception:
        return False


# Initialize storage backend on module load
STORAGE_BACKEND = get_storage_backend()
