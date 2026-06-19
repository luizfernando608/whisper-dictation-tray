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

Write-Host "Installing PyInstaller..." -ForegroundColor Cyan
& $PythonCmd -m pip install pyinstaller

Write-Host "Building executable with PyInstaller..." -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    & $PythonCmd -m PyInstaller --noconfirm --clean --onedir --windowed --name "WhisperDictationTray" --paths $SrcPath --collect-all faster_whisper --collect-all ctranslate2 main.py
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
    & $ISCC "installer.iss"
    Write-Host "[OK] Installer successfully built! Check the 'Output' folder." -ForegroundColor Green
} else {
    Write-Host "[WARNING] Inno Setup 6 not found." -ForegroundColor Yellow
    Write-Host "Please download and install Inno Setup 6 from https://jrsoftware.org/isdl.php to create the setup.exe."
    Write-Host "You can manually run it by opening 'installer.iss' in Inno Setup and compiling."
}
