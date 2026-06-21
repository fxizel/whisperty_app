"""Tests hors-ligne complémentaires : point d'entrée (``__main__``), branches
d'erreur de ``loopback``, et écouteurs de raccourci / actions menu de ``app``.

Vise les chemins déterministes restants pour une couverture exhaustive sans
matériel ni réseau. Les écouteurs pynput et le module ``soundcard`` sont des
doublures ; ``os.startfile`` est neutralisé pour ne rien lancer.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "pystray" not in sys.modules:
    import tests.conftest  # noqa: F401


# =============================================================================
# 1) Point d'entrée __main__ : chemin de config + main() (run + KeyboardInterrupt)
# =============================================================================
def test_default_config_path() -> None:
    import whisperty.__main__ as m

    had_frozen = hasattr(sys, "frozen")
    saved_frozen = getattr(sys, "frozen", None)
    saved_exe = sys.executable
    try:
        if had_frozen:
            del sys.frozen  # type: ignore[attr-defined]
        # Hors build figé → chemin relatif simple.
        assert m._default_config_path() == "config.yaml"

        # Build PyInstaller (frozen) → config.yaml à côté de l'exécutable.
        sys.frozen = True  # type: ignore[attr-defined]
        sys.executable = str(Path("X:") / "app" / "whisperty.exe")
        got = m._default_config_path()
        assert got.endswith("config.yaml") and "app" in got
    finally:
        if hasattr(sys, "frozen"):
            del sys.frozen  # type: ignore[attr-defined]
        if had_frozen:
            sys.frozen = saved_frozen  # type: ignore[attr-defined]
        sys.executable = saved_exe
    print("[extra 1] __main__._default_config_path : relatif + figé  OK")


def test_main_run_and_interrupt(tmp_path: Path) -> None:
    import whisperty.__main__ as m

    calls = {"setup": 0, "run": 0, "quit": 0}

    class FakeApp:
        def __init__(self, config):
            calls["config"] = config

        def run(self):
            calls["run"] += 1

        def quit(self):
            calls["quit"] += 1

    saved_app, saved_setup = m.WhispertyApp, m.setup_logging
    m.WhispertyApp = FakeApp
    m.setup_logging = lambda cfg: calls.__setitem__("setup", calls["setup"] + 1)
    try:
        rc = m.main(["--config", str(tmp_path / "inexistant.yaml")])
        assert rc == 0 and calls["run"] == 1 and calls["setup"] == 1
        assert calls["quit"] == 0  # run() s'est terminé proprement

        # Ctrl-C pendant run() → quit() appelé, code 0.
        class InterruptApp(FakeApp):
            def run(self):
                raise KeyboardInterrupt

        m.WhispertyApp = InterruptApp
        rc2 = m.main([])
        assert rc2 == 0 and calls["quit"] == 1
    finally:
        m.WhispertyApp, m.setup_logging = saved_app, saved_setup
    print("[extra 2] __main__.main : run normal + KeyboardInterrupt -> quit  OK")


def test_version_module() -> None:
    from whisperty import __version__
    from whisperty.version import version_info, version_tuple

    assert __version__ == "0.1.0"
    assert version_tuple() == (0, 1, 0, 0)
    assert version_info() == "0.1.0.0"
    print("[extra 2b] version : __version__ + version_tuple/info  OK")


def test_main_version_flag(capsys) -> None:
    import whisperty.__main__ as m

    with __import__("pytest").raises(SystemExit) as exc:
        m.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "whisperty 0.1.0" in out
    print("[extra 2c] __main__ --version  OK")


# =============================================================================
# 2) loopback : branches d'erreur (soundcard absent, garde bool, défaut, repli mic)
# =============================================================================
def _install_fake_soundcard(speakers, *, default=None, mics=None, get_mic=None):
    fake = types.ModuleType("soundcard")
    fake.all_speakers = lambda: speakers
    fake.default_speaker = default or (lambda: speakers[0] if speakers else None)
    fake.all_microphones = lambda include_loopback=False: mics or []
    fake.get_microphone = get_mic or (lambda dev_id, include_loopback=False: None)
    previous = sys.modules.get("soundcard")
    sys.modules["soundcard"] = fake
    return previous


def _restore_soundcard(previous) -> None:
    if previous is not None:
        sys.modules["soundcard"] = previous
    else:
        sys.modules.pop("soundcard", None)


class _Spk:
    def __init__(self, name, ident):
        self.name = name
        self.id = ident


class _Mic:
    def __init__(self, ident, isloopback=True):
        self.id = ident
        self.isloopback = isloopback


def test_soundcard_unavailable() -> None:
    from whisperty import loopback

    previous = sys.modules.get("soundcard")
    sys.modules["soundcard"] = None  # « import soundcard » -> ImportError
    try:
        raised = False
        try:
            loopback._soundcard()
        except loopback.SoundcardUnavailableError:
            raised = True
        assert raised
        # list_speakers reste best-effort : renvoie [] sans lever.
        assert loopback.list_speakers() == []
    finally:
        _restore_soundcard(previous)
    print("[extra 3] loopback : soundcard absent -> erreur claire + list_speakers []  OK")


def test_resolve_loopback_branches() -> None:
    from whisperty import loopback

    spk = _Spk("Speakers (ASUS)", "id-asus")

    # (a) Garde booléenne : True est un int en Python → refusé explicitement.
    prev = _install_fake_soundcard([spk], get_mic=lambda i, include_loopback=False: _Mic("id-asus"))
    try:
        for bad in (True, False):
            try:
                loopback.resolve_loopback(bad)
                raise AssertionError("LoopbackError attendue pour un booléen")
            except loopback.LoopbackError:
                pass
    finally:
        _restore_soundcard(prev)

    # (b) Aucune sortie par défaut → LoopbackError dédiée.
    def no_default():
        raise RuntimeError("pas de défaut")
    prev = _install_fake_soundcard([spk], default=no_default)
    try:
        try:
            loopback.resolve_loopback(None)
            raise AssertionError("LoopbackError attendue (pas de défaut)")
        except loopback.LoopbackError as exc:
            assert "défaut" in str(exc).lower()
    finally:
        _restore_soundcard(prev)

    # (c) get_microphone lève IndexError → repli sur all_microphones (mic loopback).
    def raising_get_mic(dev_id, include_loopback=False):
        raise IndexError("id inconnu")
    prev = _install_fake_soundcard(
        [spk], get_mic=raising_get_mic, mics=[_Mic("id-asus", isloopback=True)]
    )
    try:
        name, mic = loopback.resolve_loopback(0)
        assert name == "Speakers (ASUS)" and mic.isloopback
    finally:
        _restore_soundcard(prev)

    # (d) Aucun micro loopback nulle part → LoopbackError finale.
    prev = _install_fake_soundcard(
        [spk], get_mic=lambda i, include_loopback=False: None, mics=[]
    )
    try:
        try:
            loopback.resolve_loopback(0)
            raise AssertionError("LoopbackError attendue (pas de loopback)")
        except loopback.LoopbackError:
            pass
    finally:
        _restore_soundcard(prev)
    print("[extra 4] loopback : garde bool + défaut absent + repli mic + échec final  OK")


def test_com_initialized_non_windows() -> None:
    from whisperty import loopback

    saved = loopback.sys
    loopback.sys = types.SimpleNamespace(platform="linux")
    try:
        entered = False
        with loopback.com_initialized():
            entered = True
        assert entered  # no-op hors Windows, sans toucher à COM/ctypes
    finally:
        loopback.sys = saved
    print("[extra 5] loopback : com_initialized no-op hors Windows  OK")


# =============================================================================
# 3) app : écouteurs de raccourci + sélection du listener + actions menu
# =============================================================================
def _make_fake_keyboard():
    class FakeListener:
        def __init__(self, on_press=None, on_release=None):
            self.on_press = on_press
            self.on_release = on_release

        def canonical(self, key):
            return key

    class FakeGlobalHotKeys:
        def __init__(self, mapping):
            self.mapping = mapping

    class HotKey:
        @staticmethod
        def parse(combo):
            if "invalide" in combo:
                raise ValueError("combo invalide")
            return ["K1", "K2"]

    kb = types.SimpleNamespace(
        Listener=FakeListener,
        GlobalHotKeys=FakeGlobalHotKeys,
        HotKey=HotKey,
        Key=types.SimpleNamespace(ctrl="CTRL", ctrl_l="CTRL_L", ctrl_r="CTRL_R"),
        KeyCode=types.SimpleNamespace(from_char=lambda c: f"char:{c}"),
    )
    return kb


def _bare_app(tmp: Path):
    from whisperty.app import WhispertyApp
    from whisperty.config import Config

    cfg = Config()
    cfg.base_dir = tmp
    cfg.history.enabled = False
    cfg.dictionary.enabled = False
    cfg.profiles.enabled = False
    return WhispertyApp(cfg), cfg


def test_push_to_talk_listener(tmp_path: Path) -> None:
    app, _ = _bare_app(tmp_path)
    kb = _make_fake_keyboard()
    started = {"n": 0}
    stopped = {"n": 0}
    app._start_recording = lambda: started.__setitem__("n", started["n"] + 1)
    app._stop_and_process = lambda: stopped.__setitem__("n", stopped["n"] + 1)

    listener = app._push_to_talk_listener(kb, "<ctrl>+<alt>")
    # Combinaison incomplète → pas d'enregistrement.
    listener.on_press("K1")
    assert started["n"] == 0
    # Combinaison complète (K1+K2) → démarrage.
    listener.on_press("K2")
    assert started["n"] == 1
    # Relâchement d'une touche → arrêt.
    listener.on_release("K1")
    assert stopped["n"] == 1
    print("[extra 6] app : push-to-talk press/release -> start/stop  OK")


def test_double_tap_listener(tmp_path: Path) -> None:
    app, _ = _bare_app(tmp_path)
    kb = _make_fake_keyboard()
    toggles = {"n": 0}
    app.toggle = lambda: toggles.__setitem__("n", toggles["n"] + 1)

    listener = app._double_tap_listener(kb, "ctrl")
    # Deux appuis rapprochés (< 0,4 s, exécutés en quelques µs) → un toggle.
    listener.on_press("CTRL")
    listener.on_press("CTRL")
    assert toggles["n"] == 1
    # Touche hors variantes → ignorée.
    listener.on_press("char:z")
    assert toggles["n"] == 1
    print("[extra 7] app : double-tap rapide -> toggle (touche hors variantes ignorée)  OK")


def test_build_listener_selection(tmp_path: Path) -> None:
    app, cfg = _bare_app(tmp_path)
    kb = _make_fake_keyboard()
    saved = sys.modules.get("pynput")
    fake_pynput = types.ModuleType("pynput")
    fake_pynput.keyboard = kb
    sys.modules["pynput"] = fake_pynput
    sys.modules["pynput.keyboard"] = kb
    try:
        # double_tap_key prioritaire → écouteur double-tap (Listener).
        cfg.hotkey.double_tap_key = "ctrl"
        assert isinstance(app._build_listener(), kb.Listener)

        # push_to_talk → écouteur maintenu (Listener).
        cfg.hotkey.double_tap_key = None
        cfg.hotkey.mode = "push_to_talk"
        assert isinstance(app._build_listener(), kb.Listener)

        # toggle → GlobalHotKeys.
        cfg.hotkey.mode = "toggle"
        cfg.hotkey.combo = "<ctrl>+<alt>+<space>"
        assert isinstance(app._build_listener(), kb.GlobalHotKeys)
    finally:
        if saved is not None:
            sys.modules["pynput"] = saved
        else:
            sys.modules.pop("pynput", None)
        sys.modules.pop("pynput.keyboard", None)
    print("[extra 8] app : _build_listener choisit double-tap/PTT/toggle  OK")


def test_menu_actions_handle_errors(tmp_path: Path) -> None:
    import whisperty.app as app_mod

    app, _ = _bare_app(tmp_path)
    # Neutralise os.startfile : ne lance rien et exerce la branche de repli (log).
    saved_startfile = getattr(app_mod.os, "startfile", None)

    def boom(_):
        raise OSError("startfile indisponible")

    app_mod.os.startfile = boom  # type: ignore[attr-defined]
    try:
        app.open_config()       # ne lève pas (except -> log)
        app.open_history()      # ne lève pas (except -> log)
    finally:
        if saved_startfile is not None:
            app_mod.os.startfile = saved_startfile  # type: ignore[attr-defined]
        else:
            delattr(app_mod.os, "startfile")
    print("[extra 9] app : open_config/open_history tolèrent l'échec de startfile  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_extra_test_"))
    test_default_config_path()
    test_main_run_and_interrupt(tmp)
    test_soundcard_unavailable()
    test_resolve_loopback_branches()
    test_com_initialized_non_windows()
    test_push_to_talk_listener(tmp)
    test_double_tap_listener(tmp)
    test_build_listener_selection(tmp)
    test_menu_actions_handle_errors(tmp)
    print("\nTOUS LES TESTS EXTRA PASSENT")


if __name__ == "__main__":
    _run_all()
