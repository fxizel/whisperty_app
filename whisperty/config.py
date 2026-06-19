"""Whisperty — chargement de la configuration (config.yaml).

Dataclasses typées avec valeurs par défaut sûres : si config.yaml est absent ou
incomplet, l'application démarre quand même avec les défauts. Les chemins relatifs
(dictionnaire, logs) sont résolus par rapport au dossier de config.yaml.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional, Union

import yaml

logger = logging.getLogger(__name__)


@dataclass
class AudioConfig:
    device: Optional[Union[int, str]] = None
    samplerate: int = 16_000
    vad_threshold: float = 0.01
    silence_duration: float = 1.5
    max_duration: float = 60.0


@dataclass
class TranscriptionConfig:
    model: str = "small"
    language: Optional[str] = "fr"
    device: str = "cpu"            # cpu | cuda
    compute_type: str = "int8"     # int8 (CPU) | float16 (CUDA) | int8_float16
    beam_size: int = 5
    # True par défaut = 100 % hors-ligne (aucun trafic réseau). Passer à false
    # UNE fois pour télécharger un modèle absent, puis remettre à true.
    local_files_only: bool = True
    initial_prompt: Optional[str] = (
        "Contexte technique : énergie, réseau de distribution, IT."
    )


@dataclass
class HotkeyConfig:
    mode: str = "toggle"                       # toggle | push_to_talk
    combo: str = "<ctrl>+<alt>+<space>"        # format pynput ; éviter Win+Space
    double_tap_key: Optional[str] = None       # ex: "ctrl" ; None = désactivé


@dataclass
class OutputConfig:
    method: str = "paste"          # paste (Ctrl+V, robuste FR) | type (frappe)
    restore_clipboard: bool = True
    type_delay: float = 0.005


@dataclass
class DictionaryConfig:
    enabled: bool = True
    path: str = "dictionary.txt"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    path: str = "logs/whisperty.log"
    max_history: int = 200


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    dictionary: DictionaryConfig = field(default_factory=DictionaryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    base_dir: Path = field(default_factory=Path.cwd)

    @classmethod
    def load(cls, path: Union[str, Path] = "config.yaml") -> "Config":
        """Charge config.yaml ; retombe sur les défauts si absent ou illisible."""
        p = Path(path)
        data: dict = {}
        if p.is_file():
            try:
                with p.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("Lecture de %s impossible (%s) ; défauts utilisés.", p, exc)
        else:
            logger.info("Aucun %s ; configuration par défaut.", p)

        cfg = cls(
            audio=_build(AudioConfig, data.get("audio")),
            transcription=_build(TranscriptionConfig, data.get("transcription")),
            hotkey=_build(HotkeyConfig, data.get("hotkey")),
            output=_build(OutputConfig, data.get("output")),
            dictionary=_build(DictionaryConfig, data.get("dictionary")),
            logging=_build(LoggingConfig, data.get("logging")),
        )
        cfg.base_dir = p.resolve().parent if p.is_file() else Path.cwd()
        return cfg

    def resolve(self, relative: Union[str, Path]) -> Path:
        """Résout un chemin relatif par rapport au dossier de config.yaml."""
        rp = Path(relative)
        return rp if rp.is_absolute() else (self.base_dir / rp)


def _build(dc_type, raw):
    """Construit une dataclass à partir d'un dict YAML, en ignorant les clés inconnues."""
    if not isinstance(raw, dict) or not raw:
        return dc_type()
    known = {f.name for f in fields(dc_type)}
    unknown = set(raw) - known
    if unknown:
        logger.warning(
            "Clés ignorées dans %s : %s", dc_type.__name__, ", ".join(sorted(unknown))
        )
    return dc_type(**{k: v for k, v in raw.items() if k in known})
