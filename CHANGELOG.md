# Journal des modifications

Toutes les évolutions notables de Whisperty sont documentées ici. Le format s'inspire de
[Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; les versions suivent le
[versionnage sémantique](https://semver.org/lang/fr/).

## [1.0.0] — 2026-08-14

Première version stable. Le périmètre fonctionnel du dossier de spécifications
(UC-01 à UC-19) est couvert ; la contrainte cardinale — aucune donnée ne quitte la
machine — est vérifiée par un audit de confidentialité et un garde-fou automatisé.

### Ajouté

- **Diarisation des locuteurs plus précise, par modèle local** (option) : en mode
  Conférence, l'écran Configuration propose désormais deux niveaux de précision pour la
  distinction des voix. « Intégré » (défaut, inchangé) calcule l'empreinte vocale sur la
  machine sans rien télécharger. « Modèle local » utilise un modèle de vérification du
  locuteur (~26 Mo, WeSpeaker ResNet34) téléchargeable **en un clic** depuis l'écran, puis
  n'accède plus jamais au réseau ; il sépare nettement mieux les voix proches (sur un
  enregistrement de test réel à quatre participants, il retrouve les quatre voix quand
  l'empreinte intégrée n'en distingue aucune). Inférence **CPU uniquement**, télémétrie
  coupée. Modèle absent ou illisible : la réunion se déroule normalement avec l'empreinte
  intégrée et l'utilisateur en est averti. Attribution du modèle dans `NOTICE.md`.
- **Préréglages de performance** (écran Configuration) : trois boutons — « Rapide »
  (base + int8), « Équilibré » (medium + int8), « Précis » (large-v3, float16 si CUDA
  est sélectionné) — remplissent les champs existants ; l'application passe par
  « Enregistrer » comme toute modification manuelle. Bouton « **Tester sur ce poste** » :
  mesure la durée de transcription d'un audio témoin **généré localement** (pur NumPy,
  déterministe — rien à télécharger, zéro réseau) sur la configuration enregistrée,
  en mode exclusif (ne peut jamais interrompre ni concurrencer une dictée).
- **Renommage des locuteurs après la session** (FR-31 complet) : dans l'écran
  Historique, le détail d'une réunion diarisée liste les locuteurs détectés avec des
  champs de renommage ; renommer « Locuteur 2 » en « Marie » réécrit le texte archivé
  (recherche plein texte comprise) et le fichier `.txt`/`.md` exporté s'il existe
  encore (résumé de fin de session préservé) — fichier déplacé ou supprimé =
  dégradation propre, l'historique reste mis à jour et l'utilisateur est notifié.
  La base d'historique est désormais **versionnée** (`PRAGMA user_version`, migrations
  incrémentales) : les bases existantes sont migrées sans perte à l'ouverture.
- **Commandes de ponctuation dictées** (`punctuation.enabled`, opt-in) : « point »,
  « virgule », « deux points », « point d'interrogation », « à la ligne », « nouveau
  paragraphe », « ouvrez/fermez la parenthèse », « ouvrez/fermez les guillemets »… sont
  converties en ponctuation réelle (typographie française, majuscules de début de
  phrase). Dictée seulement — jamais appliquées au live, à la réunion ni à l'import.
- **Compte rendu par gabarit** (`summary.template`, opt-in) : quand le résumé de fin de
  session réussit, un compte rendu Markdown est rendu depuis un gabarit éditable
  (balises `{{date}}`, `{{source}}`, `{{resume}}`, `{{transcript}}`…) et écrit à côté
  du transcript ; le gabarit d'exemple est créé au premier usage.
- **Retour sonore de dictée** (`audio.sound_feedback`, actif par défaut) : bips brefs
  100 % locaux (winsound) au démarrage et à l'arrêt de l'enregistrement — le raccourci
  est confirmé à l'oreille, sans regarder le tray. Désactivable dans `config.yaml`.
- **Recherche plein texte dans l'historique** (SQLite FTS5, zéro dépendance) : le champ
  de recherche de l'écran Historique ignore désormais les accents (« reunion » retrouve
  « réunion ») et cherche par mots et préfixes ; repli automatique sur l'ancien filtre
  si FTS5 est indisponible.
- **Rétention temporelle de l'historique** (`history.max_age_days`, 0 = illimité) :
  purge automatique des transcriptions plus vieilles que N jours (RGPD), à l'ouverture
  et à chaque écriture.
- **CI build + release** : job GitHub Actions qui valide requirements.txt, le build
  PyInstaller et la compilation de l'installeur à chaque push ; workflow de release sur
  tag X.Y.Z (cohérence version/CHANGELOG/tag, release brouillon avec l'installeur).

### Corrigé

- Corrections issues de l'audit 2026-08 (commits `affcb4a` et `9a1300d`), dont : un
  dictionnaire enregistré en ANSI ne bloque plus le démarrage ; le collage ne peut plus
  écraser une image copiée ni coller l'ancien texte (délai `output.restore_delay`) et
  un échec d'injection est notifié ; l'application ne peut plus rester figée en fin de
  session live/réunion ; l'écriture de `config.yaml`/`dictionary.txt` est atomique et
  tolère les indentations non standard ; Échap annule la capture de raccourci ;
  mise à jour et désinstallation de l'installeur assainies.
- Renommer un locuteur ou prendre une note **pendant** une réunion ne peut plus laisser
  la tuile de transcription en direct avec une ligne manquante ou affichée deux fois :
  l'affichage se réaligne sur le rendu complet au segment suivant. Une note prise en
  séance apparaît désormais à sa position chronologique et non en fin de flux. Le fichier
  exporté et l'historique n'étaient pas concernés (rendus à l'arrêt depuis les mêmes clés).
- Renommer un locuteur après la fin d'une réunion, depuis un panneau resté affiché, ne
  peut plus réafficher la session précédente ni écraser une transcription live qui vient
  de démarrer. Le renommage d'une réunion terminée se fait dans l'écran Historique.
- **Encodage de la chaîne de build** : `installer/whisperty.iss` et `scripts/*.ps1`
  étaient en UTF-8 **sans BOM**, seule forme qu'Inno Setup et Windows PowerShell 5.1
  (le shell par défaut de Windows) lisent comme de l'ANSI. Conséquences : les dialogues
  français de l'installeur (données conservées à la désinstallation, avertissement
  WebView2) affichaient des accents corrompus, et `build.ps1` ne se compilait plus du
  tout hors PowerShell 7 — un tiret cadratin y était lu comme un guillemet fermant.
  Les fichiers portent désormais un BOM UTF-8.

### Sécurité / confidentialité

- Le texte dicté n'est plus journalisé au niveau INFO (longueur seulement, contenu
  réservé à DEBUG) ; CSP zéro-réseau dans la page de la fenêtre ; trafic de fond du
  runtime WebView2 réduit ; télémétries onnxruntime et huggingface_hub désactivées.
- Journaux durcis : le **nom du fichier audio importé**, le **chemin complet** cité par
  les erreurs d'import (fichier introuvable, illisible ou corrompu) et le contenu brut
  d'une entrée de profil mal formée passent au niveau DEBUG. Les lignes INFO, erreurs et
  avertissements restent en place, sans la métadonnée : un journal transmis pour
  diagnostic ne révèle plus quels fichiers ont été transcrits. Même traitement pour la
  ligne « Profil de contexte appliqué », écrite à chaque dictée : elle formait une trace
  horodatée des applications utilisées (toujours visible avec `logging.level: DEBUG`).
- Retrait de la planche de marque `Whisperty Logo.dc.html` (fichier de maquettage jamais
  expédié, mais qui référençait un CDN de polices). L'identité visuelle de référence est
  `scripts/make_icon.py`, 100 % local.

## [0.3.0] — 2026-07-04

### Ajouté

- **Diarisation des locuteurs en réunion (UC-18)** — en mode Conférence, une nouvelle
  option distingue désormais **chaque orateur** (plusieurs personnes au micro, plusieurs
  participants distants) et non plus seulement la source (`Moi` / `Interlocuteurs`) :
  chaque segment porte une étiquette de voix (`[MM:SS] Locuteur 2 : …`), entrelacée
  chronologiquement quelle que soit la provenance du son.
  - **100 % local, sans rien à télécharger** — l'empreinte vocale est calculée sur la
    machine (statistiques MFCC en pur NumPy) puis regroupée par similarité ; contrairement
    aux modèles de diarisation neuronaux (PyTorch + modèles *gated*), **aucun modèle ni
    réseau** n'est requis — la garantie de confidentialité la plus forte possible.
  - **Renommage à chaud** — depuis la fenêtre, un locuteur détecté peut être renommé
    (`Locuteur 2` → « Marie Dupont ») sans interrompre la capture ; le nom s'applique
    **rétroactivement** au flux en direct, à l'export et à l'historique de la session.
  - **Opt-in** (`conference.speaker_diarization.enabled`, désactivé par défaut) et sans
    risque : toute voix trop courte, silencieuse ou ambiguë retombe sur l'étiquette de
    source — la réunion ne s'arrête jamais à cause de la diarisation.
- **Gestion du dictionnaire personnalisé depuis la fenêtre (UC-19)** — un nouvel écran
  « Dictionnaire » liste, ajoute, modifie et supprime les termes favorisés (hotwords) et
  les corrections (`mauvais => correct`) sans éditer le fichier à la main. L'enregistrement
  réécrit `dictionary.txt` en **préservant commentaires et ordre**, puis recharge le
  dictionnaire **à chaud** : la dictée suivante en bénéficie sans redémarrage. En mode zone
  de notification seule, une entrée « Ouvrir le dictionnaire » ouvre le fichier dans
  l'éditeur système (créé avec un en-tête d'aide s'il n'existe pas).

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
