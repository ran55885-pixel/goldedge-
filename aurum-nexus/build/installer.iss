; Inno Setup script - Aurum Nexus installer (per-user, no admin rights needed)
[Setup]
AppName=Aurum Nexus
AppVersion=1.0.0
AppPublisher=Aurum Nexus
DefaultDirName={localappdata}\Programs\Aurum Nexus
DefaultGroupName=Aurum Nexus
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=AurumNexusSetup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Aurum Nexus.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "out\dist\Aurum Nexus\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
; user configuration is never overwritten on upgrade
Source: "..\config\*"; DestDir: "{app}\config"; Flags: recursesubdirs onlyifdoesntexist uninsneveruninstall
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Aurum Nexus"; Filename: "{app}\Aurum Nexus.exe"; IconFilename: "{app}\Aurum Nexus.exe"
Name: "{group}\Aurum Nexus (settings folder)"; Filename: "{app}\config"
Name: "{group}\Uninstall Aurum Nexus"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Aurum Nexus"; Filename: "{app}\Aurum Nexus.exe"; IconFilename: "{app}\Aurum Nexus.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\Aurum Nexus.exe"; Description: "Start Aurum Nexus (paper mode)"; Flags: nowait postinstall skipifsilent
