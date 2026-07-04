# 05 — Traçabilité, risques & questions ouvertes

## 1. Matrice Persona → Cas d'utilisation

Indique quels personas portent chaque cas d'utilisation (● = usage central, ○ = usage occasionnel).

| UC \ Persona | P-01 Camille (dev) | P-02 Marc (énergie) | P-03 Sophie (RSSI) | P-04 Thomas (access.) | P-05 Léa (réunions) | P-06 Rémi (admin) |
|--------------|:---:|:---:|:---:|:---:|:---:|:---:|
| UC-01 Dicter | ● | ● | ○ | ● | ○ | |
| UC-02 Déclencher | ● | ○ | | ● | ○ | |
| UC-03 Arrêt auto | ○ | ○ | | ● | ○ | |
| UC-04 Dictionnaire | ● | ● | | | ○ | |
| UC-05 Profils | ● | ○ | | | | |
| UC-06 IA locale | ● | ○ | ○ | | ○ | |
| UC-07 Import fichier | | ○ | | | ● | |
| UC-08 Historique | ○ | ● | ○ | ○ | ● | |
| UC-09 Live | | ○ | | | ● | |
| UC-10 Réunion | | ● | ○ | | ● | |
| UC-18 Diarisation locuteurs | | ● | ○ | | ● | |
| UC-12 Configurer | ● | ○ | ○ | ○ | ○ | ● |
| UC-13 Packaging/autostart | | | ○ | | | ● |
| UC-14 Modèle initial | ○ | ○ | ○ | ○ | ○ | ● |
| UC-15 GPU | ○ | | | | | ● |
| UC-16 Notes en session | | ○ | | | ● | |
| UC-17 Résumé de session | | ○ | | | ● | |

> P-03 (RSSI) « consomme » surtout les **contraintes** (CO-01…03) plutôt que les UC : elle
> valide le comportement zéro-réseau, transverse à tous les cas d'utilisation.

## 2. Matrice Cas d'utilisation → Exigences

> Cette matrice est la **référence de traçabilité** UC → exigences ; la couverture inverse
> (§3) en est l'exact réciproque. Les « Exigences liées » des fiches UC (`03`) et la colonne
> « Source » des exigences (`04`) en sont des vues *indicatives*.

| UC | Exigences fonctionnelles | Non fonctionnelles & contraintes |
|----|--------------------------|----------------------------------|
| UC-01 Dicter | FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-07, FR-16, FR-19, FR-20, FR-21 | US-01, US-03, US-04, RE-01, RE-02, RE-05, PE-01, PE-02, PE-05, CO-01, CO-13, BR-01, BR-02, BR-03, BR-04 |
| UC-02 Déclencher | FR-01 | US-01, US-06, US-08, RE-05, CO-09, BR-01 |
| UC-03 Arrêt auto | FR-15 | RE-03, PE-04, US-06 |
| UC-04 Dictionnaire | FR-07 | US-04, SU-02, BR-03 |
| UC-05 Profils | FR-08 | CO-10 |
| UC-06 IA locale | FR-09, FR-22 | RE-06, CO-01, CO-03, BR-03 |
| UC-07 Import fichier | FR-10, FR-20 | RE-02, US-02, US-07, CO-07, BR-06 |
| UC-08 Historique | FR-11 | RE-07, RE-10, US-02, SU-03, SU-08 |
| UC-09 Live | FR-12, FR-16, FR-19, FR-20 | RE-08, RE-10, PE-03, PE-04, US-02, US-07, US-09, CO-05, CO-06, BR-06 |
| UC-10 Réunion | FR-13, FR-16, FR-19, FR-20 | RE-08, RE-09, RE-10, PE-03, US-02, US-07, US-09, CO-04, CO-05, CO-06, CO-16, BR-05, BR-06 |
| UC-18 Diarisation locuteurs | FR-29, FR-30, FR-31, FR-32 | RE-13, RE-14, PE-07, US-11, US-12, CO-17, CO-18, CO-19, BR-08 |
| UC-12 Configurer | FR-17, FR-21, FR-32 | US-02, US-05, SU-01, SU-08 |
| UC-13 Packaging | FR-17 | SU-04, CO-08, CO-11 |
| UC-14 Modèle initial | FR-18 | PE-02, CO-01, CO-02 |
| UC-15 GPU | FR-03 | PE-01, CO-12 |
| UC-16 Notes en session | FR-23, FR-24, FR-25, FR-26, FR-27 | US-10, RE-11, PE-06, CO-01, CO-09, BR-01, BR-06, BR-07 |
| UC-17 Résumé de session | FR-28 | RE-12, CO-01, CO-03, BR-06 |

## 3. Couverture inverse Exigence → Cas d'utilisation

Vérifie qu'**aucune exigence n'est orpheline** (toute exigence sert ≥ 1 UC ou contrainte transverse).

| Exigence | Couverte par |
|----------|--------------|
| FR-01 | UC-01, UC-02 |
| FR-02 | UC-01 |
| FR-03 | UC-01, UC-15 |
| FR-04 | UC-01 |
| FR-05 | UC-01 |
| FR-06 | UC-01 |
| FR-07 | UC-01, UC-04 |
| FR-08 | UC-05 |
| FR-09 | UC-06 |
| FR-10 | UC-07 |
| FR-11 | UC-08 |
| FR-12 | UC-09 |
| FR-13 | UC-10, UC-18 |
| FR-15 | UC-03 |
| FR-16 | UC-01, UC-09, UC-10 |
| FR-17 | UC-12, UC-13 |
| FR-18 | UC-01, UC-14 |
| FR-19 | UC-01, UC-09, UC-10 (exclusivité des modes) |
| FR-20 | UC-01, UC-07, UC-09, UC-10 |
| FR-21 | UC-01, UC-12 |
| FR-22 | UC-06 |
| FR-23 | UC-16 |
| FR-24 | UC-16 |
| FR-25 | UC-16 |
| FR-26 | UC-16 |
| FR-27 | UC-16 (exclusion documentée — Won't) |
| FR-28 | UC-17 |
| FR-29 | UC-18 |
| FR-30 | UC-18 |
| FR-31 | UC-18 |
| FR-32 | UC-18, UC-12 |
| US-01 | UC-01, UC-02 |
| US-02 | UC-07, UC-08, UC-09, UC-10, UC-12 |
| US-03 | UC-01 |
| US-04 | UC-01, UC-04 |
| US-05 | UC-12 |
| US-06 | UC-02, UC-03 |
| US-07 | UC-07, UC-09, UC-10 |
| US-08 | UC-02 |
| US-09 | UC-09, UC-10, UC-18 |
| US-10 | UC-16 |
| US-11 | UC-18 |
| US-12 | UC-18 |
| RE-01 | UC-01 |
| RE-02 | UC-01, UC-07 |
| RE-03 | UC-03 |
| RE-04 | transverse (concurrence) |
| RE-05 | UC-01, UC-02 |
| RE-06 | UC-06 |
| RE-07 | UC-08 |
| RE-08 | UC-09, UC-10 |
| RE-09 | UC-10 |
| RE-10 | UC-08, UC-09, UC-10 |
| RE-11 | UC-16 |
| RE-12 | UC-17 |
| RE-13 | UC-18 |
| RE-14 | UC-18 |
| PE-01 | UC-01, UC-15 |
| PE-02 | UC-01, UC-14 |
| PE-03 | UC-01, UC-09, UC-10 |
| PE-04 | UC-03, UC-09 |
| PE-05 | UC-01 |
| PE-06 | UC-16 |
| PE-07 | UC-18 |
| SU-01 | UC-12 |
| SU-02 | UC-04 |
| SU-03 | UC-08 (+ transverse) |
| SU-04 | UC-13 |
| SU-05 | transverse (qualité) |
| SU-06 | transverse (architecture) |
| SU-07 | transverse (conventions) |
| SU-08 | UC-08, UC-12 |
| CO-01 | UC-01, UC-06, UC-14, UC-16, UC-17 (+ transverse, cardinal) |
| CO-02 | UC-14 |
| CO-03 | UC-06, UC-17 |
| CO-04 | UC-10, UC-18 |
| CO-05 | UC-09, UC-10 |
| CO-06 | UC-09, UC-10 |
| CO-07 | UC-07 |
| CO-08 | UC-13 |
| CO-09 | UC-02, UC-16 |
| CO-10 | UC-05 |
| CO-11 | UC-13 |
| CO-12 | UC-15 |
| CO-13 | UC-01 |
| CO-14 | transverse (packaging/nommage) |
| CO-15 | transverse (plateforme) |
| CO-16 | UC-10, UC-18 |
| CO-17 | UC-18 (+ transverse, cardinal) |
| CO-18 | UC-18 |
| CO-19 | UC-18, UC-14 |
| BR-01 | UC-01, UC-02, UC-16 (exclusivité, transverse aux modes) |
| BR-02 | UC-01 |
| BR-03 | UC-01, UC-04, UC-06 |
| BR-04 | UC-01 |
| BR-05 | UC-10 |
| BR-06 | UC-07, UC-09, UC-10, UC-16, UC-17 |
| BR-07 | UC-16 |
| BR-08 | UC-18 |

> **Conclusion** : aucune exigence orpheline — chaque exigence (FR/US/RE/PE/SU/CO) et chaque
> règle de gestion (BR) sert ≥ 1 UC ou est explicitement **transverse**. Les exigences purement
> transverses (RE-04, SU-05/06/07, CO-14/15) ne se rattachent pas à un UC unique mais
> conditionnent l'ensemble du système ; `CO-01` (cardinale) est **à la fois** rattachée à des UC
> et transverse à tous.

## 4. Registre des risques

| # | Risque | Impact | Prob. | Mitigation (existante ou recommandée) |
|---|--------|:------:|:-----:|---------------------------------------|
| RSK-01 | **Fuite réseau involontaire** introduite par une dépendance (télémétrie, mise à jour). | Critique | Faible | Garde `local_files_only` + `HF_HUB_OFFLINE` ; revue de dépendances ; audit Wireshark (CO-01). |
| RSK-02 | **Performance CPU insuffisante** pour `medium` → latence dissuasive. | Élevé | Moyen | Modèle configurable (rétrograder à `small`/`base`), CUDA optionnel (PE-01, UC-15). |
| RSK-03 | **Loopback indisponible** (`soundcard` absent / pilote WASAPI) → live/réunion KO. | Moyen | Moyen | Démarrage refusé proprement + notification ; documentation d'installation (CO-05). |
| RSK-04 | **Interblocage** entre verrou d'état et arrêt des modes longs. | Élevé | Faible | Ordre de verrouillage strict + arrêt par callback sans `join()` sous verrou (RE-04, RE-08). |
| RSK-05 | **Mauvaise cible d'injection** (changement de fenêtre pendant la dictée). | Moyen | Moyen | Profil/cible capturés au démarrage (CO-10) ; sensibiliser l'utilisateur ; modes passifs non injectés (BR-06). |
| RSK-06 | **Enregistrement de réunion sans consentement** → risque juridique. | Élevé | Moyen | Rappel au démarrage + règle BR-05/CO-16 ; responsabilité utilisateur documentée. |
| RSK-07 | **Dépendance à un LLM local non installé** pour l'IA locale. | Faible | Élevé | Fonction opt-in, dégradation gracieuse (texte brut) (RE-06). |
| RSK-08 | **Modèle non pré-téléchargé** au premier lancement hors-ligne. | Moyen | Moyen | Procédure dédiée (UC-14) ; message d'erreur explicite ; `local_files_only` documenté. |
| RSK-09 | **Corruption d'injection** des accents en mode `type` (frappe). | Faible | Faible | Collage par défaut (BR-04) ; `type` réservé au repli. |
| RSK-10 | **Perte du transcript de réunion** si arrêt brutal. | Moyen | Faible | Écriture au fil de l'eau (live) + archivage avant fermeture de base (RE-10). |
| RSK-11 | **Raccourci signet inopérant** (conflit avec une autre application) → prise de note mains occupées impossible en réunion. | Faible | Moyen | Raccourci configurable et distinct de la dictée (FR-24, CO-09) ; échec d'enregistrement signalé, non bloquant ; la saisie dans la fenêtre reste disponible. |

## 5. Questions ouvertes / à valider

| # | Question | Pour qui |
|---|----------|----------|
| Q-01 | Faut-il chiffrer la base d'historique SQLite (`whisperty.db`) au repos ? Aujourd'hui en clair sur le poste. | P-03 (RSSI) |
| Q-02 | Faut-il une purge/expiration **temporelle** de l'historique (RGPD) en plus de la limite par nombre ? | P-03 |
| Q-03 | Le mode réunion doit-il afficher une **bannière de consentement** plus formelle (case à cocher) plutôt qu'une simple notification ? | P-02, P-05 |
| Q-04 | Faut-il une **interface de configuration** (au-delà de l'édition YAML) pour les personas moins techniques (P-04) ? | P-04, P-06 |
| Q-05 | Souhaite-t-on une **rétroaction sonore/visuelle** au déclenchement (bip, overlay) en plus de l'icône tray ? | P-04 |
| Q-06 | La diarisation par modèle (locuteurs individuels) sera-t-elle proposée un jour en **option hors-ligne désactivée par défaut** ? | P-05, P-03 | → **Oui** — tracé UC-18, CO-17…19, `speaker_diarization.enabled: false` par défaut. |
| Q-07 | Doit-on cibler explicitement des **objectifs de latence chiffrés** (ex. < N s pour une phrase) par profil matériel ? | P-01, P-06 |
| Q-08 | Faut-il un mécanisme de **mise à jour** de l'application compatible zéro-réseau (paquet hors-ligne signé) ? | P-06, P-03 |
| Q-09 | La garde IA (`CO-03`) accepte un **hôte vide** (`""`) et `[::1]` en plus de localhost/127.0.0.1/::1 : faut-il **rejeter l'hôte vide** par rigueur (URL malformée), même s'il n'envoie rien hors machine ? | P-03 |
| Q-10 | Notes en session (UC-16) : les deux choix ergonomiques retenus — horodatage de la note textuelle **à la validation** (pas au début de saisie) et **signet sans texte** complété après la session (plutôt qu'une mini-saisie surgissante, intrusive en réunion) — conviennent-ils à l'usage réel ? | P-02, P-05 |

## 6. Statut de couverture FURPS

| Catégorie | Nb exigences | Couverture personas |
|-----------|:------------:|---------------------|
| Functionality (FR) | 31 *(dont FR-27 en `W` ; FR-14 retiré)* | P-01 → P-06 |
| Usability (US) | 12 | P-01, P-04 (priorité), tous |
| Reliability (RE) | 14 | P-04, P-02 (priorité), tous |
| Performance (PE) | 7 | P-01, P-05, P-06 |
| Supportability (SU) | 8 | P-06 (priorité), mainteneurs |
| Contraintes (CO) | 19 | P-03 (priorité), tous |
| Règles de gestion (BR) | 8 | transverse |

---

*Fin du dossier de spécifications. Voir l'[index](README.md) pour la navigation.*
