param(
  [string]$AppDir = "C:\AppGestion",
  [string]$TaskName = "AppGestionBackupDaily",
  [string]$At = "02:30"
)

$pythonExe = Join-Path $AppDir ".venv\Scripts\python.exe"
$backupScript = Join-Path $AppDir "tools\backup_instance.py"

if (!(Test-Path $pythonExe)) { throw "Python venv introuvable: $pythonExe" }
if (!(Test-Path $backupScript)) { throw "Script backup introuvable: $backupScript" }

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument $backupScript -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Tâche planifiée $TaskName créée ($At)."
