"""Point d'entrée : ``python -m whisperty [--config CHEMIN]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .app import WhispertyApp, setup_logging
from .config import Config


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
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    setup_logging(config)

    app = WhispertyApp(config)
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
