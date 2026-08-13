"""Whisperty — injection de texte dans l'application active (Étape 3).

Deux méthodes :
- ``paste`` (défaut) : copie dans le presse-papiers puis envoie Ctrl+V. Robuste
  pour les accents français et instantané sur les longs textes. Le presse-papiers
  est restauré ensuite si ``restore_clipboard`` est activé.
- ``type`` : frappe caractère par caractère via pynput (repli).

L'application active reçoit le texte sans qu'aucune donnée ne quitte la machine.
"""
from __future__ import annotations

import logging
import time

from .config import OutputConfig

logger = logging.getLogger(__name__)


class TextInjector:
    """Injecte du texte dans la fenêtre actuellement au premier plan."""

    def __init__(self, cfg: OutputConfig) -> None:
        self.cfg = cfg
        self._keyboard = None  # pynput Controller, importé paresseusement

    def _controller(self):
        if self._keyboard is None:
            from pynput.keyboard import Controller

            self._keyboard = Controller()
        return self._keyboard

    def inject(self, text: str) -> bool:
        """Injecte ``text`` dans l'application active selon la méthode configurée.

        Renvoie ``True`` si l'injection a abouti, ``False`` sinon (presse-papiers
        verrouillé, fenêtre cible élevée…) — l'appelant peut alors prévenir
        l'utilisateur, le texte restant disponible dans l'historique. Ne lève
        jamais (contrat historique : l'injection ne fait pas planter l'app).
        """
        if not text:
            return True
        try:
            if self.cfg.method == "type":
                self._inject_type(text)
            else:
                self._inject_paste(text)
            return True
        except ImportError as exc:
            logger.error("Dépendance d'injection manquante : %s", exc)
        except Exception:  # noqa: BLE001 — l'injection ne doit pas faire planter l'app
            logger.exception("Échec de l'injection de texte")
        return False

    def _inject_paste(self, text: str) -> None:
        import pyperclip
        from pynput.keyboard import Key

        previous = None
        if self.cfg.restore_clipboard:
            try:
                previous = pyperclip.paste()
            except Exception:  # noqa: BLE001 — presse-papiers indisponible
                previous = None
            # Un presse-papiers NON TEXTE (capture d'écran, fichiers copiés) est lu
            # comme "" par pyperclip : le « restaurer » écraserait ce contenu. On ne
            # restaure donc que du texte réel.
            if not previous:
                previous = None

        pyperclip.copy(text)
        time.sleep(0.05)  # laisser le presse-papiers se synchroniser

        controller = self._controller()
        with controller.pressed(Key.ctrl):
            controller.press("v")
            controller.release("v")

        if self.cfg.restore_clipboard and previous is not None:
            # Laisser l'app cible lire le presse-papiers avant restauration : un délai
            # trop court ferait coller l'ANCIEN contenu (éditeur lent, RDP). Réglable
            # via output.restore_delay.
            time.sleep(max(0.0, self.cfg.restore_delay))
            try:
                pyperclip.copy(previous)
            except Exception:  # noqa: BLE001
                pass

    def _inject_type(self, text: str) -> None:
        controller = self._controller()
        for char in text:
            try:
                controller.type(char)
            except Exception:  # noqa: BLE001 — caractère intypable (hors-BMP : émoji…)
                # On saute le caractère plutôt que de perdre TOUT le reste du texte.
                # Point de code seulement : pas de contenu dicté dans les logs ≥ INFO.
                logger.warning("Caractère non injectable ignoré : U+%04X", ord(char))
                continue
            if self.cfg.type_delay:
                time.sleep(self.cfg.type_delay)

    def copy_to_clipboard(self, text: str) -> bool:
        """Copie ``text`` dans le presse-papiers sans le coller (import audio, historique).

        Renvoie ``True`` si la copie a réussi.
        """
        if not text:
            return False
        try:
            import pyperclip

            pyperclip.copy(text)
            return True
        except Exception:  # noqa: BLE001 — presse-papiers / dépendance indisponible
            logger.exception("Copie dans le presse-papiers échouée")
            return False
