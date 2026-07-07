"""Hook PostToolUse : lint ruff du fichier Python édité (aligné sur la CI).

Reçoit le JSON du hook sur stdin, extrait ``tool_input.file_path`` et lance
``python -m ruff check`` (python du venv en priorité). Ne lint que ``whisperty/``
et ``tests/`` — même périmètre que le job « lint » de la CI (ci.yml).

Sortie 2 = les erreurs ruff sont renvoyées à Claude pour correction immédiate.
Toute autre situation (fichier hors périmètre, ruff absent, timeout) = sortie 0 :
le hook ne doit jamais bloquer une édition légitime.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # .claude/hooks/ -> racine

# Même périmètre que la CI : ruff check whisperty/ tests/
DOSSIERS_LINTES = ("whisperty", "tests")


def _python() -> str:
    """Python du venv du dépôt si présent (ruff y est installé), sinon celui du hook."""
    venv = REPO / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def main() -> int:
    # stderr en UTF-8 (sinon cp1252 sous Windows -> accents illisibles côté Claude).
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    chemin = (data.get("tool_input") or {}).get("file_path") or ""
    if not chemin.endswith(".py"):
        return 0
    fichier = Path(chemin)
    try:
        rel = fichier.resolve().relative_to(REPO)
    except (ValueError, OSError):
        return 0  # hors dépôt (scratchpad, etc.)
    if not rel.parts or rel.parts[0] not in DOSSIERS_LINTES:
        return 0
    if not fichier.exists():
        return 0
    try:
        proc = subprocess.run(
            [_python(), "-m", "ruff", "check", str(fichier)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=str(REPO),
        )
    except Exception:
        return 0  # ruff indisponible ou trop lent : ne pas bloquer
    if proc.returncode == 0 or "No module named" in (proc.stderr or ""):
        return 0
    sys.stderr.write(proc.stdout or proc.stderr or "ruff a signalé des erreurs.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
