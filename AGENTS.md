# AGENTS.md

Les consignes de ce dépôt vivent dans **[CLAUDE.md](CLAUDE.md)**, source unique : projet et
contrainte cardinale (zéro donnée sortante), commandes, architecture du pipeline, invariants
de **concurrence** (verrous, ordre imposé, verrous feuilles), décisions d'architecture à
respecter et conventions de code. **Lire ce fichier avant toute modification.**

Ce fichier ne duplique plus ce contenu : les deux versions avaient divergé de plus de cent
lignes, et un agent lisant la copie périmée travaillait sur des invariants faux.

En complément, dans `.claude/` (utile quel que soit l'agent, ce ne sont que des consignes en
Markdown) :

- `agents/concurrency-reviewer.md` — relecture des invariants de concurrence, à lancer après
  toute modification de `app.py`, `live.py`, `conference.py`, `recorder.py`, `diarization.py`,
  `gui.py`, `tray.py` ou de tout code manipulant threads, verrous ou callbacks ;
- `agents/privacy-auditor.md` — audit zéro-réseau, à lancer après tout changement de
  dépendances, avant une release, ou après l'ajout de code d'E/S ;
- `skills/test-doubles/SKILL.md` — conventions de la suite de tests hors-ligne (doublures des
  dépendances natives), à consulter **avant** d'écrire ou de modifier un test ;
- `skills/release/SKILL.md` — procédure de release ordonnée (bump de version, tests, build
  PyInstaller, installeur, signature) et pièges du projet.

Backlog produit : `docs/backlog.md`, spécifications : `docs/specifications/`, historique
fonctionnel : `CHANGELOG.md`. Les dettes techniques connues sont signalées en commentaire
à l'endroit concerné dans le code, pas dans un document à part.
