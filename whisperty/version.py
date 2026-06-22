"""Numéro de version Whisperty — source unique pour l'UI, l'exe et l'installeur."""

from __future__ import annotations

__version__ = "0.1.0"


def version_tuple() -> tuple[int, int, int, int]:
    """Quadruplet entier pour les métadonnées Windows (exe, installeur)."""
    parts = [int(x) for x in __version__.split(".")[:4]]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def version_info() -> str:
    """Version normalisée sur 4 segments (ex. « 0.1.0 » → « 0.1.0.0 »)."""
    return ".".join(str(x) for x in version_tuple())
