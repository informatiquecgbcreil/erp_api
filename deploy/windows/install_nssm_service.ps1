param(
  [string]$AppDir = "C:\AppGestion",
  [string]$ServiceName = "AppGestion",
  [string]$NssmPath = "C:\tools\nssm\win64\nssm.exe"
)

$pythonExe = Join-Path $AppDir ".venv\Scripts\python.exe"
$runScript = Join-Path $AppDir "run_waitress.py"

if (!(Test-Path $NssmPath)) { throw "NSSM introuvable: $NssmPath" }
if (!(Test-Path $pythonExe)) { throw "Python venv introuvable: $pythonExe" }
if (!(Test-Path $runScript)) { throw "run_waitress.py introuvable: $runScript" }

& $NssmPath install $ServiceName $pythonExe $runScript
& $NssmPath set $ServiceName AppDirectory $AppDir
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout (Join-Path $AppDir "logs\service-out.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $AppDir "logs\service-err.log")
& $NssmPath start $ServiceName

Write-Host "Service $ServiceName installé et démarré."
