"""Tests hors-ligne de l'orchestration (``whisperty.app.WhispertyApp``).

Couvre les chemins non testés par ``test_logic.py`` : traitement d'une dictée et
d'un fichier importé, modes live/réunion (transitions d'état +
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

    def recent(self, limit=10):
        return []

    def delete(self, entry_id):
        pass

    def clear(self):
        pass

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
    """Doublure de LiveTranscriber / ConferenceTranscriber."""

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
# 6) Validation du raccourci clavier
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

    app.quit()
    assert app._quitting is True
    assert app.live.stopped == 1 and app.live.waited == 1
    assert app.conference.stopped == 1 and app.conference.waited == 1
    assert app.history.closed is True
    assert app.tray.stopped is True

    # quit() idempotent : second appel ne ré-arrête rien.
    app.quit()
    assert app.live.stopped == 1
    print("[app 15] quit : arrêt ordonné de tous les sous-systèmes + idempotence  OK")


# =============================================================================
# 12) Flux live affiché : accumulation, révision monotone, borne mémoire
# =============================================================================
def test_live_display_buffer_capped(tmp_path: Path) -> None:
    from whisperty.app import _LIVE_DISPLAY_MAX_LINES

    app, _ = _make_app(tmp_path)
    rev0 = app.live_rev()
    app._append_live_line("premier segment")
    assert app.live_rev() == rev0 + 1
    assert app.live_transcript()["text"] == "premier segment"
    # Lignes vides/blanches ignorées (pas de bump de révision inutile).
    app._append_live_line("   ")
    assert app.live_rev() == rev0 + 1

    # Très longue session : l'affichage est borné aux N dernières lignes (la RAM et le
    # payload get_live_text restent constants ; transcript fichier/historique complets).
    for i in range(_LIVE_DISPLAY_MAX_LINES + 50):
        app._append_live_line(f"segment {i}")
    text = app.live_transcript()["text"]
    lines = text.splitlines()
    assert len(lines) == _LIVE_DISPLAY_MAX_LINES
    assert lines[-1] == f"segment {_LIVE_DISPLAY_MAX_LINES + 49}"  # les plus récentes
    assert "premier segment" not in lines                          # les plus anciennes évincées

    # Reset (nouveau live/réunion) : flux vidé + révision bumpée (re-fetch JS forcé).
    rev_before = app.live_rev()
    app._reset_live_transcript()
    assert app.live_rev() == rev_before + 1
    assert app.live_transcript()["text"] == ""
    print("[app 16] flux live affiché : accumulation + borne mémoire + reset  OK")


# =============================================================================
# 13) Renommage en session : réémission du flux + auto-réparation (US-12)
# =============================================================================
class FakeConference:
    """Doublure de ConferenceTranscriber : rend ses lignes depuis les CLÉS stockées.

    ``hook`` (optionnel) est appelé APRÈS le calcul des lignes et avant leur retour :
    il simule un évènement concurrent (renommage, note, segment) survenant pendant un
    rendu, c'est-à-dire entre l'instantané et sa publication.
    """

    def __init__(self):
        self.segments: list[tuple[str, str, str]] = []   # (« MM:SS », clé, texte)
        self.labels = {"spk:0": "Locuteur 1"}
        self.hook = None
        self.note_hook = None

    def rename_speaker(self, key, name):
        if key not in self.labels:
            return False
        self.labels[key] = name
        return True

    def add_note(self, text, stamp=None):
        stamp = stamp or "00:00"
        self.segments.append((stamp, "Note", text))
        if self.note_hook is not None:   # évènement concurrent APRÈS le stockage
            self.note_hook()
        return f"[{stamp}] Note : {text}"

    def segments_rev(self):
        # Rien n'est jamais retiré de `segments` : sa taille suffit comme version.
        return len(self.segments)

    def render_snapshot(self):
        # Comme _label_for : une clé hors registre (« Note », étiquette de source) est
        # son propre libellé. Lignes ET version prises AVANT le hook (le vrai transcriber
        # les lit sous le même verrou) : ce que le hook ajoute rend l'instantané périmé.
        lines = [f"[{s}] {self.labels.get(k, k)} : {t}" for s, k, t in self.segments]
        version = len(self.segments)
        if self.hook is not None:
            self.hook()
        return lines, version

    def render_lines(self):
        return self.render_snapshot()[0]


def _make_conference_app(tmp: Path):
    """App en session RÉUNION (état CONFERENCE) avec un transcriber doublure.

    L'état importe : `rename_speaker` et `add_note` le vérifient avant d'agir.
    """
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp)
    conf = FakeConference()
    app.conference = conf
    app._state = TrayState.CONFERENCE
    return app, conf


def test_rename_speaker_live_repair(tmp_path: Path) -> None:
    app, conf = _make_conference_app(tmp_path)

    conf.segments.append(("00:01", "spk:0", "bonjour"))
    app._on_conference_segment("[00:01] Locuteur 1 : bonjour", "bonjour")
    assert app.live_transcript()["text"] == "[00:01] Locuteur 1 : bonjour"

    # Renommage : réémission immédiate (retour visuel même sans nouveau segment)…
    assert app.rename_speaker("spk:0", "Marie")["ok"] is True
    assert app.live_transcript()["text"] == "[00:01] Marie : bonjour"
    assert app._live_repair == 1        # …et auto-réparation ARMÉE (course possible)

    # Segment suivant : le worker repart du rendu COMPLET au lieu d'ajouter — une ligne
    # perdue ou dupliquée par la course rename/append est corrigée ici.
    conf.segments.append(("00:04", "spk:0", "suite"))
    app._on_conference_segment("[00:04] Marie : suite", "suite")
    assert app.live_transcript()["text"].splitlines() == [
        "[00:01] Marie : bonjour", "[00:04] Marie : suite",
    ]
    assert app.live_transcript()["stamps"] == ["00:01", "00:04"]
    assert app._live_repair == 0        # désarmée : un seul re-rendu par renommage

    # Désarmée → simple ajout, SANS re-rendu complet : les lignes déjà affichées gardent
    # leur libellé, seule la nouvelle porte le libellé courant.
    conf.labels["spk:0"] = "Marie D"        # sans rename_speaker : pas d'armement
    conf.segments.append(("00:07", "spk:0", "fin"))
    app._on_conference_segment("[00:07] Marie D : fin", "fin")
    lines = app.live_transcript()["text"].splitlines()
    assert lines[0] == "[00:01] Marie : bonjour" and lines[-1] == "[00:07] Marie D : fin"

    # Locuteur inconnu / hors diarisation : ni réémission ni armement.
    rev = app.live_rev()
    assert app.rename_speaker("spk:9", "Paul")["ok"] is False
    assert app.live_rev() == rev and app._live_repair == 0
    print("[app 17] renommage en session : réémission + auto-réparation au segment suivant  OK")


def test_rename_speaker_repair_superseded(tmp_path: Path) -> None:
    """Renommage concurrent PENDANT la réparation : le rendu obsolète est abandonné."""
    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:01", "spk:0", "bonjour"))
    app._on_conference_segment("[00:01] Locuteur 1 : bonjour", "bonjour")
    app.rename_speaker("spk:0", "Marie")        # arme la réparation

    # Le segment suivant déclenche la réparation ; un second renommage (thread du pont)
    # survient pendant son rendu et publie des libellés PLUS FRAIS.
    conf.segments.append(("00:04", "spk:0", "suite"))

    def concurrent():
        conf.hook = None                        # one-shot (pas de récursion)
        app.rename_speaker("spk:0", "Marie Dupont")

    conf.hook = concurrent
    app._on_conference_segment("[00:04] Marie : suite", "suite")

    # Le rendu obsolète (« Marie ») est écarté ; le segment courant n'est pas dupliqué
    # (le rendu du renommage concurrent le contenait déjà).
    assert app.live_transcript()["text"].splitlines() == [
        "[00:01] Marie Dupont : bonjour", "[00:04] Marie Dupont : suite",
    ]
    assert app._live_repair          # toujours armée → réparation au segment suivant
    print("[app 18] réparation supplantée par un renommage concurrent : rendu frais conservé  OK")


def test_rename_reemit_does_not_drop_segment(tmp_path: Path) -> None:
    """Segment reçu PENDANT le rendu d'un renommage : la réémission ne l'écrase pas."""
    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:01", "spk:0", "bonjour"))
    app._on_conference_segment("[00:01] Locuteur 1 : bonjour", "bonjour")

    # Le worker transcrit « suite » pendant que le renommage rend le flux : le rendu du
    # renommage, pris AVANT, ne contient pas cette ligne — le publier tel quel
    # l'effacerait, et le compteur désarmé par le worker interdirait toute réparation.
    def concurrent():
        conf.hook = None
        conf.segments.append(("00:04", "spk:0", "suite"))
        app._on_conference_segment("[00:04] Marie : suite", "suite")

    conf.hook = concurrent
    assert app.rename_speaker("spk:0", "Marie")["ok"] is True

    assert app.live_transcript()["text"].splitlines() == [
        "[00:01] Marie : bonjour", "[00:04] Marie : suite",
    ]
    print("[app 19] renommage concurrent d'un segment : aucune ligne écrasée  OK")


def test_note_during_repair_kept(tmp_path: Path) -> None:
    """Note ajoutée pendant une réparation : ni perdue, ni dupliquée."""
    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:01", "spk:0", "bonjour"))
    app._on_conference_segment("[00:01] Locuteur 1 : bonjour", "bonjour")
    app.rename_speaker("spk:0", "Marie")            # arme la réparation

    # La note arrive du pont GUI pendant le rendu de réparation du segment suivant :
    # elle allonge le flux (sans le republier), donc le segment reste à ajouter.
    conf.segments.append(("00:04", "spk:0", "suite"))

    def concurrent():
        conf.hook = None
        assert app.add_note("budget", "00:05")["ok"] is True

    conf.hook = concurrent
    app._on_conference_segment("[00:04] Marie : suite", "suite")

    lines = app.live_transcript()["text"].splitlines()
    assert lines.count("[00:05] Note : budget") == 1     # ni doublon…
    assert lines.count("[00:04] Marie : suite") == 1     # …ni perte
    assert app._live_repair                              # armé → ordre remis au suivant
    print("[app 20] note concurrente d'une réparation : conservée une seule fois  OK")


def test_reemit_aborts_on_concurrent_append(tmp_path: Path) -> None:
    """Ligne ajoutée pendant un rendu : la publication est abandonnée puis réessayée.

    Fenêtre étroite mais réelle : une note s'inscrit dans ``_segments`` PUIS dans le flux
    affiché, et n'arme l'auto-réparation qu'ensuite. Une publication qui ne garderait que
    le jeton de renommage écraserait la ligne pendant cet intervalle.
    """
    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:01", "spk:0", "bonjour"))
    app._on_conference_segment("[00:01] Locuteur 1 : bonjour", "bonjour")

    def concurrent():
        conf.hook = None
        conf.segments.append(("00:05", "Note", "budget"))       # stockée…
        app._append_live_line("[00:05] Note : budget", "00:05")  # …puis affichée

    conf.hook = concurrent
    assert app.rename_speaker("spk:0", "Marie")["ok"] is True

    lines = app.live_transcript()["text"].splitlines()
    assert lines.count("[00:05] Note : budget") == 1
    assert lines.count("[00:01] Marie : bonjour") == 1
    print("[app 21] ajout concurrent d'une ligne pendant un rendu : ni écrasé ni dupliqué  OK")


def test_note_not_duplicated_by_concurrent_render(tmp_path: Path) -> None:
    """Note publiée par un rendu concurrent : elle n'est pas ajoutée une seconde fois."""
    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:01", "spk:0", "bonjour"))
    app._on_conference_segment("[00:01] Locuteur 1 : bonjour", "bonjour")

    # Entre l'entrée de la note dans `_segments` et son affichage, un renommage publie un
    # rendu complet — qui la contient déjà. L'ajouter à la main la dupliquerait.
    def after_store():
        conf.note_hook = None
        app.rename_speaker("spk:0", "Marie")

    conf.note_hook = after_store
    assert app.add_note("budget", "00:05")["ok"] is True

    lines = app.live_transcript()["text"].splitlines()
    assert lines.count("[00:05] Note : budget") == 1
    assert lines.count("[00:01] Marie : bonjour") == 1
    print("[app 22] note déjà publiée par un rendu concurrent : pas de doublon  OK")


def test_publish_rejects_stale_source(tmp_path: Path) -> None:
    """Rendu pris AVANT l'arrivée d'un segment : publication refusée.

    Interleaving que les tests ci-dessus n'atteignent pas (l'évènement concurrent y touche
    toujours l'AFFICHAGE) : ici seule la SOURCE bouge entre le rendu et sa publication.
    Publier effacerait le segment de la tuile, et le worker qui le traite conclurait
    « déjà republié » — la ligne resterait invisible jusqu'au segment suivant.
    """
    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:01", "spk:0", "bonjour"))

    token, rev, _render = app._live_generation()
    lines, source_rev = conf.render_snapshot()          # instantané du renommage…
    conf.segments.append(("00:04", "spk:0", "suite"))   # …puis un segment arrive
    assert app._publish_live_lines(lines, token, rev, source_rev, disarm=False) is False

    # Le rendu repris après coup, lui, publie (source de nouveau alignée).
    lines, source_rev = conf.render_snapshot()
    assert app._publish_live_lines(lines, token, rev, source_rev, disarm=False) is True
    assert app.live_transcript()["text"].splitlines()[-1] == "[00:04] Locuteur 1 : suite"
    print("[app 23] publication refusée si la source a bougé pendant le rendu  OK")


def test_append_skipped_after_full_render(tmp_path: Path) -> None:
    """Chemin NON armé : un rendu complet publié entre-temps évite le doublon."""
    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:30", "spk:0", "suite"))
    token, rev, render = app._live_generation()         # instantané du worker

    # Un renommage publie un rendu complet avant que le worker n'ajoute sa ligne : ce
    # rendu contient déjà le segment (stocké avant le callback), avec le libellé À JOUR.
    conf.labels["spk:0"] = "Marie"
    app._arm_live_repair()
    token2, rev2, _ = app._live_generation()
    lines, source_rev = conf.render_snapshot()
    assert app._publish_live_lines(lines, token2, rev2, source_rev, disarm=False) is True

    assert app._append_live_line("[00:30] Locuteur 1 : suite", "00:30", expect_render=render) is False
    assert app.live_transcript()["text"].splitlines() == ["[00:30] Marie : suite"]
    assert (token, rev) != (token2, rev2)               # garde-fou du scénario
    print("[app 24] chemin non armé : pas de doublon après un rendu complet concurrent  OK")


def test_rename_speaker_requires_session(tmp_path: Path) -> None:
    """Renommage hors session refusé : il republierait la réunion précédente."""
    from whisperty.tray import TrayState

    app, conf = _make_conference_app(tmp_path)
    conf.segments.append(("00:01", "spk:0", "bonjour"))
    app._on_conference_segment("[00:01] Locuteur 1 : bonjour", "bonjour")

    # Fin de réunion : le diariseur n'est pas remis à zéro, le panneau peut rester
    # affiché. Un clic tardif ne doit RIEN republier (a fortiori si un live a démarré).
    app._state = TrayState.IDLE
    rev = app.live_rev()
    assert app.rename_speaker("spk:0", "Marie")["ok"] is False
    assert app.live_rev() == rev
    assert app.live_transcript()["text"] == "[00:01] Locuteur 1 : bonjour"
    assert conf.labels["spk:0"] == "Locuteur 1"         # registre intact
    print("[app 25] renommage hors session : refusé, flux intact  OK")


# =============================================================================
# 14) Journaux : aucune métadonnée personnelle au niveau expédié
# =============================================================================
def test_import_logs_without_metadata(tmp_path: Path) -> None:
    import logging

    from tests.conftest import capture_logs
    from whisperty.tray import TrayState

    app, _ = _make_app(tmp_path)
    secret = str(tmp_path / "Patients Dupont" / "consultation.wav")

    with capture_logs() as logs:
        # 1) Import réussi : longueur au niveau expédié, nom de fichier en DEBUG.
        app._state = TrayState.PROCESSING
        app._process_file(secret)
        shipped = " | ".join(logs.messages(logging.INFO))
        assert "consultation.wav" not in shipped and "Dupont" not in shipped
        assert "15 caractères" in shipped                       # « texte transcrit »
        assert any("consultation.wav" in m for m in logs.messages(logging.DEBUG))

        # 2) Fichier introuvable : le message d'erreur porte le CHEMIN COMPLET.
        logs.clear()
        def missing(path):
            raise FileNotFoundError(f"Fichier audio introuvable : {path}")
        app.transcriber.transcribe_file = missing
        app._state = TrayState.PROCESSING
        app._process_file(secret)
        shipped = " | ".join(logs.messages(logging.INFO))
        assert "consultation.wav" not in shipped and "Dupont" not in shipped
        assert any("consultation.wav" in m for m in logs.messages(logging.DEBUG))

        # 3) Erreur inattendue (fichier corrompu) : la trace cite le chemin (PyAV) et
        #    reste en DEBUG ; le type d'erreur, lui, reste actionnable au niveau expédié.
        logs.clear()
        def boom(path):
            raise ValueError(f"données invalides : {path}")
        app.transcriber.transcribe_file = boom
        app._state = TrayState.PROCESSING
        app._process_file(secret)
        shipped = " | ".join(logs.messages(logging.INFO))
        assert "consultation.wav" not in shipped and "Dupont" not in shipped
        assert "ValueError" in shipped
    print("[app 26] journaux : ni nom ni chemin du fichier importé au niveau expédié  OK")


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
    test_validated_combo()
    test_spawn_worker_failure(tmp)
    test_monitor_silence(tmp)
    test_monitor_max_duration(tmp)
    test_preload(tmp)
    test_quit(tmp)
    test_live_display_buffer_capped(tmp)
    test_rename_speaker_live_repair(tmp)
    test_rename_speaker_repair_superseded(tmp)
    test_rename_reemit_does_not_drop_segment(tmp)
    test_note_during_repair_kept(tmp)
    test_reemit_aborts_on_concurrent_append(tmp)
    test_note_not_duplicated_by_concurrent_render(tmp)
    test_publish_rejects_stale_source(tmp)
    test_append_skipped_after_full_render(tmp)
    test_rename_speaker_requires_session(tmp)
    test_import_logs_without_metadata(tmp)
    print("\nTOUS LES TESTS APP PASSENT")


if __name__ == "__main__":
    _run_all()
