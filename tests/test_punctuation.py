"""Tests des commandes de ponctuation dictées (whisperty/punctuation.py).

Logique pure (regex + normalisation typographique) : aucun périphérique, aucun
réseau, aucune doublure nécessaire.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whisperty.punctuation import apply_commands  # noqa: E402


def test_texte_sans_commande_inchange() -> None:
    """Sans commande, le texte (et la ponctuation devinée par Whisper) est intact."""
    original = "Bonjour, comment ça va ?  Très bien."
    assert apply_commands(original) == original
    assert apply_commands("") == ""


def test_commandes_de_base() -> None:
    assert apply_commands("bonjour à tous point") == "Bonjour à tous."
    assert (
        apply_commands("bonjour à tous point à la ligne merci")
        == "Bonjour à tous.\nMerci"
    )
    assert (
        apply_commands("premier paragraphe point nouveau paragraphe second")
        == "Premier paragraphe.\n\nSecond"
    )
    assert apply_commands("un virgule deux virgule trois") == "Un, deux, trois"


def test_double_ponctuation_la_commande_gagne() -> None:
    """Whisper a déjà deviné un signe : la commande explicite l'emporte."""
    assert apply_commands("Bonjour. virgule comment ça va") == "Bonjour, comment ça va"
    assert apply_commands("D'accord, point") == "D'accord."


def test_ponctuation_double_francaise_espacee() -> None:
    assert apply_commands("ça va point d'interrogation oui point") == "Ça va ? Oui."
    assert apply_commands("attention point d'exclamation") == "Attention !"
    assert apply_commands("il répond deux points non") == "Il répond : non"
    assert apply_commands("l'un point-virgule l'autre") == "L'un ; l'autre"
    assert apply_commands("et cetera points de suspension") == "Et cetera…"


def test_apostrophe_typographique_et_casse() -> None:
    """Whisper peut émettre l'apostrophe typographique et capitaliser la commande."""
    assert apply_commands("vraiment point d’interrogation") == "Vraiment ?"
    assert apply_commands("stop Point") == "Stop."
    assert apply_commands("suite À la ligne fin") == "Suite\nFin"


def test_parentheses_et_guillemets() -> None:
    assert (
        apply_commands("il a dit ouvrez la parenthèse enfin fermez la parenthèse voilà")
        == "Il a dit (enfin) voilà"
    )
    assert (
        apply_commands("elle a répondu ouvrez les guillemets non fermez les guillemets")
        == "Elle a répondu « non »"
    )


def test_frontieres_de_mots() -> None:
    """« points », « pointage » ou « ponctuation » ne déclenchent rien."""
    assert apply_commands("le pointage des points") == "le pointage des points"
    assert apply_commands("la virgule flottante virgule oui") == "La, flottante, oui"


def test_majuscule_apres_fin_de_phrase() -> None:
    out = apply_commands("premier point deuxième point troisième")
    assert out == "Premier. Deuxième. Troisième"
