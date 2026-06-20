# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Whisperty : dictée vocale 100 % locale pour Windows 10/11 (équivalent Superwhisper) basée sur
`faster-whisper`. Contrainte cardinale : **aucune donnée ne sort de la machine** — aucun
appel réseau à l'usage, hormis le téléchargement initial du modèle (désactivable via
`transcription.local_files_only`). Toute dépendance ou tout appel réseau qui violerait cela
doit être signalé, pas introduit silencieusement.

## Commands

```powershell
# Environnement (Windows / PowerShell). Python 3.10+ ; vérifié avec 3.14.3
# (toutes les roues, dont ctranslate2 et PyAV, sont disponibles en cp314).
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Lancer l'application (icône tray + raccourci global)
python -m whisperty

# Démo capture audio seule (sans modèle)
python -m whisperty.recorder

# Tests hors-ligne (logique pure : config, dictionnaire, injection, machine à états)
python tests/test_logic.py
```

## Architecture (pipeline de dictée)

Raccourci global → **recorder** (capture 16 kHz mono float32) → **transcriber**
(faster-whisper) → **post-traitement** (dictionnaire) → **injector** (collage/frappe dans
l'application active). L'état (idle / rec / processing) est reflété par le **tray** ;
`app.py` orchestre cette machine à états et `config.py` charge `config.yaml`.

| Module (`whisperty/`) | Rôle | État |
|------------------------|------|------|
| `recorder.py` | Capture micro non bloquante (sounddevice), RMS pour VAD/tray, WAV PCM16, thread-safe | fait |
| `transcriber.py` | Wrapper faster-whisper (modèle configurable, hotwords, garde hors-ligne) | fait |
| `injector.py` | Injection texte (collage Ctrl+V par défaut, frappe en repli) | fait |
| `tray.py` | Icône zone de notification (pystray) | fait |
| `app.py` | Orchestration / machine à états (RLock) + raccourci global + surveillance VAD + V2 (import audio, historique, IA, profils) | fait |
| `config.py` | Chargement de `config.yaml` | fait |
| `dictionary.py` | Chargement dictionnaire + corrections | fait |
| `history.py` | Historique des transcriptions (SQLite local, thread-safe) | fait (V2) |
| `ai.py` | Raffinage texte par LLM **local** (garde localhost, désactivé par défaut) | fait (V2) |
| `profiles.py` | Profils de contexte par application (override prompt/langue/dico) | fait (V2) |
| `winutil.py` | Détection de l'application active (ctypes Win32, local) | fait (V2) |
| `loopback.py` | Capture loopback d'une sortie audio (soundcard/WASAPI, local) | fait (V2) |
| `live.py` | Transcription live continue d'une sortie (segmenteur VAD + sink) | fait (V2) |

## Concurrence (à préserver)

- Transitions d'état sérialisées par `WhispertyApp._lock` (RLock). Verrou interne
  `AudioRecorder._op_lock` pour `start()`/`stop()`. **Ordre imposé : `_lock` → `_op_lock`,
  jamais l'inverse** ; le callback PortAudio (`_callback`) ne prend aucun verrou.
- `_stop_and_process()` relâche `_lock` avant l'arrêt bloquant de PortAudio. À l'inverse,
  `_start_recording()` tient `_lock` pendant `recorder.start()` **à dessein** (évite un flux
  orphelin si un stop concurrent survient pendant l'ouverture du périphérique).
- **Live (V2)** : `stop_live()` ne tient PAS `_lock` et ne joint PAS le thread live ; c'est
  `LiveTranscriber._finish` → `_on_live_finished` (qui reprend `_lock`) qui repasse à IDLE.
  Tenir le verrou pendant un `join()` provoquerait un interblocage avec ce callback.

## Décisions d'architecture à respecter

- **Audio** : Whisper exige 16 kHz mono float32. `recorder` rééchantillonne si le micro
  n'expose pas 16 kHz (soxr, repli interpolation NumPy).
- **GPU** : CTranslate2 ne supporte que **CPU et CUDA**. Pas de DirectML — ne pas le proposer
  pour AMD/Intel ; sur ces GPU, rester en CPU int8 (ou envisager whisper.cpp/Vulkan).
- **Confidentialité** : `local_files_only` est **true par défaut** (zéro réseau) ; en mode
  hors-ligne, `transcriber.load()` pose aussi `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`.
- **IA locale (V2)** : `ai.py` n'autorise QUE des endpoints locaux (`ai.is_local_endpoint` :
  localhost/127.0.0.1/::1) et est **désactivé par défaut**. Tout endpoint distant est refusé —
  le texte dicté ne doit jamais sortir de la machine. Échec LLM = texte brut conservé (jamais bloquant).
- **Historique (V2)** : `history.py` = SQLite local (`sqlite3` stdlib), connexion partagée
  `check_same_thread=False` mais **tous les accès passent par `History._lock`** ; écriture non bloquante.
- **Profils (V2)** : surcharge `initial_prompt`/langue/dictionnaire selon l'app active, capturée
  par `winutil.foreground_app()` au **démarrage** de la dictée (= cible de l'injection).
- **Loopback (V2)** : la capture d'une **sortie** audio passe par `soundcard` (WASAPI loopback) —
  le PortAudio embarqué par `sounddevice` n'expose PAS le loopback (vérifié : `PaWasapi_IsLoopback`
  absent du binaire). `soundcard` est local (Core Audio via ctypes), importé paresseusement.
  ⚠️ **COM par thread** : `soundcard` n'initialise COM que sur son thread d'import. Tout thread
  worker qui l'appelle (transcription live, futur coordinateur réunion) DOIT s'envelopper dans
  `loopback.com_initialized()`, sinon `CO_E_NOTINITIALIZED` (0x800401F0). `get_microphone()` lève
  `IndexError` (jamais `None`) sur id inconnu — déjà géré dans `resolve_loopback`.
  La transcription live (`live.py`) est un **mode exclusif** de la dictée (état `TrayState.LIVE`) ;
  les segments sont découpés par un VAD RMS et transcrits avec les défauts de base (pas de profil,
  pas de LLM — priorité à la latence).
- **Injection FR** : privilégier le collage presse-papiers (Ctrl+V) à la frappe caractère par
  caractère — bien plus fiable pour les accents (é, è, à, ç) et les longs textes.
- **Raccourci** : ne pas utiliser `Win+Space` (réservé par Windows). Défaut configurable.
- **ffmpeg** : non requis (PyAV est embarqué par faster-whisper) — ne pas l'ajouter en dépendance.
- **Nommage** : module `injector` et non `typer` (la lib PyPI `typer` est tirée transitivement
  par huggingface-hub — un module `typer.py` la masquerait).
- **Packaging** : `config.yaml`/`dictionary.txt` ne sont PAS embarqués dans l'exe (éditables) ;
  ils doivent être déposés à côté de `whisperty.exe`. `upx=False` (UPX corrompt les DLL natives).

## Conventions

- Code commenté **en français**, docstrings, type hints (`from __future__ import annotations`).
- Gestion d'erreurs robuste et explicite : micro absent, modèle non téléchargé, droits insuffisants.
