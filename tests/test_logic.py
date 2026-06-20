"""Tests hors-ligne de la logique pure de Whisperty.

Ne nécessitent ni micro, ni modèle, ni GUI : les dépendances binaires
(sounddevice, pynput, pyperclip, pystray, PIL, faster_whisper) sont remplacées
par des doublures. Lançables tels quels (`python tests/test_logic.py`) ou via
pytest.
"""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from pathlib import Path

# --- racine du dépôt sur le PYTHONPATH ---------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --- doublures des dépendances binaires --------------------------------------
def _install_stubs() -> None:
    if "sounddevice" not in sys.modules:
        sd = types.ModuleType("sounddevice")
        sd.PortAudioError = type("PortAudioError", (Exception,), {})
        sys.modules["sounddevice"] = sd
    if "soundcard" not in sys.modules:
        # Doublure neutre : pas de sortie audio (suffit pour construire l'app).
        sc = types.ModuleType("soundcard")
        sc.all_speakers = lambda: []
        sc.default_speaker = lambda: (_ for _ in ()).throw(RuntimeError("no default"))
        sc.all_microphones = lambda include_loopback=False: []
        sc.get_microphone = lambda dev_id, include_loopback=False: None
        sys.modules["soundcard"] = sc


_install_stubs()


# =============================================================================
# 1) Configuration
# =============================================================================
def test_config_defaults_and_override(tmp_path: Path | None = None) -> None:
    import yaml

    from whisperty.config import Config

    base = tmp_path or Path(__file__).resolve().parent.parent
    # Défauts purs (fichier absent)
    cfg = Config.load(base / "config-inexistant.yaml")
    assert cfg.transcription.model == "small"
    assert cfg.hotkey.combo == "<ctrl>+<alt>+<space>"
    assert cfg.output.method == "paste"

    # Surcharge partielle + clé inconnue ignorée
    work = (tmp_path or Path(__file__).resolve().parent) / "tmp_cfg.yaml"
    work.write_text(
        yaml.safe_dump(
            {
                "transcription": {"model": "base", "device": "cuda", "inconnue": 1},
                "dictionary": {"path": "mots.txt"},
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(work)
    assert cfg.transcription.model == "base"
    assert cfg.transcription.device == "cuda"
    assert cfg.transcription.beam_size == 5  # défaut conservé
    # Résolution de chemin relative au dossier de config.yaml
    assert cfg.resolve("mots.txt") == (work.resolve().parent / "mots.txt")
    assert cfg.base_dir == work.resolve().parent
    work.unlink()
    print("[1] config : défauts + surcharge + clé inconnue + résolution chemin  OK")


# =============================================================================
# 2) Dictionnaire
# =============================================================================
def test_dictionary(tmp_path: Path | None = None) -> None:
    from whisperty.dictionary import apply_corrections, load_dictionary

    base = tmp_path or Path(__file__).resolve().parent
    dic = base / "tmp_dico.txt"
    dic.write_text(
        "# commentaire\nSCADA\nposte de transformation\n\nscada => SCADA\nge erre de => GRD\n",
        encoding="utf-8",
    )
    hotwords, replacements = load_dictionary(dic)
    assert "SCADA" in hotwords and "poste de transformation" in hotwords
    assert replacements["scada"] == "SCADA"
    assert replacements["ge erre de"] == "GRD"

    # Correction insensible à la casse, sur mots entiers
    out = apply_corrections("Le Scada et le ge erre de sont locaux.", replacements)
    assert "SCADA" in out and "GRD" in out
    # Pas de remplacement à l'intérieur d'un mot
    assert apply_corrections("descadar", replacements) == "descadar"
    # Dictionnaire absent → listes vides
    assert load_dictionary(base / "absent.txt") == ([], {})
    dic.unlink()
    print("[2] dictionnaire : chargement + corrections (casse, mots entiers)  OK")


# =============================================================================
# 3) Injection de texte (paste + type)
# =============================================================================
def _install_injection_stubs() -> tuple[list, dict]:
    copy_calls: list = []
    clip: dict = {"v": ""}

    pyperclip = types.ModuleType("pyperclip")
    pyperclip.copy = lambda t: (copy_calls.append(t), clip.__setitem__("v", t))[0]  # noqa: E731
    pyperclip.paste = lambda: clip["v"]
    sys.modules["pyperclip"] = pyperclip

    events: list = []

    class FakeController:
        def press(self, k):
            events.append(("press", k))

        def release(self, k):
            events.append(("release", k))

        def type(self, s):
            events.append(("type", s))

        @contextmanager
        def pressed(self, *keys):
            for k in keys:
                events.append(("press", k))
            try:
                yield
            finally:
                for k in reversed(keys):
                    events.append(("release", k))

    kb = types.ModuleType("pynput.keyboard")
    kb.Controller = FakeController
    kb.Key = types.SimpleNamespace(ctrl="CTRL", alt="ALT", shift="SHIFT")
    pynput = types.ModuleType("pynput")
    pynput.keyboard = kb
    sys.modules["pynput"] = pynput
    sys.modules["pynput.keyboard"] = kb
    return events, {"copy_calls": copy_calls, "clip": clip}


def test_injector_paste() -> None:
    events, state = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    state["clip"]["v"] = "ANCIEN"  # presse-papiers initial
    inj = TextInjector(OutputConfig(method="paste", restore_clipboard=True))
    inj.inject("Bonjour é à ç")

    # Le texte a bien été copié, puis l'ancien contenu restauré.
    assert state["copy_calls"] == ["Bonjour é à ç", "ANCIEN"], state["copy_calls"]
    assert state["clip"]["v"] == "ANCIEN"
    # Ctrl maintenu autour d'un press/release de 'v'
    assert ("press", "CTRL") in events
    assert ("press", "v") in events and ("release", "v") in events
    print("[3a] injector paste : copie + Ctrl+V + restauration presse-papiers  OK")


def test_injector_type() -> None:
    events, _ = _install_injection_stubs()
    from whisperty.config import OutputConfig
    from whisperty.injector import TextInjector

    inj = TextInjector(OutputConfig(method="type", type_delay=0.0))
    inj.inject("abé")
    typed = [e[1] for e in events if e[0] == "type"]
    assert typed == ["a", "b", "é"], typed
    print("[3b] injector type : frappe caractère par caractère (UTF-8)  OK")


# =============================================================================
# 4) Variantes de touches (double-tap) + smoke import de toute la chaîne
# =============================================================================
def _install_gui_stubs() -> None:
    """Doublures pystray + Pillow pour construire WhispertyApp/Tray sans GUI réelle."""
    if "pystray" not in sys.modules:
        pystray = types.ModuleType("pystray")

        class _Icon:
            def __init__(self, *a, **k):
                self.icon = None
                self.title = None

            def run(self):
                pass

            def stop(self):
                pass

        class _Menu:
            SEPARATOR = object()

            def __init__(self, *items):
                self.items = items

        class _MenuItem:
            def __init__(self, *a, **k):
                pass

        pystray.Icon, pystray.Menu, pystray.MenuItem = _Icon, _Menu, _MenuItem
        sys.modules["pystray"] = pystray

    if "PIL" not in sys.modules:
        PIL = types.ModuleType("PIL")
        image = types.ModuleType("PIL.Image")
        image.new = lambda *a, **k: object()
        draw = types.ModuleType("PIL.ImageDraw")

        class _Draw:
            def ellipse(self, *a, **k):
                pass

        draw.Draw = lambda *a, **k: _Draw()
        PIL.Image, PIL.ImageDraw = image, draw
        sys.modules["PIL"] = PIL
        sys.modules["PIL.Image"] = image
        sys.modules["PIL.ImageDraw"] = draw


def test_state_machine() -> None:
    import time

    import numpy as np

    _install_gui_stubs()
    _install_injection_stubs()
    from whisperty.app import WhispertyApp
    from whisperty.config import Config
    from whisperty.tray import TrayState

    cfg = Config()
    cfg.hotkey.mode = "push_to_talk"   # la surveillance n'applique que max_duration
    cfg.audio.max_duration = 9999      # désactive l'arrêt auto pendant le test

    app = WhispertyApp(cfg)

    class FakeRecorder:
        def __init__(self):
            self.started = 0
            self.stopped = 0
            self.current_level = 1.0

        def start(self):
            self.started += 1

        def stop(self):
            self.stopped += 1
            return np.zeros(0, dtype=np.float32)

    class FakeTranscriber:
        def load(self):
            pass

        def transcribe(self, audio, profile=None):
            return ""

    rec = FakeRecorder()
    app.recorder = rec
    app.transcriber = FakeTranscriber()

    assert app._state is TrayState.IDLE
    # IDLE -> RECORDING
    app.toggle()
    assert app._state is TrayState.RECORDING and rec.started == 1

    # toggle() est IGNORÉ pendant PROCESSING (cœur du correctif #1 de la 1re revue)
    app._state = TrayState.PROCESSING
    app.toggle()
    assert rec.started == 1 and app._state is TrayState.PROCESSING

    # RECORDING -> PROCESSING -> (async) IDLE
    app._state = TrayState.RECORDING
    app.toggle()
    assert rec.stopped >= 1
    for _ in range(40):
        if app._state is TrayState.IDLE:
            break
        time.sleep(0.05)
    assert app._state is TrayState.IDLE, app._state

    # Un second _stop_and_process() est un no-op (état déjà IDLE)
    before = rec.stopped
    app._stop_and_process()
    assert rec.stopped == before
    print("[5] machine à états : IDLE->REC->PROCESSING->IDLE + toggle ignoré en PROCESSING  OK")


def test_key_variants_and_imports() -> None:
    _install_injection_stubs()  # fournit un pynput.keyboard minimal
    import whisperty.app as app_mod  # importe config, recorder, transcriber, injector, tray, app

    fake_kb = types.SimpleNamespace(
        Key=types.SimpleNamespace(ctrl="C", ctrl_l="CL", ctrl_r="CR"),
        KeyCode=types.SimpleNamespace(from_char=lambda c: f"char:{c}"),
    )
    variants = app_mod.WhispertyApp._key_variants(fake_kb, "ctrl")
    assert variants == {"C", "CL", "CR"}, variants
    assert app_mod.WhispertyApp._key_variants(fake_kb, "x") == {"char:x"}
    print("[4] variantes de touches + import complet de la chaîne  OK")


# =============================================================================
# 6) Historique SQLite (V2)
# =============================================================================
def test_history(tmp_path: Path | None = None) -> None:
    from whisperty.history import History

    base = tmp_path or Path(__file__).resolve().parent
    db = base / "hist.db"
    if db.exists():
        db.unlink()

    h = History(db, max_entries=3, enabled=True)
    assert h.recent() == [] and h.last_text() is None  # base vide

    for i in range(5):
        h.add(f"texte {i}", source="dictée", app="Code.exe", model="small")
    rec = h.recent(10)
    assert len(rec) == 3, len(rec)                    # purge à max_entries
    assert rec[0].text == "texte 4"                   # plus récent en tête
    assert rec[0].app == "Code.exe" and rec[0].model == "small"
    assert h.last_text() == "texte 4"

    h.add("")                                          # texte vide ignoré
    assert len(h.recent(10)) == 3

    h.clear()
    assert h.recent() == []

    h2 = History(base / "off.db", enabled=False)       # désactivé = no-op
    h2.add("x")
    assert h2.recent() == [] and h2.last_text() is None
    assert not (base / "off.db").exists()              # aucun fichier créé si désactivé

    h.close()
    h2.close()
    db.unlink()
    print("[6] historique SQLite : add/recent/last_text/purge/clear + désactivé  OK")


# =============================================================================
# 7) Profils de contexte (V2)
# =============================================================================
def test_profiles() -> None:
    from whisperty.config import Config, ProfileDef, ProfilesConfig
    from whisperty.profiles import ProfileResolver

    cfg = Config()
    cfg.dictionary.enabled = False  # isole le test du dictionary.txt du dépôt
    cfg.profiles = ProfilesConfig(
        enabled=True,
        definitions=[
            ProfileDef(
                name="code", match=["code.exe", "devenv.exe"],
                initial_prompt="CONTEXTE CODE", hotwords=["commit"],
                corrections={"git ube": "GitHub"},
            ),
            ProfileDef(name="mail", match=["outlook.exe"], initial_prompt="CONTEXTE MAIL"),
        ],
    )
    resolver = ProfileResolver(cfg)

    # Correspondance (insensible à la casse) → profil "code"
    prof = resolver.for_app("Code.exe")
    assert prof is not None and prof.name == "code"
    assert prof.initial_prompt == "CONTEXTE CODE"
    assert "commit" in prof.hotwords
    assert prof.replacements["git ube"] == "GitHub"

    # Pas de correspondance → profil par défaut (hérite, prompt None)
    default = resolver.for_app("explorer.exe")
    assert default is not None and default.name == "(défaut)"
    assert default.initial_prompt is None

    # app inconnue/None sans match → défaut
    assert resolver.for_app(None).name == "(défaut)"

    # Profils désactivés → None (le transcripteur utilise ses propres défauts)
    assert ProfileResolver(Config()).for_app("Code.exe") is None
    print("[7] profils de contexte : match (casse), défaut, désactivé  OK")


# =============================================================================
# 8) Mode IA local — garde de confidentialité (V2)
# =============================================================================
def test_ai_local_guard() -> None:
    import json

    import whisperty.ai as ai_mod
    from whisperty.ai import LocalLLM, is_local_endpoint
    from whisperty.config import AIConfig

    # Garde : seuls les hôtes locaux sont reconnus.
    assert is_local_endpoint("http://localhost:11434/v1/chat/completions")
    assert is_local_endpoint("http://127.0.0.1:1234/v1/chat/completions")
    assert is_local_endpoint("http://[::1]:11434/v1/chat/completions")
    assert not is_local_endpoint("http://api.openai.com/v1/chat/completions")
    assert not is_local_endpoint("https://example.com/x")
    assert not is_local_endpoint("ftp://localhost/x")

    # Désactivé → texte inchangé, aucun appel réseau.
    assert LocalLLM(AIConfig(enabled=False)).refine("salut") == "salut"

    # Activé mais endpoint DISTANT → refus (confidentialité), texte conservé.
    remote = LocalLLM(AIConfig(enabled=True, endpoint="http://evil.example.com/v1/chat/completions"))
    assert remote.refine("texte secret") == "texte secret"

    # Activé + local + opener simulé → utilise la réponse, et n'appelle que localhost.
    captured: dict = {}

    class _FakeResp:
        def __init__(self, payload, url="http://localhost:11434/v1/chat/completions"):
            self._b = json.dumps(payload).encode("utf-8")
            self._url = url

        def read(self):
            return self._b

        def geturl(self):
            return self._url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": "Salut, ça va ?"}}]})

    original = ai_mod._OPENER
    ai_mod._OPENER = types.SimpleNamespace(open=fake_open)
    try:
        llm = LocalLLM(AIConfig(enabled=True, endpoint="http://localhost:11434/v1/chat/completions"))
        assert llm.refine("salut ca va") == "Salut, ça va ?"
        assert "localhost" in captured["url"]
        assert captured["payload"]["messages"][1]["content"] == "salut ca va"

        # Défense en profondeur : si l'URL finale n'est pas locale (redirection), on ignore.
        def fake_open_remote(req, timeout=None):
            return _FakeResp({"choices": [{"message": {"content": "NE DOIT PAS PASSER"}}]},
                             url="http://evil.example.com/x")

        ai_mod._OPENER = types.SimpleNamespace(open=fake_open_remote)
        assert llm.refine("secret") == "secret"
    finally:
        ai_mod._OPENER = original
    print("[8] IA locale : garde localhost + URL finale locale + désactivé + réponse  OK")


# =============================================================================
# 9) Transcripteur — overrides de profil + transcribe_file (V2, modèle simulé)
# =============================================================================
def test_transcriber_overrides(tmp_path: Path | None = None) -> None:
    import types as _types

    import numpy as np

    from whisperty.config import TranscriptionConfig
    from whisperty.profiles import ResolvedProfile
    from whisperty.transcriber import Transcriber

    base = tmp_path or Path(__file__).resolve().parent

    class FakeModel:
        def __init__(self):
            self.calls: list = []

        def transcribe(self, audio, language=None, beam_size=None,
                       initial_prompt=None, hotwords=None, vad_filter=None):
            self.calls.append({
                "audio": audio, "language": language,
                "initial_prompt": initial_prompt, "hotwords": hotwords,
            })
            seg = _types.SimpleNamespace(text="bonjour scada", start=0.0, end=1.5)
            info = _types.SimpleNamespace(language=language)
            return [seg], info

    cfg = TranscriptionConfig(initial_prompt="PROMPT BASE", language="fr")
    t = Transcriber(cfg, hotwords=["base"], replacements={"scada": "SCADA"})
    t._model = FakeModel()  # court-circuite load() (pas de faster-whisper requis)

    # Sans profil : défauts de l'instance + corrections du dictionnaire de base.
    out = t.transcribe(np.ones(10, dtype=np.float32))
    assert out == "bonjour SCADA", out
    call = t._model.calls[-1]
    assert call["initial_prompt"] == "PROMPT BASE"
    assert call["hotwords"] == "base"

    # Avec profil : surcharge prompt/langue/hotwords/corrections.
    prof = ResolvedProfile(
        name="code", initial_prompt="PROMPT CODE", language="en",
        hotwords=["commit", "merge"], replacements={"scada": "SCADA-2"},
    )
    out2 = t.transcribe(np.ones(10, dtype=np.float32), prof)
    assert out2 == "bonjour SCADA-2", out2
    call2 = t._model.calls[-1]
    assert call2["initial_prompt"] == "PROMPT CODE"
    assert call2["language"] == "en"
    assert call2["hotwords"] == "commit, merge"

    # transcribe_file : passe le chemin au modèle (décodage PyAV délégué).
    audio_file = base / "fake_audio.wav"
    audio_file.write_bytes(b"RIFF....WAVE")  # contenu factice : FakeModel ignore l'audio
    out3 = t.transcribe_file(audio_file)
    assert out3 == "bonjour SCADA", out3
    assert t._model.calls[-1]["audio"] == str(audio_file)

    # Fichier absent → FileNotFoundError explicite.
    try:
        t.transcribe_file(base / "absent_xyz.wav")
        raise AssertionError("FileNotFoundError attendue")
    except FileNotFoundError:
        pass
    audio_file.unlink()

    # transcribe_segments : segments HORODATÉS (start, end) + corrections par segment.
    segs = t.transcribe_segments(np.ones(10, dtype=np.float32))
    assert segs == [(0.0, 1.5, "bonjour SCADA")], segs
    assert t.transcribe_segments(np.zeros(0, dtype=np.float32)) == []  # audio vide
    print("[9] transcripteur : overrides + transcribe_file + transcribe_segments (simulé)  OK")


# =============================================================================
# 10) Robustesse du parsing config (YAML malformé ne doit jamais crasher) (V2)
# =============================================================================
def test_config_robustness(tmp_path: Path | None = None) -> None:
    import yaml

    from whisperty.config import Config, _build_profiles
    from whisperty.history import History
    from whisperty.profiles import ProfileResolver

    base = tmp_path or Path(__file__).resolve().parent

    # _build_profiles tolère tout YAML mal formé sans lever.
    assert _build_profiles(None).definitions == []
    assert _build_profiles({}).definitions == []
    # definitions non-liste (oubli des tirets) → vide + enabled conservé.
    pc = _build_profiles({"enabled": True, "definitions": {"name": "x"}})
    assert pc.enabled is True and pc.definitions == []
    # item non-dict ignoré ; match scalaire/None/int normalisé ; corrections non-dict → {}.
    pc = _build_profiles({
        "enabled": True,
        "definitions": [
            "pas un dict",
            {"name": "a", "match": "code.exe", "corrections": ["oops"]},
            {"name": "b", "match": None},
            {"name": "c", "match": 123, "hotwords": "mot"},
        ],
    })
    assert [d.name for d in pc.definitions] == ["a", "b", "c"]
    da, db, dc = pc.definitions
    assert da.match == ["code.exe"] and da.corrections == {}
    assert db.match == []
    assert dc.match == ["123"] and dc.hotwords == ["mot"]

    # ProfileResolver ne lève jamais sur ces définitions limites.
    cfg = Config()
    cfg.dictionary.enabled = False
    cfg.profiles = pc
    resolver = ProfileResolver(cfg)
    assert resolver.for_app("code.exe").name == "a"
    assert resolver.for_app("zzz.exe").name == "(défaut)"

    # Coercition numérique + démarrage sans crash malgré des valeurs mal typées.
    work = base / "tmp_robust.yaml"
    work.write_text(
        yaml.safe_dump({
            "history": {"max_entries": "pas_un_nombre"},  # invalide → défaut
            "audio": {"samplerate": "16000"},             # str coercible → int
            "transcription": {"beam_size": "abc"},        # invalide → défaut 5
            "ai": {"timeout": "12"},                       # str → float
        }),
        encoding="utf-8",
    )
    loaded = Config.load(work)
    assert loaded.audio.samplerate == 16000 and isinstance(loaded.audio.samplerate, int)
    assert loaded.transcription.beam_size == 5            # repli sur le défaut
    assert loaded.ai.timeout == 12.0
    assert loaded.history.max_entries == 200              # repli (coercition _build)
    History.from_config(loaded).close()                   # ne crashe pas au démarrage
    work.unlink()

    # Garde directe de History (constructeur appelé hors config).
    h = History(base / "never.db", max_entries="abc")
    assert h.max_entries == 200
    h.close()
    assert not (base / "never.db").exists()

    # Coercition des booléens depuis une chaîne (YAML quoté « "false" » ≠ truthy).
    work2 = base / "tmp_bool.yaml"
    work2.write_text(
        yaml.safe_dump({
            "conference": {"distinguish_speakers": "false", "enabled": "off"},
            "history": {"enabled": "yes"},
        }),
        encoding="utf-8",
    )
    loaded2 = Config.load(work2)
    assert loaded2.conference.distinguish_speakers is False
    assert loaded2.conference.enabled is False
    assert loaded2.history.enabled is True
    work2.unlink()
    print("[10] robustesse config : profils/numeriques/booleens malformes -> defauts surs  OK")


# =============================================================================
# 11) Transcription live — segmenteur VAD (logique pure) (V2)
# =============================================================================
def test_live_segmenter() -> None:
    import numpy as np

    from whisperty.live import _Segmenter

    sr = 16_000

    def blk(amp, dur=0.1):
        return np.full(int(sr * dur), amp, dtype=np.float32)

    # Fin d'utterance : 2 blocs de parole puis silence >= silence_duration → segment.
    seg = _Segmenter(sr, vad_threshold=0.01, silence_duration=0.3, max_segment=5.0)
    assert seg.push(blk(0.0)) is None          # silence initial, aucune parole
    assert seg.push(blk(0.5)) is None           # parole
    assert seg.push(blk(0.5)) is None
    assert seg.push(blk(0.0)) is None           # silence 0.1
    assert seg.push(blk(0.0)) is None           # 0.2
    out = seg.push(blk(0.0))                     # 0.3 → flush
    assert out is not None and out.size > 0
    assert seg.push(blk(0.0)) is None           # réinitialisé : silence pur → rien
    assert seg.flush_final() is None

    # Coupe forcée à max_segment, même sans silence.
    seg2 = _Segmenter(sr, 0.01, silence_duration=10.0, max_segment=0.5)
    for _ in range(4):
        assert seg2.push(blk(0.5)) is None       # 0.1..0.4
    assert seg2.push(blk(0.5)) is not None        # 0.5 → flush

    # Silence pur : jamais de segment (mémoire bornée), même au-delà de max_segment.
    seg3 = _Segmenter(sr, 0.01, silence_duration=10.0, max_segment=0.3)
    assert seg3.push(blk(0.0)) is None
    assert seg3.push(blk(0.0)) is None
    assert seg3.push(blk(0.0)) is None            # 0.3 atteint mais aucune parole → None

    # flush_final renvoie la parole en attente puis réinitialise.
    seg4 = _Segmenter(sr, 0.01, 10.0, 10.0)
    seg4.push(blk(0.5))
    assert seg4.flush_final() is not None
    assert seg4.flush_final() is None

    # Bloc vide (underrun) : ignoré sans effet ni erreur.
    seg5 = _Segmenter(sr, 0.01, 0.2, 5.0)
    assert seg5.push(np.zeros(0, dtype=np.float32)) is None
    assert seg5.push(blk(0.5)) is None              # la parole suivante reste prise en compte
    assert seg5.flush_final() is not None
    print("[11] live segmenteur VAD : utterance, max_segment, silence, bloc vide  OK")


# =============================================================================
# 12) Capture loopback — résolution de périphérique (soundcard simulé) (V2)
# =============================================================================
def test_loopback_resolve() -> None:
    from whisperty import loopback

    class FakeSpk:
        def __init__(self, name, ident):
            self.name = name
            self.id = ident

    class FakeMic:
        def __init__(self, name, ident):
            self.name = name
            self.id = ident
            self.isloopback = True

    spks = [FakeSpk("Speakers (ASUS)", "id-asus"), FakeSpk("Headset (Jabra)", "id-jabra")]
    fake = types.ModuleType("soundcard")
    fake.all_speakers = lambda: spks
    fake.default_speaker = lambda: spks[0]
    fake.all_microphones = lambda include_loopback=False: [FakeMic(s.name, s.id) for s in spks]
    fake.get_microphone = lambda dev_id, include_loopback=False: next(
        (FakeMic(s.name, s.id) for s in spks if s.id == dev_id), None
    )

    previous = sys.modules.get("soundcard")
    sys.modules["soundcard"] = fake
    try:
        listed = loopback.list_speakers()
        assert [d["name"] for d in listed] == ["Speakers (ASUS)", "Headset (Jabra)"]
        assert listed[0]["is_default"] is True and listed[1]["is_default"] is False

        assert loopback.resolve_loopback(None)[0] == "Speakers (ASUS)"   # défaut
        assert loopback.resolve_loopback(1)[0] == "Headset (Jabra)"      # index
        assert loopback.resolve_loopback("jabra")[0] == "Headset (Jabra)"  # sous-chaîne
        assert loopback.resolve_loopback("id-asus")[0] == "Speakers (ASUS)"  # id exact
        _, mic = loopback.resolve_loopback(None)
        assert mic.isloopback

        for bad in ["introuvable", 9, -1]:
            try:
                loopback.resolve_loopback(bad)
                raise AssertionError(f"LoopbackError attendue pour {bad!r}")
            except loopback.LoopbackError:
                pass
    finally:
        if previous is not None:
            sys.modules["soundcard"] = previous
        else:
            del sys.modules["soundcard"]
    print("[12] loopback : list_speakers + resolve (défaut/index/nom/id) + erreurs  OK")


# =============================================================================
# 13) Transcription live — boucle de consommation + transcript (V2)
# =============================================================================
def test_live_consume(tmp_path: Path | None = None) -> None:
    import numpy as np

    from whisperty.config import Config
    from whisperty.live import LiveTranscriber

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.live.block_duration = 0.1
    cfg.live.silence_duration = 0.2
    cfg.live.max_segment = 5.0
    cfg.live.vad_threshold = 0.01
    cfg.live.transcript_dir = "live_out"

    class FakeTr:
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio, profile=None):
            self.calls += 1
            return f"segment {self.calls}"

    finished: dict = {}
    lt = LiveTranscriber(cfg, FakeTr(), on_finished=lambda r: finished.update(r))
    path = lt._open_transcript("FakeDevice")

    sr = 16_000
    blocks = [
        np.full(1600, 0.5, np.float32),   # parole
        np.full(1600, 0.5, np.float32),   # parole
        np.full(1600, 0.0, np.float32),   # silence 0.1
        np.full(1600, 0.0, np.float32),   # silence 0.2 → flush
    ]
    state = {"i": 0}

    def record_fn(n):
        i = state["i"]
        state["i"] += 1
        if i >= len(blocks):
            lt._stop.set()
            return np.zeros(n, np.float32)
        return blocks[i]

    lt._consume(record_fn)
    lt._close_transcript()

    assert lt._segments == ["segment 1"], lt._segments
    content = path.read_text(encoding="utf-8")
    assert "segment 1" in content and "FakeDevice" in content

    lt._finish("FakeDevice", path)
    assert finished["device"] == "FakeDevice"
    assert finished["segments"] == 1
    assert finished["text"] == "segment 1"
    assert finished["error"] is None
    path.unlink()
    print("[13] live consume : segmentation -> transcription -> transcript + on_finished  OK")


# =============================================================================
# 14) Transcription live — robustesse de la boucle (2 segments, coupure périph.)
# =============================================================================
def test_live_consume_robust(tmp_path: Path | None = None) -> None:
    import numpy as np

    from whisperty.config import Config
    from whisperty.live import LiveTranscriber

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.live.block_duration = 0.1
    cfg.live.silence_duration = 0.2
    cfg.live.max_segment = 5.0
    cfg.live.vad_threshold = 0.01
    cfg.live.transcript_dir = "live_out2"

    class FakeTr:
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio, profile=None):
            self.calls += 1
            return f"seg{self.calls}"

    speech = np.full(1600, 0.5, np.float32)
    silence = np.full(1600, 0.0, np.float32)

    # (a) Deux utterances séparées par un silence → deux segments distincts.
    lt = LiveTranscriber(cfg, FakeTr())
    p1 = lt._open_transcript("Dev")
    seq_a = [speech, speech, silence, silence,   # flush 1
             speech, speech, silence, silence]   # flush 2
    st = {"i": 0}

    def rec_a(n):
        i = st["i"]
        st["i"] += 1
        if i >= len(seq_a):
            lt._stop.set()
            return silence
        return seq_a[i]

    lt._consume(rec_a)
    lt._close_transcript()
    assert lt._segments == ["seg1", "seg2"], lt._segments
    if p1 is not None:
        p1.unlink()

    # (b) Coupure du périphérique en plein milieu : flush_final émet la parole en cours,
    #     _error reste None (la rupture est gérée proprement dans _consume, pas dans _run).
    lt2 = LiveTranscriber(cfg, FakeTr())
    p2 = lt2._open_transcript("Dev2")
    seq_b = [speech, speech]
    st2 = {"i": 0}

    def rec_err(n):
        i = st2["i"]
        st2["i"] += 1
        if i < len(seq_b):
            return seq_b[i]
        raise OSError("périphérique retiré")

    lt2._consume(rec_err)
    lt2._close_transcript()
    assert lt2._segments == ["seg1"], lt2._segments
    assert lt2._error is None
    if p2 is not None:
        p2.unlink()
    print("[14] live consume robuste : 2 utterances -> 2 segments + coupure périphérique  OK")


# =============================================================================
# 15) Transcription live — COM initialisé sur le worker + repli sans périphérique
# =============================================================================
def test_live_com_init(tmp_path: Path | None = None) -> None:
    from whisperty import loopback
    from whisperty.config import Config
    from whisperty.live import LiveTranscriber

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.live.block_duration = 0.1
    cfg.live.transcript_dir = "live_com"

    # Trace l'initialisation COM faite par le worker (soundcard est ici une doublure
    # qui renvoie 0 sortie → resolve_loopback lève LoopbackError, repli propre).
    calls = {"n": 0}
    original = loopback.com_initialized

    @contextmanager
    def tracking():
        calls["n"] += 1
        with original():
            yield

    class FakeTr:
        def transcribe(self, audio, profile=None):
            return ""

    loopback.com_initialized = tracking
    try:
        finished: dict = {}
        lt = LiveTranscriber(cfg, FakeTr(), on_finished=lambda r: finished.update(r))
        lt.start(None)
        lt.wait(timeout=5.0)
        assert calls["n"] >= 1, "COM doit être initialisé sur le thread worker"
        assert finished.get("error"), "absence de sortie audio -> erreur propre attendue"
        assert "sortie audio" in finished["error"].lower()
    finally:
        loopback.com_initialized = original
    print("[15] live : COM initialisé sur le worker + repli sans périphérique  OK")


# =============================================================================
# 16) Réunion — mixage double-source (somme + normalisation anti-saturation)
# =============================================================================
def test_mix_streams() -> None:
    import numpy as np

    from whisperty.conference import mix_streams

    # Somme simple (pas de saturation).
    a = np.array([0.2, 0.2, 0.2], np.float32)
    b = np.array([0.1, 0.1, 0.1], np.float32)
    assert np.allclose(mix_streams([a, b]), [0.3, 0.3, 0.3])

    # Normalisation anti-saturation : somme 1.6 → crête ramenée à 1.0.
    loud = mix_streams([np.full(4, 0.8, np.float32), np.full(4, 0.8, np.float32)])
    assert abs(float(np.max(np.abs(loud))) - 1.0) < 1e-6

    # Troncature à la longueur du plus court.
    assert mix_streams([np.ones(5, np.float32), np.ones(3, np.float32)]).shape[0] == 3

    # Source unique (≤ 1) renvoyée inchangée ; cas vides.
    solo = np.array([0.5, -0.5], np.float32)
    assert np.allclose(mix_streams([solo]), solo)
    assert mix_streams([]).size == 0
    assert mix_streams([np.zeros(0, np.float32), None]).size == 0
    print("[16] réunion mix_streams : somme + normalisation + troncature + source unique  OK")


# =============================================================================
# 17) Réunion — formatage de l'export
# =============================================================================
def test_conference_format() -> None:
    from whisperty.conference import _transcript_header, format_segment_line

    assert format_segment_line(0, "salut") == "[00:00] salut"
    assert format_segment_line(75.4, "x") == "[01:15] x"
    assert format_segment_line(12, "x", speaker="Moi") == "[00:12] Moi : x"  # itération 2

    txt = _transcript_header("Speakers (ASUS)", "micro par défaut", "txt")
    assert "réunion" in txt.lower() and "Speakers (ASUS)" in txt
    md = _transcript_header("Spk", "mic", "md")
    assert md.startswith("# Transcription de réunion")
    print("[17] réunion format : ligne [MM:SS] (+ locuteur) + en-têtes txt/md  OK")


# =============================================================================
# 18) Réunion — boucle de consommation (mixage → segmentation → transcript)
# =============================================================================
def test_conference_consume(tmp_path: Path | None = None) -> None:
    import time

    import numpy as np

    from whisperty.config import Config
    from whisperty.conference import ConferenceTranscriber

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.conference.export_dir = "conf_out"
    cfg.conference.distinguish_speakers = False  # ce test cible le chemin MIXÉ (itération 1)
    cfg.conference.block_duration = 0.1
    cfg.conference.silence_duration = 0.2
    cfg.conference.max_segment = 5.0
    cfg.conference.vad_threshold = 0.01

    class FakeTr:
        def __init__(self):
            self.calls = 0
            self.last = None

        def transcribe(self, audio, profile=None):
            self.calls += 1
            self.last = audio
            return f"seg{self.calls}"

    tr = FakeTr()
    finished: dict = {}
    ct = ConferenceTranscriber(cfg, tr, on_finished=lambda r: finished.update(r))
    ct._active = {"mic", "system"}
    ct._t0 = time.monotonic()
    path = ct._open_transcript("FakeSys", "FakeMic")

    # Deux sources alignées (0,4 chacune → mix 0,8, sans saturation).
    speech = np.full(8000, 0.4, np.float32)
    ct._buffers["mic"].push(speech.copy())
    ct._buffers["system"].push(speech.copy())

    ct._stop.set()      # saute la boucle temps réel → vidage final déterministe
    ct._consume()
    ct._close_transcript()

    assert tr.calls == 1, tr.calls
    assert abs(float(np.max(np.abs(tr.last))) - 0.8) < 1e-5   # micro + système mixés
    content = path.read_text(encoding="utf-8")
    assert "seg1" in content and "réunion" in content.lower()

    ct._finish("FakeSys", path)
    assert finished["segments"] == 1 and finished["text"] == "seg1"
    assert finished["sources"] == ["mic", "system"]
    assert finished["error"] is None
    path.unlink()
    print("[18] réunion consume : mixage 2 sources -> segment -> transcript + on_finished  OK")


# =============================================================================
# 21) Assistant de réunion — détection de questions (heuristique) (V2)
# =============================================================================
def test_meeting_question_heuristic() -> None:
    from whisperty.meeting import looks_like_question

    assert looks_like_question("Jean, tu peux nous en dire plus ?")
    assert looks_like_question("Comment ça marche ?", "Jean") is False  # pas de prénom
    assert looks_like_question("Jean, comment ça marche ?", "Jean")
    assert not looks_like_question("Merci pour la présentation.")
    assert not looks_like_question("")
    assert looks_like_question("Est-ce que tu es d'accord Jean ?")
    print("[21] réunion : heuristique de détection de questions  OK")


# =============================================================================
# 22) Assistant de réunion — LLM meeting_is_question / meeting_reply (V2)
# =============================================================================
def test_meeting_llm() -> None:
    import json

    import whisperty.ai as ai_mod
    from whisperty.ai import LocalLLM
    from whisperty.config import AIConfig

    responses = iter(["OUI", "NON", "Oui, je peux m'en occuper demain."])

    class _FakeResp:
        def __init__(self, content):
            self._b = json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode("utf-8")

        def read(self):
            return self._b

        def geturl(self):
            return "http://localhost:11434/v1/chat/completions"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=None):
        return _FakeResp(next(responses))

    original = ai_mod._OPENER
    ai_mod._OPENER = types.SimpleNamespace(open=fake_open)
    try:
        llm = LocalLLM(AIConfig(enabled=True))
        assert llm.meeting_is_question("Jean, tu peux valider ?", "Jean", [])
        assert not llm.meeting_is_question("Question générale ?", "Jean", [])
        reply = llm.meeting_reply(
            "Jean, quand est-ce livrable ?",
            ["On parle du projet X."],
            "Chef de projet IT",
            "Réponds pour {user_name}. Contexte: {user_context}. {context}",
            "Jean",
        )
        assert reply == "Oui, je peux m'en occuper demain."
    finally:
        ai_mod._OPENER = original
    print("[22] réunion : meeting_is_question + meeting_reply (LLM simulé)  OK")


# =============================================================================
# 23) Assistant de réunion — traitement d'un segment (V2)
# =============================================================================
def test_meeting_assistant_segment(tmp_path: Path | None = None) -> None:
    from whisperty.config import Config, MeetingConfig
    from whisperty.meeting import MeetingAssistant

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.meeting = MeetingConfig(user_name="Jean", auto_inject=False)
    cfg.ai.enabled = True

    copied: list[str] = []
    notified: list[str] = []

    class FakeLLM:
        def __init__(self):
            self.cfg = cfg.ai

        def meeting_is_question(self, segment, user_name, context):
            return "Jean" in segment

        def meeting_reply(self, question, context, user_context, reply_prompt, user_name=""):
            return "Oui, c'est prévu pour vendredi."

    class FakeInjector:
        def inject(self, text):
            pass

        def copy_to_clipboard(self, text):
            copied.append(text)
            return True

    class FakeHistory:
        def add(self, *a, **k):
            pass

    ma = MeetingAssistant(
        cfg, object(), FakeLLM(), FakeInjector(),
        history=FakeHistory(), on_notify=notified.append,
    )
    ma._on_segment("12:00:00", "Jean, c'est pour quand ?")
    import time
    for _ in range(40):
        if copied:
            break
        time.sleep(0.05)
    assert copied == ["Oui, c'est prévu pour vendredi."], copied
    assert notified and "copiée" in notified[0].lower()
    print("[23] réunion : segment -> réponse copiée + notification  OK")


# =============================================================================
# 19) Réunion — robustesse : aucune source / alignement / famine / mono / queue finale
# =============================================================================
def test_conference_degradation(tmp_path: Path | None = None) -> None:
    import time

    import numpy as np

    from whisperty.config import Config
    from whisperty.conference import ConferenceTranscriber
    from whisperty.live import _Segmenter

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.conference.export_dir = "conf_deg"
    cfg.conference.distinguish_speakers = False  # plomberie de capture/mixage (itération 1)
    cfg.conference.block_duration = 0.1
    cfg.conference.silence_duration = 0.2
    cfg.conference.max_segment = 5.0
    cfg.conference.vad_threshold = 0.01

    class FakeTr:
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio, profile=None):
            self.calls += 1
            return f"s{self.calls}"

    def speech(n):
        return np.full(n, 0.4, np.float32)

    # (a) Aucune source active → drain renvoie None (pas de blocage, pas de crash).
    ct = ConferenceTranscriber(cfg, FakeTr())
    ct._active = set()
    assert ct._drain_mixed(8000) is None

    # (b) Alignement : l'excédent de tête de la source en avance (micro) est défaussé.
    ct = ConferenceTranscriber(cfg, FakeTr())
    ct._active = {"mic", "system"}
    ct._buffers["mic"].push(speech(12000))
    ct._buffers["system"].push(speech(8000))
    ct._align_sources()
    assert ct._aligned
    assert ct._buffers["mic"].available() == 8000 and ct._buffers["system"].available() == 8000

    # (c) Famine : une source muette est retirée après stall_limit ticks (mid-session).
    ct = ConferenceTranscriber(cfg, FakeTr())
    ct._active = {"mic", "system"}
    ct._buffers["mic"].push(speech(8000))  # « system » reste muet
    stalled = {"mic": 0, "system": 0}
    ct._drop_stalled_sources(stalled, 2)
    assert "system" in ct._active            # 1er tick : sursis
    ct._drop_stalled_sources(stalled, 2)
    assert ct._active == {"mic"}             # retirée → le mixage continuera sur le micro

    # (d) Mono-source : transcription sur la seule source survivante, sans gel.
    ct = ConferenceTranscriber(cfg, FakeTr())
    ct._active = {"mic"}
    ct._t0 = time.monotonic()
    path = ct._open_transcript("(aucune)", "FakeMic")
    ct._buffers["mic"].push(speech(8000))
    ct._stop.set()                           # saute la boucle live → vidage final déterministe
    ct._consume()
    ct._close_transcript()
    assert ct.transcriber.calls == 1
    if path is not None:
        path.unlink()

    # (e) Vidage final : la queue non alignée de la source la plus longue n'est pas perdue.
    ct = ConferenceTranscriber(cfg, FakeTr())
    ct._active = {"mic", "system"}
    ct._t0 = time.monotonic()
    path = ct._open_transcript("Sys", "Mic")
    ct._buffers["mic"].push(speech(16000))   # micro plus long (offset / dérive)
    ct._buffers["system"].push(speech(8000))
    ct._final_drain(_Segmenter(16000, 0.01, 0.2, 5.0), 1600)
    assert ct._buffers["mic"].available() == 0 and ct._buffers["system"].available() == 0
    ct._close_transcript()
    if path is not None:
        path.unlink()
    print("[19] réunion robustesse : aucune source / alignement / famine / mono / queue finale  OK")


# =============================================================================
# 20) Réunion — distinction par source (itération 2) : horodatage + entrelacement
# =============================================================================
def test_conference_distinct(tmp_path: Path | None = None) -> None:
    import time

    import numpy as np

    from whisperty.config import Config
    from whisperty.conference import ConferenceTranscriber

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.conference.export_dir = "conf_dist"
    cfg.conference.distinguish_speakers = True
    cfg.conference.block_duration = 0.1
    cfg.conference.silence_duration = 0.2
    cfg.conference.max_segment = 5.0
    cfg.conference.vad_threshold = 0.01
    cfg.conference.mic_label = "Moi"
    cfg.conference.system_label = "Interlocuteurs"

    class FakeTr2:
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio, profile=None):
            return "x"

        def transcribe_segments(self, audio, profile=None):
            self.calls += 1
            # Deux sous-segments dont le second à start > 0 : verrouille abs = chunk_start + start.
            return [(0.0, 1.0, f"a{self.calls}"), (2.0, 3.0, f"b{self.calls}")]

    # (a) Entrelacement chronologique : segments désordonnés → triés + étiquetés.
    ct = ConferenceTranscriber(cfg, FakeTr2())
    path = ct._open_transcript("Sys", "Mic")
    ct._segments = [
        (5.0, "Interlocuteurs", "B"),
        (1.0, "Moi", "A"),
        (3.0, "Interlocuteurs", "C"),
    ]
    finished: dict = {}
    ct._on_finished = lambda r: finished.update(r)
    ct._close_transcript()
    ct._finish("Sys", path)
    assert finished["text"] == "[00:01] Moi : A\n[00:03] Interlocuteurs : C\n[00:05] Interlocuteurs : B"
    content = path.read_text(encoding="utf-8")
    assert content.index("Moi : A") < content.index("Interlocuteurs : C") < content.index("Interlocuteurs : B")
    path.unlink()

    # (b) _emit_distinct : horodatage = (pushed - longueur)/SR + start du sous-segment.
    ct = ConferenceTranscriber(cfg, FakeTr2())
    ct._t0 = time.monotonic()
    audio = np.full(8000, 0.4, np.float32)  # 0,5 s
    ct._emit_distinct("mic", 16000, audio)      # chunk_start = (16000-8000)/16000 = 0,5 s
    s0, l0, t0_ = ct._segments[0]
    s1, l1, t1_ = ct._segments[1]
    assert l0 == "Moi" and abs(s0 - 0.5) < 1e-6 and t0_ == "a1"
    assert l1 == "Moi" and abs(s1 - 2.5) < 1e-6 and t1_ == "b1"   # 0,5 + start 2,0
    ct._emit_distinct("system", 48000, audio)   # chunk_start = (48000-8000)/16000 = 2,5 s
    s2, l2, _ = ct._segments[2]
    s3, _, _ = ct._segments[3]
    assert l2 == "Interlocuteurs" and abs(s2 - 2.5) < 1e-6
    assert abs(s3 - 4.5) < 1e-6                                   # 2,5 + start 2,0

    # (c) Chemin _consume_distinct complet (deux sources) via vidage final déterministe.
    ct = ConferenceTranscriber(cfg, FakeTr2())
    ct._active = {"mic", "system"}
    ct._t0 = time.monotonic()
    path = ct._open_transcript("Sys", "Mic")
    speech = np.full(8000, 0.4, np.float32)
    ct._buffers["mic"].push(speech.copy())
    ct._buffers["system"].push(speech.copy())
    ct._stop.set()
    ct._consume_distinct()
    ct._close_transcript()
    assert {lbl for _, lbl, _ in ct._segments} == {"Moi", "Interlocuteurs"}
    if path is not None:
        path.unlink()

    # (d) Mode distinction MONO-source (une seule source) : pas de KeyError, étiquette correcte.
    ct = ConferenceTranscriber(cfg, FakeTr2())
    ct._active = {"mic"}
    ct._t0 = time.monotonic()
    path = ct._open_transcript("(aucune)", "Mic")
    ct._buffers["mic"].push(np.full(8000, 0.4, np.float32))
    ct._stop.set()
    ct._consume_distinct()
    ct._close_transcript()
    assert ct._segments and all(lbl == "Moi" for _, lbl, _ in ct._segments)
    if path is not None:
        path.unlink()

    # (e) Export Markdown trié : en-tête md + lignes chronologiques.
    cfg.conference.export_format = "md"
    ct = ConferenceTranscriber(cfg, FakeTr2())
    path = ct._open_transcript("Sys", "Mic")
    ct._segments = [(2.0, "Interlocuteurs", "Z"), (1.0, "Moi", "A")]
    ct._on_finished = None
    ct._close_transcript()
    ct._finish("Sys", path)
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# Transcription de réunion")
    assert content.index("Moi : A") < content.index("Interlocuteurs : Z")
    if path is not None:
        path.unlink()
    cfg.conference.export_format = "txt"

    # (f) Libellés de locuteur configurables (propagés de bout en bout).
    cfg.conference.mic_label = "Alice"
    cfg.conference.system_label = "Bob"
    ct = ConferenceTranscriber(cfg, FakeTr2())
    ct._t0 = time.monotonic()
    ct._emit_distinct("mic", 8000, np.full(8000, 0.4, np.float32))
    ct._emit_distinct("system", 8000, np.full(8000, 0.4, np.float32))
    labels = {lbl for _, lbl, _ in ct._segments}
    assert labels == {"Alice", "Bob"}, labels
    print("[20] réunion distinction : horodatage, entrelacement, mono, md, libellés configurables  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_test_"))
    test_config_defaults_and_override(tmp)
    test_dictionary(tmp)
    test_injector_paste()
    test_injector_type()
    test_key_variants_and_imports()
    test_state_machine()
    test_history(tmp)
    test_profiles()
    test_ai_local_guard()
    test_transcriber_overrides(tmp)
    test_config_robustness(tmp)
    test_live_segmenter()
    test_loopback_resolve()
    test_live_consume(tmp)
    test_live_consume_robust(tmp)
    test_live_com_init(tmp)
    test_mix_streams()
    test_conference_format()
    test_conference_consume(tmp)
    test_conference_degradation(tmp)
    test_conference_distinct(tmp)
    test_meeting_question_heuristic()
    test_meeting_llm()
    test_meeting_assistant_segment(tmp)
    print("\nTOUS LES TESTS PASSENT")


if __name__ == "__main__":
    _run_all()
