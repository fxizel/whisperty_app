"""Tests hors-ligne complémentaires du mode réunion (``whisperty.conference``).

Complète les tests 16-20 de ``test_logic.py`` (logique de mixage/alignement/
distinction) par l'orchestration : démarrage sans aucune source audio (chemin
``start`` → ``_run`` → ``_finish`` avec erreur), robustesse des callbacks
(transcription, on_segment, on_finished) et cas limites de l'export.

Aucun audio ni modèle réel : ``soundcard`` (doublure conftest) ne renvoie aucune
sortie, et le micro échoue (InputStream factice) → réunion sans source.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "pystray" not in sys.modules:
    import tests.conftest  # noqa: F401

import numpy as np  # noqa: E402


def _cfg(tmp: Path, *, distinct: bool):
    from whisperty.config import Config

    cfg = Config()
    cfg.base_dir = tmp
    cfg.conference.export_dir = "conf_extra"
    cfg.conference.distinguish_speakers = distinct
    cfg.conference.block_duration = 0.05
    cfg.conference.silence_duration = 0.1
    cfg.conference.max_segment = 5.0
    cfg.conference.vad_threshold = 0.01
    return cfg


# =============================================================================
# 1) Démarrage sans aucune source audio : start -> _run -> _finish(erreur)
# =============================================================================
def test_start_no_audio_source(tmp_path: Path) -> None:
    from whisperty.conference import ConferenceTranscriber

    cfg = _cfg(tmp_path, distinct=False)

    class FakeTr:
        def transcribe(self, audio, profile=None):
            return ""

    finished: dict = {}
    ct = ConferenceTranscriber(cfg, FakeTr(), on_finished=lambda r: finished.update(r))
    assert ct.start(None) is True            # le thread worker démarre
    assert ct.start(None) is False           # déjà en cours -> refus
    ct.wait(timeout=8.0)

    # Micro KO (InputStream factice) + sortie KO (aucun haut-parleur) → erreur claire.
    assert finished.get("error"), finished
    assert "aucune source" in finished["error"].lower()
    assert finished["segments"] == 0 and finished["sources"] == []
    assert ct.is_running() is False
    print("[conf-x 1] start sans source -> _run -> _finish(erreur) + refus si en cours  OK")


# =============================================================================
# 2) _handle_segment : transcription qui échoue → segment ignoré (pas de crash)
# =============================================================================
def test_handle_segment_transcribe_error(tmp_path: Path) -> None:
    from whisperty.conference import ConferenceTranscriber

    cfg = _cfg(tmp_path, distinct=False)

    class BoomTr:
        def transcribe(self, audio, profile=None):
            raise RuntimeError("modèle KO")

    ct = ConferenceTranscriber(cfg, BoomTr())
    ct._t0 = time.monotonic()
    ct._handle_segment(np.full(800, 0.4, np.float32))  # ne lève pas
    assert ct._segments == []
    print("[conf-x 2] _handle_segment : transcription en échec -> segment ignoré  OK")


# =============================================================================
# 3) _emit + _write_line : on_segment fautif avalé, segment tout de même mémorisé
# =============================================================================
def test_emit_on_segment_error(tmp_path: Path) -> None:
    from whisperty.conference import ConferenceTranscriber

    cfg = _cfg(tmp_path, distinct=False)

    def boom(line, text):
        raise RuntimeError("on_segment fautif")

    ct = ConferenceTranscriber(cfg, object(), on_segment=boom)
    ct._t0 = time.monotonic()
    ct._emit("bonjour")  # _write_line appelle on_segment -> exception avalée
    assert len(ct._segments) == 1 and ct._segments[0][2] == "bonjour"
    print("[conf-x 3] _emit/_write_line : on_segment fautif avalé  OK")


# =============================================================================
# 4) _emit_distinct : transcribe_segments qui échoue → aucune émission
# =============================================================================
def test_emit_distinct_error(tmp_path: Path) -> None:
    from whisperty.conference import ConferenceTranscriber

    cfg = _cfg(tmp_path, distinct=True)

    class BoomTr:
        def transcribe_segments(self, audio, profile=None):
            raise RuntimeError("ASR KO")

    ct = ConferenceTranscriber(cfg, BoomTr())
    ct._t0 = time.monotonic()
    ct._emit_distinct("mic", 1600, np.full(800, 0.4, np.float32))  # ne lève pas
    assert ct._segments == []
    print("[conf-x 4] _emit_distinct : transcribe_segments en échec -> rien émis  OK")


# =============================================================================
# 5) _finish : callback fautif avalé + _thread remis à None
# =============================================================================
def test_finish_callback_error(tmp_path: Path) -> None:
    from whisperty.conference import ConferenceTranscriber

    cfg = _cfg(tmp_path, distinct=False)

    def boom(result):
        raise RuntimeError("on_finished fautif")

    ct = ConferenceTranscriber(cfg, object(), on_finished=boom)
    ct._thread = object()  # simulate « thread présent »
    ct._segments = [(1.0, None, "a"), (0.5, None, "b")]
    ct._finish("Sys", None)  # ne lève pas malgré le callback fautif
    assert ct._thread is None  # nullé après le callback (cohérence wait/is_running)
    print("[conf-x 5] _finish : callback fautif avalé + _thread=None  OK")


# =============================================================================
# 6) _rewrite_sorted(None) : no-op sûr (transcript non inscriptible)
# =============================================================================
def test_rewrite_sorted_none(tmp_path: Path) -> None:
    from whisperty.conference import ConferenceTranscriber

    cfg = _cfg(tmp_path, distinct=True)
    ct = ConferenceTranscriber(cfg, object())
    ct._rewrite_sorted(None, [(0.0, "Moi", "x")], [])  # ne lève pas, ne crée rien
    print("[conf-x 6] _rewrite_sorted(None) : no-op sûr  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_confx_test_"))
    test_start_no_audio_source(tmp)
    test_handle_segment_transcribe_error(tmp)
    test_emit_on_segment_error(tmp)
    test_emit_distinct_error(tmp)
    test_finish_callback_error(tmp)
    test_rewrite_sorted_none(tmp)
    print("\nTOUS LES TESTS CONFERENCE-EXTRA PASSENT")


if __name__ == "__main__":
    _run_all()
