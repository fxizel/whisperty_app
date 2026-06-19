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


_COLORS = {
    TrayState.IDLE: (120, 120, 120),       # gris : prêt
    TrayState.RECORDING: (220, 40, 40),    # rouge : enregistrement
    TrayState.PROCESSING: (230, 150, 30),  # orange : transcription
}
_TITLES = {
    TrayState.IDLE: "Whisperty — prêt",
    TrayState.RECORDING: "Whisperty — enregistrement…",
    TrayState.PROCESSING: "Whisperty — transcription…",
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
    ) -> None:
        from pystray import Icon, Menu, MenuItem

        self._state = TrayState.IDLE
        self._images = {state: _make_image(color) for state, color in _COLORS.items()}

        # Les callbacks pystray reçoivent (icon, item) ; on les ignore.
        menu = Menu(
            MenuItem("Démarrer / Arrêter la dictée", lambda icon, item: on_toggle()),
            MenuItem(
                "Ouvrir la configuration",
                lambda icon, item: on_open_config() if on_open_config else None,
                enabled=on_open_config is not None,
            ),
            Menu.SEPARATOR,
            MenuItem("Quitter", lambda icon, item: on_quit()),
        )
        self._icon = Icon(
            "whisperty", self._images[TrayState.IDLE], _TITLES[TrayState.IDLE], menu
        )

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

    def stop(self) -> None:
        self._icon.stop()
