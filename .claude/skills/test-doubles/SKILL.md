---
name: test-doubles
description: Conventions de la suite de tests hors-ligne de Whisperty — doublures des dépendances natives (conftest + doublures locales par test) et patterns à réutiliser. À consulter AVANT d'écrire ou de modifier des tests.
user-invocable: false
---

# Écrire des tests pour Whisperty

La suite tourne SANS aucune dépendance binaire native (pas de micro, GPU, GUI, modèle)
et SANS réseau — elle passe sur Windows ET Linux (CI multi-OS), avec seulement
`requirements-test.txt` (numpy, PyYAML, soxr, pytest, pytest-cov). Seuil de couverture
CI : **80 %** (`--cov=whisperty --cov-fail-under=80`). Tout nouveau test doit respecter
ces contraintes.

## Doublures GLOBALES (tests/conftest.py)

Installées avant toute collecte, seulement si le module est absent de `sys.modules`
(idempotent) :

| Module | Doublure | Notes |
|---|---|---|
| `sounddevice` | neutre : `query_devices()=[]`, `InputStream=None`, `PortAudioError` | les tests recorder remplacent `whisperty.recorder.sd` par un faux complet |
| `soundcard` | neutre : aucun haut-parleur/micro, `default_speaker` lève | les tests loopback remplacent le module puis le restaurent |
| `pystray` | `Icon`/`Menu`/`MenuItem` no-op | suffit à construire `Tray`/`WhispertyApp` |
| `PIL` (+ `.Image`, `.ImageDraw`) | image factice permissive (tout attribut = no-op) | le dessin du logo n'est pas vérifié |

`conftest.py` ajoute aussi la racine du dépôt au `sys.path`. Ne PAS réinstaller ces
doublures dans un test ; les spécialiser localement à la place.

## Doublures PAR TEST (pattern sauvegarde → installation → restauration)

Pattern canonique du dépôt :

```python
previous = sys.modules.get("faster_whisper")
sys.modules["faster_whisper"] = mon_faux_module     # types.ModuleType(...)
try:
    ...  # test
finally:
    if previous is not None:
        sys.modules["faster_whisper"] = previous
    else:
        sys.modules.pop("faster_whisper", None)
```

Aides existantes à RÉUTILISER (ne pas dupliquer) :

- **pyperclip + pynput enregistreurs** : `tests/test_components.py`
  (`_install_injection_stubs`) et `tests/test_logic.py` — capturent copies
  presse-papiers et frappes pour vérifier l'injection.
- **faster_whisper (modèle factice)** : `tests/test_transcriber_load.py`
  (`_install_fake_faster_whisper(model_factory)`) — teste chargement, repli CPU, etc.
  Mettre `sys.modules["faster_whisper"] = None` simule le paquet absent (ImportError).
- **faster_whisper.utils.download_model** : `tests/test_ux.py` (`_fake_faster_whisper`)
  — téléchargement de modèle simulé (progression, échec, lenteur), zéro réseau.
- **kernel32 (instance unique)** : `tests/test_ux.py` (`_FakeKernel32`) — reproduit la
  sémantique mutex/évènements NOMMÉS en mémoire, injectée via `singleinstance._k32_cached`
  pour couvrir les chemins Windows sur la CI Linux. Les tests utilisent des noms
  d'objets Win32 UNIQUES (jamais le nom réel `Local\Whisperty.SingleInstance`).

## Règles

- Jamais d'appel réseau, jamais de vrai périphérique, jamais de vrai modèle.
- Toujours restaurer `sys.modules` en `finally` (sinon fuite entre tests).
- Les tests doivent passer sur Linux : pas d'API Win32 réelle sans doublure,
  chemins via `pathlib`/`tmp_path`.
- Conventions du dépôt : commentaires/docstrings en français,
  `from __future__ import annotations`, type hints.
- Fichiers temporaires : fixture pytest `tmp_path`, pas le dépôt.

## Lancer

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v                     # suite complète
.\.venv\Scripts\python.exe -m pytest tests/test_app.py -q          # un module
.\.venv\Scripts\python.exe -m pytest tests/ --cov=whisperty --cov-report=term-missing --cov-fail-under=80
python tests/test_logic.py                                          # variante autonome historique
```
