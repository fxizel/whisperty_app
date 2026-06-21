"""Whisperty — récupère un modèle faster-whisper dans un dossier local (bundling).

Matérialise un modèle CTranslate2 (model.bin + config/tokenizer/vocabulaire) dans
``models/faster-whisper-<taille>/`` afin de le **bundler** avec l'application : le PC
cible fonctionne alors 100 % hors-ligne (config ``model: models/faster-whisper-<taille>``
+ ``local_files_only: true``), sans aucun appel réseau à l'usage.

Le modèle est copié depuis le cache Hugging Face s'il est déjà présent (aucun
téléchargement) ; sinon il est téléchargé UNE fois (seule exception réseau du projet).

    python scripts/fetch_model.py                 # « medium » dans models/
    python scripts/fetch_model.py --model small   # autre taille
    python scripts/fetch_model.py --offline       # échoue si absent du cache (pas de réseau)

Tailles : tiny | base | small | medium | large-v3 | turbo … (cf. faster_whisper).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Sortie redirigée (build.ps1) = cp1252 par défaut sous Windows : forcer UTF-8 évite
# un UnicodeEncodeError sur les caractères accentués/typographiques de nos messages.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def _safe_name(size: str) -> str:
    """Nom de dossier sûr dérivé de la taille (les ids type ``org/repo`` → basename)."""
    base = size.split("/")[-1]
    return f"faster-whisper-{base}" if not base.startswith("faster-") else base


def fetch(size: str, out_dir: Path, offline: bool) -> Path:
    try:
        from faster_whisper.utils import download_model
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "faster-whisper n'est pas installé : pip install -r requirements.txt"
        ) from exc

    target = out_dir / _safe_name(size)
    target.mkdir(parents=True, exist_ok=True)
    print(f"Récupération du modèle « {size} » -> {target} ...")
    # output_dir : matérialise des fichiers réels (pas de liens) dans target.
    download_model(size, output_dir=str(target), local_files_only=offline)

    model_bin = target / "model.bin"
    if not model_bin.is_file():
        raise SystemExit(
            f"Échec : {model_bin} introuvable après récupération. "
            "Vérifiez le nom de modèle et la connexion (ou le cache en --offline)."
        )
    # Métadonnées internes de huggingface_hub (verrous/blobs) inutiles à l'inférence :
    # on les retire pour ne pas alourdir le bundle / l'installeur.
    cache = target / ".cache"
    if cache.is_dir():
        import shutil

        shutil.rmtree(cache, ignore_errors=True)
    size_mb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6
    print(f"OK — modèle prêt ({size_mb:.0f} Mo).")
    print(f"Pour le bundler, config.yaml :  model: {target.relative_to(ROOT).as_posix()}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage un modèle faster-whisper local.")
    parser.add_argument("--model", default="medium", help="Taille/id du modèle (défaut: medium).")
    parser.add_argument(
        "--out", default=str(ROOT / "models"), help="Dossier de sortie (défaut: ./models)."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="N'utilise que le cache local (aucun téléchargement ; échoue si absent).",
    )
    args = parser.parse_args(argv)
    fetch(args.model, Path(args.out), args.offline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
