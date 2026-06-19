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
from typing import Optional

import numpy as np

from .config import Config, TranscriptionConfig
from .dictionary import apply_corrections, load_dictionary

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

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcrit un signal mono 16 kHz float32 et renvoie le texte corrigé."""
        if audio is None or audio.size == 0:
            return ""
        self.load()
        assert self._model is not None

        hotwords = ", ".join(self._hotwords) if self._hotwords else None
        segments, info = self._model.transcribe(
            audio,
            language=self.cfg.language,
            beam_size=self.cfg.beam_size,
            initial_prompt=self.cfg.initial_prompt,
            hotwords=hotwords,
            vad_filter=True,  # VAD Silero intégré : ignore les silences
        )
        text = "".join(segment.text for segment in segments).strip()
        text = apply_corrections(text, self._replacements)
        logger.info(
            "Transcription : %d caractères (langue=%s).",
            len(text), getattr(info, "language", self.cfg.language),
        )
        return text
