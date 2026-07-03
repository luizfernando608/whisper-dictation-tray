$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SrcPath = Join-Path $ProjectRoot "src"

if (Test-Path $VenvPython) {
    $PythonCmd = $VenvPython
} else {
    $PythonCmd = "py"
    try {
        & $PythonCmd --version | Out-Null
    } catch {
        $PythonCmd = "python"
    }
}

Write-Host "Ensuring PyInstaller is installed..." -ForegroundColor Cyan
# pip prints an upgrade notice to stderr; under ErrorActionPreference=Stop that
# can surface as a terminating error, so tolerate it (PyInstaller may already be
# present anyway).
try {
    & $PythonCmd -m pip install pyinstaller --disable-pip-version-check --quiet
} catch {
    Write-Host "pip note: $_"
}

# Read the single source of truth for the version.
$VersionFile = Join-Path $SrcPath "whisper_dictation\_version.py"
$VersionMatch = Select-String -Path $VersionFile -Pattern '__version__\s*=\s*"([^"]+)"'
$AppVersion = $VersionMatch.Matches[0].Groups[1].Value
Write-Host "Building version $AppVersion" -ForegroundColor Cyan

Write-Host "Building executable with PyInstaller..." -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    & $PythonCmd -m PyInstaller --noconfirm --clean --onedir --windowed `
        --name "WhisperDictationTray" --paths $SrcPath `
        --collect-all faster_whisper --collect-all ctranslate2 `
        --collect-all customtkinter --collect-all keyring --collect-all win32ctypes `
        --hidden-import keyring.backends.Windows `
        --collect-submodules openai --collect-submodules google.genai `
        main.py
} finally {
    Pop-Location
}

$ISCC_System = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$ISCC_User = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"

if (Test-Path $ISCC_System) {
    $ISCC = $ISCC_System
} elseif (Test-Path $ISCC_User) {
    $ISCC = $ISCC_User
} else {
    $ISCC = ""
}

if ($ISCC) {
    Write-Host "Building Installer with Inno Setup..." -ForegroundColor Cyan
    & $ISCC "/DAppVersion=$AppVersion" "installer.iss"
    Write-Host "[OK] Installer successfully built! Check the 'Output' folder." -ForegroundColor Green
} else {
    Write-Host "[WARNING] Inno Setup 6 not found." -ForegroundColor Yellow
    Write-Host "Please download and install Inno Setup 6 from https://jrsoftware.org/isdl.php to create the setup.exe."
    Write-Host "You can manually run it by opening 'installer.iss' in Inno Setup and compiling."
}
