<#
.SYNOPSIS
    Avvia il backend FastAPI (Uvicorn) per desktop-order-system.

.DESCRIPTION
    Legge le variabili d'ambiente da backend/.env (se esiste),
    applica i default DOS_* e lancia Uvicorn.
    Le variabili già presenti nell'ambiente hanno priorità su backend/.env.

.EXAMPLE
    # Avvio normale
    .\tools\run_backend.ps1

    # Override al volo
    $env:DOS_API_PORT = "9000"; .\tools\run_backend.ps1

.NOTES
    Prerequisiti: Python 3.12+, uvicorn, fastapi nel venv attivo.
    Riferimento variabili: docs/config.md
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 0. Radice del progetto (due livelli sopra tools/)
# ---------------------------------------------------------------------------
$ProjectRoot = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $ProjectRoot

# ---------------------------------------------------------------------------
# 1. Venv — crea se non esiste, attiva, installa backend
# ---------------------------------------------------------------------------
$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "[run_backend] Creazione venv in $VenvDir ..." -ForegroundColor Cyan
    python -m venv $VenvDir
}

Write-Host "[run_backend] Attivazione venv ..." -ForegroundColor Cyan
& "$VenvDir\Scripts\Activate.ps1"

Write-Host "[run_backend] Installazione/aggiornamento dipendenze backend ..." -ForegroundColor Cyan
pip install --quiet -e "$ProjectRoot\backend[api]"

# ---------------------------------------------------------------------------
# 2. Carica backend/.env (solo le variabili non già impostate nell'ambiente)
# ---------------------------------------------------------------------------
$EnvFile = Join-Path $ProjectRoot "backend" ".env"
if (Test-Path $EnvFile) {
    Write-Host "[run_backend] Caricamento variabili da $EnvFile" -ForegroundColor Cyan
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        # Salta righe vuote e commenti
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key   = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        # Env presente nell'ambiente → non sovrascrivere
        if (-not [System.Environment]::GetEnvironmentVariable($key)) {
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
            Write-Host "  $key=$value" -ForegroundColor DarkGray
        }
    }
} else {
    Write-Host "[run_backend] backend/.env non trovato — uso solo variabili d'ambiente" -ForegroundColor Yellow
    Write-Host "  Copia backend/.env.example in backend/.env e modifica i valori." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 3. Variabili con default
# ---------------------------------------------------------------------------
if (-not $env:DOS_API_HOST)  { $env:DOS_API_HOST  = "127.0.0.1" }
if (-not $env:DOS_API_PORT)  { $env:DOS_API_PORT  = "8000" }
if (-not $env:DOS_LOG_LEVEL) { $env:DOS_LOG_LEVEL = "INFO" }

# ---------------------------------------------------------------------------
# 4. Variabili obbligatorie
# ---------------------------------------------------------------------------
if (-not $env:DOS_DB_PATH) {
    Write-Error @"
[run_backend] ERRORE: DOS_DB_PATH non impostata.
Specifica il percorso assoluto al database SQLite:
  In backend/.env  →  DOS_DB_PATH=C:\path\to\data\app.db
  Oppure nel terminale  →  `$env:DOS_DB_PATH = "C:\path\to\data\app.db"
Riferimento: docs/config.md
"@
    exit 1
}

if (-not (Test-Path $env:DOS_DB_PATH)) {
    Write-Warning "[run_backend] Il file DOS_DB_PATH='$($env:DOS_DB_PATH)' non esiste ancora. Uvicorn si avvierà comunque."
}

# ---------------------------------------------------------------------------
# 5. Riepilogo configurazione
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Backend API — configurazione ===" -ForegroundColor Green
Write-Host "  DOS_DB_PATH   : $($env:DOS_DB_PATH)"
Write-Host "  DOS_API_HOST  : $($env:DOS_API_HOST)"
Write-Host "  DOS_API_PORT  : $($env:DOS_API_PORT)"
Write-Host "  DOS_LOG_LEVEL : $($env:DOS_LOG_LEVEL)"
Write-Host ""

# ---------------------------------------------------------------------------
# 6. Avvio Uvicorn
# ---------------------------------------------------------------------------
$UvicornArgs = @(
    "-m", "uvicorn",
    "dos_backend.api.main:app",
    "--host", $env:DOS_API_HOST,
    "--port", $env:DOS_API_PORT,
    "--log-level", $env:DOS_LOG_LEVEL.ToLower()
)

Write-Host "[run_backend] Avvio: python -m uvicorn dos_backend.api.main:app" -ForegroundColor Green
Write-Host ""

python @UvicornArgs
