param(
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"

if (-not (Test-Path $VenvPath)) {
    Write-Host "Criando ambiente virtual em $VenvPath"
    py -$PythonVersion -m venv $VenvPath
}

$Python = Join-Path $VenvPath "Scripts\\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

& $Python -m pip install --upgrade pip
& $Python -m pip install -r $Requirements

Write-Host ""
Write-Host "Instalação concluída."
Write-Host "Para listar os microfones:"
Write-Host "  & `"$Python`" `"$ProjectRoot\\main.py`" --list-devices"
Write-Host ""
Write-Host "Para iniciar o app na bandeja:"
Write-Host "  & `"$ProjectRoot\\scripts\\run.ps1`""

