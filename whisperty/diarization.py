"""Whisperty — diarisation des locuteurs en réunion (UC-18, 100 % locale).

Distingue **chaque orateur** d'une source audio (plusieurs personnes en salle sur le
micro, plusieurs participants distants sur la sortie système) au-delà de la simple
distinction par source (``Moi`` / ``Interlocuteurs`` d'UC-10). Chaque segment reçoit
une **étiquette de locuteur stable** (``Locuteur 1``, ``Locuteur 2``, …), entrelacée
chronologiquement avec les segments des deux sources (cf. :mod:`whisperty.conference`).

Doctrine — **zéro réseau, zéro dépendance nouvelle** : contrairement à ``pyannote``
(PyTorch + modèles *gated* Hugging Face, en tension avec la contrainte cardinale), la
diarisation intégrée repose sur une **empreinte vocale calculée en pur NumPy**
(statistiques MFCC par segment) et un **clustering en ligne** (similarité cosinus).
Rien n'est téléchargé : c'est la garantie zéro-fuite la plus forte possible (rien à
télécharger = rien ne peut sortir de la machine, CO-17). C'est un compromis *latence /
simplicité* assumé (précision inférieure à un modèle neuronal dédié), suffisant pour
distinguer des voix nettement différentes ; l'embedder est **enfichable**
(``embed_fn``) pour brancher un jour un backend ONNX hors-ligne sans toucher au reste.

Concurrence : :class:`SpeakerRegistry` est protégé par un **verrou feuille** interne
(``assign`` depuis le worker de diarisation, ``rename``/``speakers`` depuis le pont
GUI) — jamais imbriqué avec un autre verrou de l'application.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# -- empreinte vocale (statistiques MFCC, pur NumPy) --------------------------
_N_MELS = 26
_N_MFCC = 13          # coefficients conservés (c1..c13, le c0 « énergie » est jeté)
_N_FFT = 512
_FRAME_S = 0.025      # fenêtre d'analyse (25 ms)
_HOP_S = 0.010        # pas entre fenêtres (10 ms)
_MIN_FRAMES = 5       # sous ce nombre de trames, segment trop court pour une empreinte

# Bancs de filtres et matrices DCT mémorisés par (n_mels, n_fft, sr) : invariants,
# on évite de les recalculer à chaque segment.
_FILTERBANK_CACHE: dict[tuple[int, int, int], np.ndarray] = {}
_DCT_CACHE: dict[tuple[int, int], np.ndarray] = {}


def _hz_to_mel(freq: float) -> float:
    return 2595.0 * np.log10(1.0 + freq / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(n_mels: int, n_fft: int, sr: int) -> np.ndarray:
    """Banc de filtres triangulaires mel (``n_mels`` × ``n_fft//2+1``)."""
    key = (n_mels, n_fft, sr)
    cached = _FILTERBANK_CACHE.get(key)
    if cached is not None:
        return cached
    fmax = sr / 2.0
    mel_pts = np.linspace(_hz_to_mel(0.0), _hz_to_mel(fmax), n_mels + 2)
    hz_pts = _mel_to_hz(mel_pts)
    bins = np.floor((n_fft + 1) * hz_pts / sr).astype(int)
    n_freqs = n_fft // 2 + 1
    bins = np.clip(bins, 0, n_freqs - 1)
    fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left, center, right = bins[m - 1], bins[m], bins[m + 1]
        center = max(center, left + 1)
        right = max(right, center + 1)
        for k in range(left, min(center, n_freqs)):
            fb[m - 1, k] = (k - left) / max(center - left, 1)
        for k in range(center, min(right, n_freqs)):
            fb[m - 1, k] = (right - k) / max(right - center, 1)
    _FILTERBANK_CACHE[key] = fb
    return fb


def _dct_matrix(n_out: int, n_in: int) -> np.ndarray:
    """Matrice DCT-II (``n_out`` × ``n_in``) pour passer du log-mel aux MFCC."""
    key = (n_out, n_in)
    cached = _DCT_CACHE.get(key)
    if cached is not None:
        return cached
    n = np.arange(n_in)
    k = np.arange(n_out).reshape(-1, 1)
    mat = np.cos(np.pi * (2 * n + 1) * k / (2 * n_in)).astype(np.float32)
    _DCT_CACHE[key] = mat
    return mat


def speaker_embedding(audio: np.ndarray, sr: int = SAMPLE_RATE) -> Optional[np.ndarray]:
    """Empreinte vocale L2-normalisée d'un segment mono float32, ou ``None``.

    Renvoie ``None`` (→ repli sur l'étiquette de source, BR-08) si le segment est trop
    court, quasi silencieux ou dégénéré. Sinon un vecteur ``2*_N_MFCC`` (moyenne + écart
    type des MFCC sur les trames), normalisé pour comparer par similarité cosinus.
    """
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    frame_len = int(_FRAME_S * sr)
    hop = max(1, int(_HOP_S * sr))
    if x.size < frame_len:
        return None
    if float(np.sqrt(np.mean(x * x))) < 1e-4:  # quasi silence : pas d'empreinte fiable
        return None
    # Pré-accentuation (rehausse les hautes fréquences, classique en traitement parole).
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    n_frames = 1 + (x.size - frame_len) // hop
    if n_frames < _MIN_FRAMES:
        return None
    idx = np.arange(frame_len)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx].astype(np.float32) * np.hamming(frame_len).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames, n=_N_FFT))
    power = (mag * mag) / _N_FFT
    fb = _mel_filterbank(_N_MELS, _N_FFT, sr)
    logmel = np.log(power @ fb.T + 1e-10)
    dct = _dct_matrix(_N_MFCC + 1, _N_MELS)      # produit c0..c13
    mfcc = (logmel @ dct.T)[:, 1:]               # jette c0 (énergie) → c1..c13
    feat = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)]).astype(np.float32)
    norm = float(np.linalg.norm(feat))
    if not np.isfinite(norm) or norm < 1e-8:
        return None
    return feat / norm


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Similarité cosinus de deux vecteurs (0 si l'un est nul). Logique pure."""
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class _Cluster:
    """Centroïde d'un locuteur : moyenne courante (normalisée) des empreintes assignées."""

    __slots__ = ("speaker_id", "centroid", "count")

    def __init__(self, speaker_id: int, centroid: np.ndarray) -> None:
        self.speaker_id = speaker_id
        self.centroid = centroid
        self.count = 1

    def update(self, embedding: np.ndarray) -> None:
        # Moyenne incrémentale puis renormalisation (le centroïde reste comparable en cosinus).
        merged = (self.centroid * self.count + embedding) / (self.count + 1)
        norm = float(np.linalg.norm(merged))
        self.centroid = merged / norm if norm > 1e-8 else merged
        self.count += 1


class SpeakerRegistry:
    """Clustering **en ligne** d'empreintes vocales en étiquettes de locuteur stables.

    Clustering **par source** (le plafond ``max_speakers`` s'applique par source, FR-32)
    mais **numérotation globale** : les identifiants sont alloués dans l'ordre de
    première apparition, toutes sources confondues, si bien que l'étiquette ne révèle
    que l'identité vocale, pas la provenance technique (UC-18). Thread-safe (verrou
    feuille) : ``assign`` vient du worker de diarisation, ``rename``/``speakers`` du
    pont GUI.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.75,
        max_speakers: int = 6,
        label_prefix: str = "Locuteur",
    ) -> None:
        self._threshold = float(similarity_threshold)
        self._max_speakers = max(1, int(max_speakers))
        self._prefix = str(label_prefix) or "Locuteur"
        self._lock = threading.Lock()
        self._by_source: dict[str, list[_Cluster]] = {}
        self._next_id = 0
        self._renames: dict[int, str] = {}

    @staticmethod
    def key_for(speaker_id: int) -> str:
        return f"spk:{speaker_id}"

    @staticmethod
    def _id_of(key: str) -> Optional[int]:
        if isinstance(key, str) and key.startswith("spk:"):
            try:
                return int(key[4:])
            except ValueError:
                return None
        return None

    def assign(self, source: str, embedding: np.ndarray) -> str:
        """Attribue (ou crée) un locuteur pour ``embedding`` sur ``source``.

        Renvoie une **clé stable** (``spk:N``) ; le libellé affichable en découle via
        :meth:`label` (renommage appliqué). Nouveau locuteur si aucun centroïde de la
        source n'est assez proche ET que le plafond n'est pas atteint ; sinon rattaché
        au plus proche (regroupement plutôt qu'omission).
        """
        emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
        with self._lock:
            clusters = self._by_source.setdefault(source, [])
            best: Optional[_Cluster] = None
            best_sim = -1.0
            for cluster in clusters:
                sim = cosine_similarity(emb, cluster.centroid)
                if sim > best_sim:
                    best_sim, best = sim, cluster
            if best is not None and best_sim >= self._threshold:
                best.update(emb)
                return self.key_for(best.speaker_id)
            if len(clusters) < self._max_speakers:
                speaker_id = self._next_id
                self._next_id += 1
                clusters.append(_Cluster(speaker_id, emb))
                return self.key_for(speaker_id)
            # Plafond atteint : rattache au plus proche (jamais d'omission).
            if best is not None:
                best.update(emb)
                return self.key_for(best.speaker_id)
            # Cas dégénéré (max_speakers < 1 impossible car borné à 1) — filet de sécurité.
            speaker_id = self._next_id
            self._next_id += 1
            clusters.append(_Cluster(speaker_id, emb))
            return self.key_for(speaker_id)

    def label(self, key: str) -> str:
        """Libellé affichable d'une clé (``spk:N``) : nom renommé ou ``Prefix N+1``."""
        speaker_id = self._id_of(key)
        if speaker_id is None:
            return str(key)
        with self._lock:
            name = self._renames.get(speaker_id)
        return name if name else f"{self._prefix} {speaker_id + 1}"

    def rename(self, key: str, name: Optional[str]) -> bool:
        """Renomme un locuteur (FR-31). Nom vide/None = retour à l'étiquette auto.

        ``True`` si la clé désigne un locuteur connu, ``False`` sinon (clé invalide).
        """
        speaker_id = self._id_of(key)
        if speaker_id is None:
            return False
        clean = " ".join(str(name or "").split())
        with self._lock:
            known = any(
                cluster.speaker_id == speaker_id
                for clusters in self._by_source.values()
                for cluster in clusters
            )
            if not known:
                return False
            if clean:
                self._renames[speaker_id] = clean
            else:
                self._renames.pop(speaker_id, None)
        return True

    def speakers(self) -> list[dict]:
        """Liste des locuteurs détectés pour l'UI (triée par ordre d'apparition)."""
        with self._lock:
            rows = [
                {
                    "key": self.key_for(cluster.speaker_id),
                    "auto": f"{self._prefix} {cluster.speaker_id + 1}",
                    "name": self._renames.get(cluster.speaker_id, ""),
                    "count": cluster.count,
                    "_id": cluster.speaker_id,
                }
                for clusters in self._by_source.values()
                for cluster in clusters
            ]
        rows.sort(key=lambda r: r["_id"])
        for row in rows:
            row["label"] = row["name"] or row["auto"]
            del row["_id"]
        return rows


class Diarizer:
    """Orchestration de la diarisation : empreinte + clustering + repli gracieux.

    Une instance par **session** de réunion (la numérotation des locuteurs est stable
    sur la session, réinitialisée à la suivante). ``identify`` est appelé depuis le
    worker de diarisation dédié (RE-14) ; ``rename``/``speakers`` depuis le pont GUI.
    """

    def __init__(
        self,
        config,
        sample_rate: int = SAMPLE_RATE,
        embed_fn: Optional[Callable[[np.ndarray, int], Optional[np.ndarray]]] = None,
    ) -> None:
        self._sr = int(sample_rate)
        self._embed = embed_fn or speaker_embedding
        self._min_samples = int(max(0.0, float(getattr(config, "min_segment", 1.0))) * self._sr)
        self._registry = SpeakerRegistry(
            similarity_threshold=float(getattr(config, "similarity_threshold", 0.75)),
            max_speakers=int(getattr(config, "max_speakers", 6)),
            label_prefix=str(getattr(config, "label_prefix", "Locuteur")),
        )

    def identify(self, audio: np.ndarray, source: str, fallback_key: str) -> str:
        """Clé de locuteur pour ce segment, ou ``fallback_key`` (étiquette de source).

        Ne lève jamais (BR-08 : la diarisation n'interrompt ni la capture ni la
        transcription) : tout segment trop court, silencieux ou en erreur retombe sur
        l'étiquette de source.
        """
        try:
            arr = np.asarray(audio, dtype=np.float32).reshape(-1)
            if arr.shape[0] < self._min_samples:
                return fallback_key
            embedding = self._embed(arr, self._sr)
            if embedding is None:
                return fallback_key
            return self._registry.assign(source, embedding)
        except Exception:  # noqa: BLE001 — jamais bloquant (RE-13/BR-08)
            logger.exception("Diarisation d'un segment échouée ; repli sur la source.")
            return fallback_key

    def label(self, key: str) -> str:
        """Libellé affichable d'une clé : ``spk:N`` → locuteur ; sinon la clé telle quelle."""
        if isinstance(key, str) and key.startswith("spk:"):
            return self._registry.label(key)
        return str(key)

    def rename(self, key: str, name: Optional[str]) -> bool:
        return self._registry.rename(key, name)

    def speakers(self) -> list[dict]:
        return self._registry.speakers()
