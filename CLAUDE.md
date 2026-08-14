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
| `transcriber.py` | Wrapper faster-whisper (modèle configurable, hotwords, garde hors-ligne, DLL CUDA + repli CPU) | fait |
| `cuda.py` | Détection GPU/composants CUDA + installation **opt-in** des wheels nvidia (cuBLAS/cuDNN) | fait |
| `injector.py` | Injection texte (collage Ctrl+V par défaut, frappe en repli) | fait |
| `feedback.py` | Retour sonore local de la dictée (winsound, opt-out `audio.sound_feedback`, aucun verrou) | fait |
| `punctuation.py` | Commandes de ponctuation dictées (« point », « à la ligne »… ; opt-in `punctuation.enabled`, DICTÉE seulement — jamais live/réunion/import) | fait |
| `report.py` | Compte rendu Markdown depuis un gabarit éditable (opt-in `summary.template`, rendu dans le worker de résumé, never-fail) | fait |
| `tray.py` | Icône zone de notification (pystray) | fait |
| `app.py` | Orchestration / machine à états (RLock) + raccourci global + surveillance VAD + V2 (import audio, historique, IA, profils) | fait |
| `config.py` | Chargement de `config.yaml` | fait |
| `dictionary.py` | Chargement dictionnaire + corrections + édition assistée UC-19 (`parse_entries`/`update_dictionary_file`, préserve commentaires/ordre) | fait |
| `history.py` | Historique des transcriptions (SQLite local, thread-safe, schéma versionné `user_version`, payload de session pour le renommage post-session FR-31) | fait (V2) |
| `ai.py` | Raffinage texte par LLM **local** (garde localhost, désactivé par défaut) | fait (V2) |
| `profiles.py` | Profils de contexte par application (override prompt/langue/dico) | fait (V2) |
| `winutil.py` | Détection de l'application active (ctypes Win32, local) | fait (V2) |
| `loopback.py` | Capture loopback d'une sortie audio (soundcard/WASAPI, local) | fait (V2) |
| `live.py` | Transcription live continue d'une sortie (segmenteur VAD + sink) | fait (V2) |
| `conference.py` | Mode réunion : micro + sortie système simultanés (mixage itér. 1 / distinction source itér. 2 / diarisation locuteur itér. 3, UC-18) | fait (V2) |
| `diarization.py` | Diarisation des locuteurs (UC-18) : empreinte MFCC **pur NumPy** (défaut, zéro modèle/réseau) ou modèle **ONNX local** (CO-19, opt-in, CPU seul) + clustering en ligne, 100 % local | fait (V2) |
| `gui.py` | Fenêtre native (WebView2 via pywebview) : pont Python↔JS (`GuiApi`) vers la machine à états, la config et l'historique | fait (V2) |
| `configio.py` | Écriture **chirurgicale** de `config.yaml` (préserve commentaires/ordre, sans ruamel) | fait (V2) |
| `modeldl.py` | Téléchargement **opt-in** du modèle Whisper depuis l'UI (bannière dashboard ; doctrine de `cuda.py`) | fait (V2) |
| `singleinstance.py` | Instance unique (mutex + évènement nommés Win32) : relancer l'exe réaffiche la fenêtre ; no-op hors Windows | fait (V2) |
| `version.py` | Numéro de version unique (fenêtre, exe, installeur) | fait (V2) |
| `web/` | Assets de l'UI (`index.html`, `styles.css`, `app.js`) — rendu fidèle de la maquette, **police système** (pas de Google Fonts) | fait (V2) |

## Concurrence (à préserver)

- Transitions d'état sérialisées par `WhispertyApp._lock` (RLock). Verrou interne
  `AudioRecorder._op_lock` pour `start()`/`stop()`. **Ordre imposé : `_lock` → `_op_lock`,
  jamais l'inverse** ; le callback PortAudio (`_callback`) ne prend aucun verrou.
- **Flux live affiché (V2)** : `WhispertyApp._live_lock` protège l'accumulateur du flux
  live/réunion (`_live_lines` + `_live_rev`). C'est un **verrou feuille** : jamais imbriqué
  avec `_lock` (`poll()` relâche `_lock` avant de lire `live_rev()`). ⚠️ **Ni avec
  `_note_lock`** : le callback `on_segment` prend bien un second verrou feuille depuis US-12
  (`_reemit_conference_lines()` → `conference.render_snapshot()`), mais **séquentiellement** —
  le rendu se fait HORS `_live_lock`, la publication sous `_live_lock` seul. Corollaire à ne
  pas casser : `conference._write_line` **doit** appeler `_on_segment` après avoir relâché
  `_note_lock` (verrou NON réentrant — rendre l'écriture fichier et la notification atomiques
  figerait le worker de réunion dans `render_snapshot()`). Conséquence directe :
  `conference.segments_rev()` ne prend **aucun** verrou, puisqu'il est lu sous `_live_lock`.
- **Renommage/notes en session vs flux affiché (V2, US-12)** : `rename_speaker` et `add_note`
  (thread du pont) touchent le flux pendant que le worker y ajoute des segments. Toute
  publication d'un rendu complet est donc une **CAS à trois termes**, refusée si l'un a bougé
  entre l'instantané et la publication : `_live_repair` et `_live_rev` (l'AFFICHAGE a changé,
  publier écraserait plus frais que soi) et `segments_rev` (la SOURCE a changé, le rendu est
  déjà périmé — publier effacerait de la tuile un segment arrivé pendant le rendu, que son
  propre worker croirait ensuite « déjà republié »). Compteurs sous `_live_lock` :
  `_live_repair` **arme** la resynchronisation (bumpé par un renommage et par une note ; le
  `_on_conference_segment` suivant repart du rendu complet au lieu d'ajouter, le segment y
  figurant déjà puisque `_segments` est alimenté AVANT le callback) et n'est **désarmé** que
  par une réparation publiée ; `_live_render` compte les rendus complets publiés (monotone,
  jamais remis à zéro) et conditionne l'ajout d'une ligne (`_append_live_line(expect_render=…)`)
  — un rendu publié depuis contient déjà ce segment, l'ajouter ferait doublon. Côté
  transcriber, `_segments_rev` est incrémenté **avant** l'insertion : `segments_rev()` étant
  lu sans verrou, il doit pouvoir annoncer un segment trop tôt (réessai bénin) mais jamais
  trop tard. En réunion, une note n'est donc PAS ajoutée à la main : elle est déjà dans
  `_segments`, le flux est republié (elle apparaît à sa place chronologique, sans risque de
  doublon). Invariant : **aucun segment n'est jamais omis ni dupliqué durablement** ; une
  incohérence résiduelle d'ordre laisse le compteur armé et disparaît au segment suivant, puis
  à l'arrêt quand la tuile bascule sur le texte final d'historique. `rename_speaker` exige
  l'état `CONFERENCE` (le diariseur n'est pas remis à zéro à l'arrêt : sans cette garde, un
  clic tardif republierait la réunion précédente, voire écraserait un live qui démarre ; le
  renommage à froid passe par `rename_history_speaker`). Fichier et historique ne sont jamais
  concernés (rendus depuis les clés à l'arrêt).
- `_stop_and_process()` relâche `_lock` avant l'arrêt bloquant de PortAudio. À l'inverse,
  `_start_recording()` tient `_lock` pendant `recorder.start()` **à dessein** (évite un flux
  orphelin si un stop concurrent survient pendant l'ouverture du périphérique).
- **Live (V2)** : `stop_live()` ne tient PAS `_lock` et ne joint PAS le thread live ; c'est
  `LiveTranscriber._finish` → `_on_live_finished` (qui reprend `_lock`) qui repasse à IDLE.
  Tenir le verrou pendant un `join()` provoquerait un interblocage avec ce callback.
- **Live — capture ≠ transcription (V2)** : dans `live.py`, le thread de capture lit le
  loopback en **continu** (`record_fn` + segmentation, triviale) et empile les segments dans une
  `queue.Queue` ; un **thread worker** (`_transcribe_loop`) les transcrit en parallèle. NE JAMAIS
  transcrire dans le thread de capture : le tampon interne WASAPI de `soundcard` est borné, et toute
  pause (le temps de transcrire un segment, plusieurs s en CPU) le ferait déborder → **perte d'audio**
  pendant le traitement. Arrêt : la sentinelle `None` est mise en file APRÈS le dernier segment ;
  `_consume` joint le worker avant de rendre la main (donc avant `_close_transcript`/`_finish`), si
  bien que `_segments`/`_file` ne sont touchés que par le worker (lus par `_finish` après le join).
  La file est non bornée (latence en cas de retard, jamais de coupure). NB : `conference.py` n'a
  jamais eu ce défaut — ses sources capturent dans des threads séparés vers des tampons mémoire non
  bornés (`_StreamBuffer`).
- **Réunion (V2)** : mêmes règles que live — `stop_conference()` ne tient pas `_lock` et ne
  joint pas les threads de capture ; c'est le callback de fin (`_on_conference_finished`) qui
  reprend `_lock` et repasse à IDLE.
- **Notes en session (V2, UC-16)** : `LiveTranscriber._note_lock` et
  `ConferenceTranscriber._note_lock` sont des **verrous feuilles** (jamais imbriqués avec un
  autre verrou) protégeant `_segments`/`_notes`/`_file` — les notes arrivent du pont GUI
  (`GuiApi.add_note`) ou du raccourci signet, PAS du worker. `WhispertyApp.add_note` lit
  l'état sous `_lock`, puis appelle `add_note` du transcriber **hors** verrou. JAMAIS de
  traitement de note dans les threads de capture (RE-11) ; l'affichage passe par le flux
  existant (`_append_live_line` → `_live_rev`), pas de payload ajouté au polling.
- **Notices utilisateur (V2)** : `WhispertyApp._notify_user` publie {rev, text, kind} sous
  `_notice_lock` — **verrou feuille**, même modèle que `_live_lock` (jamais imbriqué ; les
  appelants le prennent hors de `_lock` ou après l'avoir relâché). Le JS ne récupère
  `get_notice` que quand `poll().noticeRev` change (polling, payload minimal — pas de push).
  Toute erreur qui change le comportement PERÇU (micro, modèle, échec de dictée/import) DOIT
  passer par `_notify_user` (toast + notification tray), pas seulement par les logs.
  `_model_error` (même verrou) mémorise le dernier échec de chargement du modèle et pilote la
  bannière de téléchargement du dashboard (`poll().modelOk`).
- **Interface fenêtre (V2)** : `webview.start()` exige le **thread principal** ; le tray tourne
  donc **détaché** (`Tray.run_detached()`) et `launch_gui()` bloque le thread principal. Les
  méthodes de `GuiApi` (pont) et les actions tray s'exécutent sur d'AUTRES threads et délèguent à
  `WhispertyApp` (déjà sérialisé par `_lock`). Le contrôle de la fenêtre (`minimize`/`hide`/`show`/
  `destroy`) est appelé cross-thread — **vérifié OK** sur le backend edgechromium (NE PAS lire de
  *propriétés* WebView2 ni appeler `evaluate_js` depuis un thread non-UI : cela lève `E_NOINTERFACE`).
  `quit()` met `_quitting=True` PUIS `window.destroy()` (débloque `start()` ; `on_closing` autorise
  alors la fermeture, sinon il masque dans le tray). `_quit_event` débloque le thread principal si la
  fenêtre n'a pas pu démarrer après un tray déjà détaché.
- **`toggle()` dispatch HORS verrou** : `_lock` est RÉENTRANT (RLock) — appeler
  `_start_recording`/`_stop_and_process` depuis le bloc verrouillé de `toggle()` les exécuterait
  verrou tenu malgré leurs `with` internes (relâchement avant `recorder.stop()` neutralisé,
  notification micro sous verrou). `toggle()` lit donc l'état sous `_lock` puis agit hors verrou ;
  les deux méthodes re-vérifient l'état sous `_lock` (entrelacement = no-op bénin). Même doctrine
  pour les notices : `_start_recording` renvoie son message micro après le bloc, `start_live`/
  `start_conference` notifient via un drapeau `busy` hors verrou.
- **Sessions archivées (V2, FR-31/UC-17)** : `WhispertyApp._archive_lock` sérialise les
  read-modify-write sur une session archivée (entrée d'historique + fichier transcript) —
  la séquence get→re-rendu→`update_text`→réécriture fichier de `rename_history_speaker`
  (un thread pywebview PAR appel du pont : deux renommages rapprochés se perdraient sinon
  mutuellement, avec un `.tmp` partagé) ET l'ajout du résumé au fichier par
  `_summarize_session` (E/S fichier SEULEMENT, jamais autour de l'appel LLM — sinon la
  réécriture pourrait effacer un résumé ajouté entre sa lecture et son `os.replace`).
  Ordre : `_archive_lock` → `History._lock` (feuille), JAMAIS l'inverse ; jamais imbriqué
  avec `_lock` ; `_notify_user` appelé hors de ce verrou.
- **Verrous utilitaires (V2)** : `configio._WRITE_LOCK` sérialise les read-modify-write de
  `config.yaml` (écran Configuration + fin de téléchargement du modèle) — verrou **feuille**.
  `WhispertyApp._bench_lock` (état du bench local, publié par le worker, lu par
  `GuiApi.bench_status` en polling) est un verrou **feuille**, même modèle que `_notice_lock`.
  `modeldl._embedding_downloader._lock` (modèle de diarisation, CO-19) suit le même modèle
  que celui du modèle Whisper : feuille effective, avec l'arête inter-modules
  `Transcriber._load_lock` → `_embedding_downloader._lock` (via `download_running()`), JAMAIS
  l'inverse. ⚠️ `_DownloadState._lock` est un `Lock` NON réentrant : ne JAMAIS appeler
  `_set_offline_env` (qui consulte `download_running()`) en le tenant. Threads dédiés :
  `model-download` et `diar-model-download` (téléchargements), tous deux exécutant leur
  `on_success` HORS verrou (d'où `configio._WRITE_LOCK` puis `_notice_lock` ensuite).
- **Diarisation ONNX — session et caches (V2, CO-19)** : la session onnxruntime est créée
  sur le thread qui démarre la réunion (`Diarizer.__init__` via `ConferenceTranscriber.start`,
  aucun verrou tenu — l'import à froid peut coûter ~1 s) puis utilisée **exclusivement** par
  le worker `_diar_loop` ; une session par session de réunion, donc jamais partagée entre
  threads (pas de COM par thread à gérer, contrairement à `soundcard`). Les caches de bancs
  de filtres de `diarization.py` (`_FILTERBANK_CACHE`, `_DCT_CACHE`, `_KALDI_FB_CACHE`) sont
  écrits **sans verrou** : publication par affectation d'une clé unique, calcul idempotent,
  tableaux jamais mutés ensuite (au pire un double calcul si un worker orphelin coexiste).
  `Transcriber._load_lock`, `modeldl._Downloader._lock` et `cuda._Installer._lock` sont des
  feuilles effectives. Seul ordre inter-modules : `_load_lock` → `downloader._lock` (via
  `transcriber._model_download_running`, qui diffère la repose de la garde hors-ligne pendant un
  téléchargement) — JAMAIS l'inverse (`modeldl._run` appelle `_set_offline_env` hors de son
  verrou, et publie son état final AVANT `on_success` puis repose la garde de façon déterministe).
- **Diarisation — jeton de génération (V2)** : `_session_gen` (incrémenté par `start()`) est passé
  au worker `_diar_loop` avec la file et le diariseur (arguments, jamais relus sur `self`) ;
  `_store_and_write` écarte les écritures d'un worker orphelin (jeton périmé) — ni la file, ni la
  sentinelle, ni le transcript de la session suivante ne peuvent être pollués.

## Décisions d'architecture à respecter

- **Audio** : Whisper exige 16 kHz mono float32. `recorder` rééchantillonne si le micro
  n'expose pas 16 kHz (soxr, repli interpolation NumPy).
- **GPU** : CTranslate2 ne supporte que **CPU et CUDA**. Pas de DirectML — ne pas le proposer
  pour AMD/Intel ; sur ces GPU, rester en CPU int8 (ou envisager whisper.cpp/Vulkan).
- **CUDA (composants + repli, `cuda.py`)** : en mode CUDA, CTranslate2 charge cuBLAS/cuDNN
  (wheels pip `nvidia-cublas-cu12`/`nvidia-cudnn-cu12`) **paresseusement au 1er encodage**.
  ⚠️ Sur Windows ces DLL ne sont PAS trouvées par défaut (dossier `nvidia/*/bin` hors PATH) ET
  l'init WebView2/.NET appelle `SetDefaultDllDirectories`, restreignant la recherche → ni PATH ni
  `add_dll_directory` ne suffisent. `transcriber._add_cuda_dll_directories` **précharge donc les
  DLL par chemin absolu** (`ctypes.WinDLL`, winmode `DEFAULT_DIRS|DLL_LOAD_DIR`) dès `load()` :
  une fois résidentes, le `LoadLibrary` par nom de CTranslate2 les retrouve quelle que soit la
  politique. Si CUDA est demandé mais GPU/composants absents → **repli gracieux CPU int8**
  (`_effective_device_compute`, `cfg.device` inchangé) au lieu d'un plantage à la 1re dictée.
  Installation des composants **opt-in** depuis l'écran Configuration (`GuiApi.install_gpu` →
  `cuda.start_install`, suivi par polling `gpu_status`) : ~1,3 Go, SEUL appel réseau (analogue au
  modèle), jamais silencieux, indisponible en exe figé (`can_install`=false, pas de pip).
- **Confidentialité** : `local_files_only` est **true par défaut** (zéro réseau) ; en mode
  hors-ligne, `transcriber.load()` pose aussi `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`.
- **Journaux sans données personnelles** : un fichier de log peut être partagé pour
  diagnostic — INFO/WARNING/ERROR ne portent donc **ni contenu transcrit, ni métadonnée
  personnelle** (nom ou chemin du fichier importé, entrée de config brute contenant du
  vocabulaire personnel, application au premier plan tracée à chaque dictée). Motif à
  reproduire : une ligne INFO/ERROR factuelle (longueur, type d'erreur) **plus** une ligne
  `logger.debug` avec le détail (cf. `conference._write_line`, `app._process_file` —
  y compris ses branches d'erreur, où le message porte le chemin complet du fichier —,
  `config._build_profiles`, `profiles.for_app`). Restent en clair à dessein : les chemins
  de config/dictionnaire/dossiers (pas de contenu utilisateur, indispensables au
  diagnostic) et le NOM d'un profil mal configuré (libellé de section, sans quoi
  l'avertissement n'est pas actionnable).
- **Modèle manquant → téléchargement guidé (V2, `modeldl.py`)** : si le chargement échoue
  (`ModelNotAvailableError`), la bannière du dashboard propose le téléchargement **opt-in**
  (même doctrine que `cuda.py` : jamais silencieux, progression par polling `model_status`,
  fonctionne AUSSI en exe figé — huggingface_hub est embarqué). Le modèle est matérialisé dans
  `models/faster-whisper-<taille>` à côté de la config, puis `config.yaml` est pointé dessus
  avec `local_files_only: true` (`_on_model_downloaded`). ⚠️ **Contrat taille↔chemin** : l'UI
  raisonne en TAILLES (`medium`) alors que la config peut contenir un chemin bundlé — la
  normalisation (`modeldl.model_size_name`) doit rester appliquée aux 3 endroits :
  `get_dashboard`, `get_config` et `apply_config_from_gui` (qui compare les tailles et
  privilégie un dossier local existant, sinon enregistrer sans changer de taille écraserait un
  modèle bundlé fonctionnel).
- **Instance unique (V2, `singleinstance.py`)** : mutex nommé `Local\Whisperty.SingleInstance`
  (par session, cohérent avec l'installation par utilisateur) + évènement « montre-toi » que le
  second lancement déclenche avant de sortir (`__main__` → `on_second_instance` → fenêtre
  réaffichée, ou notification en mode tray seul). Règle : la garde ne doit JAMAIS empêcher un
  lancement (échec d'API Win32 = démarrage normal) ; no-op hors Windows. Les tests utilisent des
  noms d'objets uniques (pas de collision avec une instance réelle) et une doublure kernel32
  (`_k32_cached`) pour couvrir les chemins Windows sur la CI Linux.
- **IA locale (V2)** : `ai.py` n'autorise QUE des endpoints locaux (`ai.is_local_endpoint` :
  localhost/127.0.0.1/::1) et est **désactivé par défaut**. Tout endpoint distant est refusé —
  le texte dicté ne doit jamais sortir de la machine. Échec LLM = texte brut conservé (jamais bloquant).
  Le **résumé de fin de session** (UC-17, `summary:`) réutilise le MÊME LLM local
  (`LocalLLM.summarize`, opt-in **indépendant** de `ai.enabled`, garde identique dans `_chat`) :
  lancé par `WhispertyApp._maybe_summarize` dans un thread worker **APRÈS** le retour IDLE
  (jamais sous `_lock`, ne bloque ni la machine à états ni une nouvelle dictée), il complète le
  transcript (`# Résumé`), historise (`source="résumé live/réunion"`) et notifie ; échec = session
  déjà archivée, rien n'est perdu. Entrée tronquée début+fin au-delà de `summary.max_chars`.
- **Historique (V2)** : `history.py` = SQLite local (`sqlite3` stdlib), connexion partagée
  `check_same_thread=False` mais **tous les accès passent par `History._lock`** ; écriture non bloquante.
  Schéma **versionné** (`PRAGMA user_version`, migrations incrémentales dans `_migrate`, idempotentes,
  jamais de DROP) ; index FTS synchronisé par triggers INSERT/DELETE/**UPDATE** (le renommage
  post-session réécrit `text` par UPDATE — sans le trigger, la recherche divergerait).
- **Renommage post-session des locuteurs (V2, FR-31, réunion diarisée)** : à l'arrêt, `conference.
  _session_payload` publie la **structure de session** (segments à CLÉS `spk:N`/source/« Note »,
  registre des libellés, chemin/format/**en-tête d'origine** du transcript) que `_on_conference_finished`
  archive dans la colonne `payload` (JSON). `WhispertyApp.rename_history_speaker` (pont
  `GuiApi.rename_history_speaker`, panneau du détail Historique) re-rend le texte depuis ces clés
  (`conference.render_payload_lines`, pur), met à jour l'entrée (`History.update_text`) puis réécrit le
  fichier exporté (`rewrite_payload_transcript`, atomique) en **préservant la section « Résumé »**
  ajoutée après coup par UC-17. Fichier déplacé/supprimé = dégradation propre : historique mis à jour,
  utilisateur notifié (`_notify_user`). Aucun verrou de la machine à états n'est pris ; la séquence
  complète est sérialisée par `_archive_lock` (cf. section Concurrence).
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
  100 % local.
- **Réunion — diarisation par locuteur (V2, itération 3, UC-18, `diarization.py`)** :
  `conference.speaker_diarization.enabled: true` (**opt-in**, défaut `false`) **étend** la distinction
  par source — au lieu de `Moi`/`Interlocuteurs`, chaque segment porte une étiquette de **voix**
  (`Locuteur 1`, `Locuteur 2`, …). ⚠️ **Doctrine zéro-réseau, zéro dépendance** : la diarisation intégrée
  est une **empreinte MFCC calculée en pur NumPy** (statistiques MFCC par segment, L2-normalisées) +
  **clustering en ligne** (similarité cosinus) — PAS `pyannote` (PyTorch + modèles *gated* HF, en tension
  avec la contrainte cardinale). **Rien à télécharger** = garantie zéro-fuite maximale (CO-17) ; c'est un
  compromis précision/simplicité assumé (sépare des voix nettement différentes), l'embedder restant
  **enfichable** (`Diarizer(embed_fn=…)`) pour un futur backend ONNX hors-ligne. Exige le mode distinction
  (pas de mixage) ; sinon `_make_diarizer()` renvoie `None` (repli). `SpeakerRegistry` : clustering **par
  source** (plafond `max_speakers` par source, FR-32) mais **numérotation GLOBALE** (ordre de première
  apparition, l'étiquette ne révèle que la voix, pas la source). ⚠️ **Worker dédié (RE-14)** : la
  transcription reste dans le fil `_consume_distinct` ; l'empreinte+clustering tournent dans `_diar_loop`
  (thread séparé drainant `_diar_queue`), joint par **sentinelle `None`** APRÈS le dernier segment et AVANT
  `_close_transcript`/`_finish` (comme la file live) → `_segments` complet au tri final. ⚠️ **Stockage par
  CLÉ, pas par libellé** : `_segments` retient `(start, key, text)` où `key` = `spk:N` (diarisé) / étiquette
  de source (repli) / `Note` ; `_label_for(key)` résout le libellé **au rendu** (flux, export trié,
  historique) → le **renommage est rétroactif** (FR-31 : `rename_speaker` met à jour le registre, `app`
  réémet le flux via `render_lines()` — avec auto-réparation au segment suivant, cf. Concurrence —,
  l'export/historique se rendent depuis les mêmes clés à l'arrêt).
  **Repli gracieux (BR-08/RE-13)** : segment trop court/silencieux/erreur → étiquette de source, jamais
  d'omission ni d'arrêt. `SpeakerRegistry` est un **verrou feuille** (`assign` depuis `_diar_loop`,
  `rename`/`speakers` depuis le pont GUI). `pyannote` reste **écarté** (PyTorch + modèles *gated*).
- **Diarisation — backend ONNX (V2, CO-19, opt-in)** : `speaker_diarization.backend: mfcc | onnx`
  (défaut `mfcc`). En `onnx`, `diarization.OnnxEmbedder` remplace l'empreinte MFCC (branché par
  `Diarizer` via le même `embed_fn` — le reste de la chaîne est inchangé). ⚠️ **`providers=
  ("CPUExecutionProvider",)` OBLIGATOIRE** : les roues onnxruntime récentes exposent AUSSI
  `AzureExecutionProvider` (inférence DÉPORTÉE) — laisser onnxruntime choisir ouvrirait un chemin
  réseau. Télémétrie coupée (`_disable_ort_telemetry`, factorisée dans `transcriber` pour couvrir les
  DEUX chemins d'import) et profilage désactivé. Entrée = **fbank kaldi 80 canaux en pur NumPy**
  (`fbank_features`) : ⚠️ le banc mel se construit sur `n_fft//2` bins (bin de Nyquist IGNORÉ, colonne
  de zéros ajoutée) et le plancher du log est `eps` float32 — un banc sur 257 bins ou un plancher
  `tiny` dégraderaient silencieusement les empreintes. Modèle = WeSpeaker ResNet34-LM (dépôt HF
  public, CC-BY-4.0 → **attribution dans `NOTICE.md`**), téléchargé **opt-in** par
  `modeldl.start_embedding_download` (mêmes garanties que le modèle Whisper ; dossier de travail
  temporaire pour que la progression n'englobe pas le modèle Whisper, mise en place par `os.replace`).
  ⚠️ `modeldl.download_running()` **agrège les deux téléchargements** et `transcriber.
  _model_download_running` l'appelle : sans cela, reposer la garde hors-ligne ferait échouer un
  téléchargement en vol. Échec de chargement → **repli MFCC notifié** (`Diarizer.notice` →
  `ConferenceTranscriber._on_notice` → `_notify_user`), décidé AVANT le début de la session.
  Seuil DÉDIÉ (`onnx_similarity_threshold`, 0,45 calibré sur des enregistrements réels) : l'échelle
  de similarité n'est pas celle du MFCC (0,75), réutiliser ce dernier créerait un locuteur par segment.
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
- **Flux live « au fil de l'eau » dans l'UI (V2, live + réunion)** : en mode **Live continu** comme en
  **Conférence**, la tuile « Dernière transcription » du dashboard devient un **flux en direct** — chaque
  segment transcrit s'y ajoute immédiatement (titre → « Transcription en direct », zone défilante qui suit
  le dernier segment). Câblage : `LiveTranscriber`/`ConferenceTranscriber` exposent déjà un callback
  `on_segment` (thread worker) ; `WhispertyApp` le branche (`_on_live_segment`/`_on_conference_segment`) vers
  un accumulateur thread-safe (`_live_lock` + `_live_lines` + compteur monotone `_live_rev`). Le live affiche
  le texte seul ; la réunion affiche la **ligne déjà formatée** (`[MM:SS]` + éventuel locuteur). ⚠️ Respect du
  modèle **polling, pas de push** : `poll()` ne renvoie que `liveRev` (entier) — le JS ne récupère le texte
  (`get_live_text` → `{rev, text}`) **que** lorsque `liveRev` change (payload de tick minimal, jamais tout le
  transcript 5×/s). `_live_lock` est un **verrou feuille** (jamais imbriqué avec `_lock` ni avec `_note_lock` ;
  `poll()` relâche `_lock` avant de lire `live_rev()` ; cf. Concurrence pour la CAS de
  republication). ⚠️ **Ordre dans les callbacks de fin** : `_on_live_finished`/
  `_on_conference_finished` historisent (et copient, en live) **AVANT** de repasser `IDLE` — sinon course :
  le JS, voyant `IDLE`, recharge la tuile depuis `history.last_text()` qui renverrait la transcription
  *précédente*. À l'arrêt, la tuile bascule sur ce texte final d'historique (réunion : version triée).
  `_reset_live_transcript()` (appelé au démarrage de chaque live/réunion) vide le flux et bump `_live_rev`
  (la tuile repart de « En écoute… »). La doublure `Mock` de `app.js` simule ce flux (aperçu autonome).
- **Préréglages de performance + bench local (V2, écran Configuration)** : trois préréglages
  (« Rapide » base+int8, « Équilibré » medium+int8, « Précis » large-v3 + float16 si CUDA
  sélectionné) remplissent les champs côté JS ; l'application passe par `apply_config_from_gui`.
  Le `compute_type` n'a PAS de champ dédié mais fait partie du **contrat des 3 endroits**
  (`get_config` → clé `compute`, payload `saveConfig`, `apply_config_from_gui` — liste blanche
  int8/float16/int8_float16, valeur inconnue ignorée). Le bench (« Tester sur ce poste »,
  `WhispertyApp.start_bench`) transcrit un audio témoin **généré localement**
  (`transcriber.bench_audio`, pur NumPy, graine fixe — rien à télécharger, mesures comparables)
  via `transcribe_bench` (**sans VAD** : Silero écarterait le signal synthétique et la mesure
  tomberait à ~0 s). Mode **exclusif** via la machine à états (IDLE→PROCESSING→IDLE, comme
  l'import audio — jamais en parallèle d'une dictée) ; il mesure la configuration ENREGISTRÉE
  (modèle réellement chargé), pas les champs non sauvegardés ; progression par polling
  `bench_status` (modèle `gpu_status`). Modèle manquant → statut d'erreur actionnable
  (`_model_unavailable_message`), jamais d'exception.
- **Écriture de config (V2, `configio.py`)** : l'écran Configuration enregistre via `update_yaml_file`
  (édition **ligne par ligne** préservant commentaires/ordre) — PAS `yaml.safe_dump` (détruirait les
  commentaires) ni `ruamel` (dépendance évitée). `apply_config_from_gui` mute les dataclasses en place
  (les sous-systèmes partagent ces objets), réécrit le fichier, puis applique à chaud : reset du modèle
  (taille/device/`local_files_only`), `reload_hotkey()`, reconstruction injecteur/LLM. La **langue** est
  lue à chaque transcription → pas de rechargement de modèle.
- **Dictionnaire — édition assistée (V2, UC-19, `dictionary.py`)** : l'écran « Dictionnaire » de la
  fenêtre liste/édite les entrées (`GuiApi.get_dictionary`/`save_dictionary` →
  `apply_dictionary_from_gui`). Écriture via `update_dictionary_file` (ligne par ligne, préserve
  commentaires/ordre — même doctrine que `configio`, sans ruamel) ; entrées invalides ignorées,
  doublons dédupliqués. Puis **rechargement à chaud** (`transcriber.set_dictionary` +
  `profiles.reload_dictionary`) — aucune relance, aucun rechargement de modèle. Échec d'écriture =
  fichier intact + notification (`_notify_user`). Repli mode tray seul : « Ouvrir le dictionnaire »
  (`open_dictionary`, crée le fichier avec en-tête d'aide si absent). L'édition reste possible même
  si `dictionary.enabled: false` (le fichier est écrit ; l'effet attend l'activation).
- **Injection FR** : privilégier le collage presse-papiers (Ctrl+V) à la frappe caractère par
  caractère — bien plus fiable pour les accents (é, è, à, ç) et les longs textes.
- **Raccourci** : ne pas utiliser `Win+Space` (réservé par Windows). Défaut configurable.
- **ffmpeg** : non requis (PyAV est embarqué par faster-whisper) — ne pas l'ajouter en dépendance.
- **Nommage** : module `injector` et non `typer` (la lib PyPI `typer` est tirée transitivement
  par huggingface-hub — un module `typer.py` la masquerait).
- **Packaging (build figé)** : PyInstaller en **onedir** (`whisperty.spec` → `dist\whisperty\` ;
  démarrage rapide, c'est la structure recopiée par l'installeur). Point d'entrée = `whisperty_launcher.py`
  (imports **absolus**) et NON `whisperty/__main__.py` : PyInstaller exécute l'entrée comme top-level
  `__main__` sans package parent → les imports relatifs de `__main__.py` lèveraient `ImportError`
  (`__main__.py` reste en relatif pour `python -m whisperty`). `config.yaml`/`dictionary.txt` ne sont PAS
  embarqués (éditables, déposés À CÔTÉ de l'exe = `base_dir`) ; les **assets `whisperty/web/` DOIVENT** l'être
  (`gui.web_dir()` les résout via `sys._MEIPASS`). `upx=False` (UPX corrompt les DLL natives). `collect_all`
  couvre faster_whisper/ctranslate2/soxr/sounddevice/**soundcard**/pystray/pynput/**webview+pythonnet+clr_loader**
  (pile WebView2/.NET) ; sans cette pile, repli tray seul.
- **Modèle en déploiement** : `build.ps1` **bundle** par défaut le modèle dans `dist\whisperty\models\` et
  patche `config.yaml` → `model: models/faster-whisper-<taille>` + `local_files_only: true` (zéro réseau sur
  la cible). `transcriber._resolve_model_arg()` résout un modèle « chemin » en **absolu** via `base_dir`
  (le CWD n'est pas fiable au démarrage auto / figé) ; un nom de taille reste passé tel quel. Variante
  `build.ps1 -NoModel` → `local_files_only: false` (le modèle, et la vérif de révision HF, passent par le
  réseau au 1er usage — dictée, import audio ou bench « Tester sur ce poste », tous sur le même chemin
  gardé de `load()`) — d'où le bundling comme défaut conforme à la contrainte cardinale.
- **Défauts d'expédition (build.ps1)** : le `config.yaml` du dépôt reflète le POSTE DE DEV (CUDA,
  LLM local actif). `build.ps1` patche la copie expédiée vers des défauts neutres :
  `device: cpu`/`int8`, `ai.enabled: false`, `summary.enabled: false` (un poste vierge n'a ni
  composants CUDA ni serveur LLM — sinon avertissements et échecs journalisés à chaque usage).
  NE PAS « corriger » le config.yaml du dépôt pour l'expédition : c'est le rôle de ce patch.
- **Installeur (`installer/whisperty.iss`, Inno Setup)** : installation **par utilisateur** dans
  `%LocalAppData%\Programs\Whisperty` (sans admin) — INDISPENSABLE car l'app écrit `config.yaml` (édité via
  l'UI), `whisperty.db`, `logs\`, `transcriptions\` À CÔTÉ de l'exe (échouerait sous `Program Files`).
  Autostart = clé `HKCU\…\Run` (cohérent avec `scripts\install_autostart.ps1`). `config.yaml`/`dictionary.txt`
  posés en `onlyifdoesntexist` (MAJ préserve les réglages). WebView2 vérifié, non bloquant : s'il manque, un
  dialogue propose d'OUVRIR la page de téléchargement. MAJ/désinstallation : `KillRunningApp` (taskkill dans
  `PrepareToInstall`/`CurUninstallStepChanged`) — la fermeture « douce » Restart Manager ne quitte PAS une
  app de tray (sa fenêtre intercepte la fermeture pour se masquer), les fichiers resteraient verrouillés.

## Conventions

- Code commenté **en français**, docstrings, type hints (`from __future__ import annotations`).
- Gestion d'erreurs robuste et explicite : micro absent, modèle non téléchargé, droits insuffisants.
