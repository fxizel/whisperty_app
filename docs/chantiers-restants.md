# Chantiers restants — suite de l'audit du 13 août 2026

Document de passation : les trois évolutions restantes du rapport d'audit, avec le
contexte nécessaire pour les reprendre dans une session dédiée. Tout le reste du
rapport (correctifs, durcissements, outillage, six pistes produit) est livré —
voir `CHANGELOG.md` (section « Non publié ») et les commits `affcb4a` → `d6af9ae`.

**Prérequis de lecture avant de commencer** : `CLAUDE.md` (sections « Concurrence »
et « Décisions d'architecture »), en particulier la contrainte cardinale zéro-réseau
et la doctrine opt-in de `modeldl.py`/`cuda.py`. Les relectures `concurrency-reviewer`
et `privacy-auditor` (agents du projet) sont à lancer après toute modification des
fichiers qu'elles couvrent.

---

## 1. Renommage post-session des locuteurs (FR-31 complet)

**Valeur / effort** : moyenne / moyen. **Persona** : Léa (P-05).

**Contexte.** En réunion diarisée (UC-18), les segments sont stockés en mémoire avec
des CLÉS de locuteur (`spk:N`) et les libellés sont résolus au rendu
(`conference._label_for`), ce qui rend le renommage rétroactif PENDANT la session
(`rename_speaker` → `render_lines()`). Mais à l'arrêt, l'export (`_rewrite_sorted`)
et l'entrée d'historique sont écrits avec les libellés RENDUS : les clés sont
perdues, le renommage après coup est impossible. FR-31 a été reformulée en ce sens
(`docs/specifications/04-exigences-furps.md`) — ce chantier lève la restriction.

**Design proposé.**
- Persister la structure de session : soit une colonne `payload` JSON dans
  `transcriptions` (liste `(start, key, text)` + registre `{key: label}`), soit une
  table dédiée `session_segments`. Migration de schéma OBLIGATOIREMENT versionnée :
  introduire `PRAGMA user_version` dans `history.py` (aujourd'hui absent) avec
  migrations incrémentales — c'était aussi une recommandation de l'audit.
- L'index FTS5 (`transcriptions_fts`, synchronisé par triggers) indexe la colonne
  `text` : un renommage post-session qui réécrit `text` doit passer par
  UPDATE + triggers FTS (ajouter un trigger `AFTER UPDATE` — il n'existe pas
  aujourd'hui, seuls INSERT/DELETE sont couverts).
- UI : dans le détail d'une entrée d'historique de source « réunion », lister les
  locuteurs (depuis le payload) avec champs de renommage (réutiliser le pattern du
  panneau live, `web/app.js` § speakers). À l'enregistrement : re-rendre le texte
  depuis les clés, mettre à jour l'entrée d'historique ET réécrire le fichier
  transcript exporté s'il existe encore (chemin à persister aussi — attention, il
  peut avoir été déplacé/supprimé : dégrader proprement).
- `History` reste never-fail (`sqlite3.Error, OSError` capturés partout).

**Critères d'acceptation.** Renommer « Locuteur 2 » en « Marie » depuis l'écran
Historique met à jour l'entrée archivée et le fichier `.txt`/`.md` exporté ;
la recherche FTS retrouve « Marie » ensuite ; une base ancienne (sans payload)
s'ouvre sans erreur et affiche ses entrées normalement.

---

## 2. Préréglages de performance (écran Configuration)

**Valeur / effort** : moyenne / faible-moyen. **Personas** : Camille (P-01), P-06.
**Répond à** Q-07 et RSK-02 (latence perçue selon le matériel).

**Contexte.** L'écran Configuration expose déjà taille de modèle, device et
compute_type séparément. L'utilisateur non technique ne sait pas les combiner.

**Design proposé.**
- Trois préréglages cliquables au-dessus des champs existants :
  « Rapide » (base + int8), « Équilibré » (medium + int8), « Précis » (large-v3 +
  int8, ou float16 si CUDA actif). Un clic remplit les champs existants côté JS
  (`ui.cfg.model`/`device`/`compute`), l'enregistrement passe par le circuit
  actuel (`apply_config_from_gui`) — AUCUNE nouvelle clé de config nécessaire.
- Bench local optionnel : bouton « Tester sur ce poste » qui transcrit une phrase
  témoin et affiche la durée. Contraintes fortes : l'audio témoin doit être
  généré/embarqué LOCALEMENT (pas de téléchargement — un petit WAV de parole
  enregistré une fois et embarqué dans `whisperty/web/` ou `whisperty/assets/`
  passe par le spec PyInstaller, cf. datas), et la transcription de bench doit
  passer par la machine à états (état PROCESSING, mode exclusif) ou par un
  chargement isolé — NE PAS transcrire en parallèle d'une dictée. La progression
  suit le modèle polling existant (cf. `gpu_status`/`model_status`).
- Attention au contrat taille↔chemin (`modeldl.model_size_name`, 3 endroits
  documentés dans CLAUDE.md) : les préréglages raisonnent en TAILLES.

**Critères d'acceptation.** Un clic sur un préréglage remplit les champs et
`Enregistrer` applique à chaud comme aujourd'hui ; le bench affiche une durée en
secondes sans jamais toucher au réseau ni casser une dictée en cours.

---

## 3. Backend de diarisation ONNX hors-ligne (CO-19)

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
