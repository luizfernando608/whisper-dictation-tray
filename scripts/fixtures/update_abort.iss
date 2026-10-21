[Setup]
AppName=PACE Intentional Update Failure Fixture
AppVersion=1.0
AppId=PACE-Intentional-Update-Failure-Fixture
DefaultDirName={tmp}\PACE-Failure-Fixture
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
SetupLogging=yes
OutputDir=.
OutputBaseFilename=Pace_Failure_Installer

[Code]
function InitializeSetup(): Boolean;
begin
  Log('Intentional test-only failure before installation.');
  Result := False;
end;
