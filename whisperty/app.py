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

from .config import Config
from .injector import TextInjector
from .recorder import AudioRecorder, MicrophoneError
from .transcriber import ModelNotAvailableError, Transcriber
from .tray import Tray, TrayState

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
        self.tray = Tray(
            on_toggle=self.toggle,
            on_quit=self.quit,
            on_open_config=self.open_config,
        )
        # État protégé par un verrou réentrant (transitions multi-threads).
        self._state = TrayState.IDLE
        self._lock = threading.RLock()
        self._quitting = False
        self._listener = None

    # -- gestion d'état (toujours sous verrou) ---------------------------------
    def _set_state(self, state: TrayState) -> None:
        """Met à jour l'état logique + l'icône tray. À appeler avec le verrou tenu."""
        self._state = state
        try:
            self.tray.set_state(state)
        except Exception:  # noqa: BLE001 — la MAJ tray ne doit pas faire planter l'app
            logger.exception("Mise à jour de l'icône tray échouée")

    def toggle(self) -> None:
        """Démarre/arrête la dictée. Ignoré pendant la transcription (PROCESSING)."""
        with self._lock:
            if self._state is TrayState.IDLE:
                self._start_recording()
            elif self._state is TrayState.RECORDING:
                self._stop_and_process()
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
            self._set_state(TrayState.RECORDING)
            logger.info("Dictée : enregistrement…")
            # Surveillance : arrêt auto sur silence (toggle) + garde-fou durée max.
            threading.Thread(target=self._monitor_recording, daemon=True).start()

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
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

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
        try:
            text = self.transcriber.transcribe(audio)
            if text:
                logger.info("Texte : %s", text)
                self.injector.inject(text)
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

    # -- actions menu ----------------------------------------------------------
    def open_config(self) -> None:
        path = self.config.resolve("config.yaml")
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows
        except Exception:  # noqa: BLE001
            logger.info("Configuration : %s", path)

    def quit(self) -> None:
        with self._lock:
            if self._quitting:
                return
            self._quitting = True
        logger.info("Arrêt de Whisperty.")
        try:
            self.recorder.stop()  # idempotent (no-op si pas d'enregistrement)
        except Exception:  # noqa: BLE001
            pass
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.tray.stop()
        except Exception:  # noqa: BLE001
            pass

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
        # La boucle tray bloque le thread principal jusqu'à « Quitter ».
        self.tray.run()
        self.quit()

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
