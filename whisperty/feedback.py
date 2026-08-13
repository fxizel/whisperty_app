"""Whisperty — retour sonore local de la dictée (Q-05 / US-08).

Bips brefs au démarrage et à l'arrêt de l'enregistrement : l'utilisateur sait que
son raccourci a été pris en compte sans regarder le tray (personas accessibilité
et saisie mains libres). ``winsound`` (stdlib Windows) : aucun fichier audio,
aucune dépendance, aucun réseau. No-op hors Windows ou si ``audio.sound_feedback``
est désactivé.

Concurrence : ``play()`` est non bloquant (thread démon, ``winsound.Beep`` est
bloquant pendant la durée du bip) et ne prend AUCUN verrou — il est sûr de
l'appeler depuis n'importe quel contexte, y compris sous ``_lock``.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Séquences (fréquence Hz, durée ms) : montante au démarrage, descendante à l'arrêt —
# distinguables à l'oreille sans être intrusives (courtes, médiums).
_TONES: dict[str, tuple[tuple[int, int], ...]] = {
    "start": ((660, 70), (990, 90)),
    "stop": ((990, 70), (660, 90)),
}


def play(event: str, enabled: bool = True) -> None:
    """Joue la séquence sonore de ``event`` (``start``/``stop``) sans bloquer.

    Best-effort : aucune exception ne remonte (un retour sonore ne doit jamais
    perturber la dictée), no-op si désactivé, hors Windows, ou évènement inconnu.
    """
    if not enabled:
        return
    tones = _TONES.get(event)
    if not tones:
        return
    try:
        threading.Thread(target=_play_tones, args=(tones,), daemon=True).start()
    except RuntimeError:  # threads OS épuisés : tant pis pour le bip
        logger.debug("Retour sonore ignoré (thread indisponible).")


def _play_tones(tones: tuple[tuple[int, int], ...]) -> None:
    """Corps du thread : joue la séquence (winsound absent = silence)."""
    try:
        import winsound  # stdlib Windows ; ImportError ailleurs

        for freq, duration in tones:
            winsound.Beep(freq, duration)
    except Exception:  # noqa: BLE001 — silence vaut mieux qu'un plantage de thread
        logger.debug("Retour sonore indisponible.", exc_info=True)
