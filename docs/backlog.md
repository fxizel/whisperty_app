# Backlog produit — Whisperty (épics & user stories)

> **Produit** : Whisperty — dictée vocale **100 % locale** pour Windows 10/11.
> **Promesse** : j'appuie sur un raccourci, je parle, et mon texte s'insère là où je travaille —
> **sans qu'aucune donnée (voix ou texte) ne quitte mon ordinateur.**
>
> **Contrainte cardinale (non négociable)** : aucune donnée ne sort de la machine. Toute story
> qui s'en écarterait doit être refusée. Cette exigence prime sur toutes les autres.

## Légende

- **Priorité MoSCoW** : `Must` (indispensable) · `Should` (important) · `Could` (souhaitable) · `Won't` (hors périmètre actuel).
- **Identifiants** : les épics sont notés `EPIC-n`, les stories `ST-n.m` (m = story du parent `EPIC-n`).
  Cette notation sert à lire la hiérarchie épic → story ; dans un outil de gestion de backlog, chaque élément recevrait son propre identifiant.
- Les stories sont rédigées en **langage métier** : elles décrivent un besoin et un résultat observable, pas une solution technique.

## Vue d'ensemble du backlog

| Épic | Thème | Priorité | Stories |
|------|-------|----------|---------|
| EPIC-1 | Dictée vocale de base | Must | ST-1.1 → ST-1.8 |
| EPIC-2 | Post-traitement & personnalisation | Should | ST-2.1 → ST-2.4 |
| EPIC-3 | Modes avancés de transcription | Should / Could | ST-3.1 → ST-3.6 |
| EPIC-4 | Configuration & déploiement | Must / Could | ST-4.1 → ST-4.6 |
| EPIC-5 | Confidentialité, robustesse & qualité | Must | ST-5.1 → ST-5.6 |

**Total : 5 épics · 30 user stories.**

---

# EPIC-1 — Dictée vocale de base

**Priorité : Must**

**Description.** Permettre à un utilisateur de dicter du texte n'importe où dans Windows : il
déclenche l'enregistrement par un raccourci, parle, et le texte transcrit s'insère
automatiquement dans l'application active. Toute la reconnaissance se fait sur la machine, sans
connexion. C'est le cœur du produit, dont dépendent tous les autres épics.

**Critères d'acceptance de haut niveau**
- Un utilisateur peut dicter et obtenir son texte inséré dans l'application de son choix, sans copier-coller manuel.
- La reconnaissance fonctionne entièrement hors connexion.
- L'utilisateur sait à tout moment, d'un coup d'œil, dans quel état se trouve l'application.
- Le démarrage et l'arrêt de la dictée sont simples et ne demandent pas les mains en permanence.

### ST-1.1 — Déclencher la dictée par un raccourci clavier
**En tant qu'**utilisateur qui dicte au quotidien, **je veux** démarrer et arrêter la dictée avec
un raccourci clavier, **afin de** dicter sans quitter l'application dans laquelle je travaille.
- Un raccourci clavier démarre l'enregistrement, et un second appui l'arrête.
- Le raccourci fonctionne quelle que soit la fenêtre active, sans avoir à cliquer dans Whisperty.
- Pendant l'enregistrement, l'application indique clairement qu'elle écoute.
- **Priorité : Must** · **Parent : EPIC-1**

### ST-1.2 — Choisir mon mode de déclenchement
**En tant qu'**utilisateur, **je veux** choisir comment déclencher la dictée (appui unique,
maintien de la touche pendant que je parle, ou double-appui), **afin d'**adapter le confort à
ma façon de travailler.
- Je peux choisir entre : appui pour démarrer/arrêter, maintien de la touche le temps de parler, ou double-appui rapide.
- Je peux personnaliser la combinaison de touches utilisée.
- En cas de combinaison invalide, l'application revient automatiquement au **raccourci par défaut** (le même qu'au premier lancement, choisi pour ne pas entrer en conflit avec un raccourci réservé par Windows) et me le signale, sans planter.
- **Priorité : Must** · **Parent : EPIC-1**

### ST-1.3 — Choisir le micro utilisé
**En tant qu'**utilisateur disposant de plusieurs micros (casque, webcam, micro intégré),
**je veux** choisir lequel est utilisé, **afin d'**obtenir la meilleure qualité de dictée.
- Par défaut, l'application utilise le micro défini par Windows.
- Je peux désigner un autre micro, par son nom ou en le choisissant dans une liste.
- La qualité de captation est automatiquement adaptée à ce qu'attend la reconnaissance, quel que soit le micro.
- **Priorité : Must** · **Parent : EPIC-1**

### ST-1.4 — Transcrire ma voix en texte, localement
**En tant qu'**utilisateur soucieux de mes données, **je veux** que ma voix soit transcrite
directement sur mon ordinateur, **afin d'**obtenir mon texte sans qu'il transite par Internet.
- À la fin d'une dictée, le texte correspondant à mes paroles est produit sur ma machine.
- La transcription fonctionne sans aucune connexion réseau.
- Le français est utilisé par défaut ; je peux aussi laisser la langue se détecter automatiquement.
- Je peux choisir un réglage privilégiant soit la rapidité, soit la précision, selon la puissance de mon ordinateur.
- **Priorité : Must** · **Parent : EPIC-1**

### ST-1.5 — Démarrer rapidement, sans attente à la première dictée
**En tant qu'**utilisateur, **je veux** que ma première dictée soit aussi rapide que les
suivantes, **afin de** ne pas subir d'attente au lancement.
- Le moteur de reconnaissance se prépare en arrière-plan dès le démarrage de l'application.
- Une fois l'application signalée comme prête, la première dictée démarre aussi vite que les suivantes (aucune attente supplémentaire au premier usage).
- Pendant cette préparation, l'application reste utilisable et l'état est indiqué.
- **Priorité : Should** · **Parent : EPIC-1**

### ST-1.6 — Insérer le texte dans l'application active
**En tant qu'**utilisateur, **je veux** que le texte dicté s'insère automatiquement là où se
trouve mon curseur, **afin de** ne pas avoir à copier-coller.
- Le texte transcrit est inséré dans l'application au premier plan, à l'emplacement du curseur.
- Les accents et les caractères français sont restitués fidèlement, y compris sur de longs textes.
- Si le contenu du presse-papiers a été utilisé pour l'insertion, mon contenu précédent est restauré ensuite.
- Une méthode d'insertion de repli existe si l'insertion principale échoue.
- **Priorité : Must** · **Parent : EPIC-1**

### ST-1.7 — Arrêt automatique de l'enregistrement
**En tant qu'**utilisateur, **je veux** que l'enregistrement s'arrête tout seul quand j'ai fini
de parler, **afin de** ne pas avoir à le couper manuellement à chaque fois.
- Après un silence prolongé, l'enregistrement s'arrête automatiquement et lance la transcription.
- La durée de silence déclenchant l'arrêt est paramétrable.
- Une durée maximale d'enregistrement protège contre un enregistrement oublié ouvert indéfiniment.
- **Priorité : Must** · **Parent : EPIC-1**

### ST-1.8 — Connaître l'état de l'application en un coup d'œil
**En tant qu'**utilisateur, **je veux** voir immédiatement l'état de l'application, **afin de**
ne dicter que lorsqu'elle écoute vraiment et de ne pas attendre inutilement quand elle a terminé.
- Une icône dans la barre de notification change de couleur selon l'état : prêt, enregistrement, traitement en cours, transcription en direct, réunion.
- Survoler l'icône affiche un texte décrivant l'état courant.
- L'état affiché correspond toujours à l'activité réelle de l'application.
- **Priorité : Must** · **Parent : EPIC-1**

---

# EPIC-2 — Post-traitement & personnalisation

**Priorité : Should**

**Description.** Améliorer la qualité et la pertinence du texte dicté en l'adaptant au
vocabulaire, au contexte de travail et aux préférences de l'utilisateur. Inclut une option
d'amélioration par une intelligence artificielle qui, elle aussi, fonctionne exclusivement sur
la machine de l'utilisateur.

**Critères d'acceptance de haut niveau**
- L'utilisateur peut faire reconnaître son vocabulaire métier et corriger automatiquement ses erreurs récurrentes.
- Le contexte de dictée s'adapte automatiquement à l'application utilisée.
- Toute amélioration par IA reste strictement locale ; l'indisponibilité de l'IA ne bloque jamais la dictée.

### ST-2.1 — Personnaliser mon vocabulaire métier
**En tant que** professionnel utilisant un vocabulaire spécialisé, **je veux** apprendre à
l'application mes termes et corrections habituels, **afin que** mes dictées soient fidèles à mon
métier.
- Je peux fournir une liste de termes à privilégier pour qu'ils soient mieux reconnus.
- Je peux définir des corrections automatiques (« remplacer tel mot mal reconnu par tel autre »).
- Ces corrections sont appliquées au texte avant son insertion.
- La personnalisation se fait dans un fichier simple, sans connaissance technique.
- **Priorité : Should** · **Parent : EPIC-2**

### ST-2.2 — Adapter le contexte à l'application utilisée
**En tant qu'**utilisateur travaillant dans plusieurs applications (messagerie, éditeur, etc.),
**je veux** que la dictée s'adapte automatiquement à l'application active, **afin d'**obtenir un
texte mieux adapté à chaque contexte.
- Je peux définir des profils associés à certaines applications (par exemple un profil « courriel » et un profil « technique »).
- Le profil correspondant à l'application visée s'applique automatiquement au lancement de la dictée.
- Un profil peut adapter le ton attendu, la langue et le vocabulaire.
- En l'absence de profil correspondant, un comportement par défaut s'applique.
- **Priorité : Could** · **Parent : EPIC-2**

### ST-2.3 — Améliorer automatiquement la mise en forme du texte (IA locale)
**En tant qu'**utilisateur exigeant sur la qualité rédactionnelle, **je veux** une amélioration
automatique de la ponctuation, des majuscules et des fautes évidentes, **afin d'**obtenir un
texte plus propre sans relecture systématique — tout en gardant mes données chez moi.
- Je peux activer une amélioration automatique de la ponctuation, de la casse et des fautes manifestes, sans reformulation de mon propos.
- Cette amélioration s'appuie **uniquement** sur un LLM fonctionnant sur ma propre machine.
- Toute tentative d'utiliser un service extérieur est **refusée avant tout envoi** ; même un détour qui ferait sortir le texte de l'ordinateur est bloqué.
- Si le LLM local n'est pas disponible ou échoue, ma dictée est conservée telle quelle, sans blocage.
- Cette fonction est désactivée par défaut et doit être activée explicitement.
- **Priorité : Should** · **Parent : EPIC-2**

### ST-2.4 — Gérer mon vocabulaire depuis la fenêtre de l'application
**En tant qu'**utilisateur peu technique, **je veux** ajouter, modifier et supprimer mes termes
et corrections depuis la fenêtre de l'application, **afin de** faire vivre mon vocabulaire sans
éditer de fichier à la main.
- Je peux consulter la liste de mes termes favorisés et de mes corrections dans une vue dédiée.
- Je peux ajouter, modifier ou supprimer une entrée ; l'enregistrement est confirmé et pris en compte à la dictée suivante, sans redémarrage.
- Mon fichier de vocabulaire reste lisible et modifiable à la main : mes commentaires et l'ordre de mes entrées sont préservés.
- Si l'enregistrement échoue (fichier verrouillé, droits insuffisants), rien n'est perdu et je suis prévenu.
- **Priorité : Should** · **Parent : EPIC-2** · Réf. UC-19

---

# EPIC-3 — Modes avancés de transcription

**Priorité : Should / Could**

**Description.** Étendre Whisperty au-delà de la dictée immédiate : transcription de fichiers
audio, historique réutilisable, suivi en direct d'une réunion en ligne et transcription complète
d'une réunion avec distinction des intervenants. Tous ces modes
restent 100 % locaux et, pour ceux qui captent une conversation, rappellent la nécessité du
consentement.

**Critères d'acceptance de haut niveau**
- L'utilisateur peut transcrire un fichier audio existant et retrouver ses transcriptions passées.
- L'utilisateur peut suivre et restituer une réunion en ligne (en direct ou en compte rendu), localement, et — si la fenêtre est ouverte — en voir la transcription progresser au fur et à mesure.
- Les modes de captation de conversation rappellent le consentement et n'insèrent jamais de texte à l'insu de l'utilisateur.

### ST-3.1 — Importer et transcrire un fichier audio
**En tant qu'**utilisateur recevant des enregistrements (interviews, mémos vocaux), **je veux**
transcrire un fichier audio, **afin d'**en obtenir le texte sans le confier à un service en
ligne.
- Je peux sélectionner un fichier audio dans les formats courants (par exemple un enregistrement vocal ou un fichier de musique).
- Le fichier est transcrit localement et le texte est copié pour que je le colle où je veux.
- La transcription est ajoutée à mon historique.
- Le texte n'est pas inséré automatiquement (l'application cible serait ambiguë) : il est mis à ma disposition.
- **Priorité : Should** · **Parent : EPIC-3**

### ST-3.2 — Retrouver et réutiliser mes transcriptions passées
**En tant qu'**utilisateur, **je veux** conserver l'historique de mes transcriptions, **afin de**
récupérer un texte produit précédemment.
- Chaque transcription est conservée localement.
- Je peux copier ma dernière transcription en une action depuis le menu.
- L'historique se limite à un nombre maximal d'entrées, les plus anciennes étant supprimées automatiquement.
- L'historique peut être désactivé si je le souhaite.
- **Priorité : Should** · **Parent : EPIC-3**

### ST-3.3 — Transcrire en direct le son d'une réunion en ligne
**En tant que** participant à une visioconférence, **je veux** transcrire en continu ce que
j'entends, **afin de** suivre la réunion par écrit sans prendre de notes.
- Je peux lancer une transcription en direct de ce qui sort de mon ordinateur (le son de la réunion).
- Le texte s'écrit au fil de la conversation dans un fichier daté.
- Si la fenêtre de l'application est ouverte, je vois la transcription **s'afficher au fur et à mesure** (flux en direct), sans attendre l'arrêt ; la fenêtre étant facultative, sa fermeture n'interrompt ni la transcription ni l'enregistrement du fichier.
- À l'arrêt, le texte complet est copié et ajouté à mon historique.
- Ce mode n'insère rien automatiquement dans une autre application.
- L'icône indique clairement que la transcription en direct est active.
- **Priorité : Should** · **Parent : EPIC-3**

### ST-3.4 — Transcrire une réunion (ma voix + celle des interlocuteurs)
**En tant que** personne animant ou suivant des réunions, **je veux** transcrire une réunion en
captant à la fois ma voix et celle de mes interlocuteurs, **afin d'**obtenir un compte rendu
écrit sans prendre de notes.
- L'application capte simultanément ma voix (micro) et le son des interlocuteurs (sortie de l'ordinateur).
- Le compte rendu est exporté dans un fichier daté (texte ou format enrichi) et ajouté à l'historique ; il n'est pas inséré automatiquement.
- Si la fenêtre de l'application est ouverte, je vois le compte rendu **se construire au fur et à mesure** (flux en direct, avec l'indication du locuteur), sans attendre la fin de la réunion.
- Au démarrage, l'application rappelle la nécessité du consentement des participants (cf. ST-5.4).
- Si une seule source est disponible (micro **ou** son de l'ordinateur), la transcription se poursuit avec celle-ci.
- **Priorité : Could** · **Parent : EPIC-3**

### ST-3.5 — Distinguer les intervenants dans le compte rendu
**En tant que** personne suivant des réunions, **je veux** que le compte rendu distingue ma voix
de celle des interlocuteurs, **afin de** savoir clairement qui a dit quoi.
- Par défaut, chaque passage est attribué à sa source (par ex. « Moi » / « Interlocuteurs ») avec un horodatage, dans l'ordre chronologique.
- Je peux désactiver cette distinction pour obtenir un compte rendu fusionné en un seul fil.
- La distinction repose sur la source du son (déterministe) et ne fait appel à aucun service externe.
- **Priorité : Could** · **Parent : EPIC-3**

### ST-3.6 — Distinguer chaque orateur (diarisation des locuteurs)
**En tant que** personne suivant une réunion hybride, **je veux** que le compte rendu identifie
**chaque orateur** (plusieurs personnes en salle, plusieurs participants distants) et non plus
seulement la source, **afin de** savoir précisément qui a dit quoi.
- Option **désactivée par défaut** (`conference.speaker_diarization.enabled`) : quand elle est
  activée, chaque passage porte une étiquette de voix (« Locuteur 2 ») entrelacée
  chronologiquement, quelle que soit la provenance du son.
- Je peux **renommer** un locuteur détecté depuis la fenêtre (« Locuteur 2 » → « Marie Dupont »)
  sans interrompre la réunion ; le nom s'applique rétroactivement au flux, à l'export et à l'historique.
- **100 % local, sans rien à télécharger** : l'empreinte vocale est calculée sur la machine ; aucun
  audio ni empreinte ne quitte le poste. Toute voix trop courte ou ambiguë retombe sur l'étiquette
  de source — la réunion ne s'arrête jamais à cause de la diarisation.
- **Priorité : Could** · **Parent : EPIC-3** · Réf. UC-18

# EPIC-4 — Configuration & déploiement

**Priorité : Must / Could**

**Description.** Rendre Whisperty simple à configurer, à installer et à exploiter sur les postes,
y compris en environnement maîtrisé. Couvre le fichier de configuration, l'accès aux fonctions,
l'installation autonome, le démarrage automatique et l'accélération matérielle optionnelle.

**Critères d'acceptance de haut niveau**
- L'utilisateur peut tout régler depuis un fichier unique, lisible et commenté.
- Toutes les fonctions sont accessibles depuis un menu clair.
- L'application peut être installée en un exécutable autonome et démarrée automatiquement, sans droits d'administrateur.
- Après une unique récupération initiale du moteur, l'application fonctionne hors connexion.

### ST-4.1 — Configurer l'application via un fichier simple
**En tant qu'**utilisateur ou administrateur, **je veux** régler le comportement de
l'application dans un fichier unique et commenté, **afin de** l'adapter sans connaissance
technique.
- Tous les réglages sont rassemblés dans un seul fichier de configuration, accompagné d'un fichier de vocabulaire.
- Chaque réglage est documenté (rôle, valeurs possibles, valeur par défaut).
- Les modifications sont prises en compte au redémarrage de l'application.
- En l'absence de fichier, l'application démarre avec des réglages par défaut raisonnables.
- **Priorité : Must** · **Parent : EPIC-4**

### ST-4.2 — Accéder aux fonctions depuis le menu de l'icône
**En tant qu'**utilisateur, **je veux** accéder à toutes les fonctions depuis le menu de l'icône,
**afin de** piloter l'application sans interface complexe.
- Le menu donne accès à : dictée, transcription en direct, réunion, import de fichier, historique, configuration et fermeture.
- Une fonction non configurée apparaît désactivée (grisée).
- Si je lance un mode alors qu'un autre est déjà actif, l'application me le signale clairement au lieu de se bloquer.
- Je peux ouvrir directement la configuration et l'emplacement de l'historique depuis le menu.
- **Priorité : Must** · **Parent : EPIC-4**

### ST-4.3 — Installer l'application en un exécutable autonome
**En tant qu'**administrateur, **je veux** disposer d'un exécutable autonome, **afin de**
déployer Whisperty sans installation complexe.
- Un exécutable autonome peut être produit et exécuté sur un poste Windows.
- Le fichier de configuration et le fichier de vocabulaire restent modifiables à côté de l'exécutable (ils ne sont pas figés à l'intérieur).
- Si ces fichiers sont absents, l'application fonctionne avec ses réglages par défaut.
- **Priorité : Could** · **Parent : EPIC-4**

### ST-4.4 — Lancer l'application au démarrage de Windows
**En tant qu'**utilisateur ou administrateur, **je veux** que Whisperty démarre automatiquement
avec Windows, **afin qu'**il soit toujours prêt.
- Je peux activer le lancement automatique au démarrage de la session Windows.
- L'activation ne nécessite pas de droits d'administrateur.
- Je peux désactiver ce lancement automatique tout aussi simplement.
- **Priorité : Could** · **Parent : EPIC-4**

### ST-4.5 — Récupérer le moteur une seule fois, puis fonctionner hors connexion
**En tant qu'**utilisateur, **je veux** ne récupérer le moteur de reconnaissance qu'une seule
fois, **afin de** travailler ensuite totalement hors connexion.
- À la première mise en place, le moteur de reconnaissance peut être récupéré (unique opération réseau du produit).
- Une procédure claire explique comment effectuer cette récupération initiale.
- Une fois le moteur présent, l'application fonctionne sans aucune connexion et ne tente plus d'accès réseau pour la reconnaissance.
- **Priorité : Must** · **Parent : EPIC-4**

### ST-4.6 — Accélérer la transcription avec une carte graphique compatible
**En tant qu'**utilisateur équipé d'une carte graphique compatible, **je veux** accélérer la
transcription, **afin de** réduire le temps d'attente sur de gros volumes.
- Je peux activer une accélération matérielle si je dispose d'une carte graphique compatible.
- Sans matériel compatible, l'application reste pleinement fonctionnelle sur le processeur.
- La documentation indique clairement les matériels pris en charge et ceux qui ne le sont pas.
- **Priorité : Could** · **Parent : EPIC-4**

---

# EPIC-5 — Confidentialité, robustesse & qualité

**Priorité : Must**

**Description.** Garantir et démontrer la promesse fondamentale du produit : aucune donnée ne
quitte la machine, l'application reste stable et fiable, et l'utilisateur est toujours informé.
Cet épic porte les garanties transverses qui conditionnent la confiance dans le produit.

**Critères d'acceptance de haut niveau**
- L'absence de transmission de données est garantie et vérifiable.
- L'application reste stable en usage intensif et ne perd jamais une transcription.
- L'utilisateur est informé clairement, en français, de chaque événement important.
- La qualité est verrouillée par des contrôles automatisés avant toute mise à disposition.

### ST-5.1 — Garantir qu'aucune donnée ne quitte ma machine
**En tant que** responsable de la sécurité des données, **je veux** la garantie qu'aucune donnée
ne sort de la machine, **afin d'**autoriser l'usage de l'outil sur des informations sensibles.
- Pendant l'utilisation courante, aucune donnée (voix ou texte) n'est transmise sur le réseau, et ceci est vérifiable avec un outil d'analyse réseau.
- Aucune statistique d'usage ni télémétrie n'est collectée ni envoyée.
- Les seules fonctions d'intelligence artificielle disponibles s'exécutent localement ; aucun service externe n'est sollicité.
- La seule connexion réseau possible est la récupération initiale du moteur (cf. ST-4.5), et elle est documentée.
- **Priorité : Must** · **Parent : EPIC-5**

### ST-5.2 — Rester stable en usage intensif et en actions simultanées
**En tant qu'**utilisateur intensif, **je veux** une application stable même quand plusieurs
actions s'enchaînent, **afin de** ne jamais subir de blocage ni de gel.
- Une seule activité de transcription peut être active à la fois ; toute demande concurrente est signalée plutôt que d'entraîner un blocage.
- Pendant qu'une transcription est en cours, l'icône et son menu restent accessibles et répondent aux clics ; aucune action de l'utilisateur n'est ignorée.
- Les modes longs (transcription en direct, réunion) s'exécutent en arrière-plan sans figer l'application ; un arrêt demandé pendant qu'ils tournent est toujours pris en compte, sans gel.
- Les démarrages et arrêts rapprochés ne provoquent ni gel ni comportement incohérent.
- **Priorité : Must** · **Parent : EPIC-5**

### ST-5.3 — Ne jamais perdre une transcription à la fermeture
**En tant qu'**utilisateur, **je veux** que mes transcriptions soient préservées même si je
quitte l'application en cours de route, **afin de** ne pas perdre mon travail.
- À la fermeture, l'application arrête proprement toutes ses activités en cours — écoute du raccourci, captation du micro et du son, modes longs — puis libère son icône.
- Une transcription longue (réunion, direct) en cours est enregistrée **avant** la fermeture complète.
- Aucune donnée déjà transcrite n'est perdue lors d'une fermeture normale.
- **Priorité : Must** · **Parent : EPIC-5**

### ST-5.4 — Être informé clairement, en français
**En tant qu'**utilisateur, **je veux** des messages clairs en français, **afin de** savoir quoi
faire quand une action échoue ou est refusée, sans avoir à deviner ni à chercher une aide technique.
- Chaque fin d'opération (dictée, import, direct, réunion) donne lieu à une information visible.
- Chaque refus est accompagné d'un message explicite indiquant la raison et, si possible, la marche à suivre.
- Le rappel de consentement est affiché au démarrage des modes qui captent une conversation.
- Tous les messages sont rédigés en français.
- **Priorité : Must** · **Parent : EPIC-5**

### ST-5.5 — Garantir la qualité par des contrôles automatisés
**En tant que** responsable produit, **je veux** que les fonctions clés soient protégées par des
contrôles automatisés, **afin de** livrer des versions fiables en confiance.
- Les fonctions essentielles sont couvertes par des tests automatisés exécutés à chaque évolution.
- Ces tests s'exécutent sans matériel audio ni connexion réseau.
- Outre les tests, un contrôle automatisé de la propreté et de la conformité du code est exécuté à chaque évolution ; son échec bloque la mise à disposition.
- Un niveau de couverture des tests suffisant est exigé avant toute mise à disposition (cible d'au moins 80 %, précisée dans les spécifications).
- Les contrôles s'exécutent sur les environnements cibles (Windows et environnement d'intégration).
- **Priorité : Must** · **Parent : EPIC-5**

### ST-5.6 — Conserver des journaux locaux pour le diagnostic
**En tant qu'**administrateur assurant le support, **je veux** des journaux conservés localement,
**afin de** diagnostiquer un incident sans compromettre la confidentialité.
- L'application conserve un journal d'activité sur le poste (à l'écran et dans un fichier).
- Le niveau de détail des journaux est réglable.
- Les journaux ne sont jamais transmis sur le réseau.
- **Priorité : Should** · **Parent : EPIC-5**

---

*Backlog initial — Whisperty. À affiner avec le Product Owner (estimation, ordonnancement de sprint,
critères de « Definition of Done »). Les stories sont volontairement formulées en langage métier ;
les détails techniques figurent dans le dossier de [spécifications](specifications/README.md).*
