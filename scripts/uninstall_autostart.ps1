# Whisperty — désactive le démarrage automatique avec Windows.
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $runKey -Name "Whisperty" -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name "Whisperty"
    Write-Host "Démarrage automatique désactivé."
} else {
    Write-Host "Aucune entrée de démarrage automatique trouvée."
}
