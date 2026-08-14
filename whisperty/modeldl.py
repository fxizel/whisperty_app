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
import os
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


class _DownloadState:
    """État partagé d'un téléchargement opt-in, interrogeable par polling (thread-safe).

    Socle commun au modèle Whisper et au modèle d'empreinte vocale (CO-19) :
    ``idle``/``running``/``done``/``error``, message lisible et progression (Mo déjà
    écrits dans la cible). Verrou **feuille** — même modèle que ``cuda._Installer``.
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
            mb = _dir_size_mb(target)
        return {"state": state, "message": message, "mb": mb}

    def _set(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    def running(self) -> bool:
        with self._lock:
            return self._state == "running"


def _dir_size_mb(target: Path) -> int:
    """Progression best-effort : Mo présents dans ``target`` (dossier ou fichier)."""
    try:
        if target.is_dir():
            return int(sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6)
        if target.is_file():
            return int(target.stat().st_size / 1e6)
    except OSError:
        return 0
    return 0


class _Downloader(_DownloadState):
    """Téléchargement asynchrone du modèle Whisper (un seul à la fois)."""

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

    def _run(
        self, size: str, target: Path, on_success: Optional[Callable[[str, Path], None]]
    ) -> None:
        # Lève la garde hors-ligne (HF_HUB_OFFLINE…) LE TEMPS de ce téléchargement
        # explicite ; le prochain chargement de modèle la repose selon la config
        # (cf. transcriber._load_locked). Seules les variables posées PAR NOUS sont
        # retirées : un HF_HUB_OFFLINE défini par l'utilisateur reste respecté.
        # ⚠️ ORDRE : posé AVANT l'import de faster_whisper (qui importe huggingface_hub,
        # lequel FIGE ses constantes — dont la coupure de télémétrie — à l'import).
        from .transcriber import _set_offline_env

        _set_offline_env(False)
        try:
            from faster_whisper.utils import download_model
        except ImportError:
            self._set("error", "faster-whisper n'est pas installé : téléchargement impossible.")
            _set_offline_env(True)      # garde reposée même sur ce chemin précoce
            return
        logger.info("Téléchargement du modèle « %s » vers %s…", size, target)
        try:
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


# -- modèle d'empreinte vocale pour la diarisation ONNX (CO-19) ----------------
# Modèle retenu : WeSpeaker ResNet34-LM (VoxCeleb2), export ONNX OFFICIEL de l'org
# WeSpeaker sur Hugging Face — dépôt PUBLIC (aucune acceptation de conditions, aucun
# jeton), ~25 Mio, empreinte de dimension 256, entrée fbank kaldi 80 canaux
# (cf. diarization.fbank_features). C'est l'embedder qu'utilise pyannote 3.1 : son
# comportement en CLUSTERING de locuteurs est le mieux documenté, et VoxCeleb2 étant
# multilingue il convient au français. Licence CC-BY-4.0 → ATTRIBUTION requise
# (cf. NOTICE.md et docs/). Vérifié à l'usage : sépare 4 voix là où l'empreinte MFCC
# n'en distingue aucune.
EMBEDDING_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
EMBEDDING_FILE = "voxceleb_resnet34_LM.onnx"
# Révision ÉPINGLÉE (commit) : sans elle, c'est la tête de `main` qui serait servie et
# le binaire exécuté ensuite par onnxruntime pourrait changer sans que rien ne le
# détecte. Épingler rend aussi le téléchargement reproductible. Taille attendue =
# contrôle d'intégrité léger (détecte un transfert tronqué).
EMBEDDING_REVISION = "f0c48c298fd835726c27956a5d617bad7115627e"
EMBEDDING_BYTES = 26_530_309
EMBEDDING_MB = 26
# Nom canonique sur le disque (config : conference.speaker_diarization.onnx_model).
EMBEDDING_DEST_NAME = "speaker-embedding.onnx"
# Sous-dossier de travail : le téléchargement y atterrit pour que la progression soit
# mesurable SANS compter le modèle Whisper (plusieurs Go dans le même models/).
_EMBEDDING_TMP_DIR = ".speaker-embedding-download"


def embedding_size_label() -> str:
    """Poids humain du modèle d'empreinte (« ~26 Mo »), pour le bouton de l'UI."""
    return f"~{EMBEDDING_MB} Mo"


class _EmbeddingDownloader(_DownloadState):
    """Téléchargement **opt-in** du modèle d'empreinte vocale (doctrine ``_Downloader``).

    Un seul fichier (~26 Mio) via ``huggingface_hub`` : même contrat que le modèle
    Whisper — jamais silencieux, progression par polling, garde hors-ligne levée le
    temps du téléchargement puis reposée de façon DÉTERMINISTE.
    """

    def start(
        self, dest_root: Path, on_success: Optional[Callable[[Path], None]] = None
    ) -> dict:
        """Lance le téléchargement vers ``dest_root/speaker-embedding.onnx``. Non bloquant."""
        with self._lock:
            if self._state == "running":
                return {"ok": True, "state": "running", "message": self._message}
            work = Path(dest_root) / _EMBEDDING_TMP_DIR
            self._state = "running"
            self._message = (
                f"Téléchargement du modèle de diarisation ({embedding_size_label()})…"
            )
            self._target = work
            message = self._message
        try:
            threading.Thread(
                target=self._run, args=(Path(dest_root), work, on_success),
                daemon=True, name="diar-model-download",
            ).start()
        except RuntimeError:
            logger.exception("Démarrage du téléchargement de diarisation impossible")
            self._set("error", "Démarrage du téléchargement impossible — réessayez.")
            return {"ok": False, "error": "Démarrage du téléchargement impossible."}
        return {"ok": True, "state": "running", "message": message}

    def _run(
        self, dest_root: Path, work: Path, on_success: Optional[Callable[[Path], None]]
    ) -> None:
        # Doctrine identique au modèle Whisper : la garde hors-ligne est levée LE TEMPS
        # de ce téléchargement explicite, puis reposée quel que soit le résultat.
        # ⚠️ ORDRE : l'environnement est posé AVANT d'importer huggingface_hub, qui FIGE
        # ses constantes à l'import (dont HF_HUB_DISABLE_TELEMETRY et le jeton implicite) —
        # importer d'abord laisserait la coupure de télémétrie sans effet si c'était le
        # premier import du processus.
        from .transcriber import _set_offline_env

        _set_offline_env(False)
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            self._set(
                "error",
                "huggingface_hub n'est pas installé : téléchargement impossible.",
            )
            _set_offline_env(True)      # garde reposée même sur ce chemin précoce
            return
        destination = dest_root / EMBEDDING_DEST_NAME
        logger.info(
            "Téléchargement du modèle de diarisation « %s » (%s, révision %s) vers %s…",
            EMBEDDING_FILE, EMBEDDING_REPO, EMBEDDING_REVISION[:12], destination,
        )
        try:
            work.mkdir(parents=True, exist_ok=True)
            fetched = hf_hub_download(
                repo_id=EMBEDDING_REPO,
                filename=EMBEDDING_FILE,
                revision=EMBEDDING_REVISION,   # binaire figé (cf. constantes ci-dessus)
                local_dir=str(work),
                # token=False : le dépôt est PUBLIC, aucun droit n'est requis. Sans ce
                # paramètre, huggingface_hub joindrait un jeton présent sur la machine
                # (HF_TOKEN ou cache) et rendrait le téléchargement nominativement
                # attribuable, sans aucun bénéfice.
                token=False,
            )
            source = Path(fetched)
            if not source.is_file() or source.stat().st_size != EMBEDDING_BYTES:
                got = source.stat().st_size if source.is_file() else 0
                raise RuntimeError(
                    f"fichier inattendu après le téléchargement ({got} o au lieu de "
                    f"{EMBEDDING_BYTES})"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            # os.replace : mise en place ATOMIQUE (jamais de modèle à moitié écrit lu
            # par une session de réunion qui démarrerait au même moment).
            os.replace(source, destination)
        except Exception as exc:  # noqa: BLE001 — reformulé en message actionnable
            logger.exception("Téléchargement du modèle de diarisation échoué")
            # ORDRE IMPOSÉ (comme _Downloader._run) : publier l'état final AVANT de
            # reposer la garde hors-ligne. Tant que l'état vaut « running »,
            # _set_offline_env DIFFÈRE la repose (download_running() → True) et la
            # garde resterait levée indéfiniment.
            self._set(
                "error",
                f"Échec du téléchargement : {exc}. Vérifiez la connexion internet "
                "puis réessayez.",
            )
            shutil.rmtree(work, ignore_errors=True)
            _set_offline_env(True)
            return
        logger.info("Modèle de diarisation installé (%s).", destination)
        shutil.rmtree(work, ignore_errors=True)        # métadonnées huggingface_hub
        # Idem : état final publié d'abord, garde reposée ensuite (repose EFFECTIVE).
        self._set("done", "Modèle de diarisation installé.")
        _set_offline_env(True)
        try:
            if on_success is not None:
                on_success(destination)
        except Exception:  # noqa: BLE001 — le modèle est sur disque, l'activation a échoué
            logger.exception("Activation du modèle de diarisation échouée")
            self._set(
                "error",
                "Modèle téléchargé, mais son activation a échoué — redémarrez "
                "l'application pour le prendre en compte.",
            )


_downloader = _Downloader()
_embedding_downloader = _EmbeddingDownloader()


def start_embedding_download(
    dest_root: Path, on_success: Optional[Callable[[Path], None]] = None
) -> dict:
    """Téléchargement opt-in du modèle de diarisation ONNX (écran Configuration)."""
    return _embedding_downloader.start(dest_root, on_success)


def embedding_status() -> dict:
    """État du téléchargement du modèle de diarisation ({state, message, mb})."""
    return _embedding_downloader.status()


def download_running() -> bool:
    """True si l'UN des téléchargements opt-in est en cours (modèle ou diarisation).

    Consultée par ``transcriber._model_download_running`` pour ne PAS reposer la garde
    hors-ligne sous les pieds d'un téléchargement en vol. Les deux états sont lus
    SÉQUENTIELLEMENT (verrous feuilles jamais imbriqués).
    """
    return _downloader.running() or _embedding_downloader.running()


def start_download(
    size: str, dest_root: Path, on_success: Optional[Callable[[str, Path], None]] = None
) -> dict:
    """Lance le téléchargement opt-in du modèle (bannière du dashboard). Non bloquant."""
    return _downloader.start(size, dest_root, on_success)


def status() -> dict:
    """État courant du téléchargement ({state, message, mb}) pour le polling de l'UI."""
    return _downloader.status()
