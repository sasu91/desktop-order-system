"""
Project configuration and constants.
"""
from pathlib import Path
from datetime import timedelta

# Project root
PROJECT_ROOT = Path(__file__).parent

# Data directory
DATA_DIR = PROJECT_ROOT / "data"

# Default parameters (can be overridden via config file)
DEFAULT_LEAD_TIME_DAYS = 7
DEFAULT_DAYS_COVER = 30
DEFAULT_MIN_STOCK = 10
DEFAULT_MAX_STOCK = 500

# UI Constants
ITEMS_PER_RECEIPT_PAGE = 5
