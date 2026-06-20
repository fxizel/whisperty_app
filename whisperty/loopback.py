"""Whisperty — capture loopback d'une sortie audio (V2).

Permet de transcrire ce qui SORT des haut-parleurs (ex. une confcall Teams), et
non le micro. Sous Windows, ni ``sounddevice``/PortAudio (le binaire embarqué
n'expose pas le loopback WASAPI) ne le permettent ; on s'appuie donc sur
``soundcard`` (Windows Core Audio via ctypes).

Confidentialité : capture strictement locale, aucun accès réseau. ``soundcard``
est importé paresseusement pour que le reste de l'application reste utilisable
sans cette dépendance (et que les tests hors-ligne n'en aient pas besoin).
"""
from __future__ import annotations

import contextlib
import logging
import sys
from typing import Iterator, Optional, Union

logger = logging.getLogger(__name__)

SAMPLE_RATE: int = 16_000  # fréquence cible (Whisper) ; soundcard rééchantillonne


@contextlib.contextmanager
def com_initialized() -> Iterator[None]:
    """Initialise COM (MTA) sur le thread courant pour la durée du bloc.

    ``soundcard`` n'initialise COM que sur le thread qui l'importe (au premier
    ``import``). Tout AUTRE thread qui appelle soundcard — le worker de transcription
    live, le futur coordinateur de réunion — lève sinon ``CO_E_NOTINITIALIZED``
    (0x800401F0). On (dé)initialise donc COM nous-mêmes autour de chaque session
    soundcard exécutée hors du thread principal. No-op hors Windows.
    """
    if sys.platform != "win32":
        yield
        return
    import ctypes

    ole32 = ctypes.windll.ole32
    coinit_multithreaded = 0x0
    hr = ole32.CoInitializeEx(None, coinit_multithreaded)
    # hr >= 0 (S_OK / S_FALSE) : initialisation réussie -> à équilibrer par CoUninitialize.
    # hr < 0 (ex. RPC_E_CHANGED_MODE) : COM déjà initialisé autrement -> ne pas le défaire.
    should_uninit = hr >= 0
    try:
        yield
    finally:
        if should_uninit:
            ole32.CoUninitialize()


class SoundcardUnavailableError(RuntimeError):
    """La bibliothèque ``soundcard`` n'est pas installée."""


class LoopbackError(RuntimeError):
    """Sortie audio introuvable ou capture loopback impossible."""


def _soundcard():
    """Importe ``soundcard`` à la demande, en erreur claire s'il manque."""
    try:
        import soundcard  # type: ignore
    except Exception as exc:  # noqa: BLE001 — ImportError ou erreur de chargement natif
        raise SoundcardUnavailableError(
            "La transcription live nécessite 'soundcard' : pip install soundcard"
        ) from exc
    return soundcard


def list_speakers() -> list[dict]:
    """Liste les sorties audio (haut-parleurs) disponibles.

    Renvoie une liste de ``{"index", "name", "id", "is_default"}``. Liste vide si
    ``soundcard`` est absent ou si l'énumération échoue (best-effort, ne lève pas).
    """
    try:
        with com_initialized():
            sc = _soundcard()
            speakers = sc.all_speakers()
            try:
                default_id = sc.default_speaker().id
            except Exception:  # noqa: BLE001 — pas de sortie par défaut
                default_id = None
            return [
                {"index": index, "name": spk.name, "id": spk.id, "is_default": spk.id == default_id}
                for index, spk in enumerate(speakers)
            ]
    except Exception:  # noqa: BLE001
        logger.debug("Énumération des sorties audio indisponible.", exc_info=True)
        return []


def resolve_loopback(device_spec: Optional[Union[int, str]]):
    """Résout ``device_spec`` en ``(nom, micro_loopback)`` prêt à enregistrer.

    ``device_spec`` : ``None``/``""`` = sortie par défaut ; ``int`` = index dans
    ``list_speakers`` ; ``str`` = identifiant exact ou sous-chaîne du nom.
    Lève :class:`LoopbackError` si aucune sortie ne correspond.
    """
    sc = _soundcard()
    speakers = sc.all_speakers()
    if not speakers:
        raise LoopbackError("Aucune sortie audio détectée.")

    speaker = None
    if device_spec is None or device_spec == "":
        try:
            speaker = sc.default_speaker()
        except Exception as exc:  # noqa: BLE001
            raise LoopbackError("Aucune sortie audio par défaut.") from exc
    elif isinstance(device_spec, bool):  # garde : bool est un int en Python
        raise LoopbackError(f"Sortie audio invalide : {device_spec!r}")
    elif isinstance(device_spec, int):
        if not 0 <= device_spec < len(speakers):
            raise LoopbackError(f"Index de sortie audio hors limites : {device_spec}")
        speaker = speakers[device_spec]
    else:
        needle = str(device_spec).lower()
        for candidate in speakers:
            if needle == candidate.id.lower() or needle in candidate.name.lower():
                speaker = candidate
                break
        if speaker is None:
            raise LoopbackError(f"Sortie audio introuvable : {device_spec!r}")

    # Le micro loopback partage l'identifiant du haut-parleur. NB : get_microphone()
    # lève IndexError (et non None) quand l'id ne correspond pas → on attrape pour
    # basculer sur le repli ci-dessous.
    try:
        mic = sc.get_microphone(speaker.id, include_loopback=True)
    except (IndexError, RuntimeError):
        mic = None
    if mic is None or not getattr(mic, "isloopback", False):
        mic = None  # un micro non-loopback éventuel est écarté
        for candidate in sc.all_microphones(include_loopback=True):
            if candidate.id == speaker.id and candidate.isloopback:
                mic = candidate
                break
    if mic is None:
        raise LoopbackError(
            f"Capture loopback indisponible pour la sortie « {speaker.name} »."
        )
    return speaker.name, mic
