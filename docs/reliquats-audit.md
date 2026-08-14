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

Rapport d'audit complet : https://claude.ai/code/artifact/4af39571-1f37-443c-8529-5df47f359374
