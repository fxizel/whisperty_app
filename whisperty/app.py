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
import logging.handlers
import os
import re
import sys
import threading
import time
from pathlib import Path

from . import modeldl
from .ai import LocalLLM
from .conference import ConferenceTranscriber
from .config import Config
from .history import History
from .injector import TextInjector
from .live import LiveTranscriber
from .loopback import list_speakers
from .profiles import ProfileResolver
from .recorder import AudioRecorder, MicrophoneError
from .transcriber import ModelNotAvailableError, Transcriber
from .tray import Tray, TrayState
from .winutil import foreground_app

logger = logging.getLogger("whisperty")

# Nombre max de lignes conservées dans le flux live AFFICHÉ (tuile « Transcription en
# direct »). Borne la RAM et le payload get_live_text sur une très longue session ;
# le transcript fichier et l'historique, eux, conservent l'intégralité du texte.
_LIVE_DISPLAY_MAX_LINES = 400

# Horodatage accepté pour une note-citation (UC-16) : « MM:SS » (position de session,
# réunion) ou « HH:MM:SS » (heure murale, live). Fourni par l'UI (cliente : validé ici).
_NOTE_STAMP_RE = re.compile(r"\d{1,4}:[0-5]\d(:[0-5]\d)?")

# Horodatage en tête d'une ligne de réunion (« [MM:SS] … ») — pour associer chaque
# ligne du flux affiché à sa position (action « Noter » de l'UI).
_LINE_STAMP_RE = re.compile(r"^\[(\d{1,4}:[0-5]\d)\]")


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
        # Rotation locale (stdlib) : l'app tourne en permanence, un FileHandler simple
        # croîtrait sans borne. 1 Mo × 3 sauvegardes ≈ plusieurs semaines de diagnostic.
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        )
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
        self.llm = LocalLLM(config.ai, config.summary)
        self.profiles = ProfileResolver(config)
        # V2 : flux de transcription « au fil de l'eau » des modes live/réunion, pour
        # affichage progressif dans la tuile « Dernière transcription » de l'UI fenêtre.
        # Alimenté par les callbacks on_segment (thread worker) ; lu par GuiApi.poll/
        # get_live_text (thread du pont). Le compteur _live_rev (monotone) évite de
        # renvoyer tout le transcript à chaque tick : le JS ne le récupère qu'au changement.
        self._live_lock = threading.Lock()
        self._live_lines: list[str] = []
        # Horodatage de chaque ligne affichée (parallèle à _live_lines) : permet à
        # l'action « Noter » de l'UI d'ancrer une note-citation à SON segment (FR-25).
        self._live_stamps: list[str] = []
        self._live_rev = 0
        # V2 : retours utilisateur VISIBLES (« toast » de la fenêtre + notification
        # système) — une erreur qui ne va qu'aux logs (micro absent, modèle manquant)
        # laisse l'app muette en apparence. Publié sous _notice_lock (verrou feuille,
        # jamais imbriqué) et relevé par GuiApi.poll via noticeRev (modèle polling,
        # payload minimal, comme le flux live). _model_error mémorise le dernier échec
        # de chargement du modèle : il pilote la bannière « Télécharger » du dashboard.
        self._notice_lock = threading.Lock()
        self._notice_rev = 0
        self._notice_text = ""
        self._notice_kind = "info"
        self._model_error: str | None = None
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
            on_open_dictionary=self.open_dictionary,
            on_import_audio=self.import_audio,
            on_copy_last=self.copy_last if config.history.enabled else None,
            on_open_history=self.open_history if config.history.enabled else None,
            on_start_live=self.start_live,
            on_stop_live=self.stop_live,
            live_devices=live_devices,
            on_start_conference=self.start_conference if config.conference.enabled else None,
            on_stop_conference=self.stop_conference if config.conference.enabled else None,
            on_show=self.show_window,
        )
        # État protégé par un verrou réentrant (transitions multi-threads).
        self._state = TrayState.IDLE
        self._lock = threading.RLock()
        self._quitting = False
        self._quit_event = threading.Event()
        self._listener = None
        # Écouteur du raccourci « signet de note » (UC-16) ; None si désactivé/invalide.
        self._note_listener = None
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
                self._notify_user(str(exc))
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
            self._set_model_error(None)  # le chargement a réussi : bannière levée
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
                # Bénin mais déroutant (« j'ai parlé, rien ne s'insère ») : signalé
                # dans la fenêtre seulement, sans notification Windows.
                self._notify_user("Aucune parole détectée.", "warn", tray=False)
        except ModelNotAvailableError as exc:
            logger.error("%s", exc)
            self._set_model_error(exc)
            self._notify_user(self._model_unavailable_message())
        except Exception:  # noqa: BLE001
            logger.exception("Échec du traitement de la dictée")
            self._notify_user("La transcription a échoué — détails dans logs/whisperty.log.")
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
            self._set_model_error(None)
            text = self.llm.refine(text)
            if text:
                logger.info("Fichier « %s » transcrit : %d caractères.", name, len(text))
                copied = self.injector.copy_to_clipboard(text)
                self.history.add(
                    text, source="fichier", app=name,
                    model=self.config.transcription.model,
                )
                if copied:
                    self._notify_user(
                        f"« {name} » transcrit et copié dans le presse-papiers.", "info"
                    )
                else:
                    self._notify_user(
                        f"« {name} » transcrit (copie presse-papiers indisponible).", "warn"
                    )
            else:
                self._notify_user(f"« {name} » : aucune parole détectée.", "warn")
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            self._notify_user(str(exc))
        except ModelNotAvailableError as exc:
            logger.error("%s", exc)
            self._set_model_error(exc)
            self._notify_user(self._model_unavailable_message())
        except Exception:  # noqa: BLE001
            logger.exception("Échec de l'import audio")
            self._notify_user(
                f"L'import de « {name} » a échoué — détails dans logs/whisperty.log."
            )
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
            self._live_stamps = []
            self._live_rev += 1

    def _append_live_line(self, display: str, stamp: str = "") -> None:
        """Ajoute une ligne au flux affiché (appelé depuis le thread worker)."""
        display = (display or "").strip()
        if not display:
            return
        with self._live_lock:
            self._live_lines.append(display)
            self._live_stamps.append(stamp or "")
            if len(self._live_lines) > _LIVE_DISPLAY_MAX_LINES:
                del self._live_lines[: -_LIVE_DISPLAY_MAX_LINES]
                del self._live_stamps[: -_LIVE_DISPLAY_MAX_LINES]
            self._live_rev += 1

    def _on_live_segment(self, stamp: str, text: str) -> None:
        # En live, on affiche le texte seul (lecture fluide ; l'horodatage va au fichier)
        # mais on retient le stamp par ligne pour l'action « Noter » (FR-25).
        self._append_live_line(text, stamp)

    def _on_conference_segment(self, line: str, _text: str) -> None:
        # En réunion, on affiche la ligne déjà formatée ([MM:SS] éventuel locuteur : …) ;
        # la position [MM:SS] en tête sert de stamp pour l'action « Noter ».
        match = _LINE_STAMP_RE.match(line or "")
        self._append_live_line(line, match.group(1) if match else "")

    def live_rev(self) -> int:
        """Compteur monotone du flux live (lu par GuiApi.poll, payload minimal)."""
        with self._live_lock:
            return self._live_rev

    def live_transcript(self) -> dict:
        """Flux live courant : {rev, text, stamps}. Récupéré par le JS quand rev a changé.

        ``stamps`` (horodatage par ligne, parallèle aux lignes de ``text``) permet à
        l'action « Noter » d'ancrer une note-citation au segment cliqué (FR-25).
        """
        with self._live_lock:
            return {
                "rev": self._live_rev,
                "text": "\n".join(self._live_lines),
                "stamps": list(self._live_stamps),
            }

    # -- retours utilisateur visibles (V2) ---------------------------------------
    def _notify_user(self, message: str, kind: str = "error", tray: bool = True) -> None:
        """Signale un évènement À L'UTILISATEUR : toast fenêtre + notification système.

        Complément de la journalisation (qui reste la source détaillée) : tout ce qui
        change le comportement perçu (échec de dictée, fin de session, modèle absent)
        doit être visible sans ouvrir les logs. ``kind`` : ``error`` | ``warn`` |
        ``info`` (teinte du toast). ``tray=False`` réserve le message à la fenêtre
        (cas bénins, pour ne pas inonder les notifications Windows). Best-effort,
        jamais bloquant.
        """
        if tray:
            self.tray.notify(message)
        with self._notice_lock:
            self._notice_rev += 1
            self._notice_text = message
            self._notice_kind = kind

    def notice_rev(self) -> int:
        """Compteur monotone des notices (lu par GuiApi.poll, payload minimal)."""
        with self._notice_lock:
            return self._notice_rev

    def notice(self) -> dict:
        """Dernière notice ({rev, text, kind}) — récupérée par le JS quand rev change."""
        with self._notice_lock:
            return {"rev": self._notice_rev, "text": self._notice_text, "kind": self._notice_kind}

    # -- état / téléchargement du modèle (V2) ------------------------------------
    def _set_model_error(self, exc: object = None) -> None:
        """Mémorise (ou efface, avec None) le dernier échec de chargement du modèle."""
        with self._notice_lock:
            self._model_error = None if exc is None else str(exc)

    def model_ok(self) -> bool:
        """False si le dernier chargement du modèle a échoué (bannière du dashboard)."""
        with self._notice_lock:
            return self._model_error is None

    def _model_unavailable_message(self) -> str:
        """Message actionnable quand le modèle manque (toast + notification)."""
        size = modeldl.model_size_name(self.config.transcription.model)
        if modeldl.is_downloadable(size):
            return (
                f"Le modèle Whisper « {size} » n'est pas installé. "
                "Ouvrez Whisperty pour le télécharger en un clic."
            )
        return f"Modèle Whisper « {size} » indisponible — détails dans logs/whisperty.log."

    def model_status(self) -> dict:
        """État du modèle pour la bannière du dashboard (échec + téléchargement)."""
        size = modeldl.model_size_name(self.config.transcription.model)
        with self._notice_lock:
            error = self._model_error
        return {
            "ok": error is None,
            "error": error or "",
            "size": size,
            "canDownload": modeldl.is_downloadable(size),
            "sizeLabel": modeldl.approx_size_label(size),
            "download": modeldl.status(),
        }

    def start_model_download(self) -> dict:
        """Télécharge le modèle manquant (opt-in explicite, bannière du dashboard).

        Avec l'installation GPU, c'est la seule exception réseau du projet — jamais
        silencieuse. Le modèle est matérialisé dans ``models/`` à côté de la config,
        qui est ensuite pointée dessus avec ``local_files_only: true`` (zéro réseau
        à l'usage ensuite). Non bloquant : progression suivie par ``model_status``.
        """
        size = modeldl.model_size_name(self.config.transcription.model)
        return modeldl.start_download(
            size, self.config.resolve("models"), self._on_model_downloaded
        )

    def _on_model_downloaded(self, size: str, target: object) -> None:
        """Bascule la config sur le modèle téléchargé et précharge (thread du téléchargement)."""
        from .configio import update_yaml_file

        rel = f"models/faster-whisper-{size}"
        c = self.config
        c.transcription.model = rel
        c.transcription.local_files_only = True
        try:
            update_yaml_file(
                self.config.resolve("config.yaml"),
                {"transcription.model": rel, "transcription.local_files_only": True},
            )
        except OSError:
            # Non bloquant : le modèle est actif pour CETTE session ; la persistance
            # pourra être refaite depuis l'écran Configuration.
            logger.exception("Écriture de config.yaml échouée après le téléchargement du modèle")
        self._set_model_error(None)
        self._reload_model()  # rechargement paresseux + préchauffe si l'app est au repos
        self._notify_user(f"Modèle « {size} » installé — la dictée est prête.", "info")

    # -- notes en session (UC-16) ------------------------------------------------
    def add_note(self, text: object = None, stamp: object = None) -> dict:
        """Crée une note utilisateur pendant une session live/réunion (UC-16).

        Appelée depuis le pont GUI (champ de saisie / action « Noter ») ou le
        raccourci signet — jamais depuis les threads de capture (RE-11). ``stamp``
        optionnel = horodatage du segment cité (FR-25) ; sinon la note est horodatée
        au moment de la validation (BR-07). Hors session : refus sans effet de bord.
        """
        note = " ".join(str(text or "").split())
        if not note:
            return {"ok": False, "error": "Note vide."}
        stamp_s = str(stamp).strip() if stamp else ""
        if not _NOTE_STAMP_RE.fullmatch(stamp_s):  # payload client : ne pas s'y fier
            stamp_s = None
        with self._lock:
            state = self._state
        # Hors _lock : add_note des transcribers est thread-safe (verrou feuille interne).
        if state is TrayState.LIVE:
            line = self.live.add_note(note, stamp_s)
        elif state is TrayState.CONFERENCE:
            line = self.conference.add_note(note, stamp_s)
        else:
            line = None
        if line is None:
            logger.info("Note ignorée : aucune session live/réunion en cours.")
            return {"ok": False, "error": "Aucune session live ou réunion en cours."}
        self._append_live_line(line, stamp_s or "")
        return {"ok": True}

    # -- résumé de fin de session (UC-17) ---------------------------------------
    def _maybe_summarize(self, text: str, path: object, mode: str) -> None:
        """Lance le résumé de fin de session dans un thread worker si activé (UC-17).

        L'appel LLM (transcript entier) peut durer des dizaines de secondes : il
        s'exécute APRÈS le retour à IDLE, en arrière-plan — il ne bloque ni la
        machine à états, ni une nouvelle dictée, ni l'arrêt de l'application
        (thread daemon, best-effort). Échec = pas de résumé ; la session est déjà
        archivée, rien n'est perdu (même doctrine que RE-06).
        """
        if not self.config.summary.enabled or not (text or "").strip():
            return
        try:
            threading.Thread(
                target=self._summarize_session, args=(text, path, mode), daemon=True
            ).start()
        except RuntimeError:
            logger.exception("Démarrage du thread de résumé impossible")

    def _summarize_session(self, text: str, path: object, mode: str) -> None:
        """Worker : résume la session (LLM local), complète le transcript, archive."""
        logger.info("Résumé de %s en cours (LLM local)…", mode)
        summary = self.llm.summarize(text)
        if not summary:
            # summarize() a déjà journalisé la cause (serveur muet, endpoint refusé…).
            self._notify_user(f"Résumé de {mode} indisponible (LLM local muet ou refusé).", "warn")
            return
        if path:
            try:
                p = Path(str(path))
                heading = "\n## Résumé\n\n" if p.suffix.lower() == ".md" else "\n# Résumé\n"
                with p.open("a", encoding="utf-8") as fh:
                    fh.write(heading + summary.strip() + "\n")
            except OSError:
                logger.warning("Ajout du résumé au transcript échoué.", exc_info=True)
        try:
            self.history.add(
                summary, source=f"résumé {mode}", app=None,
                model=self.config.ai.model,
            )
        except Exception:  # noqa: BLE001 — l'app peut être en cours d'arrêt (base fermée)
            logger.exception("Archivage du résumé échoué")
        self._notify_user(f"Résumé de {mode} prêt (transcript + historique).", "info")

    def add_note_bookmark(self) -> None:
        """Signet : note horodatée sans texte saisi (raccourci global, UC-16).

        Hors session live/réunion : no-op journalisé (BR-01) — aucun effet de bord
        sur la dictée. Le retour visible passe par la tuile du flux ; la notification
        tray couvre le cas « fenêtre masquée » (best-effort).
        """
        result = self.add_note("Moment marqué")
        if result.get("ok"):
            self._notify_user("Signet ajouté à la transcription.", "info")

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
                self._notify_user(
                    "Impossible : une dictée ou transcription est déjà en cours.", "warn"
                )
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
            self._notify_user(f"Transcription live arrêtée : {error}")
        elif text:
            notes = result.get("notes", 0)
            extra = f" et {notes} note(s)" if notes else ""
            self._notify_user(
                f"Transcription live arrêtée — {count} segment(s){extra} "
                "copiés dans le presse-papiers.",
                "info",
            )
        else:
            self._notify_user("Transcription live arrêtée — aucun texte transcrit.", "warn")
        # Résumé de fin de session (UC-17) : APRÈS l'archivage et le retour IDLE.
        if not error and text:
            self._maybe_summarize(text, result.get("path"), "live")

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
                self._notify_user(
                    "Impossible : une dictée ou transcription est déjà en cours.", "warn"
                )
                return
            self._set_state(TrayState.CONFERENCE)
        # Vide le flux affiché avant de démarrer (la tuille repart de zéro).
        self._reset_live_transcript()
        # Rappel consentement (tout reste local).
        self._notify_user(
            "Réunion : pensez au consentement des participants. Tout reste local.", "info"
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
        notes = result.get("notes", 0)
        extra = f", {notes} note(s)" if notes else ""
        if error:
            self._notify_user(f"Réunion arrêtée : {error}")
        elif path:
            self._notify_user(
                f"Réunion terminée — {count} segment(s){extra} (sources : {sources}). "
                f"Transcript : {path}",
                "info",
            )
        else:
            self._notify_user(
                f"Réunion terminée — {count} segment(s){extra} (sources : {sources}).", "info"
            )
        # Résumé de fin de session (UC-17) : APRÈS l'archivage et le retour IDLE.
        if not error and text:
            self._maybe_summarize(text, path, "réunion")

    # -- historique (V2) -------------------------------------------------------
    def copy_last(self) -> None:
        """Copie la dernière transcription dans le presse-papiers (menu tray)."""
        text = self.history.last_text()
        if not text:
            self._notify_user("Historique vide.", "warn")
            return
        if self.injector.copy_to_clipboard(text):
            self._notify_user("Dernière transcription copiée.", "info")

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

    def open_dictionary(self) -> None:
        """Ouvre ``dictionary.txt`` dans l'éditeur système (repli / mode tray seul, UC-19)."""
        from .dictionary import ensure_dictionary_file

        path = self.config.resolve(self.config.dictionary.path)
        try:
            ensure_dictionary_file(path)  # os.startfile échouerait sur un fichier absent
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows
        except Exception:  # noqa: BLE001
            logger.info("Dictionnaire : %s", path)

    # -- dictionnaire (édition assistée, UC-19) --------------------------------
    def get_dictionary(self) -> dict:
        """Entrées du dictionnaire pour l'interface (liste ordonnée par le fichier)."""
        from .dictionary import parse_entries

        entries = parse_entries(self.config.resolve(self.config.dictionary.path))
        hotwords = [e["term"] for e in entries if e["kind"] == "hotword"]
        corrections = [
            {"wrong": e["term"], "right": e["replacement"]}
            for e in entries
            if e["kind"] == "correction"
        ]
        return {
            "enabled": bool(self.config.dictionary.enabled),
            "hotwords": hotwords,
            "corrections": corrections,
        }

    def apply_dictionary_from_gui(self, payload: dict) -> dict:
        """Enregistre le dictionnaire édité dans la fenêtre, puis le recharge à chaud.

        ``payload`` : ``{"hotwords": [str…], "corrections": [{"wrong","right"}…]}``.
        Écrit ``dictionary.txt`` en préservant commentaires/ordre (``update_dictionary_file``)
        puis rafraîchit le transcripteur ET les profils **sans relance** (le dictionnaire
        est lu par transcription — aucun rechargement de modèle). Tout échec est capturé :
        l'enregistrement ne doit jamais faire planter l'application.
        """
        from .dictionary import load_dictionary, update_dictionary_file

        payload = payload or {}
        # Construction de la liste ordonnée d'entrées (hotwords puis corrections). La
        # normalisation fine (entrées vides, doublons) est faite par update_dictionary_file.
        entries: list[dict] = []
        for term in payload.get("hotwords") or []:
            entries.append({"kind": "hotword", "term": str(term), "replacement": ""})
        for corr in payload.get("corrections") or []:
            if isinstance(corr, dict):
                entries.append({
                    "kind": "correction",
                    "term": str(corr.get("wrong") or ""),
                    "replacement": str(corr.get("right") or ""),
                })

        path = self.config.resolve(self.config.dictionary.path)
        try:
            update_dictionary_file(path, entries)
        except OSError:
            logger.exception("Écriture du dictionnaire échouée")
            self._notify_user("Enregistrement du dictionnaire impossible (voir logs).")
            return {"ok": False, "error": "Écriture du dictionnaire impossible."}

        # Rechargement à chaud (uniquement si le dictionnaire est actif : sinon les
        # sous-systèmes n'appliquent aucun dictionnaire — le fichier est tout de même écrit).
        hotwords: list[str] = []
        replacements: dict[str, str] = {}
        if self.config.dictionary.enabled:
            try:
                hotwords, replacements = load_dictionary(path)
                self.transcriber.set_dictionary(hotwords, replacements)
                self.profiles.reload_dictionary()
            except Exception:  # noqa: BLE001 — le fichier est écrit ; l'app reste opérationnelle
                logger.exception("Rechargement à chaud du dictionnaire échoué")

        n_hot = len(hotwords) if self.config.dictionary.enabled else \
            sum(1 for e in entries if e["kind"] == "hotword")
        n_corr = len(replacements) if self.config.dictionary.enabled else \
            sum(1 for e in entries if e["kind"] == "correction" and e["replacement"].strip())
        if self.config.dictionary.enabled:
            self._notify_user(
                f"Dictionnaire enregistré : {n_hot} terme(s), {n_corr} correction(s).",
                "info", tray=False,
            )
        else:
            self._notify_user(
                "Dictionnaire enregistré (actuellement désactivé — activez "
                "dictionary.enabled pour l'appliquer).",
                "warn", tray=False,
            )
        logger.info("Dictionnaire enregistré depuis l'interface (%d entrée(s)).", len(entries))
        return {"ok": True, "hotwords": n_hot, "corrections": n_corr}

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

    def on_second_instance(self) -> None:
        """Réagit à un second lancement de l'exécutable (garde d'instance unique).

        Comportement attendu d'une app de zone de notification : « je relance
        Whisperty, sa fenêtre apparaît » — pas un doublon qui se disputerait le
        raccourci global et le micro. Appelé depuis le thread veilleur de
        ``singleinstance.watch`` ; ``show_window`` est déjà thread-safe.
        """
        logger.info("Second lancement détecté : réaffichage de la fenêtre.")
        with self._lock:
            has_gui = self._gui is not None
        if has_gui:
            self.show_window()
        else:
            self._notify_user(
                "Whisperty est déjà lancé — icône dans la zone de notification.", "info"
            )

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
            # L'UI manipule des TAILLES (« medium ») alors que la config peut contenir
            # un chemin bundlé (« models/faster-whisper-medium ») : comparaison sur la
            # taille normalisée, sinon enregistrer sans rien toucher écraserait un
            # modèle local fonctionnel par un nom de taille absent du cache.
            if "model" in payload:
                new_size = modeldl.model_size_name(payload["model"])
                if new_size != modeldl.model_size_name(c.transcription.model):
                    # Un modèle déjà téléchargé/bundlé dans models/ est privilégié
                    # (hors-ligne) ; sinon le nom de taille (cache Hugging Face).
                    local_rel = f"models/faster-whisper-{new_size}"
                    has_local = (self.config.resolve(local_rel) / "model.bin").is_file()
                    c.transcription.model = local_rel if has_local else new_size
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
            # -- résumé de fin de session (UC-17) : lu à l'usage, pas de rebuild --
            if "resume" in payload:
                res = bool(payload["resume"])
                if res != c.summary.enabled:
                    c.summary.enabled = res
                    updates["summary.enabled"] = res
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
                self.llm = LocalLLM(c.ai, c.summary)
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
            self.transcriber.reset()  # rechargement paresseux au prochain usage
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
            note_listener = self._note_listener
            self._note_listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
        if note_listener is not None:
            try:
                note_listener.stop()
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

    def _build_note_listener(self):
        """Écouteur global du signet de note (UC-16). None si désactivé ou invalide.

        Le raccourci doit différer de celui de la dictée (mêmes règles que le combo
        principal : format pynput, éviter Win+Space). Tout problème désactive le
        signet SANS bloquer le reste (RSK-11) : la saisie de notes dans la fenêtre
        reste disponible.
        """
        from pynput import keyboard

        combo = (self.config.notes.bookmark_hotkey or "").strip()
        if not combo:
            return None
        try:
            keyboard.HotKey.parse(combo)
        except ValueError:
            logger.error(
                "Raccourci signet '%s' invalide (format pynput) ; signet désactivé.", combo
            )
            return None
        if combo == self.config.hotkey.combo:
            logger.error(
                "Raccourci signet identique au raccourci de dictée ('%s') ; signet désactivé.",
                combo,
            )
            return None
        return keyboard.GlobalHotKeys({combo: self.add_note_bookmark})

    def _start_note_listener(self) -> None:
        """Démarre l'écouteur du signet de note (best-effort, jamais bloquant)."""
        try:
            listener = self._build_note_listener()
            if listener is None:
                return
            listener.start()
        except Exception:  # noqa: BLE001
            logger.exception("Raccourci signet indisponible ; notes via la fenêtre seulement.")
            return
        # Installation sous verrou (course avec quit()) ; cf. reload_hotkey.
        with self._lock:
            install = not self._quitting
            if install:
                self._note_listener = listener
        if not install:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
            return
        logger.info("Signet de note : %s.", self.config.notes.bookmark_hotkey)

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
        self._start_note_listener()  # signet de note (UC-16), best-effort
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
            self._set_model_error(None)
        except ModelNotAvailableError as exc:
            logger.error("Modèle non préchargé : %s", exc)
            self._set_model_error(exc)
            self._notify_user(self._model_unavailable_message())
        finally:
            with self._lock:
                if self._state is TrayState.PROCESSING:
                    self._set_state(TrayState.IDLE)
