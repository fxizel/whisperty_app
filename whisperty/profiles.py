"""Whisperty — profils de contexte par application (V2).

Adapte la transcription à l'application qui recevra le texte : un profil peut
surcharger l'``initial_prompt``, la langue, et ajouter des termes favorisés /
corrections (dictionnaire propre) selon le process actif au démarrage de la
dictée (ex. profil « code » dans VS Code, profil « mail » dans Outlook).

L'appariement se fait par sous-chaîne (insensible à la casse) entre les motifs
``match`` du profil et le nom de l'exécutable actif (``Code.exe``…). Le premier
profil correspondant gagne ; sans correspondance, le profil par défaut (config
de base) s'applique.

Confidentialité : aucun accès réseau ; les dictionnaires de profil sont de
simples fichiers locaux.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .dictionary import load_dictionary

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class ResolvedProfile:
    """Paramètres effectifs à appliquer pour une dictée donnée.

    ``initial_prompt`` / ``language`` à ``None`` signifient « hériter du défaut »
    (la config de base du transcripteur). ``hotwords`` / ``replacements`` sont
    déjà fusionnés (base + profil).
    """

    name: str
    initial_prompt: Optional[str] = None
    language: Optional[str] = None
    hotwords: list[str] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)


class ProfileResolver:
    """Associe l'application active à un :class:`ResolvedProfile`."""

    def __init__(self, config: "Config") -> None:
        self.enabled = config.profiles.enabled
        self._definitions = config.profiles.definitions
        self._config = config
        # Dictionnaire de base (toujours appliqué quand un profil est résolu).
        # Chargé une seule fois, et seulement si les profils sont actifs : sinon le
        # transcripteur utilise son propre dictionnaire et on évite une lecture inutile.
        self._base_hotwords: list[str] = []
        self._base_replacements: dict[str, str] = {}
        if self.enabled and config.dictionary.enabled:
            self._base_hotwords, self._base_replacements = load_dictionary(
                config.resolve(config.dictionary.path)
            )
        # Cache des dictionnaires de profil (clé = nom de profil).
        self._cache: dict[str, tuple[list[str], dict[str, str]]] = {}

    def reload_dictionary(self) -> None:
        """Recharge le dictionnaire de base après une édition (UC-19).

        Relit ``dictionary.txt`` (si profils + dictionnaire actifs) et **vide le
        cache** des dictionnaires de profil, qui dérivent tous de cette base.
        """
        self._base_hotwords = []
        self._base_replacements = {}
        if self.enabled and self._config.dictionary.enabled:
            self._base_hotwords, self._base_replacements = load_dictionary(
                self._config.resolve(self._config.dictionary.path)
            )
        self._cache.clear()

    def for_app(self, app_name: Optional[str]) -> Optional[ResolvedProfile]:
        """Résout le profil pour ``app_name``.

        Renvoie ``None`` si les profils sont désactivés (le transcripteur utilise
        alors ses propres défauts). Sinon, renvoie toujours un profil — celui qui
        correspond, ou un profil par défaut basé sur la config de base.
        """
        if not self.enabled:
            return None

        definition = self._match(app_name)
        if definition is None:
            return ResolvedProfile(
                name="(défaut)",
                hotwords=list(self._base_hotwords),
                replacements=dict(self._base_replacements),
            )

        hotwords, replacements = self._profile_dictionary(definition)
        logger.info("Profil de contexte « %s » appliqué (app=%s).", definition.name, app_name)
        return ResolvedProfile(
            name=definition.name,
            initial_prompt=definition.initial_prompt,
            language=definition.language,
            hotwords=hotwords,
            replacements=replacements,
        )

    def _match(self, app_name: Optional[str]):
        """Premier profil dont un motif ``match`` est contenu dans ``app_name``."""
        if not app_name:
            return None
        target = app_name.lower()
        for definition in self._definitions:
            for pattern in definition.match:
                if pattern.lower() in target:
                    return definition
        return None

    def _profile_dictionary(self, definition) -> tuple[list[str], dict[str, str]]:
        """Fusionne le dictionnaire de base avec celui (optionnel) du profil."""
        hotwords = list(self._base_hotwords)
        replacements = dict(self._base_replacements)

        # Termes/corrections inline déclarés directement dans le profil.
        # str(k) : robustesse aux clés YAML non-chaînes (ex. entiers).
        hotwords.extend(definition.hotwords)
        replacements.update({str(k).lower(): v for k, v in definition.corrections.items()})

        # Dictionnaire de profil sur fichier (optionnel), mis en cache.
        if definition.dictionary:
            if definition.name not in self._cache:
                self._cache[definition.name] = load_dictionary(
                    self._config.resolve(definition.dictionary)
                )
            extra_hot, extra_repl = self._cache[definition.name]
            hotwords.extend(extra_hot)
            replacements.update(extra_repl)

        return hotwords, replacements
