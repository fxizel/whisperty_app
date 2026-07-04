"""Point d'entrée : ``python -m whisperty [--config CHEMIN]``."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from .app import WhispertyApp, setup_logging
from .config import Config
from .singleinstance import SingleInstance
from .version import __version__


def _default_config_path() -> str:
    """config.yaml à côté de l'exécutable en build PyInstaller (CWD non fiable au
    démarrage automatique), sinon dans le répertoire courant."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent / "config.yaml")
    return "config.yaml"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whisperty",
        description="Dictée vocale 100 % locale pour Windows.",
    )
    parser.add_argument(
        "--config",
        default=_default_config_path(),
        help="Chemin du fichier de configuration.",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Démarre en mode zone de notification seule (sans fenêtre).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + __version__,
    )
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    if args.no_gui:
        config.gui.enabled = False
    setup_logging(config)

    # Instance unique : un second lancement (icône du menu Démarrer alors que l'app
    # tourne déjà dans le tray) réaffiche la fenêtre existante au lieu de créer un
    # doublon qui se disputerait le raccourci global et le micro.
    guard = SingleInstance()
    if not guard.acquire():
        guard.notify_existing()
        logging.getLogger("whisperty").info(
            "Whisperty est déjà en cours d'exécution : fenêtre existante réaffichée."
        )
        return 0

    app = WhispertyApp(config)
    guard.watch(app.on_second_instance)
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()
    finally:
        guard.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
