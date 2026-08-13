"""Whisperty — commandes de ponctuation dictées (opt-in, dictée seulement).

Convertit les commandes PRONONCÉES en ponctuation réelle : « point », « virgule »,
« deux points », « point d'interrogation », « à la ligne », « ouvrez la parenthèse »…
L'utilisateur contrôle ainsi la mise en forme à la voix (saisie mains libres) au
lieu de dépendre de la ponctuation devinée par Whisper.

Périmètre : appliqué à la DICTÉE uniquement (``app._process``), jamais au live, à la
réunion ni à l'import de fichier — dans une réunion, « point » est du contenu, pas
une commande. Opt-in (``punctuation.enabled: false`` par défaut) car des faux
positifs sont possibles (« c'est un bon point » devient « c'est un bon. »).

Pur traitement de texte local, aucun réseau, aucune dépendance.
"""
from __future__ import annotations

import re

# Commandes reconnues → remplacement. ORDRE SIGNIFICATIF : l'alternative regex
# retient la PREMIÈRE qui matche, les formes longues précèdent donc leurs préfixes
# (« point d'interrogation » avant « point »). Apostrophes droite et typographique
# acceptées (Whisper émet les deux).
_RULES: tuple[tuple[str, str], ...] = (
    (r"point d['’]interrogation", "?"),
    (r"point d['’]exclamation", "!"),
    (r"points? de suspension", "…"),
    (r"point[- ]virgule", ";"),
    (r"deux[- ]points", ":"),
    (r"nouveau paragraphe", "\n\n"),
    (r"(?:retour )?à la ligne", "\n"),
    (r"nouvelle ligne", "\n"),
    (r"ouvr(?:ez|e|ir) la parenthèse", "("),
    (r"ferm(?:ez|e|er) la parenthèse", ")"),
    (r"ouvr(?:ez|e|ir) les guillemets", "«"),
    (r"ferm(?:ez|e|er) les guillemets", "»"),
    (r"virgule", ","),
    (r"point", "."),
    (r"tiret", " - "),
)

_REPLACEMENTS = {f"r{i}": repl for i, (_pat, repl) in enumerate(_RULES)}
# Frontières de mots incluant l'apostrophe : « mise au point » matche (faux positif
# assumé, documenté), « pointage » ou « points » non.
_TOKEN_RE = re.compile(
    r"(?<![\w'’])(?:"
    + "|".join(f"(?P<r{i}>{pat})" for i, (pat, _repl) in enumerate(_RULES))
    + r")(?![\w'’])",
    re.IGNORECASE,
)

# Fin de phrase (ou tête de texte/ligne) suivie d'une minuscule à capitaliser.
_CAPITALIZE_RE = re.compile(r"(^|[.!?…]\s+|\n[ \t]*)([a-zà-ÿ])")


def apply_commands(text: str) -> str:
    """Remplace les commandes dictées par leur ponctuation, puis normalise.

    Si AUCUNE commande n'est présente, ``text`` est renvoyé tel quel : la
    ponctuation posée par Whisper sur un texte sans commande n'est jamais retouchée.
    """
    if not text:
        return text
    out = _TOKEN_RE.sub(lambda m: _REPLACEMENTS[m.lastgroup], text)
    if out == text:
        return text
    return _tidy(out)


def _tidy(s: str) -> str:
    """Normalisation typographique APRÈS substitution des commandes.

    Règles (typographie française simplifiée) : la commande explicite l'emporte sur
    la ponctuation devinée par Whisper autour d'elle ; pas d'espace avant ``, . … )``
    mais un avant ``; : ! ?`` ; guillemets français espacés ; majuscule en début de
    phrase.
    """
    # « Bonjour. » + commande « virgule » → « Bonjour, » : sur une suite de signes,
    # le DERNIER (la commande, substituée à droite du signe deviné) gagne.
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"[,.;:!?…][ \t]*([,.;:!?…])", r"\1", s)
    s = re.sub(r"[ \t]+([,.…)])", r"\1", s)          # rien avant , . … )
    s = re.sub(r"[ \t]*([;:!?])", r" \1", s)         # un espace avant ; : ! ?
    s = re.sub(r"([,.;:!?…»)])(?=[\w«(])", r"\1 ", s)  # un espace après, si collé
    s = re.sub(r"[ \t]*«[ \t]*", " « ", s)           # « ouvrant : espacé des deux côtés
    s = re.sub(r"[ \t]*»", " »", s)                  # » fermant : espace intérieur
    s = re.sub(r"[ \t]*\(", " (", s)                 # ( : espace avant…
    s = re.sub(r"\([ \t]+", "(", s)                  # …rien après
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)           # retours à la ligne sans espaces
    s = re.sub(r"[ \t]{2,}", " ", s)                 # espaces multiples
    s = _CAPITALIZE_RE.sub(lambda m: m.group(1) + m.group(2).upper(), s)
    return s.strip()
