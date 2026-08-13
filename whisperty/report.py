"""Whisperty — compte rendu de session depuis un gabarit Markdown (opt-in).

À l'arrêt d'une session live/réunion dont le résumé (UC-17, LLM local) a réussi,
un GABARIT ``.md`` éditable est rendu en « compte rendu » à côté du transcript :
métadonnées, synthèse, transcription intégrale. Activé par ``summary.template``
(chemin relatif à config.yaml) ; vide = comportement historique inchangé.

Le gabarit appartient à l'utilisateur : s'il est absent au premier usage, un
exemple commenté est créé à sa place (même doctrine que ``dictionary.txt``).
Balises reconnues : ``{{date}}``, ``{{heure}}``, ``{{source}}``, ``{{resume}}``,
``{{transcript}}``, ``{{fichier}}`` — toute autre balise est laissée telle quelle.

Pur traitement de fichiers local, aucun réseau. Appelé depuis le worker de résumé
(jamais sous verrou) ; never-fail : toute erreur est journalisée et renvoie None.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE = """\
# Compte rendu — {{source}} du {{date}}

> Généré localement par Whisperty à {{heure}} — aucune donnée n'a quitté la machine.
> Gabarit éditable : réorganisez les sections et les balises {{...}} à votre goût.

## Synthèse

{{resume}}

## Transcription intégrale

{{transcript}}
"""


def render(template: str, values: dict[str, str]) -> str:
    """Remplace les balises ``{{clef}}`` connues de ``values`` (les autres restent)."""
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def ensure_template_file(path: Path) -> Path:
    """Garantit l'existence du gabarit (crée l'exemple commenté si absent)."""
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(_DEFAULT_TEMPLATE, encoding="utf-8")
        os.replace(tmp, path)
        logger.info("Gabarit de compte rendu créé : %s", path)
    return path


def write_report(
    template_path: Path,
    *,
    source: str,
    resume: str,
    transcript: str,
    transcript_path: Optional[Path] = None,
    fallback_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Rend le gabarit et écrit le compte rendu à côté du transcript.

    Sortie : ``<transcript>.compte-rendu.md`` (ou ``compte-rendu_<horodatage>.md``
    dans ``fallback_dir`` si la session n'a pas de fichier transcript). Renvoie le
    chemin écrit, ou ``None`` en cas d'échec (journalisé, jamais bloquant — le
    résumé et l'archivage ont déjà eu lieu).
    """
    try:
        template = ensure_template_file(Path(template_path)).read_text(encoding="utf-8-sig")
        now = datetime.now()
        if transcript_path is not None:
            out = Path(transcript_path).with_name(Path(transcript_path).stem + ".compte-rendu.md")
        elif fallback_dir is not None:
            out = Path(fallback_dir) / f"compte-rendu_{now:%Y%m%d_%H%M%S}.md"
        else:
            logger.warning("Compte rendu ignoré : aucun emplacement de sortie.")
            return None
        content = render(template, {
            "date": now.strftime("%d/%m/%Y"),
            "heure": now.strftime("%H:%M"),
            "source": source,
            "resume": (resume or "").strip(),
            "transcript": (transcript or "").strip(),
            "fichier": str(transcript_path) if transcript_path is not None else "",
        })
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, out)
        logger.info("Compte rendu écrit : %s", out)
        return out
    except OSError:
        logger.warning("Écriture du compte rendu échouée.", exc_info=True)
        return None
