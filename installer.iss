#ifndef AppVersion
  #define AppVersion "1.5.2"
#endif

[Setup]
AppId=Pace
AppName=PACE
AppVersion={#AppVersion}
AppPublisher=PACE
ArchitecturesAllowed=x64compatible
MinVersion=10.0
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
; Inference does not use ONNX test fixtures or C++ build headers. Shipping them
; can exceed Windows path limits when installing under a longer profile path.
Source: "dist\Pace\*"; DestDir: "{app}"; Excludes: "\_internal\onnx\backend\test\*,\_internal\torch\include\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PACE"; Filename: "{app}\Pace.exe"; Parameters: "--recordings"
Name: "{group}\PACE — Configurações"; Filename: "{app}\Pace.exe"; Parameters: "--settings"
Name: "{group}\PACE — Gravações"; Filename: "{app}\Pace.exe"; Parameters: "--recordings"
Name: "{userdesktop}\PACE"; Filename: "{app}\Pace.exe"; Parameters: "--recordings"
Name: "{userdesktop}\PACE — Configurações"; Filename: "{app}\Pace.exe"; Parameters: "--settings"
Name: "{userdesktop}\PACE — Gravações"; Filename: "{app}\Pace.exe"; Parameters: "--recordings"
Name: "{userstartup}\PACE"; Filename: "{app}\Pace.exe"

[Run]
Filename: "{app}\Pace.exe"; Parameters: "--recordings"; Description: "Launch PACE"; Flags: nowait postinstall skipifsilent
