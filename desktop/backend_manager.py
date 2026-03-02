"""Shim: re-esporta BackendManager e get_lan_ip da src.backend_manager.

Quando si esegue desktop/main.py, la directory desktop/ viene aggiunta a
sys.path ma non il root del progetto.  Questo modulo aggiunge il root al path
in modo che l'import da src funzioni, mantenendo un unico file sorgente
per BackendManager.
"""
from pathlib import Path
import sys

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.backend_manager import BackendManager, get_lan_ip  # noqa: E402

__all__ = ["BackendManager", "get_lan_ip"]
