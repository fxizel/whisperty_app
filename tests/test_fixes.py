"""Tests hors-ligne des correctifs de l'audit 2026-08.

Couvre : lecture tolérante du dictionnaire (cp1252, BOM), corrections à
remplacement vide (round-trip UI), écriture atomique, configio robuste
(indentation réelle, BOM, échappements, validation par re-parse), statut
d'injection, restauration sûre du presse-papiers, historique insensible aux
``OSError`` et garde hors-ligne différée pendant un téléchargement de modèle.

Aucun périphérique, aucune GUI, aucun réseau (cf. skill test-doubles).
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import yaml

# --- racine + doublures (conftest sous pytest ; secours en autonome) ----------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "pystray" not in sys.modules:
    import tests.conftest  # noqa: F401  (installe les doublures GUI/audio)

from test_components import _install_injection_stubs  # noqa: E402


# =============================================================================
# 1) Dictionnaire : encodages tolérés (le critique de l'audit)
# =============================================================================
def test_dictionary_cp1252_ne_bloque_pas(tmp_path: Path) -> None:
    """Un dictionary.txt enregistré en ANSI/cp1252 (accents) doit se charger,
    pas faire échouer le démarrage de l'application."""
    from whisperty.dictionary import load_dictionary

    p = tmp_path / "dictionary.txt"
    p.write_bytes("réseau électrique\nmauvé => corrigé\n".encode("cp1252"))
    hotwords, replacements = load_dictionary(p)
    assert hotwords == ["réseau électrique"]
    assert replacements == {"mauvé": "corrigé"}


def test_dictionary_bom_ignore(tmp_path: Path) -> None:
    """Un BOM UTF-8 ne doit pas polluer le premier hotword (biais Whisper)."""
    from whisperty.dictionary import load_dictionary

    p = tmp_path / "dictionary.txt"
    p.write_text("HTA\n", encoding="utf-8-sig")
    hotwords, _ = load_dictionary(p)
    assert hotwords == ["HTA"]


def test_dictionary_correction_remplacement_vide_roundtrip(tmp_path: Path) -> None:
    """« euh => » (suppression d'un tic) doit survivre à une sauvegarde depuis l'UI."""
    from whisperty.dictionary import load_dictionary, parse_entries, update_dictionary_file

    p = tmp_path / "dictionary.txt"
    p.write_text("# en-tête\neuh =>\nbonjour => Bonjour\n", encoding="utf-8")

    entries = parse_entries(p)
    assert {"kind": "correction", "term": "euh", "replacement": ""} in entries

    # Round-trip UI sans modification : l'entrée vide est préservée.
    update_dictionary_file(p, entries)
    _, replacements = load_dictionary(p)
    assert replacements.get("euh") == ""
    assert replacements.get("bonjour") == "Bonjour"
    # Le commentaire d'origine est préservé (doctrine configio).
    assert p.read_text(encoding="utf-8").startswith("# en-tête")


def test_dictionary_ecriture_atomique_sans_residu(tmp_path: Path) -> None:
    """L'écriture passe par un fichier temporaire remplacé : aucun résidu .tmp."""
    from whisperty.dictionary import update_dictionary_file

    p = tmp_path / "dictionary.txt"
    update_dictionary_file(p, [{"kind": "hotword", "term": "Whisperty", "replacement": ""}])
    assert p.is_file()
    assert list(tmp_path.glob("*.tmp")) == []


# =============================================================================
# 2) configio : indentation réelle, BOM, échappements, validation
# =============================================================================
def test_configio_indentation_4_espaces(tmp_path: Path) -> None:
    """Un config.yaml réindenté à 4 espaces est mis à jour, pas corrompu
    (l'ancien code insérait des clés en doublon → YAML invalide)."""
    from whisperty.configio import update_yaml_file

    p = tmp_path / "config.yaml"
    p.write_text(
        "audio:\n    vad_threshold: 0.01  # seuil\n    device: null\n"
        "conference:\n    distinguish_speakers: true\n    speaker_diarization:\n"
        "        enabled: true\n",
        encoding="utf-8",
    )
    update_yaml_file(p, {
        "audio.vad_threshold": 0.02,
        "conference.speaker_diarization.enabled": False,
    })
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["audio"]["vad_threshold"] == 0.02
    assert data["conference"]["speaker_diarization"]["enabled"] is False


def test_configio_bom_pas_de_section_dupliquee(tmp_path: Path) -> None:
    """Un fichier avec BOM ne doit pas voir sa première section dupliquée."""
    from whisperty.configio import update_yaml_file

    p = tmp_path / "config.yaml"
    p.write_text("audio:\n  device: null\n", encoding="utf-8-sig")
    update_yaml_file(p, {"audio.device": 3})
    text = p.read_text(encoding="utf-8")
    assert text.count("audio:") == 1
    assert yaml.safe_load(text)["audio"]["device"] == 3


def test_configio_echappe_les_retours_ligne(tmp_path: Path) -> None:
    """Une valeur collée avec \\n depuis l'UI doit rester une seule ligne YAML valide."""
    from whisperty.configio import update_yaml_file

    p = tmp_path / "config.yaml"
    p.write_text("ai:\n  model: x\n", encoding="utf-8")
    update_yaml_file(p, {"ai.model": "ligne1\nligne2"})
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["ai"]["model"] == "ligne1\nligne2"


def test_configio_chaine_numerique_reste_chaine(tmp_path: Path) -> None:
    """Une chaîne « 16000 » doit être citée, sinon YAML la relirait comme un entier."""
    from whisperty.configio import format_scalar, update_yaml_file

    assert format_scalar("16000") == '"16000"'
    p = tmp_path / "config.yaml"
    p.write_text("a:\n  b: x\n", encoding="utf-8")
    update_yaml_file(p, {"a.b": "16000"})
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["a"]["b"] == "16000"


def test_configio_validation_annule_sans_toucher_le_fichier(tmp_path: Path) -> None:
    """Si le résultat relu ne porte pas la valeur demandée (clé dupliquée,
    dernier-gagne), l'écriture est ANNULÉE et le fichier reste intact."""
    import pytest

    from whisperty.configio import update_yaml_file

    p = tmp_path / "config.yaml"
    original = "a:\n  k: 1\n  k: 2\n"
    p.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError):
        update_yaml_file(p, {"a.k": 5})
    assert p.read_text(encoding="utf-8") == original


# =============================================================================
# 3) Injector : statut de retour + restauration sûre du presse-papiers
# =============================================================================
def test_injector_renvoie_un_statut() -> None:
    """inject() renvoie True en succès, False en échec (l'app peut notifier)."""
    st = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    inj = TextInjector(OutputConfig(method="paste", restore_clipboard=False))
    assert inj.inject("bonjour") is True
    st["fail"]["copy"] = True
    assert inj.inject("bonjour") is False
    st["fail"]["copy"] = False


def test_injector_ne_restaure_pas_un_presse_papiers_non_texte() -> None:
    """Un presse-papiers non-texte est lu comme "" par pyperclip : le « restaurer »
    écraserait une image/des fichiers copiés. On ne restaure que du texte réel."""
    st = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    st["clip"]["v"] = ""  # contenu non-texte (image…) vu comme chaîne vide
    inj = TextInjector(OutputConfig(method="paste", restore_clipboard=True, restore_delay=0.0))
    assert inj.inject("dicté") is True
    # Une seule copie (le texte dicté) : pas de « restauration » du vide.
    assert st["copy_calls"] == ["dicté"]


def test_injector_restaure_du_texte_reel() -> None:
    """La restauration d'un texte préexistant reste fonctionnelle."""
    st = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    st["clip"]["v"] = "ANCIEN"
    inj = TextInjector(OutputConfig(method="paste", restore_clipboard=True, restore_delay=0.0))
    assert inj.inject("dicté") is True
    assert st["copy_calls"] == ["dicté", "ANCIEN"]
    assert st["clip"]["v"] == "ANCIEN"


def test_injector_type_saute_les_caracteres_intypables() -> None:
    """La méthode « type » saute un caractère intypable (émoji hors-BMP) au lieu
    de perdre tout le reste du texte."""
    st = _install_injection_stubs()

    def _type_strict(s):
        if ord(s) > 0xFFFF:
            raise ValueError("caractère hors-BMP")
        st["events"].append(("type", s))

    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    inj = TextInjector(OutputConfig(method="type", type_delay=0.0))
    controller = inj._controller()
    controller.type = _type_strict
    assert inj.inject("a\U0001f600b") is True
    typed = [c for kind, c in st["events"] if kind == "type"]
    assert typed == ["a", "b"]


# =============================================================================
# 4) Historique : jamais fatal, même sur OSError (dossier inaccessible)
# =============================================================================
def test_history_survit_a_oserror(tmp_path: Path) -> None:
    """Un chemin de base impossible (parent = fichier) ne doit ni faire lever
    add() ni bloquer la fin de session (retour IDLE garanti par l'appelant)."""
    from whisperty.history import History

    blocker = tmp_path / "fichier"
    blocker.write_text("x", encoding="utf-8")
    hist = History(path=blocker / "whisperty.db", max_entries=10, enabled=True)
    hist.add("texte")           # ne doit pas lever (OSError du mkdir capturé)
    assert hist.recent() == []  # lecture dégradée, pas d'exception
    hist.close()


# =============================================================================
# 5) Garde hors-ligne différée pendant un téléchargement de modèle
# =============================================================================
def test_offline_env_differee_pendant_telechargement() -> None:
    """_set_offline_env(True) ne doit PAS reposer HF_HUB_OFFLINE pendant qu'un
    téléchargement modeldl est en cours (il échouerait en plein vol)."""
    from whisperty import transcriber

    saved_env = {v: os.environ.pop(v, None) for v in transcriber._OFFLINE_ENV_VARS}
    saved_set = set(transcriber._offline_env_set)
    transcriber._offline_env_set.clear()
    key = "whisperty.modeldl"
    previous = sys.modules.get(key)
    fake = types.ModuleType(key)
    state = {"state": "running"}
    fake.status = lambda: dict(state)
    sys.modules[key] = fake
    try:
        transcriber._set_offline_env(True)
        assert "HF_HUB_OFFLINE" not in os.environ  # différée : téléchargement en cours

        state["state"] = "done"
        transcriber._set_offline_env(True)
        assert os.environ.get("HF_HUB_OFFLINE") == "1"  # reposée après le téléchargement
    finally:
        if previous is not None:
            sys.modules[key] = previous
        else:
            sys.modules.pop(key, None)
        transcriber._set_offline_env(False)  # retire ce que le test a posé
        transcriber._offline_env_set.clear()
        transcriber._offline_env_set.update(saved_set)
        for var, value in saved_env.items():
            if value is not None:
                os.environ[var] = value
