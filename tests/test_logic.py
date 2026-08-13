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
def test_config_defaults_and_override(tmp_path: Path) -> None:
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
def test_dictionary(tmp_path: Path) -> None:
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
        draw = types.ModuleType("PIL.ImageDraw")

        class _Img:
            # Image factice permissive : toute méthode (paste/resize/alpha_composite…)
            # est un no-op renvoyant un _Img, pour que tray._make_image s'exécute sans
            # Pillow réel (dessin du logo non vérifié ici — c'est de la logique pure).
            def __getattr__(self, name):
                return lambda *a, **k: self

        class _Draw:
            # Toute primitive de dessin (ellipse, rounded_rectangle, line…) est ignorée.
            def __getattr__(self, name):
                return lambda *a, **k: None

        image.new = lambda *a, **k: _Img()
        image.LANCZOS = image.BILINEAR = 1  # constantes de rééchantillonnage (valeur ignorée)
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
def test_history(tmp_path: Path) -> None:
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
    import os
    import urllib.request

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

    # Garde anti-proxy (contrainte cardinale) : l'opener force la connexion DIRECTE.
    # On force un proxy d'environnement et on vérifie qu'AUCUN handler de proxy actif
    # n'est enregistré — sans ProxyHandler({}) explicite, urllib capterait ce proxy
    # (variables d'environnement, registre WinINET) et le texte dicté transiterait par
    # un hôte potentiellement distant malgré la garde localhost. NB : un ProxyHandler
    # au dict vide n'expose aucune méthode *_open, il n'apparaît donc pas dans .handlers.
    saved_env = {k: os.environ.get(k) for k in ("http_proxy", "https_proxy")}
    os.environ["http_proxy"] = "http://proxy.evil.example.com:8080"
    os.environ["https_proxy"] = "http://proxy.evil.example.com:8080"
    try:
        opener = ai_mod._build_opener()
        active_proxies = {
            scheme: url
            for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)
            for scheme, url in h.proxies.items()
        }
        assert not active_proxies, f"proxy actif dans l'opener IA : {active_proxies}"
        # Et le refus de redirection reste bien en place (défense complémentaire).
        assert any(isinstance(h, ai_mod._NoRedirectHandler) for h in opener.handlers)
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

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

        # Réponses malformées d'un serveur local non conforme : jamais d'exception,
        # texte brut conservé ("choices" vide, nœud non-dict, message absent).
        for payload in ({"choices": []}, {"choices": [None]}, {"choices": [{}]}):
            ai_mod._OPENER = types.SimpleNamespace(
                open=lambda req, timeout=None, p=payload: _FakeResp(p)
            )
            assert llm.refine("texte brut") == "texte brut"
    finally:
        ai_mod._OPENER = original
    print("[8] IA locale : garde localhost + anti-proxy + URL finale locale + désactivé + réponse  OK")


# =============================================================================
# 9) Transcripteur — overrides de profil + transcribe_file (V2, modèle simulé)
# =============================================================================
def test_transcriber_overrides(tmp_path: Path) -> None:
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
def test_config_robustness(tmp_path: Path) -> None:
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

    # Racine YAML non-mapping (liste/chaîne valides YAML) → défauts sûrs, pas de crash.
    work3 = base / "tmp_non_mapping.yaml"
    work3.write_text("- juste\n- une liste\n", encoding="utf-8")
    loaded3 = Config.load(work3)
    assert loaded3.transcription.model == "small"          # défauts intacts
    assert loaded3.hotkey.combo == "<ctrl>+<alt>+<space>"
    work3.unlink()

    # Section de premier niveau inconnue (typo « hotkeys: ») → ignorée sans crash,
    # les sections valides du même fichier restent prises en compte.
    work4 = base / "tmp_unknown_section.yaml"
    work4.write_text(
        "hotkeys:\n  combo: typo\naudio:\n  max_duration: 30\n", encoding="utf-8"
    )
    loaded4 = Config.load(work4)
    assert loaded4.hotkey.combo == "<ctrl>+<alt>+<space>"  # la typo n'écrase rien
    assert loaded4.audio.max_duration == 30.0
    work4.unlink()
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
def test_live_consume(tmp_path: Path) -> None:
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
def test_live_consume_robust(tmp_path: Path) -> None:
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
def test_live_com_init(tmp_path: Path) -> None:
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
def test_conference_consume(tmp_path: Path) -> None:
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
# 19) Réunion — robustesse : aucune source / alignement / famine / mono / queue finale
# =============================================================================
def test_conference_degradation(tmp_path: Path) -> None:
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
def test_conference_distinct(tmp_path: Path) -> None:
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


# =============================================================================
# 26) Diarisation des locuteurs (UC-18) — logique pure
# =============================================================================
def test_diarization_logic() -> None:
    import numpy as np

    from whisperty.config import SpeakerDiarizationConfig
    from whisperty.diarization import (
        Diarizer,
        SpeakerRegistry,
        cosine_similarity,
        speaker_embedding,
    )

    v = lambda *xs: np.array(xs, dtype=np.float32)  # noqa: E731

    # (a) Registre : clustering par source, numérotation GLOBALE, seuil de similarité.
    reg = SpeakerRegistry(similarity_threshold=0.9, max_speakers=3, label_prefix="Locuteur")
    k1 = reg.assign("mic", v(1, 0, 0))          # 1er locuteur → spk:0
    k2 = reg.assign("mic", v(0.98, 0.2, 0))     # proche → même locuteur
    k3 = reg.assign("mic", v(0, 1, 0))          # orthogonal → nouveau locuteur
    assert k1 == k2 == "spk:0", (k1, k2)
    assert k3 == "spk:1"
    assert reg.label("spk:0") == "Locuteur 1" and reg.label("spk:1") == "Locuteur 2"
    # Même vecteur mais AUTRE source → cluster distinct (distinction par source), numéro global.
    k4 = reg.assign("system", v(1, 0, 0))
    assert k4 == "spk:2", k4
    assert {s["key"] for s in reg.speakers()} == {"spk:0", "spk:1", "spk:2"}

    # (b) Renommage (FR-31) : rétroactif via le libellé ; nom vide = retour à l'auto ; clé inconnue.
    assert reg.rename("spk:0", "Alice") is True
    assert reg.label("spk:0") == "Alice"
    assert [s for s in reg.speakers() if s["key"] == "spk:0"][0]["label"] == "Alice"
    assert reg.rename("spk:0", "  ") is True and reg.label("spk:0") == "Locuteur 1"
    assert reg.rename("spk:99", "X") is False and reg.rename("pasunecle", "X") is False

    # (c) Plafond max_speakers PAR source : jamais de nouvel id au-delà, rattachement au plus proche.
    capped = SpeakerRegistry(similarity_threshold=0.9, max_speakers=2, label_prefix="L")
    capped.assign("mic", v(1, 0, 0))
    capped.assign("mic", v(0, 1, 0))
    k_cap = capped.assign("mic", v(0, 0, 1))    # 3e voix, plafond 2 atteint
    assert k_cap in ("spk:0", "spk:1") and len(capped.speakers()) == 2, k_cap

    # (d) Diarizer : repli gracieux (BR-08) quand l'empreinte est indisponible/trop courte.
    fallback = Diarizer(
        SpeakerDiarizationConfig(enabled=True, min_segment=1.0),
        sample_rate=16000, embed_fn=lambda a, sr: None,
    )
    assert fallback.identify(np.ones(16000, np.float32), "mic", "Moi") == "Moi"   # embedding None
    assert fallback.identify(np.ones(100, np.float32), "mic", "Moi") == "Moi"     # trop court
    ok = Diarizer(
        SpeakerDiarizationConfig(min_segment=0.0), sample_rate=16000,
        embed_fn=lambda a, sr: v(1, 0),
    )
    assert ok.identify(np.ones(16000, np.float32), "mic", "Moi") == "spk:0"
    assert ok.label("spk:0").startswith("Locuteur") and ok.label("Moi") == "Moi"

    # (e) Empreinte MFCC réelle : deux « voix » distinctes sont moins proches qu'une même voix ;
    #     silence / segment trop court → None (repli). 100 % local, déterministe.
    rng = np.random.default_rng(0)

    def voice(freqs, n=16000):
        t = np.arange(n) / 16000.0
        sig = sum(np.sin(2 * np.pi * f * t) for f in freqs)
        sig = sig / np.max(np.abs(sig)) * 0.5
        return (sig + 0.001 * rng.standard_normal(n)).astype(np.float32)

    a1 = speaker_embedding(voice([150, 300, 450]))
    a2 = speaker_embedding(voice([150, 300, 450]))
    b1 = speaker_embedding(voice([230, 460, 690]))
    assert a1 is not None and a2 is not None and b1 is not None
    assert cosine_similarity(a1, a2) > cosine_similarity(a1, b1)
    assert speaker_embedding(np.zeros(16000, np.float32)) is None       # silence
    assert speaker_embedding(np.ones(100, np.float32)) is None          # trop court
    print("[26] diarisation UC-18 : registre, numérotation globale, renommage, plafond, repli, empreinte  OK")


# =============================================================================
# 27) Diarisation en réunion (UC-18) — intégration ConferenceTranscriber
# =============================================================================
def test_conference_diarization(tmp_path: Path) -> None:
    import queue
    import time

    import numpy as np

    from whisperty.config import Config
    from whisperty.conference import ConferenceTranscriber
    from whisperty.diarization import Diarizer

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.conference.export_dir = "conf_diar"
    cfg.conference.distinguish_speakers = True
    cfg.conference.speaker_diarization.enabled = True
    cfg.conference.speaker_diarization.min_segment = 0.0
    cfg.conference.speaker_diarization.similarity_threshold = 0.75

    class FakeTr:
        def transcribe(self, audio, profile=None):
            return "x"

        def transcribe_segments(self, audio, profile=None):
            return [(0.0, 1.0, "phrase")]

    # Empreinte contrôlée par le signe de l'audio : > 0 → voix A, < 0 → voix B, 0 → None (repli).
    def fake_embed(audio, sr):
        m = float(np.mean(audio))
        if abs(m) < 1e-9:
            return None
        return np.array([1.0, 0.0], np.float32) if m > 0 else np.array([0.0, 1.0], np.float32)

    def drive(ct):
        """Draine la file de diarisation de façon déterministe (sentinelle + worker synchrone).

        File, diariseur et jeton de génération passés en arguments, comme le fait
        _consume_distinct (protection anti-worker-orphelin)."""
        ct._diar_queue.put(None)
        ct._diar_loop(ct._diar_queue, ct._diar, ct._session_gen)

    voice_a = np.full(8000, 0.4, np.float32)
    voice_b = np.full(8000, -0.4, np.float32)

    # (a) Deux voix distinctes → deux clés de locuteur, numérotation globale, libellés résolus.
    ct = ConferenceTranscriber(cfg, FakeTr())
    ct._distinct = True
    ct._diar = Diarizer(cfg.conference.speaker_diarization, 16000, embed_fn=fake_embed)
    ct._diar_queue = queue.Queue()
    ct._t0 = time.monotonic()
    ct._emit_distinct("mic", 8000, voice_a)       # → spk:0
    ct._emit_distinct("system", 8000, voice_b)    # → spk:1
    ct._emit_distinct("mic", 8000, voice_a)       # même voix A → spk:0 (cluster stable)
    drive(ct)
    keys = [k for _, k, _ in ct._segments]
    assert keys == ["spk:0", "spk:1", "spk:0"], keys
    lines = ct.render_lines()
    assert any("Locuteur 1 : phrase" in ln for ln in lines)
    assert any("Locuteur 2 : phrase" in ln for ln in lines)
    assert ct.diarization_active is True
    assert {s["key"] for s in ct.speakers()} == {"spk:0", "spk:1"}

    # (b) Renommage rétroactif (FR-31) : le rendu (flux/export) reflète le nouveau nom.
    assert ct.rename_speaker("spk:0", "Marie Dupont") is True
    lines = ct.render_lines()
    assert any("Marie Dupont : phrase" in ln for ln in lines)
    assert not any("Locuteur 1 " in ln for ln in lines)     # plus d'étiquette auto pour spk:0
    # Export final trié : le fichier reprend les libellés courants (post-renommage).
    path = ct._open_transcript("Sys", "Mic")
    ct._close_transcript()
    ct._finish("Sys", path)
    content = path.read_text(encoding="utf-8")
    assert "Marie Dupont : phrase" in content and "Locuteur 2 : phrase" in content
    path.unlink()

    # (c) Repli gracieux (BR-08) : empreinte indisponible → étiquette de SOURCE, jamais d'omission.
    ct2 = ConferenceTranscriber(cfg, FakeTr())
    ct2._distinct = True
    ct2._diar = Diarizer(cfg.conference.speaker_diarization, 16000, embed_fn=fake_embed)
    ct2._diar_queue = queue.Queue()
    ct2._t0 = time.monotonic()
    ct2._emit_distinct("system", 8000, np.zeros(8000, np.float32))  # embed None → repli
    drive(ct2)
    assert [k for _, k, _ in ct2._segments] == ["Interlocuteurs"], ct2._segments
    assert any("Interlocuteurs : phrase" in ln for ln in ct2.render_lines())

    # (d) Diarisation NON construite si opt-out ou mode mixé (repli distinction/ mixage).
    off = Config()
    off.base_dir = base
    off.conference.distinguish_speakers = True
    off.conference.speaker_diarization.enabled = False
    ct3 = ConferenceTranscriber(off, FakeTr())
    ct3._distinct = True
    assert ct3._make_diarizer() is None                     # opt-out
    off.conference.speaker_diarization.enabled = True
    ct3._distinct = False
    assert ct3._make_diarizer() is None                     # mixage : pas de diarisation
    print("[27] diarisation réunion : clés locuteur, numérotation, renommage rétroactif, export, repli  OK")


# =============================================================================
# 22) Résumé de fin de session (UC-17) — LLM local
# =============================================================================
def test_session_summary() -> None:
    import json

    import whisperty.ai as ai_mod
    from whisperty.ai import LocalLLM
    from whisperty.config import AIConfig, SummaryConfig

    # Désactivé (défaut), texte vide, ou config résumé absente → None, aucun appel.
    assert LocalLLM(AIConfig(), SummaryConfig()).summarize("transcript") is None
    assert LocalLLM(AIConfig(), SummaryConfig(enabled=True)).summarize("   ") is None
    assert LocalLLM(AIConfig()).summarize("transcript") is None

    # Endpoint DISTANT → refus par la garde commune (le transcript ne sort jamais).
    remote = LocalLLM(
        AIConfig(endpoint="http://evil.example.com/v1/chat/completions"),
        SummaryConfig(enabled=True),
    )
    assert remote.summarize("texte secret") is None

    # Local + opener simulé : prompt dédié, troncature début+fin, indépendance de
    # ai.enabled (False ici : seul summary.enabled compte).
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
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": "• Décision : GO"}}]})

    original = ai_mod._OPENER
    ai_mod._OPENER = types.SimpleNamespace(open=fake_open)
    try:
        scfg = SummaryConfig(enabled=True, max_chars=40, timeout=99.0)
        llm = LocalLLM(AIConfig(enabled=False), scfg)  # raffinage OFF, résumé ON
        text = "DEBUT " + "x" * 200 + " FIN"
        assert llm.summarize(text) == "• Décision : GO"
        messages = captured["payload"]["messages"]
        assert messages[0]["content"] == scfg.prompt          # prompt dédié au résumé
        user = messages[1]["content"]
        assert user.startswith("DEBUT") and user.endswith("FIN")
        assert "[… transcription tronquée …]" in user          # coupe début+fin
        assert len(user) <= 40 + len("\n[… transcription tronquée …]\n")
        assert captured["timeout"] == 99.0                     # timeout du résumé
    finally:
        ai_mod._OPENER = original
    print("[22] résumé de session : garde locale + opt-in indépendant + troncature  OK")


# =============================================================================
# 21) Notes en session (UC-16) — live + réunion
# =============================================================================
def test_session_notes(tmp_path: Path) -> None:
    import threading

    from whisperty.config import Config
    from whisperty.conference import ConferenceTranscriber, parse_stamp
    from whisperty.live import LiveTranscriber

    base = tmp_path or Path(__file__).resolve().parent
    cfg = Config()
    cfg.base_dir = base
    cfg.live.transcript_dir = "notes_out"
    cfg.conference.export_dir = "notes_out"

    # parse_stamp : logique pure (horodatage « MM:SS » fourni par l'UI, non fiable).
    assert parse_stamp("01:15") == 75.0
    assert parse_stamp("00:05") == 5.0
    assert parse_stamp(None) is None and parse_stamp("") is None
    assert parse_stamp("1:99") is None and parse_stamp("abc") is None

    class FakeTr:
        def transcribe(self, audio, profile=None):
            return "segment"

    # -- live : refus hors session, note vide ignorée, fichier + récap + compteur --
    lt = LiveTranscriber(cfg, FakeTr())
    assert lt.add_note("perdue") is None            # session inactive → refus
    p = lt._open_transcript("Dev")
    lt._thread = threading.current_thread()          # simule une session active
    assert lt.add_note("   ") is None                # note vide ignorée (US-10)
    lt._emit("bonjour")
    line = lt.add_note("À faire : envoyer le budget")
    assert line == "[Note] À faire : envoyer le budget"
    lt._thread = None
    lt._close_transcript()
    content = p.read_text(encoding="utf-8")
    assert "[Note] À faire : envoyer le budget" in content
    assert "# Notes" in content                      # récapitulatif de fin (FR-26)
    finished: dict = {}
    lt._on_finished = lambda r: finished.update(r)
    lt._finish("Dev", p)
    assert finished["segments"] == 1 and finished["notes"] == 1
    assert "[Note] À faire : envoyer le budget" in finished["text"]
    p.unlink()

    # -- robustesse : sans fichier transcript, la note reste en mémoire (RE-11) --
    lt2 = LiveTranscriber(cfg, FakeTr())
    lt2._thread = threading.current_thread()
    assert lt2.add_note("sans fichier") == "[Note] sans fichier"
    lt2._thread = None
    fin2: dict = {}
    lt2._on_finished = lambda r: fin2.update(r)
    lt2._finish(None, None)
    assert "[Note] sans fichier" in fin2["text"] and fin2["notes"] == 1

    # -- réunion : ancrage [MM:SS] + entrelacement chronologique au tri (BR-07) --
    cfg.conference.distinguish_speakers = True
    ct = ConferenceTranscriber(cfg, FakeTr())
    ct._thread = threading.current_thread()
    path2 = ct._open_transcript("Sys", "Micro")
    ct._segments.append((5.0, "Moi", "premier point"))
    ct._segments.append((20.0, "Interlocuteurs", "réponse"))
    assert ct.add_note("décision actée", stamp="00:10") == "[00:10] Note : décision actée"
    finished2: dict = {}
    ct._on_finished = lambda r: finished2.update(r)
    ct._close_transcript()
    ct._finish("Sys", path2)
    lines = finished2["text"].split("\n")
    assert lines == [
        "[00:05] Moi : premier point",
        "[00:10] Note : décision actée",
        "[00:20] Interlocuteurs : réponse",
    ], lines
    assert finished2["segments"] == 2 and finished2["notes"] == 1
    final = path2.read_text(encoding="utf-8")
    assert final.index("[00:05]") < final.index("[00:10] Note") < final.index("[00:20]")
    assert "# Notes" in final                        # récap aussi dans la réécriture triée
    path2.unlink()
    print("[21] notes en session : live + réunion (ancrage, tri, récapitulatif, RE-11)  OK")


def test_dictionary_edit(tmp_path: Path) -> None:
    """UC-19 : écriture chirurgicale (commentaires/ordre préservés) + parse_entries."""
    from whisperty.dictionary import (
        ensure_dictionary_file,
        load_dictionary,
        parse_entries,
        update_dictionary_file,
    )

    base = tmp_path or Path(__file__).resolve().parent
    dic = base / "tmp_edit.txt"
    dic.write_text(
        "# En-tête\n"
        "# --- section A ---\n"
        "HTA\n"
        "Whisperty\n"
        "mauvais => bon\n"
        "\n"
        "# --- section B ---\n"
        "SCADA\n",
        encoding="utf-8",
    )

    # parse_entries : ordre du fichier, commentaires/blancs ignorés.
    entries = parse_entries(dic)
    assert [e["kind"] for e in entries] == ["hotword", "hotword", "correction", "hotword"]
    assert entries[0] == {"kind": "hotword", "term": "HTA", "replacement": ""}
    assert entries[2] == {"kind": "correction", "term": "mauvais", "replacement": "bon"}

    # Édition : supprime HTA, garde Whisperty/SCADA, modifie le RHS d'une correction
    # (clé « mauvais » inchangée → en place), ajoute un hotword + une correction.
    update_dictionary_file(dic, [
        {"kind": "hotword", "term": "Whisperty", "replacement": ""},
        {"kind": "hotword", "term": "SCADA", "replacement": ""},
        {"kind": "correction", "term": "mauvais", "replacement": "CORRIGE"},
        {"kind": "hotword", "term": "faster-whisper", "replacement": ""},
        {"kind": "correction", "term": "whispeurtie", "replacement": "Whisperty"},
    ])
    text = dic.read_text(encoding="utf-8")

    # Commentaires ET ordre des entrées survivantes préservés.
    assert "# En-tête" in text and "# --- section A ---" in text and "# --- section B ---" in text
    assert "HTA" not in text.splitlines()               # supprimé (ligne exacte)
    assert text.index("Whisperty") < text.index("SCADA")  # ordre d'origine conservé
    assert "mauvais => CORRIGE" in text                 # RHS mis à jour EN PLACE
    # Les nouvelles entrées sont ajoutées EN FIN de fichier (après SCADA).
    assert text.index("SCADA") < text.index("faster-whisper")
    assert text.index("SCADA") < text.index("whispeurtie => Whisperty")

    hot, repl = load_dictionary(dic)
    assert hot == ["Whisperty", "SCADA", "faster-whisper"]
    assert repl == {"mauvais": "CORRIGE", "whispeurtie": "Whisperty"}

    # Robustesse : entrées vides écartées, doublons dédupliqués. Une correction à
    # remplacement VIDE (« x => », suppression d'un tic) est VALIDE et préservée
    # (cohérence avec load_dictionary — cf. test_fixes.py).
    update_dictionary_file(dic, [
        {"kind": "hotword", "term": "  "},                       # vide → ignoré
        {"kind": "hotword", "term": "Dup"},
        {"kind": "hotword", "term": "Dup"},                      # doublon → une seule fois
        {"kind": "correction", "term": "x", "replacement": ""},  # suppression de « x »
    ])
    hot2, repl2 = load_dictionary(dic)
    assert hot2 == ["Dup"] and repl2 == {"x": ""}

    # Fichier absent : création avec en-tête ; ensure_dictionary_file idempotent.
    fresh = base / "sub" / "new_dico.txt"
    update_dictionary_file(fresh, [{"kind": "hotword", "term": "Alpha"}])
    ftext = fresh.read_text(encoding="utf-8")
    assert ftext.startswith("#") and "Alpha" in ftext
    ensure_dictionary_file(fresh)  # ne doit pas écraser
    assert "Alpha" in fresh.read_text(encoding="utf-8")
    created = base / "sub2" / "auto.txt"
    ensure_dictionary_file(created)
    assert created.is_file() and created.read_text(encoding="utf-8").startswith("#")

    dic.unlink()
    print("[23] dictionnaire — édition : préservation commentaires/ordre, dédup, création  OK")


def test_dictionary_hot_reload(tmp_path: Path) -> None:
    """UC-19 : rechargement à chaud (transcriber + profils) sans reset modèle."""
    import types as _types

    import numpy as np

    from whisperty.config import Config, ProfileDef, ProfilesConfig, TranscriptionConfig
    from whisperty.dictionary import update_dictionary_file
    from whisperty.profiles import ProfileResolver
    from whisperty.transcriber import Transcriber

    base = tmp_path or Path(__file__).resolve().parent

    class FakeModel:
        def transcribe(self, audio, language=None, beam_size=None,
                       initial_prompt=None, hotwords=None, vad_filter=None):
            seg = _types.SimpleNamespace(text="dis motclef et corrigemoi", start=0.0, end=1.0)
            return [seg], _types.SimpleNamespace(language=language)

    # -- Transcriber.set_dictionary : appliqué à la transcription suivante (profil None) --
    t = Transcriber(TranscriptionConfig(language="fr"), hotwords=[], replacements={})
    model = FakeModel()
    t._model = model  # court-circuite load()
    t.set_dictionary(["motclef"], {"corrigemoi": "corrigé"})
    out = t.transcribe(np.ones(10, dtype=np.float32))
    assert out == "dis motclef et corrigé", out         # correction à chaud
    assert t._model is model                             # aucun reset (même instance)

    # -- ProfileResolver.reload_dictionary : recharge la base + vide le cache --
    dic = base / "reload_dico.txt"
    dic.write_text("motA\nfote => faute\n", encoding="utf-8")
    cfg = Config()
    cfg.dictionary.path = str(dic)
    cfg.dictionary.enabled = True
    cfg.profiles = ProfilesConfig(enabled=True, definitions=[
        ProfileDef(name="p", match=["app.exe"], hotwords=["pro"]),
    ])
    resolver = ProfileResolver(cfg)
    prof = resolver.for_app("app.exe")
    assert "motA" in prof.hotwords and prof.replacements.get("fote") == "faute"

    # Édition du fichier puis rechargement : la nouvelle base est reflétée, cache purgé.
    update_dictionary_file(dic, [
        {"kind": "hotword", "term": "motB"},
        {"kind": "correction", "term": "nuvo", "replacement": "nouveau"},
    ])
    resolver.reload_dictionary()
    prof2 = resolver.for_app("app.exe")
    assert "motB" in prof2.hotwords and "motA" not in prof2.hotwords
    assert prof2.replacements.get("nuvo") == "nouveau" and "fote" not in prof2.replacements
    assert "pro" in prof2.hotwords                       # hotwords inline du profil conservés

    dic.unlink()
    print("[24] dictionnaire — rechargement à chaud : transcriber + profils (sans reset)  OK")


def test_dictionary_app_e2e(tmp_path: Path) -> None:
    """UC-19 : chemin intégré réel WhispertyApp (= ce que GuiApi appelle)."""
    import types as _types

    import numpy as np

    _install_gui_stubs()
    _install_injection_stubs()
    from whisperty.app import WhispertyApp
    from whisperty.config import Config

    base = tmp_path or Path(__file__).resolve().parent
    dic = base / "app_dico.txt"
    dic.write_text("# mon dico\nSCADA\n", encoding="utf-8")

    cfg = Config()
    cfg.dictionary.enabled = True
    cfg.dictionary.path = str(dic)      # isole du dictionary.txt du dépôt
    app = WhispertyApp(cfg)

    class FakeModel:
        def transcribe(self, audio, language=None, beam_size=None,
                       initial_prompt=None, hotwords=None, vad_filter=None):
            seg = _types.SimpleNamespace(text="dis whispeurtie", start=0.0, end=1.0)
            return [seg], _types.SimpleNamespace(language=language)

    app.transcriber._model = FakeModel()  # court-circuite load()

    # get_dictionary : reflète le fichier initial.
    d0 = app.get_dictionary()
    assert d0["enabled"] is True and d0["hotwords"] == ["SCADA"] and d0["corrections"] == []

    # apply_dictionary_from_gui : écrit + recharge à chaud, renvoie les compteurs.
    res = app.apply_dictionary_from_gui({
        "hotwords": ["SCADA", "faster-whisper"],
        "corrections": [{"wrong": "whispeurtie", "right": "Whisperty"}],
    })
    assert res["ok"] is True and res["hotwords"] == 2 and res["corrections"] == 1

    text = dic.read_text(encoding="utf-8")
    assert "# mon dico" in text                          # commentaire préservé
    assert "faster-whisper" in text and "whispeurtie => Whisperty" in text

    # Effet à chaud SANS relance : la dictée suivante applique la nouvelle correction.
    out = app.transcriber.transcribe(np.ones(10, dtype=np.float32))
    assert out == "dis Whisperty", out

    # Notice utilisateur émise (compteur incrémenté + texte des compteurs).
    assert app.notice_rev() > 0
    assert "correction" in app.notice().get("text", "")

    # open_dictionary : ne lève pas ; le fichier existe (os.startfile best-effort/no-op ici).
    app.open_dictionary()
    assert dic.is_file()

    app.quit()
    dic.unlink()
    print("[25] dictionnaire — E2E WhispertyApp : get/apply (écriture+chaud+notice)/open  OK")


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
    test_diarization_logic()
    test_conference_diarization(tmp)
    test_session_notes(tmp)
    test_session_summary()
    test_dictionary_edit(tmp)
    test_dictionary_hot_reload(tmp)
    test_dictionary_app_e2e(tmp)
    print("\nTOUS LES TESTS PASSENT")


if __name__ == "__main__":
    _run_all()
