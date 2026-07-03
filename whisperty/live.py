"""Whisperty — transcription live d'une sortie audio (V2).

Capture en continu le son d'une sortie (loopback WASAPI via :mod:`loopback`),
découpe le flux en segments aux frontières de silence (VAD RMS simple), transcrit
chaque segment avec faster-whisper et écrit le résultat au fil de l'eau dans un
fichier ``transcriptions/live_<horodatage>.txt``. Pensé pour suivre une confcall
(Teams, Meet…) sans importer de fichier.

Concurrence : DEUX threads. Un thread de **capture** lit le périphérique sans
interruption (segmentation incluse, triviale) et empile les segments dans une file ;
un thread **worker** les transcrit en parallèle. La transcription (lente) ne suspend
JAMAIS la capture — sinon le tampon WASAPI borné de ``soundcard`` déborderait et des
morceaux d'audio seraient perdus pendant le traitement. Arrêt par ``threading.Event``.
Le segmenteur (:class:`_Segmenter`) est une logique pure (testable hors-ligne, sans
audio ni modèle).

Confidentialité : tout est local (capture loopback + Whisper local). Aucun réseau.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np

from . import loopback

logger = logging.getLogger(__name__)

SAMPLE_RATE = loopback.SAMPLE_RATE  # 16 kHz


class _Segmenter:
    """Découpe un flux audio en segments aux frontières de silence (logique pure).

    Accumule les blocs ; rend un segment lorsque (a) une parole a été détectée puis
    suivie d'un silence assez long, ou (b) la durée max d'un segment est atteinte.
    Les segments sans parole sont écartés (``None``) pour ne pas transcrire du silence.
    """

    def __init__(
        self,
        samplerate: int,
        vad_threshold: float,
        silence_duration: float,
        max_segment: float,
    ) -> None:
        self.samplerate = samplerate
        self.vad_threshold = vad_threshold
        self.silence_duration = silence_duration
        self.max_segment = max_segment
        self._reset()

    def _reset(self) -> None:
        self._buffer: list[np.ndarray] = []
        self._seg_len = 0.0
        self._silence_run = 0.0
        self._had_speech = False

    def push(self, block: np.ndarray) -> Optional[np.ndarray]:
        """Ajoute un bloc ; renvoie un segment complété à transcrire, ou ``None``."""
        if block is None or block.size == 0:
            return None
        duration = block.shape[0] / self.samplerate
        rms = float(np.sqrt(np.mean(np.square(block))))
        self._buffer.append(block)
        self._seg_len += duration
        if rms >= self.vad_threshold:
            self._had_speech = True
            self._silence_run = 0.0
        else:
            self._silence_run += duration

        end_of_utterance = self._had_speech and self._silence_run >= self.silence_duration
        too_long = self._seg_len >= self.max_segment
        if end_of_utterance or too_long:
            return self._flush()
        return None

    def flush_final(self) -> Optional[np.ndarray]:
        """Vide le tampon résiduel (à l'arrêt). Renvoie le segment s'il contient de la parole."""
        return self._flush()

    def _flush(self) -> Optional[np.ndarray]:
        had_speech = self._had_speech
        buffer = self._buffer
        self._reset()
        if not had_speech or not buffer:
            return None
        return np.concatenate(buffer).astype(np.float32, copy=False)


class LiveTranscriber:
    """Capture loopback + transcription continue d'une sortie audio."""

    def __init__(
        self,
        config,
        transcriber,
        on_segment: Optional[Callable[[str, str], None]] = None,
        on_finished: Optional[Callable[[dict], None]] = None,
        transcript_prefix: str = "live",
    ) -> None:
        self._config = config
        self.cfg = config.live
        self.transcriber = transcriber
        self._on_segment = on_segment
        self._on_finished = on_finished
        self._transcript_prefix = transcript_prefix or "live"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._file = None
        self._segments: list[str] = []
        self._error: Optional[str] = None
        # Notes utilisateur en session (UC-16). Elles arrivent d'AUTRES threads
        # (pont GUI, raccourci signet) que le worker : _segments/_notes/_file sont
        # protégés par ce verrou FEUILLE (jamais imbriqué avec un autre verrou).
        self._note_lock = threading.Lock()
        self._notes: list[tuple[str, str]] = []  # (horodatage HH:MM:SS, texte)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- cycle de vie ----------------------------------------------------------
    def start(self, device_spec: Optional[Union[int, str]] = None) -> bool:
        """Démarre la capture+transcription dans un thread dédié. False si déjà en cours."""
        if self.is_running():
            return False
        self._stop.clear()
        self._segments = []
        self._notes = []
        self._error = None
        try:
            self._thread = threading.Thread(
                target=self._run, args=(device_spec,), daemon=True
            )
            self._thread.start()
        except RuntimeError:
            logger.exception("Démarrage du thread de transcription live impossible")
            self._thread = None
            return False
        return True

    def stop(self) -> None:
        """Demande l'arrêt (non bloquant) ; le thread finalise et appelle on_finished."""
        self._stop.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        """Attend la fin du thread (utilisé à l'arrêt de l'application)."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # -- worker ----------------------------------------------------------------
    def _run(self, device_spec: Optional[Union[int, str]]) -> None:
        device_name: Optional[str] = None
        path: Optional[Path] = None
        try:
            # COM doit être initialisé sur CE thread (worker) : soundcard ne le fait que
            # sur le thread d'import. Sans cela : CO_E_NOTINITIALIZED (0x800401F0).
            with loopback.com_initialized():
                device_name, mic = loopback.resolve_loopback(device_spec)
                path = self._open_transcript(device_name)
                logger.info("Transcription live démarrée (sortie : %s).", device_name)
                block_frames = max(1, int(SAMPLE_RATE * self.cfg.block_duration))
                with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=block_frames) as rec:
                    self._consume(lambda n: np.asarray(rec.record(numframes=n)).reshape(-1))
        except loopback.SoundcardUnavailableError as exc:
            logger.error("%s", exc)
            self._error = str(exc)
        except loopback.LoopbackError as exc:
            logger.error("%s", exc)
            self._error = str(exc)
        except Exception:  # noqa: BLE001 — ne jamais propager dans le thread
            logger.exception("Transcription live échouée")
            self._error = "Erreur de transcription live (voir logs)."
        finally:
            self._close_transcript()
            self._finish(device_name, path)

    def _consume(self, record_fn: Callable[[int], np.ndarray]) -> None:
        """Capture en continu (CE thread) ET transcrit (thread worker) sans jamais
        suspendre la lecture du périphérique.

        ⚠️ Le tampon interne du loopback WASAPI (``soundcard``) est borné : si l'on
        cesse d'appeler ``record_fn`` — par exemple le temps de transcrire un segment
        (plusieurs secondes en CPU) — il déborde et des morceaux d'audio sont perdus.
        On lit donc le périphérique sans interruption ici (la segmentation est triviale)
        et on délègue la transcription, lente, à un thread worker via une file. En cas de
        retard, la latence augmente ; il n'y a jamais de perte. La file est non bornée :
        sur une machine où la transcription suit le temps réel (cas normal) elle reste
        quasi vide ; un dépassement durable se traduit par de la latence, pas une coupure.
        """
        segmenter = _Segmenter(
            SAMPLE_RATE, self.cfg.vad_threshold, self.cfg.silence_duration, self.cfg.max_segment
        )
        block_frames = max(1, int(SAMPLE_RATE * self.cfg.block_duration))
        seg_queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        worker = threading.Thread(
            target=self._transcribe_loop, args=(seg_queue,), daemon=True
        )
        worker.start()
        try:
            while not self._stop.is_set():
                try:
                    block = record_fn(block_frames)
                except Exception:  # noqa: BLE001 — périphérique retiré, etc.
                    logger.exception("Capture loopback interrompue")
                    break
                completed = segmenter.push(block)
                if completed is not None:
                    seg_queue.put(completed)
            # Vide le segment en cours à l'arrêt (mis en file avant la sentinelle :
            # il sera bien transcrit par le worker avant l'arrêt de ce dernier).
            final = segmenter.flush_final()
            if final is not None:
                seg_queue.put(final)
        finally:
            seg_queue.put(None)  # sentinelle : termine la boucle du worker
            worker.join()        # attend la transcription des segments déjà capturés

    def _transcribe_loop(self, seg_queue: "queue.Queue") -> None:
        """Thread worker : transcrit les segments capturés sans bloquer la capture.

        S'arrête à la réception de la sentinelle ``None`` (poussée à l'arrêt, une fois
        le dernier segment mis en file). ``_handle_segment`` absorbe les erreurs : un
        segment fautif n'interrompt pas la boucle."""
        while True:
            audio = seg_queue.get()
            if audio is None:
                break
            self._handle_segment(audio)

    def _handle_segment(self, audio: np.ndarray) -> None:
        # En live, on n'applique PAS le raffinage LLM (self.llm.refine) utilisé en dictée :
        # il ajouterait une latence par segment incompatible avec le suivi « au fil de l'eau ».
        # Le dictionnaire, lui, reste appliqué via transcriber.transcribe().
        try:
            text = self.transcriber.transcribe(audio)
        except Exception:  # noqa: BLE001 — un segment fautif ne doit pas tout arrêter
            logger.exception("Transcription d'un segment live échouée")
            return
        text = (text or "").strip()
        if text:
            self._emit(text)

    def _emit(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._note_lock:
            self._segments.append(text)
            self._write_locked(f"[{stamp}] {text}\n")
        logger.info("Live [%s] %s", stamp, text)
        if self._on_segment is not None:
            try:
                self._on_segment(stamp, text)
            except Exception:  # noqa: BLE001
                logger.exception("on_segment a levé une exception")

    def _write_locked(self, data: str) -> None:
        """Écrit dans le transcript (``_note_lock`` TENU par l'appelant).

        Un échec d'écriture n'interrompt jamais la session : le texte reste de
        toute façon en mémoire (``_segments``/``_notes``) et est restitué à l'arrêt.
        """
        if self._file is None:
            return
        try:
            self._file.write(data)
            self._file.flush()
        except OSError:
            logger.warning("Écriture du transcript live échouée.", exc_info=True)

    # -- notes utilisateur (UC-16) ----------------------------------------------
    def add_note(self, text: str, stamp: Optional[str] = None) -> Optional[str]:
        """Ajoute une note utilisateur horodatée au transcript en cours (UC-16).

        Appelée depuis le pont GUI ou le raccourci signet (threads ≠ worker) ; les
        structures partagées sont protégées par ``_note_lock``. ``stamp`` optionnel =
        horodatage du segment cité (note-citation), sinon l'instant de validation.
        Renvoie la ligne affichable (``[Note] …``), ou ``None`` si la note est vide
        ou la session inactive. Ne bloque jamais la capture ni la transcription.
        """
        text = " ".join(str(text or "").split())
        if not text or not self.is_running():
            return None
        stamp = stamp or datetime.now().strftime("%H:%M:%S")
        line = f"[Note] {text}"
        with self._note_lock:
            self._notes.append((stamp, text))
            self._segments.append(line)
            self._write_locked(f"[{stamp}] {line}\n")
        logger.info("Live [%s] %s", stamp, line)
        return line

    # -- transcript ------------------------------------------------------------
    def _open_transcript(self, device_name: str) -> Optional[Path]:
        """Ouvre le fichier transcript. En cas d'erreur d'écriture, la transcription
        continue SANS fichier (le texte est de toute façon copié/historisé à l'arrêt)."""
        try:
            folder = self._config.resolve(self.cfg.transcript_dir)
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = folder / f"{self._transcript_prefix}_{stamp}.txt"
            self._file = path.open("w", encoding="utf-8")
            self._file.write(
                f"# Transcription live — sortie : {device_name} — "
                f"{datetime.now().isoformat(timespec='seconds')}\n\n"
            )
            self._file.flush()
            return path
        except OSError:
            logger.warning(
                "Transcript non inscriptible dans « %s » ; la transcription continue "
                "sans fichier.", self.cfg.transcript_dir, exc_info=True,
            )
            self._file = None
            return None

    def _close_transcript(self) -> None:
        # Sous _note_lock : une note concurrente ne doit ni écrire dans un fichier
        # fermé, ni être omise du récapitulatif de fin (FR-26).
        with self._note_lock:
            if self._file is None:
                return
            if self._notes:
                self._write_locked(
                    "\n# Notes\n"
                    + "".join(f"[{stamp}] {text}\n" for stamp, text in self._notes)
                )
            try:
                self._file.close()
            except OSError:
                logger.warning("Fermeture du transcript live échouée.", exc_info=True)
            finally:
                self._file = None

    def _finish(self, device_name: Optional[str], path: Optional[Path]) -> None:
        # Instantané sous verrou : des notes peuvent encore arriver d'autres threads.
        with self._note_lock:
            text = "\n".join(self._segments).strip()
            segment_count = len(self._segments) - len(self._notes)
            note_count = len(self._notes)
        result = {
            "text": text,
            "device": device_name,
            "segments": segment_count,
            "notes": note_count,
            "path": str(path) if path is not None else None,
            "error": self._error,
        }
        callback = self._on_finished
        try:
            if callback is not None:
                callback(result)
        except Exception:  # noqa: BLE001
            logger.exception("on_finished a levé une exception")
        finally:
            # Nuller le thread APRÈS le callback : wait()/is_running() restent cohérents
            # pendant toute la finalisation (le callback compris). Ainsi quit()->wait()
            # bloque réellement jusqu'à la fin de _on_live_finished avant history.close().
            self._thread = None
