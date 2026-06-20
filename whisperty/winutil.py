"""Whisperty — utilitaires Windows (détection de l'application active).

Utilisé par les profils de contexte (``profiles.py``) pour adapter la
transcription à l'application qui recevra le texte (VS Code, Outlook, Teams…).

Confidentialité : lecture purement locale via l'API Win32 (``user32``/``kernel32``),
aucun accès réseau. Sur une plateforme non-Windows ou en cas d'erreur, renvoie
``None`` — l'appelant retombe alors sur le profil par défaut.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)


def foreground_app() -> Optional[str]:
    """Nom de l'exécutable de la fenêtre au premier plan (ex. ``Code.exe``).

    Renvoie ``None`` hors Windows, sans fenêtre active, ou si l'API échoue
    (droits insuffisants sur un process élevé, par exemple).
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Typage explicite : sur Python 64 bits, les HANDLE/HWND sont des pointeurs.
        # Sans restype/argtypes, ctypes les tronque en int 32 bits (handles corrompus).
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        # PROCESS_QUERY_LIMITED_INFORMATION : suffit pour le nom, dispo sans privilège.
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — la détection ne doit jamais faire planter l'app
        logger.debug("Détection de l'application active indisponible.", exc_info=True)
    return None
