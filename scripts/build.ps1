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
    [switch]$NoModel
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
& $py -c "import PyInstaller" 2>$null
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

# --- 6. Réglages utilisateur déposés À CÔTÉ de l'exe (éditables) ----------------
Copy-Item "$root\config.yaml" $appDir -Force
Copy-Item "$root\dictionary.txt" $appDir -Force

# --- 7. Modèle Whisper ---------------------------------------------------------
$bundleModel = $null
if (-not $NoModel) {
    if ($Model) {
        $bundleModel = $Model
    } else {
        # Lit transcription.model ; ne bundle que si c'est un nom de taille (pas un chemin).
        $cfgModel = (& $py -c "import yaml,io;d=yaml.safe_load(io.open(r'$root\config.yaml',encoding='utf-8')) or {};print((d.get('transcription') or {}).get('model','medium'))").Trim()
        if ($cfgModel -and ($cfgModel -notmatch '[\\/]')) { $bundleModel = $cfgModel }
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
Write-Host "  - Installeur : compiler installer\whisperty.iss avec Inno Setup (ISCC.exe)"
