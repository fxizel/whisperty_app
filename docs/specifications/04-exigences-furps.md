# 04 — Exigences (FURPS+)

Les exigences sont classées selon le modèle **FURPS+** :

| Catégorie | Code | Portée |
|-----------|------|--------|
| **F**unctionality | `FR` | Ce que le système fait (fonctions, capacités, sécurité fonctionnelle). |
| **U**sability | `US` | Ergonomie, effort d'usage, retours visuels, accessibilité. |
| **R**eliability | `RE` | Robustesse, disponibilité, tolérance aux pannes, intégrité des données. |
| **P**erformance | `PE` | Latence, débit, ressources, réactivité. |
| **S**upportability | `SU` | Maintenabilité, configurabilité, testabilité, déploiement, diagnostic. |
| **+** (contraintes) | `CO` | Contraintes de conception, d'implémentation, d'interface, légales. |

Priorité **MoSCoW** : `M` Must · `S` Should · `C` Could · `W` Won't.
*Vérification* = critère d'acceptation observable. *Source* = UC et/ou référence documentaire (cf. `01`).

> La colonne *Source* indique le ou les UC **principaux** (vue indicative). La traçabilité
> bidirectionnelle **complète et faisant foi** figure dans [`05` §2–§3](05-tracabilite-et-risques.md).

---

## 1. Functionality (FR)

| ID | Exigence | Prio | Source | Vérification |
|----|----------|------|--------|--------------|
| **FR-01** | Déclencher l'enregistrement micro via un **raccourci global** configurable, selon trois modes : `toggle`, `push_to_talk`, ou double-appui (`double_tap_key`). | M | UC-01, UC-02 | Le combo configuré démarre/arrête l'enregistrement dans chaque mode ; combo invalide → repli sur `<ctrl>+<alt>+<space>`. |
| **FR-02** | Capturer le micro en **16 kHz mono float32**, en rééchantillonnant si le périphérique ne fournit pas 16 kHz. | M | UC-01 | Un micro à 44,1/48 kHz produit un flux ré-échantillonné à 16 kHz transmis au moteur. |
| **FR-03** | Transcrire l'audio **localement** via faster-whisper, modèle configurable (`base`/`small`/`medium`/`large-v3`), `beam_size` paramétrable. | M | UC-01, UC-15 | Transcription sans accès réseau (modèle en cache), conforme au modèle de `config.yaml`. *Défaut interne du code : `small` ; le `config.yaml` livré fixe `medium`.* |
| **FR-04** | Forcer la **langue** de transcription (`fr` par défaut) ou la détecter automatiquement (`language: null`). | S | UC-01 | `language: fr` force le français ; `null` déclenche l'auto-détection. |
| **FR-05** | **Injecter** le texte dans l'application active par collage presse-papiers (`paste`, défaut) ou frappe simulée (`type`). | M | UC-01 | Le texte apparaît dans la fenêtre au premier plan via la méthode configurée. |
| **FR-06** | **Restaurer** le contenu du presse-papiers après un collage (`restore_clipboard`). | S | UC-01 | Après injection en mode `paste`, l'ancien contenu du presse-papiers est rétabli si l'option est active. |
| **FR-07** | Appliquer un **dictionnaire** : *hotwords* (biais de reconnaissance) + corrections `mauvais => bon` post-transcription. | S | UC-01, UC-04 | Un hotword listé est mieux reconnu ; une correction définie est appliquée au texte final. |
| **FR-08** | Appliquer un **profil de contexte** selon l'application active au démarrage de la dictée (override `initial_prompt`/`language`/`hotwords`/`corrections`/`dictionary`). | C | UC-05 | Profils activés : l'app correspondant à un `match` applique son profil ; sinon contexte par défaut. |
| **FR-09** | **Raffiner** le texte via un LLM **local** (ponctuation, casse, fautes) sans reformuler, en option (`ai.enabled`). | C | UC-06 | IA active : le texte est corrigé par le LLM local ; IA inactive : aucun appel LLM. |
| **FR-10** | **Importer et transcrire un fichier audio** (WAV/MP3/M4A/FLAC/OGG/OPUS/WMA/AAC) ; résultat copié dans le presse-papiers et archivé. | S | UC-07 | Un fichier supporté est transcrit et copié ; le décodage n'exige pas `ffmpeg`. |
| **FR-11** | **Historiser** chaque transcription en base **SQLite locale**, avec purge automatique au-delà de `max_entries`, et exposer « copier la dernière » / « ouvrir le dossier ». | S | UC-08 | Chaque transcription est enregistrée ; au-delà de `max_entries`, les plus anciennes sont purgées. |
| **FR-12** | **Transcrire en direct** une sortie audio (loopback) en continu, écriture au fil de l'eau dans un fichier horodaté, copie + archivage à l'arrêt ; **affichage du flux en direct** dans la fenêtre si ouverte (US-09). | S | UC-09 | La capture loopback produit un transcript croissant ; à l'arrêt le texte est copié et archivé (source `live`). |
| **FR-13** | **Transcrire une réunion** en capturant micro **+** sortie système simultanément ; par défaut **distinction par locuteur** (entrelacement chronologique `Moi`/`Interlocuteurs`), sinon mixage ; export `.txt`/`.md` horodaté, **sans injection** ; **affichage du flux en direct** dans la fenêtre si ouverte (US-09). | S | UC-10 | `distinguish_speakers: true` produit des lignes `[MM:SS] Moi/Interlocuteurs` ; `false` produit une voix mixée ; un fichier export est créé. |
| **FR-14** | **Assister la réunion** : détecter les questions adressées à l'utilisateur et proposer une réponse du LLM local (copiée ou injectée selon `auto_inject`). | C | UC-11 | Question détectée → réponse générée localement et copiée (ou injectée si `auto_inject: true`). |
| **FR-15** | **Arrêter automatiquement** l'enregistrement sur silence prolongé (mode toggle) et appliquer un **garde-fou de durée maximale** (tous modes). | M | UC-03 | Après parole puis silence ≥ `silence_duration`, l'enregistrement s'arrête ; il s'arrête aussi à `max_duration`. |
| **FR-16** | Refléter l'**état** par la couleur de l'icône tray : gris=prêt, rouge=enreg., orange=transcription, bleu=live, vert=réunion, violet=assistant. | M | UC-01, UC-09…11 | Chaque transition d'état change la couleur et l'infobulle de l'icône (6 états). |
| **FR-17** | Charger l'ensemble du comportement depuis un **unique `config.yaml`** + un `dictionary.txt`. | M | UC-12, UC-13 | Toute clé documentée modifie le comportement après relance ; absence de fichier → valeurs par défaut. |
| **FR-18** | **Précharger** le modèle au démarrage (en tâche de fond) afin d'accélérer la première dictée. | S | UC-01, UC-14 | Au lancement, l'état passe brièvement en `PROCESSING` le temps du chargement, puis `IDLE`. |
| **FR-19** | Garantir un **mode exclusif** : dictée, live, réunion et assistant ne s'exécutent jamais en parallèle ; tout déclenchement concurrent est ignoré (no-op journalisé). | M | UC-01…11 | Pendant un mode actif, un déclenchement d'un autre mode est refusé avec notification/journal. |
| **FR-20** | **Notifier** l'utilisateur (best-effort) des fins d'opération, refus et rappels (consentement, copie presse-papiers, aucune parole), en français. | S | UC-01, UC-07, UC-09…11 | Chaque fin d'opération ou refus produit une notification système explicite (selon le support du backend). |
| **FR-21** | Permettre la **sélection du périphérique micro** (`audio.device` : index ou sous-chaîne de nom ; `null` = défaut système). | S | UC-01, UC-12 | Un index/nom valide route la capture vers le micro désigné ; `null` utilise le micro système. |
| **FR-22** | **Refuser** activement tout endpoint IA non local, **sans aucune émission réseau** (sécurité fonctionnelle ; complète CO-03). | M | UC-06, UC-11 | Un endpoint distant configuré est rejeté avant tout envoi ; seul un hôte local passe la garde. |

---

## 2. Usability (US)

| ID | Exigence | Prio | Source | Vérification |
|----|----------|------|--------|--------------|
| **US-01** | Le déclenchement doit être **sans friction** : un raccourci global unique, sans fenêtre à activer, dans n'importe quelle application. | M | UC-02 | La dictée se déclenche quelle que soit la fenêtre active, sans focus préalable sur Whisperty. |
| **US-02** | Toutes les actions secondaires sont accessibles depuis un **menu tray** clair (dictée, live, réunion, assistant, import, historique, config, quitter). | M | UC-07…12 | Le menu clic droit expose chaque action ; une action sans callback (fonction non configurée) est grisée, et un mode déclenché alors qu'un autre est actif est refusé par une **notification** explicite (exclusivité — FR-19/BR-01). |
| **US-03** | L'état courant est **perceptible en permanence** via la couleur de l'icône et l'infobulle. | M | UC-01 | L'utilisateur distingue prêt/enreg./transcription/live/réunion/assistant sans ouvrir de fenêtre. |
| **US-04** | Le français est **traité fidèlement** : accents et textes longs préservés (collage privilégié), vocabulaire métier via dictionnaire. | M | UC-01, UC-04 | Un texte avec é/è/à/ç et > 500 caractères s'injecte sans perte ni corruption. |
| **US-05** | La configuration est **auto-documentée** : `config.yaml` abondamment commenté sert de référence. | S | UC-12 | Chaque section/clé est commentée (rôle, valeurs admises, valeur par défaut). |
| **US-06** | Offrir des **modes de déclenchement ergonomiques** adaptés à un usage prolongé (push-to-talk, double-appui, arrêt auto sur silence). | S | UC-02, UC-03 | Au moins un mode permet de dicter de longues sessions sans clic ni arrêt manuel répété. |
| **US-07** | Émettre des **messages d'issue compréhensibles** (notifications) en cas de refus, d'absence de texte ou d'échec (ex. « activez `ai.enabled` », « aucune parole détectée »). | S | UC-07, UC-09…11 | Chaque refus/échec produit une notification explicite en français. |
| **US-08** | Fournir un **retour perceptible** au déclenchement/arrêt (icône d'état ; piste future : bip/overlay) pour un usage mains-libres prolongé (**accessibilité**). | C | UC-02 | L'utilisateur perçoit le changement d'état sans ouvrir de fenêtre ; amélioration tracée en Q-05. |
| **US-09** | **Afficher le flux de transcription en direct** dans l'interface fenêtre (tableau de bord) : en **live** (UC-09) et en **réunion** (UC-10), lorsque la fenêtre — **compagnon optionnel** du tray (WebView2/`pywebview`) — est ouverte, chaque segment transcrit s'ajoute **au fil de l'eau** à la tuile « Dernière transcription » (titre → « Transcription en direct », défilement vers le dernier segment), sans attendre l'arrêt. La fenêtre étant optionnelle (**repli tray seul** si indisponible), son absence n'altère ni la capture, ni l'export, ni l'archivage. | S | UC-09, UC-10 | Lancer un live/une réunion avec la fenêtre ouverte fait apparaître les segments un à un dans la tuile (sans attendre l'arrêt) ; en mode tray seul, capture, fichier et historique restent identiques. |

---

## 3. Reliability (RE)

| ID | Exigence | Prio | Source | Vérification |
|----|----------|------|--------|--------------|
| **RE-01** | Les **transitions d'état** sont sérialisées (verrou réentrant) et sûres face à des déclencheurs multi-threads (raccourci, tray, VAD). | M | UC-01 | Des déclenchements concurrents ne produisent jamais d'état incohérent ni de flux micro orphelin. |
| **RE-02** | **Dégradation gracieuse** sur ressource absente : micro indisponible, modèle non téléchargé, fichier introuvable → erreur explicite, retour `IDLE`, jamais de plantage. | M | UC-01, UC-07 | Couper le micro / supprimer le modèle : l'app journalise une erreur et reste opérationnelle. |
| **RE-03** | Le **garde-fou de durée** empêche tout enregistrement illimité (protection mémoire/coût). | M | UC-03 | Un enregistrement atteignant `max_duration` est arrêté automatiquement. |
| **RE-04** | L'**ordre de verrouillage** est respecté (`_lock` → `_op_lock`, jamais l'inverse ; le callback PortAudio ne prend aucun verrou) pour exclure tout interblocage. | M | R-2 (transverse — concurrence) | Aucun blocage observé lors d'arrêts concurrents ; les modes exclusifs s'arrêtent sans `join()` sous verrou. |
| **RE-05** | L'**écouteur clavier** ne gèle jamais : les traitements bloquants (arrêt PortAudio, transcription) s'exécutent hors du thread d'écoute. | M | UC-02 | Le raccourci reste réactif pendant une transcription en cours. |
| **RE-06** | Un **échec du LLM local** (timeout/erreur) n'interrompt pas le flux : le **texte brut** est conservé. | M | UC-06, UC-11 | LLM arrêté/injoignable : la dictée produit quand même le texte transcrit. |
| **RE-07** | L'accès à l'**historique SQLite** est thread-safe (accès sérialisés) et l'écriture est **non bloquante** pour le pipeline. | S | UC-08 | Aucune corruption de base ni blocage de la dictée sous écritures concurrentes. |
| **RE-08** | Les **modes loopback** (live/réunion/assistant) initialisent COM **par thread** (`com_initialized`) et s'arrêtent proprement via callback de fin (pas de `join()` sous verrou). | M | UC-09…11 | Un worker loopback ne lève pas `CO_E_NOTINITIALIZED` ; l'arrêt repasse `IDLE` sans interblocage. |
| **RE-09** | En réunion, la **perte d'une source** (micro ou sortie) n'interrompt pas la capture : l'autre source continue seule ; les reliquats sont drainés à l'arrêt. | S | UC-10 | Débrancher une source en cours : la transcription se poursuit avec la source restante. |
| **RE-10** | L'**arrêt** (`quit`) est ordonné et idempotent : micro, écouteur, modes longs (avec attente bornée), historique et tray sont fermés ; l'historique est écrit **avant** la fermeture de la base. | M | UC-08…11 | Quitter pendant une réunion : le transcript est bien archivé avant fermeture. |

---

## 4. Performance (PE)

| ID | Exigence | Prio | Source | Vérification |
|----|----------|------|--------|--------------|
| **PE-01** | La transcription doit être **exploitable sur CPU** avec le modèle livré (`medium`/`int8` dans `config.yaml`) ; l'accélération CUDA est possible pour réduire la latence. | M | UC-01, UC-15 | Une dictée courte est transcrite sur CPU sans configuration GPU ; CUDA réduit le temps observé. |
| **PE-02** | Le **préchargement** du modèle au démarrage masque le coût de chargement à la première dictée. | S | UC-01, UC-14 | La première dictée après démarrage ne paie pas le chargement initial du modèle. |
| **PE-03** | La capture est **non bloquante** et la mémoire **bornée** (streaming + garde-fou de durée, segmentation en live/réunion). | M | UC-01, UC-09, UC-10 | La RAM reste stable sur une réunion longue ; la capture n'introduit pas de gel d'UI. |
| **PE-04** | La **latence d'arrêt** des modes continus est de l'ordre de la granularité de capture (`block_duration`). | C | UC-03, UC-09 | L'arrêt d'une transcription live se matérialise en ~`block_duration` secondes. |
| **PE-05** | L'**injection** d'un texte long est fiable et rapide via le collage presse-papiers (vs frappe caractère par caractère). | S | UC-01 | Un texte long s'injecte en une opération de collage, sans saisie lettre à lettre. |

---

## 5. Supportability (SU)

| ID | Exigence | Prio | Source | Vérification |
|----|----------|------|--------|--------------|
| **SU-01** | **Configuration unique** : un seul `config.yaml` (+ `dictionary.txt`) pilote l'ensemble ; prise en compte à la relance. | M | UC-12 | Modifier une clé puis relancer modifie le comportement correspondant. |
| **SU-02** | **Personnalisation du vocabulaire** par fichier texte simple (`dictionary.txt`, une entrée par ligne, hotwords + corrections). | S | UC-04 | Ajouter une ligne au dictionnaire est pris en compte sans toucher au code. |
| **SU-03** | **Journalisation locale** exploitable (console + fichier, niveau configurable), strictement sans handler réseau. | M | UC-08, R-2 | Les logs sont écrits dans `logs/whisperty.log` ; aucun handler réseau n'est configuré. |
| **SU-04** | **Packaging** en exécutable autonome (PyInstaller onefile) et **démarrage automatique** par scripts (par utilisateur, sans droits admin). | C | UC-13 | `pyinstaller whisperty.spec` produit l'exe ; les scripts activent/désactivent l'autostart. |
| **SU-05** | **Testabilité hors-ligne** : suite de tests sans matériel ni réseau (doublures), exécutée en CI (Windows + Linux, Python 3.10→3.12) avec **seuil de couverture 80 %** et `ruff`. | M | R-5 | La CI passe sans accès réseau ni périphérique ; la couverture ≥ 80 % est vérifiée. |
| **SU-06** | **Architecture modulaire** : un module par responsabilité (recorder, transcriber, injector, tray, history, ai, profiles, live, conference, meeting…) pour faciliter l'évolution. | S | R-2, R-4 | Une fonctionnalité est localisée dans un module dédié, testable isolément. |
| **SU-07** | **Conventions de code** : commentaires/docstrings en **français**, *type hints*, `from __future__ import annotations`, gestion d'erreurs explicite. | C | R-2 | Le code respecte ces conventions (vérifiable par revue et `ruff`). |
| **SU-08** | **Diagnostic facilité** : ouverture directe de la config et du dossier d'historique depuis le tray. | C | UC-08, UC-12 | Les entrées de menu ouvrent respectivement `config.yaml` et le dossier de la base. |

---

## 6. Contraintes de conception (CO — le « + » de FURPS+)

> Les contraintes sont **non négociables** ; `CO-01` à `CO-03` matérialisent la contrainte
> cardinale de confidentialité et prévalent sur toute autre exigence en cas de conflit.

| ID | Contrainte | Prio | Source | Vérification |
|----|------------|------|--------|--------------|
| **CO-01** | **Zéro réseau à l'usage** : aucune donnée (audio/texte) ne sort de la machine ; aucune télémétrie. | M | R-1, R-2 | Capture réseau (Wireshark) vide pendant l'usage hors-ligne. |
| **CO-02** | `transcription.local_files_only: true` **par défaut** = garde **inconditionnelle** passée à `WhisperModel` ; en complément, `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` sont posés **par défaut** (`setdefault` — non écrasés s'ils sont déjà définis dans l'environnement). | M | UC-14, R-3 | Avec le modèle en cache, l'app fonctionne sans réseau ; `local_files_only` reste actif même si ces variables d'env sont surchargées. |
| **CO-03** | Le mode IA n'accepte **que des endpoints locaux** : schéma `http`/`https` **et** hôte dans {`localhost`, `127.0.0.1`, `::1`, `[::1]`, hôte vide} ; tout hôte distant est refusé. Le **suivi des redirections 3xx est bloqué** (anti-exfiltration). | M | UC-06, UC-11 | Un endpoint distant — ou une redirection vers un hôte distant — est rejeté ; seul un endpoint local passe. *(L'acceptation de l'hôte vide est tracée en Q-09.)* |
| **CO-04** | La distinction de locuteurs en réunion est **par source** et **déterministe** (pas de diarisation par modèle *gated*, tension zéro-réseau). | M | UC-10 | Aucun modèle de diarisation réseau n'est requis ; la distinction repose sur la source (micro/sortie). |
| **CO-05** | Le **loopback** passe par `soundcard` (WASAPI) — `sounddevice`/PortAudio n'expose pas le loopback. | M | UC-09, UC-10 | Sans `soundcard`, les modes loopback sont indisponibles (liste de sorties vide, démarrage refusé). |
| **CO-06** | Tout thread worker appelant `soundcard` **doit** s'envelopper dans `com_initialized()` (COM par thread), sinon `CO_E_NOTINITIALIZED`. | M | UC-09, UC-10, UC-11 | Les workers loopback initialisent COM ; aucun `0x800401F0` observé. |
| **CO-07** | **Pas de dépendance `ffmpeg`** : le décodage audio passe par PyAV (embarqué par faster-whisper). | M | UC-07 | L'import de fichiers fonctionne sans `ffmpeg` installé. |
| **CO-08** | `config.yaml` et `dictionary.txt` **ne sont pas embarqués** dans l'exe (éditables, déposés à côté). | M | UC-13 | Les fichiers à côté de l'exe sont pris en compte ; absents → valeurs par défaut. |
| **CO-09** | Le raccourci par défaut **n'utilise pas `Win+Space`** (réservé par Windows) ; combo configurable, repli si invalide. | M | UC-02 | Le défaut est `<ctrl>+<alt>+<space>` ; un combo invalide retombe dessus. |
| **CO-10** | Le profil de contexte est résolu sur l'application **au démarrage** de la dictée (= cible de l'injection), pas à l'arrêt. | S | UC-05 | Changer de fenêtre après le démarrage ne change pas le profil appliqué. |
| **CO-11** | Le packaging utilise **`upx=False`** (UPX corrompt les DLL natives). | M | UC-13 | L'exe produit n'est pas compressé UPX et démarre correctement. |
| **CO-12** | Le moteur supporte **CPU et CUDA uniquement** — **pas de DirectML** ; AMD/Intel restent en CPU `int8`. | M | UC-15 | Aucune option DirectML n'est proposée ; CUDA requiert les paquets NVIDIA. |
| **CO-13** | L'audio fourni au moteur est **16 kHz mono float32** ; le rééchantillonnage utilise `soxr` (repli interpolation NumPy). | M | UC-01 | Une entrée non-16 kHz est convertie avant transcription. |
| **CO-14** | Le module d'injection est nommé **`injector`** (et non `typer`) pour ne pas masquer la lib PyPI `typer` (dépendance transitive). | S | R-2 | Aucun module `typer.py` dans le paquet ; l'import de la lib `typer` reste possible. |
| **CO-15** | **Plateforme cible** : Windows 10/11 64 bits, **Python 3.10+** (vérifié jusqu'à 3.14.3, roues binaires disponibles, aucune compilation requise). | M | R-1 | L'installation via `pip install -r requirements.txt` réussit sans compilateur. |
| **CO-16** | **Conformité / éthique** : l'enregistrement de réunion suppose le **consentement** des participants (responsabilité de l'utilisateur, rappelé par l'app). | M | UC-10, UC-11 | Le démarrage du mode réunion affiche un rappel de consentement. |

---

## 7. Règles de gestion (BR)

| ID | Règle | Source |
|----|-------|--------|
| **BR-01** | **Exclusivité des modes** : un seul état actif à la fois ; tout déclenchement reçu hors de l'état autorisé est **ignoré** (no-op journalisé), jamais mis en file d'attente. | UC-01…11, FR-19 |
| **BR-02** | **Langue par défaut** : le français (`fr`) est forcé par défaut ; l'auto-détection n'est active que si `language: null`. | UC-01, FR-04 |
| **BR-03** | **Ordre de post-traitement** : transcription → corrections du dictionnaire → raffinage IA local (si activé) → injection/copie → archivage historique. | UC-01, UC-04, UC-06 |
| **BR-04** | **Collage privilégié** : la méthode d'injection par défaut est le presse-papiers (`paste`/Ctrl+V), plus fiable pour les accents et les textes longs ; la frappe (`type`) est un repli. | UC-01, FR-05 |
| **BR-05** | **Consentement réunion** : avant tout enregistrement de réunion (UC-10/UC-11), le consentement des participants est requis ; l'application le rappelle mais n'en est pas garante. | UC-10, UC-11, CO-16 |
| **BR-06** | **Non-injection des modes de capture passive** : import de fichier, live et réunion ne s'**injectent pas** (cible ambiguë) — le résultat est copié/exporté et archivé. | UC-07, UC-09, UC-10 |
| **BR-07** | **Préconditions de l'assistant** : l'assistant de réunion exige `ai.enabled: true` **et** `meeting.user_name` non vide ; à défaut il est refusé avec un message explicite. | UC-11 |
