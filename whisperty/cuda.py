"""Whisperty — détection et installation (opt-in) du support GPU CUDA.

CTranslate2 (donc faster-whisper) n'accélère sur GPU qu'avec les bibliothèques CUDA 12
cuBLAS/cuDNN, fournies par les wheels pip ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12``.
Ce module :

* **détecte** la présence d'un GPU CUDA (``device_available``) et celle des composants
  (``components_present``) — deux conditions distinctes ;
* **installe** ces composants à la demande de l'utilisateur (``start_install``), jamais
  en silence : le téléchargement (~1,3 Go depuis PyPI) est le SEUL appel réseau, du même
  ordre que le téléchargement initial du modèle Whisper, et reste opt-in.

⚠️ Contrainte cardinale du projet (zéro réseau à l'usage) : l'installation n'est qu'un
**setup ponctuel** explicitement déclenché ; aucune donnée dictée ne transite. Elle n'est
possible qu'en exécution depuis les sources (``pip`` présent), pas dans l'exe figé
(``can_install`` renvoie alors ``False``).
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Wheels fournissant cuBLAS/cuDNN pour CUDA 12 (CTranslate2 >= 4.5 : cuDNN 9).
CUDA_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")

# CREATE_NO_WINDOW : pas de fenêtre console quand l'install est lancée depuis l'UI.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def nvidia_bin_dirs() -> list[str]:
    """Répertoires ``bin`` des wheels ``nvidia-*-cu12`` contenant des DLL.

    Source unique de localisation, réutilisée par ``transcriber`` (recherche/préchargement
    des DLL) et par ``components_present`` ci-dessous.
    """
    try:
        spec = importlib.util.find_spec("nvidia")
    except Exception:  # noqa: BLE001 — paquet absent / introspection impossible
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    dirs: list[str] = []
    for base in spec.submodule_search_locations:
        for bin_dir in Path(base).glob("*/bin"):
            if bin_dir.is_dir() and any(bin_dir.glob("*.dll")):
                dirs.append(str(bin_dir))
    return dirs


def components_present() -> bool:
    """True si cuBLAS **et** cuDNN sont installés (wheels présents avec leurs DLL)."""
    dirs = [Path(d) for d in nvidia_bin_dirs()]
    if not dirs:
        return False
    has_cublas = any(any(d.glob("cublas64_*.dll")) for d in dirs)
    has_cudnn = any(any(d.glob("cudnn*.dll")) for d in dirs)
    return has_cublas and has_cudnn


def device_available() -> bool:
    """True si CTranslate2 voit au moins un GPU CUDA (pilote NVIDIA présent)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 — ctranslate2 absent / appel indisponible
        return False


def can_install() -> bool:
    """L'installation pip est possible : exécution depuis les sources, pas l'exe figé."""
    return not getattr(sys, "frozen", False)


class _Installer:
    """Installation asynchrone des composants GPU, interrogeable par polling (thread-safe).

    Un seul thread d'installation à la fois ; l'état (``idle``/``running``/``done``/``error``)
    et un message lisible sont publiés sous verrou pour l'UI.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"
        self._message = ""

    def status(self) -> dict:
        with self._lock:
            return {"state": self._state, "message": self._message}

    def start(self) -> dict:
        with self._lock:
            if self._state == "running":
                return {"ok": True, "state": "running", "message": self._message}
            if not can_install():
                return {
                    "ok": False,
                    "error": "Installation indisponible dans la version installée. "
                    "Utilisez la version GPU ou installez manuellement les composants.",
                }
            self._state = "running"
            self._message = "Téléchargement des composants GPU (~1,3 Go)…"
            threading.Thread(target=self._run, daemon=True, name="cuda-install").start()
            return {"ok": True, "state": "running", "message": self._message}

    def _set(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    def _run(self) -> None:
        cmd = [sys.executable, "-m", "pip", "install", *CUDA_PACKAGES]
        logger.info("Installation des composants GPU : %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001 — pip introuvable, droits, etc.
            logger.exception("Lancement de pip échoué")
            self._set("error", f"Lancement de l'installation impossible : {exc}")
            return

        tail = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line:
                tail = line
                # Affiche la progression pip lisible (Downloading… / Installing…).
                if line.startswith(("Downloading", "Installing", "Collecting")):
                    self._set("running", line)
        code = proc.wait()
        if code == 0:
            # Le paquet nvidia vient d'apparaître dans site-packages : purge les caches
            # d'import pour que find_spec("nvidia") le voie sans redémarrage.
            importlib.invalidate_caches()
            logger.info("Composants GPU installés.")
            self._set("done", "Composants GPU installés. Activez « CUDA (GPU) » puis enregistrez.")
        else:
            logger.error("Installation des composants GPU échouée (code %s) : %s", code, tail)
            self._set("error", f"Échec de l'installation (code {code}). {tail}")


_installer = _Installer()


def start_install() -> dict:
    """Lance l'installation des composants GPU (opt-in) si possible. Non bloquant."""
    return _installer.start()


def install_status() -> dict:
    """État courant de l'installation ({state, message}) pour le polling de l'UI."""
    return _installer.status()


def status() -> dict:
    """Instantané complet de l'état GPU pour l'UI (détection + installation)."""
    inst = install_status()
    return {
        "gpu": device_available(),
        "components": components_present(),
        "canInstall": can_install(),
        "install": inst["state"],
        "message": inst["message"],
    }
