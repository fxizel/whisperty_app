"""Whisperty — transcription locale via faster-whisper (Étape 2).

Charge un modèle Whisper configurable (base/small/medium/large-v3) sur CPU ou
CUDA, transcrit un signal mono 16 kHz float32, puis applique le dictionnaire
personnalisé (mots favorisés + corrections).

Confidentialité : seul le *premier* chargement d'un modèle absent déclenche un
téléchargement Hugging Face. Mettre ``local_files_only: true`` une fois le modèle
présent pour un fonctionnement 100 % hors-ligne.
"""
from __future__ import annotations

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


class ModelNotAvailableError(RuntimeError):
    """faster-whisper absent, modèle non téléchargé ou device indisponible."""


class Transcriber:
    """Encapsule un ``WhisperModel`` faster-whisper + le post-traitement dictionnaire."""

    def __init__(
        self,
        cfg: TranscriptionConfig,
        hotwords: Optional[list[str]] = None,
        replacements: Optional[dict[str, str]] = None,
    ) -> None:
        self.cfg = cfg
        self._hotwords = hotwords or []
        self._replacements = replacements or {}
        self._model = None  # chargement paresseux

    @classmethod
    def from_config(cls, config: Config) -> "Transcriber":
        """Construit le transcripteur, dictionnaire compris, à partir de la config."""
        hotwords: list[str] = []
        replacements: dict[str, str] = {}
        if config.dictionary.enabled:
            hotwords, replacements = load_dictionary(config.resolve(config.dictionary.path))
        return cls(config.transcription, hotwords, replacements)

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

        logger.info(
            "Chargement du modèle Whisper '%s' (%s / %s)…",
            self.cfg.model, self.cfg.device, self.cfg.compute_type,
        )
        try:
            self._model = WhisperModel(
                self.cfg.model,
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
                local_files_only=self.cfg.local_files_only,
            )
        except Exception as exc:  # noqa: BLE001 — on reformule en erreur claire
            raise ModelNotAvailableError(
                f"Impossible de charger le modèle '{self.cfg.model}'. "
                "Causes possibles : modèle non téléchargé en mode hors-ligne "
                "(local_files_only=true), device CUDA indisponible, ou "
                f"compute_type incompatible. Détail : {exc}"
            ) from exc
        logger.info("Modèle Whisper chargé.")

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

    def _run(self, audio, profile: Optional["ResolvedProfile"]) -> str:
        """Cœur commun (signal mémoire ou chemin fichier) : ASR + post-traitement."""
        self.load()
        assert self._model is not None

        # Paramètres effectifs : profil de contexte si fourni, sinon défauts de l'instance.
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
