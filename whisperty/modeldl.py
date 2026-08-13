"""Whisperty — téléchargement **opt-in** du modèle Whisper depuis l'interface.

Sans modèle disponible (installeur ``-NoModel``, taille changée dans la config,
cache vide en mode hors-ligne), la dictée est impossible : plutôt que de laisser
l'utilisateur face à un échec silencieux, le tableau de bord affiche une bannière
qui propose de télécharger le modèle manquant en un clic.

Doctrine identique à ``cuda.py`` (composants GPU) : ce téléchargement (de ~75 Mo à
~3 Go depuis Hugging Face) est un **setup ponctuel explicitement déclenché**, jamais
silencieux — avec l'installation GPU et le tout premier téléchargement de modèle,
c'est la SEULE exception réseau du projet. Le modèle est matérialisé dans
``models/faster-whisper-<taille>`` À CÔTÉ de la config (comme
``scripts/fetch_model.py``), puis ``config.yaml`` est pointé dessus par l'appelant :
``local_files_only`` reste ``true`` → zéro réseau à l'usage ensuite.

Fonctionne aussi dans l'exe figé (huggingface_hub est embarqué avec faster-whisper),
contrairement à l'installation GPU qui exige pip.
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Tailles téléchargeables automatiquement (miroir de faster_whisper.utils._MODELS).
# Un chemin local ou un id de dépôt exotique n'est PAS proposé au téléchargement.
_KNOWN_SIZES = (
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large",
    "distil-small.en", "distil-medium.en", "distil-large-v2", "distil-large-v3",
    "large-v3-turbo", "turbo",
)

# Poids approximatif du téléchargement (Mo) — informatif, affiché sur le bouton.
_APPROX_MB = {
    "tiny": 75, "tiny.en": 75,
    "base": 145, "base.en": 145,
    "small": 485, "small.en": 485,
    "medium": 1530, "medium.en": 1530,
    "large-v1": 3090, "large-v2": 3090, "large-v3": 3090, "large": 3090,
    "distil-small.en": 335, "distil-medium.en": 790,
    "distil-large-v2": 1510, "distil-large-v3": 1510,
    "large-v3-turbo": 1620, "turbo": 1620,
}


def model_size_name(model: object) -> str:
    """Nom de taille « affichable » d'un réglage ``transcription.model``.

    Un modèle bundlé/téléchargé est un chemin (``models/faster-whisper-medium``) :
    on en extrait la taille (``medium``) pour l'UI et les comparaisons. Un nom de
    taille (``medium``) est renvoyé tel quel.
    """
    base = str(model or "").replace("\\", "/").rstrip("/").split("/")[-1]
    if base.startswith("faster-whisper-"):
        base = base[len("faster-whisper-"):]
    return base


def is_downloadable(size: str) -> bool:
    """True si ``size`` est une taille connue, téléchargeable automatiquement."""
    return size in _KNOWN_SIZES


def approx_size_label(size: str) -> str:
    """Poids humain du téléchargement (« ~1,5 Go », « ~485 Mo ») ; "" si inconnu."""
    mb = _APPROX_MB.get(size)
    if not mb:
        return ""
    if mb < 1000:
        return f"~{mb} Mo"
    gb = mb / 1000.0
    return "~" + f"{gb:.1f}".replace(".", ",") + " Go"


class _Downloader:
    """Téléchargement asynchrone du modèle, interrogeable par polling (thread-safe).

    Un seul téléchargement à la fois ; l'état (``idle``/``running``/``done``/``error``),
    un message lisible et la progression (Mo écrits dans le dossier cible) sont
    publiés sous verrou pour l'UI — même modèle que ``cuda._Installer``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"
        self._message = ""
        self._target: Optional[Path] = None

    def status(self) -> dict:
        with self._lock:
            state, message, target = self._state, self._message, self._target
        mb = 0
        if target is not None and state == "running":
            # Progression best-effort : taille du dossier cible (huggingface_hub y
            # écrit les fichiers au fil du téléchargement).
            try:
                mb = int(sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6)
            except OSError:
                mb = 0
        return {"state": state, "message": message, "mb": mb}

    def start(
        self,
        size: str,
        dest_root: Path,
        on_success: Optional[Callable[[str, Path], None]] = None,
    ) -> dict:
        """Lance le téléchargement de la taille ``size`` vers ``dest_root``. Non bloquant.

        ``on_success(size, target)`` est appelé depuis le thread de téléchargement
        après vérification du modèle (l'appelant y bascule la config) ; s'il lève,
        l'état passe à ``error`` (le modèle reste sur disque pour un nouvel essai).
        """
        size = model_size_name(size)
        if not is_downloadable(size):
            return {
                "ok": False,
                "error": f"« {size} » n'est pas une taille de modèle téléchargeable "
                "automatiquement. Renseignez transcription.model dans config.yaml.",
            }
        with self._lock:
            if self._state == "running":
                return {"ok": True, "state": "running", "message": self._message}
            target = Path(dest_root) / f"faster-whisper-{size}"
            label = approx_size_label(size)
            self._state = "running"
            self._message = (
                f"Téléchargement du modèle « {size} »"
                + (f" ({label})" if label else "")
                + "… Cela peut prendre plusieurs minutes."
            )
            self._target = target
            message = self._message
        try:
            threading.Thread(
                target=self._run, args=(size, target, on_success),
                daemon=True, name="model-download",
            ).start()
        except RuntimeError:
            logger.exception("Démarrage du thread de téléchargement impossible")
            self._set("error", "Démarrage du téléchargement impossible — réessayez.")
            return {"ok": False, "error": "Démarrage du téléchargement impossible."}
        return {"ok": True, "state": "running", "message": message}

    def _set(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    def _run(
        self, size: str, target: Path, on_success: Optional[Callable[[str, Path], None]]
    ) -> None:
        try:
            from faster_whisper.utils import download_model
        except ImportError:
            self._set("error", "faster-whisper n'est pas installé : téléchargement impossible.")
            return
        # Lève la garde hors-ligne (HF_HUB_OFFLINE…) LE TEMPS de ce téléchargement
        # explicite ; le prochain chargement de modèle la repose selon la config
        # (cf. transcriber._load_locked). Seules les variables posées PAR NOUS sont
        # retirées : un HF_HUB_OFFLINE défini par l'utilisateur reste respecté.
        from .transcriber import _set_offline_env

        logger.info("Téléchargement du modèle « %s » vers %s…", size, target)
        try:
            _set_offline_env(False)
            target.mkdir(parents=True, exist_ok=True)
            download_model(size, output_dir=str(target), local_files_only=False)
            if not (target / "model.bin").is_file():
                raise RuntimeError("model.bin absent après le téléchargement")
            # Métadonnées internes huggingface_hub inutiles à l'inférence.
            shutil.rmtree(target / ".cache", ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 — reformulé en message actionnable
            logger.exception("Téléchargement du modèle « %s » échoué", size)
            self._set(
                "error",
                f"Échec du téléchargement : {exc}. Vérifiez la connexion internet "
                "puis réessayez.",
            )
            # Repose DÉTERMINISTE de la garde hors-ligne : ne pas attendre un prochain
            # load() (si l'utilisateur ne dicte plus, la fenêtre resterait ouverte).
            # L'état n'est plus « running », la repose n'est donc pas différée ; sans
            # effet pervers si la config est en ligne (le prochain load(False) retire
            # les variables posées par nous).
            _set_offline_env(True)
            return
        logger.info("Modèle « %s » téléchargé.", size)
        # État final publié AVANT l'activation : on_success déclenche un reset/load qui
        # consulte status() — s'il voyait encore « running », la repose de la garde
        # hors-ligne serait différée puis jamais rattrapée (chemin rapide de load()).
        # Un échec d'activation repasse ensuite l'état à « error ».
        self._set("done", f"Modèle « {size} » installé.")
        _set_offline_env(True)  # repose déterministe (cf. chemin d'erreur ci-dessus)
        try:
            if on_success is not None:
                on_success(size, target)
        except Exception:  # noqa: BLE001 — le modèle est sur disque, l'activation a échoué
            logger.exception("Activation du modèle téléchargé échouée")
            self._set(
                "error",
                "Modèle téléchargé, mais son activation a échoué — redémarrez "
                "l'application pour le prendre en compte.",
            )


_downloader = _Downloader()


def start_download(
    size: str, dest_root: Path, on_success: Optional[Callable[[str, Path], None]] = None
) -> dict:
    """Lance le téléchargement opt-in du modèle (bannière du dashboard). Non bloquant."""
    return _downloader.start(size, dest_root, on_success)


def status() -> dict:
    """État courant du téléchargement ({state, message, mb}) pour le polling de l'UI."""
    return _downloader.status()
