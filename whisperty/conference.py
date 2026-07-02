"""Whisperty — transcription de réunion / confcall (V2, double-source).

Capture SIMULTANÉMENT le micro (ma voix) ET une sortie audio système (loopback =
interlocuteurs distants : Teams/Meet/Zoom), **mixe** les deux en un flux mono 16 kHz
(somme + normalisation anti-saturation) et transcrit en continu → UNE transcription
unique, sans distinction de locuteur (itération 1). Export incrémental .txt/.md +
historique (``source="réunion"``).

Réutilise, ne réinvente pas :
- micro : :class:`whisperty.recorder.AudioRecorder` en **mode streaming** (``frame_callback``,
  RAM bornée) + ``_resample`` vers 16 kHz ;
- son système : :mod:`whisperty.loopback` (``soundcard`` WASAPI, ``com_initialized``) ;
- segmentation : :class:`whisperty.live._Segmenter` (VAD RMS).

Confidentialité : 100 % local, aucun réseau.

Concurrence : le micro (callback PortAudio) et le son système (thread ``soundcard``)
alimentent chacun un tampon thread-safe ; un thread mixeur draine, mixe, segmente et
transcrit. Arrêt par ``threading.Event`` ; retour à IDLE via le callback de fin
(jamais de ``join()`` sous le verrou de l'application).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np

from . import loopback
from .live import _Segmenter
from .recorder import AudioRecorder, MicrophoneError, _resample

logger = logging.getLogger(__name__)

SAMPLE_RATE = loopback.SAMPLE_RATE  # 16 kHz commun aux deux sources


# -- helpers purs (testables hors-ligne) --------------------------------------
def mix_streams(streams: list[np.ndarray]) -> np.ndarray:
    """Mixe des flux mono float32 par **somme**, avec **normalisation anti-saturation**.

    Tronque à la longueur du plus court flux (alignement). Avec un seul flux, le
    renvoie (tronqué si besoin). Renvoie un tableau vide si aucun flux exploitable.
    """
    arrays = [
        np.asarray(s, dtype=np.float32).reshape(-1)
        for s in streams
        if s is not None and np.asarray(s).size
    ]
    if not arrays:
        return np.zeros(0, dtype=np.float32)
    length = min(a.shape[0] for a in arrays)
    if length == 0:
        return np.zeros(0, dtype=np.float32)
    mixed = np.zeros(length, dtype=np.float32)
    for a in arrays:
        mixed += a[:length]
    peak = float(np.max(np.abs(mixed)))
    if peak > 1.0:
        mixed = mixed / peak  # normalisation : ramène la crête à 1.0 (anti-saturation)
    return mixed


def format_segment_line(elapsed_seconds: float, text: str, speaker: Optional[str] = None) -> str:
    """Formate une ligne de transcript : ``[MM:SS] texte`` ou ``[MM:SS] Locuteur : texte``."""
    elapsed_seconds = max(0.0, elapsed_seconds)
    stamp = f"{int(elapsed_seconds // 60):02d}:{int(elapsed_seconds % 60):02d}"
    prefix = f"[{stamp}] " + (f"{speaker} : " if speaker else "")
    return prefix + text


def _transcript_header(device_sys: Optional[str], mic_label: str, fmt: str) -> str:
    when = datetime.now().isoformat(timespec="seconds")
    if fmt == "md":
        return (
            f"# Transcription de réunion\n\n"
            f"- Date : {when}\n- Sortie système : {device_sys}\n- Micro : {mic_label}\n\n"
        )
    return (
        f"# Transcription de réunion — {when}\n"
        f"# Sortie système : {device_sys} | Micro : {mic_label}\n\n"
    )


class _StreamBuffer:
    """Tampon mono float32 thread-safe : alimenté par une source, drainé par le mixeur."""

    def __init__(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()

    def push(self, block: np.ndarray) -> None:
        b = np.asarray(block, dtype=np.float32).reshape(-1)
        if b.size == 0:
            return
        with self._lock:
            self._buf = np.concatenate([self._buf, b])

    def available(self) -> int:
        with self._lock:
            return self._buf.shape[0]

    def take(self, n: int) -> np.ndarray:
        with self._lock:
            n = min(max(0, n), self._buf.shape[0])
            out = self._buf[:n]
            self._buf = self._buf[n:]
            return out


class ConferenceTranscriber:
    """Capture micro + sortie système, mixe et transcrit en continu (itération 1)."""

    def __init__(
        self,
        config,
        transcriber,
        on_finished: Optional[Callable[[dict], None]] = None,
        on_segment: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._config = config
        self.cfg = config.conference
        self.transcriber = transcriber
        self._on_finished = on_finished
        self._on_segment = on_segment
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._file = None
        # Chaque segment : (instant de début en s, locuteur ou None, texte). En mode
        # « distinction », le locuteur est renseigné et les segments sont triés à la fin.
        self._segments: list[tuple[float, Optional[str], str]] = []
        self._error: Optional[str] = None
        self._t0 = 0.0
        self._buffers: dict[str, _StreamBuffer] = {"mic": _StreamBuffer(), "system": _StreamBuffer()}
        self._active: set[str] = set()
        self._mic_recorder: Optional[AudioRecorder] = None
        self._system_name: Optional[str] = None
        self._system_ready = threading.Event()
        self._aligned = False  # les deux flux ont-ils été calés temporellement ?
        self._align_attempts = 0  # ticks passés à attendre que les 2 sources produisent
        # Distinction des locuteurs par source (itération 2) : pas de mixage.
        self._distinct = bool(getattr(self.cfg, "distinguish_speakers", False))
        self._fmt = "md" if str(self.cfg.export_format).lower() == "md" else "txt"
        self._device_sys: Optional[str] = None
        self._mic_label_header = ""

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- cycle de vie ----------------------------------------------------------
    def start(self, system_spec: Optional[Union[int, str]] = None) -> bool:
        """Démarre la capture+transcription de réunion. False si déjà en cours."""
        if self.is_running():
            return False
        self._stop.clear()
        self._segments = []
        self._error = None
        self._active = set()
        self._buffers = {"mic": _StreamBuffer(), "system": _StreamBuffer()}
        self._system_name = None
        self._system_ready = threading.Event()
        try:
            self._thread = threading.Thread(target=self._run, args=(system_spec,), daemon=True)
            self._thread.start()
        except RuntimeError:
            logger.exception("Démarrage du thread de réunion impossible")
            self._thread = None
            return False
        return True

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # -- worker ----------------------------------------------------------------
    def _run(self, system_spec: Optional[Union[int, str]]) -> None:
        device_sys: Optional[str] = None
        path: Optional[Path] = None
        sys_thread: Optional[threading.Thread] = None
        try:
            # Micro (synchrone, mode streaming). Échec → réunion sur son système seul.
            mic_label = self._start_mic()
            # Son système : thread dédié (COM par thread). Échec → micro seul.
            sys_thread = threading.Thread(
                target=self._system_loop, args=(system_spec,), daemon=True
            )
            sys_thread.start()
            self._system_ready.wait(timeout=6.0)  # résolution loopback (rapide en pratique)
            device_sys = self._system_name

            if self._stop.is_set():
                return  # arrêt demandé pendant le démarrage : on file vers le finally
            if not self._active:
                self._error = "Aucune source audio (micro et sortie système indisponibles)."
                return

            self._device_sys = device_sys
            self._mic_label_header = mic_label or "(micro indisponible)"
            path = self._open_transcript(device_sys, self._mic_label_header)
            self._t0 = time.monotonic()
            logger.info(
                "Réunion démarrée (sources : %s ; mode : %s).",
                ", ".join(sorted(self._active)),
                "distinction par source" if self._distinct else "mixage",
            )
            if self._distinct:
                self._consume_distinct()
            else:
                self._consume()
        except Exception:  # noqa: BLE001 — ne jamais propager dans le thread
            logger.exception("Transcription de réunion échouée")
            self._error = self._error or "Erreur de transcription de réunion (voir logs)."
        finally:
            self._stop.set()  # arrête le thread système
            self._stop_mic()
            if sys_thread is not None and sys_thread.is_alive():
                sys_thread.join(timeout=2.0)
                if sys_thread.is_alive():
                    # record() bloqué (périphérique retiré ?) : loopback non libéré. Le tampon
                    # local du thread (cf. _system_loop) évite qu'il corrompe une session future.
                    logger.warning(
                        "Thread de capture système toujours actif (loopback non libéré ; "
                        "périphérique probablement retiré)."
                    )
            self._close_transcript()
            self._finish(device_sys, path)

    # -- sources ---------------------------------------------------------------
    def _start_mic(self) -> Optional[str]:
        """Démarre le micro en mode streaming. Renvoie un libellé, ou None si indispo."""
        recorder = AudioRecorder(
            device=self.cfg.mic_device,
            samplerate=SAMPLE_RATE,
            frame_callback=self._on_mic_block,
        )
        # Assigné AVANT start() : le callback PortAudio peut se déclencher aussitôt et
        # a besoin de capture_rate (déjà fixé par start() avant le 1er bloc).
        self._mic_recorder = recorder
        try:
            recorder.start()
        except MicrophoneError as exc:
            logger.error("Micro indisponible : %s — réunion sur son système seul.", exc)
            self._mic_recorder = None
            return None
        except Exception:  # noqa: BLE001
            logger.exception("Micro indisponible")
            self._mic_recorder = None
            return None
        self._active.add("mic")
        if self.cfg.mic_device in (None, ""):
            return "micro par défaut"
        return str(self.cfg.mic_device)

    def _on_mic_block(self, block: np.ndarray) -> None:
        """Callback PortAudio : downmix mono + resample 16 kHz, puis empile (léger)."""
        b = block
        if b.ndim > 1:
            b = b.mean(axis=1)
        b = np.asarray(b, dtype=np.float32).reshape(-1)
        # Référence locale : _stop_mic (autre thread) peut nuller _mic_recorder entre le
        # test et la lecture de capture_rate — la capture évite l'AttributeError.
        recorder = self._mic_recorder
        rate = recorder.capture_rate if recorder is not None else SAMPLE_RATE
        if rate != SAMPLE_RATE:
            b = _resample(b, rate, SAMPLE_RATE)
        self._buffers["mic"].push(b)

    def _stop_mic(self) -> None:
        if self._mic_recorder is not None:
            try:
                self._mic_recorder.stop()
            except Exception:  # noqa: BLE001
                logger.debug("Arrêt du micro de réunion échoué.", exc_info=True)
            self._mic_recorder = None

    def _system_loop(self, system_spec: Optional[Union[int, str]]) -> None:
        """Thread de capture du son système (loopback). COM initialisé sur CE thread."""
        try:
            with loopback.com_initialized():
                name, mic = loopback.resolve_loopback(system_spec)
                self._system_name = name
                # Référence locale au tampon : si ce thread devient orphelin (record()
                # bloqué) et qu'une NOUVELLE réunion démarre, il poussera dans SON ancien
                # tampon (jeté) et non dans celui de la nouvelle session.
                sysbuf = self._buffers["system"]
                self._active.add("system")
                self._system_ready.set()  # résolu → _run peut continuer
                block_frames = max(1, int(SAMPLE_RATE * self.cfg.block_duration))
                with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=block_frames) as rec:
                    while not self._stop.is_set():
                        block = np.asarray(rec.record(numframes=block_frames)).reshape(-1)
                        sysbuf.push(block)
        except (loopback.SoundcardUnavailableError, loopback.LoopbackError) as exc:
            logger.error("Son système indisponible : %s — réunion sur micro seul.", exc)
            # Mort/échec (≠ arrêt normal) : retirer la source pour ne pas geler le mixage.
            self._active.discard("system")
        except Exception:  # noqa: BLE001
            logger.exception("Capture du son système interrompue.")
            self._active.discard("system")
        finally:
            self._system_ready.set()  # débloque _run même en cas d'échec

    # -- mixage + transcription ------------------------------------------------
    def _consume(self) -> None:
        segmenter = _Segmenter(
            SAMPLE_RATE, self.cfg.vad_threshold, self.cfg.silence_duration, self.cfg.max_segment
        )
        target = max(1, int(SAMPLE_RATE * self.cfg.block_duration))
        # Seuil de « famine » : une source active qui ne produit plus pendant que l'autre
        # accumule = périphérique mort. On la retire pour ne pas geler le mixage aligné.
        stall_limit = max(2, int(round(2.0 / max(self.cfg.block_duration, 0.05))))
        stalled = {"mic": 0, "system": 0}
        self._aligned = False
        self._align_attempts = 0
        while not self._stop.is_set():
            self._stop.wait(self.cfg.block_duration)  # tick réveillable par stop()
            if not self._aligned:
                self._align_sources()
            else:
                self._drop_stalled_sources(stalled, stall_limit)
            mixed = self._drain_mixed(target)
            if mixed is None or mixed.size == 0:
                continue
            completed = segmenter.push(mixed)
            if completed is not None:
                self._handle_segment(completed)
        self._final_drain(segmenter, target)

    def _align_sources(self) -> None:
        """Cale temporellement les flux : défausse l'excédent de tête de la source en
        avance (le micro démarre avant le son système). Une seule fois, dès que les deux
        sources actives ont produit. Avec moins de deux sources, rien à aligner.

        Garde-fou : si une source active ne produit JAMAIS (record() bloqué sur un
        périphérique mort), on la retire après un délai de grâce — sinon ``_aligned``
        resterait faux et le mixage aligné (mode mixé) gèlerait, ou la source fantôme
        traînerait dans ``_active`` (mode distinction)."""
        active = [s for s in ("mic", "system") if s in self._active]
        if len(active) < 2:
            self._aligned = True
            return
        avails = {s: self._buffers[s].available() for s in active}
        base = min(avails.values())
        if base <= 0:
            self._align_attempts += 1
            grace = max(2, int(round(4.0 / max(self.cfg.block_duration, 0.05))))
            if self._align_attempts >= grace:
                for source in active:
                    if avails[source] == 0:
                        self._active.discard(source)
                        logger.warning(
                            "Source « %s » sans aucune donnée au démarrage : retirée ; "
                            "réunion sur l'autre source.", source,
                        )
                self._aligned = True
            return  # attend que les DEUX sources aient produit (ou expiration ci-dessus)
        for source, avail in avails.items():
            excess = avail - base
            if excess > 0:
                self._buffers[source].take(excess)  # jette l'excédent de tête (pré-démarrage)
        self._aligned = True

    def _drop_stalled_sources(self, stalled: dict, stall_limit: int) -> None:
        """Retire une source qui ne produit plus (périphérique mort / record() bloqué),
        pour que le mixage aligné (``min``) continue sur la source survivante."""
        active = [s for s in ("mic", "system") if s in self._active]
        if len(active) < 2:
            return
        avails = {s: self._buffers[s].available() for s in active}
        backlog = max(avails.values())
        for source in active:
            if avails[source] == 0 and backlog > 0:
                stalled[source] += 1
                if stalled[source] >= stall_limit:
                    self._active.discard(source)
                    logger.warning(
                        "Source « %s » muette depuis ~%.0f s : retirée ; réunion poursuivie "
                        "sur l'autre source.", source, stall_limit * self.cfg.block_duration,
                    )
            else:
                stalled[source] = 0

    def _final_drain(self, segmenter: "_Segmenter", target: int) -> None:
        """Vidage à l'arrêt : partie alignée, PUIS reliquat non aligné de chaque source
        (la source la plus longue a une queue que ``min`` ne draine pas → ne pas la perdre)."""
        while True:
            mixed = self._drain_mixed(target * 8)
            if mixed is None or mixed.size == 0:
                break
            completed = segmenter.push(mixed)
            if completed is not None:
                self._handle_segment(completed)
        for source in [s for s in ("mic", "system") if s in self._active]:
            leftover = self._buffers[source].take(self._buffers[source].available())
            if leftover.size:
                completed = segmenter.push(mix_streams([leftover]))
                if completed is not None:
                    self._handle_segment(completed)
        final = segmenter.flush_final()
        if final is not None:
            self._handle_segment(final)

    def _drain_mixed(self, target: int) -> Optional[np.ndarray]:
        """Draine une longueur **alignée** (min des sources actives) et mixe."""
        active = [s for s in ("mic", "system") if s in self._active]
        if not active:
            return None
        avail = min(self._buffers[s].available() for s in active)
        if avail <= 0:
            return None
        m = min(avail, target * 8)  # borne par tick pour éviter des chunks énormes
        streams = [self._buffers[s].take(m) for s in active]
        return mix_streams(streams)

    def _handle_segment(self, audio: np.ndarray) -> None:
        # Pas de raffinage LLM en réunion (latence par segment), comme en live ;
        # le dictionnaire reste appliqué via transcriber.transcribe().
        try:
            text = self.transcriber.transcribe(audio)
        except Exception:  # noqa: BLE001
            logger.exception("Transcription d'un segment de réunion échouée")
            return
        text = (text or "").strip()
        if text:
            self._emit(text)

    def _emit(self, text: str) -> None:
        """Mode mixé (itération 1) : un segment sans étiquette de locuteur."""
        elapsed = max(0.0, time.monotonic() - self._t0)
        self._segments.append((elapsed, None, text))
        self._write_line(format_segment_line(elapsed, text), text)

    def _write_line(self, line: str, text: str) -> None:
        """Écrit une ligne dans le transcript (au fil de l'eau), journalise, notifie."""
        if self._file is not None:
            try:
                self._file.write(line + "\n")
                self._file.flush()
            except OSError:
                logger.warning("Écriture du transcript de réunion échouée.", exc_info=True)
        logger.info("Réunion %s", line)
        if self._on_segment is not None:
            try:
                self._on_segment(line, text)
            except Exception:  # noqa: BLE001
                logger.exception("on_segment a levé une exception")

    # -- distinction par source (itération 2) ----------------------------------
    def _consume_distinct(self) -> None:
        """Transcrit chaque source SÉPARÉMENT (pas de mixage), horodate par position
        audio, puis entrelace chronologiquement à l'arrêt (cf. _finish)."""
        segmenters = {
            s: _Segmenter(
                SAMPLE_RATE, self.cfg.vad_threshold, self.cfg.silence_duration, self.cfg.max_segment
            )
            for s in ("mic", "system")
        }
        pushed = {"mic": 0, "system": 0}  # échantillons poussés par source = repère temporel
        target = max(1, int(SAMPLE_RATE * self.cfg.block_duration))
        stall_limit = max(2, int(round(2.0 / max(self.cfg.block_duration, 0.05))))
        stalled = {"mic": 0, "system": 0}
        self._aligned = False
        self._align_attempts = 0
        while not self._stop.is_set():
            self._stop.wait(self.cfg.block_duration)
            if not self._aligned:
                self._align_sources()
            else:
                self._drop_stalled_sources(stalled, stall_limit)
            self._drain_per_source(segmenters, pushed, target, final=False)
        # Vidage final : reliquats par source + flush des segments en cours.
        self._drain_per_source(segmenters, pushed, target, final=True)
        for source in ("mic", "system"):
            final = segmenters[source].flush_final()
            if final is not None:
                self._emit_distinct(source, pushed[source], final)

    def _drain_per_source(self, segmenters: dict, pushed: dict, target: int, final: bool) -> None:
        """Draine chaque source indépendamment (pas d'alignement entre sources)."""
        for source in [s for s in ("mic", "system") if s in self._active]:
            buf = self._buffers[source]
            while buf.available() >= target or (final and buf.available() > 0):
                block = buf.take(target)
                if block.size == 0:
                    break
                pushed[source] += block.shape[0]
                completed = segmenters[source].push(block)
                if completed is not None:
                    self._emit_distinct(source, pushed[source], completed)

    def _emit_distinct(self, source: str, pushed_after: int, audio: np.ndarray) -> None:
        """Transcrit un segment d'une source et émet ses sous-segments horodatés (absolus)."""
        # Position de début du segment dans la réunion (échantillons poussés - longueur).
        chunk_start = max(0, pushed_after - audio.shape[0]) / SAMPLE_RATE
        label = self.cfg.mic_label if source == "mic" else self.cfg.system_label
        try:
            subs = self.transcriber.transcribe_segments(audio)
        except Exception:  # noqa: BLE001
            logger.exception("Transcription d'un segment (%s) échouée", source)
            return
        for start, _end, text in subs:
            text = (text or "").strip()
            if not text:
                continue
            abs_start = chunk_start + max(0.0, float(start))
            self._segments.append((abs_start, label, text))
            self._write_line(format_segment_line(abs_start, text, speaker=label), text)

    # -- transcript ------------------------------------------------------------
    def _open_transcript(self, device_sys: Optional[str], mic_label: str) -> Optional[Path]:
        fmt = "md" if str(self.cfg.export_format).lower() == "md" else "txt"
        try:
            folder = self._config.resolve(self.cfg.export_dir)
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = folder / f"reunion_{stamp}.{fmt}"
            self._file = path.open("w", encoding="utf-8")
            self._file.write(_transcript_header(device_sys, mic_label, fmt))
            self._file.flush()
            return path
        except OSError:
            logger.warning(
                "Transcript de réunion non inscriptible dans « %s » ; "
                "la transcription continue sans fichier.", self.cfg.export_dir, exc_info=True,
            )
            self._file = None
            return None

    def _close_transcript(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None

    def _rewrite_sorted(self, path: Optional[Path], ordered: list) -> None:
        """Réécrit le transcript trié chronologiquement (entrelacement des deux sources).

        Écriture atomique (fichier temporaire + ``os.replace``) : si elle échoue, la
        version au fil de l'eau (complète mais non triée) reste intacte au lieu d'être tronquée."""
        if path is None:
            return
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(_transcript_header(self._device_sys, self._mic_label_header, self._fmt))
                for start, label, text in ordered:
                    fh.write(format_segment_line(start, text, speaker=label) + "\n")
            os.replace(tmp, path)  # atomique (NTFS) : l'original n'est remplacé qu'en cas de succès
        except OSError:
            logger.warning(
                "Réécriture triée du transcript échouée ; version au fil de l'eau conservée.",
                exc_info=True,
            )
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def _finish(self, device_sys: Optional[str], path: Optional[Path]) -> None:
        # Entrelacement chronologique : tri par instant de début (identité en mode mixé,
        # vrai entrelacement des deux sources en mode distinction).
        ordered = sorted(self._segments, key=lambda record: record[0])
        if self._distinct:
            body = "\n".join(
                format_segment_line(start, text, speaker=label) for start, label, text in ordered
            )
            self._rewrite_sorted(path, ordered)  # le fichier final reflète l'ordre chronologique
        else:
            body = "\n".join(text for _start, _label, text in ordered)
        result = {
            "text": body.strip(),
            "device": device_sys,
            "sources": sorted(self._active),
            "segments": len(self._segments),
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
            # Nuller le thread APRÈS le callback (cf. live : wait()/is_running() cohérents).
            self._thread = None
