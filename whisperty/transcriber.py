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

        model_arg = self._resolve_model_arg()
        logger.info(
            "Chargement du modèle Whisper '%s' (%s / %s)…",
            model_arg, self.cfg.device, self.cfg.compute_type,
        )
        try:
            self._model = WhisperModel(
                model_arg,
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
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
