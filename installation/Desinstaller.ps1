# ============================================================================
#  Désinstallation — App Gestion
#  Arrête et retire le service Windows et la tâche de sauvegarde.
#  NE SUPPRIME NI la base de données PostgreSQL NI le dossier de
#  l'application (vos données restent en place).
# ============================================================================
$ErrorActionPreference = "Stop"

$estAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $estAdmin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$dossierDefaut = "C:\AppGestion"
$dossier = Read-Host "Dossier d'installation [$dossierDefaut]"
if ([string]::IsNullOrWhiteSpace($dossier)) { $dossier = $dossierDefaut }

$nssm = "$dossier\tools\nssm\win64\nssm.exe"
if ((Get-Service -Name "AppGestion" -ErrorAction SilentlyContinue) -and (Test-Path $nssm)) {
    & $nssm stop AppGestion 2>$null | Out-Null
    & $nssm remove AppGestion confirm | Out-Null
    Write-Host "[OK] Service 'AppGestion' retiré" -ForegroundColor Green
} else {
    Write-Host "Service 'AppGestion' introuvable (déjà retiré ?)"
}

schtasks /Delete /F /TN "AppGestion-Sauvegarde" 2>$null | Out-Null
netsh advfirewall firewall delete rule name="AppGestion" 2>$null | Out-Null

Write-Host ""
Write-Host "Désinstallation du service terminée." -ForegroundColor Green
Write-Host "Vos données sont conservées :"
Write-Host "  - Base PostgreSQL 'appgestion' (intacte)"
Write-Host "  - Dossier $dossier (fichiers, pièces jointes, sauvegardes)"
Write-Host "Supprimez-les manuellement si vous le souhaitez vraiment."
Read-Host "Appuyez sur Entrée pour fermer"
