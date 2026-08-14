"""Whisperty — diarisation des locuteurs en réunion (UC-18, 100 % locale).

Distingue **chaque orateur** d'une source audio (plusieurs personnes en salle sur le
micro, plusieurs participants distants sur la sortie système) au-delà de la simple
distinction par source (``Moi`` / ``Interlocuteurs`` d'UC-10). Chaque segment reçoit
une **étiquette de locuteur stable** (``Locuteur 1``, ``Locuteur 2``, …), entrelacée
chronologiquement avec les segments des deux sources (cf. :mod:`whisperty.conference`).

Doctrine — **zéro réseau** : contrairement à ``pyannote`` (PyTorch + modèles *gated*
Hugging Face, en tension avec la contrainte cardinale), deux backends d'empreinte
vocale **locaux** sont proposés (``speaker_diarization.backend``) :

- ``mfcc`` (**défaut**) : statistiques MFCC par segment en pur NumPy. **Rien n'est
  téléchargé** — la garantie zéro-fuite la plus forte possible (rien à télécharger =
  rien ne peut sortir de la machine, CO-17). Précision faible en contrepartie : ne
  sépare que des voix très différentes.
- ``onnx`` (**option, CO-19**) : modèle de vérification du locuteur ONNX local
  (:class:`OnnxEmbedder`), inférence **CPU uniquement**, features fbank kaldi
  calculées ici même en NumPy. Nettement plus précis ; le modèle est téléchargé
  **explicitement** depuis l'écran Configuration (cf. :mod:`whisperty.modeldl`), puis
  tout est hors-ligne. Échec de chargement ⇒ repli ``mfcc`` notifié (BR-08/RE-13).

L'embedder reste **enfichable** (``embed_fn``) : le clustering, le registre et le
repli ignorent quel backend produit les empreintes.

Concurrence : :class:`SpeakerRegistry` est protégé par un **verrou feuille** interne
(``assign`` depuis le worker de diarisation, ``rename``/``speakers`` depuis le pont
GUI) — jamais imbriqué avec un autre verrou de l'application. La session ONNX est
créée sur le thread qui démarre la session de réunion (``Diarizer.__init__``, donc
``ConferenceTranscriber.start``) puis utilisée **exclusivement** par le worker
``_diar_loop`` : un seul thread l'appelle, et chaque session a la sienne. Les caches
de bancs de filtres (module-globaux) sont écrits sans verrou — publication par
affectation de clé unique, calcul idempotent, tableaux jamais mutés ensuite.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional, Union

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


# -- backend ONNX (CO-19) : empreinte par modèle de vérification du locuteur -----
# ⚠️ CONFIDENTIALITÉ — EP explicitement limité au CPU. Les roues onnxruntime récentes
# exposent AUSSI « AzureExecutionProvider » (inférence DÉPORTÉE vers un endpoint Azure) :
# laisser onnxruntime choisir ses providers reviendrait à tolérer un chemin réseau dans
# le cœur du produit. On passe donc TOUJOURS providers=("CPUExecutionProvider",),
# ce qui écarte aussi CUDA/DirectML (inutiles ici, et non supportés côté CTranslate2).
_ONNX_PROVIDERS = ("CPUExecutionProvider",)

# Seuil de similarité par défaut du backend ONNX, si la config n'en fournit pas.
# DOIT rester aligné sur ``config.SpeakerDiarizationConfig.onnx_similarity_threshold``
# (calibré sur des enregistrements multi-locuteurs réels).
_ONNX_THRESHOLD_DEFAULT = 0.45


def _make_onnx_session(model_path: Union[str, Path]):
    """Ouvre une session onnxruntime **CPU** sur ``model_path`` (aucun réseau).

    Lève (``ImportError``, ``FileNotFoundError``, ``Exception`` d'onnxruntime) —
    l'appelant retombe sur le backend MFCC (BR-08/RE-13). Import PARESSEUX :
    onnxruntime ne doit peser sur le démarrage que si la diarisation ONNX est
    demandée (il est déjà embarqué pour le VAD Silero de faster-whisper).
    """
    path = Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(f"Modèle de diarisation introuvable : {path}")
    import onnxruntime as ort

    # Télémétrie TraceLogging/ETW des builds Windows : coupée dès l'import, comme sur
    # le chemin du VAD (cf. transcriber._disable_ort_telemetry — factorisé là-bas pour
    # que les DEUX chemins d'import d'onnxruntime soient couverts).
    try:
        from .transcriber import _disable_ort_telemetry

        _disable_ort_telemetry()
    except Exception:  # noqa: BLE001 — best-effort, jamais bloquant
        logger.debug("Coupure de la télémétrie onnxruntime indisponible.", exc_info=True)
    options = ort.SessionOptions()
    options.enable_profiling = False          # aucun fichier de profil sur le disque
    options.log_severity_level = 3            # warnings et au-delà seulement
    # Un seul thread intra-op : les segments sont courts et la session tourne dans le
    # worker de diarisation, en parallèle de la transcription (elle-même multithread) —
    # inutile de se disputer les cœurs.
    options.intra_op_num_threads = 1
    return ort.InferenceSession(
        str(path), sess_options=options, providers=list(_ONNX_PROVIDERS)
    )


# Convention d'entrée des modèles d'empreinte vocale usuels (WeSpeaker, 3D-Speaker,
# CAM++/ERes2Net exportés ONNX) : banc de filtres log-mel « kaldi fbank », 80 canaux,
# fenêtres de 25 ms toutes les 10 ms, normalisation de moyenne par séquence (CMN).
_FBANK_N_MELS = 80
_FBANK_N_FFT = 512
_FBANK_LOW_HZ = 20.0        # kaldi : low_freq=20 (écarte le souffle/continu)
_KALDI_FB_CACHE: dict[tuple, np.ndarray] = {}


def _kaldi_mel_filterbank(n_mels: int, n_fft: int, sr: int, low_hz: float) -> np.ndarray:
    """Banc de filtres triangulaires mel **façon kaldi** (``n_mels`` × ``n_fft//2+1``).

    Différence assumée avec :func:`_mel_filterbank` (backend MFCC) : les pentes sont
    calculées sur le **mel réel de chaque bin FFT**, sans arrondi des bornes à un
    index de bin. C'est la convention des extracteurs kaldi/torchaudio dont dépendent
    les modèles d'empreinte vocale ; l'ancien banc reste inchangé pour ne pas
    déplacer les seuils du backend MFCC (rétrocompatibilité des sessions passées).
    """
    key = (n_mels, n_fft, sr, low_hz)
    cached = _KALDI_FB_CACHE.get(key)
    if cached is not None:
        return cached
    # ⚠️ PIÈGE kaldi : le banc est construit sur n_fft//2 bins (256 pour une FFT de 512),
    # PAS sur les n_fft//2+1 bins de rfft — le bin de Nyquist est IGNORÉ, complété par
    # une colonne de zéros (cf. torchaudio.compliance.kaldi : F.pad(..., (0, 1))).
    # Un banc construit naïvement sur 257 bins décale tout le log-mel et produit des
    # empreintes subtilement fausses (dégradation silencieuse de la séparation).
    n_bins = n_fft // 2
    high_hz = sr / 2.0
    mel_low, mel_high = _hz_to_mel(low_hz), _hz_to_mel(high_hz)
    # n_mels+2 points : chaque filtre m couvre [points[m], points[m+2]], centre points[m+1].
    mel_points = np.linspace(mel_low, mel_high, n_mels + 2)
    bin_hz = np.arange(n_bins, dtype=np.float64) * (sr / float(n_fft))
    bin_mel = _hz_to_mel(bin_hz)
    fb = np.zeros((n_mels, n_bins + 1), dtype=np.float32)   # +1 = bin Nyquist à zéro
    for m in range(n_mels):
        left, center, right = mel_points[m], mel_points[m + 1], mel_points[m + 2]
        rising = (bin_mel - left) / max(center - left, 1e-9)
        falling = (right - bin_mel) / max(right - center, 1e-9)
        # Triangles SANS normalisation d'aire (kaldi ; librosa/Slaney normalise, lui).
        fb[m, :n_bins] = np.clip(np.minimum(rising, falling), 0.0, None)
    _KALDI_FB_CACHE[key] = fb
    return fb


def fbank_features(
    audio: np.ndarray,
    sr: int = SAMPLE_RATE,
    n_mels: int = _FBANK_N_MELS,
    cmn: bool = True,
) -> Optional[np.ndarray]:
    """Features log-mel « kaldi fbank » d'un signal mono float32 (pur NumPy).

    Renvoie un tableau ``(trames, n_mels)`` float32, ou ``None`` si le segment est
    trop court pour une seule trame. ``cmn`` retire la moyenne par canal sur la
    séquence (normalisation attendue par les modèles WeSpeaker/3D-Speaker).

    Approximation ASSUMÉE de kaldi : mêmes fenêtrage (povey), pré-accentuation,
    retrait du continu et échelle mel, mais **sans dither** (bruit aléatoire : nuirait
    au déterminisme sans bénéfice ici). Les écarts résiduels avec l'extracteur de
    référence restent faibles devant la robustesse de ces réseaux ; c'est le même
    compromis pragmatique que le backend MFCC, documenté plutôt que masqué.
    """
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    # kaldi travaille sur des échantillons entiers 16 bits : nos flottants [-1, 1] sont
    # remis à cette échelle, sinon le log-mel serait décalé d'une constante par rapport
    # aux features vues à l'entraînement.
    x = x * 32768.0
    frame_len = int(round(0.025 * sr))
    hop = max(1, int(round(0.010 * sr)))
    if x.size < frame_len:
        return None
    n_frames = 1 + (x.size - frame_len) // hop      # snip_edges=True (défaut kaldi)
    idx = np.arange(frame_len)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx].astype(np.float32)
    frames = frames - frames.mean(axis=1, keepdims=True)          # remove_dc_offset
    # Pré-accentuation PAR TRAME, premier échantillon répliqué (comme kaldi).
    frames = np.concatenate(
        [frames[:, :1] * (1.0 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1
    )
    # Fenêtre « povey » : hann élevé à la puissance 0,85.
    n = np.arange(frame_len, dtype=np.float32)
    hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * n / (frame_len - 1))
    frames = frames * np.power(hann, 0.85).astype(np.float32)
    power = np.abs(np.fft.rfft(frames, n=_FBANK_N_FFT)) ** 2       # use_power=true
    fb = _kaldi_mel_filterbank(n_mels, _FBANK_N_FFT, sr, _FBANK_LOW_HZ)
    # Plancher = eps float32 (≈1,19e-07), comme kaldi/torchaudio — et NON « tiny ».
    feats = np.log(np.maximum(power @ fb.T, float(np.finfo(np.float32).eps)))
    if cmn:
        feats = feats - feats.mean(axis=0, keepdims=True)
    return feats.astype(np.float32)


class OnnxEmbedder:
    """Empreinte vocale par **modèle ONNX local** (CO-19), enfichable dans :class:`Diarizer`.

    Appelable ``(audio, sr) -> Optional[np.ndarray]`` L2-normalisée, exactement comme
    :func:`speaker_embedding` : le reste de la chaîne (clustering, registre, repli)
    est inchangé. 100 % hors-ligne — le modèle est un fichier local (téléchargement
    opt-in séparé, cf. :mod:`whisperty.modeldl`), l'inférence est cantonnée au CPU
    (cf. ``_ONNX_PROVIDERS``).

    La session est créée au **constructeur** : un modèle absent/illisible lève ici,
    donc l'appelant retombe sur le backend MFCC AVANT le début de la session plutôt
    qu'à mi-réunion (BR-08). Le nombre de canaux mel et le nom des entrées/sorties
    sont déduits du graphe : le même code sert plusieurs familles de modèles.
    """

    def __init__(self, model_path: Union[str, Path], sample_rate: int = SAMPLE_RATE) -> None:
        self._sr = int(sample_rate)
        self._session = _make_onnx_session(model_path)
        inputs = self._session.get_inputs()
        if not inputs:
            raise ValueError("Modèle de diarisation sans entrée exploitable.")
        self._input_name = inputs[0].name
        self._output_name = self._session.get_outputs()[0].name
        # Dernière dimension de l'entrée = nombre de canaux mel quand elle est fixe
        # (ex. [batch, trames, 80]) ; sinon on garde la convention 80.
        shape = list(getattr(inputs[0], "shape", []) or [])
        last = shape[-1] if shape else None
        self._n_mels = int(last) if isinstance(last, int) and last > 1 else _FBANK_N_MELS
        # Certains exports attendent un second tenseur « longueur de séquence ».
        self._length_input = inputs[1].name if len(inputs) > 1 else None
        self._dim = 0                            # renseigné par la 1re inférence
        # Inférence à blanc sur un signal SYNTHÉTIQUE (jamais l'audio de l'utilisateur) :
        # un modèle qui charge mais attend une autre entrée (forme d'onde brute, autre
        # nombre de canaux sur un axe dynamique) échouerait sinon à CHAQUE segment, avec
        # une trace par segment et un repli silencieux pendant toute la réunion. En
        # levant ici, l'incompatibilité passe par le MÊME repli notifié que les autres
        # échecs de chargement (BR-08), avant le début de la session.
        probe = np.zeros(int(0.6 * self._sr), dtype=np.float32)
        probe[::40] = 0.05                       # un peu d'énergie : évite un cas dégénéré
        if self._embed_raw(probe, self._sr) is None:
            raise ValueError("Le modèle n'a produit aucune empreinte exploitable.")
        logger.info(
            "Diarisation ONNX : modèle « %s » chargé (CPU, %d canaux mel, dimension %d).",
            Path(model_path).name, self._n_mels, self._dim,
        )

    def _embed_raw(self, audio: np.ndarray, sr: int) -> Optional[np.ndarray]:
        """Inférence brute (features → modèle → vecteur L2-normalisé), ou ``None``.

        Laisse remonter les exceptions d'onnxruntime : au constructeur elles déclenchent
        le repli notifié, en session ``__call__`` les absorbe (BR-08).
        """
        feats = fbank_features(audio, sr or self._sr, n_mels=self._n_mels)
        if feats is None or feats.shape[0] < 2:
            return None
        batch = feats[None, :, :].astype(np.float32)
        feeds = {self._input_name: batch}
        if self._length_input is not None:
            feeds[self._length_input] = np.array([feats.shape[0]], dtype=np.int64)
        outputs = self._session.run([self._output_name], feeds)
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm < 1e-8:
            return None
        self._dim = int(vector.shape[0])
        return vector / norm

    def __call__(self, audio: np.ndarray, sr: int = SAMPLE_RATE) -> Optional[np.ndarray]:
        """Empreinte L2-normalisée du segment, ou ``None`` (trop court / dégénéré).

        Ne lève pas pour un segment inexploitable : ``Diarizer.identify`` traite
        ``None`` comme un repli sur l'étiquette de source (BR-08). Le contrat d'entrée
        du modèle a déjà été validé au constructeur (inférence à blanc), donc une erreur
        ici est ponctuelle (segment atypique) et ne doit pas interrompre la session.
        """
        try:
            return self._embed_raw(audio, sr)
        except Exception:  # noqa: BLE001 — segment isolé : repli étiquette de source
            logger.warning("Empreinte ONNX indisponible pour un segment.", exc_info=True)
            return None


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
        model_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self._sr = int(sample_rate)
        self._min_samples = int(max(0.0, float(getattr(config, "min_segment", 1.0))) * self._sr)
        # Backend d'empreinte (CO-19). ``embed_fn`` explicite (tests, futur backend)
        # court-circuite le choix ; sinon ``backend: onnx`` tente le modèle local et
        # retombe sur les statistiques MFCC en cas d'échec (BR-08/RE-13) — la session
        # est ouverte ICI, donc le repli est décidé AVANT le début de la réunion.
        self.backend = "mfcc"
        self.notice: Optional[str] = None
        threshold = float(getattr(config, "similarity_threshold", 0.75))
        if embed_fn is not None:
            self._embed = embed_fn
            self.backend = str(getattr(config, "backend", "mfcc") or "mfcc").lower()
            if self.backend == "onnx":
                threshold = float(getattr(config, "onnx_similarity_threshold", _ONNX_THRESHOLD_DEFAULT))
        elif str(getattr(config, "backend", "mfcc") or "mfcc").strip().lower() == "onnx":
            self._embed, threshold = self._make_onnx_embed(config, model_path, threshold)
        else:
            self._embed = speaker_embedding
        self._registry = SpeakerRegistry(
            similarity_threshold=threshold,
            max_speakers=int(getattr(config, "max_speakers", 6)),
            label_prefix=str(getattr(config, "label_prefix", "Locuteur")),
        )

    def _make_onnx_embed(self, config, model_path, mfcc_threshold: float):
        """Tente le backend ONNX ; renvoie ``(embed_fn, seuil)``, repli MFCC si échec.

        Le repli est journalisé ET exposé via ``notice`` : la qualité de séparation
        perçue change, l'utilisateur doit donc en être informé (l'appelant relaie la
        notice) plutôt que de croire la diarisation ONNX active.
        """
        path = model_path or getattr(config, "onnx_model", "") or ""
        try:
            embedder = OnnxEmbedder(path, self._sr)
        except FileNotFoundError:
            self.notice = (
                "Modèle de diarisation ONNX absent : locuteurs distingués par "
                "empreinte MFCC (moins précise). Téléchargez-le depuis l'écran "
                "Configuration."
            )
            logger.warning("Diarisation ONNX indisponible (modèle « %s » absent) ; repli MFCC.", path)
            return speaker_embedding, mfcc_threshold
        except ImportError:
            self.notice = (
                "onnxruntime indisponible : diarisation repliée sur l'empreinte MFCC."
            )
            logger.warning("onnxruntime absent ; repli sur l'empreinte MFCC.", exc_info=True)
            return speaker_embedding, mfcc_threshold
        except Exception:  # noqa: BLE001 — modèle corrompu, format inattendu, EP absent…
            self.notice = (
                "Modèle de diarisation ONNX illisible : repli sur l'empreinte MFCC "
                "(détails dans les journaux)."
            )
            logger.exception("Chargement du modèle de diarisation ONNX échoué ; repli MFCC.")
            return speaker_embedding, mfcc_threshold
        self.backend = "onnx"
        return embedder, float(getattr(config, "onnx_similarity_threshold", _ONNX_THRESHOLD_DEFAULT))

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
