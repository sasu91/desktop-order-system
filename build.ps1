#Requires -Version 5.1
<#
.SYNOPSIS
    Reproducible portable build for DesktopOrderSystem.

.DESCRIPTION
    Creates an isolated Python venv, installs build dependencies, runs PyInstaller
    and packages the result in a ZIP.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipVenv       # re-use existing .venv-build
    .\build.ps1 -NoCleanbuild   # skip cleaning previous dist

.NOTES
    Requirements:
      - Python 3.12 x64 on PATH  (py -3.12 or python)
      - Run from the project root (where this .ps1 lives)
    Output:
      dist\DesktopOrderSystem\DesktopOrderSystem.exe  (onedir portable)
      dist\DesktopOrderSystem.zip
#>

[CmdletBinding()]
param(
    [switch] $SkipVenv,
    [switch] $NoCleanBuild,
    [string] $PythonExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Resolve Python ───────────────────────────────────────────────────────────
function Find-Python {
    if ($PythonExe -ne "" -and (Test-Path $PythonExe)) { return $PythonExe }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $v = & py -3.12 --version 2>&1
        if ($LASTEXITCODE -eq 0) { return "py -3.12" }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $v = & python --version 2>&1
        if ($v -match "Python 3\.(1[0-9]|[2-9]\d)") { return "python" }
    }
    throw "Python 3.10+ not found on PATH. Install Python 3.12 x64 and retry."
}

$PyCmd = Find-Python
Write-Host "[INFO] Using Python: $PyCmd" -ForegroundColor Cyan

# ── Create / activate isolated build venv ───────────────────────────────────
$VenvDir = ".venv-build"

if (-not $SkipVenv -or -not (Test-Path "$VenvDir\Scripts\Activate.ps1")) {
    Write-Host "[INFO] Creating build venv in $VenvDir ..." -ForegroundColor Cyan
    Invoke-Expression "$PyCmd -m venv $VenvDir"
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed." }
}

$PipExe    = "$VenvDir\Scripts\pip.exe"
$PyInsExe  = "$VenvDir\Scripts\pyinstaller.exe"
$PythonVenv = "$VenvDir\Scripts\python.exe"

# Activate (optional for subprocesses; Activate.ps1 is for interactive use)
Write-Host "[INFO] Installing build dependencies ..." -ForegroundColor Cyan
& $PipExe install --upgrade pip --quiet
& $PipExe install -r requirements-build.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

# ── Clean previous build artifacts ──────────────────────────────────────────
if (-not $NoCleanBuild) {
    Write-Host "[INFO] Cleaning previous build ..." -ForegroundColor Cyan
    if (Test-Path "build\DesktopOrderSystem") { Remove-Item "build\DesktopOrderSystem" -Recurse -Force }
    if (Test-Path "dist\DesktopOrderSystem")  { Remove-Item "dist\DesktopOrderSystem"  -Recurse -Force }
    if (Test-Path "dist\DesktopOrderSystem.zip") { Remove-Item "dist\DesktopOrderSystem.zip" -Force }
}

# ── Run PyInstaller ──────────────────────────────────────────────────────────
Write-Host "[INFO] Running PyInstaller (onedir, windowed) ..." -ForegroundColor Cyan
& $PyInsExe DesktopOrderSystem.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed. See output above." }

# ── Scaffold portable data/ and logs/ directories ───────────────────────────
Write-Host "[INFO] Scaffolding data\ and logs\ directories ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "dist\DesktopOrderSystem\data" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\DesktopOrderSystem\logs" | Out-Null

# Copy README
if (Test-Path "README_DIST.txt") {
    Copy-Item "README_DIST.txt" "dist\DesktopOrderSystem\README.txt" -Force
}

# ── Create distribution ZIP ──────────────────────────────────────────────────
Write-Host "[INFO] Creating distribution archive ..." -ForegroundColor Cyan
Compress-Archive -Path "dist\DesktopOrderSystem" `
                 -DestinationPath "dist\DesktopOrderSystem.zip" `
                 -Force
Write-Host "[INFO] Archive: dist\DesktopOrderSystem.zip" -ForegroundColor Green

# ── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " BUILD COMPLETE" -ForegroundColor Green
Write-Host " Executable : dist\DesktopOrderSystem\DesktopOrderSystem.exe" -ForegroundColor Green
Write-Host " Archive    : dist\DesktopOrderSystem.zip" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
