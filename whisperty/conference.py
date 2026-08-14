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
import queue
import re
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


def parse_stamp(stamp: Optional[str]) -> Optional[float]:
    """Convertit un horodatage « MM:SS » (position de session) en secondes.

    Logique pure (testable hors-ligne). ``None`` si absent ou invalide — le format
    vient de l'interface (cliente : on ne lui fait pas confiance).
    """
    if not stamp:
        return None
    m = re.fullmatch(r"(\d{1,4}):([0-5]\d)", str(stamp).strip())
    if not m:
        return None
    return float(int(m.group(1)) * 60 + int(m.group(2)))


def render_notes_recap(notes: list, fmt: str) -> str:
    """Section récapitulative « Notes » de fin de transcript (FR-26). Logique pure."""
    heading = "\n## Notes\n\n" if fmt == "md" else "\n# Notes\n"
    return heading + "".join(
        format_segment_line(elapsed, text) + "\n" for elapsed, text in notes
    )


def render_payload_lines(payload: dict) -> list[str]:
    """Lignes du transcript re-rendues depuis une structure de session archivée (FR-31).

    Les libellés sont résolus au rendu depuis le registre embarqué (``speakers``) —
    le renommage post-session est donc rétroactif, comme en session. Une clé absente
    du registre (étiquette de source, « Note », repli BR-08) est affichée telle
    quelle. Le payload vient de la base : les lignes malformées sont ignorées, jamais
    d'exception (logique pure, testable hors-ligne).
    """
    labels: dict[str, str] = {}
    for spk in payload.get("speakers") or []:
        if isinstance(spk, dict) and spk.get("key"):
            key = str(spk["key"])
            name = " ".join(str(spk.get("name") or "").split())
            labels[key] = name or str(spk.get("auto") or key)
    records: list[tuple[float, Optional[str], str]] = []
    for row in payload.get("segments") or []:
        try:
            start, key, text = float(row[0]), row[1], str(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        records.append((start, None if key is None else str(key), text))
    records.sort(key=lambda record: record[0])
    return [
        format_segment_line(
            start, text, speaker=(labels.get(key, key) if key is not None else None)
        )
        for start, key, text in records
    ]


def _payload_notes(payload: dict) -> list[tuple[float, str]]:
    """Notes utilisateur d'une structure archivée, triées (malformées ignorées)."""
    notes: list[tuple[float, str]] = []
    for row in payload.get("notes") or []:
        try:
            notes.append((float(row[0]), str(row[1])))
        except (TypeError, ValueError, IndexError):
            continue
    notes.sort(key=lambda note: note[0])
    return notes


def render_payload_transcript(payload: dict) -> str:
    """Contenu complet du fichier transcript (hors résumé UC-17) depuis une structure
    de session archivée : en-tête d'origine, segments triés, récapitulatif des notes."""
    fmt = "md" if payload.get("format") == "md" else "txt"
    header = str(payload.get("header") or "") or _transcript_header(
        payload.get("device"), str(payload.get("mic") or ""), fmt
    )
    parts = [header]
    parts.extend(line + "\n" for line in render_payload_lines(payload))
    notes = _payload_notes(payload)
    if notes:
        parts.append(render_notes_recap(notes, fmt))
    return "".join(parts)


def _summary_tail(content: str, fmt: str) -> str:
    """Queue « Résumé » (UC-17) du fichier actuel, à préserver lors d'une réécriture
    post-session (le résumé est ajouté APRÈS l'archivage : le payload ne le contient
    pas). Les lignes de segments/notes commencent toujours par « [MM:SS] » : un titre
    « Résumé » seul sur sa ligne ne peut venir que de l'ajout d'UC-17."""
    heading = "\n## Résumé\n" if fmt == "md" else "\n# Résumé\n"
    idx = content.find(heading)
    return content[idx:] if idx >= 0 else ""


def rewrite_payload_transcript(payload: dict) -> tuple[bool, str]:
    """Réécrit le fichier transcript exporté depuis une structure de session archivée.

    Renvoie ``(ok, détail)``. Dégradation propre (FR-31) : fichier jamais exporté,
    déplacé ou supprimé → ``(False, raison)`` sans lever — l'entrée d'historique,
    elle, reste mise à jour par l'appelant. Écriture atomique (fichier temporaire +
    ``os.replace``, comme ``_rewrite_sorted``) ; la section « Résumé » (UC-17) déjà
    présente dans le fichier est préservée.
    """
    path_str = payload.get("path") if isinstance(payload, dict) else None
    if not path_str:
        return False, "aucun fichier exporté pour cette session"
    path = Path(str(path_str))
    if not path.is_file():
        return False, "fichier exporté introuvable (déplacé ou supprimé)"
    fmt = "md" if payload.get("format") == "md" else "txt"
    try:
        tail = _summary_tail(path.read_text(encoding="utf-8"), fmt)
    except (OSError, UnicodeDecodeError):
        tail = ""  # fichier illisible : on réécrit sans queue plutôt que d'échouer
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(render_payload_transcript(payload))
            if tail:
                fh.write(tail)
        os.replace(tmp, path)  # atomique : l'original n'est remplacé qu'en cas de succès
        return True, ""
    except OSError:
        logger.warning("Réécriture post-session du transcript échouée.", exc_info=True)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False, "écriture du fichier échouée (voir logs)"


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
        on_notice: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._config = config
        self.cfg = config.conference
        self.transcriber = transcriber
        self._on_finished = on_finished
        self._on_segment = on_segment
        # Notice utilisateur (texte, genre) — sert au repli de backend de diarisation
        # (CO-19) : un changement de qualité PERÇU ne doit pas rester dans les logs.
        # Appelée depuis le thread qui démarre la session, aucun verrou tenu.
        self._on_notice = on_notice
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._file = None
        # Chaque segment : (instant de début en s, locuteur ou None, texte). En mode
        # « distinction », le locuteur est renseigné et les segments sont triés à la fin.
        self._segments: list[tuple[float, Optional[str], str]] = []
        # Notes utilisateur en session (UC-16), (position en s, texte). Elles arrivent
        # d'AUTRES threads (pont GUI, raccourci signet) que le mixeur : _segments/_notes/
        # _file sont protégés par ce verrou FEUILLE (jamais imbriqué avec un autre verrou).
        self._note_lock = threading.Lock()
        self._notes: list[tuple[float, str]] = []
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
        # En-tête écrit à l'ouverture du transcript, conservé pour les réécritures
        # (tri à l'arrêt, renommage post-session) : sans lui, chaque réécriture
        # régénérerait un en-tête daté du moment de la réécriture.
        self._header = ""
        # Diarisation des locuteurs individuels (itération 3, UC-18). Construite par
        # session dans start() (numérotation stable sur la session) ; None = repli sur
        # la distinction par source. Le worker de diarisation (RE-14) draine _diar_queue.
        self._diar = None
        self._diar_queue: "queue.Queue" = queue.Queue()
        # Jeton de génération de session : incrémenté à chaque start(). Un worker de
        # diarisation orphelin (join expiré) porte le jeton de SA session ; ses
        # écritures tardives sont écartées par _store_and_write (pas de pollution
        # inter-sessions du transcript, de l'historique ni du flux affiché).
        self._session_gen = 0

    @property
    def diarization_active(self) -> bool:
        """True si la diarisation par locuteur tourne pour la session courante (US-11/12)."""
        return self._diar is not None

    def is_running(self) -> bool:
        # Capture locale : _finish (thread de réunion) peut nuller _thread entre les
        # deux lectures — sans elle, None.is_alive() lèverait dans l'appelant (pont
        # GUI ou callback pynput du signet, qui en mourrait pour la session).
        thread = self._thread
        return thread is not None and thread.is_alive()

    # -- cycle de vie ----------------------------------------------------------
    def start(self, system_spec: Optional[Union[int, str]] = None) -> bool:
        """Démarre la capture+transcription de réunion. False si déjà en cours."""
        if self.is_running():
            return False
        self._stop.clear()
        self._segments = []
        self._notes = []
        self._error = None
        self._active = set()
        self._buffers = {"mic": _StreamBuffer(), "system": _StreamBuffer()}
        self._system_name = None
        self._system_ready = threading.Event()
        # Relus par session (une modification de config n'exige pas de reconstruire l'objet).
        self._distinct = bool(getattr(self.cfg, "distinguish_speakers", False))
        self._fmt = "md" if str(self.cfg.export_format).lower() == "md" else "txt"
        self._header = ""
        self._diar = self._make_diarizer()
        self._diar_queue = queue.Queue()
        self._session_gen += 1
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

    def _make_diarizer(self):
        """Construit le diariseur de la session (UC-18), ou None (repli distinction source).

        Actif SEULEMENT en mode distinction (la diarisation exige des sources séparées, pas
        de mixage) ET si ``speaker_diarization.enabled``. Tout échec de construction
        (import, config) retombe silencieusement sur la distinction par source (BR-08)."""
        sd = getattr(self.cfg, "speaker_diarization", None)
        if not (self._distinct and sd is not None and getattr(sd, "enabled", False)):
            return None
        try:
            from .diarization import Diarizer

            # Chemin du modèle ONNX résolu ICI (relatif à config.yaml) : `diarization`
            # ne connaît pas `base_dir`. Vide = pas de modèle → repli MFCC signalé.
            raw = str(getattr(sd, "onnx_model", "") or "")
            model_path = self._config.resolve(raw) if raw else raw
            diarizer = Diarizer(sd, SAMPLE_RATE, model_path=model_path)
            logger.info(
                "Diarisation des locuteurs activée (backend %s, max %s/source).",
                diarizer.backend, getattr(sd, "max_speakers", "?"),
            )
            # Repli de backend (modèle absent/illisible) : la précision change, donc
            # notification utilisateur — hors verrou (appelé depuis start()).
            if diarizer.notice and self._on_notice is not None:
                try:
                    self._on_notice(diarizer.notice, "warn")
                except Exception:  # noqa: BLE001
                    logger.exception("on_notice a levé une exception")
            return diarizer
        except Exception:  # noqa: BLE001 — jamais bloquant : repli sur la distinction par source
            logger.exception("Diarisation indisponible ; repli sur la distinction par source.")
            return None

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
            if not self._distinct:
                mode = "mixage"
            elif self._diar is not None:
                mode = "diarisation par locuteur"
            else:
                mode = "distinction par source"
            logger.info(
                "Réunion démarrée (sources : %s ; mode : %s).",
                ", ".join(sorted(self._active)), mode,
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
        with self._note_lock:
            self._segments.append((elapsed, None, text))
        self._write_line(format_segment_line(elapsed, text), text)

    def _write_line(self, line: str, text: str) -> None:
        """Écrit une ligne dans le transcript (au fil de l'eau), journalise, notifie."""
        with self._note_lock:
            self._write_file_locked(line + "\n")
        # Confidentialité : pas de contenu transcrit dans les logs au niveau INFO
        # (le transcript et l'historique le conservent déjà) — texte réservé à DEBUG.
        logger.info("Réunion : segment transcrit (%d caractères).", len(text))
        logger.debug("Réunion %s", line)
        if self._on_segment is not None:
            try:
                self._on_segment(line, text)
            except Exception:  # noqa: BLE001
                logger.exception("on_segment a levé une exception")

    def _write_file_locked(self, data: str) -> None:
        """Écriture brute dans le transcript (``_note_lock`` TENU par l'appelant).

        Un échec n'interrompt jamais la session : le texte reste en mémoire
        (``_segments``/``_notes``) et est restitué à l'arrêt.
        """
        if self._file is None:
            return
        try:
            self._file.write(data)
            self._file.flush()
        except OSError:
            logger.warning("Écriture du transcript de réunion échouée.", exc_info=True)

    # -- notes utilisateur (UC-16) ----------------------------------------------
    def add_note(self, text: str, stamp: Optional[str] = None) -> Optional[str]:
        """Ajoute une note utilisateur ancrée à la position de session (UC-16).

        Appelée depuis le pont GUI ou le raccourci signet (threads ≠ mixeur) ; les
        structures partagées sont protégées par ``_note_lock``. ``stamp`` optionnel
        (« MM:SS ») = position du segment cité, sinon la position courante. La note
        entre dans ``_segments`` avec le locuteur « Note » : elle est entrelacée
        chronologiquement au tri final (BR-07). Renvoie la ligne affichable, ou
        ``None`` si la note est vide ou la session inactive.
        """
        text = " ".join(str(text or "").split())
        if not text or not self.is_running():
            return None
        elapsed = parse_stamp(stamp)
        if elapsed is None:
            elapsed = max(0.0, time.monotonic() - self._t0) if self._t0 else 0.0
        line = format_segment_line(elapsed, text, speaker="Note")
        with self._note_lock:
            self._notes.append((elapsed, text))
            self._segments.append((elapsed, "Note", text))
            self._write_file_locked(line + "\n")
        logger.info("Réunion : note ajoutée (%d caractères).", len(text))
        logger.debug("Réunion %s", line)
        return line

    # -- distinction par source (itération 2) / par locuteur (itération 3, UC-18) --
    def _consume_distinct(self) -> None:
        """Transcrit chaque source SÉPARÉMENT (pas de mixage), horodate par position
        audio, puis entrelace chronologiquement à l'arrêt (cf. _finish).

        En diarisation (UC-18), un worker dédié (_diar_loop, RE-14) étiquette chaque
        segment par locuteur ; il est démarré ici et joint APRÈS le dernier segment
        (sentinelle), avant _close_transcript/_finish (→ _segments complet)."""
        # File et diariseur passés en ARGUMENTS (pas de lookup d'attribut dans le
        # worker) : un worker orphelin d'une session précédente (join expiré) ne peut
        # ainsi jamais consommer la file — ni la sentinelle — de la session suivante
        # (même protection que sysbuf dans _system_loop et que la file de live.py).
        # Le jeton de génération protège l'autre sens : les ÉCRITURES tardives de
        # l'orphelin sont écartées par _store_and_write.
        diar_thread: Optional[threading.Thread] = None
        diar_queue = self._diar_queue
        if self._diar is not None:
            diar_thread = threading.Thread(
                target=self._diar_loop,
                args=(diar_queue, self._diar, self._session_gen),
                daemon=True, name="diarization",
            )
            diar_thread.start()
        try:
            segmenters = {
                s: _Segmenter(
                    SAMPLE_RATE, self.cfg.vad_threshold, self.cfg.silence_duration,
                    self.cfg.max_segment,
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
        finally:
            # Sentinelle après le dernier segment enfilé ; join avant le retour (comme la
            # file de transcription live) pour que _finish voie tous les segments diarisés.
            if diar_thread is not None:
                diar_queue.put(None)
                diar_thread.join(timeout=30.0)
                if diar_thread.is_alive():
                    logger.warning("Worker de diarisation toujours actif à l'arrêt (backlog).")

    def _diar_loop(self, diar_queue: "queue.Queue", diar, gen: int) -> None:
        """Worker de diarisation (RE-14) : draine ``diar_queue``, étiquette par locuteur, émet.

        Séparé du fil de capture ET du fil de transcription : l'empreinte vocale +
        clustering n'y bloquent ni l'un ni l'autre. ``None`` = sentinelle d'arrêt.
        File, diariseur et jeton de génération reçus en arguments (jamais relus sur
        ``self``) : un worker orphelin ne consomme pas la file de la session suivante
        et ses écritures tardives sont écartées (``gen`` périmé)."""
        while True:
            job = diar_queue.get()
            if job is None:
                break
            source, src_label, audio, stamped = job
            key = diar.identify(audio, source, src_label)
            for abs_start, text in stamped:
                self._store_and_write(abs_start, key, text, gen=gen)

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
        """Transcrit un segment d'une source et émet ses sous-segments horodatés (absolus).

        En diarisation (UC-18), l'étiquetage du locuteur est DÉFÉRÉ au worker dédié
        (_diar_loop, RE-14) : la transcription (lourde) reste ici, l'empreinte vocale est
        calculée hors du fil de transcription et une même clé de locuteur est appliquée à
        tous les sous-segments du segment audio. Sinon (distinction par source, itér. 2),
        émission immédiate avec l'étiquette de source (« Moi » / « Interlocuteurs »)."""
        # Position de début du segment dans la réunion (échantillons poussés - longueur).
        chunk_start = max(0, pushed_after - audio.shape[0]) / SAMPLE_RATE
        src_label = self.cfg.mic_label if source == "mic" else self.cfg.system_label
        try:
            subs = self.transcriber.transcribe_segments(audio)
        except Exception:  # noqa: BLE001
            logger.exception("Transcription d'un segment (%s) échouée", source)
            return
        stamped = [
            (chunk_start + max(0.0, float(start)), (text or "").strip())
            for start, _end, text in subs
        ]
        stamped = [(abs_start, text) for abs_start, text in stamped if text]
        if not stamped:
            return
        if self._diar is not None:
            self._diar_queue.put((source, src_label, audio, stamped))
        else:
            for abs_start, text in stamped:
                self._store_and_write(abs_start, src_label, text)

    def _store_and_write(
        self, abs_start: float, key: str, text: str, gen: Optional[int] = None,
    ) -> None:
        """Mémorise un segment (clé de locuteur = ``spk:N`` en diarisation, sinon étiquette
        de source) et l'écrit au fil de l'eau avec le libellé courant.

        ``gen`` (worker de diarisation) : jeton de génération de la session émettrice.
        S'il est périmé — worker orphelin d'une session précédente — le segment est
        écarté au lieu de polluer le transcript/historique de la session courante."""
        if gen is not None and gen != self._session_gen:
            logger.warning(
                "Segment de diarisation tardif écarté (session terminée) : %d caractères.",
                len(text),
            )
            return
        with self._note_lock:
            self._segments.append((abs_start, key, text))
        self._write_line(
            format_segment_line(abs_start, text, speaker=self._label_for(key)), text
        )

    def _label_for(self, key: Optional[str]) -> Optional[str]:
        """Résout une clé de segment en libellé affichable (applique le renommage).

        ``spk:N`` → libellé du locuteur (diarisation) ; toute autre clé (étiquette de
        source, « Note », ou ``None`` en mode mixé) est renvoyée telle quelle."""
        if self._diar is not None and isinstance(key, str) and key.startswith("spk:"):
            return self._diar.label(key)
        return key

    # -- diarisation : liste + renommage (UC-18, FR-31 / US-11 / US-12) ----------
    def speakers(self) -> list[dict]:
        """Locuteurs détectés pour l'interface ([] hors diarisation)."""
        return self._diar.speakers() if self._diar is not None else []

    def rename_speaker(self, key: str, name: Optional[str]) -> bool:
        """Renomme un locuteur détecté (FR-31) ; ``False`` hors diarisation / clé inconnue."""
        return self._diar.rename(key, name) if self._diar is not None else False

    def render_lines(self) -> list[str]:
        """Lignes du transcript rendues avec les libellés COURANTS (post-renommage).

        Rafraîchit le flux affiché après un renommage (US-12) : les libellés sont résolus
        à la demande depuis les clés stockées, dans l'ordre chronologique."""
        with self._note_lock:
            ordered = sorted(self._segments, key=lambda record: record[0])
        return [
            format_segment_line(start, text, speaker=self._label_for(key))
            for start, key, text in ordered
        ]

    # -- transcript ------------------------------------------------------------
    def _open_transcript(self, device_sys: Optional[str], mic_label: str) -> Optional[Path]:
        fmt = "md" if str(self.cfg.export_format).lower() == "md" else "txt"
        self._header = _transcript_header(device_sys, mic_label, fmt)
        try:
            folder = self._config.resolve(self.cfg.export_dir)
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = folder / f"reunion_{stamp}.{fmt}"
            self._file = path.open("w", encoding="utf-8")
            self._file.write(self._header)
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
        # Sous _note_lock : une note concurrente ne doit ni écrire dans un fichier
        # fermé, ni être omise du récapitulatif de fin (FR-26).
        with self._note_lock:
            if self._file is None:
                return
            if self._notes:
                self._write_file_locked(self._notes_recap(self._notes))
            try:
                self._file.close()
            except OSError:
                logger.warning("Fermeture du transcript de réunion échouée.", exc_info=True)
            finally:
                self._file = None

    def _notes_recap(self, notes: list) -> str:
        """Section récapitulative « Notes » de fin de transcript (FR-26)."""
        return render_notes_recap(notes, self._fmt)

    def _rewrite_sorted(self, path: Optional[Path], ordered: list, notes: list) -> None:
        """Réécrit le transcript trié chronologiquement (entrelacement des deux sources,
        notes utilisateur comprises), suivi du récapitulatif « Notes » (FR-26).

        Écriture atomique (fichier temporaire + ``os.replace``) : si elle échoue, la
        version au fil de l'eau (complète mais non triée) reste intacte au lieu d'être tronquée."""
        if path is None:
            return
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(
                    self._header
                    or _transcript_header(self._device_sys, self._mic_label_header, self._fmt)
                )
                for start, key, text in ordered:
                    fh.write(
                        format_segment_line(start, text, speaker=self._label_for(key)) + "\n"
                    )
                if notes:
                    fh.write(self._notes_recap(notes))
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

    def _session_payload(
        self, ordered: list, notes: list, path: Optional[Path],
    ) -> Optional[dict]:
        """Structure de session archivable (FR-31), ou ``None`` hors diarisation.

        Persistée dans l'historique (colonne ``payload``) pour permettre le renommage
        des locuteurs APRÈS la session : segments à CLÉS (``spk:N``, étiquette de
        source, « Note »), registre des libellés, chemin/format/en-tête du fichier
        exporté. JSON-compatible. Never-fail (un échec prive du renommage post-session,
        pas de l'archivage du texte).
        """
        if not self._distinct or self._diar is None:
            return None
        try:
            return {
                "type": "réunion",
                "version": 1,
                "segments": [[start, key, text] for start, key, text in ordered],
                "speakers": [
                    {"key": s["key"], "auto": s["auto"], "name": s["name"]}
                    for s in self._diar.speakers()
                ],
                "notes": [[start, text] for start, text in notes],
                "path": str(path) if path is not None else None,
                "format": self._fmt,
                "header": self._header,
            }
        except Exception:  # noqa: BLE001 — jamais bloquant à l'arrêt de session
            logger.exception("Construction de la structure de session échouée")
            return None

    def _finish(self, device_sys: Optional[str], path: Optional[Path]) -> None:
        # TOUTE la construction du résultat est protégée : une exception ici (ex.
        # RuntimeError si un thread de capture survivant mute _active pendant le
        # sorted, OSError de _rewrite_sorted) sauterait sinon le callback de fin et
        # laisserait l'application figée en état CONFERENCE jusqu'au redémarrage.
        try:
            # Instantané sous verrou : des notes peuvent encore arriver d'autres threads.
            with self._note_lock:
                segments = list(self._segments)
                notes = sorted(self._notes, key=lambda note: note[0])
            # _active est muté sans verrou par _system_loop (discard d'une source morte) :
            # copie défensive avant tri.
            sources = sorted(set(self._active))
            # Entrelacement chronologique : tri par instant de début (identité en mode mixé,
            # vrai entrelacement des deux sources — et des notes — en mode distinction).
            ordered = sorted(segments, key=lambda record: record[0])
            if self._distinct:
                # Clé de segment → libellé courant (locuteur diarisé renommable, source, ou Note).
                body = "\n".join(
                    format_segment_line(start, text, speaker=self._label_for(key))
                    for start, key, text in ordered
                )
                # Le fichier final reflète l'ordre chronologique (notes comprises).
                self._rewrite_sorted(path, ordered, notes)
            else:
                # Mode mixé : texte nu, mais les notes gardent leur marqueur (US-10).
                body = "\n".join(
                    (f"[Note] {text}" if key == "Note" else text)
                    for _start, key, text in ordered
                )
            result = {
                "text": body.strip(),
                "device": device_sys,
                "sources": sources,
                "segments": len(segments) - len(notes),
                "notes": len(notes),
                "path": str(path) if path is not None else None,
                "error": self._error,
                "payload": self._session_payload(ordered, notes, path),
            }
        except Exception:  # noqa: BLE001 — le callback de fin DOIT partir quoi qu'il arrive
            logger.exception("Construction du résultat de réunion échouée")
            result = {
                "text": "",
                "device": device_sys,
                "sources": [],
                "segments": 0,
                "notes": 0,
                "path": str(path) if path is not None else None,
                "error": self._error or "erreur interne à l'arrêt (voir logs)",
                "payload": None,
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
