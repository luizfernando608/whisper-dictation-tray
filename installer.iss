[Setup]
AppName=Whisper Dictation Tray
AppVersion=1.0
DefaultDirName={localappdata}\WhisperDictation
DefaultGroupName=Whisper Dictation
UninstallDisplayIcon={app}\WhisperDictationTray.exe
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=WhisperDictation_Installer
PrivilegesRequired=lowest

[Files]
Source: "dist\WhisperDictationTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Whisper Dictation"; Filename: "{app}\WhisperDictationTray.exe"
Name: "{userstartup}\Whisper Dictation"; Filename: "{app}\WhisperDictationTray.exe"

[Run]
Filename: "{app}\WhisperDictationTray.exe"; Description: "Launch Whisper Dictation Tray"; Flags: nowait postinstall skipifsilent
