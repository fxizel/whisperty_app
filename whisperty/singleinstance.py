r"""Whisperty — garde d'instance unique (Windows).

Deux instances simultanées se disputeraient le raccourci global, le micro et le
tray (double injection de texte, deux icônes). Le cas arrive naturellement :
l'app démarre avec Windows, puis l'utilisateur clique sur le raccourci du menu
Démarrer/Bureau « pour l'ouvrir ». Comportement attendu d'une app de zone de
notification : ce second lancement doit RÉAFFICHER la fenêtre de l'instance
existante, pas créer un doublon.

Mécanisme (objets noyau Windows nommés, espace ``Local\`` = par session — deux
sessions utilisateur restent indépendantes, cohérent avec l'installation par
utilisateur) :

* un **mutex nommé** détecte l'instance déjà lancée (``acquire``) ;
* un **évènement nommé** (auto-reset) sert de signal « montre-toi » : le second
  lancement le déclenche (``notify_existing``) puis se termine ; la première
  instance le surveille dans un thread veilleur (``watch``) et rappelle
  ``WhispertyApp.on_second_instance``.

100 % local (aucun réseau). Hors Windows (tests/CI Linux) : no-op transparent —
``acquire`` renvoie toujours True. Tout échec d'API Win32 est **non bloquant** :
au pire, la garde est absente et le comportement historique (multi-instance)
s'applique — jamais un lancement refusé à tort.

Les appels kernel32 passent par l'indirection ``_Win32`` (mise en cache) : les
tests multiplateformes la remplacent par une doublure via ``_k32_cached``.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0
_EVENT_MODIFY_STATE = 0x0002


class _Win32:
    """Liaisons kernel32 typées (HANDLE 64 bits — un restype int tronquerait).

    ``use_last_error=True`` : le code d'erreur est capturé PAR APPEL et relu via
    ``ctypes.get_last_error()`` — le ``GetLastError`` global peut être écrasé par
    les propres appels internes de ctypes entre-temps.
    """

    # pragma no cover : liaisons natives, exécutables uniquement sous Windows réel
    # (couvertes par test_singleinstance_windows_roundtrip ; la CI Linux passe par
    # la doublure _FakeK32 qui court-circuite cette construction).
    def __init__(self) -> None:  # pragma: no cover
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.CreateEventW.argtypes = (
            wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
        )
        k32.OpenEventW.restype = wintypes.HANDLE
        k32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        k32.SetEvent.restype = wintypes.BOOL
        k32.SetEvent.argtypes = (wintypes.HANDLE,)
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.create_mutex = k32.CreateMutexW
        self.create_event = k32.CreateEventW
        self.open_event = k32.OpenEventW
        self.set_event = k32.SetEvent
        self.wait_for = k32.WaitForSingleObject
        self.close_handle = k32.CloseHandle

    def get_last_error(self) -> int:  # pragma: no cover — idem __init__ (Windows réel)
        return self._ctypes.get_last_error()


_k32_cached: Optional[_Win32] = None


def _win32() -> _Win32:
    """Instance ``_Win32`` partagée (les tests la remplacent par une doublure)."""
    global _k32_cached
    if _k32_cached is None:
        _k32_cached = _Win32()  # pragma: no cover — construction réelle (Windows seul)
    return _k32_cached


class SingleInstance:
    """Garde d'instance unique fondée sur un mutex + un évènement nommés.

    ``name`` personnalise les objets noyau (les tests utilisent des noms uniques
    pour ne pas interférer avec une vraie instance de Whisperty en cours).
    """

    def __init__(self, name: str = "Whisperty") -> None:
        self._mutex_name = f"Local\\{name}.SingleInstance"
        self._event_name = f"Local\\{name}.ShowWindow"
        self._mutex = None
        self._event = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def acquire(self) -> bool:
        """True si nous sommes la première instance (toujours True hors Windows).

        En cas d'échec de l'API (improbable), on laisse démarrer : mieux vaut une
        éventuelle double instance qu'un lancement impossible.
        """
        if os.name != "nt":
            return True
        try:
            k32 = _win32()
            handle = k32.create_mutex(None, False, self._mutex_name)
            if not handle:
                return True
            if k32.get_last_error() == _ERROR_ALREADY_EXISTS:
                # Une instance tient déjà le mutex : on referme notre handle.
                k32.close_handle(handle)
                return False
            self._mutex = handle
            return True
        except Exception:  # noqa: BLE001 — la garde ne doit jamais empêcher le lancement
            logger.exception("Garde d'instance unique indisponible ; démarrage normal.")
            return True

    def notify_existing(self) -> bool:
        """Demande à l'instance existante de se manifester (second lancement)."""
        if os.name != "nt":
            return False
        try:
            k32 = _win32()
            handle = k32.open_event(_EVENT_MODIFY_STATE, False, self._event_name)
            if not handle:
                return False
            k32.set_event(handle)
            k32.close_handle(handle)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Signal vers l'instance existante impossible.")
            return False

    def watch(self, callback: Callable[[], None]) -> None:
        """Surveille les lancements ultérieurs (thread veilleur) ; appelle ``callback``.

        À n'appeler que sur la première instance (après ``acquire`` réussi).
        Best-effort : sans évènement, la garde reste active (pas de doublon), seul
        le réaffichage automatique de la fenêtre est perdu.
        """
        if os.name != "nt" or self._mutex is None or self._thread is not None:
            return
        try:
            k32 = _win32()
            # Évènement auto-reset : chaque SetEvent ne réveille qu'une attente.
            self._event = k32.create_event(None, False, False, self._event_name)
        except Exception:  # noqa: BLE001
            logger.exception("Création de l'évènement d'instance unique impossible.")
            return
        if not self._event:
            return

        def loop() -> None:
            while not self._stop.is_set():
                # Attente bornée (500 ms) pour relire le drapeau d'arrêt sans fuite.
                if k32.wait_for(self._event, 500) == _WAIT_OBJECT_0:
                    if self._stop.is_set():
                        return
                    logger.info("Second lancement de Whisperty détecté.")
                    try:
                        callback()
                    except Exception:  # noqa: BLE001
                        logger.exception("Rappel d'instance unique échoué")

        self._thread = threading.Thread(target=loop, daemon=True, name="single-instance")
        self._thread.start()

    def release(self) -> None:
        """Libère mutex/évènement (fin de vie ; sinon l'OS les ferme avec le process)."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.5)
            self._thread = None
        if os.name != "nt":
            return
        try:
            k32 = _win32()
            for attr in ("_mutex", "_event"):
                handle = getattr(self, attr)
                if handle:
                    k32.close_handle(handle)
                    setattr(self, attr, None)
        except Exception:  # noqa: BLE001
            logger.debug("Libération de la garde d'instance unique échouée.", exc_info=True)
