# Politique de confidentialité — Whisperty

**Dernière mise à jour :** 6 juillet 2026  
**Éditeur :** projet personnel open source ([`fxizel/whisperty_app`](https://github.com/fxizel/whisperty_app))  
**Application :** Whisperty (dictée vocale locale pour Windows 10/11)

**URL de cette politique :**  
https://github.com/fxizel/whisperty_app/blob/main/docs/privacy-policy.md

---

## 1. Résumé

Whisperty est conçu pour que **vos données audio et textuelles ne quittent pas votre ordinateur**.
L'application ne collecte pas de données personnelles à des fins publicitaires, analytiques
ou de profilage. Il n'existe **aucun compte utilisateur**, **aucune télémétrie** et **aucun
serveur Whisperty** vers lequel vos dictées seraient envoyées.

## 2. Données traitées

Whisperty traite localement, sur votre machine :

| Donnée | Usage | Stockage |
|--------|-------|----------|
| **Audio du microphone** | Transcription en dictée, mode réunion | Mémoire pendant l'enregistrement ; fichiers temporaires le cas échéant |
| **Audio d'une sortie système** (loopback) | Modes live et réunion | Mémoire / segments temporaires |
| **Texte transcrit** | Injection dans l'application active, historique, export | Fichier SQLite local (`whisperty.db`), dossier `transcriptions\` |
| **Réglages** (`config.yaml`, dictionnaire) | Personnalisation de l'application | À côté de l'exécutable, sur votre disque |
| **Journaux** (`logs\`) | Diagnostic en cas d'incident | Localement, sur votre disque |

Ces données restent **sous votre contrôle**. Vous pouvez les supprimer en désinstallant
l'application (certaines données utilisateur sont conservées par choix lors de la
désinstallation — voir l'installeur) ou en effaçant les fichiers concernés.

## 3. Absence de collecte par un tiers

Le développeur du projet **ne reçoit pas** :

- vos enregistrements audio ;
- vos transcriptions ;
- votre historique ;
- des statistiques d'usage ;
- des identifiants machine à des fins de suivi.

Whisperty **n'intègre pas** de SDK analytique, de publicité ni de service cloud propriétaire.

## 4. Accès réseau (exceptions explicites)

Par conception, Whisperty fonctionne **sans envoi de vos dictées sur Internet**. Les seuls
accès réseau possibles sont **opt-in** ou **ponctuels**, distincts du contenu de vos dictées :

| Situation | Réseau ? | Données concernées |
|-----------|----------|-------------------|
| Usage normal (`local_files_only: true`, défaut) | **Non** | — |
| Téléchargement initial du modèle Whisper (opt-in depuis l'interface) | Oui | Fichiers du modèle depuis Hugging Face |
| Installation des composants CUDA (opt-in) | Oui | Bibliothèques NVIDIA (pip) |
| Build sans modèle bundlé, 1er lancement | Oui (une fois) | Modèle Whisper |
| Raffinage IA / résumé (`ai.enabled`, opt-in) | **Local uniquement** | Texte envoyé à un LLM sur `localhost` — jamais à un hôte distant |
| Lien proposé si WebView2 manque (installeur) | Oui (si vous cliquez) | Ouverture du navigateur vers Microsoft |
| Ouverture de la politique de confidentialité (lien dans l'app) | Oui (si vous cliquez) | Ouverture du navigateur vers cette page |

Vous pouvez vérifier l'absence de trafic en usage courant avec un analyseur réseau
(Wireshark, etc.) lorsque `local_files_only` est activé.

## 5. Intelligence artificielle locale

Si vous activez le raffinage par LLM ou le résumé de session, Whisperty n'accepte que des
endpoints **locaux** (`localhost`, `127.0.0.1`, `::1`). Tout endpoint distant est **refusé**
par l'application. Le texte dicté ne doit jamais quitter votre machine via cette fonctionnalité.

## 6. Sous-traitants et logiciels tiers

Whisperty s'appuie sur des composants exécutés **sur votre poste** (Whisper /
`faster-whisper`, bibliothèques audio, WebView2 pour l'interface). Aucun de ces composants
n'est utilisé par le développeur pour collecter vos données.

Les téléchargements opt-in (modèle, CUDA) s'effectuent directement entre votre PC et les
hébergeurs concernés (ex. Hugging Face, NVIDIA), **sans intermédiaire** et sans transit
par un serveur de l'éditeur.

## 7. Sécurité

Les données restent sur votre disque dans le dossier d'installation utilisateur
(`%LocalAppData%\Programs\Whisperty` par défaut). Protégez l'accès à votre session Windows
comme pour tout document local sensible.

## 8. Vos droits

Étant donné qu'aucune donnée personnelle n'est transmise au développeur, il n'y a pas de dossier
utilisateur centralisé à consulter chez un tiers. Vous gérez vos données localement :
historique, transcriptions exportées, configuration.

Pour toute question relative à cette politique, ouvrez une discussion sur le dépôt public :
https://github.com/fxizel/whisperty_app

## 9. Modifications

Cette politique peut être mise à jour lors de nouvelles versions. La date en tête de document
indique la dernière révision. L'URL reste stable pour les références dans l'installeur et
les portails de distribution.

## 10. Contact

Via le dépôt GitHub : https://github.com/fxizel/whisperty_app
