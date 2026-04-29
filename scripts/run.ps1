$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $ProjectRoot ".venv\\Scripts\\pythonw.exe"
$MainFile = Join-Path $ProjectRoot "main.py"

if (-not (Test-Path $Pythonw)) {
    throw "Ambiente virtual não encontrado. Rode scripts\\install.ps1 primeiro."
}

$ExistingProcess = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like "*$MainFile*"
    } |
    Select-Object -First 1

if ($ExistingProcess) {
    return
}

Start-Process -FilePath $Pythonw -ArgumentList "`"$MainFile`"" -WorkingDirectory $ProjectRoot
