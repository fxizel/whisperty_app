# Spécifications — Whisperty

> **Dictée vocale 100 % locale pour Windows 10/11.** Dossier de spécifications fonctionnelles
> et techniques rédigé selon une approche d'analyse métier : vision produit, personas,
> cas d'utilisation et exigences classées **FURPS+**.

## Objet de ce dossier

Ce dossier formalise *ce que fait* Whisperty et *pourquoi*, indépendamment de *comment* le
code l'implémente. Il sert de référence partagée entre les parties prenantes (utilisateurs,
mainteneurs, équipe IT) pour cadrer les évolutions, prioriser et tester.

Les exigences sont **dérivées du produit existant** (code, `config.yaml`, `README.md`,
`CLAUDE.md`) : il s'agit d'une spécification *descriptive et normative* de l'état actuel
(V2), enrichie des règles de gestion implicites et des questions ouvertes.

## Structure logique

| # | Document | Contenu |
|---|----------|---------|
| 01 | [Introduction & contexte](01-introduction-et-contexte.md) | Objet, périmètre, glossaire, vision produit, hypothèses et contraintes |
| 02 | [Parties prenantes & personas](02-personas.md) | Cartographie des acteurs et fiches personas |
| 03 | [Cas d'utilisation](03-cas-utilisation.md) | Acteurs, diagramme, catalogue et fiches détaillées des UC |
| 04 | [Exigences FURPS+](04-exigences-furps.md) | Exigences fonctionnelles et non fonctionnelles + règles de gestion |
| 05 | [Traçabilité & risques](05-tracabilite-et-risques.md) | Matrice persona → UC → exigence, risques, questions ouvertes |

## Comment lire ce dossier

1. Commencez par l'**introduction** pour la vision et la contrainte cardinale (zéro réseau).
2. Les **personas** donnent le « pour qui ».
3. Les **cas d'utilisation** donnent le « quoi », sous forme de scénarios.
4. Les **exigences FURPS+** donnent les critères vérifiables, tracés vers les UC.
5. La **matrice de traçabilité** relie le tout et liste les zones d'incertitude.

## Conventions de référencement

| Préfixe | Catégorie | Exemple |
|---------|-----------|---------|
| `P-xx` | Persona | `P-01` |
| `UC-xx` | Cas d'utilisation | `UC-01` |
| `FR-xx` | Exigence — **F**unctionality | `FR-03` |
| `US-xx` | Exigence — **U**sability | `US-02` |
| `RE-xx` | Exigence — **R**eliability | `RE-04` |
| `PE-xx` | Exigence — **P**erformance | `PE-01` |
| `SU-xx` | Exigence — **S**upportability | `SU-05` |
| `CO-xx` | Contrainte de conception (**FURPS+** : le « + ») | `CO-01` |
| `BR-xx` | Règle de gestion (*business rule*) | `BR-02` |

Priorisation **MoSCoW** : `M` (Must), `S` (Should), `C` (Could), `W` (Won't / hors périmètre).

---

*Version du dossier : 1.0 — 2026-06-21. Produit couvert : Whisperty V2.*
