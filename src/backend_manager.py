"""
BackendManager — gestisce il processo backend FastAPI (uvicorn) come sottoprocesso
del desktop, incluso avvio/arresto, health-check e rilevamento IP LAN.

Ciclo di vita:
  start()        → avvio subprocess, polling /health fino a ready (timeout 15 s)
  stop()         → termine graceful (SIGTERM / terminate()) + join con timeout
  is_running()   → True se il processo è vivo e /health risponde
  lan_base_url   → property: "http://<ip_lan>:<port>"

LAN IP detection:
  Usata la tecnica socket-route: connect UDP verso 8.8.8.8:80 per scoprire
  l'interfaccia di uscita attiva; non invia traffico reale.

Thread-safety:
  start/stop possono essere chiamati dal thread Tkinter; il polling di /health
  viene eseguito in un threading.Thread daemon per non bloccare la GUI.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Porta e host predefiniti
# ---------------------------------------------------------------------------
DEFAULT_PORT = 8000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_lan_ip() -> str:
    """Restituisce l'indirizzo IP LAN del PC tramite route outbound.

    Non invia traffico reale: crea un socket UDP senza fare connect effettivo.
    Fallback: 127.0.0.1 (loopback, solo emulatore Android).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _health_check(base_url: str, timeout: float = 2.0) -> bool:
    """Restituisce True se GET {base_url}/health risponde con status 200."""
    try:
        req = urllib.request.Request(
            f"{base_url}/health",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# BackendManager
# ---------------------------------------------------------------------------

class BackendManager:
    """Gestisce il ciclo di vita del processo backend.

    Parametri
    ----------
    port:            porta TCP su cui uvicorn ascolta (default 8000)
    backend_dir:     la directory contenente il pacchetto dos_backend
                     (default: <root_progetto>/backend)
    on_ready:        callback(base_url: str) chiamata dal thread-watcher
                     sul thread principale tramite after(); non obbligatoria
    on_failed:       callback(reason: str) in caso startup fallisce
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        backend_dir: Path | None = None,
        on_ready: Callable[[str], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
        tk_root=None,                    # usato per after() cross-thread
    ):
        self.port = port
        self.backend_dir: Path = backend_dir or (
            Path(__file__).parent.parent / "backend"
        )
        self.on_ready = on_ready
        self.on_failed = on_failed
        self._tk_root = tk_root

        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._started = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def lan_base_url(self) -> str:
        return f"http://{get_lan_ip()}:{self.port}"

    @property
    def local_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pairing_url(self) -> str:
        """QR payload da scansionare su Android: dos://pair?base_url=http://..."""
        return f"dos://pair?base_url={self.lan_base_url}"

    def start(self) -> None:
        """Avvia uvicorn in un subprocess.

        L'avvio è non-bloccante: un thread daemon fa polling su /health e chiama
        on_ready quando il server è pronto.  Sicuro da chiamare più volte
        (no-op se già in esecuzione).
        """
        with self._lock:
            if self._started and self._process and self._process.poll() is None:
                logger.debug("BackendManager: già in esecuzione, skip start()")
                return
            self._started = False
            logger.info("BackendManager: avvio backend...")

        env = os.environ.copy()
        env["DOS_API_HOST"] = "0.0.0.0"    # LAN-reachable
        env["DOS_API_PORT"] = str(self.port)
        env["DOS_API_TOKEN"] = ""           # dev mode — nessun token
        env["DOS_LOG_LEVEL"] = "warning"    # riduce rumore nei log desktop

        # PYTHONPATH: assicura che dos_backend sia trovabile
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(self.backend_dir) + os.pathsep + existing
            if existing else str(self.backend_dir)
        )

        cmd = [
            sys.executable,
            "-m", "dos_backend.api.main",
        ]

        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(self.backend_dir),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:
            logger.error(f"BackendManager: impossibile avviare il processo: {exc}")
            self._fire_failed(str(exc))
            return

        with self._lock:
            self._process = process

        # Thread daemon: polling health fino a ready o timeout
        threading.Thread(
            target=self._wait_for_ready,
            args=(process,),
            daemon=True,
            name="backend-health-poller",
        ).start()

    def stop(self) -> None:
        """Termina il processo backend in modo graceful."""
        with self._lock:
            proc = self._process
            self._process = None
            self._started = False

        if proc is None:
            return

        logger.info("BackendManager: arresto backend...")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception as exc:
            logger.warning(f"BackendManager: errore durante arresto: {exc}")

    def is_running(self) -> bool:
        """True se il processo è vivo e /health risponde."""
        with self._lock:
            proc = self._process
        if proc is None or proc.poll() is not None:
            return False
        return _health_check(self.local_base_url, timeout=1.0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wait_for_ready(self, process: subprocess.Popen) -> None:
        """Polling /health; chiama on_ready o on_failed sul thread Tkinter."""
        started_at = time.monotonic()
        timeout = 15.0
        interval = 0.4

        while time.monotonic() - started_at < timeout:
            if process.poll() is not None:
                # Processo uscito prematuramente
                stderr_output = ""
                try:
                    stderr_output = process.stderr.read(500) if process.stderr else ""
                except Exception:
                    pass
                reason = f"processo terminato (exit {process.returncode})"
                if stderr_output:
                    reason += f": {stderr_output[:200]}"
                logger.error(f"BackendManager: {reason}")
                self._fire_failed(reason)
                return

            if _health_check(self.local_base_url):
                with self._lock:
                    self._started = True
                logger.info(
                    f"BackendManager: pronto su {self.lan_base_url} "
                    f"(locale: {self.local_base_url})"
                )
                self._fire_ready(self.lan_base_url)
                return

            time.sleep(interval)

        # Timeout raggiunto
        reason = "timeout: /health non ha risposto entro 15 s"
        logger.error(f"BackendManager: {reason}")
        self._fire_failed(reason)

    def _fire_ready(self, base_url: str) -> None:
        cb = self.on_ready          # capture now — avoid None-call if reassigned later
        if cb is None:
            return
        if self._tk_root is not None:
            self._tk_root.after(0, lambda: cb(base_url))
        else:
            cb(base_url)

    def _fire_failed(self, reason: str) -> None:
        cb = self.on_failed         # capture now
        if cb is None:
            return
        if self._tk_root is not None:
            self._tk_root.after(0, lambda: cb(reason))
        else:
            cb(reason)
