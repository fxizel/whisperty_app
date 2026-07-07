"""Hook PostToolUse : garde zéro-réseau (contrainte cardinale de Whisperty).

Après chaque Edit/Write, deux périmètres sont surveillés :

- ``whisperty/web/`` : l'UI ne doit contenir AUCUNE ressource distante
  (CDN, Google Fonts, fetch, WebSocket…). Tout motif réseau non whitelisté
  fait échouer le hook (sortie 2) — le détail est renvoyé à Claude pour
  retrait ou justification explicite auprès de l'utilisateur.
- ``requirements*.txt`` : un rappel est injecté dans le contexte — toute
  nouvelle dépendance doit être vérifiée (aucun appel réseau à l'usage)
  et signalée, jamais introduite silencieusement.

Whitelist : hôtes locaux (localhost, 127.0.0.1, ::1) et espaces de noms
XML/SVG (www.w3.org — identifiants jamais résolus par le navigateur).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # .claude/hooks/ -> racine

# Hôtes tolérés dans une URL (aucun trafic sortant possible/réel).
HOTES_OK = ("localhost", "127.0.0.1", "[::1]", "www.w3.org")

# Motifs réseau interdits dans les assets de l'UI (indépendants d'une URL).
MOTIFS_INTERDITS = (
    "@import", "fetch(", "XMLHttpRequest", "new WebSocket", "sendBeacon",
    'src="//', "src='//", 'href="//', "href='//",
)

URL_RE = re.compile(r"https?://([^/\s\"'<>)]+)")

RAPPEL_REQUIREMENTS = (
    "Rappel (contrainte cardinale Whisperty) : ce fichier de dépendances vient "
    "d'être modifié. Toute nouvelle dépendance doit être vérifiée — aucun appel "
    "réseau à l'usage — et signalée explicitement à l'utilisateur, jamais "
    "introduite silencieusement (cf. CLAUDE.md)."
)


def _hote_suspect(ligne: str) -> bool:
    """Vrai si la ligne contient une URL vers un hôte non whitelisté."""
    for m in URL_RE.finditer(ligne):
        hote = m.group(1).lower()
        if not hote.startswith(HOTES_OK):
            return True
    return False


def main() -> int:
    # stderr en UTF-8 (sinon cp1252 sous Windows -> accents illisibles côté Claude).
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    chemin = (data.get("tool_input") or {}).get("file_path") or ""
    if not chemin:
        return 0
    fichier = Path(chemin)
    try:
        rel = fichier.resolve().relative_to(REPO)
    except (ValueError, OSError):
        return 0  # hors dépôt

    nom = rel.name.lower()
    if nom.startswith("requirements") and nom.endswith(".txt"):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": RAPPEL_REQUIREMENTS,
            }
        }))
        return 0

    parties = tuple(p.lower() for p in rel.parts)
    if parties[:2] != ("whisperty", "web") or not fichier.exists():
        return 0

    problemes: list[str] = []
    texte = fichier.read_text(encoding="utf-8", errors="replace")
    for num, ligne in enumerate(texte.splitlines(), 1):
        motifs = [m for m in MOTIFS_INTERDITS if m in ligne]
        if _hote_suspect(ligne):
            motifs.append("URL distante")
        if motifs:
            problemes.append(f"  ligne {num} ({', '.join(motifs)}) : {ligne.strip()[:160]}")

    if problemes:
        sys.stderr.write(
            f"GARDE ZÉRO-RÉSEAU — {rel} contient des motifs réseau interdits dans l'UI "
            "(contrainte cardinale : aucun asset/CDN/fetch distant dans whisperty/web/, "
            "cf. CLAUDE.md) :\n" + "\n".join(problemes) +
            "\nRetire ces ressources distantes (inline les assets, police système), ou "
            "signale explicitement à l'utilisateur pourquoi elles seraient justifiées."
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
