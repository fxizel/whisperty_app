"""Tests hors-ligne du chargement de modèle (``Transcriber.load``).

Sans faster-whisper ni modèle réel : la bibliothèque est remplacée par une
doublure. On vérifie la gestion d'erreur (lib absente, modèle illisible) et —
point de confidentialité cardinal — que le mode ``local_files_only`` force les
variables d'environnement hors-ligne de Hugging Face AVANT tout import.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "sounddevice" not in sys.modules:
    import tests.conftest  # noqa: F401


def _install_fake_faster_whisper(model_factory):
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = model_factory
    previous = sys.modules.get("faster_whisper")
    sys.modules["faster_whisper"] = fw
    return previous


def _restore(previous) -> None:
    if previous is not None:
        sys.modules["faster_whisper"] = previous
    else:
        sys.modules.pop("faster_whisper", None)


# =============================================================================
# 1) faster-whisper absent → ModelNotAvailableError, modèle non chargé
# =============================================================================
def test_load_library_missing() -> None:
    from whisperty.config import TranscriptionConfig
    from whisperty.transcriber import ModelNotAvailableError, Transcriber

    t = Transcriber(TranscriptionConfig(local_files_only=False))
    previous = sys.modules.get("faster_whisper")
    sys.modules["faster_whisper"] = None  # « from faster_whisper import … » -> ImportError
    try:
        raised = False
        try:
            t.load()
        except ModelNotAvailableError as exc:
            raised = True
            assert "faster-whisper" in str(exc)
        assert raised and not t.is_loaded
    finally:
        _restore(previous)
    print("[trans-load 1] lib absente -> ModelNotAvailableError  OK")


# =============================================================================
# 2) WhisperModel qui lève → ModelNotAvailableError (message explicite)
# =============================================================================
def test_load_model_error() -> None:
    from whisperty.config import TranscriptionConfig
    from whisperty.transcriber import ModelNotAvailableError, Transcriber

    def boom_model(*a, **k):
        raise RuntimeError("CUDA indisponible")

    t = Transcriber(TranscriptionConfig(local_files_only=False))
    previous = _install_fake_faster_whisper(boom_model)
    try:
        raised = False
        try:
            t.load()
        except ModelNotAvailableError as exc:
            raised = True
            assert "CUDA indisponible" in str(exc)
        assert raised and not t.is_loaded
    finally:
        _restore(previous)
    print("[trans-load 2] modèle illisible -> ModelNotAvailableError détaillée  OK")


# =============================================================================
# 3) local_files_only=True → variables hors-ligne HF posées + chargement idempotent
# =============================================================================
def test_load_offline_env_and_idempotent() -> None:
    from whisperty.config import TranscriptionConfig
    from whisperty.transcriber import Transcriber

    calls: list[dict] = []

    class FakeModel:
        def __init__(self, model, device=None, compute_type=None, local_files_only=None):
            calls.append({"model": model, "device": device,
                          "compute_type": compute_type, "local": local_files_only})

    t = Transcriber(TranscriptionConfig(
        model="small", device="cpu", compute_type="int8", local_files_only=True
    ))

    saved_env = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    for k in saved_env:
        os.environ.pop(k, None)
    previous = _install_fake_faster_whisper(FakeModel)
    try:
        t.load()
        assert t.is_loaded
        # Garde de confidentialité : mode hors-ligne forcé pour huggingface_hub.
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        assert calls[0]["model"] == "small" and calls[0]["local"] is True

        # load() est idempotent : pas de second chargement.
        t.load()
        assert len(calls) == 1
    finally:
        _restore(previous)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("[trans-load 3] local_files_only -> env HF hors-ligne + idempotence  OK")


# =============================================================================
# 4) reset() : invalide le modèle, rechargé paresseusement au prochain usage
# =============================================================================
def test_reset_reloads_lazily() -> None:
    from whisperty.config import TranscriptionConfig
    from whisperty.transcriber import Transcriber

    calls: list[str] = []

    class FakeModel:
        def __init__(self, model, **kw):
            calls.append(model)

    t = Transcriber(TranscriptionConfig(model="small", local_files_only=False))
    previous = _install_fake_faster_whisper(FakeModel)
    try:
        t.load()
        assert t.is_loaded and t.effective_device == "cpu"
        t.reset()
        # Invalidé : rechargement paresseux (pas immédiat), device effectif oublié.
        assert not t.is_loaded and t.effective_device is None
        t.load()
        assert t.is_loaded and len(calls) == 2
    finally:
        _restore(previous)
    print("[trans-load 4] reset() -> modèle invalidé puis rechargé paresseusement  OK")


# =============================================================================
# 5) local_files_only=False : les variables hors-ligne posées PAR NOUS sont retirées
#    (sinon décocher « localOnly » dans l'UI resterait sans effet jusqu'au redémarrage)
# =============================================================================
def test_offline_env_removed_when_online() -> None:
    import types as _types

    from whisperty import transcriber as tr
    from whisperty.config import TranscriptionConfig
    from whisperty.transcriber import Transcriber

    class FakeModel:
        def __init__(self, model, **kw):
            pass

    saved_env = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    for k in saved_env:
        os.environ.pop(k, None)
    saved_set = set(tr._offline_env_set)
    tr._offline_env_set.clear()
    # Doublure huggingface_hub : la constante figée à l'import doit être resynchronisée.
    fake_hub = _types.ModuleType("huggingface_hub")
    fake_hub.constants = _types.SimpleNamespace(HF_HUB_OFFLINE=False)
    saved_hub = sys.modules.get("huggingface_hub")
    sys.modules["huggingface_hub"] = fake_hub
    previous = _install_fake_faster_whisper(FakeModel)
    try:
        cfg = TranscriptionConfig(model="small", local_files_only=True)
        t = Transcriber(cfg)
        t.load()
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert fake_hub.constants.HF_HUB_OFFLINE is True

        # L'utilisateur décoche « localOnly » puis recharge (écran Configuration).
        cfg.local_files_only = False
        t.reset()
        t.load()
        # Les variables posées par nous sont retirées ; la constante est resynchronisée.
        assert os.environ.get("HF_HUB_OFFLINE") is None
        assert os.environ.get("TRANSFORMERS_OFFLINE") is None
        assert fake_hub.constants.HF_HUB_OFFLINE is False

        # Une variable posée PAR L'UTILISATEUR n'est jamais retirée.
        os.environ["HF_HUB_OFFLINE"] = "1"
        t.reset()
        t.load()
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
    finally:
        _restore(previous)
        if saved_hub is not None:
            sys.modules["huggingface_hub"] = saved_hub
        else:
            sys.modules.pop("huggingface_hub", None)
        tr._offline_env_set.clear()
        tr._offline_env_set.update(saved_set)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("[trans-load 5] localOnly décoché -> env hors-ligne retiré (posé par nous seulement)  OK")


def _run_all() -> None:
    test_load_library_missing()
    test_load_model_error()
    test_load_offline_env_and_idempotent()
    test_reset_reloads_lazily()
    test_offline_env_removed_when_online()
    print("\nTOUS LES TESTS TRANSCRIBER-LOAD PASSENT")


if __name__ == "__main__":
    _run_all()
