# Gera um instalador de TESTE com outra versão (ex.: 1.6.1) a partir do código atual, só para o
# laboratório verificar que a versão candidata consegue se atualizar no futuro. Nunca publicar.
#   scripts\lab\build_test_release.ps1 -Version 1.6.1
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$VersionFile = Join-Path $Repo "src\whisper_dictation\_version.py"
$Original = [IO.File]::ReadAllText($VersionFile)
$Destination = Join-Path $Repo "Output\lab\$Version"
try {
    [IO.File]::WriteAllText($VersionFile, ($Original -replace '__version__ = "[^"]+"', "__version__ = `"$Version`""))
    & (Join-Path $Repo "build.ps1")
    if ($LASTEXITCODE -ne 0) { throw "build.ps1 falhou" }
    New-Item -ItemType Directory -Force $Destination | Out-Null
    Move-Item (Join-Path $Repo "Output\Pace_Installer.exe") (Join-Path $Destination "Pace_Installer.exe") -Force
    Set-Content (Join-Path $Destination "NAO-PUBLICAR.txt") "Instalador de teste do laboratório ($Version). Não publicar." -Encoding utf8
    Write-Host "Instalador de teste: $Destination\Pace_Installer.exe" -ForegroundColor Green
} finally {
    [IO.File]::WriteAllText($VersionFile, $Original)
}
