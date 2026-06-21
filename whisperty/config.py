"""Whisperty — chargement de la configuration (config.yaml).

Dataclasses typées avec valeurs par défaut sûres : si config.yaml est absent ou
incomplet, l'application démarre quand même avec les défauts. Les chemins relatifs
(dictionnaire, logs) sont résolus par rapport au dossier de config.yaml.
"""
from __future__ import annotations

import logging
from dataclasses import MISSING, dataclass, field, fields
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
class HistoryConfig:
    """Historique local des transcriptions (V2, SQLite)."""

    enabled: bool = True
    path: str = "whisperty.db"
    max_entries: int = 200


@dataclass
class AIConfig:
    """Raffinage optionnel par un LLM **local** (V2). Désactivé par défaut.

    ``endpoint`` doit pointer vers un serveur local compatible OpenAI
    (Ollama, LM Studio, llama.cpp). Tout hôte non-local est refusé (confidentialité).
    """

    enabled: bool = False
    endpoint: str = "http://localhost:11434/v1/chat/completions"
    model: str = "llama3.2"
    prompt: str = (
        "Tu corriges la ponctuation, la casse et les fautes évidentes d'un texte "
        "dicté en français. Ne reformule pas, n'ajoute rien, ne réponds que par le "
        "texte corrigé."
    )
    timeout: float = 30.0


@dataclass
class ProfileDef:
    """Un profil de contexte associé à une ou plusieurs applications."""

    name: str = "profil"
    match: list[str] = field(default_factory=list)        # sous-chaînes du nom de process
    initial_prompt: Optional[str] = None
    language: Optional[str] = None
    dictionary: Optional[str] = None                       # chemin d'un dictionnaire propre
    hotwords: list[str] = field(default_factory=list)      # termes favorisés inline
    corrections: dict[str, str] = field(default_factory=dict)  # corrections inline


@dataclass
class ProfilesConfig:
    """Profils de contexte par application active (V2). Désactivés par défaut."""

    enabled: bool = False
    definitions: list[ProfileDef] = field(default_factory=list)


@dataclass
class LiveConfig:
    """Transcription live d'une sortie audio (loopback WASAPI, V2)."""

    device: Optional[Union[int, str]] = None  # null = sortie par défaut ; index ou nom sinon
    block_duration: float = 0.5               # taille des blocs de capture (s)
    max_segment: float = 20.0                 # durée max d'un segment avant transcription forcée
    silence_duration: float = 0.8             # silence (s) marquant la fin d'un segment
    vad_threshold: float = 0.008              # seuil RMS de détection de parole
    transcript_dir: str = "transcriptions"    # dossier des transcripts live


@dataclass
class ConferenceConfig:
    """Mode réunion : capture micro + sortie système simultanés (V2)."""

    enabled: bool = True
    system_device: Optional[Union[int, str]] = None  # sortie à capturer (null = défaut)
    mic_device: Optional[Union[int, str]] = None      # micro (null = défaut)
    block_duration: float = 0.5
    max_segment: float = 20.0
    silence_duration: float = 0.8
    vad_threshold: float = 0.008
    export_dir: str = "transcriptions"                # dossier des transcripts de réunion
    export_format: str = "txt"                         # txt | md
    # Itération 2 : distinguer les locuteurs PAR SOURCE (micro / sortie), sans mixage.
    # True (recommandé, 100 % local) = transcription séparée + entrelacement chronologique
    # « Moi » / « Interlocuteurs ». False = itération 1 (mixage en une transcription).
    distinguish_speakers: bool = True
    mic_label: str = "Moi"
    system_label: str = "Interlocuteurs"


@dataclass
class MeetingConfig:
    """Assistant de réunion : détection de questions + réponses LLM locales (V2).

    Réutilise les paramètres audio de ``live`` (sortie loopback, VAD). Nécessite
    ``ai.enabled: true`` et un serveur LLM local (Ollama, LM Studio…).
    """

    user_name: str = ""           # prénom/nom pour cibler les questions (ex. "Jean")
    user_context: str = ""      # contexte perso (rôle, expertise) pour le LLM
    auto_inject: bool = False     # True = injecter ; False = copier presse-papiers
    context_segments: int = 15    # segments récents gardés comme contexte
    reply_prompt: str = (
        "Tu rédiges une réponse courte et professionnelle en français pour {user_name} "
        "lors d'une réunion.\n"
        "Contexte sur {user_name} : {user_context}\n"
        "Transcription récente de la réunion :\n{context}\n\n"
        "Réponds de manière naturelle et concise (1 à 3 phrases), à la première personne. "
        "Ne commence pas par « Réponse : » ni par des guillemets."
    )


@dataclass
class GuiConfig:
    """Interface fenêtre (WebView2 via pywebview, V2).

    ``enabled: true`` (défaut) ouvre la fenêtre au démarrage (le tray reste actif,
    en compagnon). ``false`` = mode tray seul historique. Si ``pywebview`` ou
    WebView2 est indisponible, repli automatique sur le tray seul.
    """

    enabled: bool = True


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    dictionary: DictionaryConfig = field(default_factory=DictionaryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    profiles: ProfilesConfig = field(default_factory=ProfilesConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    conference: ConferenceConfig = field(default_factory=ConferenceConfig)
    meeting: MeetingConfig = field(default_factory=MeetingConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
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
            history=_build(HistoryConfig, data.get("history")),
            ai=_build(AIConfig, data.get("ai")),
            profiles=_build_profiles(data.get("profiles")),
            live=_build(LiveConfig, data.get("live")),
            conference=_build(ConferenceConfig, data.get("conference")),
            meeting=_build(MeetingConfig, data.get("meeting")),
            gui=_build(GuiConfig, data.get("gui")),
        )
        cfg.base_dir = p.resolve().parent if p.is_file() else Path.cwd()
        return cfg

    def resolve(self, relative: Union[str, Path]) -> Path:
        """Résout un chemin relatif par rapport au dossier de config.yaml."""
        rp = Path(relative)
        return rp if rp.is_absolute() else (self.base_dir / rp)


def _build(dc_type, raw):
    """Construit une dataclass à partir d'un dict YAML, en ignorant les clés inconnues.

    Robustesse (doctrine du module) : une valeur présente mais mal typée ne doit pas
    faire planter l'application. Les champs numériques (défaut int/float) sont coercés
    au mieux ; en cas d'échec, on retombe sur le défaut avec un avertissement.
    """
    if not isinstance(raw, dict) or not raw:
        return dc_type()
    known = {f.name: f for f in fields(dc_type)}
    unknown = set(raw) - set(known)
    if unknown:
        logger.warning(
            "Clés ignorées dans %s : %s", dc_type.__name__, ", ".join(sorted(unknown))
        )
    kwargs = {
        name: _coerce(dc_type, known[name], value)
        for name, value in raw.items()
        if name in known
    }
    return dc_type(**kwargs)


_BOOL_TRUE = {"true", "1", "yes", "on", "oui", "vrai"}
_BOOL_FALSE = {"false", "0", "no", "off", "non", "faux", ""}


def _coerce(dc_type, field_obj, value):
    """Coerce ``value`` vers le type du champ (best-effort, repli sur le défaut)."""
    default = field_obj.default
    if default is MISSING:
        # Pas de défaut scalaire (ex. champ à default_factory) : on ne touche pas.
        return value
    # bool d'abord (bool est une sous-classe d'int) : une chaîne YAML quotée « "false" »
    # serait sinon « truthy ». On interprète explicitement les formes courantes.
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in _BOOL_TRUE:
                return True
            if low in _BOOL_FALSE:
                return False
        elif isinstance(value, (int, float)):
            return bool(value)
        logger.warning(
            "%s.%s : booléen %r non reconnu ; défaut %r utilisé.",
            dc_type.__name__, field_obj.name, value, default,
        )
        return default
    target = None
    if isinstance(default, int):
        target = int
    elif isinstance(default, float):
        target = float
    if target is None or isinstance(value, target):
        return value
    try:
        return target(value)
    except (TypeError, ValueError):
        logger.warning(
            "%s.%s : valeur %r invalide ; défaut %r utilisé.",
            dc_type.__name__, field_obj.name, value, default,
        )
        return default


def _as_str_list(value) -> list[str]:
    """Normalise une valeur YAML en liste de chaînes non vides (robuste aux None/scalaires)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]  # scalaire isolé (ex. int) → ["123"]


def _build_profiles(raw) -> ProfilesConfig:
    """Construit ``ProfilesConfig`` (section avec une liste de profils imbriquée).

    Tolère les YAML mal formés : ``definitions`` non-liste, entrées non-dict, ``match``/
    ``hotwords`` scalaires ou nuls, ``corrections`` non-mapping — sans jamais lever.
    """
    if not isinstance(raw, dict) or not raw:
        return ProfilesConfig()
    defs_raw = raw.get("definitions")
    if defs_raw and not isinstance(defs_raw, list):
        logger.warning(
            "profiles.definitions doit être une liste ; section profils ignorée."
        )
        defs_raw = []
    definitions: list[ProfileDef] = []
    for item in defs_raw or []:
        if not isinstance(item, dict):
            logger.warning("Profil ignoré (entrée non-dictionnaire) : %r", item)
            continue
        definition = _build(ProfileDef, item)
        definition.match = _as_str_list(definition.match)
        definition.hotwords = _as_str_list(definition.hotwords)
        if not isinstance(definition.corrections, dict):
            logger.warning(
                "Profil « %s » : 'corrections' doit être un mapping ; ignoré.",
                definition.name,
            )
            definition.corrections = {}
        definitions.append(definition)
    return ProfilesConfig(enabled=bool(raw.get("enabled", False)), definitions=definitions)
