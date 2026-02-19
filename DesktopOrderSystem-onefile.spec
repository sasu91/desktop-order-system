# -*- mode: python ; coding: utf-8 -*-
#
# DesktopOrderSystem-onefile.spec
# PyInstaller spec — ONEFILE variant for DesktopOrderSystem
#
# ⚠ IMPORTANT — Onefile limitations:
#   • Startup is ~5-10 s slower (extracts to a tmp dir on each launch).
#   • sys._MEIPASS points to a TEMP subfolder — NOT next to the .exe.
#   • DATA (data/) and LOGS (logs/) are resolved via src/utils/paths.py
#     which uses sys.executable (the real .exe path) — so data/logs ARE
#     still written next to the .exe, not in TEMP. Migrations are in _MEIPASS.
#   • More aggressive antivirus false positives (single-file extractors).
#
# Usage:
#   pyinstaller DesktopOrderSystem-onefile.spec --clean --noconfirm
#
# Output: dist/DesktopOrderSystem.exe  (single executable)
# ---------------------------------------------------------------------

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH)

added_datas = [
    (str(ROOT / "migrations"), "migrations"),
]
_default_settings = ROOT / "data" / "settings.json"
if _default_settings.exists():
    added_datas.append((str(_default_settings), "data"))

hidden = [
    "tkcalendar",
    "babel", "babel.numbers", "babel.dates", "babel.core",
    "matplotlib", "matplotlib.pyplot", "matplotlib.figure",
    "matplotlib.backends.backend_tkagg",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends._backend_tk",
    "numpy", "numpy.core._multiarray_umath",
    "PIL", "PIL.Image", "PIL.ImageTk", "PIL._imagingtk",
    "barcode", "barcode.writer", "barcode.codex", "barcode.ean", "barcode.isxn",
    "tkinter", "tkinter.ttk", "tkinter.messagebox",
    "tkinter.filedialog", "tkinter.simpledialog",
    "_sqlite3", "sqlite3", "csv", "json", "logging", "logging.handlers",
]
hidden += collect_submodules("barcode")
hidden += collect_submodules("matplotlib.backends")

exclusions = [
    "pytest", "py", "_pytest", "IPython", "ipykernel",
    "jupyter", "notebook", "jinja2", "setuptools", "pkg_resources",
    "docutils", "sphinx", "wx", "PyQt5", "PyQt6", "PySide2", "PySide6",
]

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
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,       # ← bundled directly into the single EXE
    a.datas,
    [],
    name="DesktopOrderSystem",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140.dll", "msvcp140.dll"],
    console=False,
    runtime_tmpdir=None,   # None = OS temp dir (default); set to '.' to extract next to exe
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    # icon="assets/icon.ico",
    version=str(ROOT / "version_info.txt") if (ROOT / "version_info.txt").exists() else None,
)
