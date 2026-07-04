# 03 — Cas d'utilisation

## 1. Acteurs

### Acteurs humains
| Acteur | Description | Personas |
|--------|-------------|----------|
| **Utilisateur** | Personne qui dicte / transcrit / configure au quotidien. | P-01, P-02, P-04, P-05 |
| **Administrateur** | Installe, package, déploie, maintient. | P-06 |
| *(Auditeur)* | Vérifie la confidentialité — n'interagit pas directement, contraint le système. | P-03 |

### Acteurs système (secondaires)
| Acteur | Rôle dans les scénarios |
|--------|-------------------------|
| **Application active** | Cible de l'injection de texte (fenêtre Windows au premier plan). |
| **Moteur Whisper** (faster-whisper) | Transcrit l'audio en texte, localement. |
| **LLM local** | Raffine le texte (Ollama, LM Studio…), localhost uniquement. |
| **Périphérique audio** | Micro (entrée) et/ou sortie système (loopback). |

## 2. Diagramme de cas d'utilisation

```mermaid
graph LR
    U((Utilisateur))
    A((Administrateur))

    subgraph Whisperty
      UC01[UC-01 Dicter dans l'app active]
      UC02[UC-02 Déclencher la dictée]
      UC03[UC-03 Arrêt auto silence / durée max]
      UC04[UC-04 Corriger via dictionnaire]
      UC05[UC-05 Adapter par profil de contexte]
      UC06[UC-06 Raffiner par IA locale]
      UC07[UC-07 Importer un fichier audio]
      UC08[UC-08 Consulter / copier l'historique]
      UC09[UC-09 Transcrire une sortie en direct]
      UC10[UC-10 Transcrire une réunion]
      UC12[UC-12 Configurer l'application]
      UC13[UC-13 Packager / démarrage auto]
      UC14[UC-14 Obtenir le modèle initial]
      UC15[UC-15 Activer l'accélération GPU]
      UC16[UC-16 Prendre des notes en session]
      UC17[UC-17 Résumer une session]
      UC18[UC-18 Distinguer les locuteurs individuels]
      UC19[UC-19 Gérer le dictionnaire]
    end

    U --> UC01 & UC02 & UC07 & UC08 & UC09 & UC10 & UC12 & UC16 & UC17 & UC19
    A --> UC12 & UC13 & UC14 & UC15

    UC01 -. include .-> UC02
    UC02 -. include .-> UC03
    UC01 -. include .-> UC04
    UC01 -. extend .-> UC05
    UC01 -. extend .-> UC06
    UC07 -. extend .-> UC06
    UC09 -. extend .-> UC16
    UC10 -. extend .-> UC16
    UC10 -. extend .-> UC18
    UC09 -. extend .-> UC17
    UC10 -. extend .-> UC17
    UC18 -. extend .-> UC17
```

> `include` = sous-fonction toujours exécutée ; `extend` = comportement optionnel/conditionnel.

## 3. Machine à états (rappel)

La dictée et les modes exclusifs partagent une machine à états sérialisée (un seul état à la
fois) ; l'icône tray en reflète la couleur.

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> RECORDING : déclenchement (UC-02)
    RECORDING --> PROCESSING : 2e appui / relâchement / silence / durée max
    PROCESSING --> IDLE : texte injecté ou archivé
    IDLE --> PROCESSING : import fichier (UC-07) / préchargement modèle
    IDLE --> LIVE : transcription live (UC-09)
    IDLE --> CONFERENCE : réunion (UC-10)
    LIVE --> IDLE : arrêt (callback de fin)
    CONFERENCE --> IDLE : arrêt (callback de fin)
```

**Règle d'exclusivité (BR-01)** : tout déclenchement reçu dans un état autre que `IDLE` (pour
les modes exclusifs) ou hors `RECORDING` (pour l'arrêt de dictée) est **ignoré** (no-op
journalisé), jamais mis en file.

## 4. Catalogue des cas d'utilisation

| UC | Titre | Acteur | Priorité | Personas |
|----|-------|--------|----------|----------|
| UC-01 | Dicter du texte dans l'application active | Utilisateur | M | P-01, P-02, P-04 |
| UC-02 | Déclencher / arrêter la dictée | Utilisateur | M | P-01, P-04 |
| UC-03 | Arrêt automatique (silence / durée max) | Système | M | P-04 |
| UC-04 | Corriger via le dictionnaire personnalisé | Utilisateur | S | P-01, P-02 |
| UC-05 | Adapter le contexte par profil applicatif | Utilisateur | C | P-01 |
| UC-06 | Raffiner le texte par IA locale | Utilisateur | C | P-01 |
| UC-07 | Importer et transcrire un fichier audio | Utilisateur | S | P-05 |
| UC-08 | Consulter / copier l'historique | Utilisateur | S | P-02, P-05 |
| UC-09 | Transcrire une sortie audio en direct (live) | Utilisateur | S | P-05 |
| UC-10 | Transcrire une réunion (micro + sortie) | Utilisateur | S | P-02, P-05 |
| UC-12 | Configurer l'application | Utilisateur / Admin | M | P-06 |
| UC-13 | Packager / activer le démarrage automatique | Administrateur | C | P-06 |
| UC-14 | Obtenir le modèle initial (1er lancement) | Utilisateur / Admin | M | P-06 |
| UC-15 | Activer l'accélération GPU NVIDIA | Administrateur | C | P-06 |
| UC-16 | Prendre des notes pendant une session (live / réunion) | Utilisateur | C | P-02, P-05 |
| UC-17 | Résumer une session (live / réunion) par IA locale | Utilisateur / Système | C | P-02, P-05 |
| UC-18 | Distinguer les locuteurs individuels en réunion | Utilisateur | C | P-02, P-05 |
| UC-19 | Gérer le dictionnaire personnalisé (ajouter / modifier / supprimer) | Utilisateur | S | P-01, P-02 |

> Le persona **P-03 (RSSI/DPO)** n'apparaît pas en colonne « Personas » : il agit comme
> **acteur-contrainte** (validation du zéro-réseau, `CO-01…03`) plutôt qu'en utilisateur direct.
> Son rattachement transverse aux UC figure dans la matrice [`05` §1](05-tracabilite-et-risques.md).

---

## 5. Fiches détaillées

> Dans les fiches ci-dessous, **« Exigences liées »** est une liste *indicative* des exigences
> les plus saillantes du cas. La **traçabilité complète et bidirectionnelle** UC ↔ exigences
> fait foi dans [`05` §2–§3](05-tracabilite-et-risques.md).

### UC-01 — Dicter du texte dans l'application active

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Acteurs secondaires** | Moteur Whisper, Application active, (LLM local si UC-06) |
| **Objectif** | Convertir la parole en texte et l'insérer dans la fenêtre active. |
| **Déclencheur** | Raccourci global (UC-02). |
| **Préconditions** | App lancée (icône tray) ; micro autorisé ; modèle disponible ; état `IDLE`. |
| **Garanties (succès)** | Le texte transcrit (corrigé, éventuellement raffiné) est inséré dans l'app cible et archivé dans l'historique. |
| **Garanties minimales** | En cas d'échec, retour à `IDLE`, erreur journalisée localement, aucune donnée perdue ni envoyée. |

**Scénario nominal**
1. L'utilisateur déclenche la dictée (UC-02) → état `RECORDING` (icône **rouge**).
2. Le système capture le micro en 16 kHz mono (rééchantillonnage si nécessaire).
3. (Si profils activés) le système mémorise l'application au premier plan = cible (UC-05).
4. L'utilisateur arrête la dictée (2ᵉ appui / relâchement / silence / durée max) → état `PROCESSING` (icône **orange**).
5. Le moteur Whisper transcrit l'audio localement (langue forcée `fr` par défaut, hotwords du dictionnaire).
6. Le système applique les **corrections** du dictionnaire (UC-04).
7. (Si IA activée) le texte est raffiné par le LLM local (UC-06).
8. Le système **injecte** le texte dans l'app cible (collage `Ctrl+V` par défaut).
9. Le système archive la transcription (source = `dictée`) et revient à `IDLE` (icône **grise**).

**Extensions / exceptions**
- *4a. Transcription vide* (aucune parole) : rien n'est injecté, retour `IDLE`, journalisé.
- *5a. Modèle non disponible* : erreur explicite journalisée, retour `IDLE`, aucune injection.
- *2a. Micro absent / non autorisé* : l'enregistrement ne démarre pas, erreur journalisée, reste `IDLE`.
- *8a. Méthode `type`* : si configurée, frappe caractère par caractère (repli, moins fiable pour les accents).
- *7a. Échec du LLM* : le **texte brut** est conservé et injecté (non bloquant).

**Règles** : BR-01 (exclusivité), BR-02 (langue), BR-04 (collage privilégié).
**Exigences liées** : FR-01, FR-02, FR-03, FR-05, FR-07, RE-01, RE-02, PE-01, PE-02.

---

### UC-02 — Déclencher / arrêter la dictée

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Démarrer et arrêter l'enregistrement de manière ergonomique et configurable. |
| **Préconditions** | Écouteur de raccourci global actif. |

**Variantes (selon `hotkey.mode` / `double_tap_key`)**
1. **Toggle** *(défaut)* : un appui sur le combo (`<ctrl>+<alt>+<space>`) démarre ; un second appui (ou un silence, UC-03) arrête.
2. **Push-to-talk** : maintenir le combo enregistre ; relâcher arrête et transcrit.
3. **Double-appui** : si `double_tap_key` est défini (ex. `ctrl`), un double-appui rapide (< 0,4 s) démarre/arrête.

**Extensions / exceptions**
- *Combo invalide* : repli automatique sur `<ctrl>+<alt>+<space>`, avertissement journalisé.
- *Déclenchement pendant un mode exclusif / `PROCESSING`* : ignoré (BR-01).

**Exigences liées** : FR-01, US-01, RE-05, CO-09 (ne pas utiliser `Win+Space`).

---

### UC-03 — Arrêt automatique (silence / durée max)

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Système (surveillance) |
| **Objectif** | Clore l'enregistrement sans action manuelle. |

**Scénario nominal (mode toggle)**
1. Pendant `RECORDING`, le système surveille le niveau RMS.
2. Après détection de parole puis un **silence** ≥ `audio.silence_duration`, l'enregistrement s'arrête et passe en `PROCESSING`.

**Garde-fou (tous modes)**
- Si la durée atteint `audio.max_duration`, l'enregistrement est **forcé** à l'arrêt (protection mémoire / coût).
- En push-to-talk, l'arrêt provient du relâchement de touche ; seul le garde-fou de durée s'applique.

**Exigences liées** : FR-15, RE-03, PE-04.

---

### UC-04 — Corriger via le dictionnaire personnalisé

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Améliorer la fidélité du vocabulaire métier et corriger des erreurs récurrentes. |

**Mécanisme** (`dictionary.txt`, une entrée par ligne)
- `terme` → **hotword** fourni au modèle pour favoriser sa reconnaissance.
- `mauvais => correct` → **correction** appliquée *après* transcription (post-traitement).

**Gestion des entrées** : l'ajout / la modification / la suppression des entrées se fait via **UC-19**
(assistée par la fenêtre, ou par édition directe du fichier).
**Préconditions** : `dictionary.enabled: true`.
**Exigences liées** : FR-07, US-04, SU-02.

---

### UC-05 — Adapter le contexte par profil applicatif

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Adapter prompt / langue / dictionnaire selon l'application visée. |

**Scénario**
1. Au **démarrage** de la dictée, le système identifie l'exécutable au premier plan (Win32, local).
2. Le **premier profil** dont un `match` est une sous-chaîne du nom d'exécutable s'applique (sinon contexte par défaut).
3. Ses `initial_prompt` / `language` / `hotwords` / `corrections` / `dictionary` surchargent le contexte de la transcription.

**Préconditions** : `profiles.enabled: true`.
**Règle** : la cible est capturée **au démarrage** (= cible de l'injection), pas à l'arrêt.
**Exigences liées** : FR-08, CO-10.

---

### UC-06 — Raffiner le texte par IA locale

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Acteur secondaire** | LLM local |
| **Objectif** | Corriger ponctuation, casse et fautes évidentes sans reformuler. |

**Scénario**
1. Après transcription, le texte est envoyé au LLM **local** (endpoint compatible OpenAI).
2. Le LLM renvoie le texte corrigé, qui remplace le texte brut.

**Préconditions** : `ai.enabled: true` ; endpoint **local** ; serveur LLM lancé.
**Exceptions**
- *Endpoint non local* : **refusé** par conception (CO-03) — aucune sortie de données.
- *Échec / timeout du LLM* : le **texte brut** est conservé (non bloquant).

**Exigences liées** : FR-09, CO-03, RE-06.

---

### UC-07 — Importer et transcrire un fichier audio

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Transcrire un fichier audio existant (WAV/MP3/M4A/FLAC/OGG/OPUS/WMA/AAC). |

**Scénario nominal**
1. Tray → « Importer un fichier audio… » ; un sélecteur de fichiers s'ouvre.
2. Le fichier est décodé (PyAV, **sans ffmpeg**) et transcrit localement → `PROCESSING`.
3. (Si IA) raffinage local.
4. Le texte est **copié dans le presse-papiers** (et non injecté : la cible serait ambiguë depuis le tray) et archivé (source = `fichier`).
5. Une notification confirme ; retour `IDLE`.

**Exceptions** : fichier introuvable / modèle absent / aucune parole → notification + journalisation, retour `IDLE`.
**Préconditions** : état `IDLE` ; tkinter disponible pour le sélecteur.
**Exigences liées** : FR-10, CO-07 (pas de ffmpeg), RE-02.

---

### UC-08 — Consulter / copier l'historique

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Récupérer une transcription passée. |

**Fonctions**
- « **Copier la dernière transcription** » → presse-papiers.
- « **Ouvrir le dossier de l'historique** » → explorateur sur le dossier de `whisperty.db`.

**Mécanisme** : base **SQLite locale** (`whisperty.db`), purge automatique au-delà de
`history.max_entries` (défaut 200), accès sérialisés (thread-safe), écriture non bloquante.
**Préconditions** : `history.enabled: true`.
**Exigences liées** : FR-11, RE-07, SU-03.

---

### UC-09 — Transcrire une sortie audio en direct (live)

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Transcrire en continu ce qui *sort* d'un périphérique (ex. confcall) sans importer de fichier. |

**Scénario nominal**
1. Tray → « Transcription live (sortie audio) » → choix de la sortie (défaut ou périphérique listé).
2. État `LIVE` (icône **bleue**) ; capture **loopback** (WASAPI / `soundcard`).
3. Un segmenteur VAD découpe le flux ; chaque segment est transcrit (réglages de base, **sans** profil ni LLM — priorité latence) et écrit au fil de l'eau dans `transcriptions/live_<horodatage>.txt`.
4. (Si l'**interface fenêtre** est ouverte) chaque segment s'ajoute **au fil de l'eau** à la tuile « Dernière transcription » du tableau de bord, qui devient un flux en direct (US-09) — sans attendre l'arrêt.
5. À l'arrêt (menu), le texte complet est **copié** dans le presse-papiers et archivé (source = `live`) ; retour `IDLE`.

**Exceptions**
- *`soundcard` absent / loopback indisponible* : démarrage échoue, notification, retour `IDLE`.
- *Aucun texte transcrit* : notification, pas d'archivage.
- *Fenêtre fermée / mode tray seul* : la capture, le fichier et l'archivage sont **inchangés** (l'affichage en direct est un confort, non bloquant).

**Préconditions** : `soundcard` installé.
**Exigences liées** : FR-12, US-09, RE-08, PE-03, CO-05, CO-06 (COM par thread).

---

### UC-10 — Transcrire une réunion (micro + sortie système)

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Transcrire une réunion complète : sa voix (micro) **et** les interlocuteurs distants (sortie système). |

**Scénario nominal (distinction par locuteur — défaut)**
1. Tray → « Transcription de réunion (micro + sortie) » → choix de la sortie.
2. Notification de **rappel de consentement** ; état `CONFERENCE` (icône **verte**).
3. Le micro et la sortie sont capturés **simultanément**, chacun segmenté et transcrit séparément, horodaté par position audio.
4. (Si l'**interface fenêtre** est ouverte) chaque segment transcrit s'affiche **au fil de l'eau** dans la tuile « Dernière transcription » du tableau de bord (flux en direct, ligne `[MM:SS] Moi/Interlocuteurs : …`) — US-09.
5. À l'arrêt, les segments des deux sources sont **entrelacés chronologiquement** et exportés (`.txt`/`.md` horodaté) :
   `[MM:SS] Moi : …` / `[MM:SS] Interlocuteurs : …`.
6. Le transcript est archivé (source = `réunion`) ; **non injecté** ; retour `IDLE`.

**Variante (itération 1 — `distinguish_speakers: false`)** : les deux sources sont **mixées**
en une transcription continue sans étiquette de locuteur.

**Exceptions / robustesse**
- *Une source manque ou meurt en cours* : l'autre source est utilisée seule (la source morte est retirée pour ne pas geler l'alignement).
- *Aucune source* : démarrage échoue, notification.
- *Fenêtre fermée / mode tray seul* : capture, export et archivage **inchangés** (l'affichage en direct est un confort, non bloquant).

**Préconditions** : `conference.enabled: true` ; `soundcard` ; **consentement** des participants (BR-05).
**Extensions**
- *Distinction individuelle des orateurs* (UC-18) : au-delà de `Moi`/`Interlocuteurs`, chaque voix
  est identifiée séparément sur le micro **et** sur la sortie système, puis entrelacée chronologiquement.
**Exigences liées** : FR-13, US-09, RE-08, RE-09, CO-04, CO-05, BR-05.

---

### UC-12 — Configurer l'application

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur / Administrateur |
| **Objectif** | Régler le comportement via un fichier unique. |

**Scénario**
1. Tray → « Ouvrir la configuration » ouvre `config.yaml`.
2. L'utilisateur édite les sections (audio, transcription, hotkey, output, dictionary, history, ai, profiles, live, conference — dont `speaker_diarization` pour UC-18).
3. **Relance** de l'application pour prise en compte.

**Règle** : un seul fichier `config.yaml` abondamment commenté fait foi ; le dictionnaire est dans `dictionary.txt`.
**Exigences liées** : FR-17, SU-01, US-05.

---

### UC-13 — Packager / activer le démarrage automatique *(Admin)*

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Administrateur |
| **Objectif** | Produire un exécutable autonome et lancer Whisperty au démarrage de Windows. |

**Scénario**
1. `pyinstaller whisperty.spec` → `dist\whisperty.exe` (onefile, `upx=False`).
2. Copier `config.yaml` et `dictionary.txt` **à côté** de l'exe (non embarqués, éditables).
3. `scripts\install_autostart.ps1` (par utilisateur, sans droits admin) ; désinstallation par le script inverse.

**Exigences liées** : FR-17, SU-04, CO-08 (config non embarqué), CO-11 (`upx=False`).

---

### UC-14 — Obtenir le modèle initial (premier lancement)

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur / Administrateur |
| **Objectif** | Mettre en cache le modèle Whisper, **unique** opération réseau tolérée. |

**Scénario**
1. Récupérer le modèle (`python -c "from faster_whisper import WhisperModel; WhisperModel('medium')"` — adaptez `'medium'` au `model` de votre `config.yaml`) **ou** passer ponctuellement `local_files_only: false`, lancer une fois, puis remettre `true`.
2. Ensuite, fonctionnement 100 % hors-ligne : `local_files_only` est passé inconditionnellement à `WhisperModel` ; `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` sont posés par défaut (`setdefault`).

**Exigences liées** : CO-01, CO-02, PE-02 (préchargement).

---

### UC-15 — Activer l'accélération GPU NVIDIA *(Admin)*

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Administrateur |
| **Objectif** | Accélérer la transcription sur GPU NVIDIA. |

**Scénario**
1. `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`.
2. `config.yaml` : `device: cuda`, `compute_type: float16`.

**Contrainte** : CPU et CUDA uniquement — **pas** de DirectML (AMD/Intel restent en CPU `int8`).
**Exigences liées** : FR-03, PE-01, CO-12.

---

### UC-16 — Prendre des notes pendant une session (live / réunion)

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Capturer pendant une session live (UC-09) ou réunion (UC-10) des **notes personnelles** horodatées (idée, action à faire, moment important), ancrées chronologiquement dans la transcription, sans quitter la réunion ni interrompre la capture. |
| **Déclencheur** | Champ « Ajouter une note » de la fenêtre ; **ou** raccourci global « signet » ; **ou** action « Noter » sur un segment du flux en direct. |
| **Préconditions** | État `LIVE` ou `CONFERENCE`. La saisie et « Noter » requièrent la fenêtre ouverte (US-09) ; le **signet** fonctionne fenêtre masquée. |
| **Garanties (succès)** | Les notes figurent dans le flux affiché, dans le transcript à leur position chronologique (+ section récapitulative « Notes »), dans l'historique et dans la copie de fin de live. |
| **Garanties minimales** | Une note qui échoue à s'écrire dans le fichier est conservée en mémoire et restituée à l'arrêt ; la capture/transcription n'est **jamais** interrompue par la prise de note. |

> UC-16 n'introduit **aucun état** dans la machine à états (§3) : il s'exécute *pendant*
> `LIVE`/`CONFERENCE` et ne modifie pas les transitions.

**Scénario nominal (note textuelle)**
1. Pendant la session, l'utilisateur saisit son texte (ex. « À faire : envoyer le budget révisé à Marc ») et valide.
2. Le système horodate la note au moment de la **validation**, dans le référentiel du mode : position de session `[MM:SS]` en réunion (comme les segments), heure `HH:MM:SS` en live (comme le fichier).
3. La note s'insère dans le flux affiché, **visuellement distincte** des segments transcrits (préfixe stable, ex. `[Note]`).
4. La note est écrite au fil de l'eau dans le fichier transcript, sur sa propre ligne ; le champ de saisie se vide.

**Variantes**
- *Signet (mains occupées)* : un appui sur le raccourci global dédié crée immédiatement une note-signet horodatée **sans texte** (ex. `[Note] Moment marqué`), même sans focus sur la fenêtre ; retour discret (ligne dans la tuile, notification brève si fenêtre masquée). L'utilisateur la complète après la session, le segment transcrit voisin fournissant le contexte. Des appuis rapprochés créent des signets distincts.
- *Note depuis un segment* : « Noter » sur une ligne du flux pré-remplit le champ avec la **citation** du segment ; l'utilisateur peut ajouter un commentaire (optionnel) avant de valider. La note reprend l'horodatage **du segment cité**, pas celui du clic.

**Extensions / exceptions**
- *1a. Note vide* (texte vide ou blanc) : ignorée silencieusement, pas d'erreur.
- *4a. Transcript non inscriptible* : la note est conservée en mémoire et restituée à l'arrêt via l'historique et l'export ; la session continue (cohérent avec le comportement live existant).
- *4b. Réunion avec distinction par locuteur* : la note est incluse dans le **tri chronologique final** (réécriture triée), entrelacée avec les lignes `Moi`/`Interlocuteurs` ou, si UC-18 actif, avec les lignes `Locuteur N`.
- *Signet hors session* (`IDLE`, dictée…) : **no-op journalisé** (cohérent BR-01), aucun effet de bord.
- *Session arrêtée entre la saisie et la validation* : la note est rattachée à la session qui vient de se terminer (dernier horodatage) plutôt que perdue.
- *Raccourci signet non enregistrable* (conflit) : signalé (log + UI), le reste de la session fonctionne normalement.

**Hors périmètre (Won't, cette itération)** : la **note vocale** (dictée au micro pendant une session) — voir FR-27.

**Règles** : BR-01 (le signet hors session est ignoré), BR-06 (les notes suivent le résultat copié/exporté, jamais injecté), BR-07 (ancrage chronologique).
**Exigences liées** : FR-23…FR-27, US-10, RE-11, PE-06, CO-01, CO-09.

---

### UC-17 — Résumer une session (live / réunion) par IA locale

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur (activation) / Système (exécution automatique) |
| **Acteur secondaire** | LLM local (cf. UC-06) |
| **Objectif** | Obtenir, à la fin de chaque session Live continu (UC-09) ou Conférence (UC-10, éventuellement avec UC-18), un **résumé de la conversation** (sujets, décisions, actions) — sans relire tout le transcript. |
| **Déclencheur** | Arrêt de la session, si `summary.enabled: true` (opt-in, écran Configuration ou `config.yaml`). |
| **Préconditions** | `summary.enabled` ; serveur LLM **local** lancé (même endpoint que UC-06, activation indépendante de `ai.enabled`) ; session terminée avec du texte. |
| **Garanties (succès)** | Le résumé est ajouté en fin de transcript (section « Résumé »), archivé dans l'historique (source `résumé live`/`résumé réunion`) et notifié. |
| **Garanties minimales** | Échec du LLM (absent, muet, timeout) : le transcript et l'historique de la session, déjà archivés, sont **intacts** ; notification d'indisponibilité ; l'application reste pleinement opérationnelle. |

**Scénario nominal**
1. L'utilisateur arrête la session ; le flux normal de fin s'exécute (export, historique, notification, retour `IDLE`).
2. Le système lance le résumé **en arrière-plan** (l'appel LLM peut durer des dizaines de secondes ; une nouvelle dictée est possible immédiatement).
3. Le transcript (tronqué début+fin au-delà de `summary.max_chars`) est envoyé au LLM **local** avec le prompt de résumé (points concis : sujets, décisions, actions).
4. Le résumé est ajouté au transcript (`# Résumé`, `## Résumé` en `.md` — après la section « Notes » d'UC-16 le cas échéant), archivé dans l'historique et notifié.

**Extensions / exceptions**
- *3a. Endpoint non local* : **refusé** par la garde commune (CO-03) — le transcript ne quitte jamais la machine ; pas de résumé.
- *3b. LLM absent / muet / réponse invalide* : notification d'indisponibilité, aucun autre effet (session déjà archivée).
- *4a. Transcript non inscriptible* : le résumé reste disponible via l'historique.
- *Fermeture de l'application pendant le résumé* : abandon silencieux (best-effort) ; la session archivée est intacte.

**Règles** : BR-06 (le résumé n'est jamais injecté).
**Exigences liées** : FR-28, RE-12, CO-01, CO-03.

---

### UC-18 — Distinguer les locuteurs individuels en réunion

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Acteurs secondaires** | Moteur Whisper, module de diarisation locale |
| **Objectif** | Identifier **chaque orateur** dans le compte rendu de réunion — qu'il parle au micro local (plusieurs personnes en salle) ou via la sortie système (plusieurs participants distants) — et non plus seulement la source audio (`Moi` / `Interlocuteurs`). |
| **Déclencheur** | Activation de `conference.speaker_diarization.enabled: true` (ou équivalent UI) avant ou pendant une session UC-10. |
| **Préconditions** | UC-10 actif ou sur le point de démarrer ; modèle de diarisation **téléchargé et en cache local** (opt-in, analogue UC-14) ; **consentement** des participants (BR-05). |
| **Garanties (succès)** | Le transcript exporté et le flux en direct affichent des lignes horodatées du type `[MM:SS] Locuteur N : …` (ou libellé personnalisé), entrelacées chronologiquement quelle que soit la provenance du son. |
| **Garanties minimales** | Si la diarisation échoue ou est indisponible, la session se poursuit avec la distinction par source (UC-10, itération 2) ; aucune perte d'audio ni d'arrêt de capture. |

> UC-18 **étend** UC-10 : il ne crée pas de nouvel état dans la machine à états (§3) et
> s'exécute pendant `CONFERENCE`. Il ne s'applique pas au mode live seul (UC-09).

**Scénario nominal (réunion hybride)**
1. L'utilisateur active la diarisation des locuteurs (`speaker_diarization.enabled: true`) et lance une réunion (UC-10).
2. Le micro local capture les voix **en salle** ; la sortie système capture les voix **à distance** (Teams, Meet…).
3. Chaque source est segmentée et transcrite comme en UC-10 ; en parallèle, un **worker dédié** applique la diarisation sur les segments de chaque source (empreintes vocales locales, sans réseau).
4. Les segments sont étiquetés par locuteur (`Locuteur 1`, `Locuteur 2`, … — numérotation stable sur la session) et **fusionnés chronologiquement** avec les segments des deux sources.
5. (Si fenêtre ouverte) le flux en direct affiche chaque ligne avec le libellé du locuteur (US-09, US-11).
6. À l'arrêt, l'export et l'historique reprennent le transcript entrelacé avec les étiquettes de locuteur.

**Variantes**
- *Plusieurs personnes au micro* : deux collègues en salle partagent le même micro → la diarisation les distingue (`Locuteur 1` / `Locuteur 2` sur la branche micro).
- *Plusieurs participants distants* : trois intervenants à l'écran → la diarisation les distingue sur la branche sortie système (`Locuteur 3` / `Locuteur 4` / `Locuteur 5`).
- *Mélange présentiel + distanciel* : les locuteurs micro et distants apparaissent dans **un seul fil chronologique** ; l'étiquette ne révèle pas la source technique, seulement l'identité vocale.
- *Renommage des locuteurs* : depuis la fenêtre, l'utilisateur remplace `Locuteur 2` par « Marie Dupont » ; le renommage s'applique rétroactivement au transcript de la session (flux, export, historique).
- *Nombre maximal de locuteurs* : `speaker_diarization.max_speakers` borne la détection (défaut documenté) pour limiter les faux positifs en salle bruyante.

**Extensions / exceptions**
- *Modèle de diarisation absent* : bannière sur le tableau de bord ou l'écran Configuration proposant un **téléchargement en un clic** (poids annoncé, progression suivie — même doctrine que UC-14 / `modeldl.py`) ; refus → repli sur distinction par source (UC-10).
- *Diarisation indisponible en exe figé sans modèle embarqué* : repli gracieux sur UC-10, message explicite (analogue UC-14).
- *Locuteur non distingué* (voix trop proches, chevauchement) : le segment est attribué au locuteur le plus probable ou regroupé sous un libellé générique (`Locuteur ?`), sans bloquer la transcription.
- *Échec du worker de diarisation* : la capture et la transcription Whisper continuent ; les segments déjà diarisés sont conservés, les suivants retombent sur l'étiquette de source (`Moi` / `Interlocuteurs`).
- *Fenêtre fermée* : diarisation, export et archivage **inchangés** (affichage en direct = confort).

**Hors périmètre (Won't, cette itération)**
- Identification nominative automatique (reconnaissance du nom prononcé ou annuaire) — seul le renommage manuel est prévu.
- Diarisation en mode live seul (UC-09) — une seule source, hors périmètre conférence.
- Envoi d'empreintes vocales ou d'audio vers un service distant — interdit (CO-01, CO-17).

**Règles** : BR-05 (consentement), BR-06 (pas d'injection), BR-08 (continuité de session en cas d'échec diarisation).
**Exigences liées** : FR-29…FR-32, US-11, US-12, RE-13, RE-14, PE-07, CO-17, CO-18, CO-19.

---

### UC-19 — Gérer le dictionnaire personnalisé

| Champ | Valeur |
|-------|--------|
| **Acteur principal** | Utilisateur |
| **Objectif** | Ajouter, modifier et supprimer les entrées du dictionnaire (termes favorisés et corrections) sans éditer le fichier à la main, et voir la prise en compte sans redémarrer. |
| **Déclencheur** | Écran « Dictionnaire » de la fenêtre ; **ou** (mode tray seul) « Ouvrir le dictionnaire » qui ouvre `dictionary.txt` dans l'éditeur par défaut. |
| **Préconditions** | App lancée. L'édition assistée requiert la fenêtre ouverte (WebView2, US-09) ; l'édition du fichier fonctionne **toujours** (repli mode tray seul, CO-08). L'**effet** à la transcription requiert `dictionary.enabled: true` (UC-04), mais l'édition reste possible même désactivé. |
| **Garanties (succès)** | Les entrées sont écrites dans `dictionary.txt` en **préservant commentaires et ordre** ; le dictionnaire est **rechargé à chaud** (prochaine dictée), sans redémarrage ni aucune sortie réseau. |
| **Garanties minimales** | En cas d'échec d'écriture (droits, fichier verrouillé), l'ancien contenu est **préservé**, une erreur est notifiée, aucune entrée n'est perdue. |

> UC-19 n'introduit **aucun état** dans la machine à états (§3) : l'édition se fait hors dictée ;
> une édition pendant un mode actif ne prend effet qu'au prochain chargement du dictionnaire.

**Scénario nominal (édition assistée par la fenêtre)**
1. L'utilisateur ouvre l'écran « Dictionnaire » ; la fenêtre liste les entrées existantes, séparées en **termes favorisés** (hotwords) et **corrections** (`mauvais => correct`), lues depuis `dictionary.txt`.
2. Il **ajoute** un terme (ex. `faster-whisper`) ou une correction (ex. `whispeurtie => Whisperty`), **modifie** ou **supprime** une entrée existante.
3. À l'enregistrement, le système réécrit `dictionary.txt` **ligne par ligne** — préservation des commentaires `#` et de l'ordre (même doctrine que `configio.py` pour `config.yaml`, sans `ruamel`) ; les lignes vides/blanches sont ignorées et les doublons dédupliqués.
4. Le dictionnaire est **rechargé** (reconstruction de l'index hotwords/corrections, comme lors d'un `apply_config`) : la prochaine dictée en bénéficie **sans relance**.
5. Une notification confirme (ex. « N termes, M corrections »).

**Variantes**
- *Édition directe du fichier (repli / mode tray seul)* : « Ouvrir le dictionnaire » ouvre `dictionary.txt` dans l'éditeur système (analogue à UC-12 pour `config.yaml`) ; la prise en compte se fait à la **relance**.

**Extensions / exceptions**
- *Entrée vide ou invalide* : ignorée silencieusement (cohérent avec `load_dictionary`), sans erreur.
- *Nature de l'entrée* : déterminée par la présence de `=>` — sans `=>` = **hotword** ; avec `=>` = **correction** (indexée en minuscules, mot entier, insensible à la casse).
- *Fichier absent* : il est créé à l'enregistrement de la première entrée.
- *2a. Échec d'écriture* (droits insuffisants, fichier verrouillé) : le contenu existant reste **inchangé**, l'erreur est notifiée ; l'édition n'affecte jamais une dictée en cours.

**Règles** : BR-03 (ordre de post-traitement inchangé), CO-01 (édition 100 % locale), CO-08 (`dictionary.txt` éditable à côté de l'exe, non embarqué).
**Exigences liées** : FR-33, FR-07, FR-17, US-05, SU-02, CO-01, CO-08.
