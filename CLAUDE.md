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

# Lancer l'application (fenêtre WebView2 + icône tray + raccourci global)
python -m whisperty
# Mode zone de notification seule (sans fenêtre ; = gui.enabled: false)
python -m whisperty --no-gui

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
| `conference.py` | Mode réunion : micro + sortie système simultanés, mixés (itération 1) | fait (V2) |
| `meeting.py` | Assistant de réunion : loopback + détection questions + réponses LLM locales | fait (V2) |
| `gui.py` | Fenêtre native (WebView2 via pywebview) : pont Python↔JS (`GuiApi`) vers la machine à états, la config et l'historique | fait (V2) |
| `configio.py` | Écriture **chirurgicale** de `config.yaml` (préserve commentaires/ordre, sans ruamel) | fait (V2) |
| `web/` | Assets de l'UI (`index.html`, `styles.css`, `app.js`) — rendu fidèle de la maquette, **police système** (pas de Google Fonts) | fait (V2) |

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
- **Réunion (V2)** : même règles que live (`stop_meeting()` sans verrou ni join) ;
  l'analyse LLM (détection + réponse) tourne dans des threads workers dédiés par segment
  suspect, sans bloquer la capture loopback.
- **Interface fenêtre (V2)** : `webview.start()` exige le **thread principal** ; le tray tourne
  donc **détaché** (`Tray.run_detached()`) et `launch_gui()` bloque le thread principal. Les
  méthodes de `GuiApi` (pont) et les actions tray s'exécutent sur d'AUTRES threads et délèguent à
  `WhispertyApp` (déjà sérialisé par `_lock`). Le contrôle de la fenêtre (`minimize`/`hide`/`show`/
  `destroy`) est appelé cross-thread — **vérifié OK** sur le backend edgechromium (NE PAS lire de
  *propriétés* WebView2 ni appeler `evaluate_js` depuis un thread non-UI : cela lève `E_NOINTERFACE`).
  `quit()` met `_quitting=True` PUIS `window.destroy()` (débloque `start()` ; `on_closing` autorise
  alors la fermeture, sinon il masque dans le tray). `_quit_event` débloque le thread principal si la
  fenêtre n'a pas pu démarrer après un tray déjà détaché.

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
- **Réunion (V2, `conference.py`)** : mode exclusif `TrayState.CONFERENCE` capturant SIMULTANÉMENT
  le micro (`AudioRecorder` en mode streaming `frame_callback` → RAM bornée + `_resample` 16 kHz) et
  une sortie système (`loopback`/`soundcard`, COM via `com_initialized()` sur son thread). Itération 1 :
  les deux sources alimentent des tampons thread-safe, un mixeur draine une longueur **alignée**
  (min des sources actives), **mixe** (somme + normalisation anti-saturation `mix_streams`), segmente
  (réutilise `_Segmenter`) et transcrit → UNE transcription, sans étiquette. NE PAS injecter : export
  `.txt`/`.md` horodaté + historique `source="réunion"`. Échec d'une source → l'autre seule. Arrêt
  comme live (callback de fin, pas de `join()` sous `_lock`). Robustesse : calage temporel des deux
  flux au démarrage, retrait d'une source morte en cours (sinon le mixage aligné gèlerait), reliquats
  drainés à l'arrêt.
- **Réunion — distinction par locuteur (V2, itération 2)** : `conference.distinguish_speakers: true`
  (défaut recommandé) → PAS de mixage ; chaque source a son propre `_Segmenter`, est transcrite via
  `transcriber.transcribe_segments()` (variante horodatée conservant `segment.start/end`), horodatée
  par position audio (échantillons poussés), puis les segments sont **entrelacés chronologiquement**
  (tri à l'arrêt + réécriture triée du transcript) : `[MM:SS] Moi : …` / `[MM:SS] Interlocuteurs : …`.
  Distinction PAR SOURCE uniquement (micro = `mic_label`, sortie = `system_label`) — déterministe,
  100 % local. La diarisation des interlocuteurs individuels (pyannote = PyTorch + modèles *gated* HF)
  est **écartée** (tension zéro-réseau) ; à n'envisager qu'en option hors-ligne désactivée par défaut.
- **Interface fenêtre (V2, `gui.py` + `web/`)** : la maquette HTML est rendue par **Edge WebView2**
  via `pywebview` (préinstallé sur Win10/11). `pywebview` est une **dépendance optionnelle** : absente
  (ou WebView2 indisponible), l'app retombe sur le **mode tray seul** historique (`gui.enabled: false`
  ou `--no-gui`). Le pont JS appelle `window.pywebview.api.*` (méthodes de `GuiApi`) ; `app.js` interroge
  l'état par *polling* (`poll()` ~5×/s) — pas de push Python→JS (évite `evaluate_js` cross-thread).
  ⚠️ **Zéro-réseau dans l'UI** : la maquette chargeait Google Fonts — **retiré** (police système Segoe UI).
  AUCUN asset/CDN/fetch distant ne doit être introduit dans `web/`. `app.js` a une couche de données qui
  préfère le pont et retombe sur des **données factices** (sert d'aperçu autonome sans backend).
  ⚠️ **`GuiApi` : références natives PRIVÉES** — pywebview introspecte l'objet `js_api` (`dir()`+`getattr`,
  `webview/util.py:get_functions`) et **récurse dans tout attribut public non-callable**. La fenêtre et l'app
  DOIVENT donc être `self._window`/`self._app` (préfixe `_`) : exposées en public, pywebview parcourrait le
  graphe natif Window→WinForms→WebView2 hors thread UI → tempête `E_NOINTERFACE` + récursion infinie sur
  `Rectangle.Empty`, au DÉMARRAGE. Déplacement de la fenêtre : `win_move` (→ `SetWindowPos`, thread-safe) avec
  un décalage calculé en JS (`screenX − clientX`) — NE JAMAIS lire `window.x/.y` (→ `Control.Left/Top` hors
  thread UI = plantage).
- **Écriture de config (V2, `configio.py`)** : l'écran Configuration enregistre via `update_yaml_file`
  (édition **ligne par ligne** préservant commentaires/ordre) — PAS `yaml.safe_dump` (détruirait les
  commentaires) ni `ruamel` (dépendance évitée). `apply_config_from_gui` mute les dataclasses en place
  (les sous-systèmes partagent ces objets), réécrit le fichier, puis applique à chaud : reset du modèle
  (taille/device/`local_files_only`), `reload_hotkey()`, reconstruction injecteur/LLM. La **langue** est
  lue à chaque transcription → pas de rechargement de modèle.
- **Injection FR** : privilégier le collage presse-papiers (Ctrl+V) à la frappe caractère par
  caractère — bien plus fiable pour les accents (é, è, à, ç) et les longs textes.
- **Raccourci** : ne pas utiliser `Win+Space` (réservé par Windows). Défaut configurable.
- **ffmpeg** : non requis (PyAV est embarqué par faster-whisper) — ne pas l'ajouter en dépendance.
- **Nommage** : module `injector` et non `typer` (la lib PyPI `typer` est tirée transitivement
  par huggingface-hub — un module `typer.py` la masquerait).
- **Packaging** : `config.yaml`/`dictionary.txt` ne sont PAS embarqués dans l'exe (éditables) ;
  ils doivent être déposés à côté de `whisperty.exe`. `upx=False` (UPX corrompt les DLL natives).
  Les **assets `whisperty/web/`** (UI), eux, DOIVENT être embarqués (`--add-data "whisperty/web;whisperty/web"`) :
  `gui.web_dir()` résout le dossier en source comme en build figé (`sys._MEIPASS`).

## Conventions

- Code commenté **en français**, docstrings, type hints (`from __future__ import annotations`).
- Gestion d'erreurs robuste et explicite : micro absent, modèle non téléchargé, droits insuffisants.
