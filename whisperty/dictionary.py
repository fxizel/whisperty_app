"""Whisperty — dictionnaire personnalisé.

Format de ``dictionary.txt`` :
- ``terme`` (ligne simple)        → mot favorisé (hotwords / initial_prompt) ;
- ``mauvais => correct``          → correction appliquée après transcription ;
- lignes vides et lignes ``#``    → ignorées.

Édition depuis l'interface (UC-19) : :func:`parse_entries` liste les entrées et
:func:`update_dictionary_file` réécrit le fichier **en préservant commentaires et ordre**
(même doctrine que ``configio`` pour ``config.yaml``). Pur traitement local, aucun réseau.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, Mapping, Union

logger = logging.getLogger(__name__)

# En-tête déposé quand un dictionnaire est créé de zéro (fichier absent).
_DEFAULT_HEADER = (
    "# Whisperty — dictionnaire personnalisé.\n"
    "#\n"
    "# Format :\n"
    "#   terme              -> mot favorisé par la reconnaissance (hotword / initial_prompt)\n"
    "#   mauvais => correct -> correction appliquée APRÈS transcription (mots entiers, casse ignorée)\n"
    "#   # ...              -> commentaire (ignoré), lignes vides ignorées\n"
)


def _read_text_lenient(p: Path) -> str:
    """Lit un fichier dictionnaire en UTF-8 (BOM toléré), repli cp1252.

    Le dictionnaire est édité à la main : un enregistrement en ANSI/cp1252 (courant
    sous Windows avec des accents) ou un fichier illisible ne doivent JAMAIS faire
    échouer le chargement — et encore moins le démarrage de l'application. Toute
    erreur est journalisée et dégrade en contenu vide.
    """
    try:
        data = p.read_bytes()
    except OSError as exc:
        logger.error("Dictionnaire illisible (%s) : %s", p, exc)
        return ""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.warning(
            "Dictionnaire non UTF-8 (%s) : lecture en cp1252. "
            "Réenregistrez-le en UTF-8 pour éviter cet avertissement.", p,
        )
        return data.decode("cp1252", errors="replace")


def _write_text_atomic(p: Path, content: str) -> None:
    """Écrit ``content`` de façon atomique (fichier temporaire + ``os.replace``).

    Garantit la promesse « échec d'écriture = fichier intact » : un disque plein ou
    une coupure ne laisse jamais un dictionnaire tronqué.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, p)


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

    for raw in _read_text_lenient(p).splitlines():
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
    """Applique les corrections ``mauvais => correct`` sur des mots entiers (insensible à la casse)."""
    if not replacements or not text:
        return text
    # Tri par longueur décroissante : les expressions multi-mots priment sur les mots seuls.
    keys = sorted(replacements, key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b",
        re.IGNORECASE | re.UNICODE,
    )
    return pattern.sub(lambda m: replacements[m.group(0).lower()], text)


# ── Édition assistée (UC-19) ────────────────────────────────────────────────
# L'interface manipule des ENTRÉES structurées ({kind, term, replacement}) ; le
# fichier reste la source de vérité (éditable à la main, cf. CO-08). L'écriture
# préserve les commentaires et l'ordre — comme configio pour config.yaml.


def _line_kind(line: str):
    """Classe une ligne : ``("comment", …)`` / ``("blank", …)`` / ``(kind, term, repl)``.

    ``kind`` vaut ``"hotword"`` ou ``"correction"`` — même découpage que
    :func:`load_dictionary` (le ``=>`` distingue une correction).
    """
    stripped = line.strip()
    if not stripped:
        return ("blank", "", "")
    if stripped.startswith("#"):
        return ("comment", "", "")
    if "=>" in stripped:
        wrong, _, right = stripped.partition("=>")
        return ("correction", wrong.strip(), right.strip())
    return ("hotword", stripped, "")


def _entry_key(kind: str, term: str, replacement: str):
    """Clé d'identité d'une entrée (stable entre deux éditions).

    Une correction est indexée par sa forme erronée **en minuscules** (comme
    :func:`load_dictionary`) ; un hotword par son terme exact (la casse compte,
    ex. ``HTA``). Renvoie ``None`` pour une entrée invalide (à ignorer).
    """
    term = (term or "").strip()
    if kind == "correction":
        # Le remplacement peut être VIDE (« euh => » supprime un tic de langage) :
        # load_dictionary l'accepte, l'édition assistée doit donc le préserver.
        if not term:
            return None
        return ("correction", term.lower())
    if not term:
        return None
    return ("hotword", term)


def _render_entry(kind: str, term: str, replacement: str) -> str:
    """Sérialise une entrée en ligne de dictionnaire (sans le saut de ligne final)."""
    if kind == "correction":
        right = replacement.strip()
        return f"{term.strip()} => {right}" if right else f"{term.strip()} =>"
    return term.strip()


def parse_entries(path: Union[str, Path]) -> list[dict]:
    """Liste les entrées de ``path`` **dans l'ordre du fichier** (pour l'interface).

    Chaque entrée : ``{"kind": "hotword"|"correction", "term": str, "replacement": str}``.
    Les commentaires et lignes vides sont ignorés (mais préservés à l'écriture).
    Fichier absent → liste vide.
    """
    p = Path(path)
    if not p.is_file():
        return []
    entries: list[dict] = []
    for raw in _read_text_lenient(p).splitlines():
        kind, term, repl = _line_kind(raw)
        if kind in ("hotword", "correction") and _entry_key(kind, term, repl) is not None:
            entries.append({"kind": kind, "term": term, "replacement": repl})
    return entries


def _normalize_desired(entries: Iterable[Mapping[str, object]]):
    """(clés ordonnées, map clé→ligne rendue) à partir des entrées voulues.

    Les entrées invalides sont écartées ; les doublons dédupliqués (la dernière
    occurrence l'emporte pour la valeur, la première pour l'ordre).
    """
    order: list[tuple] = []
    rendered: dict[tuple, str] = {}
    for e in entries or []:
        kind = str(e.get("kind") or "").strip()
        term = str(e.get("term") or "")
        repl = str(e.get("replacement") or "")
        if kind not in ("hotword", "correction"):
            # Tolérance : une entrée sans kind explicite mais avec « => » est une correction.
            kind = "correction" if "=>" in term else "hotword"
            if kind == "correction" and not repl:
                term, _, repl = term.partition("=>")
        key = _entry_key(kind, term, repl)
        if key is None:
            continue
        if key not in rendered:
            order.append(key)
        rendered[key] = _render_entry(kind, term, repl)
    return order, rendered


def update_dictionary_file(
    path: Union[str, Path],
    entries: Iterable[Mapping[str, object]],
) -> None:
    """Réécrit ``path`` avec ``entries``, en **préservant commentaires et ordre**.

    Les lignes de commentaire/vides sont conservées telles quelles ; une entrée
    encore présente est réécrite à sa place (valeur éventuellement mise à jour) ;
    une entrée retirée est omise ; les entrées nouvelles sont ajoutées en fin de
    fichier. Doublons et entrées vides sont écartés. Crée le fichier (avec en-tête)
    s'il est absent. Aucun accès réseau.
    """
    p = Path(path)
    order, rendered = _normalize_desired(entries)

    lines = _read_text_lenient(p).splitlines(keepends=True) if p.is_file() else []
    out: list[str] = []
    emitted: set[tuple] = set()

    for line in lines:
        kind, term, repl = _line_kind(line)
        if kind in ("comment", "blank"):
            out.append(line)
            continue
        key = _entry_key(kind, term, repl)
        if key is not None and key in rendered and key not in emitted:
            out.append(rendered[key] + "\n")  # valeur canonique (RHS éventuellement mis à jour)
            emitted.add(key)
        # Entrée supprimée ou doublon : omise (aucune ligne réémise).

    # Entrées nouvelles (jamais rencontrées dans le fichier) : ajoutées à la fin.
    new_keys = [k for k in order if k not in emitted]
    if new_keys:
        if not out:
            out.append(_DEFAULT_HEADER)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        # Séparateur visuel si le fichier se terminait sur une entrée (pas déjà une ligne vide).
        if out and out[-1].strip() != "":
            out.append("\n")
        out.extend(rendered[k] + "\n" for k in new_keys)

    if not out:  # dictionnaire vidé de toute entrée : ne pas laisser un fichier vide sans repère.
        out.append(_DEFAULT_HEADER)

    _write_text_atomic(p, "".join(out))


def ensure_dictionary_file(path: Union[str, Path]) -> Path:
    """Garantit l'existence de ``path`` (crée un fichier à en-tête si absent).

    Utilisé par « Ouvrir le dictionnaire » : ``os.startfile`` échouerait sur un
    chemin inexistant. Renvoie le chemin.
    """
    p = Path(path)
    if not p.is_file():
        _write_text_atomic(p, _DEFAULT_HEADER)
    return p
