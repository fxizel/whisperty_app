"""Whisperty — icône system tray (Étape 4).

Icône colorée reflétant l'état (idle / recording / processing) + menu clic droit.
Les images sont générées en mémoire (Pillow) : aucun fichier d'icône externe requis.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class TrayState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    LIVE = "live"
    CONFERENCE = "conference"
    MEETING = "meeting"


_COLORS = {
    TrayState.IDLE: (120, 120, 120),       # gris : prêt
    TrayState.RECORDING: (220, 40, 40),    # rouge : enregistrement
    TrayState.PROCESSING: (230, 150, 30),  # orange : transcription
    TrayState.LIVE: (40, 90, 220),         # bleu : transcription live (sortie audio)
    TrayState.CONFERENCE: (30, 170, 120),  # vert : réunion (micro + sortie)
    TrayState.MEETING: (90, 40, 180),      # violet : assistant de réunion
}
_TITLES = {
    TrayState.IDLE: "Whisperty — prêt",
    TrayState.RECORDING: "Whisperty — enregistrement…",
    TrayState.PROCESSING: "Whisperty — transcription…",
    TrayState.LIVE: "Whisperty — transcription live…",
    TrayState.CONFERENCE: "Whisperty — réunion en cours…",
    TrayState.MEETING: "Whisperty — assistant de réunion…",
}


def _make_image(color: tuple[int, int, int]):
    """Génère une pastille ronde de la couleur donnée (64×64, fond transparent)."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=color)
    return image


class Tray:
    """Icône de zone de notification pilotable depuis l'application."""

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        on_open_config: Optional[Callable[[], None]] = None,
        on_import_audio: Optional[Callable[[], None]] = None,
        on_copy_last: Optional[Callable[[], None]] = None,
        on_open_history: Optional[Callable[[], None]] = None,
        on_start_live: Optional[Callable[[Optional[object]], None]] = None,
        on_stop_live: Optional[Callable[[], None]] = None,
        on_start_meeting: Optional[Callable[[Optional[object]], None]] = None,
        on_stop_meeting: Optional[Callable[[], None]] = None,
        live_devices: Optional[list[dict]] = None,
        on_start_conference: Optional[Callable[[Optional[object]], None]] = None,
        on_stop_conference: Optional[Callable[[], None]] = None,
        on_show: Optional[Callable[[], None]] = None,
    ) -> None:
        from pystray import Icon, Menu, MenuItem

        self._state = TrayState.IDLE
        self._images = {state: _make_image(color) for state, color in _COLORS.items()}

        def _action(callback: Optional[Callable[[], None]]):
            # Les callbacks pystray reçoivent (icon, item) ; on les ignore.
            return lambda icon, item: callback() if callback else None

        menu = Menu(
            # Action par défaut (double-clic gauche) : ré-ouvrir la fenêtre si elle existe.
            MenuItem(
                "Ouvrir Whisperty",
                _action(on_show),
                default=True,
                visible=on_show is not None,
            ),
            MenuItem("Démarrer / Arrêter la dictée", _action(on_toggle)),
            MenuItem(
                "Transcription live (sortie audio)",
                self._build_capture_submenu(
                    Menu, MenuItem, on_start_live, on_stop_live, live_devices,
                    "Arrêter la transcription live",
                ),
                enabled=on_start_live is not None,
            ),
            MenuItem(
                "Transcription de réunion (micro + sortie)",
                self._build_capture_submenu(
                    Menu, MenuItem, on_start_conference, on_stop_conference, live_devices,
                    "Arrêter la réunion",
                ),
                enabled=on_start_conference is not None,
            ),
            MenuItem(
                "Assistant de réunion (réponses auto)",
                self._build_meeting_submenu(
                    Menu, MenuItem, on_start_meeting, on_stop_meeting, live_devices
                ),
                enabled=on_start_meeting is not None,
            ),
            MenuItem(
                "Importer un fichier audio…",
                _action(on_import_audio),
                enabled=on_import_audio is not None,
            ),
            MenuItem(
                "Copier la dernière transcription",
                _action(on_copy_last),
                enabled=on_copy_last is not None,
            ),
            Menu.SEPARATOR,
            MenuItem(
                "Ouvrir la configuration",
                _action(on_open_config),
                enabled=on_open_config is not None,
            ),
            MenuItem(
                "Ouvrir le dossier de l'historique",
                _action(on_open_history),
                enabled=on_open_history is not None,
            ),
            Menu.SEPARATOR,
            MenuItem("Quitter", _action(on_quit)),
        )
        self._icon = Icon(
            "whisperty", self._images[TrayState.IDLE], _TITLES[TrayState.IDLE], menu
        )

    @staticmethod
    def _build_capture_submenu(Menu, MenuItem, on_start, on_stop, devices, stop_label):
        """Sous-menu générique « capture de sortie » : choix de la sortie + arrêt.

        Partagé par la transcription live et la réunion (la sortie système à capturer
        est choisie de la même façon). ``on_start(spec)`` reçoit None (défaut) ou un index.
        """
        def start_with(spec):
            def action(icon, item):
                return on_start(spec) if on_start else None
            return action

        items = [MenuItem("Démarrer — sortie par défaut", start_with(None))]
        for dev in (devices or []):
            label = dev["name"] + (" (défaut)" if dev.get("is_default") else "")
            items.append(MenuItem(label, start_with(dev["index"])))
        items.append(Menu.SEPARATOR)
        items.append(
            MenuItem(
                stop_label,
                lambda icon, item: on_stop() if on_stop else None,
                enabled=on_stop is not None,
            )
        )
        return Menu(*items)

    @staticmethod
    def _build_meeting_submenu(Menu, MenuItem, on_start_meeting, on_stop_meeting, live_devices):
        """Sous-menu de l'assistant de réunion : choix de la sortie + arrêt."""
        def start_with(spec):
            return lambda icon, item: on_start_meeting(spec) if on_start_meeting else None

        items = [MenuItem("Démarrer — sortie par défaut", start_with(None))]
        for dev in (live_devices or []):
            label = dev["name"] + (" (défaut)" if dev.get("is_default") else "")
            items.append(MenuItem(label, start_with(dev["index"])))
        items.append(Menu.SEPARATOR)
        items.append(
            MenuItem(
                "Arrêter l'assistant de réunion",
                lambda icon, item: on_stop_meeting() if on_stop_meeting else None,
                enabled=on_stop_meeting is not None,
            )
        )
        return Menu(*items)

    def notify(self, message: str, title: str = "Whisperty") -> None:
        """Affiche une notification système (best-effort selon le backend pystray)."""
        try:
            self._icon.notify(message, title)
        except Exception:  # noqa: BLE001 — toutes les plateformes ne supportent pas notify()
            logger.debug("Notification tray indisponible : %s", message)

    @property
    def state(self) -> TrayState:
        return self._state

    def set_state(self, state: TrayState) -> None:
        """Met à jour l'icône et l'infobulle selon l'état."""
        self._state = state
        self._icon.icon = self._images[state]
        self._icon.title = _TITLES[state]

    def run(self) -> None:
        """Boucle bloquante (tient le thread principal jusqu'à Quitter)."""
        self._icon.run()

    def run_detached(self) -> None:
        """Démarre le tray dans un thread dédié (backend win32) sans bloquer.

        Utilisé quand le thread principal est tenu par la fenêtre WebView2 : le tray
        reste un compagnon actif. ``stop()`` reste valable depuis n'importe quel thread.
        """
        self._icon.run_detached()

    def stop(self) -> None:
        self._icon.stop()
