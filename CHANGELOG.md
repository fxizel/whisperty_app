# Journal des modifications

Toutes les évolutions notables de Whisperty sont documentées ici. Le format s'inspire de
[Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent le
[versionnage sémantique](https://semver.org/lang/fr/).

## [0.2.0] — 2026-07-03

### Ajouté

- **Téléchargement guidé du modèle** — si le modèle Whisper n'est pas installé (installeur
  léger `-NoModel`, taille changée, cache vide en mode hors-ligne), le tableau de bord
  affiche désormais une bannière qui propose de le télécharger **en un clic** (poids
  annoncé, progression suivie). Le modèle est installé dans `models/` à côté de la
  configuration, qui repasse automatiquement en `local_files_only: true` — le
  téléchargement, explicitement déclenché, reste la seule exception réseau du projet.
  Fini la modification manuelle de `config.yaml` décrite dans l'ancien démarrage rapide.
- **Erreurs et évènements enfin visibles** — micro inaccessible, modèle absent, échec de
  transcription, fin de session live/réunion, résumé prêt… sont maintenant signalés par
  une notification Windows **et** un toast dans la fenêtre, au lieu de finir uniquement
  dans les fichiers de logs (l'application semblait « muette » en cas de problème).
- **Instance unique** — relancer Whisperty (icône du menu Démarrer alors que l'app tourne
  déjà dans la zone de notification) **réaffiche la fenêtre existante** au lieu de créer
  un doublon qui se disputerait le raccourci global et le micro.
- **Premier lancement plus accueillant** — le préchargement du modèle s'affiche comme
  « Chargement du modèle… » (au lieu d'un « Transcription… » trompeur) ; quand l'historique
  est vide, la tuile principale explique le premier geste (raccourci réel mis en évidence) ;
  la première réduction dans la zone de notification est signalée (« Whisperty reste
  actif en arrière-plan »).

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

- **Installeur et build de déploiement** — la mise à jour ferme proprement l'instance en
  cours avant de copier les fichiers (plus de blocage « fichiers en cours d'utilisation ») ;
  si WebView2 manque, l'installeur propose d'**ouvrir la page de téléchargement** au lieu
  d'afficher une URL à recopier ; la configuration expédiée revient à des défauts neutres
  (CPU, IA locale et résumé désactivés — opt-in documenté), indépendamment des réglages du
  poste de build. L'écran Configuration protège par ailleurs un modèle bundlé : enregistrer
  sans changer de taille ne remplace plus le chemin local par un nom de taille à
  re-télécharger.
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
