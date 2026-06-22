"""Tests hors-ligne de l'interface fenêtre (V2) : écriture chirurgicale de
``config.yaml`` (``configio``), suppression d'historique, pont ``GuiApi`` et
application des réglages (``WhispertyApp.apply_config_from_gui``).

Aucune fenêtre/WebView2 n'est lancée : on teste la logique du pont et la
persistance. Sous-systèmes lourds remplacés par des doublures (cf. ``conftest``).
100 % local, aucun accès réseau.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "sounddevice" not in sys.modules:  # secours hors pytest
    import tests.conftest  # noqa: F401

from whisperty.configio import format_scalar, update_yaml_file

_SAMPLE = """\
audio:
  device: null            # micro par défaut
  vad_threshold: 0.01     # seuil RMS
  silence_duration: 1.5

transcription:
  model: medium           # base | small | medium | large-v3
  language: fr
  local_files_only: true  # 100 % hors-ligne

hotkey:
  combo: "<ctrl>+<alt>+<space>"

meeting:
  reply_prompt: >-
    Tu rédiges une réponse pour {user_name}.
    Reste concis.
"""


# =============================================================================
# 1) configio : préservation des commentaires + mise à jour des valeurs
# =============================================================================
def test_configio_preserves_comments(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(_SAMPLE, encoding="utf-8")
    n_comments = sum(1 for ln in _SAMPLE.splitlines() if ln.strip().startswith("#"))

    update_yaml_file(p, {
        "audio.vad_threshold": 0.02,
        "transcription.model": "large-v3",
        "transcription.local_files_only": False,
        "transcription.language": None,
        "hotkey.combo": "<alt>+<shift>+d",
    })
    after = p.read_text(encoding="utf-8")
    d = yaml.safe_load(after)

    assert abs(d["audio"]["vad_threshold"] - 0.02) < 1e-9
    assert d["transcription"]["model"] == "large-v3"
    assert d["transcription"]["local_files_only"] is False
    assert d["transcription"]["language"] is None
    assert d["hotkey"]["combo"] == "<alt>+<shift>+d"
    # Commentaires préservés (nombre inchangé) + commentaire inline conservé.
    assert sum(1 for ln in after.splitlines() if ln.strip().startswith("#")) == n_comments
    vad_line = [ln for ln in after.splitlines() if "vad_threshold" in ln][0]
    assert "# seuil RMS" in vad_line
    # Bloc multi-lignes (reply_prompt) intact.
    assert "Tu rédiges une réponse pour {user_name}." in after
    assert "Reste concis." in after
    print("[gui 1] configio : commentaires + inline + bloc multi-lignes préservés  OK")


def test_configio_quoting() -> None:
    assert format_scalar(None) == "null"
    assert format_scalar(True) == "true"
    assert format_scalar(False) == "false"
    assert format_scalar(42) == "42"
    assert format_scalar(0.005) == "0.005"
    assert format_scalar("fr") == "fr"            # simple => sans guillemets
    assert format_scalar("<ctrl>+<alt>+d") == '"<ctrl>+<alt>+d"'  # spéciaux => cités
    assert format_scalar("a # b") == '"a # b"'    # # cité (sinon faux commentaire)
    assert format_scalar("true") == '"true"'      # mot réservé => cité
    assert format_scalar("") == '""'
    print("[gui 2] configio : sérialisation/citation des scalaires  OK")


def test_configio_missing_key_and_section(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(_SAMPLE, encoding="utf-8")
    update_yaml_file(p, {
        "audio.max_duration": 90,        # clé absente de la section audio
        "gui.enabled": False,            # section absente
    })
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert d["audio"]["max_duration"] == 90
    assert d["audio"]["vad_threshold"] == 0.01   # voisins intacts
    assert d["gui"]["enabled"] is False
    print("[gui 3] configio : clé/section manquantes ajoutées sans casse  OK")


def test_configio_multiline_block_scalar(tmp_path: Path) -> None:
    # Mettre à jour une clé à scalaire multi-lignes (>-) ne doit PAS laisser de lignes
    # de continuation orphelines (sinon YAML invalide).
    p = tmp_path / "config.yaml"
    p.write_text(_SAMPLE, encoding="utf-8")
    update_yaml_file(p, {
        "meeting.reply_prompt": "Réponse courte et pro.",
        "transcription.model": "small",  # scalaire normal en même temps
    })
    text = p.read_text(encoding="utf-8")
    d = yaml.safe_load(text)  # ne doit pas lever
    assert d["meeting"]["reply_prompt"] == "Réponse courte et pro."
    assert d["transcription"]["model"] == "small"
    # Les anciennes lignes de continuation du bloc ont disparu.
    assert "Tu rédiges une réponse pour {user_name}." not in text
    assert "Reste concis." not in text
    print("[gui 4b] configio : remplacement d'un scalaire bloc (>-) sans orphelins  OK")


def test_configio_no_cross_section_leak(tmp_path: Path) -> None:
    # « language » n'existe que dans transcription : ne doit PAS toucher une autre section.
    p = tmp_path / "config.yaml"
    p.write_text(_SAMPLE, encoding="utf-8")
    update_yaml_file(p, {"transcription.language": "en"})
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert d["transcription"]["language"] == "en"
    assert "language" not in d["audio"] and "language" not in d["hotkey"]
    print("[gui 4] configio : pas de fuite de clé entre sections  OK")


# =============================================================================
# 2) History.delete
# =============================================================================
def test_history_delete(tmp_path: Path) -> None:
    from whisperty.history import History

    h = History(path=tmp_path / "h.db", max_entries=200, enabled=True)
    h.add("un")
    h.add("deux")
    h.add("trois")
    rows = h.recent(10)
    assert [r.text for r in rows] == ["trois", "deux", "un"]
    mid = [r for r in rows if r.text == "deux"][0].id
    h.delete(mid)
    assert [r.text for r in h.recent(10)] == ["trois", "un"]
    h.delete("pasunid")   # id invalide => no-op silencieux
    h.delete(999999)      # id absent => no-op
    assert len(h.recent(10)) == 2
    h.close()
    h.delete(1)           # après close => no-op (pas de réouverture)
    print("[gui 5] History.delete : suppression ciblée + id invalide/absent/fermé  OK")


# =============================================================================
# 3) GuiApi (pont) + apply_config_from_gui
# =============================================================================
def _make_app(tmp: Path):
    from whisperty.app import WhispertyApp
    from whisperty.config import Config

    cfg = Config()
    cfg.base_dir = tmp
    cfg.history.enabled = True
    cfg.history.path = "h.db"
    cfg.dictionary.enabled = False
    cfg.profiles.enabled = False
    app = WhispertyApp(cfg)
    # Évite chargement modèle réel + démarrage d'un écouteur clavier global.
    app._preload = lambda: None
    app.reload_hotkey = lambda: None
    return app, cfg


def test_gui_api_shapes(tmp_path: Path) -> None:
    from whisperty import __version__
    from whisperty.gui import GuiApi, _fmt_time
    from datetime import datetime

    app, cfg = _make_app(tmp_path)
    app.history.add("Bonjour le monde", source="dictée", model="small")
    app.history.add("Réunion test", source="réunion", model="small")
    api = GuiApi(app)

    gc = api.get_config()
    for k in ("model", "device", "langue", "mic", "mics", "vad", "silence",
              "combo", "injection", "delai", "ia", "iaEndpoint", "iaModel", "localOnly"):
        assert k in gc, k
    assert gc["device"] == "CPU" and gc["langue"] == "fr"
    assert gc["mics"][0]["value"] is None

    db = api.get_dashboard()
    assert set(("lastText", "statsWords", "statsDur", "statsTrans", "combo", "model", "device")) <= set(db)

    h = api.get_history()
    assert h["total"] == 2 and len(h["items"]) == 2
    assert {it["source"] for it in h["items"]} == {"dictée", "réunion"}

    assert api.poll()["state"] == "idle"
    assert api.get_version() == {"version": __version__}
    assert _fmt_time(datetime.now().isoformat(timespec="seconds")).startswith("Aujourd'hui")
    assert _fmt_time("2026-06-19T14:55:00").startswith("19 juin")
    print("[gui 6] GuiApi : get_config/get_dashboard/get_history/poll/_fmt_time  OK")


def test_gui_api_audio_source(tmp_path: Path) -> None:
    """Sélecteur de source loopback : énumération + mémorisation + passage au démarrage."""
    from whisperty.gui import GuiApi
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    api = GuiApi(app)

    # Énumération : au minimum « Sortie par défaut » (value None), best-effort.
    outs = api.list_audio_outputs()
    assert outs and outs[0]["value"] is None
    assert outs[0]["label"] == "Sortie par défaut"

    # set_source : None/"" => défaut ; entier/chaîne numérique => index ; invalide => défaut.
    assert api._source is None
    api.set_source(2)
    assert api._source == 2
    api.set_source("3")
    assert api._source == 3
    api.set_source("")
    assert api._source is None
    api.set_source(1)
    api.set_source(None)
    assert api._source is None
    api.set_source("pas-un-index")
    assert api._source is None

    # toggle_record transmet la source choisie à start_live / start_conference.
    started: dict = {}
    app.start_live = lambda spec=None: started.__setitem__("live", spec)
    app.start_conference = lambda spec=None: started.__setitem__("conf", spec)
    app._state = TrayState.IDLE

    api.set_source(2)
    api._mode = "live"
    api.toggle_record()
    assert started.get("live") == 2

    api._mode = "conference"
    api.toggle_record()
    assert started.get("conf") == 2

    # Source « défaut » → None transmis (start_* retombe sur la config).
    api.set_source("")
    api._mode = "live"
    api.toggle_record()
    assert started.get("live") is None
    print("[gui 9] GuiApi : source audio (list/set_source + passage à start_live/conf)  OK")


def test_apply_config_from_gui(tmp_path: Path) -> None:
    app, cfg = _make_app(tmp_path)
    inj0, llm0 = id(app.injector), id(app.llm)

    res = app.apply_config_from_gui({
        "model": "large-v3", "device": "CUDA", "langue": "auto", "localOnly": False,
        "vad": 33, "silence": 900, "mic": 2, "combo": "<ctrl>+<alt>+x",
        "injection": "frappe", "delai": 60,
        "ia": True, "iaEndpoint": "http://localhost:1234", "iaModel": "qwen2.5:3b",
    })
    assert res == {"ok": True}

    # En mémoire
    assert cfg.transcription.model == "large-v3"
    assert cfg.transcription.device == "cuda"
    assert cfg.transcription.language is None      # auto -> None
    assert cfg.transcription.local_files_only is False
    assert abs(cfg.audio.vad_threshold - 0.033) < 1e-9
    assert abs(cfg.audio.silence_duration - 0.9) < 1e-9
    assert cfg.audio.device == 2
    assert cfg.hotkey.combo == "<ctrl>+<alt>+x"
    assert cfg.output.method == "type"
    assert abs(cfg.output.type_delay - 0.06) < 1e-9
    assert cfg.ai.enabled is True and cfg.ai.endpoint == "http://localhost:1234"
    # Effets à chaud : injecteur + LLM reconstruits, périphérique micro propagé.
    assert id(app.injector) != inj0 and id(app.llm) != llm0
    assert app.recorder.device == 2

    # Persisté dans config.yaml
    d = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert d["transcription"]["model"] == "large-v3"
    assert d["transcription"]["device"] == "cuda"
    assert d["transcription"]["language"] is None
    assert d["output"]["method"] == "type"
    assert d["hotkey"]["combo"] == "<ctrl>+<alt>+x"
    print("[gui 7] apply_config_from_gui : mémoire + effets à chaud + config.yaml  OK")


def test_apply_config_invalid_nonblocking(tmp_path: Path) -> None:
    app, cfg = _make_app(tmp_path)
    # Valeur ininterprétable pour un champ numérique => géré, jamais d'exception.
    res = app.apply_config_from_gui({"vad": "pas-un-nombre"})
    assert res.get("ok") is False
    # La config reste cohérente (valeur par défaut conservée).
    assert cfg.audio.vad_threshold == 0.01
    print("[gui 8] apply_config_from_gui : entrée invalide gérée (non bloquant)  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_gui_test_"))
    test_configio_quoting()
    for i, fn in enumerate([
        test_configio_preserves_comments,
        test_configio_multiline_block_scalar,
        test_configio_missing_key_and_section,
        test_configio_no_cross_section_leak,
        test_history_delete,
        test_gui_api_shapes,
        test_gui_api_audio_source,
        test_apply_config_from_gui,
        test_apply_config_invalid_nonblocking,
    ]):
        d = tmp / f"t{i}"
        d.mkdir(parents=True, exist_ok=True)
        fn(d)
    print("\nTOUS LES TESTS GUI PASSENT")


if __name__ == "__main__":
    _run_all()
