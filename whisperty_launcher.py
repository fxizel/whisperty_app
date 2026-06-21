"""Point d'entrée de l'exécutable figé (PyInstaller).

Pourquoi un fichier séparé de ``whisperty/__main__.py`` ?
``__main__.py`` utilise des imports **relatifs** (``from .app import …``), parfaitement
valides sous ``python -m whisperty`` (le module est alors exécuté avec
``__package__ == "whisperty"``). Mais PyInstaller exécute le script d'entrée comme un
top-level ``__main__`` **sans package parent** : les imports relatifs y lèvent alors
``ImportError: attempted relative import with no known parent package``.

Ce launcher importe le paquet de façon **absolue** ; les imports relatifs internes de
``whisperty`` fonctionnent ensuite normalement. Côté code applicatif, rien ne change.
"""
from __future__ import annotations

import multiprocessing
import sys

from whisperty.__main__ import main

if __name__ == "__main__":
    # Garde-fou PyInstaller : si une dépendance lance un process enfant, évite que
    # l'enfant ré-exécute l'application entière (rouvrirait une fenêtre/tray).
    multiprocessing.freeze_support()
    sys.exit(main())
