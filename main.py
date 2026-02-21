#!/usr/bin/env python3
"""
Desktop Order System - Entry point.

Run this to start the application.
"""
import sys
import multiprocessing
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.gui.app import main

if __name__ == "__main__":
    # Required for ProcessPoolExecutor with PyInstaller / Windows 'spawn' start method.
    # Must be called as early as possible (before any other multiprocessing code).
    multiprocessing.freeze_support()
    main()
