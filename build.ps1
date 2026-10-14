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
& $PythonCmd (Join-Path $ProjectRoot "scripts\prepare_directml_runtime.py")
if ($LASTEXITCODE -ne 0) { throw "DirectML runtime preparation failed" }
Push-Location $ProjectRoot
try {
    & $PythonCmd -m PyInstaller --noconfirm --clean --onedir --windowed `
        --name "Pace" --paths $SrcPath `
        --icon "assets\branding\pace.ico" `
        --add-data "assets\branding;assets\branding" `
        --add-data ".runtime-directml;dml_runtime" `
        --collect-all faster_whisper --collect-all ctranslate2 --collect-all av `
        --collect-all customtkinter --collect-all keyring --collect-all win32ctypes `
        --collect-all numpy `
        --collect-all pyaudiowpatch --collect-all tkinterdnd2 `
        --collect-all pyannote.audio --collect-all pyannote.core --collect-all pyannote.pipeline `
        --collect-all torch --collect-all torchaudio --collect-all torchcodec `
        --collect-all onnx --collect-all onnxruntime --collect-all openvino `
        --collect-all scipy --collect-all sklearn --collect-all huggingface_hub `
        --hidden-import keyring.backends.Windows `
        --collect-submodules openai --collect-submodules google.genai `
        main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
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
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
    $InstallerPath = Join-Path $ProjectRoot "Output\Pace_Installer.exe"
    Get-FileHash $InstallerPath -Algorithm SHA256 |
        Select-Object Algorithm, Hash | ConvertTo-Json |
        Set-Content (Join-Path $ProjectRoot "Output\Pace_Installer.sha256.json") -Encoding ascii
    $PaceBuildCommit = & git -C $ProjectRoot rev-parse HEAD
    $PaceBuildDirty = [bool](& git -C $ProjectRoot status --porcelain)
    [ordered]@{
        version = $AppVersion
        sourceCommit = $PaceBuildCommit
        sourceDirty = $PaceBuildDirty
        builtAtUtc = [DateTime]::UtcNow.ToString("o")
        installerSha256 = (Get-FileHash $InstallerPath -Algorithm SHA256).Hash
        executableSha256 = (Get-FileHash (Join-Path $ProjectRoot "dist\Pace\Pace.exe") -Algorithm SHA256).Hash
    } | ConvertTo-Json | Set-Content (Join-Path $ProjectRoot "Output\Pace_Installer.build.json") -Encoding ascii
    Write-Host "[OK] Installer successfully built! Check the 'Output' folder." -ForegroundColor Green
} else {
    Write-Host "[WARNING] Inno Setup 6 not found." -ForegroundColor Yellow
    Write-Host "Please download and install Inno Setup 6 from https://jrsoftware.org/isdl.php to create the setup.exe."
    Write-Host "You can manually run it by opening 'installer.iss' in Inno Setup and compiling."
}
