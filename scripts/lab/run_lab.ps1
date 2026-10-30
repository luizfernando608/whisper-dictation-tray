# Laboratório local do PACE: instalação limpa e atualização automática numa Windows Sandbox,
# sem publicar nada e sem usar chaves reais. Requer o recurso "Windows Sandbox" ativado.
#
#   scripts\lab\run_lab.ps1 -Candidate Output\Pace_Installer.exe -Next Output\lab\1.6.1\Pace_Installer.exe
#
# -Baseline: instalador publicado anterior (padrão: Output\v1.5.2\Pace_Installer.exe, igual ao do GitHub).
# Resultado: <Output>\report.json, lab.log e evidências (logs de atualização). Código 0 = tudo ok.
#
#   scripts\lab\run_lab.ps1 -Public     (após publicar: a versão anterior se atualiza pelo GitHub REAL)
param(
    [string]$Candidate,
    [string]$Next,
    [switch]$Public,
    [string]$Baseline,
    [string]$Output,
    [int]$TimeoutMinutes = 75,
    [switch]$KeepOpen
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $Baseline) { $Baseline = Join-Path $Repo "Output\v1.5.2\Pace_Installer.exe" }
if (-not $Output) { $Output = Join-Path $Repo ("Output\lab\run-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
if (-not $Public -and (-not $Candidate -or -not $Next)) { throw "Informe -Candidate e -Next, ou use -Public." }
$Sandbox = Join-Path $env:windir "System32\WindowsSandbox.exe"
if (-not (Test-Path $Sandbox)) {
    throw "Windows Sandbox não está ativado. Em um PowerShell de administrador: Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All (e reinicie)."
}
if (Get-Process WindowsSandbox, WindowsSandboxClient, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue) {
    throw "Já existe uma Windows Sandbox aberta. Feche-a antes de rodar o laboratório."
}

$Stage = Join-Path $env:TEMP ("pace-lab-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force $Stage, $Output | Out-Null
$Output = (Resolve-Path $Output).Path
$Installers = New-Object System.Collections.ArrayList  # lista explícita: o PowerShell "desenrola" arrays de um item
[void]$Installers.Add(@("baseline", $Baseline))
if (-not $Public) { [void]$Installers.Add(@("candidate", $Candidate)); [void]$Installers.Add(@("next", $Next)) }
foreach ($pair in $Installers) {
    $source = (Resolve-Path $pair[1]).Path
    New-Item -ItemType Directory -Force (Join-Path $Stage $pair[0]) | Out-Null
    Copy-Item $source (Join-Path $Stage "$($pair[0])\Pace_Installer.exe")
}
Copy-Item (Join-Path $PSScriptRoot "in_sandbox.ps1"), (Join-Path $PSScriptRoot "in_sandbox_public.ps1"), (Join-Path $PSScriptRoot "fake_github.ps1") $Stage
$Script = if ($Public) { "in_sandbox_public.ps1" } else { "in_sandbox.ps1" }
Set-Content (Join-Path $Stage "pace-lab.marker") "Pasta do laboratório PACE (somente sandbox)." -Encoding utf8
if ($KeepOpen) { Set-Content (Join-Path $Stage "keep-open.flag") "1" }

if (-not $Public) {
Write-Host "Gerando áudios de teste..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "make_fixtures.ps1") -Output (Join-Path $Stage "fixtures") | Out-Null

Write-Host "Compilando o instalador que falha de propósito (rollback)..." -ForegroundColor Cyan
$Iscc = @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 não encontrado." }
& $Iscc "/Q" "/O$(Join-Path $Stage 'abort')" (Join-Path $Repo "scripts\fixtures\update_abort.iss") | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar update_abort.iss" }

$Models = Join-Path $env:LOCALAPPDATA "PACE-Preview-Data\models"
if (-not (Test-Path $Models)) { $Models = Join-Path $env:LOCALAPPDATA "PaceData\models" }
if (Test-Path $Models) {
    Write-Host "Copiando modelos de diarização já preparados (evita baixar de novo)..." -ForegroundColor Cyan
    Copy-Item $Models (Join-Path $Stage "models") -Recurse
}

}

$Config = @"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Enable</Networking>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$Stage</HostFolder><SandboxFolder>C:\lab</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$Output</HostFolder><SandboxFolder>C:\results</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\lab\$Script</Command>
  </LogonCommand>
</Configuration>
"@
$Wsb = Join-Path $Stage "pace-lab.wsb"
Set-Content -Path $Wsb -Value $Config -Encoding utf8
Write-Host "Abrindo a Windows Sandbox (Windows limpo, descartado ao fechar)..." -ForegroundColor Cyan
Start-Process $Wsb

$ReportPath = Join-Path $Output "report.json"
$Deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$Shown = 0
while ((Get-Date) -lt $Deadline) {
    Start-Sleep -Seconds 10
    $LogPath = Join-Path $Output "lab.log"
    if (Test-Path $LogPath) {
        $lines = @(Get-Content $LogPath -Encoding utf8)
        if ($lines.Count -gt $Shown) {
            $lines[$Shown..($lines.Count - 1)] | Where-Object { $_ } | ForEach-Object { Write-Host $_ }
            $Shown = $lines.Count
        }
    }
    try {
        # report.json é regravado a cada verificação; uma leitura no meio da escrita só adia a checagem.
        if ((Test-Path $ReportPath) -and ((Get-Content -Raw $ReportPath -Encoding utf8 | ConvertFrom-Json).finished)) { break }
    } catch {}
}
$Report = $null
try { if (Test-Path $ReportPath) { $Report = Get-Content -Raw $ReportPath -Encoding utf8 | ConvertFrom-Json } } catch {}
if (-not $KeepOpen) {
    Start-Sleep -Seconds 20
    Get-Process WindowsSandbox, WindowsSandboxClient, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
if (-not $Report -or -not $Report.finished) { Write-Host "O laboratório não terminou no tempo. Veja $Output" -ForegroundColor Red; exit 2 }
$Failed = @($Report.checks | Where-Object { -not $_.ok })
Write-Host ""
Write-Host ("Resultado: {0}/{1} verificações ok. Relatório: {2}" -f ($Report.checks.Count - $Failed.Count), $Report.checks.Count, $ReportPath) -ForegroundColor ($(if ($Failed) { "Red" } else { "Green" }))
$Failed | ForEach-Object { Write-Host "  FALHA $($_.name): $($_.detail)" -ForegroundColor Red }
exit $(if ($Failed) { 1 } else { 0 })
