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
param()

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

# --- 3. Compiler ---------------------------------------------------------------
& $iscc "$root\installer\whisperty.iss"
if ($LASTEXITCODE -ne 0) { throw "Échec de la compilation Inno Setup (code $LASTEXITCODE)." }

$out = Get-ChildItem "$root\dist\installer\*.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($out) {
    Write-Host ""
    Write-Host "Installeur produit : $($out.FullName)" -ForegroundColor Green
}
