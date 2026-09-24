#ifndef Payload
  #error Payload directory required
#endif
#ifndef Deliverables
  #define Deliverables "."
#endif
[Setup]
AppId={{867301CB-6143-4C33-BF2A-EDC1CFC2DE6A}
AppName=Mon Centre Social
AppVersion=1.0.0-rc2
AppVerName=Mon Centre Social 1.0.0-rc2
AppPublisher=Mon Centre Social — projet associatif
AppPublisherURL=https://github.com/informatiquecgbcreil/erp_api
DefaultDirName={autopf}\Mon Centre Social
DefaultGroupName=Mon Centre Social
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#Deliverables}
OutputBaseFilename=Mon-Centre-Social-1.0.0-rc2-Setup-x64
SetupIconFile={#Payload}\mon-centre-social.ico
UninstallDisplayIcon={app}\MonCentreSocial.exe
LicenseFile={#Payload}\application\LICENSE
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=115
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
SetupLogging=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "vc_redist.x64.exe"
Source: "{#Payload}\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\Mon Centre Social"; Filename: "{app}\MonCentreSocial.exe"; Parameters: "--tray"
Name: "{group}\Configurer Mon Centre Social"; Filename: "{app}\MonCentreSocial.exe"; Parameters: "--configure"
Name: "{group}\Guide d'installation"; Filename: "{app}\GUIDE-WINDOWS.md"
Name: "{autodesktop}\Mon Centre Social"; Filename: "{app}\MonCentreSocial.exe"; Parameters: "--tray"

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MonCentreSocial"; ValueData: """{app}\MonCentreSocial.exe"" --tray"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\MonCentreSocial.exe"; Parameters: "--tray"; Description: "Lancer l'icône Mon Centre Social"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\MonCentreSocial.exe"; Parameters: "--uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "StopCentreSocial"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\MonCentreSocial.exe')) then begin
    if not Exec(ExpandConstant('{app}\MonCentreSocial.exe'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := 'Le service existant ne peut pas être arrêté.'
    else if Code <> 0 then Result := 'Le service existant ne peut pas être arrêté. Fermez-le avant de réessayer.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if CurStep = ssPostInstall then begin
    WizardForm.StatusLabel.Caption := 'Préparation des composants Windows…';
    if not Exec(ExpandConstant('{tmp}\vc_redist.x64.exe'), '/install /quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('Le composant Microsoft Visual C++ ne peut pas être installé.');
    if (Code <> 0) and (Code <> 3010) and (Code <> 1638) then
      RaiseException('Microsoft Visual C++ a signalé une erreur : ' + IntToStr(Code));
    WizardForm.StatusLabel.Caption := 'Configuration de votre centre…';
    if not WizardSilent then begin
      if not Exec(ExpandConstant('{app}\MonCentreSocial.exe'), '--configure', '', SW_SHOW, ewWaitUntilTerminated, Code) then
        RaiseException('L''assistant ne peut pas démarrer.');
      if Code <> 0 then
        RaiseException('La configuration reste à terminer. Relancez « Configurer Mon Centre Social » dans le menu Démarrer.');
    end;
  end;
end;
