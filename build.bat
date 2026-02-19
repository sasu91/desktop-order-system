@echo off
REM ============================================================
REM  build.bat — Reproducible portable build for DesktopOrderSystem
REM
REM  Prerequisites:
REM    - Python 3.12 x64 on PATH  (python --version)
REM    - Run from the project root (where this file lives)
REM
REM  Output: dist\DesktopOrderSystem\DesktopOrderSystem.exe
REM          (onedir portable — extract anywhere, run directly)
REM ============================================================

setlocal enabledelayedexpansion

:: ── 0. Locate Python ────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found on PATH.
    echo         Install Python 3.12 x64 and re-run.
    exit /b 1
)

:: ── 1. Create / activate isolated build venv ────────────────────
set VENV=.venv-build

if not exist "%VENV%\Scripts\activate.bat" (
    echo [INFO] Creating build venv in %VENV% ...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] venv creation failed.
        exit /b 1
    )
)

call "%VENV%\Scripts\activate.bat"

:: ── 2. Install / upgrade build dependencies ─────────────────────
echo [INFO] Installing build dependencies from requirements-build.txt ...
pip install --upgrade pip --quiet
pip install -r requirements-build.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed.
    exit /b 1
)

:: ── 3. Clean previous build artefacts ───────────────────────────
echo [INFO] Cleaning previous build ...
if exist "build\DesktopOrderSystem" rmdir /s /q "build\DesktopOrderSystem"
if exist "dist\DesktopOrderSystem"  rmdir /s /q "dist\DesktopOrderSystem"

:: ── 4. Run PyInstaller ──────────────────────────────────────────
echo [INFO] Running PyInstaller (onedir, no-console) ...
pyinstaller DesktopOrderSystem.spec --clean --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. See output above.
    exit /b 1
)

:: ── 5. Create empty data/ and logs/ inside the bundle ───────────
echo [INFO] Scaffolding portable data and logs directories ...
if not exist "dist\DesktopOrderSystem\data"  mkdir "dist\DesktopOrderSystem\data"
if not exist "dist\DesktopOrderSystem\logs"  mkdir "dist\DesktopOrderSystem\logs"

:: ── 6. Copy README into the distribution folder ─────────────────
if exist "README_DIST.txt" (
    copy /y "README_DIST.txt" "dist\DesktopOrderSystem\README.txt" >nul
)

:: ── 7. (Optional) Create distribution ZIP ───────────────────────
echo [INFO] Creating distribution archive ...
powershell -NoProfile -Command ^
  "Compress-Archive -Path 'dist\DesktopOrderSystem' -DestinationPath 'dist\DesktopOrderSystem.zip' -Force"
if errorlevel 1 (
    echo [WARN] ZIP creation failed (non-fatal). Distribute the dist\DesktopOrderSystem folder directly.
) else (
    echo [INFO] Archive created: dist\DesktopOrderSystem.zip
)

:: ── Done ────────────────────────────────────────────────────────
echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Executable : dist\DesktopOrderSystem\DesktopOrderSystem.exe
echo  Archive    : dist\DesktopOrderSystem.zip
echo ============================================================

deactivate
endlocal
