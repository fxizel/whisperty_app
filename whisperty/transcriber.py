"""Whisperty — transcription locale via faster-whisper (Étape 2).

Charge un modèle Whisper configurable (base/small/medium/large-v3) sur CPU ou
CUDA, transcrit un signal mono 16 kHz float32, puis applique le dictionnaire
personnalisé (mots favorisés + corrections).

Confidentialité : seul le *premier* chargement d'un modèle absent déclenche un
téléchargement Hugging Face. Mettre ``local_files_only: true`` une fois le modèle
présent pour un fonctionnement 100 % hors-ligne.
"""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np

from .config import Config, TranscriptionConfig
from .dictionary import apply_corrections, load_dictionary

if TYPE_CHECKING:
    from .profiles import ResolvedProfile

logger = logging.getLogger(__name__)

# Indique si les répertoires de DLL CUDA (paquets pip ``nvidia-*-cu12``) ont déjà été
# ajoutés au chemin de recherche Windows — évite ajouts et logs redondants lors d'un
# rechargement de modèle (changement de config à chaud).
_cuda_dll_dirs_added = False


# Flags LoadLibraryEx pour le préchargement par chemin absolu : résout les dépendances
# sœurs (DLL_LOAD_DIR = dossier de la DLL chargée) + les répertoires par défaut/utilisateur
# (DEFAULT_DIRS inclut USER_DIRS, renseignés par os.add_dll_directory ci-dessus).
_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
_LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000
_PRELOAD_WINMODE = _LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | _LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR


def _preload_cuda_dlls(bin_dirs: list[str]) -> None:
    """Précharge en mémoire, par CHEMIN ABSOLU, toutes les DLL des dossiers ``bin_dirs``.

    Pourquoi c'est nécessaire (et pas seulement PATH / ``add_dll_directory``) : CTranslate2
    charge cuBLAS/cuDNN **paresseusement au 1er encodage**, par NOM. Or, entre le chargement
    du modèle et cet encodage, l'initialisation de WebView2/.NET (pythonnet) appelle
    ``SetDefaultDllDirectories`` et **restreint l'ordre de recherche** : ni ``PATH`` ni les
    user-dirs ne sont alors consultés → ``cublas64_12.dll is not found``. En préchargeant les
    DLL par chemin absolu DÈS le chargement du modèle (avant l'init de la fenêtre), elles sont
    déjà résidentes : le ``LoadLibrary("cublas64_12.dll")`` interne de CTranslate2 retrouve le
    module **déjà chargé par son nom de base**, indépendamment de la politique de recherche.

    Deux passes pour tolérer l'ordre des dépendances entre DLL ; échecs individuels ignorés.
    """
    pending = [dll for d in bin_dirs for dll in Path(d).glob("*.dll")]
    for _ in range(2):
        if not pending:
            break
        still: list[Path] = []
        for dll in pending:
            try:
                ctypes.WinDLL(str(dll), winmode=_PRELOAD_WINMODE)
            except OSError:
                still.append(dll)  # dépendance pas encore chargée : retentée à la passe suivante
        if len(still) == len(pending):
            break  # plus aucun progrès : inutile d'insister
        pending = still
    if pending:
        logger.debug("DLL CUDA non préchargées (dépendances ?) : %s", [p.name for p in pending])


def _add_cuda_dll_directories() -> list[str]:
    """Rend trouvables les DLL CUDA des wheels pip ``nvidia-*-cu12`` (cuBLAS, cuDNN…),
    puis renvoie la liste des répertoires ``bin`` concernés.

    En mode CUDA, CTranslate2 (donc faster-whisper) charge ``cublas64_12.dll`` et les
    DLL cuDNN **paresseusement, au premier encodage** — pas au chargement du modèle.
    Or les wheels ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` déposent ces DLL sous
    ``site-packages/nvidia/<lib>/bin``, dossier ABSENT du chemin de recherche par
    défaut. Sans cela, la transcription échoue par :
    ``RuntimeError: Library cublas64_12.dll is not found or cannot be loaded``.

    Trois mécanismes complémentaires (du moins au plus robuste) :
      * ``os.add_dll_directory`` — pour les DLL chargées via ``LoadLibraryEx``/user-dirs ;
      * préfixe de ``PATH`` — pour le ``LoadLibrary`` classique de CTranslate2, tant que
        la politique de recherche par défaut n'a pas été restreinte ;
      * **préchargement par chemin absolu** (``_preload_cuda_dlls``) — décisif : survit à
        la restriction de recherche imposée par l'init WebView2/.NET (voir cette fonction).

    No-op hors Windows et si le paquet namespace ``nvidia`` est absent. Idempotent
    au niveau du processus.
    """
    global _cuda_dll_dirs_added
    if _cuda_dll_dirs_added or os.name != "nt":
        return []
    from . import cuda  # localisation centralisée des wheels nvidia-*-cu12

    added: list[str] = []
    for d in cuda.nvidia_bin_dirs():
        try:
            os.add_dll_directory(d)
        except OSError:  # dossier disparu entre-temps : on ignore
            pass
        added.append(d)
    if not added:
        # Composants pas (encore) installés : NE PAS marquer comme fait — un nouvel essai
        # au prochain chargement permettra de les prendre en compte après une installation
        # opt-in (cf. cuda.start_install) sans redémarrer l'application.
        return []
    _cuda_dll_dirs_added = True
    # Préfixe de PATH (sans doublon) : c'est ce que voit le LoadLibrary de CTranslate2.
    current = os.environ.get("PATH", "")
    existing = current.split(os.pathsep)
    new_parts = [d for d in added if d not in existing]
    if new_parts:
        os.environ["PATH"] = os.pathsep.join(new_parts + ([current] if current else []))
    # Décisif : charge les DLL en mémoire MAINTENANT, avant toute restriction de recherche.
    _preload_cuda_dlls(added)
    logger.debug("DLL CUDA rendues trouvables (préchargées + PATH + add_dll_directory) : %s", added)
    return added


class ModelNotAvailableError(RuntimeError):
    """faster-whisper absent, modèle non téléchargé ou device indisponible."""


class Transcriber:
    """Encapsule un ``WhisperModel`` faster-whisper + le post-traitement dictionnaire."""

    def __init__(
        self,
        cfg: TranscriptionConfig,
        hotwords: Optional[list[str]] = None,
        replacements: Optional[dict[str, str]] = None,
        base_dir: Optional[Path] = None,
    ) -> None:
        self.cfg = cfg
        self._hotwords = hotwords or []
        self._replacements = replacements or {}
        # Dossier de référence (= dossier de config.yaml / de l'exe figé) pour résoudre
        # un modèle bundlé en chemin relatif (cf. _resolve_model_arg). None = pas de
        # résolution (le nom de taille « medium » est passé tel quel).
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._model = None  # chargement paresseux
        # Device réellement utilisé au dernier chargement (peut différer de cfg.device en
        # cas de repli CUDA→CPU). None tant que le modèle n'a pas été chargé.
        self._effective_device: Optional[str] = None

    @property
    def effective_device(self) -> Optional[str]:
        """Device réellement utilisé au dernier chargement (None si pas encore chargé)."""
        return self._effective_device

    @classmethod
    def from_config(cls, config: Config) -> "Transcriber":
        """Construit le transcripteur, dictionnaire compris, à partir de la config."""
        hotwords: list[str] = []
        replacements: dict[str, str] = {}
        if config.dictionary.enabled:
            hotwords, replacements = load_dictionary(config.resolve(config.dictionary.path))
        return cls(config.transcription, hotwords, replacements, base_dir=config.base_dir)

    def _resolve_model_arg(self) -> str:
        """Argument ``model`` effectif passé à ``WhisperModel``.

        Un nom de taille (``base``/``small``/``medium``/``large-v3``) est résolu par
        faster-whisper dans le cache Hugging Face — on le laisse tel quel. En revanche,
        un **modèle bundlé** (chemin contenant un séparateur, p. ex. ``models/faster-
        whisper-medium``) est résolu en **absolu** par rapport au dossier de config / de
        l'exe figé : le CWD n'est pas fiable au démarrage automatique ni en build figé,
        et faster-whisper interpréterait sinon le chemin relatif depuis le CWD.
        """
        model = self.cfg.model
        looks_like_path = isinstance(model, str) and ("/" in model or "\\" in model)
        if looks_like_path and self._base_dir is not None:
            candidate = self._base_dir / model
            if candidate.exists():
                return str(candidate)
        return model

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Charge le modèle en mémoire (idempotent). Peut être long au premier appel."""
        if self._model is not None:
            return
        # Défense en profondeur : en mode hors-ligne, couper tout accès réseau de
        # huggingface_hub AVANT son import (sinon il vérifie la révision en ligne
        # même si le modèle est déjà en cache). Complète local_files_only.
        if self.cfg.local_files_only:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ModelNotAvailableError(
                "faster-whisper n'est pas installé : pip install faster-whisper"
            ) from exc

        # Device/compute EFFECTIFS : repli gracieux sur CPU si CUDA est demandé mais
        # indisponible (cf. _effective_device_compute). On évite ainsi le plantage
        # « cublas64_12.dll is not found » au 1er encodage (DLL chargées paresseusement).
        device, compute_type = self._effective_device_compute()
        self._effective_device = device
        # CUDA réellement retenu : rendre cuBLAS/cuDNN (wheels nvidia-*-cu12) trouvables
        # AVANT le chargement (préchargement par chemin absolu, robuste à WebView2/.NET).
        if device.startswith("cuda"):
            _add_cuda_dll_directories()

        model_arg = self._resolve_model_arg()
        logger.info(
            "Chargement du modèle Whisper '%s' (%s / %s)…",
            model_arg, device, compute_type,
        )
        try:
            self._model = WhisperModel(
                model_arg,
                device=device,
                compute_type=compute_type,
                local_files_only=self.cfg.local_files_only,
            )
        except Exception as exc:  # noqa: BLE001 — on reformule en erreur claire
            raise ModelNotAvailableError(
                f"Impossible de charger le modèle '{model_arg}'. "
                "Causes possibles : modèle non téléchargé en mode hors-ligne "
                "(local_files_only=true), device CUDA indisponible, ou "
                f"compute_type incompatible. Détail : {exc}"
            ) from exc
        logger.info("Modèle Whisper chargé.")

    def _effective_device_compute(self) -> tuple[str, str]:
        """Device et ``compute_type`` RÉELS du chargement, avec repli gracieux CUDA→CPU.

        Le mode CUDA n'est retenu que si un GPU est présent ET les composants cuBLAS/cuDNN
        sont installés ; sinon on charge en CPU ``int8`` (sûr) avec un avertissement clair,
        plutôt que de laisser planter la 1re transcription. Le device CONFIGURÉ
        (``self.cfg.device``) n'est PAS modifié — l'écran Configuration propose l'installation
        du support GPU, et un simple rechargement du modèle réactivera CUDA une fois prêt.
        """
        device = (self.cfg.device or "cpu").strip().lower()
        if not device.startswith("cuda"):
            return "cpu", self.cfg.compute_type
        from . import cuda

        if not cuda.device_available():
            logger.warning("CUDA demandé mais aucun GPU NVIDIA détecté ; repli sur CPU (int8).")
            return "cpu", "int8"
        if not cuda.components_present():
            logger.warning(
                "CUDA demandé mais composants GPU (cuBLAS/cuDNN) absents ; repli sur CPU "
                "(int8). Installez le support GPU depuis l'écran Configuration."
            )
            return "cpu", "int8"
        return device, self.cfg.compute_type

    def transcribe(
        self, audio: np.ndarray, profile: Optional["ResolvedProfile"] = None
    ) -> str:
        """Transcrit un signal mono 16 kHz float32 et renvoie le texte corrigé.

        ``profile`` (optionnel) surcharge l'``initial_prompt``, la langue et le
        dictionnaire pour cette dictée (profils de contexte par application).
        """
        if audio is None or audio.size == 0:
            return ""
        return self._run(audio, profile)

    def transcribe_file(self, path: Union[str, Path]) -> str:
        """Transcrit un fichier audio existant (WAV, MP3, M4A…) et renvoie le texte.

        Le décodage/rééchantillonnage est délégué à faster-whisper (PyAV embarqué) :
        aucune dépendance ni ffmpeg requis. Utilise le dictionnaire de base.
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Fichier audio introuvable : {p}")
        # faster-whisper accepte directement un chemin et décode via PyAV.
        return self._run(str(p), None)

    def transcribe_segments(
        self, audio: np.ndarray, profile: Optional["ResolvedProfile"] = None
    ) -> list[tuple[float, float, str]]:
        """Comme :meth:`transcribe`, mais renvoie les **segments horodatés**.

        Liste de ``(start, end, texte_corrigé)`` en secondes (relatives au début du
        signal fourni). Requis par le mode réunion (itération 2) pour entrelacer
        chronologiquement deux sources. ``transcribe()`` jette ces horodatages.
        """
        if audio is None or audio.size == 0:
            return []
        return self._run_segments(audio, profile)

    def _resolve_params(self, profile: Optional["ResolvedProfile"]):
        """Paramètres effectifs : profil de contexte si fourni, sinon défauts de l'instance."""
        if profile is not None:
            initial_prompt = (
                profile.initial_prompt
                if profile.initial_prompt is not None
                else self.cfg.initial_prompt
            )
            language = profile.language or self.cfg.language
            hotword_list = profile.hotwords
            replacements = profile.replacements
        else:
            initial_prompt = self.cfg.initial_prompt
            language = self.cfg.language
            hotword_list = self._hotwords
            replacements = self._replacements
        hotwords = ", ".join(hotword_list) if hotword_list else None
        return initial_prompt, language, hotwords, replacements

    def _run(self, audio, profile: Optional["ResolvedProfile"]) -> str:
        """Cœur commun (signal mémoire ou chemin fichier) : ASR + post-traitement → texte."""
        self.load()
        assert self._model is not None
        initial_prompt, language, hotwords, replacements = self._resolve_params(profile)
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self.cfg.beam_size,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
            vad_filter=True,  # VAD Silero intégré : ignore les silences
        )
        text = "".join(segment.text for segment in segments).strip()
        text = apply_corrections(text, replacements)
        logger.info(
            "Transcription : %d caractères (langue=%s).",
            len(text), getattr(info, "language", language),
        )
        return text

    def _run_segments(
        self, audio, profile: Optional["ResolvedProfile"]
    ) -> list[tuple[float, float, str]]:
        """Variante horodatée : renvoie les segments (start, end, texte corrigé)."""
        self.load()
        assert self._model is not None
        initial_prompt, language, hotwords, replacements = self._resolve_params(profile)
        segments, _ = self._model.transcribe(
            audio,
            language=language,
            beam_size=self.cfg.beam_size,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
            vad_filter=True,
        )
        out: list[tuple[float, float, str]] = []
        # Corrections appliquées PAR sous-segment (et non sur le texte joint comme _run) :
        # nécessaire pour conserver les timestamps. Limite assumée : une correction
        # multi-mots dont l'expression chevauche deux sous-segments Whisper ne s'applique
        # pas ici (cas rare — Whisper coupe aux frontières de phrase/silence).
        for segment in segments:
            text = apply_corrections(segment.text.strip(), replacements)
            if text:
                out.append((float(segment.start), float(segment.end), text))
        return out
