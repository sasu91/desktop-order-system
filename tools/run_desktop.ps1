<#
.SYNOPSIS
    Avvia la GUI desktop di desktop-order-system.

.DESCRIPTION
    Legge le variabili d'ambiente da .env nella radice del progetto (se esiste),
    applica i default DOS_* e lancia main.py.
    Le variabili già presenti nell'ambiente hanno priorità su .env.

.EXAMPLE
    # Avvio normale
    .\tools\run_desktop.ps1

    # Override storage backend
    $env:DOS_STORAGE_BACKEND = "csv"; .\tools\run_desktop.ps1

.NOTES
    Prerequisiti: Python 3.12+, dipendenze in requirements.txt.
    Riferimento variabili: docs/config.md
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 0. Radice del progetto
# ---------------------------------------------------------------------------
$ProjectRoot = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $ProjectRoot

# ---------------------------------------------------------------------------
# 1. Venv — crea se non esiste, attiva, installa backend + dipendenze desktop
# ---------------------------------------------------------------------------
$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "[run_desktop] Creazione venv in $VenvDir ..." -ForegroundColor Cyan
    python -m venv $VenvDir
}

Write-Host "[run_desktop] Attivazione venv ..." -ForegroundColor Cyan
& "$VenvDir\Scripts\Activate.ps1"

Write-Host "[run_desktop] Installazione/aggiornamento dipendenze desktop ..." -ForegroundColor Cyan
# desktop/requirements.txt include già: -e ../backend[api]
pip install --quiet -r "$ProjectRoot\desktop\requirements.txt"

# ---------------------------------------------------------------------------
# 2. Carica .env dalla radice del progetto (solo variabili non già impostate)
# ---------------------------------------------------------------------------
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Write-Host "[run_desktop] Caricamento variabili da $EnvFile" -ForegroundColor Cyan
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key   = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        if (-not [System.Environment]::GetEnvironmentVariable($key)) {
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
            Write-Host "  $key=$value" -ForegroundColor DarkGray
        }
    }
} else {
    Write-Host "[run_desktop] .env non trovato nella radice — uso solo variabili d'ambiente" -ForegroundColor Yellow
    Write-Host "  Copia .env.example in .env per personalizzare percorsi e backend." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 3. Variabili con default
# ---------------------------------------------------------------------------
if (-not $env:DOS_STORAGE_BACKEND) { $env:DOS_STORAGE_BACKEND = "sqlite" }
if (-not $env:DOS_LOG_LEVEL)        { $env:DOS_LOG_LEVEL        = "INFO"   }

# ---------------------------------------------------------------------------
# 4. Riepilogo configurazione
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Desktop GUI — configurazione ===" -ForegroundColor Green
if ($env:DOS_DATA_DIR) { Write-Host "  DOS_DATA_DIR      : $($env:DOS_DATA_DIR)" }
if ($env:DOS_DB_PATH)  { Write-Host "  DOS_DB_PATH       : $($env:DOS_DB_PATH)"  }
Write-Host "  DOS_STORAGE_BACKEND : $($env:DOS_STORAGE_BACKEND)"
Write-Host "  DOS_LOG_LEVEL       : $($env:DOS_LOG_LEVEL)"
Write-Host ""

# ---------------------------------------------------------------------------
# 5. Avvio desktop
# ---------------------------------------------------------------------------
Write-Host "[run_desktop] Avvio: python desktop/main.py" -ForegroundColor Green
Write-Host ""

python desktop/main.py
