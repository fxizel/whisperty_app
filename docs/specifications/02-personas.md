# 02 — Parties prenantes & personas

## 1. Cartographie des parties prenantes

| Partie prenante | Intérêt principal | Influence |
|-----------------|-------------------|-----------|
| **Utilisateur final** (dicteur) | Productivité, fiabilité, confidentialité | Forte (usage quotidien) |
| **RSSI / DPO** (sécurité & conformité) | Garantie zéro-réseau, conformité RGPD | Forte (droit de veto) |
| **Équipe IT / déployeur** | Installation, packaging, support, maintenabilité | Moyenne |
| **Mainteneur / contributeur** | Code clair, testable, hackable | Moyenne |
| **Participants de réunion** (tiers) | Consentement, respect de la vie privée | Indirecte (concernés) |

Les **personas** ci-dessous incarnent les types d'utilisateurs finaux et les rôles
décisionnels. Chaque persona porte un préfixe `P-xx` réutilisé dans la matrice de traçabilité.

---

## P-01 — Camille, développeuse logicielle

| | |
|---|---|
| **Rôle** | Ingénieure logicielle dans une équipe produit. |
| **Contexte** | Travaille dans VS Code, Windows Terminal, navigateur ; rédige du code, de la documentation, des messages de revue. |
| **Compétence technique** | Élevée — édite volontiers `config.yaml`, installe un LLM local. |

**Objectifs**
- Dicter de la documentation, des commentaires et des messages longs sans quitter le clavier.
- Conserver le **vocabulaire technique** (noms de fonctions, anglicismes : *commit*, *merge*, *refactor*).
- Adapter le contexte selon l'application (prompt « code » dans l'IDE, « mail » dans Outlook).

**Frustrations**
- Les dictées cloud déforment le jargon et exposent du code potentiellement sensible.
- Réécrire les accents et la ponctuation à la main est chronophage.

**Fonctionnalités clés** : dictée par raccourci (UC-01), profils de contexte (UC-05),
dictionnaire de hotwords (UC-04), raffinage IA local (UC-06).

---

## P-02 — Marc, consultant dans l'énergie

| | |
|---|---|
| **Rôle** | Consultant / chef de projet (réseau de distribution électrique, IT). |
| **Contexte** | Beaucoup d'e-mails Outlook, de réunions Teams, de comptes rendus. Données client **confidentielles**. |
| **Compétence technique** | Moyenne — suit une procédure d'installation, modifie quelques lignes de config. |

**Objectifs**
- Rédiger rapidement des e-mails et notes professionnels en français soigné.
- Transcrire des **réunions** (sa voix + interlocuteurs distants) avec distinction des locuteurs.
- Garantir à ses clients qu'aucune donnée ne transite par un cloud.

**Frustrations**
- Prise de notes manuelle pendant les réunions = perte d'attention.
- Outils de transcription en ligne incompatibles avec ses obligations de confidentialité.

**Fonctionnalités clés** : dictée dans Outlook (UC-01), mode réunion avec distinction par
locuteur (UC-10), historique (UC-08), confidentialité vérifiable (transversal).

---

## P-03 — Sophie, RSSI / déléguée à la protection des données

| | |
|---|---|
| **Rôle** | Responsable sécurité & conformité (RSSI faisant aussi office de DPO). |
| **Contexte** | Valide ou interdit le déploiement d'outils sur le parc. Audite les flux réseau. |
| **Compétence technique** | Élevée sur les aspects sécurité/réseau ; vérifie avec un analyseur (Wireshark). |

**Objectifs**
- Garantir qu'**aucune donnée ne sort** des postes (audio, texte, télémétrie).
- Pouvoir **prouver** le comportement hors-ligne et auditer le code (logiciel libre).
- S'assurer que le mode IA ne crée pas de canal d'exfiltration (endpoints locaux uniquement).

**Frustrations**
- Boîtes noires propriétaires impossibles à auditer.
- Fonctions « cloud » activées silencieusement par défaut.

**Fonctionnalités clés** : zéro-réseau vérifiable (CO-01), `local_files_only` par défaut
(CO-02), garde endpoints locaux IA (CO-03), journalisation locale (SU), code ouvert.

---

## P-04 — Thomas, utilisateur en prévention des TMS / accessibilité

| | |
|---|---|
| **Rôle** | Professionnel limitant la frappe (tendinite / RSI) ou recherchant un confort d'usage. |
| **Contexte** | Utilise la dictée comme mode de saisie **principal**, sur de longues sessions. |
| **Compétence technique** | Faible à moyenne — attend un fonctionnement « ça marche tout seul ». |

**Objectifs**
- Saisir du texte **partout** dans Windows sans solliciter les mains plus que nécessaire.
- Un déclenchement ergonomique (push-to-talk ou double-appui) et un arrêt automatique sur silence.
- Une grande **fiabilité** : pas de blocage, pas de texte perdu.

**Frustrations**
- Devoir cliquer dans une fenêtre dédiée puis copier-coller le résultat.
- Applications instables qui « gèlent » et l'obligent à tout recommencer.

**Fonctionnalités clés** : modes de déclenchement (UC-02), arrêt auto sur silence (UC-03),
injection system-wide (UC-01), robustesse (RE).

---

## P-05 — Léa, cheffe de projet / preneuse de notes

| | |
|---|---|
| **Rôle** | Anime et suit de nombreuses réunions et webinaires (Teams, Meet, Zoom). |
| **Contexte** | A besoin de comptes rendus et de réponses rapides en séance ; reçoit aussi des fichiers audio à transcrire. |
| **Compétence technique** | Moyenne. |

**Objectifs**
- Suivre une visioconférence avec une **transcription en direct** de ce qui se dit.
- Obtenir un **transcript horodaté exporté** d'une réunion, sans prise de notes manuelle.
- Être assistée pour **répondre** quand on l'interpelle pendant une réunion.
- Transcrire des **fichiers audio** reçus (interviews, mémos).

**Frustrations**
- Jongler entre écoute, prise de notes et réponses.
- Outils de transcription qui imposent un envoi des enregistrements en ligne.

**Fonctionnalités clés** : transcription live (UC-09), mode réunion (UC-10), assistant de
réunion (UC-11), import de fichier audio (UC-07).

---

## P-06 — Rémi, administrateur IT / déployeur *(persona secondaire)*

| | |
|---|---|
| **Rôle** | Prépare, déploie et maintient l'application sur les postes. |
| **Contexte** | Construit l'exécutable, gère le démarrage automatique, le cache du modèle, le support N1/N2. |
| **Compétence technique** | Élevée (scripts, packaging, diagnostic via logs). |

**Objectifs**
- Packager un exécutable autonome et le déployer avec ses fichiers éditables à côté.
- Activer le **démarrage automatique** par utilisateur (sans droits admin).
- **Diagnostiquer** les incidents via des journaux locaux exploitables.

**Frustrations**
- Dépendances natives lourdes ou compilation requise.
- Configuration figée empêchant l'adaptation au poste.

**Fonctionnalités clés** : packaging exe + autostart (UC-13), préchargement du modèle (UC-14),
configuration unique (UC-12), supportabilité (SU).
