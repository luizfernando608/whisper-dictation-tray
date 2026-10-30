# Verificação pós-publicação, DENTRO da Windows Sandbox, com o GitHub REAL (sem servidor falso):
# instala a versão anterior publicada e deixa ela se atualizar sozinha para o release mais recente.
param([string]$Lab = "C:\lab", [string]$Results = "C:\results", [ValidateRange(1, 60)][int]$UpdateTimeoutMinutes = 10)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ($env:USERNAME -ne "WDAGUtilityAccount" -or -not (Test-Path (Join-Path $Lab "pace-lab.marker"))) {
    Write-Error "Só roda dentro da Windows Sandbox do laboratório (use scripts\lab\run_lab.ps1 -Public)."
    exit 3
}
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$App = Join-Path $env:LOCALAPPDATA "Pace"
$Data = Join-Path $env:LOCALAPPDATA "PaceData"
$Cli = Join-Path $App "pace-cli.exe"
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Pace_is1"
$Report = [ordered]@{ started = (Get-Date).ToString("o"); finished = $false; checks = @() }

function Log([string]$Message) { Add-Content -Path (Join-Path $Results "lab.log") -Value ("{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) -Encoding utf8 }
function Save { $Report | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $Results "report.json") -Encoding utf8 }
function Check([string]$Name, [scriptblock]$Body) {
    try { $detail = & $Body; $Report.checks += [ordered]@{ name = $Name; ok = $true; detail = "$detail" }; Log "OK    $Name  $detail" }
    catch { $Report.checks += [ordered]@{ name = $Name; ok = $false; detail = "$($_.Exception.Message)" }; Log "FALHA $Name  $($_.Exception.Message)" }
    Save
}
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Wait-Until([scriptblock]$Condition, [int]$Seconds, [string]$What) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) { try { if (& $Condition) { return } } catch {}; Start-Sleep -Milliseconds 700 }
    throw "tempo esgotado: $What"
}
function Send-Pace([string]$Command, [switch]$Read) {
    $client = New-Object System.Net.Sockets.TcpClient
    $client.Connect("127.0.0.1", 49517)
    try {
        $stream = $client.GetStream(); $bytes = [Text.Encoding]::UTF8.GetBytes($Command)
        $stream.Write($bytes, 0, $bytes.Length); $stream.Flush()
        if ($Read) { $stream.ReadTimeout = 8000; return (New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)).ReadToEnd() }
        Start-Sleep -Milliseconds 300
    } finally { $client.Close() }
}
function Running-Version { (Send-Pace "capabilities" -Read | ConvertFrom-Json).version }
function Installed-Version { (Get-ItemProperty $UninstallKey -ErrorAction Stop).DisplayVersion }
function Last-UpdateResult {
    $latest = Get-ChildItem (Join-Path $Data "updates\runs\*\result.json") -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($latest) { Get-Content -Raw $latest.FullName | ConvertFrom-Json }
}

try {
    # Testar o Windows limpo com as proteções originais. Se uma política bloquear
    # o instalador sem assinatura, registrar o bloqueio em vez de desativá-la.
    Log "Políticas de segurança da sandbox preservadas."
    $release = Invoke-RestMethod "https://api.github.com/repos/luizfernando608/whisper-dictation-tray/releases/latest" -Headers @{ "User-Agent" = "PACE-lab" } -TimeoutSec 30
    $target = $release.tag_name.TrimStart("v")
    Log "== release mais recente no GitHub real: $($release.tag_name)"
    Check "instala a versão anterior publicada" {
        $process = Start-Process "$Lab\baseline\Pace_Installer.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/LOG=$Results\baseline-install.log" -Wait -PassThru
        Assert ($process.ExitCode -eq 0) "instalador retornou $($process.ExitCode)"
        Start-Process (Join-Path $App "Pace.exe")
        Wait-Until { (Send-Pace "ping" -Read) -eq "pong" } 90 "PACE na bandeja"
        Running-Version
    }
    Assert ($Report.checks[-1].ok) "a instalação inicial falhou; as verificações dependentes não serão executadas"
    Check "atualiza sozinha para $target pelo GitHub real" {
        # Pede "Reiniciar para atualizar" a cada 15 segundos (se ainda não estiver pronto, o PACE verifica e baixa
        # antes de aplicar) e registra o andamento do download.
        $cache = Join-Path $Data "updates\$target"
        $runs = Join-Path $Data "updates\runs"
        $deadline = (Get-Date).AddMinutes($UpdateTimeoutMinutes)
        while (-not (Test-Path $runs) -or @(Get-ChildItem $runs -Directory).Count -eq 0) {
            Assert ((Get-Date) -lt $deadline) "o PACE não iniciou a atualização"
            $files = @(Get-ChildItem $cache -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name)=$([math]::Round($_.Length / 1MB))MB" })
            Log "      cache: $($files -join ', ')"
            try { Send-Pace "apply-update" } catch { Log "      pedido falhou: $($_.Exception.Message)" }
            Start-Sleep -Seconds 15
        }
        Wait-Until { (Installed-Version) -eq $target -and (Running-Version) -eq $target } 600 "$target instalada e confirmada"
        Wait-Until { (Last-UpdateResult).status -eq "updated" } 60 "resultado do auxiliar"
        $hash = (Get-FileHash (Join-Path $cache "Pace_Installer.exe")).Hash.ToLower()
        Assert ("sha256:$hash" -eq ($release.assets | Where-Object name -eq "Pace_Installer.exe").digest) "hash diferente do publicado"
        "$target confirmada; instalador com o hash publicado"
    }
    Assert ($Report.checks[-1].ok) "a atualização falhou; a verificação do CLI depende da versão nova instalada"
    Check "pace-cli e MCP da versão publicada" {
        $version = (& $Cli --version) -join ""
        Assert ($version -eq "PACE $target") "pace-cli: $version"
        $info = New-Object Diagnostics.ProcessStartInfo $Cli, "mcp"
        $info.UseShellExecute = $false; $info.RedirectStandardInput = $true; $info.RedirectStandardOutput = $true
        $info.StandardOutputEncoding = [Text.Encoding]::UTF8; $info.CreateNoWindow = $true
        $server = [Diagnostics.Process]::Start($info)
        $server.StandardInput.WriteLine('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}')
        $server.StandardInput.Flush()
        $line = $server.StandardOutput.ReadLineAsync()
        Assert ($line.Wait(60000)) "MCP sem resposta"
        $server.Kill()
        Assert (($line.Result | ConvertFrom-Json).result.serverInfo.version -eq $target) "MCP: $($line.Result)"
        "pace-cli e MCP $target"
    }
} catch {
    $Report.checks += [ordered]@{ name = "roteiro"; ok = $false; detail = "$($_.Exception.Message)" }
    Log "ERRO no roteiro: $($_.Exception.Message)"
} finally {
    # Diagnóstico de rede separado do fluxo do aplicativo (não aquece certificados
    # nem substitui o transporte do atualizador antes do teste).
    try {
        $network = [ordered]@{ checked_at = (Get-Date).ToString("o") }
        foreach ($hostName in @("api.github.com", "github.com", "release-assets.githubusercontent.com")) {
            try { $network[$hostName] = @([Net.Dns]::GetHostAddresses($hostName) | ForEach-Object { $_.IPAddressToString }) }
            catch { $network[$hostName] = $_.Exception.Message }
        }
        $network | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Results "network.json") -Encoding utf8
    } catch {}
    # Diagnóstico de bloqueios do Windows (Smart App Control / App Control / SmartScreen).
    try {
        $diag = @("SmartAppControlState: " + (Get-MpComputerStatus).SmartAppControlState)
        $diag += "Build: " + [Environment]::OSVersion.VersionString
        $diag += Get-WinEvent -LogName "Microsoft-Windows-CodeIntegrity/Operational" -MaxEvents 25 -ErrorAction SilentlyContinue |
            ForEach-Object { "{0} {1} {2}" -f $_.TimeCreated, $_.Id, ($_.Message -replace "\s+", " ") }
        $diag += Get-WinEvent -LogName "Microsoft-Windows-AppLocker/EXE and DLL" -MaxEvents 10 -ErrorAction SilentlyContinue |
            ForEach-Object { "{0} {1} {2}" -f $_.TimeCreated, $_.Id, ($_.Message -replace "\s+", " ") }
        $diag | Set-Content -Path (Join-Path $Results "app-control.txt") -Encoding utf8
    } catch {}
    $source = Join-Path $Data "updates\runs"
    if (Test-Path $source) { Copy-Item $source (Join-Path $Results "evidencias-runs") -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path (Join-Path $App "logs")) { Copy-Item (Join-Path $App "logs") (Join-Path $Results "evidencias-logs") -Recurse -Force -ErrorAction SilentlyContinue }
    $Report.finished = $true
    $Report.passed = @($Report.checks | Where-Object { -not $_.ok }).Count -eq 0
    Save
    Log "== fim: $(@($Report.checks | Where-Object { $_.ok }).Count)/$($Report.checks.Count) verificações ok"
    if (-not (Test-Path "$Lab\keep-open.flag")) { & shutdown.exe /s /t 5 }
}
