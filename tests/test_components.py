"""Tests hors-ligne des composants restants : injector (cas limites),
winutil (détection d'app), tray (états + sous-menus) et setup_logging.

Complète ``test_logic.py`` (qui couvre déjà paste/type nominaux). Aucune GUI,
aucun presse-papiers réel, aucun accès réseau.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import types
from contextlib import contextmanager
from pathlib import Path

# --- racine + doublures (conftest sous pytest ; secours en autonome) ----------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "pystray" not in sys.modules:
    import tests.conftest  # noqa: F401  (installe les doublures GUI/audio)


def _install_injection_stubs():
    """Installe des doublures enregistreuses pyperclip + pynput.keyboard."""
    copy_calls: list = []
    clip: dict = {"v": ""}
    fail = {"copy": False}

    def _copy(t):
        if fail["copy"]:
            raise RuntimeError("presse-papiers indisponible")
        copy_calls.append(t)
        clip["v"] = t

    pyperclip = types.ModuleType("pyperclip")
    pyperclip.copy = _copy
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
    return {"events": events, "copy_calls": copy_calls, "clip": clip, "fail": fail}


# =============================================================================
# 1) Injector : cas limites (texte vide, copy_to_clipboard, échecs avalés)
# =============================================================================
def test_injector_edge_cases() -> None:
    st = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    inj = TextInjector(OutputConfig(method="paste"))

    # Texte vide → aucun effet (ni copie ni frappe).
    inj.inject("")
    assert st["copy_calls"] == [] and st["events"] == []

    # copy_to_clipboard : vide → False ; texte → True + copie effective.
    assert inj.copy_to_clipboard("") is False
    assert inj.copy_to_clipboard("bonjour") is True
    assert st["copy_calls"] == ["bonjour"]

    # copy_to_clipboard avale les erreurs presse-papiers et renvoie False.
    st["fail"]["copy"] = True
    assert inj.copy_to_clipboard("x") is False
    st["fail"]["copy"] = False
    print("[comp 1] injector : texte vide + copy_to_clipboard (ok/vide/échec)  OK")


def test_injector_paste_no_restore() -> None:
    st = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    st["clip"]["v"] = "ANCIEN"
    inj = TextInjector(OutputConfig(method="paste", restore_clipboard=False))
    inj.inject("Salut é à")
    # Sans restauration : seul le nouveau texte est copié (l'ancien n'est pas restauré).
    assert st["copy_calls"] == ["Salut é à"]
    assert st["clip"]["v"] == "Salut é à"
    print("[comp 2] injector : paste sans restore_clipboard  OK")


def test_injector_swallows_errors() -> None:
    _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    # Le contrôleur clavier lève : inject() ne doit pas propager (app jamais tuée).
    class BoomController:
        def type(self, s):
            raise RuntimeError("clavier indisponible")

        @contextmanager
        def pressed(self, *keys):
            raise RuntimeError("clavier indisponible")
            yield  # pragma: no cover

    sys.modules["pynput.keyboard"].Controller = BoomController
    inj = TextInjector(OutputConfig(method="type", type_delay=0.0))
    inj.inject("abc")  # ne lève pas
    inj2 = TextInjector(OutputConfig(method="paste"))
    inj2.inject("abc")  # ne lève pas non plus
    print("[comp 3] injector : exceptions d'injection avalées (pas de crash)  OK")


# =============================================================================
# 2) winutil : détection de l'application active
# =============================================================================
def test_foreground_app() -> None:
    from whisperty import winutil

    # Hors Windows → None déterministe (branche multiplateforme).
    saved = winutil.sys
    winutil.sys = types.SimpleNamespace(platform="linux")
    try:
        assert winutil.foreground_app() is None
    finally:
        winutil.sys = saved

    # Sur la plateforme courante : ne lève jamais ; renvoie None ou un nom d'exe (str).
    result = winutil.foreground_app()
    assert result is None or isinstance(result, str)
    print("[comp 4] winutil.foreground_app : None hors Windows + jamais d'exception  OK")


# =============================================================================
# 3) tray : couverture des états + sous-menus (câblage des callbacks)
# =============================================================================
def test_tray_states() -> None:
    from whisperty.tray import _COLORS, _TITLES, Tray, TrayState

    # Tous les états ont une couleur et un titre (pas de KeyError dans set_state).
    for state in TrayState:
        assert state in _COLORS and state in _TITLES

    tray = Tray(on_toggle=lambda: None, on_quit=lambda: None)
    for state in TrayState:
        tray.set_state(state)
        assert tray.state is state
        assert tray._icon.title == _TITLES[state]
        assert tray._icon.icon is tray._images[state]

    # notify() est best-effort : la doublure d'icône n'a pas notify() → avalé sans lever.
    tray.notify("message de test")
    print("[comp 5] tray : 6 états (couleur/titre/icône) + notify best-effort  OK")


def test_tray_capture_submenu() -> None:
    from whisperty.tray import Tray

    class FakeMenuItem:
        def __init__(self, text=None, action=None, **kw):
            self.text = text
            self.action = action
            self.kw = kw

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    started: list = []
    stopped: list = []
    devices = [
        {"name": "Speakers", "index": 0, "is_default": True},
        {"name": "Casque", "index": 1},
    ]
    menu = Tray._build_capture_submenu(
        FakeMenu, FakeMenuItem, started.append, lambda: stopped.append(1),
        devices, "Arrêter",
    )
    items = [it for it in menu.items if isinstance(it, FakeMenuItem)]
    # Items attendus : défaut + 2 périphériques + arrêt.
    assert items[0].text.startswith("Démarrer")
    assert items[1].text == "Speakers (défaut)" and items[2].text == "Casque"
    assert items[-1].text == "Arrêter"

    # Chaque callback transmet la bonne spec (None / index) — garde anti-capture tardive.
    items[0].action(None, None)
    items[1].action(None, None)
    items[2].action(None, None)
    assert started == [None, 0, 1], started
    items[-1].action(None, None)
    assert stopped == [1]
    print("[comp 6] tray : sous-menu capture câble les bonnes specs (None/index)  OK")


def test_tray_meeting_submenu() -> None:
    from whisperty.tray import Tray

    class FakeMenuItem:
        def __init__(self, text=None, action=None, **kw):
            self.text = text
            self.action = action

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    started: list = []
    stopped: list = []
    devices = [{"name": "HDMI", "index": 3, "is_default": False}]
    menu = Tray._build_meeting_submenu(
        FakeMenu, FakeMenuItem, started.append, lambda: stopped.append(1), devices
    )
    items = [it for it in menu.items if isinstance(it, FakeMenuItem)]
    items[0].action(None, None)   # défaut → None
    items[1].action(None, None)   # HDMI → 3
    items[-1].action(None, None)  # arrêt
    assert started == [None, 3] and stopped == [1]
    print("[comp 7] tray : sous-menu assistant réunion câble les bonnes specs  OK")


# =============================================================================
# 4) setup_logging : journalisation locale, aucun handler réseau
# =============================================================================
def test_setup_logging(tmp_path: Path) -> None:
    from whisperty.app import setup_logging
    from whisperty.config import Config

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.logging.path = "logs_test/whisperty.log"
    cfg.logging.level = "DEBUG"

    root = logging.getLogger()
    snapshot = list(root.handlers)
    try:
        setup_logging(cfg)
        # Le dossier de log local est créé (handler fichier local, pas réseau).
        assert (base / "logs_test").is_dir()
        # Confidentialité : aucun handler réseau (Socket/Datagram/HTTP) installé.
        forbidden = (
            logging.handlers.SocketHandler,
            logging.handlers.DatagramHandler,
            logging.handlers.HTTPHandler,
        )
        assert not any(isinstance(h, forbidden) for h in root.handlers)
    finally:
        # Restaure l'état de journalisation et ferme les handlers ajoutés.
        for h in list(root.handlers):
            if h not in snapshot:
                try:
                    h.close()
                except Exception:
                    pass
                root.removeHandler(h)
        for h in snapshot:
            if h not in root.handlers:
                root.addHandler(h)
    print("[comp 8] setup_logging : dossier local créé + aucun handler réseau  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_comp_test_"))
    test_injector_edge_cases()
    test_injector_paste_no_restore()
    test_injector_swallows_errors()
    test_foreground_app()
    test_tray_states()
    test_tray_capture_submenu()
    test_tray_meeting_submenu()
    test_setup_logging(tmp)
    print("\nTOUS LES TESTS COMPONENTS PASSENT")


if __name__ == "__main__":
    _run_all()
