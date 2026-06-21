"""Whisperty — orchestration (Étape 4).

Machine à états IDLE → RECORDING → PROCESSING → IDLE pilotée par un raccourci
clavier global non bloquant. Toutes les transitions sont sérialisées par un
verrou réentrant : les déclencheurs (raccourci, push-to-talk, double-appui, menu
tray, surveillance VAD) proviennent de threads distincts.

La transcription tourne dans un thread dédié pour ne jamais geler l'écoute du
clavier ni l'icône tray.

Confidentialité : journalisation strictement locale (console + fichier), aucun
handler réseau.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

from .ai import LocalLLM
from .conference import ConferenceTranscriber
from .config import Config
from .history import History
from .injector import TextInjector
from .live import LiveTranscriber
from .loopback import list_speakers
from .meeting import MeetingAssistant
from .profiles import ProfileResolver
from .recorder import AudioRecorder, MicrophoneError
from .transcriber import ModelNotAvailableError, Transcriber
from .tray import Tray, TrayState
from .winutil import foreground_app

logger = logging.getLogger("whisperty")


def setup_logging(config: Config) -> None:
    """Configure une journalisation locale (console + fichier). Aucun envoi réseau."""
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    handlers: list[logging.Handler] = []
    # En build « windowed » (PyInstaller console=False), sys.stderr peut être None.
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        log_path = config.resolve(config.logging.path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except OSError as exc:
        logging.getLogger(__name__).warning("Fichier de log indisponible : %s", exc)
    logging.basicConfig(
        level=level,
        handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class WhispertyApp:
    """Application complète : raccourci → enregistrement → transcription → injection."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.recorder = AudioRecorder(
            device=config.audio.device,
            samplerate=config.audio.samplerate,
        )
        self.transcriber = Transcriber.from_config(config)
        self.injector = TextInjector(config.output)
        # V2 : historique local, raffinage LLM local, profils de contexte.
        self.history = History.from_config(config)
        self.llm = LocalLLM(config.ai)
        self.profiles = ProfileResolver(config)
        # V2 : flux de transcription « au fil de l'eau » des modes live/réunion, pour
        # affichage progressif dans la tuile « Dernière transcription » de l'UI fenêtre.
        # Alimenté par les callbacks on_segment (thread worker) ; lu par GuiApi.poll/
        # get_live_text (thread du pont). Le compteur _live_rev (monotone) évite de
        # renvoyer tout le transcript à chaque tick : le JS ne le récupère qu'au changement.
        self._live_lock = threading.Lock()
        self._live_lines: list[str] = []
        self._live_rev = 0
        # V2 : transcription live d'une sortie audio (loopback).
        self.live = LiveTranscriber(
            config,
            self.transcriber,
            on_finished=self._on_live_finished,
            on_segment=self._on_live_segment,
        )
        # V2 : mode réunion (micro + sortie système simultanés).
        self.conference = ConferenceTranscriber(
            config,
            self.transcriber,
            on_finished=self._on_conference_finished,
            on_segment=self._on_conference_segment,
        )
        live_devices = list_speakers()  # best-effort (liste vide si soundcard absent)
        self.tray = Tray(
            on_toggle=self.toggle,
            on_quit=self.quit,
            on_open_config=self.open_config,
            on_import_audio=self.import_audio,
            on_copy_last=self.copy_last if config.history.enabled else None,
            on_open_history=self.open_history if config.history.enabled else None,
            on_start_live=self.start_live,
            on_stop_live=self.stop_live,
            on_start_meeting=self.start_meeting,
            on_stop_meeting=self.stop_meeting,
            live_devices=live_devices,
            on_start_conference=self.start_conference if config.conference.enabled else None,
            on_stop_conference=self.stop_conference if config.conference.enabled else None,
            on_show=self.show_window,
        )
        # V2 : assistant de réunion (questions → réponses LLM locales).
        self.meeting = MeetingAssistant(
            config,
            self.transcriber,
            self.llm,
            self.injector,
            history=self.history,
            on_notify=self.tray.notify,
            on_finished=self._on_meeting_finished,
        )
        # État protégé par un verrou réentrant (transitions multi-threads).
        self._state = TrayState.IDLE
        self._lock = threading.RLock()
        self._quitting = False
        self._quit_event = threading.Event()
        self._listener = None
        # Interface fenêtre (GuiApi) si lancée ; None en mode tray seul.
        self._gui = None
        # Application active capturée au démarrage de la dictée (profils de contexte).
        self._active_app: str | None = None

    # -- gestion d'état (toujours sous verrou) ---------------------------------
    def _set_state(self, state: TrayState) -> None:
        """Met à jour l'état logique + l'icône tray. À appeler avec le verrou tenu."""
        self._state = state
        try:
            self.tray.set_state(state)
        except Exception:  # noqa: BLE001 — la MAJ tray ne doit pas faire planter l'app
            logger.exception("Mise à jour de l'icône tray échouée")

    def toggle(self) -> None:
        """Démarre/arrête la dictée. Ignoré pendant PROCESSING ou la transcription live."""
        with self._lock:
            if self._state is TrayState.IDLE:
                self._start_recording()
            elif self._state is TrayState.RECORDING:
                self._stop_and_process()
            elif self._state is TrayState.LIVE:
                logger.info("Dictée ignorée : transcription live en cours.")
            elif self._state is TrayState.CONFERENCE:
                logger.info("Dictée ignorée : réunion en cours.")
            elif self._state is TrayState.MEETING:
                logger.info("Dictée ignorée : assistant de réunion en cours.")
            else:  # PROCESSING
                logger.info("Dictée ignorée : transcription/chargement en cours.")

    def _start_recording(self) -> None:
        # Verrou tenu pendant recorder.start() À DESSEIN (asymétrie volontaire avec
        # _stop_and_process) : démarrer le flux atomiquement sous verrou évite qu'un
        # stop concurrent, survenant pendant l'ouverture du périphérique, ne laisse un
        # flux orphelin. Coût : une latence brève (ouverture micro) sur les transitions.
        with self._lock:
            if self._quitting or self._state is not TrayState.IDLE:
                return
            try:
                self.recorder.start()
            except MicrophoneError as exc:
                logger.error("%s", exc)
                return
            # Capture l'application au premier plan (= cible de l'injection) pour
            # choisir le profil de contexte. Lecture locale rapide ; None si désactivé.
            self._active_app = foreground_app() if self.config.profiles.enabled else None
            self._set_state(TrayState.RECORDING)
            logger.info("Dictée : enregistrement…")
            # Surveillance : arrêt auto sur silence (toggle) + garde-fou durée max.
            try:
                threading.Thread(target=self._monitor_recording, daemon=True).start()
            except RuntimeError:
                # Threads OS épuisés : sans surveillance, on perd l'arrêt auto et le
                # garde-fou de durée. On annule proprement plutôt que de laisser un
                # flux micro orphelin en RECORDING (stop() prend _op_lock : ordre _lock→_op_lock respecté).
                logger.exception("Démarrage de la surveillance impossible ; enregistrement annulé.")
                try:
                    self.recorder.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._set_state(TrayState.IDLE)

    def _stop_and_process(self) -> None:
        # Transition d'état sous verrou ; passer à PROCESSING rend tout autre
        # _stop_and_process()/toggle() concurrent inopérant (no-op via le test d'état).
        with self._lock:
            if self._state is not TrayState.RECORDING:
                return
            self._set_state(TrayState.PROCESSING)
        # Verrou RELÂCHÉ avant l'arrêt PortAudio (bloquant) : ne pas geler le thread
        # écouteur clavier ni les autres transitions. recorder.stop() est thread-safe.
        try:
            audio = self.recorder.stop()
        except Exception:  # noqa: BLE001 — ne jamais propager dans un thread écouteur
            logger.exception("Arrêt du micro échoué")
            with self._lock:
                if self._state is TrayState.PROCESSING:
                    self._set_state(TrayState.IDLE)
            return
        # Transcription hors du thread d'écoute clavier.
        self._spawn_worker(self._process, audio)

    def _spawn_worker(self, target, *args) -> bool:
        """Démarre un thread worker daemon ; restaure IDLE si le démarrage échoue.

        ``Thread.start()`` peut lever ``RuntimeError`` (threads OS épuisés). Sans
        filet, l'état resterait figé en PROCESSING (plus aucun worker pour le finally
        qui repasse IDLE), rendant l'app inopérante. Ici on rétablit l'état.
        """
        try:
            threading.Thread(target=target, args=args, daemon=True).start()
            return True
        except RuntimeError:
            logger.exception("Démarrage d'un thread worker impossible")
            with self._lock:
                if self._state is TrayState.PROCESSING:
                    self._set_state(TrayState.IDLE)
            return False

    def _monitor_recording(self) -> None:
        """Coupe l'enregistrement sur silence prolongé (toggle) ou à la durée max."""
        cfg = self.config.audio
        push_to_talk = self.config.hotkey.mode == "push_to_talk"
        t0 = time.monotonic()
        speech_seen = False
        silence_started = None
        while True:
            time.sleep(0.05)
            with self._lock:
                if self._state is not TrayState.RECORDING:
                    return  # déjà arrêté (touche relâchée, second appui, quit…)
            now = time.monotonic()
            if now - t0 >= cfg.max_duration:
                logger.info("Durée max d'enregistrement atteinte (%.0f s).", cfg.max_duration)
                self._stop_and_process()
                return
            if push_to_talk:
                continue  # l'arrêt vient du relâchement de touche
            level = self.recorder.current_level
            if level >= cfg.vad_threshold:
                speech_seen = True
                silence_started = None
            elif speech_seen:
                if silence_started is None:
                    silence_started = now
                elif now - silence_started >= cfg.silence_duration:
                    logger.info("Silence détecté : arrêt automatique.")
                    self._stop_and_process()
                    return

    def _process(self, audio) -> None:
        app_name = self._active_app
        try:
            profile = self.profiles.for_app(app_name)
            text = self.transcriber.transcribe(audio, profile)
            text = self.llm.refine(text)  # raffinage LLM local (no-op si désactivé)
            if text:
                logger.info("Texte : %s", text)
                self.injector.inject(text)
                self.history.add(
                    text, source="dictée", app=app_name,
                    model=self.config.transcription.model,
                )
            else:
                logger.info("Transcription vide (aucune parole détectée).")
        except ModelNotAvailableError as exc:
            logger.error("%s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("Échec du traitement de la dictée")
        finally:
            with self._lock:
                # Ne remettre IDLE que si l'on est toujours en PROCESSING
                # (un nouvel enregistrement a pu démarrer entre-temps).
                if self._state is TrayState.PROCESSING:
                    self._set_state(TrayState.IDLE)

    # -- import de fichier audio (V2) ------------------------------------------
    def import_audio(self) -> None:
        """Transcrit un fichier audio choisi par l'utilisateur (menu tray).

        Réutilise la machine à états (IDLE → PROCESSING → IDLE). Le résultat est
        copié dans le presse-papiers et archivé (plutôt qu'injecté : l'app cible
        est ambiguë depuis le menu tray).
        """
        path = self._ask_audio_file()
        if not path:
            return
        with self._lock:
            if self._state is not TrayState.IDLE:
                logger.info("Import ignoré : une dictée/transcription est en cours.")
                return
            self._set_state(TrayState.PROCESSING)
        self._spawn_worker(self._process_file, path)

    def _process_file(self, path: str) -> None:
        name = os.path.basename(path)
        try:
            text = self.transcriber.transcribe_file(path)
            text = self.llm.refine(text)
            if text:
                logger.info("Fichier « %s » transcrit : %d caractères.", name, len(text))
                copied = self.injector.copy_to_clipboard(text)
                self.history.add(
                    text, source="fichier", app=name,
                    model=self.config.transcription.model,
                )
                if copied:
                    self.tray.notify(f"« {name} » transcrit et copié dans le presse-papiers.")
                else:
                    self.tray.notify(f"« {name} » transcrit (copie presse-papiers indisponible).")
            else:
                self.tray.notify(f"« {name} » : aucune parole détectée.")
        except FileNotFoundError as exc:
            logger.error("%s", exc)
        except ModelNotAvailableError as exc:
            logger.error("%s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("Échec de l'import audio")
        finally:
            with self._lock:
                if self._state is TrayState.PROCESSING:
                    self._set_state(TrayState.IDLE)

    def _ask_audio_file(self) -> str | None:
        """Ouvre un sélecteur de fichier (tkinter, standard). None si annulé/indispo."""
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            logger.error("tkinter indisponible : import audio impossible.")
            return None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                title="Importer un fichier audio",
                filetypes=[
                    ("Fichiers audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.wma *.aac"),
                    ("Tous les fichiers", "*.*"),
                ],
            )
            root.destroy()
            return path or None
        except Exception:  # noqa: BLE001
            logger.exception("Sélecteur de fichier indisponible")
            return None

    # -- flux live « au fil de l'eau » (live / réunion) ------------------------
    def _reset_live_transcript(self) -> None:
        """Vide le flux affiché et invalide le cache JS (le compteur change → re-fetch)."""
        with self._live_lock:
            self._live_lines = []
            self._live_rev += 1

    def _append_live_line(self, display: str) -> None:
        """Ajoute une ligne au flux affiché (appelé depuis le thread worker)."""
        display = (display or "").strip()
        if not display:
            return
        with self._live_lock:
            self._live_lines.append(display)
            self._live_rev += 1

    def _on_live_segment(self, _stamp: str, text: str) -> None:
        # En live, on affiche le texte seul (lecture fluide ; l'horodatage va au fichier).
        self._append_live_line(text)

    def _on_conference_segment(self, line: str, _text: str) -> None:
        # En réunion, on affiche la ligne déjà formatée ([MM:SS] éventuel locuteur : …).
        self._append_live_line(line)

    def live_rev(self) -> int:
        """Compteur monotone du flux live (lu par GuiApi.poll, payload minimal)."""
        with self._live_lock:
            return self._live_rev

    def live_transcript(self) -> dict:
        """Flux live courant : {rev, text}. Récupéré par le JS quand rev a changé."""
        with self._live_lock:
            return {"rev": self._live_rev, "text": "\n".join(self._live_lines)}

    # -- transcription live d'une sortie audio (V2) ----------------------------
    def start_live(self, device_spec: object = None) -> None:
        """Démarre la transcription live d'une sortie audio (menu tray).

        ``device_spec`` : None = sortie configurée/par défaut ; index = haut-parleur
        choisi dans le menu. Mode exclusif : refusé si une autre opération est en cours.
        """
        if device_spec is None:
            device_spec = self.config.live.device
        with self._lock:
            if self._quitting:
                return
            if self._state is not TrayState.IDLE:
                logger.info("Transcription live ignorée : une autre opération est en cours.")
                self.tray.notify("Impossible : une dictée ou transcription est déjà en cours.")
                return
            self._set_state(TrayState.LIVE)
        # Vide le flux affiché avant de démarrer (la tuille repart de zéro).
        self._reset_live_transcript()
        # Démarrage hors verrou ; en cas d'échec immédiat, on rétablit l'état.
        if not self.live.start(device_spec):
            logger.warning("La transcription live n'a pas pu démarrer.")
            with self._lock:
                if self._state is TrayState.LIVE:
                    self._set_state(TrayState.IDLE)

    def stop_live(self) -> None:
        """Arrête la transcription live (menu tray)."""
        with self._lock:
            if self._state is not TrayState.LIVE:
                return
        # On ne tient PAS le verrou et on ne joint PAS ici : le thread live appellera
        # _on_live_finished (qui reprend le verrou) pour repasser IDLE → pas d'interblocage.
        self.live.stop()

    def _on_live_finished(self, result: dict) -> None:
        """Callback de fin de transcription live (appelé depuis le thread live)."""
        error = result.get("error")
        text = result.get("text") or ""
        count = result.get("segments", 0)
        device = result.get("device")
        # Historiser/copier AVANT de repasser IDLE : la tuile (pilotée par l'état) bascule
        # vers le texte d'historique dès qu'IDLE est vu — l'entrée doit déjà exister, sinon
        # course (last_text() renverrait la transcription précédente).
        if not error and text:
            self.history.add(
                text, source="live", app=device, model=self.config.transcription.model
            )
            self.injector.copy_to_clipboard(text)
        with self._lock:
            if self._state is TrayState.LIVE:
                self._set_state(TrayState.IDLE)
        if error:
            self.tray.notify(f"Transcription live arrêtée : {error}")
        elif text:
            self.tray.notify(
                f"Transcription live arrêtée — {count} segment(s) copiés dans le presse-papiers."
            )
        else:
            self.tray.notify("Transcription live arrêtée — aucun texte transcrit.")

    # -- mode réunion (V2) -----------------------------------------------------
    def start_conference(self, device_spec: object = None) -> None:
        """Démarre la transcription de réunion (micro + sortie système), menu tray.

        ``device_spec`` : None = sortie configurée/par défaut ; index = sortie choisie.
        Mode exclusif, comme la transcription live.
        """
        if device_spec is None:
            device_spec = self.config.conference.system_device
        with self._lock:
            if self._quitting:
                return
            if self._state is not TrayState.IDLE:
                logger.info("Réunion ignorée : une autre opération est en cours.")
                self.tray.notify("Impossible : une dictée ou transcription est déjà en cours.")
                return
            self._set_state(TrayState.CONFERENCE)
        # Vide le flux affiché avant de démarrer (la tuille repart de zéro).
        self._reset_live_transcript()
        # Rappel consentement (tout reste local).
        self.tray.notify(
            "Réunion : pensez au consentement des participants. Tout reste local."
        )
        if not self.conference.start(device_spec):
            logger.warning("Le mode réunion n'a pas pu démarrer.")
            with self._lock:
                if self._state is TrayState.CONFERENCE:
                    self._set_state(TrayState.IDLE)

    def stop_conference(self) -> None:
        """Arrête la transcription de réunion (menu tray)."""
        with self._lock:
            if self._state is not TrayState.CONFERENCE:
                return
        # Idem live : pas de verrou ni de join() ici ; _on_conference_finished repassera IDLE.
        self.conference.stop()

    def _on_conference_finished(self, result: dict) -> None:
        """Callback de fin de réunion (appelé depuis le thread de réunion)."""
        error = result.get("error")
        text = result.get("text") or ""
        count = result.get("segments", 0)
        device = result.get("device")
        path = result.get("path")
        sources = ", ".join(result.get("sources", [])) or "aucune"
        # Historiser AVANT de repasser IDLE (cf. _on_live_finished : évite la course
        # tuile/last_text()). Le texte final est la version triée (entrelacement).
        if not error and text:
            self.history.add(
                text, source="réunion", app=device, model=self.config.transcription.model
            )
        with self._lock:
            if self._state is TrayState.CONFERENCE:
                self._set_state(TrayState.IDLE)
        if error:
            self.tray.notify(f"Réunion arrêtée : {error}")
        elif path:
            self.tray.notify(
                f"Réunion terminée — {count} segment(s) (sources : {sources}). Transcript : {path}"
            )
        else:
            self.tray.notify(f"Réunion terminée — {count} segment(s) (sources : {sources}).")

    # -- assistant de réunion (V2) ---------------------------------------------
    def start_meeting(self, device_spec: object = None) -> None:
        """Démarre l'assistant de réunion (menu tray).

        Écoute la sortie audio de la confcall, détecte les questions posées à
        l'utilisateur (``meeting.user_name``) et génère des réponses via le LLM local.
        """
        if not self.config.ai.enabled:
            self.tray.notify(
                "Assistant de réunion : activez ai.enabled et un LLM local (Ollama…)."
            )
            logger.warning("Assistant de réunion refusé : ai.enabled est false.")
            return
        if not self.config.meeting.user_name.strip():
            self.tray.notify(
                "Assistant de réunion : renseignez meeting.user_name dans config.yaml."
            )
            logger.warning("Assistant de réunion refusé : meeting.user_name vide.")
            return
        if device_spec is None:
            device_spec = self.config.live.device
        with self._lock:
            if self._quitting:
                return
            if self._state is not TrayState.IDLE:
                logger.info("Assistant de réunion ignoré : une autre opération est en cours.")
                self.tray.notify("Impossible : une dictée ou transcription est déjà en cours.")
                return
            self._set_state(TrayState.MEETING)
        if not self.meeting.start(device_spec):
            logger.warning("L'assistant de réunion n'a pas pu démarrer.")
            with self._lock:
                if self._state is TrayState.MEETING:
                    self._set_state(TrayState.IDLE)
            return
        self.tray.notify(
            f"Assistant de réunion actif — écoute des questions pour "
            f"{self.config.meeting.user_name.strip()}."
        )

    def stop_meeting(self) -> None:
        """Arrête l'assistant de réunion (menu tray)."""
        with self._lock:
            if self._state is not TrayState.MEETING:
                return
        self.meeting.stop()

    def _on_meeting_finished(self, result: dict) -> None:
        """Callback de fin d'assistant de réunion (thread worker)."""
        with self._lock:
            if self._state is TrayState.MEETING:
                self._set_state(TrayState.IDLE)
        error = result.get("error")
        if error:
            self.tray.notify(f"Assistant de réunion arrêté : {error}")
            return
        text = result.get("text") or ""
        reply_count = result.get("reply_count", 0)
        device = result.get("device")
        if text:
            self.history.add(
                text, source="réunion-transcript", app=device,
                model=self.config.transcription.model,
            )
            self.injector.copy_to_clipboard(text)
        if reply_count:
            self.tray.notify(
                f"Réunion terminée — {reply_count} réponse(s) générée(s), "
                f"transcript copié dans le presse-papiers."
            )
        elif text:
            self.tray.notify("Réunion terminée — transcript copié dans le presse-papiers.")
        else:
            self.tray.notify("Réunion terminée — aucun texte transcrit.")

    # -- historique (V2) -------------------------------------------------------
    def copy_last(self) -> None:
        """Copie la dernière transcription dans le presse-papiers (menu tray)."""
        text = self.history.last_text()
        if not text:
            self.tray.notify("Historique vide.")
            return
        if self.injector.copy_to_clipboard(text):
            self.tray.notify("Dernière transcription copiée.")

    def open_history(self) -> None:
        """Ouvre le dossier contenant la base d'historique."""
        folder = self.config.resolve(self.config.history.path).parent
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows
        except Exception:  # noqa: BLE001
            logger.info("Dossier de l'historique : %s", folder)

    # -- actions menu ----------------------------------------------------------
    def open_config(self) -> None:
        path = self.config.resolve("config.yaml")
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows
        except Exception:  # noqa: BLE001
            logger.info("Configuration : %s", path)

    # -- interface fenêtre (V2) ------------------------------------------------
    def show_window(self) -> None:
        """Ré-affiche la fenêtre (menu tray « Ouvrir » / double-clic). No-op si absente.

        Appelée depuis le thread tray : on lit ``_gui`` sous ``_lock`` (publication/
        destruction concurrente), puis on appelle la fenêtre hors verrou (l'appel est
        marshalé vers le thread UI et ``destroy()`` concurrent est rattrapé par try/except).
        """
        with self._lock:
            gui = self._gui
            window = getattr(gui, "_window", None) if gui is not None else None
        if window is None:
            return
        try:
            window.show()
            window.restore()
        except Exception:  # noqa: BLE001
            logger.exception("Affichage de la fenêtre échoué")

    def apply_config_from_gui(self, payload: dict) -> dict:
        """Applique et persiste les réglages de l'écran Configuration.

        Met à jour la config en mémoire (les sous-systèmes partagent ces dataclasses),
        réécrit config.yaml en **préservant les commentaires** (``configio``), puis
        applique les effets à chaud (rechargement modèle / raccourci / injecteur / LLM).
        Tout échec est capturé : l'enregistrement ne doit jamais faire planter l'app.
        """
        from .configio import update_yaml_file

        c = self.config
        updates: dict[str, object] = {}
        reload_model = hotkey_changed = output_changed = ai_changed = device_changed = False

        try:
            # -- transcription (model/device/local_files_only => rechargement) --
            if "model" in payload and str(payload["model"]) != c.transcription.model:
                c.transcription.model = str(payload["model"])
                updates["transcription.model"] = c.transcription.model
                reload_model = True
            if "device" in payload:
                dev = "cuda" if str(payload["device"]).lower() == "cuda" else "cpu"
                if dev != c.transcription.device:
                    c.transcription.device = dev
                    updates["transcription.device"] = dev
                    reload_model = True
            if "localOnly" in payload:
                lo = bool(payload["localOnly"])
                if lo != c.transcription.local_files_only:
                    c.transcription.local_files_only = lo
                    updates["transcription.local_files_only"] = lo
                    reload_model = True
            # langue : lue à chaque transcription => pas de rechargement.
            if "langue" in payload:
                raw = payload["langue"]
                lang = None if raw in (None, "", "auto") else str(raw)
                if lang != c.transcription.language:
                    c.transcription.language = lang
                    updates["transcription.language"] = lang
            # -- audio (bornes raisonnables : l'UI est cliente, on ne fait pas confiance) --
            if "vad" in payload:
                vad = min(1.0, max(0.0, float(payload["vad"]) / 1000.0))
                if vad != c.audio.vad_threshold:
                    c.audio.vad_threshold = vad
                    updates["audio.vad_threshold"] = vad
            if "silence" in payload:
                sil = min(60.0, max(0.0, float(payload["silence"]) / 1000.0))
                if sil != c.audio.silence_duration:
                    c.audio.silence_duration = sil
                    updates["audio.silence_duration"] = sil
            if "mic" in payload:
                mic = payload["mic"]
                mic = None if mic in (None, "") else mic
                if mic != c.audio.device:
                    c.audio.device = mic
                    updates["audio.device"] = mic
                    device_changed = True
            # -- raccourci --
            if payload.get("combo"):
                combo = self._safe_combo(str(payload["combo"]))
                if combo != c.hotkey.combo:
                    c.hotkey.combo = combo
                    updates["hotkey.combo"] = combo
                    hotkey_changed = True
            # -- injection --
            if "injection" in payload:
                method = "type" if payload["injection"] == "frappe" else "paste"
                if method != c.output.method:
                    c.output.method = method
                    updates["output.method"] = method
                    output_changed = True
            if "delai" in payload:
                delay = min(5.0, max(0.0, float(payload["delai"]) / 1000.0))
                if delay != c.output.type_delay:
                    c.output.type_delay = delay
                    updates["output.type_delay"] = delay
                    output_changed = True
            # -- IA locale --
            if "ia" in payload:
                ia = bool(payload["ia"])
                if ia != c.ai.enabled:
                    c.ai.enabled = ia
                    updates["ai.enabled"] = ia
                    ai_changed = True
            if "iaEndpoint" in payload and str(payload["iaEndpoint"]) != c.ai.endpoint:
                c.ai.endpoint = str(payload["iaEndpoint"])
                updates["ai.endpoint"] = c.ai.endpoint
                ai_changed = True
            if "iaModel" in payload and str(payload["iaModel"]) != c.ai.model:
                c.ai.model = str(payload["iaModel"])
                updates["ai.model"] = c.ai.model
                ai_changed = True
        except (TypeError, ValueError):
            logger.exception("Réglages invalides reçus de l'interface")
            return {"ok": False, "error": "Réglages invalides."}

        # Persistance fichier (préserve commentaires/ordre). Échec non bloquant.
        if updates:
            try:
                update_yaml_file(self.config.resolve("config.yaml"), updates)
            except OSError:
                logger.exception("Écriture de config.yaml échouée")
                return {"ok": False, "error": "Écriture de config.yaml impossible."}

        # Effets à chaud (chacun isolé : un échec ne bloque pas les autres).
        if device_changed:
            try:
                self.recorder.device = c.audio.device
            except Exception:  # noqa: BLE001
                logger.exception("MAJ du périphérique micro échouée")
        if output_changed:
            try:
                self.injector = TextInjector(c.output)
            except Exception:  # noqa: BLE001
                logger.exception("Reconstruction de l'injecteur échouée")
        if ai_changed:
            try:
                self.llm = LocalLLM(c.ai)
            except Exception:  # noqa: BLE001
                logger.exception("Reconstruction du client LLM échouée")
        if reload_model:
            self._reload_model()
        if hotkey_changed:
            self.reload_hotkey()
        logger.info("Configuration enregistrée depuis l'interface (%d champ(s)).", len(updates))
        return {"ok": True}

    def _safe_combo(self, combo: str) -> str:
        """Valide un combo (format pynput) ; repli sur le défaut documenté si invalide."""
        try:
            from pynput import keyboard

            return self._validated_combo(keyboard, combo)
        except Exception:  # noqa: BLE001
            return combo

    def _reload_model(self) -> None:
        """Force le rechargement du modèle (taille/device/hors-ligne modifiés)."""
        try:
            self.transcriber._model = None  # rechargement paresseux au prochain usage
        except Exception:  # noqa: BLE001
            logger.exception("Réinitialisation du modèle échouée")
            return
        # Réchauffe immédiatement si l'app est au repos (sinon : au prochain usage).
        with self._lock:
            idle = self._state is TrayState.IDLE
        if idle:
            self._spawn_worker(self._preload)

    def reload_hotkey(self) -> None:
        """Reconstruit l'écouteur clavier global après changement de combinaison.

        Démarre le nouvel écouteur AVANT d'arrêter l'ancien (pas de fenêtre sans
        raccourci). L'échange ``_listener`` est fait sous ``_lock`` (course avec
        ``quit()``) ; ``start()``/``stop()`` restent HORS verrou (potentiellement
        bloquants). Si un arrêt est en cours, on n'installe pas l'écouteur (sinon
        il resterait orphelin, ``quit()`` ayant déjà arrêté l'ancien).
        """
        try:
            new = self._build_listener()
            new.start()
        except Exception:  # noqa: BLE001
            logger.exception("Reconstruction du raccourci échouée ; ancien conservé.")
            return
        with self._lock:
            if self._quitting:
                old, install = None, False
            else:
                old, install = self._listener, True
                self._listener = new
        if not install:
            try:
                new.stop()
            except Exception:  # noqa: BLE001
                pass
            return
        if old is not None and old is not new:
            try:
                old.stop()
            except Exception:  # noqa: BLE001
                pass
        logger.info(
            "Raccourci rechargé : %s (%s).", self.config.hotkey.combo, self.config.hotkey.mode
        )

    def quit(self) -> None:
        with self._lock:
            if self._quitting:
                return
            self._quitting = True
        logger.info("Arrêt de Whisperty.")
        # Détruit la fenêtre pour débloquer webview.start() sur le thread principal
        # (on_closing autorise la fermeture car _quitting est vrai). Lecture de _gui
        # sous verrou (course possible avec show_window depuis le thread tray).
        with self._lock:
            gui = self._gui
            window = getattr(gui, "_window", None) if gui is not None else None
        if window is not None:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.recorder.stop()  # idempotent (no-op si pas d'enregistrement)
        except Exception:  # noqa: BLE001
            pass
        # Capture _listener sous verrou (course avec reload_hotkey depuis le thread du pont).
        with self._lock:
            listener = self._listener
            self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            # Arrête la transcription live et attend la fin du thread (flush du transcript).
            self.live.stop()
            self.live.wait(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            # Idem pour le mode réunion. Budget > pire cas du worker (résolution loopback
            # ~6 s + join du thread système 2 s) pour que l'historique soit écrit AVANT close().
            self.conference.stop()
            self.conference.wait(timeout=10.0)
            self.meeting.stop()
            self.meeting.wait(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.history.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.tray.stop()
        except Exception:  # noqa: BLE001
            pass
        # Débloque un éventuel thread principal en attente (repli post-échec GUI).
        self._quit_event.set()

    # -- raccourci clavier global ----------------------------------------------
    def _build_listener(self):
        from pynput import keyboard

        hk = self.config.hotkey
        if hk.double_tap_key:
            return self._double_tap_listener(keyboard, hk.double_tap_key)
        combo = self._validated_combo(keyboard, hk.combo)
        if hk.mode == "push_to_talk":
            return self._push_to_talk_listener(keyboard, combo)
        return keyboard.GlobalHotKeys({combo: self.toggle})

    @staticmethod
    def _validated_combo(keyboard, combo: str) -> str:
        """Valide le combo (format pynput) ; repli sur le défaut si invalide."""
        default = "<ctrl>+<alt>+<space>"
        try:
            keyboard.HotKey.parse(combo)
            return combo
        except ValueError:
            logger.error(
                "Raccourci '%s' invalide (format pynput) ; repli sur %s.", combo, default
            )
            return default

    def _push_to_talk_listener(self, keyboard, combo: str):
        """Enregistre tant que la combinaison est maintenue (push-to-talk)."""
        target = set(keyboard.HotKey.parse(combo))
        pressed: set = set()
        listener_box: dict = {}

        def canonical(key):
            listener = listener_box.get("listener")
            return listener.canonical(key) if listener else key

        def on_press(key):
            pressed.add(canonical(key))
            if target <= pressed:
                self._start_recording()

        def on_release(key):
            pressed.discard(canonical(key))
            if not (target <= pressed):
                self._stop_and_process()

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener_box["listener"] = listener
        return listener

    def _double_tap_listener(self, keyboard, key_name: str):
        """Active la dictée par double-appui rapide sur une touche (ex: ctrl)."""
        variants = self._key_variants(keyboard, key_name)
        state = {"last": 0.0}

        def on_press(key):
            if key in variants:
                now = time.monotonic()
                if now - state["last"] < 0.4:
                    state["last"] = 0.0
                    self.toggle()
                else:
                    state["last"] = now

        return keyboard.Listener(on_press=on_press)

    @staticmethod
    def _key_variants(keyboard, key_name: str) -> set:
        """Renvoie les variantes d'une touche (ex: ctrl → ctrl, ctrl_l, ctrl_r)."""
        name = key_name.lower().lstrip("<").rstrip(">")
        groups = {
            "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
            "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
            "shift": ("shift", "shift_l", "shift_r"),
            "cmd": ("cmd", "cmd_l", "cmd_r"),
        }
        variants: set = set()
        for member in groups.get(name, (name,)):
            key = getattr(keyboard.Key, member, None)
            if key is not None:
                variants.add(key)
        if not variants:  # touche caractère simple
            variants.add(keyboard.KeyCode.from_char(name))
        return variants

    # -- boucle principale -----------------------------------------------------
    def run(self) -> None:
        logger.info(
            "Whisperty démarre. Raccourci : %s (%s).",
            self.config.hotkey.combo, self.config.hotkey.mode,
        )
        # Préchargement du modèle en arrière-plan : la première dictée est ainsi rapide.
        threading.Thread(target=self._preload, daemon=True).start()
        self._listener = self._build_listener()
        self._listener.start()
        if not self._run_with_gui():
            # Repli : boucle tray seule, bloquante (comportement historique).
            self.tray.run()
        self.quit()

    def _run_with_gui(self) -> bool:
        """Ouvre la fenêtre (tray en compagnon détaché). False = repli tray seul.

        ``webview.start()`` tient le thread principal ; le tray tourne détaché. Si
        ``pywebview``/WebView2 est indisponible, on retombe proprement sur le tray seul.
        """
        if not self.config.gui.enabled:
            return False
        try:
            import webview  # noqa: F401  (dépendance optionnelle)

            from .gui import launch_gui
        except Exception:  # noqa: BLE001
            logger.info("pywebview indisponible : interface fenêtre désactivée (tray seul).")
            return False
        try:
            self.tray.run_detached()  # tray dans un thread dédié
        except Exception:  # noqa: BLE001
            logger.exception("Tray détaché impossible ; repli tray bloquant.")
            return False
        try:
            launch_gui(self)  # bloque jusqu'à destruction de la fenêtre
        except Exception:  # noqa: BLE001
            # Le tray est déjà détaché : on ne peut pas relancer tray.run() ici. On
            # bloque le thread principal jusqu'à « Quitter » pour rester opérationnel.
            logger.exception("Lancement de l'interface fenêtre échoué ; tray seul conservé.")
            self._quit_event.wait()
        return True

    def _preload(self) -> None:
        with self._lock:
            if self._state is TrayState.IDLE:
                self._set_state(TrayState.PROCESSING)
        try:
            self.transcriber.load()
        except ModelNotAvailableError as exc:
            logger.error("Modèle non préchargé : %s", exc)
        finally:
            with self._lock:
                if self._state is TrayState.PROCESSING:
                    self._set_state(TrayState.IDLE)
