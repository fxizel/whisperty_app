"""Tests hors-ligne du peaufinage UX (V2) : retours utilisateur visibles (notices),
état/téléchargement guidé du modèle (``modeldl``), protection du modèle bundlé à
l'enregistrement de la config, et garde d'instance unique (``singleinstance``).

Aucun réseau : ``faster_whisper.utils.download_model`` est remplacé par une doublure
qui matérialise un faux ``model.bin`` local. Les objets noyau Windows des tests
d'instance unique utilisent des noms UNIQUES (pas de collision avec une éventuelle
instance réelle de Whisperty sur le poste de dev).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "sounddevice" not in sys.modules:  # secours hors pytest
    import tests.conftest  # noqa: F401

from whisperty import modeldl
from whisperty.transcriber import ModelNotAvailableError


def _make_app(tmp: Path):
    from whisperty.app import WhispertyApp
    from whisperty.config import Config

    cfg = Config()
    cfg.base_dir = tmp
    cfg.history.enabled = True
    cfg.history.path = "h.db"
    cfg.dictionary.enabled = False
    cfg.profiles.enabled = False
    app = WhispertyApp(cfg)
    # Évite chargement modèle réel + démarrage d'un écouteur clavier global.
    app._preload = lambda: None
    app.reload_hotkey = lambda: None
    return app, cfg


# =============================================================================
# 1) Notices : publication thread-safe + relais tray + câblage des erreurs
# =============================================================================
def test_notify_user_and_notice(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    seen: list[str] = []
    app.tray.notify = lambda msg, title="Whisperty": seen.append(msg)

    assert app.notice_rev() == 0
    assert app.notice() == {"rev": 0, "text": "", "kind": "info"}

    app._notify_user("Boum", "error")
    n = app.notice()
    assert n == {"rev": 1, "text": "Boum", "kind": "error"}
    assert seen == ["Boum"]

    # tray=False : réservé à la fenêtre (pas de notification système).
    app._notify_user("Discret", "warn", tray=False)
    assert app.notice() == {"rev": 2, "text": "Discret", "kind": "warn"}
    assert seen == ["Boum"]
    print("[ux 1] _notify_user : rev/text/kind + relais tray (opt-out)  OK")


def test_mic_error_is_notified(tmp_path: Path) -> None:
    from whisperty.recorder import MicrophoneError
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    app.tray.notify = lambda *a, **k: None

    def boom():
        raise MicrophoneError("Micro indisponible ou paramètres non supportés.")

    app.recorder.start = boom
    app.toggle()  # IDLE -> tentative d'enregistrement -> erreur micro
    assert app._state is TrayState.IDLE
    n = app.notice()
    assert n["kind"] == "error" and "Micro indisponible" in n["text"]
    print("[ux 2] Erreur micro : état intact + notice visible  OK")


def test_process_model_error_sets_banner_state(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    app.tray.notify = lambda *a, **k: None

    def raise_model(audio, profile=None):
        raise ModelNotAvailableError("modèle absent")

    app.transcriber.transcribe = raise_model
    with app._lock:
        app._set_state(TrayState.PROCESSING)
    app._process(object())
    assert app._state is TrayState.IDLE
    assert app.model_ok() is False
    n = app.notice()
    # Message actionnable : pointe vers le téléchargement en un clic (taille connue).
    assert "small" in n["text"] and "télécharger" in n["text"].lower()

    # Une transcription qui réussit ensuite efface l'état d'échec (bannière levée).
    app.transcriber.transcribe = lambda audio, profile=None: ""
    with app._lock:
        app._set_state(TrayState.PROCESSING)
    app._process(object())
    assert app.model_ok() is True
    print("[ux 3] Échec modèle : _model_error + notice, effacé au succès suivant  OK")


def test_process_generic_error_notified(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    app.tray.notify = lambda *a, **k: None

    def raise_generic(audio, profile=None):
        raise RuntimeError("boom")

    app.transcriber.transcribe = raise_generic
    with app._lock:
        app._set_state(TrayState.PROCESSING)
    app._process(object())
    assert app._state is TrayState.IDLE
    assert "logs/whisperty.log" in app.notice()["text"]
    print("[ux 4] Échec générique de dictée : notice avec pointeur vers les logs  OK")


# =============================================================================
# 2) modeldl : aides (taille, téléchargeabilité, poids) + machine à états
# =============================================================================
def test_model_size_name_and_labels() -> None:
    assert modeldl.model_size_name("medium") == "medium"
    assert modeldl.model_size_name("models/faster-whisper-medium") == "medium"
    assert modeldl.model_size_name("models\\faster-whisper-large-v3") == "large-v3"
    assert modeldl.model_size_name("models/faster-whisper-medium/") == "medium"
    assert modeldl.model_size_name(None) == ""

    assert modeldl.is_downloadable("medium") is True
    assert modeldl.is_downloadable("turbo") is True
    assert modeldl.is_downloadable("mon-dossier-perso") is False

    assert modeldl.approx_size_label("tiny") == "~75 Mo"
    assert modeldl.approx_size_label("medium") == "~1,5 Go"
    assert modeldl.approx_size_label("inconnu") == ""
    print("[ux 5] modeldl : normalisation de taille + libellés de poids  OK")


def _fake_faster_whisper(download_impl):
    """Installe une doublure ``faster_whisper.utils.download_model`` ; renvoie l'état à restaurer."""
    fake_utils = types.ModuleType("faster_whisper.utils")
    fake_utils.download_model = download_impl
    fake = types.ModuleType("faster_whisper")
    fake.utils = fake_utils
    saved = {k: sys.modules.get(k) for k in ("faster_whisper", "faster_whisper.utils")}
    sys.modules["faster_whisper"] = fake
    sys.modules["faster_whisper.utils"] = fake_utils
    return saved


def _restore_modules(saved: dict) -> None:
    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def test_downloader_success_and_activation(tmp_path: Path) -> None:
    calls: dict = {}

    def fake_download(size, output_dir=None, local_files_only=False):
        calls["args"] = (size, output_dir, local_files_only)
        out = Path(output_dir)
        (out / "model.bin").write_bytes(b"x" * 2048)
        (out / ".cache").mkdir()
        (out / ".cache" / "meta").write_text("hub", encoding="utf-8")

    saved = _fake_faster_whisper(fake_download)
    try:
        dl = modeldl._Downloader()
        done: dict = {}
        dl._run("small", tmp_path / "models" / "faster-whisper-small",
                lambda size, target: done.update(size=size, target=target))
        st = dl.status()
        assert st["state"] == "done" and "small" in st["message"]
        assert done["size"] == "small"
        assert (tmp_path / "models" / "faster-whisper-small" / "model.bin").is_file()
        # Métadonnées hub purgées, téléchargement bien en ligne (local_files_only=False).
        assert not (tmp_path / "models" / "faster-whisper-small" / ".cache").exists()
        assert calls["args"][2] is False
    finally:
        _restore_modules(saved)
    print("[ux 6] modeldl : téléchargement + vérif model.bin + activation  OK")


def test_downloader_failure_paths(tmp_path: Path) -> None:
    # 1) Échec réseau (download_model lève) → état error, message actionnable.
    def fail_download(size, output_dir=None, local_files_only=False):
        raise OSError("connexion coupée")

    saved = _fake_faster_whisper(fail_download)
    try:
        dl = modeldl._Downloader()
        dl._run("small", tmp_path / "m" / "faster-whisper-small", None)
        st = dl.status()
        assert st["state"] == "error" and "connexion" in st["message"]
    finally:
        _restore_modules(saved)

    # 2) Téléchargement OK mais activation (on_success) qui lève → error explicite.
    def ok_download(size, output_dir=None, local_files_only=False):
        (Path(output_dir) / "model.bin").write_bytes(b"x")

    saved = _fake_faster_whisper(ok_download)
    try:
        dl = modeldl._Downloader()

        def bad_activation(size, target):
            raise RuntimeError("écriture config impossible")

        dl._run("small", tmp_path / "m2" / "faster-whisper-small", bad_activation)
        st = dl.status()
        assert st["state"] == "error" and "activation" in st["message"]
    finally:
        _restore_modules(saved)

    # 3) Taille inconnue refusée AVANT tout thread/réseau.
    res = modeldl._Downloader().start("mon-modele-perso", tmp_path, None)
    assert res["ok"] is False and "téléchargeable" in res["error"]
    print("[ux 7] modeldl : échec réseau / activation / taille inconnue  OK")


def _fake_hf_hub(download_impl):
    """Doublure ``huggingface_hub.hf_hub_download`` (zéro réseau) ; état à restaurer."""
    fake = types.ModuleType("huggingface_hub")
    fake.hf_hub_download = download_impl
    saved = {"huggingface_hub": sys.modules.get("huggingface_hub")}
    sys.modules["huggingface_hub"] = fake
    return saved


def test_embedding_downloader(tmp_path: Path) -> None:
    """CO-19 : téléchargement opt-in du modèle de diarisation (succès, échecs, garde)."""
    from whisperty.transcriber import _offline_env_set, _set_offline_env

    seen: dict = {}
    # Taille attendue réduite le temps du test (le vrai modèle pèse 26 Mo : inutile de
    # les écrire sur le disque pour valider la logique).
    real_bytes = modeldl.EMBEDDING_BYTES
    modeldl.EMBEDDING_BYTES = 2_000

    def fake_download(repo_id=None, filename=None, revision=None, local_dir=None, token=None):
        seen["args"] = (repo_id, filename, local_dir)
        seen["revision"] = revision
        seen["token"] = token
        # La garde hors-ligne doit être LEVÉE pendant le téléchargement, sinon
        # huggingface_hub refuserait la requête.
        seen["offline_during"] = os.environ.get("HF_HUB_OFFLINE")
        out = Path(local_dir) / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"z" * modeldl.EMBEDDING_BYTES)
        return str(out)

    saved = _fake_hf_hub(fake_download)
    _set_offline_env(True)                    # état de départ : hors-ligne (défaut)
    try:
        dl = modeldl._EmbeddingDownloader()
        activated: dict = {}
        dl._run(tmp_path, tmp_path / modeldl._EMBEDDING_TMP_DIR,
                lambda path: activated.update(path=path))
        st = dl.status()
        assert st["state"] == "done", st
        # Modèle en place sous son nom canonique, dossier de travail nettoyé.
        dest = tmp_path / modeldl.EMBEDDING_DEST_NAME
        assert dest.is_file() and dest.stat().st_size == modeldl.EMBEDDING_BYTES
        assert not (tmp_path / modeldl._EMBEDDING_TMP_DIR).exists()
        assert activated["path"] == dest
        # Dépôt PUBLIC et fichier attendus.
        assert seen["args"][0] == modeldl.EMBEDDING_REPO
        assert seen["args"][1] == modeldl.EMBEDDING_FILE
        # Révision ÉPINGLÉE (binaire figé) et jeton EXPLICITEMENT refusé : le dépôt est
        # public, un jeton présent sur la machine rendrait le téléchargement
        # nominativement attribuable sans aucun bénéfice.
        assert seen["revision"] == modeldl.EMBEDDING_REVISION
        assert seen["token"] is False
        assert seen["offline_during"] is None          # garde bien levée pendant
        # …et REPOSÉE de façon déterministe après (zéro réseau à l'usage ensuite).
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
    finally:
        modeldl.EMBEDDING_BYTES = real_bytes
        _restore_modules(saved)
        for var in list(_offline_env_set):
            os.environ.pop(var, None)
            _offline_env_set.discard(var)

    # Échec réseau → état error actionnable, garde reposée, rien de laissé en place.
    def fail_download(repo_id=None, filename=None, revision=None, local_dir=None, token=None):
        raise OSError("connexion coupée")

    saved = _fake_hf_hub(fail_download)
    _set_offline_env(True)
    try:
        dl = modeldl._EmbeddingDownloader()
        dl._run(tmp_path / "b", tmp_path / "b" / modeldl._EMBEDDING_TMP_DIR, None)
        st = dl.status()
        assert st["state"] == "error" and "connexion" in st["message"]
        assert not (tmp_path / "b" / modeldl.EMBEDDING_DEST_NAME).exists()
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
    finally:
        _restore_modules(saved)
        for var in list(_offline_env_set):
            os.environ.pop(var, None)
            _offline_env_set.discard(var)

    # Fichier de taille inattendue (transfert interrompu, mauvais fichier) → refusé :
    # jamais de modèle inutilisable mis en place.
    def short_download(repo_id=None, filename=None, revision=None, local_dir=None, token=None):
        out = Path(local_dir) / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * 10)
        return str(out)

    saved = _fake_hf_hub(short_download)
    try:
        dl = modeldl._EmbeddingDownloader()
        dl._run(tmp_path / "c", tmp_path / "c" / modeldl._EMBEDDING_TMP_DIR, None)
        assert dl.status()["state"] == "error"
        assert not (tmp_path / "c" / modeldl.EMBEDDING_DEST_NAME).exists()
    finally:
        _restore_modules(saved)

    # huggingface_hub absent (exe minimal) → message clair, jamais d'exception.
    saved = {"huggingface_hub": sys.modules.get("huggingface_hub")}
    sys.modules["huggingface_hub"] = None
    try:
        dl = modeldl._EmbeddingDownloader()
        dl._run(tmp_path / "d", tmp_path / "d" / modeldl._EMBEDDING_TMP_DIR, None)
        assert dl.status()["state"] == "error"
        assert "huggingface_hub" in dl.status()["message"]
    finally:
        _restore_modules(saved)
    print("[ux 8b] modeldl : modèle de diarisation (succès, échec, tronqué, garde)  OK")


def test_download_running_aggregates() -> None:
    """La garde hors-ligne ne doit pas être reposée pendant l'UN des téléchargements."""
    from whisperty.transcriber import _model_download_running

    assert modeldl.download_running() is False
    assert _model_download_running() is False
    # Simule un téléchargement de DIARISATION en cours (pas celui de Whisper) :
    # sans l'agrégation, transcriber reposerait la garde et le ferait échouer.
    modeldl._embedding_downloader._set("running", "…")
    try:
        assert modeldl.download_running() is True
        assert _model_download_running() is True
    finally:
        modeldl._embedding_downloader._set("idle", "")
    modeldl._downloader._set("running", "…")
    try:
        assert modeldl.download_running() is True
    finally:
        modeldl._downloader._set("idle", "")
    assert modeldl.download_running() is False
    print("[ux 8c] garde hors-ligne : agrégation des deux téléchargements opt-in  OK")


def test_app_diar_model_status(tmp_path: Path) -> None:
    """CO-19 : état exposé à l'UI + activation du backend après téléchargement."""
    app, cfg = _make_app(tmp_path)
    sd = cfg.conference.speaker_diarization
    sd.backend = "mfcc"
    sd.onnx_model = "models/speaker-embedding.onnx"

    st = app.diar_model_status()
    assert st["backend"] == "mfcc" and st["installed"] is False
    assert "Mo" in st["sizeLabel"] and st["download"]["state"] == "idle"

    # Modèle présent sur le disque → installed True (chemin résolu près de config.yaml).
    model = tmp_path / "models" / "speaker-embedding.onnx"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"onnx")
    assert app.diar_model_status()["installed"] is True

    # Chemin vide (config manuelle) : pas d'exception, simplement « absent ».
    sd.onnx_model = ""
    assert app.diar_model_status()["installed"] is False
    sd.onnx_model = "models/speaker-embedding.onnx"

    # Activation après téléchargement : config en mémoire ET config.yaml basculés.
    (tmp_path / "config.yaml").write_text(
        "conference:\n  speaker_diarization:\n    enabled: true  # garder ce commentaire\n",
        encoding="utf-8",
    )
    app._on_diar_model_downloaded(model, backend_at_start="mfcc")
    assert sd.backend == "onnx"
    assert sd.onnx_model == f"models/{modeldl.EMBEDDING_DEST_NAME}"
    written = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "garder ce commentaire" in written              # écriture chirurgicale
    d = yaml.safe_load(written)["conference"]["speaker_diarization"]
    assert d["backend"] == "onnx" and d["onnx_model"].endswith(".onnx")
    assert app.notice_rev() > 0 and "diarisation" in app.notice()["text"]
    assert app.diar_model_status()["backend"] == "onnx"

    # L'utilisateur revient à « Intégré » PENDANT le téléchargement (thread du pont) :
    # son choix récent primait, le callback ne doit pas le réécraser (mise à jour perdue).
    sd.backend = "mfcc"
    sd.onnx_model = "models/vieux.onnx"
    app._on_diar_model_downloaded(model, backend_at_start="onnx")
    assert sd.backend == "mfcc"                            # choix utilisateur conservé
    assert sd.onnx_model == f"models/{modeldl.EMBEDDING_DEST_NAME}"   # chemin quand même à jour
    d = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert d["conference"]["speaker_diarization"]["backend"] == "onnx"  # pas réécrit
    print("[ux 8d] app : état du modèle de diarisation + activation persistée  OK")


def test_downloader_start_async(tmp_path: Path) -> None:
    """start() : passage en running, progression (Mo), refus de doublon, fin done."""
    gate = threading.Event()

    def slow_download(size, output_dir=None, local_files_only=False):
        (Path(output_dir) / "model.bin").write_bytes(b"y" * 4096)
        assert gate.wait(timeout=5.0)

    saved = _fake_faster_whisper(slow_download)
    try:
        dl = modeldl._Downloader()
        res = dl.start("tiny", tmp_path / "models", None)
        assert res["ok"] is True and res["state"] == "running"
        assert "tiny" in res["message"] and "75 Mo" in res["message"]
        # Doublon pendant l'exécution : renvoie l'état courant, pas un 2e thread.
        res2 = dl.start("tiny", tmp_path / "models", None)
        assert res2["state"] == "running"
        # Progression : le faux model.bin est déjà écrit (>0 Mo arrondi à 0 possible,
        # on vérifie seulement que le champ existe et est un entier).
        st = dl.status()
        assert st["state"] == "running" and isinstance(st["mb"], int)
        gate.set()
        deadline = time.monotonic() + 5.0
        while dl.status()["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert dl.status()["state"] == "done"
    finally:
        gate.set()
        _restore_modules(saved)
    print("[ux 8] modeldl.start : running -> done, doublon refusé, progression  OK")


# =============================================================================
# 3) WhispertyApp : model_status / start_model_download / _on_model_downloaded
# =============================================================================
def test_app_model_status_and_download(tmp_path: Path) -> None:
    app, cfg = _make_app(tmp_path)
    app.tray.notify = lambda *a, **k: None

    st = app.model_status()
    assert st["ok"] is True and st["size"] == "small" and st["canDownload"] is True
    assert st["sizeLabel"] == "~485 Mo" and st["download"]["state"] in ("idle", "done", "error")

    app._set_model_error(ModelNotAvailableError("absent"))
    st = app.model_status()
    assert st["ok"] is False and st["error"] == "absent"

    # start_model_download délègue au module avec la taille normalisée et models/.
    captured: dict = {}

    def fake_start(size, dest, on_success=None):
        captured.update(size=size, dest=Path(dest), cb=on_success)
        return {"ok": True, "state": "running"}

    saved = modeldl.start_download
    modeldl.start_download = fake_start
    try:
        cfg.transcription.model = "models/faster-whisper-medium"
        res = app.start_model_download()
        assert res["ok"] is True
        assert captured["size"] == "medium"
        assert captured["dest"] == cfg.resolve("models")
        assert captured["cb"] == app._on_model_downloaded
    finally:
        modeldl.start_download = saved
    print("[ux 9] model_status + start_model_download : formes et délégation  OK")


def test_on_model_downloaded_activates_and_persists(tmp_path: Path) -> None:
    app, cfg = _make_app(tmp_path)
    app.tray.notify = lambda *a, **k: None
    (tmp_path / "config.yaml").write_text(
        "transcription:\n  model: small           # taille\n  local_files_only: false\n",
        encoding="utf-8",
    )
    cfg.transcription.local_files_only = False
    app._set_model_error(ModelNotAvailableError("absent"))

    reloaded: list[bool] = []
    app._reload_model = lambda: reloaded.append(True)
    app._on_model_downloaded("small", tmp_path / "models" / "faster-whisper-small")

    # Config en mémoire + fichier : modèle local, hors-ligne strict rétabli.
    assert cfg.transcription.model == "models/faster-whisper-small"
    assert cfg.transcription.local_files_only is True
    d = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert d["transcription"]["model"] == "models/faster-whisper-small"
    assert d["transcription"]["local_files_only"] is True
    # Rechargement demandé, bannière levée, notice de succès.
    assert reloaded == [True]
    assert app.model_ok() is True
    n = app.notice()
    assert n["kind"] == "info" and "installé" in n["text"]
    print("[ux 10] _on_model_downloaded : config (mémoire+fichier) + rechargement + notice  OK")


# =============================================================================
# 4) apply_config_from_gui : protection d'un modèle bundlé (chemin ≠ taille)
# =============================================================================
def test_apply_config_keeps_bundled_model(tmp_path: Path) -> None:
    app, cfg = _make_app(tmp_path)
    cfg.transcription.model = "models/faster-whisper-medium"
    (tmp_path / "config.yaml").write_text(
        "transcription:\n  model: models/faster-whisper-medium\n", encoding="utf-8"
    )

    # Enregistrer SANS changer de taille (l'UI renvoie « medium ») : le chemin
    # bundlé fonctionnel ne doit PAS être remplacé par un nom de taille.
    res = app.apply_config_from_gui({"model": "medium"})
    assert res == {"ok": True}
    assert cfg.transcription.model == "models/faster-whisper-medium"

    # Changement réel de taille sans dossier local : nom de taille (cache HF).
    res = app.apply_config_from_gui({"model": "small"})
    assert res == {"ok": True}
    assert cfg.transcription.model == "small"

    # Changement vers une taille disposant d'un dossier local téléchargé/bundlé :
    # le chemin local est privilégié (hors-ligne).
    local = tmp_path / "models" / "faster-whisper-medium"
    local.mkdir(parents=True)
    (local / "model.bin").write_bytes(b"x")
    res = app.apply_config_from_gui({"model": "medium"})
    assert res == {"ok": True}
    assert cfg.transcription.model == "models/faster-whisper-medium"
    print("[ux 11] apply_config : modèle bundlé protégé + résolution locale d'abord  OK")


# =============================================================================
# 5) GuiApi : poll enrichi, get_notice, model_status/download_model
# =============================================================================
def test_gui_api_notices_and_model(tmp_path: Path) -> None:
    from whisperty.gui import GuiApi

    app, _ = _make_app(tmp_path)
    app.tray.notify = lambda *a, **k: None
    api = GuiApi(app)

    p = api.poll()
    for key in ("state", "level", "liveRev", "noticeRev", "modelOk", "modelLoaded"):
        assert key in p, key
    assert p["modelOk"] is True and p["modelLoaded"] is False  # pas encore chargé

    app._notify_user("Coucou", "info")
    p = api.poll()
    assert p["noticeRev"] == 1
    assert api.get_notice() == {"rev": 1, "text": "Coucou", "kind": "info"}

    app._set_model_error(RuntimeError("absent"))
    assert api.poll()["modelOk"] is False
    st = api.model_status()
    assert st["ok"] is False and st["canDownload"] is True

    # download_model délègue à l'app (remplacée ici par un espion).
    app.start_model_download = lambda: {"ok": True, "state": "running"}
    assert api.download_model()["ok"] is True
    print("[ux 12] GuiApi : poll (noticeRev/modelOk/modelLoaded) + get_notice + modèle  OK")


def test_gui_hidden_notice_once(tmp_path: Path) -> None:
    """La première réduction dans le tray produit UNE notice (pas de répétition)."""
    from whisperty.gui import GuiApi

    app, _ = _make_app(tmp_path)
    app.tray.notify = lambda *a, **k: None
    api = GuiApi(app)
    api.notify_hidden_once()
    api.notify_hidden_once()
    n = app.notice()
    assert n["rev"] == 1 and "reste actif" in n["text"]
    print("[ux 13] Réduction dans le tray : notice unique par session  OK")


# =============================================================================
# 6) singleinstance : no-op hors Windows + vrai aller-retour sous Windows
# =============================================================================
def test_singleinstance_posix_noop() -> None:
    from whisperty import singleinstance as si

    guard = si.SingleInstance(f"WhispertyTest-{uuid.uuid4().hex}")
    saved_os = si.os
    si.os = types.SimpleNamespace(name="posix")
    try:
        assert guard.acquire() is True
        assert guard.notify_existing() is False
        guard.watch(lambda: None)      # no-op sans mutex/plateforme
        assert guard._thread is None
        guard.release()                # ne lève pas
    finally:
        si.os = saved_os
    print("[ux 14] singleinstance : no-op transparent hors Windows  OK")


class _FakeK32:
    """Doublure kernel32 : sémantique mutex/évènement NOMMÉS reproduite en mémoire.

    Permet de tester les chemins Windows de ``singleinstance`` sur toute plateforme
    (CI Linux comprise) sans objets noyau réels. Auto-reset des évènements et
    destruction du mutex à la fermeture du dernier handle, comme l'API réelle.
    """

    def __init__(self) -> None:
        self.mutex_names: set[str] = set()
        self.events: dict[str, threading.Event] = {}
        self.handles: dict[int, tuple[str, str]] = {}
        self._next = 100

    def _new_handle(self, kind: str, name: str) -> int:
        self._next += 1
        self.handles[self._next] = (kind, name)
        return self._next

    def create_mutex(self, sec, initial, name):
        self._last = 183 if name in self.mutex_names else 0
        self.mutex_names.add(name)
        return self._new_handle("mutex", name)

    def get_last_error(self) -> int:
        return self._last

    def create_event(self, sec, manual, initial, name):
        self.events.setdefault(name, threading.Event())
        return self._new_handle("event", name)

    def open_event(self, access, inherit, name):
        if name not in self.events:
            return 0
        return self._new_handle("event", name)

    def set_event(self, handle):
        _, name = self.handles[handle]
        self.events[name].set()
        return True

    def wait_for(self, handle, timeout_ms):
        _, name = self.handles[handle]
        if self.events[name].wait(timeout_ms / 1000.0):
            self.events[name].clear()  # auto-reset
            return 0
        return 258  # WAIT_TIMEOUT

    def close_handle(self, handle):
        kind, name = self.handles.pop(handle, ("", ""))
        if kind == "mutex" and not any(
            n == name and k == "mutex" for k, n in self.handles.values()
        ):
            self.mutex_names.discard(name)  # dernier handle fermé => mutex détruit
        return True


def test_singleinstance_fake_win32_roundtrip() -> None:
    """Chemins Windows complets via la doublure kernel32 (exécutable partout)."""
    from whisperty import singleinstance as si

    fake = _FakeK32()
    saved_cache, saved_os = si._k32_cached, si.os
    si._k32_cached = fake
    si.os = types.SimpleNamespace(name="nt")
    try:
        name = f"WhispertyFake-{uuid.uuid4().hex}"
        first, second = si.SingleInstance(name), si.SingleInstance(name)
        assert first.acquire() is True
        assert second.acquire() is False        # doublon détecté (ERROR_ALREADY_EXISTS)
        assert second.notify_existing() is False  # pas encore de veilleur => pas d'évènement

        shown = threading.Event()
        first.watch(shown.set)
        thread = first._thread
        first.watch(shown.set)                  # idempotent : pas de second veilleur
        assert first._thread is thread
        assert second.notify_existing() is True
        assert shown.wait(timeout=3.0)

        second.release()
        first.release()
        assert first._mutex is None and first._event is None

        # Dernier handle fermé => le nom est réutilisable (pas de fuite).
        third = si.SingleInstance(name)
        assert third.acquire() is True
        third.release()
    finally:
        si._k32_cached, si.os = saved_cache, saved_os
    print("[ux 14b] singleinstance : aller-retour complet sur doublure kernel32  OK")


def test_singleinstance_api_failures_never_block() -> None:
    """Échec des API Win32 : acquire laisse démarrer, notify/watch se dégradent."""
    from whisperty import singleinstance as si

    class BrokenK32:
        def create_mutex(self, *a):
            raise OSError("api hs")

        def open_event(self, *a):
            raise OSError("api hs")

        def create_event(self, *a):
            raise OSError("api hs")

        def close_handle(self, *a):
            return True

    saved_cache, saved_os = si._k32_cached, si.os
    si._k32_cached = BrokenK32()
    si.os = types.SimpleNamespace(name="nt")
    try:
        guard = si.SingleInstance(f"WhispertyBroken-{uuid.uuid4().hex}")
        assert guard.acquire() is True          # jamais un lancement refusé à tort
        assert guard.notify_existing() is False
        guard._mutex = 42                        # simule un mutex acquis
        guard.watch(lambda: None)                # création d'évènement en échec => no-op
        assert guard._thread is None
        guard.release()
    finally:
        si._k32_cached, si.os = saved_cache, saved_os
    print("[ux 14c] singleinstance : API en échec = dégradation douce  OK")


@pytest.mark.skipif(os.name != "nt", reason="objets noyau Windows requis")
def test_singleinstance_windows_roundtrip() -> None:
    from whisperty.singleinstance import SingleInstance

    # Nom unique : n'interfère JAMAIS avec une vraie instance de Whisperty.
    name = f"WhispertyTest-{uuid.uuid4().hex}"
    first = SingleInstance(name)
    second = SingleInstance(name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False   # doublon détecté

        shown = threading.Event()
        first.watch(shown.set)
        assert second.notify_existing() is True
        assert shown.wait(timeout=3.0)     # le veilleur a rappelé la 1re instance
    finally:
        second.release()
        first.release()

    # Après libération, le nom est réutilisable (pas de fuite de handle).
    third = SingleInstance(name)
    try:
        assert third.acquire() is True
    finally:
        third.release()
    print("[ux 15] singleinstance : mutex + signal « montre-toi » (Windows)  OK")


def test_on_second_instance_paths(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    notified: list[str] = []
    app.tray.notify = lambda msg, title="Whisperty": notified.append(msg)

    # Sans fenêtre : notification tray « déjà lancé ».
    app.on_second_instance()
    assert notified and "déjà lancé" in notified[-1]

    # Avec fenêtre : réaffichage (show + restore), pas de nouvelle notification.
    calls: list[str] = []

    class FakeWin:
        def show(self):
            calls.append("show")

        def restore(self):
            calls.append("restore")

    class FakeGui:
        _window = FakeWin()

    with app._lock:
        app._gui = FakeGui()
    before = len(notified)
    app.on_second_instance()
    assert calls == ["show", "restore"] and len(notified) == before
    print("[ux 16] on_second_instance : tray seul vs fenêtre réaffichée  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_ux_test_"))
    test_model_size_name_and_labels()
    test_download_running_aggregates()
    test_singleinstance_posix_noop()
    test_singleinstance_fake_win32_roundtrip()
    test_singleinstance_api_failures_never_block()
    if os.name == "nt":
        test_singleinstance_windows_roundtrip()
    for i, fn in enumerate([
        test_notify_user_and_notice,
        test_mic_error_is_notified,
        test_process_model_error_sets_banner_state,
        test_process_generic_error_notified,
        test_downloader_success_and_activation,
        test_downloader_failure_paths,
        test_embedding_downloader,
        test_app_diar_model_status,
        test_downloader_start_async,
        test_app_model_status_and_download,
        test_on_model_downloaded_activates_and_persists,
        test_apply_config_keeps_bundled_model,
        test_gui_api_notices_and_model,
        test_gui_hidden_notice_once,
        test_on_second_instance_paths,
    ]):
        d = tmp / f"t{i}"
        d.mkdir(parents=True, exist_ok=True)
        fn(d)
    print("\nTOUS LES TESTS UX PASSENT")


if __name__ == "__main__":
    _run_all()
