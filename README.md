# 🎙️ Whisperty

> **Dictée vocale 100 % locale pour Windows 10/11** — une alternative libre à Superwhisper,
> propulsée par OpenAI Whisper (`faster-whisper`).

![Plateforme](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Confidentialité](https://img.shields.io/badge/100%25-local%20%C2%B7%20z%C3%A9ro%20r%C3%A9seau-2ea44f)
![Moteur](https://img.shields.io/badge/moteur-faster--whisper-ff7139)

Appuyez sur un raccourci, parlez, relâchez : votre voix est transcrite **sur votre machine**
puis insérée directement dans l'application active — VS Code, Outlook, Teams, navigateur,
n'importe où. **Aucune donnée audio ni texte ne quitte jamais votre ordinateur.**

```
  Ctrl+Alt+Espace        🎙️ micro 16 kHz       🧠 Whisper local       📖 dictionnaire       ⌨️ collage
 ───────────────►  capture  ───────────►  faster-whisper  ──────►  + corrections  ──────►  app active
     (raccourci)          (sounddevice)        (CPU / CUDA)         (hotwords)         (Ctrl+V robuste FR)
```

---

## Sommaire

- [Pourquoi Whisperty ?](#pourquoi-whisperty-)
- [Confidentialité d'abord](#confidentialité-dabord)
- [Fonctionnalités](#fonctionnalités)
- [Démarrage rapide](#démarrage-rapide)
- [Configuration](#configuration)
- [Fonctions avancées](#fonctions-avancées)
- [Dictionnaire personnalisé](#dictionnaire-personnalisé)
- [Tests](#tests)
- [Packaging & démarrage automatique](#packaging--démarrage-automatique)
- [Accélération GPU NVIDIA](#accélération-gpu-nvidia)
- [Architecture](#architecture)
- [Feuille de route](#feuille-de-route)

---

## Pourquoi Whisperty ?

- 🔒 **Vraiment privé** — tout tourne en local. Pas de cloud, pas de compte, pas de télémétrie.
- ⚡ **Partout dans Windows** — la transcription s'injecte dans la fenêtre active, sans copier-coller.
- 🇫🇷 **Pensé pour le français** — collage presse-papiers fiable pour les accents, dictionnaire métier, profils par application.
- 🧩 **Sans friction** — une icône dans la zone de notification, un raccourci global, un seul fichier `config.yaml`.
- 🆓 **Libre et hackable** — Python pur, modules clairs, aucune dépendance propriétaire.

## Confidentialité d'abord

C'est la contrainte cardinale du projet : **aucune donnée ne sort de la machine.**

- Aucun appel réseau à l'usage, aucune télémétrie. Journalisation strictement locale.
- **Seule exception** : le *premier* téléchargement du modèle Whisper. Ensuite, avec
  `transcription.local_files_only: true` (le **défaut**), l'application fonctionne
  100 % hors-ligne — vérifiable à Wireshark.
- Le **mode IA optionnel** n'accepte que des endpoints locaux (`localhost`/`127.0.0.1`) ;
  tout hôte distant est rejeté par conception.

## Fonctionnalités

**Dictée**
- 🎙️ Enregistrement au **raccourci global** : `toggle`, push-to-talk ou double-appui.
- 🧠 Transcription Whisper locale, **modèle configurable** (base / small / medium / large-v3), CPU ou CUDA.
- ⌨️ **Injection system-wide** dans l'app active (collage Ctrl+V robuste, ou frappe en repli).
- 📖 **Dictionnaire personnalisé** : termes métier favorisés + corrections automatiques.
- 🔔 **Icône de notification** avec statut (gris = prêt, rouge = enregistrement, orange = transcription, bleu = capture live).

**Au-delà de la dictée**
- 📜 **Historique local** (SQLite) — purge automatique, « Copier la dernière transcription » depuis le tray.
- 📂 **Import de fichiers audio** (WAV / MP3 / M4A / FLAC…) — transcrit, copié, archivé (décodage PyAV, sans ffmpeg).
- 🤖 **Mode IA local** — reponctuation / correction via un LLM **sur la machine** (Ollama, LM Studio…). Désactivé par défaut.
- 🎯 **Profils de contexte** — prompt, langue et dictionnaire s'adaptent à l'application active (code, mail…).
- 🔊 **Transcription live d'une sortie audio** — suivez une confcall Teams/Meet en continu (loopback WASAPI).
- 🧑‍🤝‍🧑 **Mode réunion** — capture micro **+** sortie système, export horodaté avec distinction par locuteur (`Moi` / `Interlocuteurs`).
- 💬 **Assistant de réunion** — propose des réponses LLM locales quand on vous pose une question.

## Démarrage rapide

> **Prérequis** : Windows 10/11 64 bits, **Python 3.10+** (testé jusqu'à 3.14.3 — toutes les
> roues binaires, dont `ctranslate2` et PyAV, sont disponibles, aucune compilation requise),
> et un micro autorisé dans *Paramètres > Confidentialité et sécurité > Microphone*.
> `ffmpeg` n'est **pas** nécessaire.

**1. Installer**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**2. Télécharger le modèle (une seule fois)**

Comme `local_files_only` est à `true` par défaut (zéro réseau), il faut récupérer le modèle
explicitement la première fois :

```powershell
# Pré-télécharge le modèle défini dans config.yaml (medium par défaut) :
python -c "from faster_whisper import WhisperModel; WhisperModel('medium')"
```

> Alternative : passez temporairement `local_files_only: false` dans `config.yaml`, lancez
> l'app une fois (le modèle se télécharge), puis remettez `true`. Ensuite, plus aucune
> requête réseau (`HF_HUB_OFFLINE` est forcé quand `local_files_only` est vrai).

**3. Lancer**

```powershell
python -m whisperty                       # icône tray + raccourci global
python -m whisperty --config mon.yaml     # configuration personnalisée
```

**4. Dicter**

Appuyez sur **Ctrl+Alt+Espace**, parlez, ré-appuyez (ou faites une pause) : le texte
s'insère dans la fenêtre active. Clic droit sur l'icône tray pour la config ou pour quitter.

> 💡 Pour tester uniquement la capture micro, sans modèle : `python -m whisperty.recorder`

## Configuration

Tout se règle dans un seul fichier **`config.yaml`** (à côté de l'exécutable). Relancez
l'application après modification.

| Section | Clés notables |
|---------|---------------|
| `audio` | `device`, `samplerate`, `vad_threshold`, `silence_duration`, `max_duration` |
| `transcription` | `model`, `language`, `device` (cpu/cuda), `compute_type`, `local_files_only`, `initial_prompt` |
| `hotkey` | `mode` (toggle/push_to_talk), `combo`, `double_tap_key` |
| `output` | `method` (paste/type), `restore_clipboard`, `type_delay` |
| `dictionary` | `enabled`, `path` (→ `dictionary.txt`) |
| `logging` | `level`, `path` |
| `history` | `enabled`, `path` (→ `whisperty.db`), `max_entries` |
| `ai` | `enabled`, `endpoint` (**local uniquement**), `model`, `prompt`, `timeout` |
| `profiles` | `enabled`, `definitions` (`name`, `match`, `initial_prompt`, `language`, `hotwords`, `corrections`, `dictionary`) |
| `live` | `device`, `block_duration`, `max_segment`, `silence_duration`, `vad_threshold`, `transcript_dir` |
| `conference` | `enabled`, `system_device`, `mic_device`, `distinguish_speakers`, `mic_label`, `system_label`, `export_dir`, `export_format` |
| `meeting` | `user_name`, `user_context`, `auto_inject`, `context_segments`, `reply_prompt` |

> Le fichier `config.yaml` livré est abondamment commenté : c'est la référence la plus à jour.

## Fonctions avancées

### 📜 Historique local
Chaque transcription est archivée dans `whisperty.db` (SQLite, à côté de `config.yaml`).
Le menu tray propose « Copier la dernière transcription » et « Ouvrir le dossier de
l'historique ». Désactivable via `history.enabled: false`.

### 📂 Import de fichiers audio
Menu tray → **« Importer un fichier audio… »**. Le fichier est transcrit localement, le
texte est **copié dans le presse-papiers** (et archivé) — collez-le où vous voulez.

### 🤖 Mode IA local
`ai.enabled: true` active un post-traitement (ponctuation, casse, fautes) par un LLM tournant
sur votre machine. Exemple avec [Ollama](https://ollama.com) :

```powershell
ollama run llama3.2
# puis dans config.yaml :
#   ai.endpoint: http://localhost:11434/v1/chat/completions
```

**Tout endpoint non-local est refusé** : le texte dicté ne quitte jamais la machine. En cas
d'échec du LLM, le texte brut est conservé (jamais bloquant).

### 🎯 Profils de contexte
Avec `profiles.enabled: true`, le profil correspondant à l'application au premier plan
(au démarrage de la dictée) surcharge l'`initial_prompt`, la langue et le dictionnaire —
par exemple un profil « code » dans VS Code, « mail » dans Outlook. Voir `profiles.definitions`.

### 🔊 Transcription live d'une sortie audio
Menu tray → **« Transcription live (sortie audio) »** → choisissez la sortie à capturer.
Whisperty capture en **loopback** ce qui *sort* de ce périphérique (ex. l'audio d'une confcall
Teams) et transcrit en continu dans `transcriptions/live_<horodatage>.txt`. À l'arrêt, le texte
complet est copié et archivé. Icône tray **bleue** pendant la capture.

> Nécessite `soundcard` (`pip install soundcard`) — `sounddevice`/PortAudio n'expose pas le
> loopback WASAPI. 100 % local, aucun réseau.

### 🧑‍🤝‍🧑 Mode réunion
Capture **simultanément le micro (votre voix) ET une sortie système** (les interlocuteurs
distants) pour transcrire une réunion complète, exportée en `.txt`/`.md` horodaté + historisée.
Par défaut (`conference.distinguish_speakers: true`), distinction **par source** :

```
[00:12] Moi : Est-ce qu'on valide le planning ?
[00:18] Interlocuteurs : Oui, on cale ça pour vendredi.
```

Entrelacé chronologiquement, déterministe, 100 % local (sans modèle de diarisation). Réglable
en transcription mixée avec `distinguish_speakers: false`.

> ⚖️ Pensez au **consentement** des participants avant d'enregistrer une réunion.

### 💬 Assistant de réunion
Écoute la sortie audio et, quand une question vous est posée, propose une réponse rédigée par
le LLM local. Nécessite `ai.enabled: true`. Par défaut, la réponse est copiée dans le
presse-papiers (`meeting.auto_inject: false`) ; renseignez `meeting.user_name` et
`meeting.user_context` pour des suggestions pertinentes.

## Dictionnaire personnalisé

Éditez **`dictionary.txt`**, une entrée par ligne :

```
terme                 # mot favorisé par la reconnaissance (hotword)
mauvais => correct    # correction appliquée après transcription
```

## Tests

```powershell
python tests/test_logic.py   # logique pure : config, dictionnaire, injection, historique,
                             # profils, garde IA locale, overrides — sans micro ni modèle
```

## Packaging & démarrage automatique

Construire un exécutable autonome :

```powershell
pip install pyinstaller
pyinstaller whisperty.spec    # produit dist\whisperty.exe (onefile)
```

> ⚠️ `config.yaml` et `dictionary.txt` ne sont **pas** embarqués dans l'exe (volontaire : ils
> restent éditables). Copiez-les **à côté de `dist\whisperty.exe`**, sinon l'app tourne sur ses
> réglages par défaut. Le modèle Whisper doit aussi être déjà en cache (cf. *Démarrage rapide*).

Lancer Whisperty au démarrage de Windows (par utilisateur, sans droits admin) :

```powershell
.\scripts\install_autostart.ps1     # activer
.\scripts\uninstall_autostart.ps1   # désactiver
```

## Accélération GPU NVIDIA

```powershell
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Puis dans `config.yaml` : `device: cuda` et `compute_type: float16`.

> CTranslate2 ne supporte que **CPU et CUDA**. Les GPU **AMD / Intel** restent en CPU
> (pas de DirectML).

## Architecture

Pipeline : raccourci → `recorder` → `transcriber` → post-traitement dictionnaire → `injector`,
état reflété par le `tray`, le tout orchestré par `app.py`.

| Module (`whisperty/`) | Rôle |
|------------------------|------|
| `recorder.py` | Capture micro non bloquante (sounddevice), RMS pour VAD/tray |
| `transcriber.py` | Wrapper faster-whisper (modèle configurable, hotwords, garde hors-ligne) |
| `injector.py` | Injection texte (collage Ctrl+V par défaut, frappe en repli) |
| `tray.py` | Icône zone de notification (pystray) |
| `app.py` | Orchestration / machine à états + raccourci global + surveillance VAD |
| `config.py` · `dictionary.py` | Chargement de `config.yaml` / du dictionnaire |
| `history.py` | Historique des transcriptions (SQLite local, thread-safe) |
| `ai.py` | Raffinage texte par LLM **local** (garde localhost) |
| `profiles.py` · `winutil.py` | Profils par application + détection de l'app active (Win32) |
| `loopback.py` · `live.py` | Capture loopback (soundcard/WASAPI) + transcription live |
| `conference.py` · `meeting.py` | Mode réunion + assistant de réunion |

Détails de conception et règles de concurrence : voir [`CLAUDE.md`](CLAUDE.md).

## Feuille de route

- [x] Capture audio
- [x] Transcription Whisper locale
- [x] Injection de texte system-wide
- [x] Raccourci global + icône tray + configuration YAML
- [x] Packaging (exe) + démarrage automatique
- [x] **V2** — import audio, IA locale, historique SQLite, profils, transcription live, mode réunion, assistant
