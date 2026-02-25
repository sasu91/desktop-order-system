#!/usr/bin/env bash
# tools/run_backend.sh — Avvia il backend FastAPI (Uvicorn)
#
# Legge le variabili d'ambiente da backend/.env (se esiste),
# applica i default DOS_* e lancia Uvicorn.
# Le variabili già presenti nell'ambiente hanno priorità su backend/.env.
#
# Utilizzo:
#   bash tools/run_backend.sh
#   DOS_API_PORT=9000 bash tools/run_backend.sh
#
# Prerequisiti: Python 3.12+, uvicorn, fastapi nel venv attivo.
# Riferimento variabili: docs/config.md

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Radice del progetto (due livelli sopra tools/)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# 1. Venv — crea se non esiste, attiva, installa backend
# ---------------------------------------------------------------------------
VENV_DIR="$PROJECT_ROOT/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "[run_backend] Creazione venv in $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
fi

# set +u per evitare errori nelle variabili interne dello script activate
set +u
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
set -u

echo "[run_backend] Installazione/aggiornamento dipendenze backend ..."
pip install --quiet -e "$PROJECT_ROOT/backend[api]"

# ---------------------------------------------------------------------------
# 2. Carica backend/.env (solo variabili non già impostate nell'ambiente)
# ---------------------------------------------------------------------------
ENV_FILE="$PROJECT_ROOT/backend/.env"
if [[ -f "$ENV_FILE" ]]; then
    echo "[run_backend] Caricamento variabili da $ENV_FILE"
    # set -a esporta automaticamente ogni variabile assegnata
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
else
    echo "[run_backend] backend/.env non trovato — uso solo variabili d'ambiente"
    echo "  Copia backend/.env.example in backend/.env e modifica i valori."
fi

# ---------------------------------------------------------------------------
# 3. Variabili con default
# ---------------------------------------------------------------------------
DOS_API_HOST="${DOS_API_HOST:-127.0.0.1}"
DOS_API_PORT="${DOS_API_PORT:-8000}"
DOS_LOG_LEVEL="${DOS_LOG_LEVEL:-INFO}"

# ---------------------------------------------------------------------------
# 4. Variabili obbligatorie
# ---------------------------------------------------------------------------
if [[ -z "${DOS_DB_PATH:-}" ]]; then
    echo ""
    echo "[run_backend] ERRORE: DOS_DB_PATH non impostata." >&2
    echo "  Specifica il percorso assoluto al database SQLite:" >&2
    echo "    In backend/.env   →  DOS_DB_PATH=/path/to/data/app.db" >&2
    echo "    Oppure nel terminale: export DOS_DB_PATH=/path/to/data/app.db" >&2
    echo "  Riferimento: docs/config.md" >&2
    exit 1
fi

if [[ ! -f "$DOS_DB_PATH" ]]; then
    echo "[run_backend] AVVISO: DOS_DB_PATH='$DOS_DB_PATH' non esiste ancora. Uvicorn si avvierà comunque."
fi

# ---------------------------------------------------------------------------
# 5. Riepilogo configurazione
# ---------------------------------------------------------------------------
echo ""
echo "=== Backend API — configurazione ==="
echo "  DOS_DB_PATH   : $DOS_DB_PATH"
echo "  DOS_API_HOST  : $DOS_API_HOST"
echo "  DOS_API_PORT  : $DOS_API_PORT"
echo "  DOS_LOG_LEVEL : $DOS_LOG_LEVEL"
echo ""

# ---------------------------------------------------------------------------
# 6. Avvio Uvicorn
# ---------------------------------------------------------------------------
echo "[run_backend] Avvio: python -m uvicorn dos_backend.api.main:app ..."
echo ""

exec python -m uvicorn dos_backend.api.main:app \
    --host "$DOS_API_HOST" \
    --port "$DOS_API_PORT" \
    --log-level "$(echo "$DOS_LOG_LEVEL" | tr '[:upper:]' '[:lower:]')"
