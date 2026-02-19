# -*- mode: python ; coding: utf-8 -*-
#
# DesktopOrderSystem.spec
# PyInstaller spec — onedir portable build for Windows 10/11 x64
#
# Usage:
#   pyinstaller DesktopOrderSystem.spec --clean --noconfirm
#
# Output: dist/DesktopOrderSystem/   (folder with .exe + libs)
#
# ---------------------------------------------------------------------
# NOTE ON PATHS
# The application uses src/utils/paths.py to resolve all runtime dirs;
# it never relies on cwd or hardcoded relative paths.
# Migrations SQL files are bundled so the DB can be initialised on first
# run even on a machine without the source tree.
# ---------------------------------------------------------------------

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Project root (where this .spec lives)
ROOT = Path(SPECPATH)

# ── Data files to include in the bundle ──────────────────────────────
# Format: (source, dest_inside_bundle)
added_datas = [
    # SQL migrations — read by src/db.py at first run to initialise the DB
    (str(ROOT / "migrations"), "migrations"),
]

# Include a default settings.json if it already exists in data/
_default_settings = ROOT / "data" / "settings.json"
if _default_settings.exists():
    added_datas.append((str(_default_settings), "data"))

# ── Hidden imports that PyInstaller's static analyser misses ─────────
hidden = [
    # tkcalendar + its Babel locale data
    "tkcalendar",
    "babel",
    "babel.numbers",
    "babel.dates",
    "babel.core",
    # matplotlib TkAgg backend (dynamic import via matplotlib.use('TkAgg'))
    "matplotlib",
    "matplotlib.pyplot",
    "matplotlib.figure",
    "matplotlib.backends.backend_tkagg",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends._backend_tk",
    # numpy (used by matplotlib)
    "numpy",
    "numpy.core._multiarray_umath",
    # PIL / Pillow
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "PIL._imagingtk",
    # python-barcode
    "barcode",
    "barcode.writer",
    "barcode.codex",
    "barcode.ean",
    "barcode.isxn",
    # tkinter (usually found, but explicit is safer)
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "tkinter.filedialog",
    "tkinter.simpledialog",
    # sqlite3 (stdlib, but ensure the .so is included on Linux build hosts)
    "_sqlite3",
    "sqlite3",
    # json / csv (stdlib, normally bundled — be explicit)
    "csv",
    "json",
    # email / logging (used internally)
    "logging",
    "logging.handlers",
]

# Collect all barcode sub-modules (writer plugins loaded dynamically)
hidden += collect_submodules("barcode")
# Collect all matplotlib backends just in case user switches
hidden += collect_submodules("matplotlib.backends")

# ── Exclusions (tests, dev tools, heavy unused libs) ─────────────────
exclusions = [
    "pytest",
    "py",
    "_pytest",
    "IPython",
    "ipykernel",
    "jupyter",
    "notebook",
    "jinja2",       # not needed at runtime
    "setuptools",
    "pkg_resources",
    "docutils",
    "sphinx",
    "wx",           # alternate GUI toolkit — not used
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
]

# ── Analysis ──────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=exclusions,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data)

# ── EXE (no console window) ───────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,         # onedir mode: binaries collected separately
    name="DesktopOrderSystem",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                 # windowed (no console flicker)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",       # uncomment when an .ico file is provided
    version=str(ROOT / "version_info.txt") if (ROOT / "version_info.txt").exists() else None,
)

# ── COLLECT (onedir bundle) ───────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140.dll", "msvcp140.dll"],   # don't compress VC runtimes
    name="DesktopOrderSystem",
)
