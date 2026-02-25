#!/usr/bin/env python3
"""desktop/main.py — Entry point for the Tkinter desktop client.

Adds the desktop/ directory to sys.path so the `gui` package is importable,
then delegates to gui.app.main().  dos_backend must be installed beforehand:

    pip install -e backend/[api]
"""
import sys
import multiprocessing
from pathlib import Path

# Ensure desktop/ is on the path (enables `from gui.X import ...`)
_desktop_dir = Path(__file__).parent
if str(_desktop_dir) not in sys.path:
    sys.path.insert(0, str(_desktop_dir))

from gui.app import main  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
