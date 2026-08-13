"""Whisperty — écriture chirurgicale de config.yaml (préserve les commentaires).

L'interface (écran Configuration) doit pouvoir **enregistrer** un sous-ensemble de
réglages dans ``config.yaml`` SANS détruire les nombreux commentaires explicatifs ni
réordonner le fichier — ce que ferait ``yaml.safe_dump``. Plutôt que d'ajouter une
dépendance (ruamel.yaml), on édite le fichier ligne par ligne : pour chaque clé
``section.clef`` à mettre à jour, on remplace **uniquement la valeur**, en conservant
l'indentation et l'éventuel commentaire en fin de ligne.

Portée : clés scalaires d'un mapping de second niveau (``audio.vad_threshold``,
``transcription.model``…) et, pour l'UI réunion (UC-18), d'un **troisième** niveau
(``conference.speaker_diarization.enabled``…). Robuste : une clé absente est créée
sous sa section ; une section absente est ajoutée en fin de fichier.

Confidentialité : pur traitement de fichier local, aucun accès réseau.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Mapping, Union

logger = logging.getLogger(__name__)

# Sérialise les read-modify-write concurrents (écran Configuration et fin de
# téléchargement du modèle écrivent tous deux config.yaml). Verrou feuille :
# aucun autre verrou applicatif n'est pris sous lui.
_WRITE_LOCK = threading.Lock()

# Ligne de section de premier niveau : « section: » sans indentation.
_SECTION_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(#.*)?$")
# Ligne « clef: valeur » indentée (mapping de second niveau).
_KEY_RE = re.compile(r"^(\s+)([A-Za-z_][\w-]*):(.*)$")

# Scalaire « simple » pouvant rester sans guillemets en YAML.
_PLAIN_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
# Texte qui, écrit sans guillemets, serait relu comme un NOMBRE (à citer donc).
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d*)?$")
# Mots réservés YAML qu'il faut citer même s'ils paraissent simples.
_RESERVED = {
    "null", "Null", "NULL", "~", "true", "True", "TRUE", "false", "False", "FALSE",
    "yes", "Yes", "no", "No", "on", "On", "off", "Off",
}


def format_scalar(value: object) -> str:
    """Sérialise une valeur Python en scalaire YAML (citation si nécessaire)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # repr évite la notation scientifique pour nos petites valeurs (0.01, 0.005…).
        return repr(value)
    text = str(value)
    if text == "":
        return '""'
    if _PLAIN_RE.match(text) and text not in _RESERVED and not _NUMERIC_RE.match(text):
        return text
    # Chaîne citée : les caractères de contrôle doivent être échappés, sinon un
    # retour à la ligne collé depuis l'UI produirait un fichier invalide.
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def _split_value_comment(rest: str) -> tuple[str, str]:
    """Sépare la portion valeur du commentaire de fin de ligne (« valeur  # note »).

    Le « # » n'est un commentaire que précédé d'un espace et hors d'une chaîne citée.
    Renvoie (texte_avant_commentaire_brut, commentaire_avec_espaces) ; le second est
    vide s'il n'y a pas de commentaire.
    """
    in_single = in_double = False
    for i, ch in enumerate(rest):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and i > 0 and rest[i - 1] in " \t":
            return rest[:i].rstrip("\n"), rest[i:]
    return rest.rstrip("\n"), ""


def _partition_updates(
    updates: Mapping[str, object],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, dict[str, object]]]]:
    """Sépare les clés à 2 niveaux (section.clef) et 3 niveaux (section.sous.clef)."""
    flat: dict[str, dict[str, object]] = {}
    nested: dict[str, dict[str, dict[str, object]]] = {}
    for dotted, value in updates.items():
        parts = dotted.split(".")
        if len(parts) == 2:
            flat.setdefault(parts[0], {})[parts[1]] = value
        elif len(parts) == 3:
            nested.setdefault(parts[0], {}).setdefault(parts[1], {})[parts[2]] = value
        else:
            logger.warning("Clé de config ignorée (attendu section.clef ou section.sous.clef) : %r", dotted)
    return flat, nested


def update_yaml_file(
    path: Union[str, Path],
    updates: Mapping[str, object],
) -> None:
    """Met à jour, en place, des clés ``section.clef`` (et ``section.sous.clef``) de ``path``.

    ``updates`` : dict de clés pointées (ex. ``{"transcription.model": "small"}``,
    ``{"conference.speaker_diarization.enabled": true}``) vers leur nouvelle valeur
    Python. Les commentaires et l'ordre du fichier sont préservés. Crée le fichier
    (avec ses sections) s'il est absent. Le résultat est **validé par re-parse** puis
    écrit **atomiquement** : en cas d'anomalie, ``ValueError`` est levée et le fichier
    reste intact.
    """
    with _WRITE_LOCK:
        _update_yaml_locked(Path(path), updates)


def _update_yaml_locked(p: Path, updates: Mapping[str, object]) -> None:
    """Corps de :func:`update_yaml_file` (appelé sous ``_WRITE_LOCK``)."""
    by_section, nested = _partition_updates(updates)
    if not by_section and not nested:
        return

    lines = p.read_text(encoding="utf-8-sig").splitlines(keepends=True) if p.is_file() else []
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    applied: set[tuple[str, str]] = set()
    applied_nested: set[tuple[str, str, str]] = set()
    current_section: str | None = None
    current_subsection: str | None = None
    # Indentation réelle du 2e niveau de la section courante : fixée par la 1re clé
    # rencontrée (2 espaces en pratique, mais un fichier réindenté reste géré au lieu
    # d'être corrompu par des doublons de clés).
    level2: int | None = None
    out: list[str] = []

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        sec_match = _SECTION_RE.match(line)
        if sec_match:
            current_section = sec_match.group(1)
            current_subsection = None
            level2 = None
            out.append(line)
            i += 1
            continue
        key_match = _KEY_RE.match(line)
        if key_match and current_section:
            indent, key, rest = key_match.groups()
            indent_len = len(indent)
            if level2 is None:
                level2 = indent_len

            # Troisième niveau (ex. conference → speaker_diarization → enabled).
            if indent_len > level2 and current_subsection:
                sec_nested = nested.get(current_section, {}).get(current_subsection, {})
                tri = (current_section, current_subsection, key)
                if key in sec_nested and tri not in applied_nested:
                    value_part, comment = _split_value_comment(rest)
                    new_val = format_scalar(sec_nested[key])
                    comment = (" " + comment.lstrip()) if comment.strip() else ""
                    out.append(f"{indent}{key}: {new_val}{comment}\n")
                    applied_nested.add(tri)
                    i += 1
                    if value_part.strip()[:1] in ("|", ">"):
                        i = _consume_block_body(lines, i, out, indent_len)
                    continue

            # Second niveau (ex. conference → distinguish_speakers).
            if indent_len == level2 and current_section in by_section:
                sec_updates = by_section[current_section]
                if key in sec_updates and (current_section, key) not in applied:
                    value_part, comment = _split_value_comment(rest)
                    new_val = format_scalar(sec_updates[key])
                    comment = (" " + comment.lstrip()) if comment.strip() else ""
                    out.append(f"{indent}{key}: {new_val}{comment}\n")
                    applied.add((current_section, key))
                    i += 1
                    if value_part.strip()[:1] in ("|", ">"):
                        i = _consume_block_body(lines, i, out, indent_len)
                    current_subsection = None
                    continue

            # Entrée dans un sous-mapping (ex. « speaker_diarization: »).
            if indent_len == level2:
                value_part, _ = _split_value_comment(rest)
                current_subsection = key if value_part.strip() == "" else None

        out.append(line)
        i += 1

    # Clés/sections non trouvées : on les ajoute (fallback robuste).
    _append_missing(out, by_section, applied)
    _append_nested_missing(out, nested, applied_nested)

    # Filet de sécurité : re-parse et vérifie AVANT d'écrire (fichier intact sinon),
    # puis écriture atomique (temporaire + os.replace) — jamais de fichier tronqué.
    content = "".join(out)
    _verify_updates(content, by_section, nested, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, p)


def _verify_updates(
    content: str,
    by_section: Mapping[str, Mapping[str, object]],
    nested: Mapping[str, Mapping[str, Mapping[str, object]]],
    p: Path,
) -> None:
    """Vérifie par re-parse que ``content`` porte bien chaque valeur demandée.

    Une mise en forme inattendue du fichier (indentation exotique, clé dupliquée)
    doit se solder par une erreur franche, jamais par un fichier corrompu ou une
    valeur silencieusement ignorée.
    """
    import yaml  # dépendance déjà présente (config.py)

    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Écriture de {p} annulée : le résultat ne serait pas un YAML valide ({exc})"
        ) from exc

    def _check(parts: tuple[str, ...], expected: object) -> None:
        node: object = data
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        actual = node.get(parts[-1]) if isinstance(node, dict) else None
        if actual != expected:
            raise ValueError(
                f"Écriture de {p} annulée : {'.'.join(parts)} vaudrait "
                f"{actual!r} au lieu de {expected!r} (mise en forme inattendue ?)"
            )

    for section, kv in by_section.items():
        for k, v in kv.items():
            _check((section, k), v)
    for section, subs in nested.items():
        for sub, kv in subs.items():
            for k, v in kv.items():
                _check((section, sub, k), v)


def _consume_block_body(lines: list[str], start: int, out: list[str], key_indent: int) -> int:
    """Saute le corps d'un scalaire multi-lignes (lignes plus indentées + lignes vides).

    Renvoie l'index de la première ligne hors-bloc. Les lignes vides **finales** (entre la
    fin du bloc et la clé/section suivante) sont réémises dans ``out`` : on ne supprime pas
    les séparateurs visuels du fichier.
    """
    n = len(lines)
    end = start
    while end < n:
        nxt = lines[end]
        if nxt.strip() == "" or (len(nxt) - len(nxt.lstrip())) > key_indent:
            end += 1
        else:
            break
    # Réémet les lignes vides finales (trailing) du bloc consommé.
    last = end
    while last > start and lines[last - 1].strip() == "":
        last -= 1
    out.extend(lines[last:end])
    return end


def _append_missing(
    out: list[str],
    by_section: Mapping[str, Mapping[str, object]],
    applied: set[tuple[str, str]],
) -> None:
    """Insère les clés non appliquées : sous leur section si elle existe, sinon en fin."""
    # Index des en-têtes de section présents dans la sortie courante.
    section_line: dict[str, int] = {}
    for i, line in enumerate(out):
        m = _SECTION_RE.match(line)
        if m:
            section_line[m.group(1)] = i

    # On insère en partant de la fin pour ne pas invalider les index suivants.
    for section in reversed(list(by_section)):
        missing = {
            k: v for k, v in by_section[section].items() if (section, k) not in applied
        }
        if not missing:
            continue
        if section in section_line:
            # Respecte l'indentation réelle des clés existantes de la section
            # (des sœurs à indentation différente rendraient le YAML invalide).
            indent = _child_indent(out, section_line[section])
            new_lines = [f"{indent}{k}: {format_scalar(v)}\n" for k, v in missing.items()]
            insert_at = section_line[section] + 1
            out[insert_at:insert_at] = new_lines
        else:
            new_lines = [f"  {k}: {format_scalar(v)}\n" for k, v in missing.items()]
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(f"{section}:\n")
            out.extend(new_lines)


def _append_nested_missing(
    out: list[str],
    nested: Mapping[str, Mapping[str, Mapping[str, object]]],
    applied: set[tuple[str, str, str]],
) -> None:
    """Insère les clés imbriquées (3 niveaux) non appliquées sous leur sous-section."""
    for section, subsections in nested.items():
        for subsection, keys in subsections.items():
            missing = {
                k: v
                for k, v in keys.items()
                if (section, subsection, k) not in applied
            }
            if not missing:
                continue
            _insert_nested_keys(out, section, subsection, missing)


def _insert_nested_keys(
    out: list[str],
    section: str,
    subsection: str,
    missing: Mapping[str, object],
) -> None:
    """Ajoute des clés sous ``section.subsection`` (crée section/sous-section si besoin)."""
    section_idx = _find_section(out, section)
    if section_idx is None:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{section}:\n")
        out.append(f"  {subsection}:\n")
        for k, v in missing.items():
            out.append(f"    {k}: {format_scalar(v)}\n")
        return

    sub_idx, sub_indent = _find_subsection(out, section_idx, subsection)
    if sub_idx is None:
        insert_at = _section_insert_at(out, section_idx)
        block = [f"  {subsection}:\n"]
        block.extend(f"    {k}: {format_scalar(v)}\n" for k, v in missing.items())
        out[insert_at:insert_at] = block
        return

    child_indent = sub_indent + 2
    insert_at = _subsection_insert_at(out, sub_idx, sub_indent)
    new_lines = [f"{' ' * child_indent}{k}: {format_scalar(v)}\n" for k, v in missing.items()]
    out[insert_at:insert_at] = new_lines


def _child_indent(out: list[str], section_idx: int) -> str:
    """Indentation des clés directes d'une section existante (défaut : 2 espaces)."""
    i = section_idx + 1
    while i < len(out):
        if _SECTION_RE.match(out[i]):
            break
        m = _KEY_RE.match(out[i])
        if m:
            return m.group(1)
        i += 1
    return "  "


def _find_section(out: list[str], section: str) -> int | None:
    for i, line in enumerate(out):
        m = _SECTION_RE.match(line)
        if m and m.group(1) == section:
            return i
    return None


def _find_subsection(out: list[str], section_idx: int, subsection: str) -> tuple[int | None, int]:
    """Renvoie (index_ligne, indent) du sous-mapping ``subsection:`` sous ``section``."""
    i = section_idx + 1
    level2: int | None = None  # indentation réelle du 2e niveau (cf. _update_yaml_locked)
    while i < len(out):
        line = out[i]
        if _SECTION_RE.match(line):
            break
        m = _KEY_RE.match(line)
        if not m:
            i += 1
            continue
        indent, key, rest = m.groups()
        if level2 is None:
            level2 = len(indent)
        if len(indent) != level2:
            i += 1
            continue
        if key == subsection:
            value_part, _ = _split_value_comment(rest)
            if value_part.strip() == "":
                return i, len(indent)
        i += 1
    return None, 0


def _section_insert_at(out: list[str], section_idx: int) -> int:
    """Index d'insertion à la fin du corps de ``section`` (avant la section suivante)."""
    i = section_idx + 1
    while i < len(out):
        if _SECTION_RE.match(out[i]):
            break
        i += 1
    return i


def _subsection_insert_at(out: list[str], sub_idx: int, sub_indent: int) -> int:
    """Index d'insertion à la fin du corps du sous-mapping (clés plus indentées)."""
    i = sub_idx + 1
    while i < len(out):
        line = out[i]
        if _SECTION_RE.match(line):
            break
        m = _KEY_RE.match(line)
        if m and len(m.group(1)) <= sub_indent:
            break
        i += 1
    return i
