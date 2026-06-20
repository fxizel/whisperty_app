# Whisperty

Dictée vocale **100 % locale** pour Windows 10/11 — une alternative à Superwhisper
basée sur OpenAI Whisper (via `faster-whisper`). L'audio est capté au raccourci clavier,
transcrit localement, puis injecté dans l'application active (VS Code, Outlook, Teams,
navigateur…) **sans qu'aucune donnée ne quitte la machine**.

## Confidentialité

- Aucun appel réseau à l'usage, aucune télémétrie. Journalisation strictement locale.
- Seule exception : le **premier** téléchargement du modèle Whisper. Activez ensuite
  `transcription.local_files_only: true` dans `config.yaml` pour un fonctionnement
  100 % hors-ligne (vérifiable à Wireshark).

## Fonctionnalités

- 🎙️ Enregistrement au **raccourci global** (toggle, push-to-talk ou double-appui).
- 🧠 Transcription locale Whisper, **modèle configurable** (base/small/medium/large-v3), CPU ou CUDA.
- ⌨️ **Injection system-wide** dans l'app active (collage Ctrl+V robuste pour le français, ou frappe).
- 📖 **Dictionnaire personnalisé** : termes métier favorisés + corrections automatiques.
- 🔔 **Icône system tray** avec statut (gris = prêt, rouge = enregistrement, orange = transcription).
- ⚙️ Configuration **YAML** unique (`config.yaml`).

### Nouveautés V2

- 📜 **Historique local** des dictées (SQLite) — purge automatique, « Copier la dernière transcription » depuis le tray.
- 📂 **Import de fichiers audio** (WAV/MP3/M4A/FLAC…) via le menu tray ; le texte est copié dans le presse-papiers et archivé (décodage PyAV embarqué, sans ffmpeg).
- 🤖 **Mode IA local optionnel** — reponctuation/correction via un LLM **sur la machine** (Ollama, LM Studio…). Désactivé par défaut ; **seuls les endpoints locaux sont acceptés** (la confidentialité interdit tout envoi distant).
- 🎯 **Profils de contexte** — l'`initial_prompt`, la langue et le dictionnaire s'adaptent à l'application active (profil « code » dans VS Code, « mail » dans Outlook…).

## Prérequis système (Windows)

- **Python 3.10+** (64 bits) — **testé avec 3.14.3** : toutes les roues binaires, dont
  `ctranslate2` 4.8.0 et PyAV, sont disponibles (aucune compilation requise).
- Un micro autorisé : *Paramètres > Confidentialité et sécurité > Microphone*.
- `ffmpeg` : **non requis** (faster-whisper embarque PyAV).
- GPU NVIDIA *optionnel* : CUDA 12 + cuDNN 9. GPU AMD / Intel : CPU uniquement (CTranslate2
  ne supporte pas DirectML).

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Premier lancement — télécharger le modèle (une seule fois)

Par défaut, `transcription.local_files_only: true` → **zéro réseau**. Le modèle doit
donc être téléchargé une première fois explicitement :

```powershell
# Option A : passez temporairement local_files_only à false dans config.yaml, lancez
#            l'app une fois (le modèle se télécharge), puis remettez-le à true.
# Option B : pré-téléchargez sans lancer l'app :
python -c "from faster_whisper import WhisperModel; WhisperModel('small')"
```

Ensuite, l'application n'émet plus aucune requête réseau (`HF_HUB_OFFLINE` est forcé
quand `local_files_only` est vrai).

## Utilisation

```powershell
python -m whisperty               # lance l'app (icône tray + raccourci global)
python -m whisperty --config mon_config.yaml
```

Par défaut : **Ctrl+Alt+Espace** démarre/arrête la dictée (mode `toggle`). Parlez, ré-appuyez :
le texte transcrit s'insère dans la fenêtre active. Clic droit sur l'icône tray pour ouvrir
la config ou quitter.

Tester uniquement la **capture audio** (Étape 1, sans modèle) :

```powershell
python -m whisperty.recorder
```

## Configuration (`config.yaml`)

| Section | Clés notables |
|---------|---------------|
| `audio` | `device`, `samplerate`, `vad_threshold`, `silence_duration` |
| `transcription` | `model`, `language`, `device` (cpu/cuda), `compute_type`, `local_files_only`, `initial_prompt` |
| `hotkey` | `mode` (toggle/push_to_talk), `combo`, `double_tap_key` |
| `output` | `method` (paste/type), `restore_clipboard` |
| `dictionary` | `enabled`, `path` (→ `dictionary.txt`) |
| `logging` | `level`, `path` |
| `history` | `enabled`, `path` (→ `whisperty.db`), `max_entries` |
| `ai` | `enabled`, `endpoint` (local uniquement), `model`, `prompt`, `timeout` |
| `profiles` | `enabled`, `definitions` (`name`, `match`, `initial_prompt`, `language`, `hotwords`, `corrections`, `dictionary`) |

## Dictionnaire (`dictionary.txt`)

- `terme` → mot favorisé par la reconnaissance (hotwords) ;
- `mauvais => correct` → correction appliquée après transcription.

## Fonctions V2

- **Historique** : chaque dictée est archivée dans `whisperty.db` (SQLite, à côté de
  `config.yaml`). Le menu tray propose « Copier la dernière transcription » et « Ouvrir le
  dossier de l'historique ». Désactivable (`history.enabled: false`).
- **Import audio** : menu tray → « Importer un fichier audio… ». Le fichier est transcrit
  localement, le texte est **copié dans le presse-papiers** (et archivé) — collez-le où vous voulez.
- **Mode IA local** (`ai.enabled: true`) : post-traitement par un LLM tournant sur la machine.
  Démarrez par exemple [Ollama](https://ollama.com) (`ollama run llama3.2`) puis pointez
  `ai.endpoint` vers `http://localhost:11434/v1/chat/completions`. **Tout endpoint non-local est
  refusé** : le texte dicté ne quitte jamais la machine. En cas d'échec, le texte brut est conservé.
- **Profils de contexte** (`profiles.enabled: true`) : selon l'application au premier plan au
  démarrage de la dictée, un profil surcharge l'`initial_prompt`, la langue et le dictionnaire
  (cf. `profiles.definitions` dans `config.yaml`).

## Tests

```powershell
python tests/test_logic.py        # logique pure (config, dictionnaire, injection, historique,
                                  # profils, garde IA locale, overrides) — sans micro ni modèle
```

## Packaging (Étape 5)

```powershell
pip install pyinstaller
pyinstaller whisperty.spec         # produit dist\whisperty.exe (onefile)
```

> **Important** : `config.yaml` et `dictionary.txt` ne sont **pas** embarqués dans l'exe
> (volontaire : ils restent éditables). Copiez-les **à côté de `dist\whisperty.exe`** ;
> sinon l'app tourne sur ses réglages par défaut. Le modèle Whisper doit aussi être déjà
> en cache (cf. « Premier lancement »).

Démarrage automatique avec Windows (par utilisateur, sans droits admin) :

```powershell
.\scripts\install_autostart.ps1    # activer
.\scripts\uninstall_autostart.ps1  # désactiver
```

## Accélération GPU NVIDIA (optionnel)

```powershell
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Puis dans `config.yaml` : `device: cuda`, `compute_type: float16`.

## Architecture

Voir [`CLAUDE.md`](CLAUDE.md). Pipeline : raccourci → `recorder` → `transcriber` →
post-traitement dictionnaire → `injector`, état reflété par le `tray`, le tout orchestré
par `app.py`.

## Feuille de route

- [x] **Étape 1** — Capture audio (`whisperty/recorder.py`)
- [x] **Étape 2** — Transcription (`whisperty/transcriber.py`)
- [x] **Étape 3** — Injection de texte (`whisperty/injector.py`)
- [x] **Étape 4** — Raccourci global + system tray (`whisperty/app.py`, `tray.py`, `config.py`)
- [x] **Étape 5** — Packaging (`whisperty.spec`, `scripts/`)
- [x] **V2** — Import de fichiers audio, mode IA (LLM local), historique SQLite, profils de contexte
