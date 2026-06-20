"""Tests hors-ligne de l'assistant de réunion (``whisperty.meeting``).

Cible les chemins déterministes de ``MeetingAssistant`` non couverts par
``test_logic.py`` : traitement synchrone d'une question (gardes IA, rejet LLM,
réponse vide, copie vs injection, échec d'injection), enrobage du callback de
fin, notifications robustes, et démarrage (résolution du périphérique + reset).

Aucun audio, modèle ni LLM réel : LLM/injector/historique sont des doublures.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "pystray" not in sys.modules:
    import tests.conftest  # noqa: F401


class FakeLLM:
    def __init__(self, enabled=True, is_question=True, reply="Oui, c'est prévu."):
        self.cfg = types.SimpleNamespace(enabled=enabled, model="llama3.2")
        self._is_question = is_question
        self._reply = reply
        self.is_question_calls: list = []
        self.reply_calls: list = []

    def meeting_is_question(self, segment, user_name, context):
        self.is_question_calls.append((segment, user_name, list(context)))
        return self._is_question

    def meeting_reply(self, question, context, user_context, reply_prompt, user_name=""):
        self.reply_calls.append((question, user_name))
        return self._reply


class FakeInjector:
    def __init__(self, inject_raises=False):
        self.injected: list[str] = []
        self.copied: list[str] = []
        self._inject_raises = inject_raises

    def inject(self, text):
        if self._inject_raises:
            raise RuntimeError("injection impossible")
        self.injected.append(text)

    def copy_to_clipboard(self, text):
        self.copied.append(text)
        return True


class FakeHistory:
    def __init__(self):
        self.added: list[dict] = []

    def add(self, text, **kwargs):
        self.added.append({"text": text, **kwargs})


class FakeLive:
    def __init__(self, start_ok=True):
        self.start_ok = start_ok
        self.started_with: list = []

    def start(self, device_spec=None):
        self.started_with.append(device_spec)
        return self.start_ok


def _make_assistant(tmp, *, llm=None, injector=None, on_notify=None,
                    auto_inject=False, user_name="Jean", on_finished=None):
    from whisperty.config import Config, MeetingConfig
    from whisperty.meeting import MeetingAssistant

    cfg = Config()
    cfg.base_dir = tmp
    cfg.meeting = MeetingConfig(user_name=user_name, auto_inject=auto_inject)
    cfg.ai.enabled = True
    ma = MeetingAssistant(
        cfg, object(), llm or FakeLLM(), injector or FakeInjector(),
        history=FakeHistory(), on_notify=on_notify, on_finished=on_finished,
    )
    return ma, cfg


# =============================================================================
# 1) _process_question : copie de la réponse + historique (auto_inject=False)
# =============================================================================
def test_process_question_copy(tmp_path: Path) -> None:
    notes: list[str] = []
    ma, _ = _make_assistant(tmp_path, on_notify=notes.append)
    ma._process_question("Jean, c'est pour quand ?")

    assert ma.injector.copied == ["Oui, c'est prévu."]
    assert ma.injector.injected == []
    assert ma._replies == ["Oui, c'est prévu."]
    assert any("copiée" in n.lower() for n in notes)
    assert ma.history.added and ma.history.added[0]["source"] == "réunion"
    assert ma.history.added[0]["model"] == "llama3.2"
    print("[meeting 1] _process_question : réponse copiée + historique + notif  OK")


# =============================================================================
# 2) _process_question : injection (auto_inject=True) + échec d'injection
# =============================================================================
def test_process_question_inject(tmp_path: Path) -> None:
    notes: list[str] = []
    ma, _ = _make_assistant(tmp_path, on_notify=notes.append, auto_inject=True)
    ma._process_question("Jean, tu valides ?")
    assert ma.injector.injected == ["Oui, c'est prévu."]
    assert any("injectée" in n.lower() for n in notes)

    # Injection qui échoue → notification d'échec, pas de crash.
    notes2: list[str] = []
    ma2, _ = _make_assistant(
        tmp_path, injector=FakeInjector(inject_raises=True),
        on_notify=notes2.append, auto_inject=True,
    )
    ma2._process_question("Jean, tu valides ?")
    assert any("échou" in n.lower() for n in notes2)
    print("[meeting 2] _process_question : injection + échec d'injection géré  OK")


# =============================================================================
# 3) _process_question : gardes (IA désactivée, rejet LLM, réponse vide)
# =============================================================================
def test_process_question_guards(tmp_path: Path) -> None:
    # (a) IA désactivée → notification dédiée, aucune réponse.
    notes: list[str] = []
    ma, _ = _make_assistant(
        tmp_path, llm=FakeLLM(enabled=False), on_notify=notes.append
    )
    ma._process_question("Jean, c'est pour quand ?")
    assert ma.injector.copied == [] and ma._replies == []
    assert any("ai.enabled" in n for n in notes)

    # (b) Le LLM rejette le segment comme question → rien.
    ma2, _ = _make_assistant(tmp_path, llm=FakeLLM(is_question=False))
    ma2._process_question("Jean, c'est pour quand ?")
    assert ma2.injector.copied == [] and ma2._replies == []
    assert ma2.llm.reply_calls == []  # pas de génération de réponse

    # (c) Réponse vide → rien copié, rien archivé.
    ma3, _ = _make_assistant(tmp_path, llm=FakeLLM(reply=""))
    ma3._process_question("Jean, c'est pour quand ?")
    assert ma3.injector.copied == [] and ma3._replies == []
    assert ma3.history.added == []
    print("[meeting 3] _process_question : gardes IA/rejet/réponse vide  OK")


# =============================================================================
# 4) _on_finished_wrapper : injecte replies/reply_count + robustesse
# =============================================================================
def test_on_finished_wrapper(tmp_path: Path) -> None:
    received: dict = {}
    ma, _ = _make_assistant(tmp_path, on_finished=lambda r: received.update(r))
    ma._process_question("Jean, c'est pour quand ?")   # produit 1 réponse
    ma._on_finished_wrapper({"text": "transcript", "device": "Spk"})
    assert received["reply_count"] == 1
    assert received["replies"] == ["Oui, c'est prévu."]
    assert received["text"] == "transcript"

    # Callback fautif → avalé (pas de propagation dans le thread live).
    def boom(_):
        raise RuntimeError("callback fautif")
    ma2, _ = _make_assistant(tmp_path, on_finished=boom)
    ma2._on_finished_wrapper({"text": "x"})  # ne lève pas
    print("[meeting 4] _on_finished_wrapper : reply_count/replies + callback fautif  OK")


# =============================================================================
# 5) _notify robuste + _on_segment (contexte + court-circuit non-question)
# =============================================================================
def test_notify_and_segment(tmp_path: Path) -> None:
    # on_notify absent → pas de crash.
    ma, _ = _make_assistant(tmp_path, on_notify=None)
    ma._notify("message")  # ne lève pas

    # on_notify fautif → avalé.
    def boom(_):
        raise RuntimeError("notify fautif")
    ma2, _ = _make_assistant(tmp_path, on_notify=boom)
    ma2._notify("message")  # ne lève pas

    # _on_segment : un segment qui n'est pas une question alimente le contexte
    # mais ne déclenche aucune analyse LLM (pas de réponse).
    ma3, _ = _make_assistant(tmp_path)
    ma3._on_segment("00:00", "Merci pour la présentation.")
    assert list(ma3._context) == ["Merci pour la présentation."]
    assert ma3.llm.is_question_calls == [] and ma3.injector.copied == []
    print("[meeting 5] _notify robuste + _on_segment (contexte/non-question)  OK")


# =============================================================================
# 6) start : reset du contexte + résolution du périphérique
# =============================================================================
def test_start(tmp_path: Path) -> None:
    ma, cfg = _make_assistant(tmp_path)
    ma.live = FakeLive(start_ok=True)
    ma._context.append("vieux contexte")
    ma._replies.append("vieille réponse")

    assert ma.start(None) is True
    assert ma.live.started_with == [cfg.live.device]  # None résolu vers le défaut config
    assert list(ma._context) == [] and ma._replies == []  # reset au démarrage

    assert ma.start(2) is True
    assert ma.live.started_with[-1] == 2  # index transmis tel quel
    print("[meeting 6] start : reset contexte/replies + résolution périphérique  OK")


def _run_all() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="whisperty_meeting_test_"))
    test_process_question_copy(tmp)
    test_process_question_inject(tmp)
    test_process_question_guards(tmp)
    test_on_finished_wrapper(tmp)
    test_notify_and_segment(tmp)
    test_start(tmp)
    print("\nTOUS LES TESTS MEETING PASSENT")


if __name__ == "__main__":
    _run_all()
