# ============================================================================
#  Installateur guidé — App Gestion (centre social)
#  Usage : clic droit sur ce fichier -> "Exécuter avec PowerShell"
#  Ce script installe TOUT : Python, PostgreSQL, l'application,
#  le service Windows (démarrage automatique) et la sauvegarde quotidienne.
# ============================================================================
$ErrorActionPreference = "Stop"

# --- 0. Élévation administrateur -------------------------------------------
$estAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $estAdmin) {
    Write-Host "Demande des droits administrateur..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$global:LogDir = Join-Path $env:TEMP "AppGestion-install"
New-Item -ItemType Directory -Force -Path $global:LogDir | Out-Null
$global:LogFile = Join-Path $global:LogDir ("installation-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
"=== Journal d'installation App Gestion - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Set-Content -Path $global:LogFile -Encoding UTF8

function Horodatage { Get-Date -Format "HH:mm:ss" }
function Log($texte) { Add-Content -Path $global:LogFile -Value "[$(Horodatage)] $texte" -Encoding UTF8 }
function Etape($texte) { Write-Host "`n=== $texte ===" -ForegroundColor Cyan; Log "=== $texte ===" }
function Ok($texte)    { Write-Host "  [OK] $texte" -ForegroundColor Green; Log "[OK] $texte" }
function Info($texte)  { Write-Host "  $texte"; Log $texte }
function Warn($texte)  { Write-Host "  [ATTENTION] $texte" -ForegroundColor Yellow; Log "[ATTENTION] $texte" }
function Err($texte)   { Write-Host "  [ERREUR] $texte" -ForegroundColor Red; Log "[ERREUR] $texte" }

function MotDePasseAleatoire([int]$longueur = 24) {
    $alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    -join (1..$longueur | ForEach-Object { $alphabet[(Get-Random -Maximum $alphabet.Length)] })
}

function DureeLisible([datetime]$debut) {
    $ecoule = New-TimeSpan -Start $debut -End (Get-Date)
    if ($ecoule.TotalMinutes -lt 1) { return ("{0}s" -f [int]$ecoule.TotalSeconds) }
    return ("{0}min {1}s" -f [int]$ecoule.TotalMinutes, $ecoule.Seconds)
}

function Ajouter-FichierAuJournal($chemin, $titre) {
    if (Test-Path $chemin) {
        Add-Content -Path $global:LogFile -Value "`n--- $titre ---" -Encoding UTF8
        Get-Content $chemin -ErrorAction SilentlyContinue | Add-Content -Path $global:LogFile -Encoding UTF8
    }
}

function Executer-AvecSuivi($nom, $fichier, $arguments, [int]$intervalleSecondes = 15, [string]$messageAttente = "") {
    Info "$nom : démarrage..."
    if ($arguments) { Log "Commande : $fichier $arguments" } else { Log "Commande : $fichier" }

    $stdout = Join-Path $global:LogDir ("{0}-out.log" -f (($nom -replace '[^a-zA-Z0-9_-]', '_')))
    $stderr = Join-Path $global:LogDir ("{0}-err.log" -f (($nom -replace '[^a-zA-Z0-9_-]', '_')))
    Remove-Item $stdout, $stderr -ErrorAction SilentlyContinue

    $debut = Get-Date
    $process = Start-Process -FilePath $fichier -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $dernierMessage = Get-Date

    while (-not $process.HasExited) {
        Start-Sleep -Seconds 2
        if (((Get-Date) - $dernierMessage).TotalSeconds -ge $intervalleSecondes) {
            $detail = if ($messageAttente) { " — $messageAttente" } else { "" }
            Info "$nom toujours en cours ($(DureeLisible $debut))$detail"
            $dernierMessage = Get-Date
        }
    }

    $process.WaitForExit()
    Ajouter-FichierAuJournal $stdout "$nom - sortie standard"
    Ajouter-FichierAuJournal $stderr "$nom - erreurs"

    if ($process.ExitCode -ne 0) {
        Err "$nom a retourné le code $($process.ExitCode). Voir le journal : $global:LogFile"
    } else {
        Ok "$nom terminé ($(DureeLisible $debut))"
    }
    return $process.ExitCode
}

function Trouver-Psql {
    Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
}

function Attendre-Fichier($description, $scriptBlock, [int]$tentatives = 24, [int]$pauseSecondes = 5) {
    for ($i = 1; $i -le $tentatives; $i++) {
        $resultat = & $scriptBlock
        if ($resultat) { return $resultat }
        Info "$description : attente... ($i/$tentatives)"
        Start-Sleep -Seconds $pauseSecondes
    }
    return $null
}

function Attendre-PostgreSQL($psql, $port, $motDePasseSuper, [int]$tentatives = 36) {
    Info "Vérification que PostgreSQL répond sur 127.0.0.1:$port..."
    $env:PGPASSWORD = $motDePasseSuper
    try {
        for ($i = 1; $i -le $tentatives; $i++) {
            $sortie = & $psql.FullName -w -U postgres -h 127.0.0.1 -p $port -tAc "SELECT 1" 2>&1
            if ($LASTEXITCODE -eq 0 -and (($sortie | Out-String).Trim()) -eq "1") {
                Ok "PostgreSQL répond correctement"
                return $true
            }
            Info "PostgreSQL installé mais pas encore prêt pour les commandes SQL... ($i/$tentatives)"
            Start-Sleep -Seconds 5
        }
        return $false
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "+--------------------------------------------------------------+" -ForegroundColor Cyan
Write-Host "¦      INSTALLATION DE L'APPLICATION DE GESTION                  ¦" -ForegroundColor Cyan
Write-Host "¦  Répondez aux questions (Entrée = choix par défaut).          ¦" -ForegroundColor Cyan
Write-Host "¦  L'installation prend 10 à 20 minutes selon la connexion.     ¦" -ForegroundColor Cyan
Write-Host "+--------------------------------------------------------------+" -ForegroundColor Cyan
Write-Host ""
Write-Host "Journal détaillé : $global:LogFile" -ForegroundColor Yellow
Write-Host "Si une étape semble bloquée, laissez cette fenêtre ouverte et consultez ce fichier." -ForegroundColor Yellow

# --- 1. Questions -----------------------------------------------------------
Etape "1/8 - Questions de configuration"

$dossierDefaut = "C:\AppGestion"
$dossier = Read-Host "Dossier d'installation [$dossierDefaut]"
if ([string]::IsNullOrWhiteSpace($dossier)) { $dossier = $dossierDefaut }

$portDefaut = "8000"
$port = Read-Host "Port de l'application [$portDefaut]"
if ([string]::IsNullOrWhiteSpace($port)) { $port = $portDefaut }

$reseau = Read-Host "L'application doit-elle être accessible depuis d'autres postes du réseau ? (o/N)"
$hote = "127.0.0.1"
if ($reseau -match "^[oO]") { $hote = "0.0.0.0" }

$pgPortDefaut = "5432"
$pgPort = Read-Host "Port PostgreSQL [$pgPortDefaut]"
if ([string]::IsNullOrWhiteSpace($pgPort)) { $pgPort = $pgPortDefaut }

$mdpSuper = MotDePasseAleatoire
$mdpApp = MotDePasseAleatoire
$reponse = Read-Host "Mot de passe de la base de données (Entrée = généré automatiquement)"
if (-not [string]::IsNullOrWhiteSpace($reponse)) { $mdpApp = $reponse }

Ok "Configuration saisie : dossier=$dossier, port application=$port, port PostgreSQL=$pgPort, accès=$hote"

# --- 2. Python --------------------------------------------------------------
Etape "2/8 - Python"
$python = Get-Command python -ErrorAction SilentlyContinue
$pythonOk = $false
if ($python) {
    $version = & python --version 2>&1
    Info "Python détecté : $version ($($python.Source))"
    if ($version -match "Python 3\.(1[1-9]|[2-9][0-9])") { $pythonOk = $true; Ok "$version déjà installé" }
}
if (-not $pythonOk) {
    Warn "Python 3.11+ introuvable : installation de Python 3.13 avec winget."
    $code = Executer-AvecSuivi "Installation Python 3.13" "winget" "install --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements" 20 "winget peut rester silencieux pendant le téléchargement"
    if ($code -ne 0) { throw "Échec de l'installation de Python. Installez-le depuis python.org puis relancez ce script. Journal : $global:LogFile" }
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    Ok "Python installé"
}

# --- 3. PostgreSQL ----------------------------------------------------------
Etape "3/8 - PostgreSQL"
Info "Cette étape est souvent la plus longue. L'installeur officiel PostgreSQL peut rester muet pendant 5 à 15 minutes."
Info "Le script affiche maintenant un message régulier et écrit tout dans : $global:LogFile"

$psql = Trouver-Psql
$postgresDejaInstalle = $false
if ($psql) {
    $postgresDejaInstalle = $true
    Ok "PostgreSQL déjà installé : $($psql.FullName)"
    Warn "Un mot de passe postgres existant est nécessaire pour créer/mettre à jour la base."
    $mdpSuper = Read-Host "Mot de passe du super-utilisateur 'postgres' existant"
} else {
    Info "Installation de PostgreSQL 16 avec winget. Ne fermez pas cette fenêtre."
    $overridePg = "--mode unattended --unattendedmodeui none --superpassword $mdpSuper --serverport $pgPort"
    $argsPg = "install --id PostgreSQL.PostgreSQL.16 --silent --accept-package-agreements --accept-source-agreements --override `"$overridePg`""
    $codePg = Executer-AvecSuivi "Installation PostgreSQL 16" "winget" $argsPg 20 "installation serveur/base de données en cours"

    $psql = Attendre-Fichier "Recherche de psql.exe après installation" { Trouver-Psql } 24 5
    if (-not $psql) {
        throw "PostgreSQL semble ne pas être installé ou psql.exe est introuvable. Code winget=$codePg. Journal : $global:LogFile"
    }
    if ($codePg -ne 0) {
        Warn "winget a retourné le code $codePg mais psql.exe existe : on continue avec prudence."
    }
    Ok "PostgreSQL installé : $($psql.FullName)"
}

if (-not (Attendre-PostgreSQL $psql $pgPort $mdpSuper)) {
    throw "PostgreSQL est installé mais ne répond pas avec le mot de passe fourni. Vérifiez le service PostgreSQL, le port $pgPort et le mot de passe 'postgres'. Journal : $global:LogFile"
}

Info "Création/mise à jour du rôle PostgreSQL 'appgestion'..."
$env:PGPASSWORD = $mdpSuper
try {
    $mdpAppSql = $mdpApp -replace "'", "''"
    $sql = @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'appgestion') THEN
    CREATE ROLE appgestion LOGIN PASSWORD '$mdpAppSql';
  ELSE
    ALTER ROLE appgestion WITH LOGIN PASSWORD '$mdpAppSql';
  END IF;
END `$`$;
"@
    & $psql.FullName -w -U postgres -h 127.0.0.1 -p $pgPort -v ON_ERROR_STOP=1 -c $sql 2>&1 | Tee-Object -FilePath $global:LogFile -Append | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Impossible de créer/mettre à jour le rôle appgestion." }

    Info "Vérification de l'existence de la base 'appgestion'..."
    $existe = (& $psql.FullName -w -U postgres -h 127.0.0.1 -p $pgPort -tAc "SELECT 1 FROM pg_database WHERE datname='appgestion'" 2>&1 | Tee-Object -FilePath $global:LogFile -Append | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Impossible de vérifier l'existence de la base appgestion." }

    if ($existe -ne "1") {
        Info "Création de la base 'appgestion'..."
        & $psql.FullName -w -U postgres -h 127.0.0.1 -p $pgPort -v ON_ERROR_STOP=1 -c "CREATE DATABASE appgestion OWNER appgestion ENCODING 'UTF8'" 2>&1 | Tee-Object -FilePath $global:LogFile -Append | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Impossible de créer la base appgestion." }
    } else {
        Ok "Base 'appgestion' déjà existante"
    }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
Ok "Base 'appgestion' prête (utilisateur 'appgestion')"

# --- 4. Copie de l'application ----------------------------------------------
Etape "4/8 - Copie de l'application"
$source = Split-Path -Parent $PSScriptRoot   # le script est dans <app>\installation\
Info "Source : $source"
Info "Destination : $dossier"
New-Item -ItemType Directory -Force -Path $dossier | Out-Null
robocopy $source $dossier /E /XD .git .venv __pycache__ instance /XF .env /TEE /LOG+:$global:LogFile /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Échec de la copie des fichiers (robocopy: $LASTEXITCODE). Journal : $global:LogFile" }
Ok "Fichiers copiés vers $dossier"

# --- 5. Environnement Python -------------------------------------------------
Etape "5/8 - Dépendances Python (plusieurs minutes)"
Set-Location $dossier
if (-not (Test-Path "$dossier\.venv")) {
    $codeVenv = Executer-AvecSuivi "Création de l'environnement virtuel" "python" "-m venv .venv" 10 "préparation de Python local à l'application"
    if ($codeVenv -ne 0) { throw "Échec de la création de l'environnement virtuel. Journal : $global:LogFile" }
} else {
    Ok "Environnement virtuel déjà présent"
}

$codePipUp = Executer-AvecSuivi "Mise à jour de pip" "$dossier\.venv\Scripts\python.exe" "-m pip install --upgrade pip" 20 "téléchargement éventuel depuis internet"
if ($codePipUp -ne 0) { throw "Échec de la mise à jour de pip. Journal : $global:LogFile" }

$codeReq = Executer-AvecSuivi "Installation des dépendances Python" "$dossier\.venv\Scripts\python.exe" "-m pip install -r requirements.txt" 20 "Flask, PostgreSQL, Excel, documents..."
if ($codeReq -ne 0) { throw "Échec de l'installation des dépendances Python. Journal : $global:LogFile" }
Ok "Dépendances installées"

# --- 6. Configuration (.env) -------------------------------------------------
Etape "6/8 - Configuration"
$secretKey = MotDePasseAleatoire 64
$urlPublique = "http://$(if ($hote -eq '0.0.0.0') { $env:COMPUTERNAME } else { '127.0.0.1' }):$port"
@"
# Fichier généré par l'installateur le $(Get-Date -Format "yyyy-MM-dd HH:mm")
# NE PAS PARTAGER : contient les mots de passe de l'application.
ERP_ENV=production
SECRET_KEY=$secretKey
DATABASE_URL=postgresql://appgestion:$mdpApp@127.0.0.1:$pgPort/appgestion
ERP_HOST=$hote
ERP_PORT=$port
ERP_THREADS=12
ERP_PUBLIC_BASE_URL=$urlPublique
ERP_LOG_DIR=$dossier\logs
APP_UPLOAD_DIR=$dossier\uploads
DB_AUTO_UPGRADE_ON_START=1
PASSWORD_RESET_ALLOW_DEBUG_LINK=0
"@ | Set-Content -Path "$dossier\.env" -Encoding UTF8
New-Item -ItemType Directory -Force -Path "$dossier\logs", "$dossier\uploads" | Out-Null
Ok "Configuration écrite dans $dossier\.env"
Ok "Logs applicatifs : $dossier\logs"
Ok "Uploads : $dossier\uploads"

# --- 7. Service Windows (démarrage automatique) ------------------------------
Etape "7/8 - Service Windows"
$nssm = "$dossier\tools\nssm\win64\nssm.exe"
if (-not (Test-Path $nssm)) {
    Info "Téléchargement de NSSM (gestionnaire de service)..."
    $zip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath "$env:TEMP\nssm-extract" -Force
    New-Item -ItemType Directory -Force -Path "$dossier\tools\nssm\win64" | Out-Null
    Copy-Item "$env:TEMP\nssm-extract\nssm-2.24\win64\nssm.exe" $nssm -Force
    Ok "NSSM téléchargé"
} else {
    Ok "NSSM déjà présent"
}
New-Item -ItemType Directory -Force -Path "$dossier\logs" | Out-Null
$serviceExiste = Get-Service -Name "AppGestion" -ErrorAction SilentlyContinue
if ($serviceExiste) {
    Info "Service AppGestion existant détecté : arrêt et remplacement..."
    & $nssm stop AppGestion 2>&1 | Tee-Object -FilePath $global:LogFile -Append | Out-Null
    & $nssm remove AppGestion confirm 2>&1 | Tee-Object -FilePath $global:LogFile -Append | Out-Null
}
& $nssm install AppGestion "$dossier\.venv\Scripts\python.exe" "$dossier\run_waitress.py" | Out-Null
& $nssm set AppGestion AppDirectory $dossier | Out-Null
& $nssm set AppGestion Start SERVICE_AUTO_START | Out-Null
& $nssm set AppGestion AppStdout "$dossier\logs\service-out.log" | Out-Null
& $nssm set AppGestion AppStderr "$dossier\logs\service-err.log" | Out-Null
& $nssm start AppGestion 2>&1 | Tee-Object -FilePath $global:LogFile -Append | Out-Null
Ok "Service 'AppGestion' installé (démarre automatiquement avec Windows)"

if ($hote -eq "0.0.0.0") {
    Info "Ouverture du pare-feu Windows sur le port $port..."
    netsh advfirewall firewall delete rule name="AppGestion" 2>$null | Out-Null
    netsh advfirewall firewall add rule name="AppGestion" dir=in action=allow protocol=TCP localport=$port | Out-Null
    Ok "Pare-feu ouvert sur le port $port (accès réseau local)"
}

# Sauvegarde quotidienne (2h du matin)
if (Test-Path "$dossier\tools\backup_instance.py") {
    Info "Programmation de la sauvegarde quotidienne..."
    schtasks /Create /F /TN "AppGestion-Sauvegarde" /SC DAILY /ST 02:00 /RU SYSTEM `
        /TR "`"$dossier\.venv\Scripts\python.exe`" `"$dossier\tools\backup_instance.py`"" | Out-Null
    Ok "Sauvegarde quotidienne programmée (2h00)"
}

# --- 8. Vérification ----------------------------------------------------------
Etape "8/8 - Vérification du démarrage"
$pret = $false
foreach ($i in 1..24) {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/healthz" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $pret = $true; break }
    } catch {
        Info "L'application démarre... ($i/24). Logs service : $dossier\logs\service-err.log"
    }
}

Write-Host ""
if ($pret) {
    Write-Host "+--------------------------------------------------------------+" -ForegroundColor Green
    Write-Host "¦  INSTALLATION TERMINÉE AVEC SUCCÈS                             ¦" -ForegroundColor Green
    Write-Host "+--------------------------------------------------------------+" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Adresse de l'application : $urlPublique" -ForegroundColor White
    Write-Host "  Dossier d'installation   : $dossier"
    Write-Host "  Journal installateur     : $global:LogFile"
    Write-Host "  Logs du service          : $dossier\logs\service-out.log / service-err.log"
    Write-Host "  Journal applicatif       : $dossier\logs\erreurs.log"
    Write-Host "  Configuration            : $dossier\.env  (à conserver précieusement)"
    if (-not $postgresDejaInstalle) {
        Write-Host "  Mot de passe 'postgres'  : $mdpSuper  (NOTEZ-LE)" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  Le navigateur va s'ouvrir : créez votre compte administrateur." -ForegroundColor Cyan
    Start-Process "http://127.0.0.1:$port/setup/"
} else {
    Write-Host "L'application n'a pas répondu à temps." -ForegroundColor Red
    Write-Host "Journal installateur : $global:LogFile" -ForegroundColor Red
    Write-Host "Logs service        : $dossier\logs\service-err.log" -ForegroundColor Red
    Write-Host "Essayez aussi : Get-Service AppGestion ; Get-Service postgresql*" -ForegroundColor Yellow
}
Write-Host ""
Read-Host "Appuyez sur Entrée pour fermer"
