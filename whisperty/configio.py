"""Whisperty — écriture chirurgicale de config.yaml (préserve les commentaires).

L'interface (écran Configuration) doit pouvoir **enregistrer** un sous-ensemble de
réglages dans ``config.yaml`` SANS détruire les nombreux commentaires explicatifs ni
réordonner le fichier — ce que ferait ``yaml.safe_dump``. Plutôt que d'ajouter une
dépendance (ruamel.yaml), on édite le fichier ligne par ligne : pour chaque clé
``section.clef`` à mettre à jour, on remplace **uniquement la valeur**, en conservant
l'indentation et l'éventuel commentaire en fin de ligne.

Portée : clés scalaires d'un mapping de second niveau (``audio.vad_threshold``,
``transcription.model``…), ce qui couvre tout ce que l'UI modifie. Robuste : une clé
absente est créée sous sa section ; une section absente est ajoutée en fin de fichier.

Confidentialité : pur traitement de fichier local, aucun accès réseau.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Mapping, Union

logger = logging.getLogger(__name__)

# Ligne de section de premier niveau : « section: » sans indentation.
_SECTION_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(#.*)?$")
# Ligne « clef: valeur » indentée (mapping de second niveau).
_KEY_RE = re.compile(r"^(\s+)([A-Za-z_][\w-]*):(.*)$")

# Scalaire « simple » pouvant rester sans guillemets en YAML.
_PLAIN_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
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
    if _PLAIN_RE.match(text) and text not in _RESERVED:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
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


def update_yaml_file(
    path: Union[str, Path],
    updates: Mapping[str, object],
) -> None:
    """Met à jour, en place, des clés ``section.clef`` de ``path``.

    ``updates`` : dict de clés pointées (ex. ``{"transcription.model": "small"}``)
    vers leur nouvelle valeur Python. Les commentaires et l'ordre du fichier sont
    préservés. Crée le fichier (avec ses sections) s'il est absent.
    """
    p = Path(path)
    # Regroupe les mises à jour par section pour un traitement par bloc.
    by_section: dict[str, dict[str, object]] = {}
    for dotted, value in updates.items():
        if "." not in dotted:
            logger.warning("Clé de config non pointée ignorée : %r", dotted)
            continue
        section, key = dotted.split(".", 1)
        by_section.setdefault(section, {})[key] = value
    if not by_section:
        return

    lines = p.read_text(encoding="utf-8").splitlines(keepends=True) if p.is_file() else []
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    applied: set[tuple[str, str]] = set()
    current_section: str | None = None
    out: list[str] = []

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        sec_match = _SECTION_RE.match(line)
        if sec_match:
            current_section = sec_match.group(1)
            out.append(line)
            i += 1
            continue
        key_match = _KEY_RE.match(line)
        if key_match and current_section in by_section:
            indent, key, rest = key_match.groups()
            sec_updates = by_section[current_section]
            if key in sec_updates and (current_section, key) not in applied:
                value_part, comment = _split_value_comment(rest)
                new_val = format_scalar(sec_updates[key])
                comment = (" " + comment.lstrip()) if comment.strip() else ""
                out.append(f"{indent}{key}: {new_val}{comment}\n")
                applied.add((current_section, key))
                i += 1
                # Scalaire multi-lignes (bloc « >- » / « | »…) : consommer le corps
                # (lignes plus indentées que la clé) sinon il resterait orphelin et
                # casserait le YAML. On préserve les lignes vides de séparation finales.
                if value_part.strip()[:1] in ("|", ">"):
                    i = _consume_block_body(lines, i, out, len(indent))
                continue
        out.append(line)
        i += 1

    # Clés/sections non trouvées : on les ajoute (fallback robuste).
    _append_missing(out, by_section, applied)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(out), encoding="utf-8")


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
        new_lines = [f"  {k}: {format_scalar(v)}\n" for k, v in missing.items()]
        if section in section_line:
            insert_at = section_line[section] + 1
            out[insert_at:insert_at] = new_lines
        else:
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(f"{section}:\n")
            out.extend(new_lines)
