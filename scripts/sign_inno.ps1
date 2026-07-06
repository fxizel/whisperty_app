<#
.SYNOPSIS
    Point d'entrée SignTool pour Inno Setup (reçoit le chemin du fichier à signer).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$FilePath
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\sign.ps1" -Path $FilePath
exit $LASTEXITCODE
