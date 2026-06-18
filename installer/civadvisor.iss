; CivAdvisor Inno Setup Script
; Run with: iscc civadvisor.iss
; Requires PyInstaller output at ..\overlay\dist\CivAdvisor.exe

#define AppName      "CivAdvisor"
#define AppVersion   "1.0.0"
#define AppPublisher "tobias-jermain"
#define AppURL       "https://github.com/tobias-jermain/CivAdvisor"
#define AppExeName   "CivAdvisor.exe"
#define AppIconFile  "CivAdvisor.ico"
#define ModsSubPath  "My Games\Sid Meier's Civilization VI\Mods\CivAdvisor"

[Setup]
AppId={{B3A7C2D1-4E5F-4A6B-8C9D-0E1F2A3B4C5D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=CivAdvisor_Setup
SetupIconFile={#AppIconFile}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExeName}
; Minimum OS: Windows 10
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Main overlay executable (built by PyInstaller)
Source: "..\overlay\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Lua mod — installed into the user's Civilization VI Mods folder
Source: "..\lua_mod\CivAdvisor.modinfo"; DestDir: "{userdocs}\{#ModsSubPath}"; Flags: ignoreversion
Source: "..\lua_mod\UI\CivAdvisor.xml";  DestDir: "{userdocs}\{#ModsSubPath}\UI"; Flags: ignoreversion
Source: "..\lua_mod\UI\CivAdvisor.lua";  DestDir: "{userdocs}\{#ModsSubPath}\UI"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";             Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}";   Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";       Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the mod folder on uninstall
Type: filesandordirs; Name: "{userdocs}\{#ModsSubPath}"

[Messages]
; Friendly finish message
FinishedLabel=CivAdvisor is installed.%n%nThe Civ VI mod has been placed in:%n%n  Documents\{#ModsSubPath}%n%nEnable it in Civ VI → Additional Content before starting a game.%n%nLogs are written to:%n%n  %LOCALAPPDATA%\CivAdvisor\logs

[Code]

// ── Helpers ──────────────────────────────────────────────────────────────────

function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  // Registry key written by a previous Inno Setup installation
  sUnInstPath := ExpandConstant(
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function ModSubscribed(): Boolean;
begin
  Result := DirExists(ExpandConstant('{userdocs}\{#ModsSubPath}'));
end;

// ── Pre-install: silently remove the old version ─────────────────────────────

procedure RemoveOldVersion();
var
  sUnInstallString: String;
  iResultCode: Integer;
begin
  sUnInstallString := RemoveQuotes(GetUninstallString());
  if sUnInstallString = '' then
    Exit;
  Exec(sUnInstallString, '/SILENT /NORESTART /SUPPRESSMSGBOXES',
       '', SW_HIDE, ewWaitUntilTerminated, iResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    if IsUpgrade() then
      RemoveOldVersion();
  end;
end;

// ── Wizard: inform the user about what was found ─────────────────────────────

function InitializeSetup(): Boolean;
var
  Msg: String;
begin
  Result := True;

  if IsUpgrade() then
  begin
    Msg := 'An existing installation of CivAdvisor was found.' + #13#10 +
           'It will be removed automatically before the new version is installed.';
    if ModSubscribed() then
      Msg := Msg + #13#10#13#10 +
             'The Civ VI mod folder is also present and will be updated.';
    MsgBox(Msg, mbInformation, MB_OK);
  end
  else if ModSubscribed() then
  begin
    MsgBox(
      'A CivAdvisor mod folder was found in your Civ VI Mods directory.' + #13#10 +
      'The installer will overwrite it with the current version.',
      mbInformation, MB_OK);
  end;
end;
