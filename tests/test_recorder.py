"""Tests hors-ligne du module de capture audio (``whisperty.recorder``).

Aucun micro requis : ``sounddevice`` est remplacé par une doublure complète
(InputStream/check_input_settings/query_devices) injectée dans le module. On
couvre le rééchantillonnage, le calcul RMS du callback, la machine start/stop,
l'écriture WAV et la boucle ``record_until_silence``.

Lançable tel quel (``python tests/test_recorder.py``) ou via pytest.
"""
from __future__ import annotations

import sys
import threading
import time
import types
import wave
from pathlib import Path

# --- racine + doublures minimales pour exécution autonome --------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "sounddevice" not in sys.modules:
    _sd = types.ModuleType("sounddevice")
    _sd.PortAudioError = type("PortAudioError", (Exception,), {})
    _sd.query_devices = lambda *a, **k: []
    _sd.check_input_settings = lambda **k: None
    _sd.InputStream = None
    sys.modules["sounddevice"] = _sd

import numpy as np  # noqa: E402


# --- doublure sounddevice complète et configurable ---------------------------
def _make_fake_sd(*, fail_16k: bool = False, native_rate: int = 48_000,
                  fail_on_start: bool = False):
    sd = types.ModuleType("sounddevice")

    class PortAudioError(Exception):
        pass

    sd.PortAudioError = PortAudioError

    def check_input_settings(device=None, channels=None, samplerate=None, dtype=None):
        if fail_16k:
            raise PortAudioError("16 kHz non supporté")

    def query_devices(device=None, kind=None):
        return {"default_samplerate": native_rate, "name": "Faux micro",
                "max_input_channels": 2}

    class FakeStream:
        def __init__(self, **kw):
            self.kw = kw
            self.started = False
            self.stopped = False
            self.closed = False

        def start(self):
            if fail_on_start:
                raise PortAudioError("périphérique occupé")
            self.started = True

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    sd.check_input_settings = check_input_settings
    sd.query_devices = query_devices
    sd.InputStream = FakeStream
    return sd


def _with_fake_sd(fake):
    """Installe ``fake`` comme module sd de recorder et renvoie l'ancien (à restaurer)."""
    from whisperty import recorder

    previous = recorder.sd
    recorder.sd = fake
    return recorder, previous


# =============================================================================
# 1) Rééchantillonnage (_resample) : identité, soxr, repli interpolation
# =============================================================================
def test_resample() -> None:
    from whisperty.recorder import _resample

    # Identité : même fréquence → tableau renvoyé inchangé (même objet).
    x = np.linspace(-1, 1, 100, dtype=np.float32)
    assert _resample(x, 16_000, 16_000) is x
    # Signal vide → renvoyé tel quel sans erreur.
    assert _resample(np.zeros(0, np.float32), 48_000, 16_000).size == 0

    # Sous-échantillonnage 48k → 16k : ~1/3 des échantillons, dtype float32.
    src = np.sin(np.linspace(0, 20, 4800, dtype=np.float32))
    down = _resample(src, 48_000, 16_000)
    assert down.dtype == np.float32
    assert abs(down.size - 1600) <= 2, down.size

    # Repli interpolation NumPy quand soxr est indisponible (import forcé en échec).
    saved = sys.modules.get("soxr")
    sys.modules["soxr"] = None  # rend « import soxr » -> ImportError
    try:
        up = _resample(src, 16_000, 32_000)
        assert up.dtype == np.float32
        assert abs(up.size - 9600) <= 2, up.size  # 4800 * 32000/16000
    finally:
        if saved is not None:
            sys.modules["soxr"] = saved
        else:
            del sys.modules["soxr"]
    print("[recorder 1] _resample : identité + vide + soxr + repli interpolation  OK")


# =============================================================================
# 2) Callback PortAudio : RMS, modes batch/streaming, callbacks robustes
# =============================================================================
def test_callback_rms_and_modes() -> None:
    from whisperty.recorder import AudioRecorder

    levels: list[float] = []
    rec = AudioRecorder(level_callback=levels.append)

    # Bloc constant d'amplitude 0,5 → RMS = 0,5 ; accumulé en mode batch.
    block = np.full((100, 1), 0.5, np.float32)
    rec._callback(block, 100, None, None)
    assert abs(rec.current_level - 0.5) < 1e-6
    assert levels == [0.5]
    assert len(rec._frames) == 1 and rec._frames[0] is not block  # copie défensive

    # Bloc vide → niveau 0, pas d'exception.
    rec._callback(np.zeros((0, 1), np.float32), 0, None, None)
    assert rec.current_level == 0.0

    # Mode streaming : frame_callback reçoit le bloc, rien n'est conservé.
    captured: list = []
    rec2 = AudioRecorder(frame_callback=captured.append)
    rec2._callback(np.full((10, 1), 0.2, np.float32), 10, None, None)
    assert len(captured) == 1 and rec2._frames == []
    assert abs(rec2.current_level - 0.2) < 1e-6

    # Un callback fautif ne tue pas le flux (exception avalée et journalisée).
    def boom(_):
        raise RuntimeError("callback fautif")

    rec3 = AudioRecorder(level_callback=boom, frame_callback=boom)
    rec3._callback(np.full((4, 1), 0.1, np.float32), 4, None, None)  # ne lève pas
    assert abs(rec3.current_level - 0.1) < 1e-6
    print("[recorder 2] callback : RMS + batch/streaming + bloc vide + callback fautif  OK")


# =============================================================================
# 3) start()/stop() : ouverture, idempotence, mono, rééchantillonnage
# =============================================================================
def test_start_stop_batch() -> None:
    from whisperty.recorder import AudioRecorder

    recorder, previous = _with_fake_sd(_make_fake_sd())  # 16 kHz supporté
    try:
        rec = AudioRecorder(samplerate=16_000)
        assert not rec.is_recording
        rec.start()
        assert rec.is_recording and rec.capture_rate == 16_000
        assert rec._stream.started is True

        # start() idempotent : second appel ignoré, même flux.
        stream = rec._stream
        rec.start()
        assert rec._stream is stream

        # Deux blocs stéréo → réduction mono (moyenne) + concat, pas de resample.
        rec._callback(np.full((50, 2), 0.4, np.float32), 50, None, None)
        rec._callback(np.full((30, 2), 0.4, np.float32), 30, None, None)
        audio = rec.stop()
        assert not rec.is_recording
        assert audio.dtype == np.float32 and audio.ndim == 1
        assert audio.size == 80
        assert np.allclose(audio, 0.4)
        assert stream.stopped and stream.closed

        # stop() idempotent : second appel → tableau vide, pas de double-close.
        assert rec.stop().size == 0
    finally:
        recorder.sd = previous
    print("[recorder 3] start/stop : ouverture + idempotence + mono + concat  OK")


def test_start_native_rate_then_resample() -> None:
    from whisperty.recorder import AudioRecorder

    recorder, previous = _with_fake_sd(_make_fake_sd(fail_16k=True, native_rate=48_000))
    try:
        rec = AudioRecorder(samplerate=16_000)
        rec.start()
        # 16 kHz refusé → capture à la fréquence native du périphérique.
        assert rec.capture_rate == 48_000
        rec._callback(np.full((4800, 1), 0.3, np.float32), 4800, None, None)
        audio = rec.stop()
        # Rééchantillonné 48k → 16k : environ 1/3 des échantillons.
        assert audio.dtype == np.float32
        assert abs(audio.size - 1600) <= 4, audio.size
    finally:
        recorder.sd = previous
    print("[recorder 4] start : fréquence native + rééchantillonnage à stop()  OK")


def test_start_failure_raises_microphone_error() -> None:
    from whisperty.recorder import AudioRecorder, MicrophoneError

    recorder, previous = _with_fake_sd(_make_fake_sd(fail_on_start=True))
    try:
        rec = AudioRecorder()
        raised = False
        try:
            rec.start()
        except MicrophoneError:
            raised = True
        assert raised, "MicrophoneError attendue quand le flux refuse de démarrer"
        # Pas de flux orphelin : nettoyé et état non-enregistrant.
        assert rec._stream is None and not rec.is_recording
    finally:
        recorder.sd = previous
    print("[recorder 5] start : échec PortAudio -> MicrophoneError + nettoyage  OK")


def test_stop_when_idle_returns_empty() -> None:
    from whisperty.recorder import AudioRecorder

    rec = AudioRecorder()
    out = rec.stop()  # jamais démarré
    assert isinstance(out, np.ndarray) and out.size == 0
    print("[recorder 6] stop() au repos : tableau vide  OK")


def test_start_invalid_device_raises_microphone_error() -> None:
    """Périphérique inexistant (nom/index erroné) : sounddevice lève ValueError, qui doit
    être convertie en MicrophoneError — sinon elle remonterait dans le thread écouteur
    pynput et tuerait le raccourci global."""
    from whisperty.recorder import AudioRecorder, MicrophoneError

    fake = _make_fake_sd()

    def _invalid(*a, **k):
        raise ValueError("No input device matching 'micro fantôme'")

    fake.check_input_settings = _invalid
    fake.query_devices = _invalid
    recorder, previous = _with_fake_sd(fake)
    try:
        rec = AudioRecorder(device="micro fantôme")
        raised = False
        try:
            rec.start()
        except MicrophoneError:
            raised = True
        assert raised, "MicrophoneError attendue pour un périphérique invalide"
        assert rec._stream is None and not rec.is_recording
    finally:
        recorder.sd = previous
    print("[recorder 6b] start : device invalide (ValueError) -> MicrophoneError  OK")


def test_stop_failure_resets_state() -> None:
    """Si l'arrêt PortAudio lève (périphérique débranché), l'état doit être réinitialisé :
    sinon tous les start() suivants seraient refusés (« déjà en cours ») jusqu'au
    redémarrage de l'application. Les frames déjà captées sont conservées."""
    from whisperty.recorder import AudioRecorder

    recorder, previous = _with_fake_sd(_make_fake_sd())
    try:
        rec = AudioRecorder(samplerate=16_000)
        rec.start()
        rec._callback(np.full((40, 1), 0.25, np.float32), 40, None, None)

        def boom():
            raise RuntimeError("périphérique débranché")

        rec._stream.stop = boom  # l'arrêt du flux échoue
        audio = rec.stop()       # ne lève pas ; frames conservées
        assert audio.size == 40 and np.allclose(audio, 0.25)
        assert not rec.is_recording and rec._stream is None

        # L'enregistreur reste utilisable : un nouveau cycle start/stop fonctionne.
        rec.start()
        assert rec.is_recording
        rec._callback(np.full((10, 1), 0.1, np.float32), 10, None, None)
        assert rec.stop().size == 10
    finally:
        recorder.sd = previous
    print("[recorder 6c] stop : échec d'arrêt -> état réinitialisé, frames conservées  OK")


# =============================================================================
# 4) is_silent + écriture WAV 16 bits PCM
# =============================================================================
def test_is_silent_and_save_wav(tmp_path: Path) -> None:
    from whisperty.recorder import AudioRecorder

    base = tmp_path or Path(__file__).resolve().parent
    rec = AudioRecorder(samplerate=16_000)

    rec._level = 0.005
    assert rec.is_silent(threshold=0.01) is True
    rec._level = 0.5
    assert rec.is_silent(threshold=0.01) is False

    # save_wav : float32 [-1,1] (avec dépassements à écrêter) → PCM16 mono 16 kHz.
    audio = np.array([0.0, 1.0, -1.0, 2.0, -2.0, 0.5], np.float32)  # 2.0/-2.0 → écrêtés
    dest = base / "out_dir" / "clip.wav"
    written = rec.save_wav(dest, audio)
    assert written == dest and dest.is_file()

    with wave.open(str(dest), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16_000
        assert w.getnframes() == 6
        raw = w.readframes(6)
    pcm = np.frombuffer(raw, dtype=np.int16)
    assert pcm[0] == 0
    assert pcm[1] == 32767 and pcm[3] == 32767      # 1.0 et 2.0 écrêtés à +pleine échelle
    assert pcm[2] == -32767 and pcm[4] == -32767    # -1.0 et -2.0 écrêtés
    dest.unlink()
    print("[recorder 7] is_silent + save_wav : PCM16 mono 16 kHz + écrêtage  OK")


# =============================================================================
# 5) record_until_silence : arrêt sur silence + garde-fou durée max
# =============================================================================
def test_record_until_silence_max_duration() -> None:
    from whisperty.recorder import AudioRecorder

    rec = AudioRecorder()
    sentinel = np.full(3, 0.7, np.float32)
    rec.start = lambda: None          # type: ignore[method-assign]
    rec.stop = lambda: sentinel       # type: ignore[method-assign]
    rec._level = 0.0                  # silence permanent : jamais de parole détectée

    t0 = time.monotonic()
    out = rec.record_until_silence(
        threshold=0.01, silence_duration=10.0, max_duration=0.2, poll_interval=0.01
    )
    elapsed = time.monotonic() - t0
    assert out is sentinel
    assert 0.15 <= elapsed < 1.5, elapsed  # coupé par max_duration, pas par le silence
    print("[recorder 8] record_until_silence : garde-fou durée max  OK")


def test_record_until_silence_on_silence() -> None:
    from whisperty.recorder import AudioRecorder

    rec = AudioRecorder()
    sentinel = np.full(2, 0.9, np.float32)
    rec.start = lambda: None          # type: ignore[method-assign]
    rec.stop = lambda: sentinel       # type: ignore[method-assign]
    rec._level = 0.5                  # parole au départ

    # Après ~60 ms, bascule en silence : la boucle doit couper bien avant max_duration.
    def silence_after():
        time.sleep(0.06)
        rec._level = 0.0

    threading.Thread(target=silence_after, daemon=True).start()
    t0 = time.monotonic()
    out = rec.record_until_silence(
        threshold=0.01, silence_duration=0.08, max_duration=5.0, poll_interval=0.01
    )
    elapsed = time.monotonic() - t0
    assert out is sentinel
    assert elapsed < 2.0, elapsed  # arrêt déclenché par le silence, pas par max_duration
    print("[recorder 9] record_until_silence : arrêt sur silence prolongé  OK")


# =============================================================================
# 6) list_input_devices : filtrage des périphériques d'entrée
# =============================================================================
def test_list_input_devices() -> None:
    from whisperty import recorder

    fake = _make_fake_sd()
    fake.query_devices = lambda: [
        {"name": "Micro USB", "max_input_channels": 2, "default_samplerate": 44_100.0},
        {"name": "Haut-parleurs", "max_input_channels": 0, "default_samplerate": 48_000.0},
        {"name": "Webcam", "max_input_channels": 1, "default_samplerate": 16_000.0},
    ]
    previous = recorder.sd
    recorder.sd = fake
    try:
        devices = recorder.list_input_devices()
    finally:
        recorder.sd = previous
    # Seuls les périphériques avec des canaux d'entrée sont retenus, index conservé.
    assert [d["name"] for d in devices] == ["Micro USB", "Webcam"]
    assert devices[0]["index"] == 0 and devices[1]["index"] == 2
    assert devices[0]["channels"] == 2 and devices[0]["default_samplerate"] == 44_100
    print("[recorder 10] list_input_devices : filtrage entrée + mapping  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_rec_test_"))
    test_resample()
    test_callback_rms_and_modes()
    test_start_stop_batch()
    test_start_native_rate_then_resample()
    test_start_failure_raises_microphone_error()
    test_stop_when_idle_returns_empty()
    test_start_invalid_device_raises_microphone_error()
    test_stop_failure_resets_state()
    test_is_silent_and_save_wav(tmp)
    test_record_until_silence_max_duration()
    test_record_until_silence_on_silence()
    test_list_input_devices()
    print("\nTOUS LES TESTS RECORDER PASSENT")


if __name__ == "__main__":
    _run_all()
