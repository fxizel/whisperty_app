# Reliquats de l'audit du 13 août 2026

Les trois chantiers de ce document (renommage post-session des locuteurs, préréglages
de performance, backend de diarisation ONNX) sont **livrés** — voir `CHANGELOG.md`
(section « Non publié ») et l'historique Git. Les points « au clavier » (fichier de
maquettage à la racine, métadonnées dans les journaux, course d'affichage au renommage
en session) sont livrés à leur tour. Ne reste que ce qui demande un lancement réel.
Rien n'est bloquant.

Rappel avant toute modification : `CLAUDE.md` (sections « Concurrence » et « Décisions
d'architecture »), en particulier la contrainte cardinale zéro-réseau. Les relectures
`concurrency-reviewer` et `privacy-auditor` (agents du projet) sont à lancer après
toute modification des fichiers qu'elles couvrent.

## À vérifier de visu (demande un lancement réel)

- **CSP de la fenêtre** : valider visuellement `whisperty/web/index.html` au prochain
  lancement (le pont pywebview passe par postMessage natif et devrait cohabiter avec
  la balise `<meta http-equiv="Content-Security-Policy" …>`). Si l'écran reste vide,
  retirer la balise et le signaler.
- **Bips de dictée** : écouter les tonalités (`feedback.py`, `_TONES`) et ajuster
  fréquences et durées au goût.
- **Renommage pendant une réunion** : vérifier au passage que la tuile « Transcription
  en direct » reste cohérente quand on renomme un locuteur alors que les segments
  continuent d'arriver (auto-réparation au segment suivant, cf. `_on_conference_segment`).

## Connus, non corrigés (relevés par les relectures, hors périmètre de l'audit)

- **`_session_gen` : contrôle-puis-agit** (`conference.py`, `_store_and_write`). Le
  jeton de génération est comparé HORS `_note_lock`, et `start()` réinitialise
  `_segments`/`_segments_rev`/`_session_gen` sans verrou. Un `_diar_loop` orphelin
  (join expiré) déschédulé juste après un contrôle réussi peut donc insérer dans les
  `_segments` de la session SUIVANTE. Fenêtre de quelques bytecodes, pré-existant.
  Correction connue : déplacer le contrôle de `gen` DANS le `with self._note_lock` qui
  fait l'insertion, et envelopper le bloc de reset de `start()` dans ce même verrou
  feuille (aucun autre verrou n'est tenu à cet endroit — `conference.start()` est
  appelé hors `_lock` — donc aucune imbrication).
- **Free-threading**. L'argument de sûreté de la lecture non verrouillée de
  `conference.segments_rev()` (cf. `CLAUDE.md`, section Concurrence) s'appuie sur le
  GIL. Sans lui, cette lecture n'est appariée à aucune barrière avec le relâchement de
  `_note_lock` de l'écrivain. Sans objet sur les roues CPython visées — à reprendre si
  un build free-threaded devient une cible.

Rapport d'audit complet : https://claude.ai/code/artifact/4af39571-1f37-443c-8529-5df47f359374
