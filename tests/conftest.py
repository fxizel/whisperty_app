"""Doublures partagées des dépendances binaires pour la suite hors-ligne.

Chargé automatiquement par pytest avant toute collecte. Installe des doublures
neutres pour les modules natifs (``sounddevice``, ``soundcard``, ``pystray``,
``Pillow``) afin que ``import whisperty.*`` réussisse sans micro, sortie audio,
ni GUI réelle. Les tests qui ont besoin d'un comportement précis remplacent
ces doublures localement (ex. ``whisperty.recorder.sd = faux_module``).

Idempotent et compatible avec ``tests/test_logic.py`` (qui installe les mêmes
doublures derrière des gardes ``if "x" not in sys.modules``).

Confidentialité : aucune de ces doublures n'ouvre de connexion réseau.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# --- racine du dépôt sur le PYTHONPATH (comme test_logic.py) ------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _install_audio_stubs() -> None:
    if "sounddevice" not in sys.modules:
        sd = types.ModuleType("sounddevice")
        sd.PortAudioError = type("PortAudioError", (Exception,), {})
        # Défauts neutres : aucun périphérique. Les tests recorder remplacent ``sd``
        # par un faux complet (InputStream, check_input_settings, query_devices).
        sd.query_devices = lambda *a, **k: []
        sd.check_input_settings = lambda **k: None
        sd.InputStream = None
        sys.modules["sounddevice"] = sd

    if "soundcard" not in sys.modules:
        # Aucune sortie audio : suffit pour construire l'app ; les tests loopback
        # remplacent ce module par un faux et le restaurent.
        sc = types.ModuleType("soundcard")
        sc.all_speakers = lambda: []
        sc.default_speaker = lambda: (_ for _ in ()).throw(RuntimeError("no default"))
        sc.all_microphones = lambda include_loopback=False: []
        sc.get_microphone = lambda dev_id, include_loopback=False: None
        sys.modules["soundcard"] = sc


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
                self.args = a
                self.kwargs = k

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


_install_audio_stubs()
_install_gui_stubs()
