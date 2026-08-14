# Chantiers restants — suite de l'audit du 13 août 2026

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
> `bench_status`). Voir `CHANGELOG.md`.

**Prérequis de lecture avant de commencer** : `CLAUDE.md` (sections « Concurrence »
et « Décisions d'architecture »), en particulier la contrainte cardinale zéro-réseau
et la doctrine opt-in de `modeldl.py`/`cuda.py`. Les relectures `concurrency-reviewer`
et `privacy-auditor` (agents du projet) sont à lancer après toute modification des
fichiers qu'elles couvrent.

---

## 1. Backend de diarisation ONNX hors-ligne (CO-19)

**Valeur / effort** : moyenne-forte / élevé. Le plus gros chantier — à faire en
dernier, dans une session dédiée.

**Contexte.** La diarisation intégrée est une empreinte MFCC pur NumPy
(`diarization.py`), 100 % locale, sans modèle. Sa précision est limitée (voix
nettement différentes). L'embedder est ENFICHABLE : `Diarizer(embed_fn=…)` accepte
une fonction `(audio_16k_float32) -> np.ndarray` L2-normalisée. CO-19
(`docs/specifications/04-exigences-furps.md`) décrit ce backend comme optionnel
futur ; la note d'UC-18 (`03-cas-utilisation.md`) aussi.

**Design proposé.**
- Modèle d'empreinte vocale ONNX (ex. famille speaker-embedding type ECAPA/campplus
  exportée ONNX — vérifier LICENCE et disponibilité sur HF sans gating).
  `onnxruntime` est DÉJÀ une dépendance transitive de faster-whisper (VAD Silero),
  donc zéro dépendance nouvelle — mais vérifier qu'il est collecté dans l'exe figé
  (spec PyInstaller) et rester sur le CPU EP (pas de DirectML/CUDA EP).
- Téléchargement du modèle : DOCTRINE `modeldl.py` À L'IDENTIQUE (opt-in, bannière
  ou bouton explicite, progression par polling, jamais silencieux, matérialisé dans
  `models/` à côté de la config, garde hors-ligne levée le temps du téléchargement
  puis reposée de façon déterministe — cf. le correctif récent de `modeldl._run`).
- Config : `conference.speaker_diarization.backend: mfcc | onnx` (+ chemin du
  modèle), défaut `mfcc` (rien à télécharger). Échec de chargement ONNX → repli
  MFCC journalisé + notification (`_notify_user`), jamais bloquant (BR-08/RE-13).
- Concurrence : l'empreinte reste calculée dans le worker `_diar_loop` (RE-14),
  qui reçoit déjà file/diariseur/jeton de génération en arguments — passer le
  backend au constructeur du `Diarizer` de session, rien d'autre à changer.
- Confidentialité : lancer `privacy-auditor` sur le diff (nouveau chemin de
  téléchargement + inférence locale) ; télémétrie onnxruntime déjà coupée
  (`transcriber._disable_ort_telemetry` — vérifier qu'elle couvre aussi ce chemin
  si onnxruntime est importé par `diarization.py` avant `transcriber`).

**Critères d'acceptation.** `backend: onnx` sans modèle → proposition de
téléchargement opt-in, puis fonctionnement 100 % hors-ligne ; voix proches mieux
séparées que le MFCC sur un test manuel ; `backend: mfcc` (défaut) inchangé ;
exe figé fonctionnel avec et sans le modèle.

---

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
