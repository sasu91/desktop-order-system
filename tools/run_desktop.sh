#!/usr/bin/env bash
# tools/run_desktop.sh — Avvia la GUI desktop di desktop-order-system
#
# Legge le variabili d'ambiente da .env nella radice del progetto (se esiste),
# applica i default DOS_* e lancia main.py.
# Le variabili già presenti nell'ambiente hanno priorità su .env.
#
# Utilizzo:
#   bash tools/run_desktop.sh
#   DOS_STORAGE_BACKEND=csv bash tools/run_desktop.sh
#
# Prerequisiti: Python 3.12+, dipendenze in requirements.txt, display X11/Wayland.
# Riferimento variabili: docs/config.md

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Radice del progetto
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# 1. Venv — crea se non esiste, attiva, installa backend + dipendenze desktop
# ---------------------------------------------------------------------------
VENV_DIR="$PROJECT_ROOT/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "[run_desktop] Creazione venv in $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
fi

# set +u per evitare errori nelle variabili interne dello script activate
set +u
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
set -u

echo "[run_desktop] Installazione/aggiornamento dipendenze desktop ..."
# desktop/requirements.txt include già: -e ../backend[api]
pip install --quiet -r "$PROJECT_ROOT/desktop/requirements.txt"

# ---------------------------------------------------------------------------
# 2. Carica .env dalla radice (solo variabili non già impostate nell'ambiente)
# ---------------------------------------------------------------------------
ENV_FILE="$PROJECT_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
    echo "[run_desktop] Caricamento variabili da $ENV_FILE"
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
else
    echo "[run_desktop] .env non trovato nella radice — uso solo variabili d'ambiente"
    echo "  Copia .env.example in .env per personalizzare percorsi e backend."
fi

# ---------------------------------------------------------------------------
# 3. Variabili con default
# ---------------------------------------------------------------------------
DOS_STORAGE_BACKEND="${DOS_STORAGE_BACKEND:-sqlite}"
DOS_LOG_LEVEL="${DOS_LOG_LEVEL:-INFO}"
export DOS_STORAGE_BACKEND DOS_LOG_LEVEL

# ---------------------------------------------------------------------------
# 4. Riepilogo configurazione
# ---------------------------------------------------------------------------
echo ""
echo "=== Desktop GUI — configurazione ==="
[[ -n "${DOS_DATA_DIR:-}" ]] && echo "  DOS_DATA_DIR        : $DOS_DATA_DIR"
[[ -n "${DOS_DB_PATH:-}"  ]] && echo "  DOS_DB_PATH         : $DOS_DB_PATH"
echo "  DOS_STORAGE_BACKEND : $DOS_STORAGE_BACKEND"
echo "  DOS_LOG_LEVEL       : $DOS_LOG_LEVEL"
echo ""

# ---------------------------------------------------------------------------
# 5. Avvio desktop
# ---------------------------------------------------------------------------
echo "[run_desktop] Avvio: python desktop/main.py"
echo ""

exec python desktop/main.py
