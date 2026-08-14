<#
.SYNOPSIS
    Build de déploiement Whisperty : exécutable autonome (onedir) + config + dictionnaire
    + modèle Whisper bundlé, prêt à être empaqueté par l'installeur (Inno Setup).

.DESCRIPTION
    Produit  dist\whisperty\  contenant whisperty.exe et toutes ses dépendances, avec
    config.yaml / dictionary.txt À CÔTÉ (éditables) et, par défaut, le modèle Whisper
    dans models\ pour un fonctionnement 100 % hors-ligne sur le PC cible.

.PARAMETER Model
    Taille du modèle à bundler (tiny|base|small|medium|large-v3|turbo…).
    Défaut : la valeur de transcription.model dans config.yaml (si c'est un nom de taille).

.PARAMETER NoModel
    Ne bundle PAS de modèle (installeur léger). Le modèle sera téléchargé au 1er lancement
    (config local_files_only passée à false) — seule exception réseau du projet.

.EXAMPLE
    .\scripts\build.ps1                 # bundle le modèle de config.yaml (medium) — hors-ligne
.EXAMPLE
    .\scripts\build.ps1 -Model small    # bundle « small » (installeur plus léger)
.EXAMPLE
    .\scripts\build.ps1 -NoModel        # installeur minimal ; téléchargement au 1er lancement
#>
[CmdletBinding()]
param(
    [string]$Model = "",
    [switch]$NoModel,
    [switch]$Sign,
    [string]$SignPfx = "",
    [string]$SignPassword = "",
    [string]$SignThumbprint = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Sortie Python en UTF-8 (sinon cp1252 sur stdout redirigé -> UnicodeEncodeError sur
# les messages accentués/typographiques des scripts appelés).
$env:PYTHONUTF8 = "1"

function Invoke-Checked([scriptblock]$Block, [string]$What) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "Échec : $What (code $LASTEXITCODE)." }
}

# --- 1. Interpréteur Python (venv prioritaire) ---------------------------------
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv introuvable ; utilisation du python du PATH." -ForegroundColor Yellow
    $py = "python"
}

# --- 2. PyInstaller présent ? --------------------------------------------------
# Sonde via find_spec (sans import réel) : si le paquet est absent, AUCUNE trace n'est
# écrite sur stderr — seul le code de sortie l'indique. On évite ainsi le piège de
# PowerShell 5.1 où rediriger stderr (2>$null) d'une commande native sous
# $ErrorActionPreference='Stop' transforme la moindre écriture stderr (ici la traceback
# d'un import manquant) en NativeCommandError terminante.
& $py -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installation de PyInstaller…" -ForegroundColor Cyan
    Invoke-Checked { & $py -m pip install "pyinstaller>=6.0" } "pip install pyinstaller"
}

# --- 3. Icône (générée si absente) ---------------------------------------------
if (-not (Test-Path "$root\installer\whisperty.ico")) {
    Write-Host "Génération de l'icône…" -ForegroundColor Cyan
    Invoke-Checked { & $py "$root\scripts\make_icon.py" } "make_icon.py"
}

# --- 4. Nettoyage du build précédent (préserve un éventuel installeur dans dist) -
Remove-Item -Recurse -Force "$root\build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$root\dist\whisperty" -ErrorAction SilentlyContinue

# --- 4b. Métadonnées de version (exe Windows) ----------------------------------
$version = (& $py -c "from whisperty.version import __version__; print(__version__)").Trim()
Write-Host "Version Whisperty : $version" -ForegroundColor Cyan
Invoke-Checked { & $py "$root\scripts\gen_version_info.py" } "gen_version_info.py"

# --- 5. Build PyInstaller (onedir) ---------------------------------------------
Write-Host "Build PyInstaller (onedir)…" -ForegroundColor Cyan
Invoke-Checked { & $py -m PyInstaller --noconfirm "$root\whisperty.spec" } "PyInstaller"

$appDir = Join-Path $root "dist\whisperty"
if (-not (Test-Path "$appDir\whisperty.exe")) { throw "whisperty.exe introuvable dans $appDir." }

# --- 5b. Signature Authenticode (opt-in ; supprime SmartScreen si certificat valide) -
$doSign = $Sign.IsPresent -or $SignPfx -or $SignThumbprint -or $env:WHISPERTY_SIGN_PFX -or $env:WHISPERTY_SIGN_THUMBPRINT
if ($doSign) {
    Write-Host "Signature Authenticode de whisperty.exe…" -ForegroundColor Cyan
    $signArgs = @{ Path = "$appDir\whisperty.exe" }
    if ($SignPfx) { $signArgs.SignPfx = $SignPfx }
    if ($SignPassword) { $signArgs.SignPassword = $SignPassword }
    if ($SignThumbprint) { $signArgs.SignThumbprint = $SignThumbprint }
    & "$PSScriptRoot\sign.ps1" @signArgs
    if ($LASTEXITCODE -ne 0) { throw "Échec de la signature Authenticode." }
}

# --- 6. Réglages utilisateur déposés À CÔTÉ de l'exe (éditables) ----------------
Copy-Item "$root\config.yaml" $appDir -Force
Copy-Item "$root\dictionary.txt" $appDir -Force
# Attributions des modèles (licence CC-BY-4.0 du modèle de diarisation : l'attribution
# doit accompagner l'application installée, pas rester dans le dépôt).
Copy-Item "$root\NOTICE.md" $appDir -Force

# Défauts d'expédition NEUTRES : le config.yaml du dépôt reflète le poste de dev
# (CUDA actif, LLM local LM Studio…). Un poste cible vierge n'a ni composants CUDA
# (repli CPU avec avertissement à chaque chargement) ni serveur LLM (tentative +
# échec journalisé à chaque dictée, notification « résumé indisponible » à chaque
# session). On expédie donc : CPU, IA locale et résumé désactivés (opt-in documenté,
# réactivables depuis l'écran Configuration). La diarisation est également remise
# à son défaut opt-in (CO-18 : « Locuteur N » ne doit s'activer que sur choix
# explicite de l'utilisateur), backend « mfcc » compris : le modèle ONNX n'est pas
# bundlé, expédier « onnx » pousserait donc vers un téléchargement dès la 1re réunion.
# Commentaires du YAML préservés.
Invoke-Checked {
    & $py -c "from whisperty.configio import update_yaml_file; update_yaml_file(r'$appDir\config.yaml', {'transcription.device':'cpu','transcription.compute_type':'int8','ai.enabled':False,'summary.enabled':False,'conference.speaker_diarization.enabled':False,'conference.speaker_diarization.backend':'mfcc'})"
} "patch config (défauts d'expédition)"

# --- 7. Modèle Whisper ---------------------------------------------------------
$bundleModel = $null
if (-not $NoModel) {
    if ($Model) {
        $bundleModel = $Model
    } else {
        # Lit transcription.model ; ne bundle que si c'est un nom de taille (pas un chemin).
        $cfgModel = (& $py -c "import yaml,io;d=yaml.safe_load(io.open(r'$root\config.yaml',encoding='utf-8')) or {};print((d.get('transcription') or {}).get('model','medium'))").Trim()
        if ($cfgModel -and ($cfgModel -notmatch '[\\/]')) {
            $bundleModel = $cfgModel
        } else {
            # transcription.model est un CHEMIN : basculer silencieusement en « sans
            # modèle + local_files_only:false » violerait la doctrine hors-ligne par
            # défaut. On exige un choix explicite.
            throw ("transcription.model du dépôt est un chemin (« $cfgModel »), impossible d'en déduire la taille à bundler. " +
                   "Relancez avec -Model <taille> (bundling hors-ligne) ou -NoModel (téléchargement au 1er lancement).")
        }
    }
}

if ($bundleModel) {
    Write-Host "Bundling du modèle « $bundleModel » (hors-ligne)…" -ForegroundColor Cyan
    Invoke-Checked { & $py "$root\scripts\fetch_model.py" --model $bundleModel --out "$appDir\models" } "fetch_model"
    # Nom de dossier : mirroir de scripts/fetch_model.py:_safe_name
    $base = ($bundleModel -split '[\\/]')[-1]
    $folder = if ($base.StartsWith("faster-")) { $base } else { "faster-whisper-$base" }
    # Pointe la config vers le modèle local + hors-ligne strict (zéro réseau à l'usage).
    Invoke-Checked {
        & $py -c "from whisperty.configio import update_yaml_file; update_yaml_file(r'$appDir\config.yaml', {'transcription.model':'models/$folder','transcription.local_files_only':True})"
    } "patch config (modèle local)"
} else {
    Write-Host "Aucun modèle bundlé : téléchargement au 1er lancement." -ForegroundColor Yellow
    # local_files_only=false pour autoriser ce 1er téléchargement (seule exception réseau).
    Invoke-Checked {
        & $py -c "from whisperty.configio import update_yaml_file; update_yaml_file(r'$appDir\config.yaml', {'transcription.local_files_only':False})"
    } "patch config (téléchargement 1er lancement)"
}

# --- 8. Récapitulatif ----------------------------------------------------------
$bytes = (Get-ChildItem -Recurse -File $appDir | Measure-Object -Property Length -Sum).Sum
$mb = [math]::Round($bytes / 1MB, 0)
Write-Host ""
Write-Host "Build terminé : $appDir ($mb Mo)" -ForegroundColor Green
Write-Host "  Version : $version"
if ($bundleModel) { Write-Host "  Modèle bundlé : $bundleModel (100 % hors-ligne)" }
else { Write-Host "  Sans modèle (léger) : téléchargé au 1er lancement" }
Write-Host ""
Write-Host "Étapes suivantes :"
Write-Host "  - Tester :    .\dist\whisperty\whisperty.exe"
Write-Host "  - Installeur : .\scripts\make_installer.ps1$(if ($doSign) { ' -Sign' })"
if (-not $doSign) {
    Write-Host "  - SmartScreen : ajoutez -Sign (certificat requis) — voir installer\README.md"
}
