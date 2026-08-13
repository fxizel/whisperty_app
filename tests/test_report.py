"""Tests du compte rendu par gabarit (whisperty/report.py).

Fichiers locaux uniquement (tmp_path), aucun réseau, aucune doublure nécessaire.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whisperty.report import _DEFAULT_TEMPLATE, render, write_report  # noqa: E402


def test_render_remplace_les_balises_connues() -> None:
    out = render("A {{date}} B {{resume}} C {{inconnue}}", {"date": "01/01/2026", "resume": "R"})
    assert out == "A 01/01/2026 B R C {{inconnue}}"  # balise inconnue laissée telle quelle


def test_write_report_cree_gabarit_et_compte_rendu(tmp_path: Path) -> None:
    """1er usage : le gabarit d'exemple est créé, le compte rendu est écrit à côté
    du transcript (« <nom>.compte-rendu.md ») avec résumé et transcript rendus."""
    template = tmp_path / "templates" / "compte-rendu.md"
    transcript = tmp_path / "transcriptions" / "reunion_20260813.txt"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("transcript brut", encoding="utf-8")

    out = write_report(
        template,
        source="réunion",
        resume="Décisions : A. Actions : B.",
        transcript="[00:12] Moi : bonjour",
        transcript_path=transcript,
    )
    assert template.is_file()
    assert template.read_text(encoding="utf-8") == _DEFAULT_TEMPLATE
    assert out == transcript.with_name("reunion_20260813.compte-rendu.md")
    content = out.read_text(encoding="utf-8")
    assert "Décisions : A. Actions : B." in content
    assert "[00:12] Moi : bonjour" in content
    assert "{{resume}}" not in content and "{{transcript}}" not in content
    assert list(tmp_path.rglob("*.tmp")) == []  # écritures atomiques sans résidu


def test_write_report_gabarit_personnalise_et_fallback_dir(tmp_path: Path) -> None:
    """Gabarit utilisateur respecté ; sans transcript, sortie dans fallback_dir."""
    template = tmp_path / "mon_gabarit.md"
    template.write_text("== {{source}} ==\n{{resume}}", encoding="utf-8")
    out = write_report(
        template,
        source="live",
        resume="synthèse",
        transcript="peu importe",
        transcript_path=None,
        fallback_dir=tmp_path / "exports",
    )
    assert out is not None and out.parent == tmp_path / "exports"
    assert out.name.startswith("compte-rendu_") and out.suffix == ".md"
    assert out.read_text(encoding="utf-8") == "== live ==\nsynthèse"


def test_write_report_never_fail(tmp_path: Path) -> None:
    """Emplacement de gabarit impossible (parent = fichier) : None, sans exception."""
    blocker = tmp_path / "fichier"
    blocker.write_text("x", encoding="utf-8")
    out = write_report(
        blocker / "gabarit.md",
        source="live",
        resume="r",
        transcript="t",
        transcript_path=None,
        fallback_dir=tmp_path,
    )
    assert out is None
    # Ni transcript ni fallback : refus propre également.
    assert write_report(tmp_path / "g.md", source="live", resume="r", transcript="t") is None
