# Audit du 13 août 2026 — clôture

Tous les reliquats de l'audit sont **traités**. Voir `CHANGELOG.md` (section « Non
publié ») et l'historique Git pour le détail.

- Les trois chantiers (renommage post-session des locuteurs, préréglages de performance,
  backend de diarisation ONNX) ont été livrés en premier.
- Les points « au clavier » ont suivi : retrait du fichier de maquettage à la racine,
  métadonnées personnelles hors des journaux, course d'affichage au renommage en session
  (US-12, avec les correctifs des trois relectures `concurrency-reviewer`).
- Les vérifications qui demandaient un lancement réel ont été faites et sont
  concluantes : CSP de la fenêtre (la balise cohabite avec le pont pywebview), bips de
  dictée, cohérence de la tuile « Transcription en direct » lors d'un renommage de
  locuteur en cours de réunion.

Ce document ne conserve donc que les deux points ci-dessous, hors périmètre de l'audit,
pour ne pas les perdre. Aucun n'est bloquant.

Rappel avant toute modification : `CLAUDE.md` (sections « Concurrence » et « Décisions
d'architecture »), en particulier la contrainte cardinale zéro-réseau. Les relectures
`concurrency-reviewer` et `privacy-auditor` (agents du projet) sont à lancer après
toute modification des fichiers qu'elles couvrent.

## Connus, non corrigés

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
