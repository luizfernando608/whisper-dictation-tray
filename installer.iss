#ifndef AppVersion
  #define AppVersion "1.3.2"
#endif

[Setup]
AppId=Pace
AppName=PACE
AppVersion={#AppVersion}
AppPublisher=PACE
DefaultDirName={localappdata}\Pace
DefaultGroupName=PACE
UninstallDisplayIcon={app}\Pace.exe
SetupIconFile=assets\branding\pace.ico
SetupLogging=yes
WizardStyle=modern
DisableDirPage=auto
DisableProgramGroupPage=auto
UsePreviousAppDir=yes
CloseApplications=yes
CloseApplicationsFilter=Pace.exe,WhisperDictationTray.exe
RestartApplications=no
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=Pace_Installer
PrivilegesRequired=lowest

[InstallDelete]
Type: files; Name: "{app}\Pace.exe"
Type: files; Name: "{app}\WhisperDictationTray.exe"
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{userdesktop}\Whisper Dictation — Configurações.lnk"
Type: files; Name: "{userdesktop}\Whisper Dictation.lnk"
Type: files; Name: "{userstartup}\Whisper Dictation.lnk"

[Files]
Source: "dist\Pace\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PACE"; Filename: "{app}\Pace.exe"
Name: "{group}\PACE — Configurações"; Filename: "{app}\Pace.exe"; Parameters: "--settings"
Name: "{userdesktop}\PACE"; Filename: "{app}\Pace.exe"
Name: "{userdesktop}\PACE — Configurações"; Filename: "{app}\Pace.exe"; Parameters: "--settings"
Name: "{userstartup}\PACE"; Filename: "{app}\Pace.exe"

[Run]
Filename: "{app}\Pace.exe"; Description: "Launch PACE"; Flags: nowait postinstall skipifsilent
