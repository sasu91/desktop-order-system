"""
Frozen-aware path resolver for desktop-order-system.

Provides stable, portable paths whether the app runs:
  - From the IDE / source tree (development)
  - As a PyInstaller frozen .exe (production)

Rules
-----
* base_dir   → directory of the .exe (frozen) or project root (dev)
* data_dir   → base_dir/data  (portable first); fallback %APPDATA%/DesktopOrderSystem/data
* logs_dir   → base_dir/logs  (portable first); fallback %APPDATA%/DesktopOrderSystem/logs
* migrations → sys._MEIPASS/migrations (frozen) or base_dir/migrations (dev)
* db_path    → data_dir/app.db
* backup_dir → data_dir/backups

NEVER use os.getcwd() or relative Path("...") strings in runtime code;
always call one of the functions below.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_base_dir() -> Path:
    """
    Return the application's root directory.

    - frozen (PyInstaller onedir): directory that contains the .exe file
      (sys.executable = <install_dir>/DesktopOrderSystem.exe)
    - dev / IDE: project root  (two levels up from src/utils/paths.py)
    """
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys.executable = full path to the .exe
        return Path(sys.executable).resolve().parent
    # Dev: src/utils/paths.py  →  parent = src/utils, parent.parent = src, parent.parent.parent = project root
    return Path(__file__).resolve().parent.parent.parent


def _try_writable(path: Path) -> bool:
    """
    Return True if *path* can be created and used as a writable directory.

    Creates the directory if it does not exist.  Uses a canary-file probe
    so we detect permission issues (e.g. system volume read-only, UAC).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        canary = path / ".write_probe"
        canary.touch()
        canary.unlink()
        return True
    except (OSError, PermissionError):
        return False


def _appdata_dir(sub: str) -> Path:
    """Return %APPDATA%/DesktopOrderSystem/<sub> (Windows) or ~/DesktopOrderSystem/<sub>."""
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "DesktopOrderSystem" / sub


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_base_dir() -> Path:
    """
    Application root:
      - frozen → directory of DesktopOrderSystem.exe
      - dev    → repository / project root
    """
    return _get_base_dir()


def get_data_dir() -> Path:
    """
    Portable data directory.

    Priority:
      1. <base_dir>/data          ← preferred (portable, next to .exe)
      2. %APPDATA%/DesktopOrderSystem/data  ← fallback if base_dir is read-only
    """
    primary = _get_base_dir() / "data"
    if _try_writable(primary):
        return primary
    fallback = _appdata_dir("data")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def get_logs_dir() -> Path:
    """
    Portable logs directory.

    Priority:
      1. <base_dir>/logs
      2. %APPDATA%/DesktopOrderSystem/logs
    """
    primary = _get_base_dir() / "logs"
    if _try_writable(primary):
        return primary
    fallback = _appdata_dir("logs")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def get_migrations_dir() -> Path:
    """
    SQL migrations directory.

    - frozen → sys._MEIPASS/migrations  (bundled inside the build,
               read-only — already applied at first run, only read afterwards)
    - dev    → <base_dir>/migrations
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "migrations"  # type: ignore[attr-defined]
    return _get_base_dir() / "migrations"


def get_db_path() -> Path:
    """Full path to the SQLite database file."""
    return get_data_dir() / "app.db"


def get_backup_dir() -> Path:
    """Full path to the automatic-backup directory."""
    return get_data_dir() / "backups"
