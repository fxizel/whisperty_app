<#
.SYNOPSIS
    Compile l'installeur Whisperty (installer\whisperty.iss) avec Inno Setup.

.DESCRIPTION
    Localise ISCC.exe (compilateur Inno Setup 6) puis produit
    dist\installer\Whisperty-Setup-<version>.exe.
    Prérequis : avoir lancé scripts\build.ps1 (dossier dist\whisperty\ présent).

.EXAMPLE
    .\scripts\make_installer.ps1
#>
[CmdletBinding()]
param(
    [switch]$Sign,
    [string]$SignPfx = "",
    [string]$SignPassword = "",
    [string]$SignThumbprint = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# --- 1. dist\whisperty présent ? -----------------------------------------------
if (-not (Test-Path "$root\dist\whisperty\whisperty.exe")) {
    throw "dist\whisperty\whisperty.exe introuvable. Lancez d'abord : .\scripts\build.ps1"
}

# --- 2. Localiser ISCC.exe -----------------------------------------------------
$iscc = $null
$onPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($onPath) { $iscc = $onPath.Source }
if (-not $iscc) {
    foreach ($p in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $p) { $iscc = $p; break }
    }
}
if (-not $iscc) {
    Write-Host "Inno Setup (ISCC.exe) introuvable." -ForegroundColor Red
    Write-Host "Installez-le, puis relancez :"
    Write-Host "  winget install --id JRSoftware.InnoSetup -e"
    Write-Host "  (ou https://jrsoftware.org/isdl.php)"
    exit 1
}
Write-Host "Inno Setup : $iscc" -ForegroundColor Cyan

# --- 2b. Version (source unique : whisperty.version) ---------------------------
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$version = (& $py -c "from whisperty.version import __version__; print(__version__)").Trim()
$versionInfo = (& $py -c "from whisperty.version import version_info; print(version_info())").Trim()
Write-Host "Version Whisperty : $version ($versionInfo)" -ForegroundColor Cyan

# --- 3. Signature de l'application (si demandée et pas déjà faite au build) ----
$doSign = $Sign.IsPresent -or $SignPfx -or $SignThumbprint -or $env:WHISPERTY_SIGN_PFX -or $env:WHISPERTY_SIGN_THUMBPRINT
if ($doSign) {
    $exe = "$root\dist\whisperty\whisperty.exe"
    $sig = Get-AuthenticodeSignature -FilePath $exe -ErrorAction SilentlyContinue
    if ($sig -and $sig.Status -eq "Valid") {
        Write-Host "whisperty.exe déjà signé." -ForegroundColor DarkGray
    } else {
        Write-Host "Signature Authenticode de whisperty.exe…" -ForegroundColor Cyan
        $signArgs = @{ Path = $exe }
        if ($SignPfx) { $signArgs.SignPfx = $SignPfx }
        if ($SignPassword) { $signArgs.SignPassword = $SignPassword }
        if ($SignThumbprint) { $signArgs.SignThumbprint = $SignThumbprint }
        & "$PSScriptRoot\sign.ps1" @signArgs
        if ($LASTEXITCODE -ne 0) { throw "Échec de la signature Authenticode." }
    }
}

# --- 4. Compiler (Inno Setup signe le setup si -Sign / certificat configuré) ---
$isccArgs = @(
    "/DMyAppVersion=$version",
    "/DMyAppVersionInfo=$versionInfo",
    "$root\installer\whisperty.iss"
)
if ($doSign) {
    $signScript = Join-Path $PSScriptRoot "sign_inno.ps1"
    $isccArgs = @(
        "/DSignEnabled=1",
        "/DSignScript=$signScript"
    ) + $isccArgs
}
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) { throw "Échec de la compilation Inno Setup (code $LASTEXITCODE)." }

$out = Get-ChildItem "$root\dist\installer\*.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($out) {
    Write-Host ""
    Write-Host "Installeur produit : $($out.FullName)" -ForegroundColor Green
    if ($doSign) {
        $setupSig = Get-AuthenticodeSignature -FilePath $out.FullName -ErrorAction SilentlyContinue
        if ($setupSig -and $setupSig.Status -eq "Valid") {
            Write-Host "  Signature : valide ($($setupSig.SignerCertificate.Subject))" -ForegroundColor Green
        } else {
            Write-Host "  Attention : signature absente ou invalide — SmartScreen peut encore s'afficher." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  Non signé : SmartScreen affichera un avertissement. Utilisez -Sign (voir installer\README.md)." -ForegroundColor Yellow
    }
}
