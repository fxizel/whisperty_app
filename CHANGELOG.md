# Journal des modifications

Toutes les évolutions notables de Whisperty sont documentées ici. Le format s'inspire de
[Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent le
[versionnage sémantique](https://semver.org/lang/fr/).

## [0.2.0] — 2026-07-03

### Ajouté

- **Notes en session (Live continu / Conférence)** — pendant une transcription live ou une
  réunion, il est désormais possible de prendre des notes personnelles horodatées sans
  interrompre la capture :
  - un champ « Ajouter une note » sous la tuile de transcription en direct (validation par
    Entrée ou bouton) ;
  - une action « Noter » au survol d'un segment du flux, qui pré-remplit une citation ancrée
    à l'horodatage exact de ce segment ;
  - un **raccourci global de signet** (`notes.bookmark_hotkey`, configurable dans
    `config.yaml`), qui pose une note horodatée même sans focus sur la fenêtre — utile en
    pleine réunion.
  - Les notes sont entrelacées chronologiquement dans le transcript, visuellement
    distinctes des segments transcrits, récapitulées en fin de fichier (section
    « Notes »), et incluses dans l'historique et la copie de fin de session.
- **Résumé de fin de session par IA locale** — en option (`summary.enabled`, décoché par
  défaut), Whisperty peut générer un résumé (sujets abordés, décisions, actions) à l'arrêt
  d'un live ou d'une réunion, via le **même serveur LLM local** que le raffinage de dictée
  (Ollama, LM Studio…). Le résumé est produit en arrière-plan sans bloquer l'application,
  puis ajouté au transcript et archivé dans l'historique. Comme pour le raffinage, tout
  endpoint distant est refusé et un échec du LLM n'affecte jamais la session déjà archivée.
  Réglable depuis l'écran Configuration (accordéon « IA locale ») ou `config.yaml`.
- **Accélération GPU NVIDIA (CUDA)** — détection du GPU et des composants (cuBLAS/cuDNN),
  et installation **opt-in** de ces composants depuis l'écran Configuration (~1,3 Go, seul
  appel réseau explicite, comme le téléchargement du modèle). Repli automatique et
  silencieux sur CPU `int8` si le GPU ou les composants sont absents.

### Modifié

- **Robustesse de la capture micro** — les erreurs de périphérique (nom/index invalide,
  débranchement en cours d'enregistrement) sont maintenant absorbées proprement : l'état
  interne est toujours réinitialisé, ce qui évite qu'un enregistrement reste bloqué
  « en cours » après un incident matériel.
- **Import de fichier audio en version installée** — correction d'une régression qui
  empêchait le sélecteur de fichier de s'ouvrir dans l'exécutable packagé (`tkinter`
  manquant dans le build figé).

### Retiré

- **Assistant de réunion** (détection de questions et réponses automatiques par IA pendant
  une réunion) — retiré de cette version. Le mode Conférence conserve la capture
  micro + sortie système, la distinction par locuteur et l'export horodaté.

## [0.1.0] — 2026-06-22

Première version documentée : dictée vocale locale (raccourci global, dictionnaire,
profils de contexte, raffinage par IA locale optionnelle), historique SQLite, import de
fichiers audio, interface fenêtre (WebView2), transcription live d'une sortie audio, mode
réunion (micro + sortie système, distinction par locuteur), packaging PyInstaller et
installeur Windows par utilisateur.
