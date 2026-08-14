# Chantiers restants — suite de l'audit du 13 août 2026

> **Les trois chantiers de ce document sont LIVRÉS** (13-14 août 2026). Il ne reste
> que les reliquats mineurs listés en fin de page. Voir `CHANGELOG.md`.

Document de passation : les évolutions restantes du rapport d'audit, avec le
contexte nécessaire pour les reprendre dans une session dédiée. Tout le reste du
rapport (correctifs, durcissements, outillage, six pistes produit) est livré —
voir `CHANGELOG.md` (section « Non publié ») et les commits `affcb4a` → `d6af9ae`.

> **Livré depuis** : le chantier « Renommage post-session des locuteurs » (FR-31
> complet) a été réalisé le 13 août 2026 — migration versionnée de la base
> (`PRAGMA user_version`), colonne `payload` (structure de session), trigger FTS
> `AFTER UPDATE`, `WhispertyApp.rename_history_speaker`, panneau de renommage dans
> le détail Historique. Le chantier « Préréglages de performance » a été réalisé le
> 14 août 2026 — préréglages Rapide/Équilibré/Précis (clé `compute` ajoutée au
> contrat get_config/saveConfig/apply_config_from_gui) et bench local « Tester sur
> ce poste » (audio témoin généré en pur NumPy, `transcriber.bench_audio` +
> `transcribe_bench` sans VAD, mode exclusif via la machine à états, polling
> `bench_status`). Le chantier « Backend de diarisation ONNX hors-ligne » (CO-19) a été
> réalisé le 14 août 2026 — `diarization.OnnxEmbedder` + `fbank_features` (kaldi, pur
> NumPy), CPU EP imposé, modèle WeSpeaker ResNet34-LM téléchargé en opt-in
> (`modeldl.start_embedding_download`), sélecteur de précision dans l'écran
> Configuration, repli MFCC notifié, attribution dans `NOTICE.md`. Voir `CHANGELOG.md`.

**Prérequis de lecture avant de commencer** : `CLAUDE.md` (sections « Concurrence »
et « Décisions d'architecture »), en particulier la contrainte cardinale zéro-réseau
et la doctrine opt-in de `modeldl.py`/`cuda.py`. Les relectures `concurrency-reviewer`
et `privacy-auditor` (agents du projet) sont à lancer après toute modification des
fichiers qu'elles couvrent.

## Divers (reliquats mineurs de l'audit, à glisser dans n'importe quelle session)

- **Valider visuellement la CSP** de `whisperty/web/index.html` au prochain
  lancement de la fenêtre (le pont pywebview passe par postMessage natif et devrait
  cohabiter ; si l'écran reste vide, retirer la balise `<meta http-equiv=
  "Content-Security-Policy" …>` et le signaler).
- **Écouter les bips** de dictée (`feedback.py`, `_TONES`) et ajuster fréquences/
  durées au goût.
- **`Whisperty Logo.dc.html`** (racine) : maquette de dev référençant Google Fonts —
  à supprimer ou déplacer hors du dépôt (jamais expédiée, mais en tension avec la
  doctrine zéro-CDN).
- **Métadonnées dans les logs** (INFO) : nom du fichier audio importé
  (`app.py`, `_process_file`) et entrée de profil malformée en `%r`
  (`config.py`) — à rétrograder si le durcissement RGPD se poursuit.
- **Renommage live pendant flux** : course d'affichage bénigne documentée par
  l'audit (ligne perdue/dupliquée dans la tuile, export intact) — auto-réparation
  possible en ré-émettant depuis `render_lines()` au segment suivant.

Rapport d'audit complet : https://claude.ai/code/artifact/4af39571-1f37-443c-8529-5df47f359374
