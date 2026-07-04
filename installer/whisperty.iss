; ============================================================================
;  Whisperty — script d'installation Inno Setup 6
;  Compiler avec :  ISCC.exe installer\whisperty.iss
;  (ou via scripts\make_installer.ps1 qui localise ISCC.exe)
;
;  Prérequis : avoir produit dist\whisperty\ au préalable (scripts\build.ps1).
;
;  Choix d'architecture : installation PAR UTILISATEUR (sans droits admin) dans
;  %LocalAppData%\Programs\Whisperty. C'est INDISPENSABLE car l'application écrit
;  config.yaml (édité depuis l'UI), whisperty.db, logs\ et transcriptions\ À CÔTÉ
;  de l'exe : un dossier Program Files (lecture seule pour un standard user) les
;  ferait échouer. L'autostart utilise la clé HKCU Run (par utilisateur, cohérent).
; ============================================================================

#define MyAppName "Whisperty"
#ifndef MyAppVersion
  #define MyAppVersion "0.3.0"
#endif
#ifndef MyAppVersionInfo
  #define MyAppVersionInfo "0.3.0.0"
#endif
#define MyAppPublisher "Softcom"
#define MyAppExeName "whisperty.exe"
; Dossier source produit par scripts\build.ps1 (relatif à ce .iss).
#define SourceDir "..\dist\whisperty"

[Setup]
; AppId fixe (NE PAS changer entre versions : identifie le produit pour les MAJ).
AppId={{B2F8C4D6-3A91-4E27-9F5C-7D0E1A6B8C34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersionInfo}

; --- Installation par utilisateur (aucun droit admin requis) ---
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

; --- 64 bits uniquement (ctranslate2/PyAV n'ont pas de roues 32 bits) ---
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; --- Sortie ---
OutputDir=..\dist\installer
OutputBaseFilename=Whisperty-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; --- Icônes / branding ---
SetupIconFile=whisperty.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; --- Ferme une instance en cours d'exécution avant MAJ/désinstallation ---
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Lancer Whisperty au démarrage de Windows"; GroupDescription: "Démarrage :"

[Files]
; Tout le dossier onedir SAUF les réglages utilisateur (gérés à part, préservés en MAJ).
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "config.yaml,dictionary.txt,whisperty.db,logs\*,transcriptions\*"; Flags: recursesubdirs createallsubdirs ignoreversion

; Réglages utilisateur : posés seulement s'ils n'existent pas (ne PAS écraser à la MAJ).
Source: "{#SourceDir}\config.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#SourceDir}\dictionary.txt"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Dirs]
; Dossiers de données runtime (écrits par l'app ; dans un emplacement utilisateur inscriptible).
Name: "{app}\logs"
Name: "{app}\transcriptions"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Démarrage automatique par utilisateur (HKCU Run) — cohérent avec scripts\install_autostart.ps1.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Description: "Lancer {#MyAppName} maintenant"; Filename: "{app}\{#MyAppExeName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nettoyage des artefacts runtime (on PRÉSERVE config/dictionnaire/historique/transcriptions).
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
{ Ferme l'instance en cours d'exécution avant d'écrire les fichiers. Whisperty est
  une app de zone de notification : sa fenêtre (masquée) intercepte la fermeture
  pour se réduire dans le tray, si bien que la fermeture « douce » du Restart
  Manager (CloseApplications=yes) ne la quitte PAS — les fichiers resteraient
  verrouillés et la mise à jour échouerait. taskkill est le repli pragmatique,
  exécuté à l'installation ET à la désinstallation. }
procedure KillRunningApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#MyAppExeName}', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    KillRunningApp();
end;

{ Détection du runtime Edge WebView2 (Evergreen). Non bloquant : sans lui, Whisperty
  démarre en mode zone de notification seule (la fenêtre nécessite WebView2). }
function WebView2Installed(): Boolean;
var
  Pv: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv);
  { Une valeur 'pv' vide ('0.0.0.0') signifie « non installé ». }
  if Result and ((Pv = '') or (Pv = '0.0.0.0')) then
    Result := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and (not WebView2Installed()) then
  begin
    { Dialogue ACTIONNABLE : proposer d'ouvrir la page de téléchargement plutôt
      que d'afficher une URL à recopier. }
    if MsgBox(
      'Microsoft Edge WebView2 ne semble pas installé sur ce poste.' + #13#10 + #13#10 +
      'Whisperty fonctionnera quand même (dictée, raccourci, zone de notification),' + #13#10 +
      'mais la FENÊTRE (tableau de bord, configuration, historique) ne s''ouvrira pas.' + #13#10 + #13#10 +
      'Voulez-vous ouvrir la page de téléchargement de « Microsoft Edge WebView2' + #13#10 +
      'Runtime » (Evergreen) maintenant ?',
      mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/',
                '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
  end;
end;
