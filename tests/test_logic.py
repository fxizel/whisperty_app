"""Tests hors-ligne de la logique pure de Whisperty.

Ne nécessitent ni micro, ni modèle, ni GUI : les dépendances binaires
(sounddevice, pynput, pyperclip, pystray, PIL, faster_whisper) sont remplacées
par des doublures. Lançables tels quels (`python tests/test_logic.py`) ou via
pytest.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from pathlib import Path

# --- racine du dépôt sur le PYTHONPATH ---------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --- doublures des dépendances binaires --------------------------------------
def _install_stubs() -> None:
    if "sounddevice" not in sys.modules:
        sd = types.ModuleType("sounddevice")
        sd.PortAudioError = type("PortAudioError", (Exception,), {})
        sys.modules["sounddevice"] = sd


_install_stubs()


# =============================================================================
# 1) Configuration
# =============================================================================
def test_config_defaults_and_override(tmp_path: Path | None = None) -> None:
    import yaml

    from whisperty.config import Config

    base = tmp_path or Path(__file__).resolve().parent.parent
    # Défauts purs (fichier absent)
    cfg = Config.load(base / "config-inexistant.yaml")
    assert cfg.transcription.model == "small"
    assert cfg.hotkey.combo == "<ctrl>+<alt>+<space>"
    assert cfg.output.method == "paste"

    # Surcharge partielle + clé inconnue ignorée
    work = (tmp_path or Path(__file__).resolve().parent) / "tmp_cfg.yaml"
    work.write_text(
        yaml.safe_dump(
            {
                "transcription": {"model": "base", "device": "cuda", "inconnue": 1},
                "dictionary": {"path": "mots.txt"},
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(work)
    assert cfg.transcription.model == "base"
    assert cfg.transcription.device == "cuda"
    assert cfg.transcription.beam_size == 5  # défaut conservé
    # Résolution de chemin relative au dossier de config.yaml
    assert cfg.resolve("mots.txt") == (work.resolve().parent / "mots.txt")
    assert cfg.base_dir == work.resolve().parent
    work.unlink()
    print("[1] config : défauts + surcharge + clé inconnue + résolution chemin  OK")


# =============================================================================
# 2) Dictionnaire
# =============================================================================
def test_dictionary(tmp_path: Path | None = None) -> None:
    from whisperty.dictionary import apply_corrections, load_dictionary

    base = tmp_path or Path(__file__).resolve().parent
    dic = base / "tmp_dico.txt"
    dic.write_text(
        "# commentaire\nSCADA\nposte de transformation\n\nscada => SCADA\nge erre de => GRD\n",
        encoding="utf-8",
    )
    hotwords, replacements = load_dictionary(dic)
    assert "SCADA" in hotwords and "poste de transformation" in hotwords
    assert replacements["scada"] == "SCADA"
    assert replacements["ge erre de"] == "GRD"

    # Correction insensible à la casse, sur mots entiers
    out = apply_corrections("Le Scada et le ge erre de sont locaux.", replacements)
    assert "SCADA" in out and "GRD" in out
    # Pas de remplacement à l'intérieur d'un mot
    assert apply_corrections("descadar", replacements) == "descadar"
    # Dictionnaire absent → listes vides
    assert load_dictionary(base / "absent.txt") == ([], {})
    dic.unlink()
    print("[2] dictionnaire : chargement + corrections (casse, mots entiers)  OK")


# =============================================================================
# 3) Injection de texte (paste + type)
# =============================================================================
def _install_injection_stubs() -> tuple[list, dict]:
    copy_calls: list = []
    clip: dict = {"v": ""}

    pyperclip = types.ModuleType("pyperclip")
    pyperclip.copy = lambda t: (copy_calls.append(t), clip.__setitem__("v", t))[0]  # noqa: E731
    pyperclip.paste = lambda: clip["v"]
    sys.modules["pyperclip"] = pyperclip

    events: list = []

    class FakeController:
        def press(self, k):
            events.append(("press", k))

        def release(self, k):
            events.append(("release", k))

        def type(self, s):
            events.append(("type", s))

        @contextmanager
        def pressed(self, *keys):
            for k in keys:
                events.append(("press", k))
            try:
                yield
            finally:
                for k in reversed(keys):
                    events.append(("release", k))

    kb = types.ModuleType("pynput.keyboard")
    kb.Controller = FakeController
    kb.Key = types.SimpleNamespace(ctrl="CTRL", alt="ALT", shift="SHIFT")
    pynput = types.ModuleType("pynput")
    pynput.keyboard = kb
    sys.modules["pynput"] = pynput
    sys.modules["pynput.keyboard"] = kb
    return events, {"copy_calls": copy_calls, "clip": clip}


def test_injector_paste() -> None:
    events, state = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    state["clip"]["v"] = "ANCIEN"  # presse-papiers initial
    inj = TextInjector(OutputConfig(method="paste", restore_clipboard=True))
    inj.inject("Bonjour é à ç")

    # Le texte a bien été copié, puis l'ancien contenu restauré.
    assert state["copy_calls"] == ["Bonjour é à ç", "ANCIEN"], state["copy_calls"]
    assert state["clip"]["v"] == "ANCIEN"
    # Ctrl maintenu autour d'un press/release de 'v'
    assert ("press", "CTRL") in events
    assert ("press", "v") in events and ("release", "v") in events
    print("[3a] injector paste : copie + Ctrl+V + restauration presse-papiers  OK")


def test_injector_type() -> None:
    events, _ = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    inj = TextInjector(OutputConfig(method="type", type_delay=0.0))
    inj.inject("abé")
    typed = [e[1] for e in events if e[0] == "type"]
    assert typed == ["a", "b", "é"], typed
    print("[3b] injector type : frappe caractère par caractère (UTF-8)  OK")


# =============================================================================
# 4) Variantes de touches (double-tap) + smoke import de toute la chaîne
# =============================================================================
def _install_gui_stubs() -> None:
    """Doublures pystray + Pillow pour construire WhispertyApp/Tray sans GUI réelle."""
    if "pystray" not in sys.modules:
        pystray = types.ModuleType("pystray")

        class _Icon:
            def __init__(self, *a, **k):
                self.icon = None
                self.title = None

            def run(self):
                pass

            def stop(self):
                pass

        class _Menu:
            SEPARATOR = object()

            def __init__(self, *items):
                self.items = items

        class _MenuItem:
            def __init__(self, *a, **k):
                pass

        pystray.Icon, pystray.Menu, pystray.MenuItem = _Icon, _Menu, _MenuItem
        sys.modules["pystray"] = pystray

    if "PIL" not in sys.modules:
        PIL = types.ModuleType("PIL")
        image = types.ModuleType("PIL.Image")
        image.new = lambda *a, **k: object()
        draw = types.ModuleType("PIL.ImageDraw")

        class _Draw:
            def ellipse(self, *a, **k):
                pass

        draw.Draw = lambda *a, **k: _Draw()
        PIL.Image, PIL.ImageDraw = image, draw
        sys.modules["PIL"] = PIL
        sys.modules["PIL.Image"] = image
        sys.modules["PIL.ImageDraw"] = draw


def test_state_machine() -> None:
    import time

    import numpy as np

    _install_gui_stubs()
    _install_injection_stubs()
    from whisperty.app import WhispertyApp
    from whisperty.config import Config
    from whisperty.tray import TrayState

    cfg = Config()
    cfg.hotkey.mode = "push_to_talk"   # la surveillance n'applique que max_duration
    cfg.audio.max_duration = 9999      # désactive l'arrêt auto pendant le test

    app = WhispertyApp(cfg)

    class FakeRecorder:
        def __init__(self):
            self.started = 0
            self.stopped = 0
            self.current_level = 1.0

        def start(self):
            self.started += 1

        def stop(self):
            self.stopped += 1
            return np.zeros(0, dtype=np.float32)

    class FakeTranscriber:
        def load(self):
            pass

        def transcribe(self, audio):
            return ""

    rec = FakeRecorder()
    app.recorder = rec
    app.transcriber = FakeTranscriber()

    assert app._state is TrayState.IDLE
    # IDLE -> RECORDING
    app.toggle()
    assert app._state is TrayState.RECORDING and rec.started == 1

    # toggle() est IGNORÉ pendant PROCESSING (cœur du correctif #1 de la 1re revue)
    app._state = TrayState.PROCESSING
    app.toggle()
    assert rec.started == 1 and app._state is TrayState.PROCESSING

    # RECORDING -> PROCESSING -> (async) IDLE
    app._state = TrayState.RECORDING
    app.toggle()
    assert rec.stopped >= 1
    for _ in range(40):
        if app._state is TrayState.IDLE:
            break
        time.sleep(0.05)
    assert app._state is TrayState.IDLE, app._state

    # Un second _stop_and_process() est un no-op (état déjà IDLE)
    before = rec.stopped
    app._stop_and_process()
    assert rec.stopped == before
    print("[5] machine à états : IDLE->REC->PROCESSING->IDLE + toggle ignoré en PROCESSING  OK")


def test_key_variants_and_imports() -> None:
    _install_injection_stubs()  # fournit un pynput.keyboard minimal
    import whisperty.app as app_mod  # importe config, recorder, transcriber, injector, tray, app

    fake_kb = types.SimpleNamespace(
        Key=types.SimpleNamespace(ctrl="C", ctrl_l="CL", ctrl_r="CR"),
        KeyCode=types.SimpleNamespace(from_char=lambda c: f"char:{c}"),
    )
    variants = app_mod.WhispertyApp._key_variants(fake_kb, "ctrl")
    assert variants == {"C", "CL", "CR"}, variants
    assert app_mod.WhispertyApp._key_variants(fake_kb, "x") == {"char:x"}
    print("[4] variantes de touches + import complet de la chaîne  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_test_"))
    test_config_defaults_and_override(tmp)
    test_dictionary(tmp)
    test_injector_paste()
    test_injector_type()
    test_key_variants_and_imports()
    test_state_machine()
    print("\nTOUS LES TESTS PASSENT")


if __name__ == "__main__":
    _run_all()
