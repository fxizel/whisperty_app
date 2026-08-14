# Reliquats de l'audit du 13 août 2026

Les trois chantiers de ce document (renommage post-session des locuteurs, préréglages
de performance, backend de diarisation ONNX) sont **livrés** — voir `CHANGELOG.md`
(section « Non publié ») et l'historique Git. Ne restent que les points mineurs
ci-dessous, à glisser dans n'importe quelle session. Rien n'est bloquant.

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

## À traiter au clavier

- **`Whisperty Logo.dc.html`** (racine) : maquette de dev référençant Google Fonts, à
  supprimer ou déplacer hors du dépôt. Jamais expédiée, mais en tension avec la
  doctrine zéro-CDN.
- **Métadonnées dans les logs** (niveau INFO) : nom du fichier audio importé
  (`app.py`, `_process_file`) et entrée de profil malformée en `%r` (`config.py`) — à
  rétrograder en DEBUG si le durcissement RGPD se poursuit.
- **Renommage live pendant flux** : course d'affichage bénigne relevée par l'audit
  (une ligne perdue ou dupliquée dans la tuile, export intact). Auto-réparation
  possible en ré-émettant depuis `render_lines()` au segment suivant.

Rapport d'audit complet : https://claude.ai/code/artifact/4af39571-1f37-443c-8529-5df47f359374
