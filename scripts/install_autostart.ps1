# Whisperty — démarrage automatique avec Windows (par utilisateur, sans droits admin).
# Usage :
#   .\scripts\install_autostart.ps1                 # cible dist\whisperty.exe
#   .\scripts\install_autostart.ps1 -ExePath "C:\chemin\whisperty.exe"

param(
    # Layout onedir (cf. whisperty.spec / scripts\build.ps1) : dist\whisperty\whisperty.exe.
    [string]$ExePath = (Join-Path $PSScriptRoot "..\dist\whisperty\whisperty.exe")
)

# Compatible Windows PowerShell 5.1 (pas d'opérateur ?. qui exige PowerShell 7).
$resolved = Resolve-Path -LiteralPath $ExePath -ErrorAction SilentlyContinue
if (-not $resolved) {
    Write-Error "Exécutable introuvable. Compilez d'abord (pyinstaller whisperty.spec) ou passez -ExePath."
    exit 1
}
$ExePath = $resolved.Path

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Set-ItemProperty -Path $runKey -Name "Whisperty" -Value "`"$ExePath`""
Write-Host "Démarrage automatique activé : $ExePath"
