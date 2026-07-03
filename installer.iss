#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif

[Setup]
AppId=Whisper Dictation Tray
AppName=Whisper Dictation Tray
AppVersion={#AppVersion}
AppPublisher=Whisper Dictation Tray
DefaultDirName={localappdata}\WhisperDictation
DefaultGroupName=Whisper Dictation
UninstallDisplayIcon={app}\WhisperDictationTray.exe
SetupLogging=yes
WizardStyle=modern
DisableDirPage=auto
DisableProgramGroupPage=auto
UsePreviousAppDir=yes
CloseApplications=yes
CloseApplicationsFilter=WhisperDictationTray.exe
RestartApplications=no
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=WhisperDictation_Installer
PrivilegesRequired=lowest

[InstallDelete]
Type: files; Name: "{app}\WhisperDictationTray.exe"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\WhisperDictationTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Whisper Dictation"; Filename: "{app}\WhisperDictationTray.exe"
Name: "{group}\Configurações do Whisper Dictation"; Filename: "{app}\WhisperDictationTray.exe"; Parameters: "--settings"
Name: "{userdesktop}\Whisper Dictation — Configurações"; Filename: "{app}\WhisperDictationTray.exe"; Parameters: "--settings"
Name: "{userstartup}\Whisper Dictation"; Filename: "{app}\WhisperDictationTray.exe"

[Run]
Filename: "{app}\WhisperDictationTray.exe"; Description: "Launch Whisper Dictation Tray"; Flags: nowait postinstall skipifsilent
