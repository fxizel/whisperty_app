"""Tests hors-ligne de l'orchestration (``whisperty.app.WhispertyApp``).

Couvre les chemins non testés par ``test_logic.py`` : traitement d'une dictée et
d'un fichier importé, modes live/réunion/assistant (transitions d'état +
notifications), historique (copier la dernière), surveillance VAD, validation du
raccourci, robustesse du démarrage de thread, préchargement et arrêt propre.

Les sous-systèmes lourds (recorder, transcriber, injector, tray, live…) sont
remplacés par des doublures après construction de l'app. Aucune dépendance
binaire ni réseau (doublures installées par ``conftest.py``).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# --- racine + doublures minimales pour exécution autonome --------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "sounddevice" not in sys.modules:  # secours hors pytest (conftest non chargé)
    import tests.conftest  # noqa: F401  (déclenche l'installation des doublures)


# --- doublures réutilisables --------------------------------------------------
class FakeTray:
    def __init__(self):
        self.notes: list[str] = []
        self.states: list = []
        self.stopped = False

    def notify(self, message, title="Whisperty"):
        self.notes.append(message)

    def set_state(self, state):
        self.states.append(state)

    def stop(self):
        self.stopped = True


class FakeInjector:
    def __init__(self, copy_ok=True):
        self.injected: list[str] = []
        self.copied: list[str] = []
        self._copy_ok = copy_ok

    def inject(self, text):
        self.injected.append(text)

    def copy_to_clipboard(self, text):
        self.copied.append(text)
        return self._copy_ok


class FakeHistory:
    def __init__(self, last=None):
        self.added: list[dict] = []
        self._last = last
        self.closed = False

    def add(self, text, **kwargs):
        self.added.append({"text": text, **kwargs})

    def last_text(self):
        return self._last

    def close(self):
        self.closed = True


class FakeTranscriber:
    def __init__(self, text="texte transcrit"):
        self.text = text
        self.loaded = 0
        self.error: Exception | None = None

    def load(self):
        self.loaded += 1
        if self.error is not None:
            raise self.error

    def transcribe(self, audio, profile=None):
        return self.text

    def transcribe_file(self, path):
        return self.text


class FakeWorker:
    """Doublure de LiveTranscriber / ConferenceTranscriber / MeetingAssistant."""

    def __init__(self, start_ok=True):
        self.start_ok = start_ok
        self.started_with: list = []
        self.stopped = 0
        self.waited = 0

    def start(self, device_spec=None):
        self.started_with.append(device_spec)
        return self.start_ok

    def stop(self):
        self.stopped += 1

    def wait(self, timeout=None):
        self.waited += 1


def _make_app(tmp: Path):
    """Construit une WhispertyApp isolée (pas d'I/O cwd) avec doublures observables."""
    from whisperty.app import WhispertyApp
    from whisperty.config import Config

    cfg = Config()
    cfg.base_dir = tmp
    cfg.history.enabled = False
    cfg.dictionary.enabled = False
    cfg.profiles.enabled = False
    app = WhispertyApp(cfg)
    app.tray = FakeTray()
    app.injector = FakeInjector()
    app.history = FakeHistory()
    app.transcriber = FakeTranscriber()
    return app, cfg


# =============================================================================
# 1) Traitement d'une dictée (_process)
# =============================================================================
def test_process_dictation(tmp_path: Path) -> None:
    import numpy as np

    from whisperty.tray import TrayState

    app, cfg = _make_app(tmp_path)
    app._active_app = "Code.exe"
    app._state = TrayState.PROCESSING
    app._process(np.ones(4, np.float32))

    assert app.injector.injected == ["texte transcrit"]
    assert app.history.added and app.history.added[0]["source"] == "dictée"
    assert app.history.added[0]["app"] == "Code.exe"
    assert app.history.added[0]["model"] == cfg.transcription.model
    assert app._state is TrayState.IDLE
    print("[app 1] _process : transcription -> injection + historique + IDLE  OK")


def test_process_empty_and_error(tmp_path: Path) -> None:
    import numpy as np

    from whisperty.tray import TrayState
    from whisperty.transcriber import ModelNotAvailableError

    # Texte vide → ni injection ni historique, retour à IDLE.
    app, _ = _make_app(tmp_path)
    app.transcriber.text = ""
    app._state = TrayState.PROCESSING
    app._process(np.ones(4, np.float32))
    assert app.injector.injected == [] and app.history.added == []
    assert app._state is TrayState.IDLE

    # Modèle indisponible → erreur gérée, état rétabli, pas d'injection.
    app2, _ = _make_app(tmp_path)
    def boom(audio, profile=None):
        raise ModelNotAvailableError("modèle absent")
    app2.transcriber.transcribe = boom
    app2._state = TrayState.PROCESSING
    app2._process(np.ones(4, np.float32))
    assert app2.injector.injected == [] and app2._state is TrayState.IDLE
    print("[app 2] _process : texte vide + ModelNotAvailableError -> IDLE  OK")


# =============================================================================
# 2) Import d'un fichier audio (_process_file + import_audio)
# =============================================================================
def test_process_file(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, cfg = _make_app(tmp_path)
    app._state = TrayState.PROCESSING
    app._process_file("/chemin/reunion.wav")

    assert app.injector.copied == ["texte transcrit"]   # copié (pas injecté)
    assert app.injector.injected == []
    assert app.history.added[0]["source"] == "fichier"
    assert app.history.added[0]["app"] == "reunion.wav"
    assert any("copié" in n for n in app.tray.notes)
    assert app._state is TrayState.IDLE
    print("[app 3] _process_file : transcription -> presse-papiers + notif + IDLE  OK")


def test_process_file_not_found(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    def missing(path):
        raise FileNotFoundError("introuvable")
    app.transcriber.transcribe_file = missing
    app._state = TrayState.PROCESSING
    app._process_file("/absent.wav")
    assert app.injector.copied == [] and app._state is TrayState.IDLE
    print("[app 4] _process_file : FileNotFoundError gérée -> IDLE  OK")


def test_import_audio_guarded(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    # Annulation du sélecteur → no-op.
    app._ask_audio_file = lambda: None
    app.import_audio()
    assert app._state is TrayState.IDLE

    # Sélection valide mais app occupée → import ignoré, état inchangé.
    app._ask_audio_file = lambda: "/x.wav"
    app._state = TrayState.RECORDING
    app.import_audio()
    assert app._state is TrayState.RECORDING
    print("[app 5] import_audio : annulation + refus si occupé  OK")


# =============================================================================
# 3) Historique : copier la dernière transcription
# =============================================================================
def test_copy_last(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)

    # Historique vide → notification dédiée, rien de copié.
    app.history = FakeHistory(last=None)
    app.copy_last()
    assert app.injector.copied == []
    assert any("vide" in n.lower() for n in app.tray.notes)

    # Avec texte → copie + notification.
    app2, _ = _make_app(tmp_path)
    app2.history = FakeHistory(last="dernier texte")
    app2.copy_last()
    assert app2.injector.copied == ["dernier texte"]
    assert any("copiée" in n.lower() for n in app2.tray.notes)
    print("[app 6] copy_last : historique vide + copie + notifications  OK")


# =============================================================================
# 4) Mode live : démarrage/arrêt + callback de fin
# =============================================================================
def test_live_lifecycle(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, cfg = _make_app(tmp_path)
    app.live = FakeWorker(start_ok=True)
    app.start_live()
    assert app._state is TrayState.LIVE
    assert app.live.started_with == [cfg.live.device]  # défaut config

    app.stop_live()
    assert app.live.stopped == 1

    # Callback de fin avec texte → IDLE + historique « live » + copie + notif.
    app._on_live_finished({"text": "abc", "segments": 2, "device": "Speakers"})
    assert app._state is TrayState.IDLE
    assert app.history.added[-1]["source"] == "live"
    assert app.injector.copied == ["abc"]

    # Refus si une autre opération est en cours.
    app2, _ = _make_app(tmp_path)
    app2.live = FakeWorker()
    app2._state = TrayState.RECORDING
    app2.start_live()
    assert app2.live.started_with == [] and app2._state is TrayState.RECORDING

    # Échec de démarrage → retour à IDLE.
    app3, _ = _make_app(tmp_path)
    app3.live = FakeWorker(start_ok=False)
    app3.start_live()
    assert app3._state is TrayState.IDLE

    # Callback de fin en erreur → notification d'erreur, pas d'historique.
    app4, _ = _make_app(tmp_path)
    app4._state = TrayState.LIVE
    app4._on_live_finished({"error": "périphérique perdu"})
    assert app4._state is TrayState.IDLE
    assert any("périphérique perdu" in n for n in app4.tray.notes)
    assert app4.history.added == []
    print("[app 7] live : start/stop + fin (texte/erreur) + refus si occupé  OK")


# =============================================================================
# 5) Mode réunion (conference) : démarrage + consentement + callback
# =============================================================================
def test_conference_lifecycle(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, cfg = _make_app(tmp_path)
    app.conference = FakeWorker(start_ok=True)
    app.start_conference()
    assert app._state is TrayState.CONFERENCE
    assert app.conference.started_with == [cfg.conference.system_device]
    assert any("consentement" in n.lower() for n in app.tray.notes)  # rappel local

    app.stop_conference()
    assert app.conference.stopped == 1

    # Fin avec transcript exporté → notification mentionnant le chemin + historique.
    app._on_conference_finished({
        "text": "compte rendu", "segments": 3, "device": "Spk",
        "path": str(tmp_path / "reunion.txt"), "sources": ["mic", "system"],
    })
    assert app._state is TrayState.IDLE
    assert app.history.added[-1]["source"] == "réunion"
    assert any("reunion.txt" in n for n in app.tray.notes)
    print("[app 8] réunion : start (consentement) + stop + fin (transcript)  OK")


# =============================================================================
# 6) Assistant de réunion : gardes (ai/user_name) + cycle de vie
# =============================================================================
def test_meeting_guards_and_lifecycle(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    # (a) ai.enabled = False → refus, pas de démarrage.
    app, cfg = _make_app(tmp_path)
    app.meeting = FakeWorker()
    cfg.ai.enabled = False
    cfg.meeting.user_name = "Jean"
    app.start_meeting()
    assert app.meeting.started_with == [] and app._state is TrayState.IDLE
    assert any("ai.enabled" in n for n in app.tray.notes)

    # (b) user_name vide → refus.
    app2, cfg2 = _make_app(tmp_path)
    app2.meeting = FakeWorker()
    cfg2.ai.enabled = True
    cfg2.meeting.user_name = "   "
    app2.start_meeting()
    assert app2.meeting.started_with == [] and app2._state is TrayState.IDLE
    assert any("user_name" in n for n in app2.tray.notes)

    # (c) Conditions remplies → démarre, état MEETING, notification « actif ».
    app3, cfg3 = _make_app(tmp_path)
    app3.meeting = FakeWorker(start_ok=True)
    cfg3.ai.enabled = True
    cfg3.meeting.user_name = "Jean"
    app3.start_meeting()
    assert app3._state is TrayState.MEETING
    assert app3.meeting.started_with == [cfg3.live.device]
    assert any("actif" in n.lower() for n in app3.tray.notes)

    # (d) Échec du démarrage → retour IDLE.
    app4, cfg4 = _make_app(tmp_path)
    app4.meeting = FakeWorker(start_ok=False)
    cfg4.ai.enabled = True
    cfg4.meeting.user_name = "Jean"
    app4.start_meeting()
    assert app4._state is TrayState.IDLE

    # (e) Callback de fin avec réponses → historique + copie + notif « réponse(s) ».
    app5, _ = _make_app(tmp_path)
    app5._state = TrayState.MEETING
    app5._on_meeting_finished({"text": "tr", "reply_count": 2, "device": "Spk"})
    assert app5._state is TrayState.IDLE
    assert app5.history.added[-1]["source"] == "réunion-transcript"
    assert app5.injector.copied == ["tr"]
    assert any("réponse" in n.lower() for n in app5.tray.notes)
    print("[app 9] assistant réunion : gardes ai/user_name + cycle + fin  OK")


# =============================================================================
# 7) Validation du raccourci clavier
# =============================================================================
def test_validated_combo() -> None:
    from whisperty.app import WhispertyApp

    class FakeHotKey:
        @staticmethod
        def parse(combo):
            if combo == "<invalide>":
                raise ValueError("combo invalide")
            return [combo]

    kb = types.SimpleNamespace(HotKey=FakeHotKey)
    assert WhispertyApp._validated_combo(kb, "<ctrl>+<alt>+x") == "<ctrl>+<alt>+x"
    # Invalide → repli sur le défaut documenté.
    assert WhispertyApp._validated_combo(kb, "<invalide>") == "<ctrl>+<alt>+<space>"
    print("[app 10] _validated_combo : valide conservé + invalide -> défaut  OK")


# =============================================================================
# 8) Robustesse : échec de démarrage d'un thread worker
# =============================================================================
def test_spawn_worker_failure(tmp_path: Path) -> None:
    import whisperty.app as app_mod
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    app._state = TrayState.PROCESSING

    def raising_thread(*a, **k):
        raise RuntimeError("threads OS épuisés")

    saved = app_mod.threading
    app_mod.threading = types.SimpleNamespace(Thread=raising_thread)
    try:
        ok = app._spawn_worker(lambda: None)
    finally:
        app_mod.threading = saved
    assert ok is False
    assert app._state is TrayState.IDLE  # état rétabli, pas figé en PROCESSING
    print("[app 11] _spawn_worker : échec de thread -> IDLE (pas de blocage)  OK")


# =============================================================================
# 9) Surveillance d'enregistrement (_monitor_recording)
# =============================================================================
def test_monitor_silence(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, cfg = _make_app(tmp_path)
    cfg.hotkey.mode = "toggle"
    cfg.audio.vad_threshold = 0.01
    cfg.audio.silence_duration = 0.1
    cfg.audio.max_duration = 999.0

    class LevelSource:
        def __init__(self):
            self.n = 0

        @property
        def current_level(self):
            self.n += 1
            return 0.5 if self.n <= 2 else 0.0  # parole brève puis silence

    app.recorder = LevelSource()
    calls = {"stop": 0}
    def fake_stop():
        calls["stop"] += 1
        app._state = TrayState.IDLE
    app._stop_and_process = fake_stop

    app._state = TrayState.RECORDING
    app._monitor_recording()
    assert calls["stop"] == 1  # arrêt déclenché par le silence prolongé
    print("[app 12] _monitor_recording : silence prolongé -> arrêt auto  OK")


def test_monitor_max_duration(tmp_path: Path) -> None:
    from whisperty.tray import TrayState

    app, cfg = _make_app(tmp_path)
    cfg.hotkey.mode = "toggle"
    cfg.audio.vad_threshold = 0.01
    cfg.audio.silence_duration = 999.0
    cfg.audio.max_duration = 0.1  # garde-fou court

    app.recorder = types.SimpleNamespace(current_level=0.0)  # jamais de parole
    calls = {"stop": 0}
    def fake_stop():
        calls["stop"] += 1
        app._state = TrayState.IDLE
    app._stop_and_process = fake_stop

    app._state = TrayState.RECORDING
    app._monitor_recording()
    assert calls["stop"] == 1  # coupé par le garde-fou de durée max
    print("[app 13] _monitor_recording : garde-fou durée max -> arrêt  OK")


# =============================================================================
# 10) Préchargement du modèle (_preload)
# =============================================================================
def test_preload(tmp_path: Path) -> None:
    from whisperty.tray import TrayState
    from whisperty.transcriber import ModelNotAvailableError

    app, _ = _make_app(tmp_path)
    app._preload()
    assert app.transcriber.loaded == 1 and app._state is TrayState.IDLE

    # Échec de chargement → erreur gérée, état rétabli.
    app2, _ = _make_app(tmp_path)
    app2.transcriber.error = ModelNotAvailableError("modèle absent")
    app2._preload()
    assert app2._state is TrayState.IDLE
    print("[app 14] _preload : PROCESSING -> chargement -> IDLE (+ échec géré)  OK")


# =============================================================================
# 11) Arrêt propre (quit) : ordre des arrêts + idempotence
# =============================================================================
def test_quit(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    app.recorder = types.SimpleNamespace(stop=lambda: None)
    app.live = FakeWorker()
    app.conference = FakeWorker()
    app.meeting = FakeWorker()

    app.quit()
    assert app._quitting is True
    assert app.live.stopped == 1 and app.live.waited == 1
    assert app.conference.stopped == 1 and app.conference.waited == 1
    assert app.meeting.stopped == 1 and app.meeting.waited == 1
    assert app.history.closed is True
    assert app.tray.stopped is True

    # quit() idempotent : second appel ne ré-arrête rien.
    app.quit()
    assert app.live.stopped == 1
    print("[app 15] quit : arrêt ordonné de tous les sous-systèmes + idempotence  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_app_test_"))
    test_process_dictation(tmp)
    test_process_empty_and_error(tmp)
    test_process_file(tmp)
    test_process_file_not_found(tmp)
    test_import_audio_guarded(tmp)
    test_copy_last(tmp)
    test_live_lifecycle(tmp)
    test_conference_lifecycle(tmp)
    test_meeting_guards_and_lifecycle(tmp)
    test_validated_combo()
    test_spawn_worker_failure(tmp)
    test_monitor_silence(tmp)
    test_monitor_max_duration(tmp)
    test_preload(tmp)
    test_quit(tmp)
    print("\nTOUS LES TESTS APP PASSENT")


if __name__ == "__main__":
    _run_all()
