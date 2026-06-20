"""Whisperty — assistant de réunion (V2).

Écoute la sortie audio d'une confcall (loopback, comme la transcription live),
détecte les questions posées à l'utilisateur et propose une réponse via un LLM
**local**. Le texte suggéré est copié dans le presse-papiers (défaut) ou injecté
dans l'application active si ``meeting.auto_inject`` est activé.

Concurrence : la capture tourne dans le thread ``LiveTranscriber`` ; chaque
segment suspect déclenche un thread worker dédié pour l'analyse LLM (ne bloque
pas la transcription). Même règles COM/verrous que :mod:`live`.

Confidentialité : tout reste local (Whisper + LLM localhost uniquement).
"""
from __future__ import annotations

import logging
import re
import threading
from collections import deque
from typing import Callable, Deque, Optional, Union

from .live import LiveTranscriber

logger = logging.getLogger(__name__)

# Mots interrogatifs courants en français (début de phrase ou après ponctuation).
_INTERROGATIVE = re.compile(
    r"(?:^|[.!?,;]\s*)"
    r"(?:est-ce que|est ce que|comment|pourquoi|quand|où|ou|qui|que|quoi|"
    r"peux-tu|peux tu|pouvez-vous|pouvez vous|as-tu|as tu|avez-vous|avez vous|"
    r"tu peux|vous pouvez|tu as|vous avez|dis-moi|dis moi|dites-moi|dites moi|"
    r"qu'en penses-tu|qu en penses tu|ton avis|votre avis|tu penses|vous pensez)",
    re.IGNORECASE,
)


def looks_like_question(text: str, user_name: str = "") -> bool:
    """Pré-filtre rapide (sans LLM) : le segment ressemble-t-il à une question ?

    Si ``user_name`` est renseigné, exige aussi la présence du prénom/nom dans le
    texte pour limiter les faux positifs sur les questions générales.
    """
    t = (text or "").strip()
    if not t:
        return False
    has_interrogation = "?" in t or bool(_INTERROGATIVE.search(t))
    if not has_interrogation:
        return False
    if user_name and user_name.strip():
        # Correspondance insensible à la casse, mot entier ou sous-chaîne du nom.
        name = user_name.strip()
        if name.lower() not in t.lower():
            return False
    return True


class MeetingAssistant:
    """Coordonne la transcription live + détection de questions + réponses LLM."""

    def __init__(
        self,
        config,
        transcriber,
        llm,
        injector,
        history=None,
        on_notify: Optional[Callable[[str], None]] = None,
        on_finished: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._config = config
        self.cfg = config.meeting
        self.transcriber = transcriber
        self.llm = llm
        self.injector = injector
        self.history = history
        self._on_notify = on_notify
        self._on_finished = on_finished
        self._context: Deque[str] = deque(maxlen=max(1, self.cfg.context_segments))
        self._replies: list[str] = []
        self._lock = threading.Lock()
        self.live = LiveTranscriber(
            config,
            transcriber,
            on_segment=self._on_segment,
            on_finished=self._on_finished_wrapper,
            transcript_prefix="meeting",
        )

    def is_running(self) -> bool:
        return self.live.is_running()

    def start(self, device_spec: Optional[Union[int, str]] = None) -> bool:
        """Démarre l'écoute de réunion. False si déjà en cours."""
        with self._lock:
            self._context.clear()
            self._replies.clear()
        if device_spec is None:
            device_spec = self._config.live.device
        return self.live.start(device_spec)

    def stop(self) -> None:
        """Demande l'arrêt (non bloquant)."""
        self.live.stop()

    def wait(self, timeout: Optional[float] = None) -> None:
        self.live.wait(timeout=timeout)

    def _on_segment(self, stamp: str, text: str) -> None:
        """Callback synchrone depuis le thread live — ne pas bloquer ici."""
        with self._lock:
            self._context.append(text)
        if not looks_like_question(text, self.cfg.user_name):
            return
        logger.info("Segment suspect (question) [%s] : %s", stamp, text)
        try:
            threading.Thread(
                target=self._process_question, args=(text,), daemon=True
            ).start()
        except RuntimeError:
            logger.exception("Impossible de lancer l'analyse LLM pour une question")

    def _process_question(self, segment: str) -> None:
        """Analyse LLM + génération de réponse (thread worker dédié)."""
        with self._lock:
            context = list(self._context)
        if not self.llm.cfg.enabled:
            logger.warning(
                "Question détectée mais le mode IA est désactivé ; activez ai.enabled."
            )
            self._notify(
                "Question détectée — activez ai.enabled et un LLM local pour les réponses."
            )
            return

        if not self.llm.meeting_is_question(segment, self.cfg.user_name, context):
            logger.debug("Le LLM a rejeté le segment comme question personnelle.")
            return

        reply = self.llm.meeting_reply(
            segment,
            context,
            self.cfg.user_context,
            self.cfg.reply_prompt,
            self.cfg.user_name,
        )
        if not reply:
            logger.warning("Le LLM n'a pas généré de réponse.")
            return

        logger.info("Réponse suggérée : %s", reply)
        with self._lock:
            self._replies.append(reply)

        if self.cfg.auto_inject:
            try:
                self.injector.inject(reply)
                self._notify(f"Réponse injectée : {reply[:120]}")
            except Exception:  # noqa: BLE001
                logger.exception("Injection de la réponse échouée")
                self._notify("Réponse générée mais injection échouée (voir logs).")
        else:
            try:
                self.injector.copy_to_clipboard(reply)
            except Exception:  # noqa: BLE001
                logger.exception("Copie presse-papiers échouée")
            self._notify(f"Réponse copiée : {reply[:120]}")

        if self.history is not None:
            try:
                self.history.add(
                    f"Q: {segment}\nR: {reply}",
                    source="réunion",
                    app=self.cfg.user_name or "réunion",
                    model=self.llm.cfg.model,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Archivage de la réponse échoué")

    def _notify(self, message: str) -> None:
        if self._on_notify is not None:
            try:
                self._on_notify(message)
            except Exception:  # noqa: BLE001
                logger.exception("on_notify a levé une exception")

    def _on_finished_wrapper(self, result: dict) -> None:
        result = dict(result)
        with self._lock:
            result["replies"] = list(self._replies)
            result["reply_count"] = len(self._replies)
        callback = self._on_finished
        if callback is not None:
            try:
                callback(result)
            except Exception:  # noqa: BLE001
                logger.exception("on_finished (réunion) a levé une exception")
