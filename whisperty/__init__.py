"""Whisperty — dictée vocale 100 % locale pour Windows.

Pipeline : raccourci global → capture audio → transcription Whisper →
post-traitement (dictionnaire) → injection dans l'application active.

Aucune donnée ne quitte la machine.
"""

from .version import __version__, version_info, version_tuple

__all__ = ["__version__", "version_info", "version_tuple"]
