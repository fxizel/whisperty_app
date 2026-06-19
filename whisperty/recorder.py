"""Whisperty — capture audio (Étape 1).

Enregistrement micro non bloquant via ``sounddevice``, au format attendu par
Whisper : 16 kHz, mono, float32 normalisé dans [-1, 1].

Points clés :
- Fil d'exécution dédié (callback PortAudio) : ``start()`` / ``stop()`` n'attendent pas.
- Niveau RMS exposé en continu (``current_level``) pour le VAD simple et le tray.
- Rééchantillonnage automatique vers 16 kHz si le micro ne le propose pas.
- Sauvegarde WAV 16 bits PCM sans dépendance externe (module ``wave`` standard).

Confidentialité : aucun accès réseau. Les échantillons restent en mémoire puis,
au besoin, dans un fichier WAV temporaire local.

Démo : ``python -m whisperty.recorder``
"""
from __future__ import annotations

import logging
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np

try:
    import sounddevice as sd
except OSError as exc:  # PortAudio absent / non chargeable
    raise RuntimeError(
        "Impossible de charger PortAudio (sounddevice). "
        "Vérifiez l'installation : pip install sounddevice"
    ) from exc

logger = logging.getLogger(__name__)

SAMPLE_RATE: int = 16_000   # fréquence attendue par Whisper
CHANNELS: int = 1           # mono
DTYPE: str = "float32"      # échantillons normalisés [-1.0, 1.0]


class MicrophoneError(RuntimeError):
    """Micro absent, occupé ou paramètres non supportés."""


def list_input_devices() -> list[dict]:
    """Retourne la liste des périphériques d'entrée disponibles.

    Utile pour renseigner ``audio.device`` dans config.yaml.
    """
    devices: list[dict] = []
    for index, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            devices.append(
                {
                    "index": index,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "default_samplerate": int(dev["default_samplerate"]),
                }
            )
    return devices


def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Rééchantillonne un signal mono float32 de ``src_rate`` vers ``dst_rate``.

    Utilise ``soxr`` (haute qualité) si disponible, sinon une interpolation
    linéaire NumPy (qualité suffisante pour de la parole).
    """
    if src_rate == dst_rate or data.size == 0:
        return data
    try:
        import soxr  # import paresseux : dépendance optionnelle

        return soxr.resample(data, src_rate, dst_rate).astype(np.float32)
    except ImportError:
        logger.warning(
            "soxr indisponible : rééchantillonnage par interpolation linéaire."
        )
        duration = data.shape[0] / src_rate
        n_dst = int(round(duration * dst_rate))
        x_src = np.linspace(0.0, duration, num=data.shape[0], endpoint=False)
        x_dst = np.linspace(0.0, duration, num=n_dst, endpoint=False)
        return np.interp(x_dst, x_src, data).astype(np.float32)


@dataclass
class AudioRecorder:
    """Enregistreur micro non bloquant.

    Exemple (push-to-talk) ::

        rec = AudioRecorder()
        rec.start()          # à l'appui de la touche
        ...                  # l'utilisateur parle
        audio = rec.stop()   # au relâchement -> np.ndarray float32 mono 16 kHz

    Paramètres
    ----------
    samplerate : fréquence cible (16 kHz pour Whisper).
    channels   : nombre de canaux capturés (1 = mono).
    device     : index ou nom du micro ; None = périphérique d'entrée par défaut.
    blocksize  : taille de bloc en frames (1600 ≈ 100 ms à 16 kHz).
    level_callback : appelé à chaque bloc avec le niveau RMS courant (0.0–1.0).
    """

    samplerate: int = SAMPLE_RATE
    channels: int = CHANNELS
    device: Optional[Union[int, str]] = None
    blocksize: int = 1600
    level_callback: Optional[Callable[[float], None]] = None

    _stream: Optional["sd.InputStream"] = field(default=None, init=False, repr=False)
    _frames: list = field(default_factory=list, init=False, repr=False)
    _capture_rate: int = field(default=SAMPLE_RATE, init=False, repr=False)
    _level: float = field(default=0.0, init=False, repr=False)
    _recording: bool = field(default=False, init=False, repr=False)
    # Sérialise start()/stop() : ces méthodes peuvent être appelées depuis
    # plusieurs threads (écouteur clavier, surveillance, quit). Jamais pris dans
    # le callback PortAudio (éviterait une inversion de verrou).
    _op_lock: "threading.Lock" = field(default_factory=threading.Lock, init=False, repr=False)

    # -- propriétés ------------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_level(self) -> float:
        """Dernier niveau RMS mesuré (0.0–1.0)."""
        return self._level

    def is_silent(self, threshold: float = 0.01) -> bool:
        """Vrai si le dernier bloc est sous le seuil (VAD simple)."""
        return self._level < threshold

    # -- callback PortAudio ----------------------------------------------------
    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.debug("Statut flux audio : %s", status)
        # Copie indispensable : le tampon `indata` est réutilisé par PortAudio.
        block = indata.copy()
        self._frames.append(block)
        # Niveau RMS du bloc (tous canaux confondus) pour le VAD / l'animation tray.
        self._level = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
        if self.level_callback is not None:
            try:
                self.level_callback(self._level)
            except Exception:  # un callback fautif ne doit pas tuer le flux audio
                logger.exception("level_callback a levé une exception")

    # -- ouverture du flux -----------------------------------------------------
    def _open_stream(self) -> "sd.InputStream":
        """Ouvre le flux à 16 kHz ; bascule sur la fréquence native au besoin."""
        try:
            sd.check_input_settings(
                device=self.device,
                channels=self.channels,
                samplerate=self.samplerate,
                dtype=DTYPE,
            )
            self._capture_rate = self.samplerate
        except (sd.PortAudioError, ValueError):
            # Le micro ne propose pas 16 kHz : on capte en natif, le
            # rééchantillonnage aura lieu dans stop().
            info = sd.query_devices(self.device, "input")
            self._capture_rate = int(info["default_samplerate"])
            logger.info(
                "16 kHz non supporté ; capture à %d Hz puis rééchantillonnage.",
                self._capture_rate,
            )
        return sd.InputStream(
            samplerate=self._capture_rate,
            channels=self.channels,
            device=self.device,
            dtype=DTYPE,
            blocksize=self.blocksize,
            callback=self._callback,
        )

    # -- API publique ----------------------------------------------------------
    def start(self) -> None:
        """Démarre l'enregistrement (non bloquant). Thread-safe et idempotent."""
        with self._op_lock:
            if self._recording:
                logger.warning("start() ignoré : enregistrement déjà en cours.")
                return
            self._frames = []
            self._level = 0.0
            try:
                self._stream = self._open_stream()
                self._stream.start()
            except sd.PortAudioError as exc:
                # Fermer le flux éventuellement ouvert avant d'échouer (pas de fuite).
                if self._stream is not None:
                    try:
                        self._stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._stream = None
                raise MicrophoneError(
                    "Micro indisponible ou paramètres non supportés. Branchez un "
                    "micro et vérifiez les autorisations Windows "
                    "(Paramètres > Confidentialité et sécurité > Microphone)."
                ) from exc
            self._recording = True
            logger.info("Enregistrement démarré (%d Hz).", self._capture_rate)

    def stop(self) -> np.ndarray:
        """Arrête l'enregistrement et renvoie le signal mono float32 à 16 kHz.

        Thread-safe et idempotent : un appel concurrent ou ultérieur renvoie un
        tableau vide sans toucher au flux déjà fermé (pas de double-close PortAudio).
        """
        with self._op_lock:
            if not self._recording:
                return np.zeros(0, dtype=np.float32)
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self._recording = False
            # Le callback ne tourne plus après stream.stop() : lecture des frames sûre.
            frames = self._frames
            self._frames = []

        if not frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(frames, axis=0)
        # Réduction mono : moyenne des canaux si la capture était multi-canal.
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32, copy=False)
        audio = _resample(audio, self._capture_rate, self.samplerate)
        logger.info("Enregistrement arrêté : %.2f s.", audio.size / self.samplerate)
        return audio

    def record_until_silence(
        self,
        threshold: float = 0.01,
        silence_duration: float = 1.5,
        max_duration: float = 60.0,
        poll_interval: float = 0.05,
    ) -> np.ndarray:
        """Enregistre jusqu'à un silence prolongé (mode toggle / mains libres).

        S'arrête après ``silence_duration`` secondes sous ``threshold`` une fois
        la parole détectée, ou au plus tard après ``max_duration`` secondes.
        """
        self.start()
        speech_seen = False
        silence_started: Optional[float] = None
        t0 = time.monotonic()
        try:
            while True:
                time.sleep(poll_interval)
                now = time.monotonic()
                if self.current_level >= threshold:
                    speech_seen = True
                    silence_started = None
                elif speech_seen:
                    if silence_started is None:
                        silence_started = now
                    elif now - silence_started >= silence_duration:
                        break
                if now - t0 >= max_duration:
                    logger.info("Durée max atteinte (%.0f s).", max_duration)
                    break
        finally:
            audio = self.stop()
        return audio

    # -- utilitaires -----------------------------------------------------------
    def save_wav(self, path: Union[str, Path], audio: np.ndarray) -> Path:
        """Écrit ``audio`` (float32 [-1, 1]) en WAV 16 bits PCM mono à 16 kHz."""
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        clipped = np.clip(audio, -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype(np.int16)
        with wave.open(str(dest), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16 bits
            wav.setframerate(self.samplerate)
            wav.writeframes(pcm16.tobytes())
        return dest


def _demo() -> None:
    """Démo Étape 1 : liste les micros, enregistre, sauvegarde un WAV temporaire."""
    import tempfile

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("Périphériques d'entrée détectés :")
    devices = list_input_devices()
    if not devices:
        print("  (aucun micro détecté — branchez un micro et réessayez)")
        return
    for d in devices:
        print(
            f"  [{d['index']}] {d['name']} "
            f"({d['channels']} canal/canaux, {d['default_samplerate']} Hz)"
        )

    def meter(level: float) -> None:
        bars = int(min(level, 0.3) / 0.3 * 40)
        print("\r  niveau |" + "#" * bars + " " * (40 - bars) + "|", end="", flush=True)

    rec = AudioRecorder(level_callback=meter)
    print("\nParlez maintenant… (arrêt auto après 1,5 s de silence, max 10 s)")
    try:
        audio = rec.record_until_silence(max_duration=10.0)
    except MicrophoneError as exc:
        print(f"\nErreur micro : {exc}")
        return
    print()  # saut de ligne après le compteur

    if audio.size == 0:
        print("Aucun son capté.")
        return

    out = Path(tempfile.gettempdir()) / "whisperty" / "demo.wav"
    rec.save_wav(out, audio)
    print(f"Durée   : {audio.size / rec.samplerate:.2f} s")
    print(f"Crête   : {float(np.max(np.abs(audio))):.3f}")
    print(f"Fichier : {out}")


if __name__ == "__main__":
    _demo()
