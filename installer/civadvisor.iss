; CivAdvisor Inno Setup Script
; Run with: iscc civadvisor.iss
; Requires PyInstaller output at ..\overlay\dist\CivAdvisor.exe

#define AppName      "CivAdvisor"
#define AppVersion   "1.0.0"
#define AppPublisher "tobias-jermain"
#define AppURL       "https://github.com/tobias-jermain/CivAdvisor"
#define AppExeName   "CivAdvisor.exe"
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
Source: "..\lua_mod\CivAdvisor.xml";     DestDir: "{userdocs}\{#ModsSubPath}"; Flags: ignoreversion
Source: "..\lua_mod\UI\CivAdvisor.lua";  DestDir: "{userdocs}\{#ModsSubPath}\UI"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the mod folder on uninstall
Type: filesandordirs; Name: "{userdocs}\{#ModsSubPath}"

[Messages]
; Friendly finish message
FinishedLabel=CivAdvisor is installed.%n%nThe Civ VI mod has been placed in:%n%n  Documents\{#ModsSubPath}%n%nEnable it in Civ VI → Additional Content before starting a game.
