# Déploiement de Whisperty (build + installeur)

Cette procédure produit un **installeur Windows autonome** (`Whisperty-Setup-<version>.exe`)
déployable sur d'autres PC Windows 10/11 64 bits, **sans Python ni dépendances** sur la machine
cible. Conforme à la contrainte cardinale du projet : **aucune donnée ne sort de la machine** —
le seul accès réseau possible est le téléchargement initial du modèle (évitable en le bundlant).

## En bref

```powershell
# Depuis la racine du dépôt, dans le venv (.\.venv\Scripts\Activate.ps1) :
.\scripts\build.ps1            # 1) construit dist\whisperty\ (exe + modèle bundlé)
.\scripts\make_installer.ps1   # 2) produit dist\installer\Whisperty-Setup-<version>.exe
```

L'installeur résultant s'installe **par utilisateur** (aucun droit administrateur requis).

## 1. Construire le dossier applicatif — `scripts\build.ps1`

Produit `dist\whisperty\` : `whisperty.exe` (mode *onedir* PyInstaller) + toutes ses
dépendances natives + `config.yaml`/`dictionary.txt` éditables à côté de l'exe.

| Commande | Résultat |
|----------|----------|
| `.\scripts\build.ps1` | Bundle le modèle de `config.yaml` (`medium`) → **100 % hors-ligne** |
| `.\scripts\build.ps1 -Model small` | Bundle `small` (installeur plus léger, ~0,5 Go) |
| `.\scripts\build.ps1 -NoModel` | **Sans** modèle → installeur minimal ; modèle téléchargé au 1er lancement |

- Installe automatiquement PyInstaller dans le venv s'il manque.
- Quand un modèle est bundlé : `config.yaml` est patché vers `model: models/faster-whisper-<taille>`
  et `local_files_only: true` (zéro réseau à l'usage).
- Sans modèle : `local_files_only` passe à `false` pour autoriser le **seul** téléchargement
  initial (le modèle s'installe dans le cache utilisateur au premier lancement).

> Pré-stager un modèle séparément : `python scripts\fetch_model.py --model medium`
> (copie depuis le cache Hugging Face s'il est déjà présent ; sinon le télécharge une fois).

## 2. Construire l'installeur — `scripts\make_installer.ps1`

Nécessite **Inno Setup 6** sur la machine de build :

```powershell
winget install --id JRSoftware.InnoSetup -e
```

Le script localise `ISCC.exe` puis compile `installer\whisperty.iss` →
`dist\installer\Whisperty-Setup-<version>.exe`.

### Signature de code (supprimer l'avertissement SmartScreen)

L'écran bleu **« Windows a protégé votre PC »** (Microsoft Defender SmartScreen)
apparaît parce que l'installeur n'est **pas signé** avec un certificat Authenticode
reconnu. Il n'existe **aucun contournement logiciel** : il faut signer l'exe avec un
certificat émis par une autorité de certification de confiance.

| Type de certificat | SmartScreen au lancement | Coût indicatif |
|--------------------|--------------------------|----------------|
| **EV** (Extended Validation, clé sur token USB) | Confiance immédiate en général | ~400–700 €/an |
| **OV** (Organization Validation) | Réputation à construire (téléchargements) | ~200–400 €/an |
| Auto-signé / absent | Avertissement systématique | — |

Fournisseurs courants : DigiCert, Sectigo, SSL.com. Pour un projet open source,
[SignPath Foundation](https://signpath.org/) propose des certificats gratuits.

**Une fois le certificat obtenu** (fichier `.pfx` ou installé dans le magasin Windows) :

```powershell
# Variables de session (ne pas committer le .pfx ni le mot de passe)
$env:WHISPERTY_SIGN_PFX = "C:\chemin\softcom-codesign.pfx"
$env:WHISPERTY_SIGN_PASSWORD = "votre-mot-de-passe"

# Build + installeur signés
.\scripts\build.ps1 -Sign
.\scripts\make_installer.ps1 -Sign
```

Alternative avec certificat déjà dans le magasin Windows (empreinte SHA1) :

```powershell
$env:WHISPERTY_SIGN_THUMBPRINT = "ABCD1234…"
.\scripts\build.ps1 -Sign
.\scripts\make_installer.ps1 -Sign
```

Prérequis machine de build : **signtool.exe** (Windows SDK — composant « Signing Tools
for Desktop Apps », ou Visual Studio Build Tools).

Vérification après build :

```powershell
Get-AuthenticodeSignature .\dist\installer\Whisperty-Setup-*.exe
```

> L'éditeur affiché dans SmartScreen correspond au **sujet du certificat** (ex. « Softcom »).
> Il doit être cohérent avec `AppPublisher` dans `whisperty.iss` et `CompanyName` dans
> `scripts/gen_version_info.py`.

## Ce que fait l'installeur (`whisperty.iss`)

- **Installation par utilisateur** dans `%LocalAppData%\Programs\Whisperty` (sans admin).
  *Pourquoi* : l'app écrit `config.yaml` (édité depuis l'UI), `whisperty.db`, `logs\` et
  `transcriptions\` **à côté de l'exe** ; un dossier `Program Files` (lecture seule pour un
  utilisateur standard) ferait échouer ces écritures.
- Raccourci menu Démarrer ; raccourci Bureau (optionnel) ; **démarrage avec Windows**
  (optionnel, clé `HKCU\…\Run`).
- `config.yaml`/`dictionary.txt` posés **uniquement s'ils n'existent pas** : une mise à jour
  préserve les réglages et l'historique de l'utilisateur.
- Vérifie le runtime **Edge WebView2** ; s'il manque, prévient (non bloquant : la fenêtre
  ne s'ouvrira pas mais la dictée et la zone de notification fonctionnent).

## Sur la machine cible

| Composant | Requis ? |
|-----------|----------|
| Python / dépendances | **Non** (tout est embarqué dans l'exe) |
| Windows 10/11 64 bits | Oui |
| Microsoft Edge WebView2 Runtime | Pour la **fenêtre** uniquement (préinstallé sur Win10/11 récents ; sinon repli zone de notification) |
| Connexion réseau | **Non** si le modèle est bundlé ; sinon une fois, au 1er lancement |
| GPU NVIDIA + CUDA | Optionnel (sinon CPU int8) |

## Désinstallation

Via « Ajout/Suppression de programmes ». Les fichiers applicatifs et `logs\` sont supprimés ;
`config.yaml`, `dictionary.txt`, `whisperty.db` et `transcriptions\` sont **conservés**
(données utilisateur). Le démarrage automatique (clé `Run`) est retiré.
