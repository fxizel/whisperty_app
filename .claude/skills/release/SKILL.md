---
name: release
description: Construit, package et signe une release Whisperty — bump de version, tests, build PyInstaller, installeur Inno Setup, signature Authenticode, changelog. Workflow ordonné avec les pièges du projet.
disable-model-invocation: true
---

# Release Whisperty

Chaîne de release complète, dans l'ordre. Ne saute pas d'étape : chaque script suppose
la précédente (l'installeur empaquette `dist\whisperty\`, le build patche la config
expédiée, la version vient d'une source unique).

## 0. Pré-vol

- Arbre git propre, sur `main` (ou branche de release convenue avec l'utilisateur).
- Suite de tests verte : `.\.venv\Scripts\python.exe -m pytest tests/ -q`
- Lint vert (même périmètre que la CI) :
  `.\.venv\Scripts\python.exe -m ruff check whisperty/ tests/`
- Demander à l'utilisateur : quelle version (majeure/mineure/patch), modèle bundlé ou
  `-NoModel`, signature ou non.

## 1. Bump de version — SOURCE UNIQUE

Éditer `whisperty/version.py` → `__version__ = "X.Y.Z"`. C'est la seule source :
fenêtre « À propos », métadonnées de l'exe (`gen_version_info.py`) et installeur
(`make_installer.ps1` la lit via `whisperty.version`) en dérivent. Ne versionner
nulle part ailleurs.

## 2. CHANGELOG.md

Ajouter l'entrée `X.Y.Z` (date du jour, changements depuis le dernier tag :
`git log <dernier-tag>..HEAD --oneline`). Style des entrées existantes, en français.

## 3. Build PyInstaller

```powershell
.\scripts\build.ps1                    # bundle le modèle de config.yaml (défaut, 100 % hors-ligne)
.\scripts\build.ps1 -Model small       # installeur plus léger
.\scripts\build.ps1 -NoModel           # minimal : téléchargement au 1er lancement
# Signature au build : -Sign, -SignThumbprint <hash> ou -SignPfx <chemin> -SignPassword <mdp>
# (ou variables d'env WHISPERTY_SIGN_PFX / WHISPERTY_SIGN_THUMBPRINT)
```

⚠️ Pièges connus :
- Le script patche automatiquement la COPIE expédiée de `config.yaml` (device `cpu`/
  `int8`, `ai.enabled: false`, `summary.enabled: false`, modèle bundlé +
  `local_files_only: true`). NE JAMAIS « corriger » le `config.yaml` du dépôt pour
  l'expédition : il reflète le poste de dev (CUDA, LLM local), c'est voulu.
- Produit `dist\whisperty\` en onedir ; `build\` est régénéré, ne rien y chercher.

## 4. Test de fumée du build

Lancer `.\dist\whisperty\whisperty.exe` : fenêtre OK, une dictée courte OK, quitter
via le tray. En cas d'échec de chargement du modèle, vérifier le patch config de
l'étape 3 avant tout autre diagnostic.

⚠️ La **dictée** de ce test revient à l'utilisateur : l'injection colle le texte
dans la fenêtre ACTIVE, donc un agent qui la déclenche écrit dans ce que l'humain
est en train de faire. Ce qu'un agent peut prouver seul, sans rien injecter :
`dist\whisperty\logs\whisperty.log` doit contenir « Chargement du modèle Whisper
'<chemin absolu>\models\faster-whisper-<taille>' » PUIS « Modèle Whisper chargé »
(le chemin absolu vaut vérification du bundling + de `local_files_only`).
Ces logs et `whisperty.db` sont exclus de l'installeur — inutile de nettoyer.

## 5. Installeur Inno Setup

```powershell
.\scripts\make_installer.ps1           # → dist\installer\Whisperty-Setup-<version>.exe
.\scripts\make_installer.ps1 -Sign     # signe l'exe (si pas déjà fait) ET le setup
```

Prérequis : Inno Setup 6 (`winget install --id JRSoftware.InnoSetup -e`).
⚠️ Avant d'installer quoi que ce soit, vérifier qu'il n'est pas DÉJÀ là : winget
l'installe en **per-user** (`%LocalAppData%\Programs\Inno Setup 6\ISCC.exe`), pas
dans `Program Files`. `make_installer.ps1` connaît les trois emplacements — le
plus simple est de le lancer et de lire son message.
L'installeur est par utilisateur (`%LocalAppData%\Programs\Whisperty`, sans admin) —
ne pas changer ce choix, l'app écrit sa config à côté de l'exe.

## 6. Vérifications finales

- Si signé : `Get-AuthenticodeSignature dist\installer\Whisperty-Setup-*.exe`
  → Status `Valid` (sinon SmartScreen s'affichera).
- Taille plausible (modèle bundlé ≈ plusieurs Go avec medium ; `-NoModel` ≈ centaines de Mo).
- Installer sur machine (ou VM) de test si disponible : mise à jour préserve
  `config.yaml`/`dictionary.txt` (`onlyifdoesntexist`).

## 7. Publication

Après validation de l'utilisateur uniquement :
```powershell
git add whisperty/version.py CHANGELOG.md   # + tout fichier versionné (README, web/, docs…)
git commit -m "release: X.Y.Z"
git tag X.Y.Z
git push origin main X.Y.Z
```
⚠️ **Tag SANS préfixe `v`** : c'est la convention des tags existants (`0.1.0`,
`0.3.0`) ET le déclencheur de `.github/workflows/release.yml`
(`tags: ['[0-9]*.[0-9]*.[0-9]*']`). Un tag `v1.0.0` ne déclencherait aucune
release, et la vérification de cohérence tag/`version.py` du workflow échouerait.

Le push du tag suffit : le workflow rejoue les tests, build en `-NoModel`, compile
l'installeur et crée une **release GitHub en BROUILLON** (à relire puis publier).
L'installeur local (`dist\installer\`, avec modèle bundlé) n'est PAS versionné dans
git — le joindre à la main s'il doit remplacer l'artefact CI :
`gh release upload X.Y.Z dist\installer\Whisperty-Setup-X.Y.Z.exe --clobber`.
