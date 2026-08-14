<#
.SYNOPSIS
    Signature Authenticode (signtool) pour whisperty.exe et l'installeur Inno Setup.

.DESCRIPTION
    Supprime l'avertissement Microsoft Defender SmartScreen à l'exécution du setup,
    à condition d'utiliser un certificat de signature de code émis par une autorité
    de certification reconnue (OV ou EV). Sans certificat valide, SmartScreen
    affichera toujours « Windows a protégé votre PC ».

    Configuration (par ordre de priorité) :
      - paramètres -SignPfx / -SignPassword / -SignThumbprint
      - variables d'environnement WHISPERTY_SIGN_PFX, WHISPERTY_SIGN_PASSWORD,
        WHISPERTY_SIGN_THUMBPRINT, WHISPERTY_SIGN_TIMESTAMP

.EXAMPLE
    $env:WHISPERTY_SIGN_PFX = "C:\certs\softcom.pfx"
    $env:WHISPERTY_SIGN_PASSWORD = "secret"
    .\scripts\sign.ps1 -Path .\dist\whisperty\whisperty.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, ValueFromPipeline)]
    [string[]]$Path,

    [string]$SignPfx = $env:WHISPERTY_SIGN_PFX,
    [string]$SignPassword = $env:WHISPERTY_SIGN_PASSWORD,
    [string]$SignThumbprint = $env:WHISPERTY_SIGN_THUMBPRINT,
    [string]$TimestampUrl = $(if ($env:WHISPERTY_SIGN_TIMESTAMP) { $env:WHISPERTY_SIGN_TIMESTAMP } else { "http://timestamp.digicert.com" }),
    [switch]$Verify
)

$ErrorActionPreference = "Stop"

function Find-SignTool {
    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path $kitsRoot) {
        $candidate = Get-ChildItem $kitsRoot -Directory |
            Sort-Object { [version]$_.Name } -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($candidate) { return $candidate }
    }
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

function Get-SignCredentialArgs {
    if ($SignPfx -and (Test-Path -LiteralPath $SignPfx)) {
        if (-not $SignPassword) {
            throw "Mot de passe du certificat requis : -SignPassword ou WHISPERTY_SIGN_PASSWORD."
        }
        return @("/f", (Resolve-Path -LiteralPath $SignPfx).Path, "/p", $SignPassword)
    }
    if ($SignThumbprint) {
        return @("/sha1", $SignThumbprint.Trim())
    }
    throw @(
        "Certificat de signature introuvable."
        ""
        "Fournissez un fichier .pfx :"
        "  `$env:WHISPERTY_SIGN_PFX = 'C:\chemin\certificat.pfx'"
        "  `$env:WHISPERTY_SIGN_PASSWORD = 'mot-de-passe'"
        "ou un certificat du magasin Windows :"
        "  `$env:WHISPERTY_SIGN_THUMBPRINT = '<empreinte SHA1>'"
        ""
        "Voir installer/README.md § « Signature de code » pour obtenir un certificat."
    ) -join "`n"
}

$signtool = Find-SignTool
if (-not $signtool) {
    throw @(
        "signtool.exe introuvable."
        "Installez le Windows SDK (composant « Windows SDK Signing Tools for Desktop Apps »)"
        "ou Visual Studio Build Tools, puis relancez."
    ) -join "`n"
}

$credArgs = Get-SignCredentialArgs
$commonArgs = @(
    "sign",
    "/tr", $TimestampUrl,
    "/td", "sha256",
    "/fd", "sha256",
    "/v"
) + $credArgs

$signed = 0
foreach ($item in $Path) {
    if (-not (Test-Path -LiteralPath $item)) {
        throw "Fichier introuvable : $item"
    }
    $target = (Resolve-Path -LiteralPath $item).Path
    Write-Host "Signature : $target" -ForegroundColor Cyan
    & $signtool @commonArgs $target
    if ($LASTEXITCODE -ne 0) {
        throw "Échec signtool pour $target (code $LASTEXITCODE)."
    }
    if ($Verify) {
        & $signtool verify /pa /v $target
        if ($LASTEXITCODE -ne 0) {
            throw "Vérification de signature échouée pour $target."
        }
    }
    $signed++
}

Write-Host "Signature terminée ($signed fichier(s))." -ForegroundColor Green
