# 01 — Introduction & contexte

## 1. Objet du document

Ce document pose le cadre des spécifications de **Whisperty** : son objet, son périmètre, le
vocabulaire employé, la vision produit et les contraintes structurantes. Il est le préalable
à la lecture des personas, cas d'utilisation et exigences.

## 2. Périmètre (*scope*)

### 2.1 Dans le périmètre

Whisperty est une application **de bureau Windows 10/11** offrant :

- la **dictée vocale** transcrite localement et injectée dans l'application active ;
- des fonctions connexes de transcription (import de fichiers, capture d'une sortie audio en
  direct, mode réunion) et de productivité (historique, profils de contexte, raffinage IA
  local, assistant de réunion).

L'ensemble fonctionne **sans aucun envoi de données hors de la machine** (cf. §5).

### 2.2 Hors périmètre

- Toute plateforme non-Windows (macOS, Linux, mobile, web) — *Won't*.
- Tout service cloud, compte utilisateur, synchronisation ou télémétrie — *interdit par
  conception* (cf. contrainte cardinale).
- La diarisation par modèle (identification de locuteurs individuels via pyannote) — écartée
  pour cause de tension avec le zéro-réseau (modèles *gated*) ; au mieux une option future
  hors-ligne désactivée par défaut.
- L'accélération GPU sur AMD/Intel (DirectML) — non supportée par le moteur (CTranslate2).

## 3. Glossaire

| Terme | Définition |
|-------|------------|
| **Dictée** | Cycle raccourci → enregistrement micro → transcription → injection dans l'app active. |
| **Injection** | Insertion du texte transcrit dans l'application au premier plan (collage `Ctrl+V` ou frappe simulée). |
| **faster-whisper** | Implémentation optimisée (CTranslate2) du modèle Whisper d'OpenAI, exécutée localement. |
| **Hotword** | Terme « biaisé » fourni au modèle pour favoriser sa reconnaissance (vocabulaire métier). |
| **VAD** | *Voice Activity Detection* — ici un détecteur de parole simple basé sur le niveau RMS du signal. |
| **Loopback** | Capture de ce qui *sort* d'un périphérique audio (ce que l'on entend), via WASAPI / `soundcard`. |
| **Toggle / Push-to-talk** | Modes de déclenchement : un appui démarre/arrête, *vs* maintien de la touche pendant la parole. |
| **Profil de contexte** | Jeu de réglages (prompt, langue, dictionnaire) appliqué selon l'application active au démarrage de la dictée. |
| **LLM local** | Modèle de langage servi sur la machine (Ollama, LM Studio…) via un endpoint compatible OpenAI, **localhost uniquement**. |
| **Tray** | Icône de la zone de notification Windows, point d'entrée des actions et indicateur d'état. |
| **Mode exclusif** | Mode (live, réunion, assistant) qui monopolise l'application : aucune autre opération concurrente. |
| **Itération 1 / 2 (réunion)** | Mixage des sources en une voix (it. 1) *vs* distinction par source entrelacée chronologiquement (it. 2). |

## 4. Vision & proposition de valeur

### 4.1 Énoncé du problème

Les solutions de dictée vocale grand public (et certaines solutions « locales » réputées,
type Superwhisper sur macOS) **transmettent la voix ou le texte à des serveurs distants**, ou
ne sont pas disponibles sous Windows. Pour des professionnels manipulant des informations
**sensibles ou confidentielles** (énergie, IT, juridique, santé), cette fuite de données est
rédhibitoire.

### 4.2 Proposition de valeur

> *« Appuyez sur un raccourci, parlez, relâchez : votre voix est transcrite **sur votre
> machine** puis insérée dans l'application active — sans qu'aucune donnée ne quitte
> l'ordinateur. »*

Whisperty combine : confidentialité **vérifiable** (zéro réseau), **ubiquité** (injection
*system-wide* dans n'importe quelle application Windows), **adaptation au français** (collage
fiable pour les accents, dictionnaire métier, profils) et **absence de friction** (une icône,
un raccourci, un fichier de configuration).

### 4.3 Positionnement

Alternative **libre, hackable et multi-Windows** à Superwhisper, sans cloud, sans compte, sans
abonnement et sans télémétrie.

## 5. Contrainte cardinale — Confidentialité (zéro réseau)

C'est **la** contrainte structurante, qui surplombe toutes les exigences :

> **Aucune donnée (audio ou texte) ne sort de la machine.**

Déclinaison :

- Aucun appel réseau à l'usage, aucune télémétrie ; journalisation **strictement locale**.
- **Seule exception tolérée** : le *premier* téléchargement du modèle Whisper. Ensuite,
  `transcription.local_files_only: true` (le **défaut**) active le fonctionnement hors-ligne :
  `local_files_only` est passé **inconditionnellement** à `WhisperModel`, et
  `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` sont posés **par défaut** (`setdefault`, non écrasés
  s'ils existent déjà dans l'environnement). Comportement vérifiable à l'analyseur réseau.
- Le **mode IA** n'accepte que des endpoints **locaux** (`localhost`, `127.0.0.1`, `::1`) ;
  tout hôte distant est **refusé par conception**.

Toute dépendance ou tout appel réseau qui violerait cela **doit être signalé, jamais
introduit silencieusement**.

## 6. Hypothèses

| # | Hypothèse |
|---|-----------|
| H-01 | L'utilisateur dispose d'un PC Windows 10/11 64 bits avec un micro autorisé dans les paramètres de confidentialité. |
| H-02 | Le premier lancement (téléchargement du modèle) dispose d'un accès réseau ponctuel ; ensuite l'usage est hors-ligne. |
| H-03 | Le CPU est suffisant pour le modèle visé (le `config.yaml` livré fixe `medium`/`int8` ; défaut interne du code : `small`) ; sinon l'utilisateur rétrograde le modèle ou active CUDA. |
| H-04 | Pour les modes IA / assistant de réunion, un serveur LLM local (Ollama, LM Studio…) est installé et lancé par l'utilisateur. |
| H-05 | Pour le loopback (live, réunion, assistant), le paquet `soundcard` est installé (WASAPI loopback). |
| H-06 | L'utilisateur est responsable du **consentement** des participants avant tout enregistrement de réunion. |

## 7. Contraintes (synthèse — détaillées en `CO-xx`)

- **Plateforme** : Windows 10/11 uniquement ; Python 3.10+ (vérifié jusqu'à 3.14.3).
- **Audio** : Whisper exige 16 kHz mono float32 (rééchantillonnage automatique sinon).
- **GPU** : CPU et CUDA uniquement (pas de DirectML pour AMD/Intel).
- **Dépendances** : pas de `ffmpeg` (PyAV embarqué) ; `soundcard` requis pour le loopback.
- **Packaging** : `config.yaml` / `dictionary.txt` non embarqués (éditables, déposés à côté de l'exe).

## 8. Références

| Réf. | Source |
|------|--------|
| R-1 | `README.md` — présentation, fonctionnalités, démarrage, configuration. |
| R-2 | `CLAUDE.md` — décisions d'architecture, règles de concurrence, contraintes. |
| R-3 | `config.yaml` — surface de configuration commentée (référence la plus à jour). |
| R-4 | Code source `whisperty/` — comportement effectif (machine à états `app.py`, `tray.py`). |
| R-5 | `tests/` + `.github/workflows/ci.yml` — comportements vérifiés, seuil de couverture. |
