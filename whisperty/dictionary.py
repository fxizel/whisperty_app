"""Whisperty — dictionnaire personnalisé.

Format de ``dictionary.txt`` :
- ``terme`` (ligne simple)        → mot favorisé (hotwords / initial_prompt) ;
- ``mauvais => correct``          → correction appliquée après transcription ;
- lignes vides et lignes ``#``    → ignorées.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


def load_dictionary(path: Union[str, Path]) -> tuple[list[str], dict[str, str]]:
    """Charge ``(hotwords, replacements)`` depuis le fichier dictionnaire.

    ``replacements`` est indexé par la forme erronée en minuscules.
    """
    hotwords: list[str] = []
    replacements: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        logger.info("Dictionnaire introuvable : %s", p)
        return hotwords, replacements

    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            wrong, _, right = line.partition("=>")
            wrong, right = wrong.strip(), right.strip()
            if wrong:
                replacements[wrong.lower()] = right
        else:
            hotwords.append(line)

    logger.info(
        "Dictionnaire : %d termes favorisés, %d corrections.",
        len(hotwords), len(replacements),
    )
    return hotwords, replacements


def apply_corrections(text: str, replacements: dict[str, str]) -> str:
    """Applique les corriges ``mauvais => correct`` sur des mots entiers (insensible à la casse)."""
    if not replacements or not text:
        return text
    # Tri par longueur décroissante : les expressions multi-mots priment sur les mots seuls.
    keys = sorted(replacements, key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b",
        re.IGNORECASE | re.UNICODE,
    )
    return pattern.sub(lambda m: replacements[m.group(0).lower()], text)
