# Roteiro do laboratório, executado DENTRO da Windows Sandbox (Windows limpo e descartável).
# C:\lab (somente leitura): instaladores, fixtures, modelos, scripts. C:\results (gravável): relatório.
# Cenários: 1.5.2 publicada -> 1.6.0 pela atualização automática real; agentes; MCP; transcrição;
# 1.6.0 -> 1.6.1-teste com servidor MCP aberto; rollback com instalador que falha; desinstalação.
param([string]$Lab = "C:\lab", [string]$Results = "C:\results")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# Segurança: este roteiro altera certificados, hosts e configs de agentes. Só roda na sandbox
# descartável aberta por run_lab.ps1 (usuário da sandbox + marcador na pasta mapeada).
if ($env:USERNAME -ne "WDAGUtilityAccount" -or -not (Test-Path (Join-Path $Lab "pace-lab.marker"))) {
    $reason = "in_sandbox.ps1 só roda dentro da Windows Sandbox do laboratório (usuário atual: $env:USERNAME). Use scripts\lab\run_lab.ps1."
    if ((Test-Path (Join-Path $Lab "pace-lab.marker")) -and (Test-Path $Results)) {
        # Dentro da sandbox do laboratório, mas com outro usuário: avisa o host em vez de deixá-lo esperando.
        Add-Content -Path (Join-Path $Results "lab.log") -Value $reason -Encoding utf8
        [ordered]@{ finished = $true; passed = $false; checks = @([ordered]@{ name = "ambiente"; ok = $false; detail = $reason }) } |
            ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $Results "report.json") -Encoding utf8
    }
    Write-Error $reason
    exit 3
}
$Run = "C:\labrun"
New-Item -ItemType Directory -Force $Run, $Results | Out-Null
$App = Join-Path $env:LOCALAPPDATA "Pace"
$Data = Join-Path $env:LOCALAPPDATA "PaceData"
$Cli = Join-Path $App "pace-cli.exe"
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Pace_is1"
$Report = [ordered]@{ started = (Get-Date).ToString("o"); finished = $false; checks = @() }

function Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Add-Content -Path (Join-Path $Results "lab.log") -Value $line -Encoding utf8
}
function Save { $Report | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $Results "report.json") -Encoding utf8 }
function Check([string]$Name, [scriptblock]$Body) {
    try {
        $detail = & $Body
        $Report.checks += [ordered]@{ name = $Name; ok = $true; detail = "$detail" }
        Log "OK    $Name  $detail"
    } catch {
        $Report.checks += [ordered]@{ name = $Name; ok = $false; detail = "$($_.Exception.Message)" }
        Log "FALHA $Name  $($_.Exception.Message)"
    }
    Save
}
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Wait-Until([scriptblock]$Condition, [int]$Seconds, [string]$What) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { if (& $Condition) { return } } catch {}
        Start-Sleep -Milliseconds 700
    }
    throw "tempo esgotado: $What"
}
function Send-Pace([string]$Command, [switch]$Read) {
    $client = New-Object System.Net.Sockets.TcpClient
    $client.Connect("127.0.0.1", 49517)
    try {
        $stream = $client.GetStream()
        $bytes = [Text.Encoding]::UTF8.GetBytes($Command)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
        if ($Read) {
            $stream.ReadTimeout = 8000
            return (New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)).ReadToEnd()
        }
        Start-Sleep -Milliseconds 300
    } finally { $client.Close() }
}
function Running-Version { (Send-Pace "capabilities" -Read | ConvertFrom-Json).version }
function Installed-Version { (Get-ItemProperty $UninstallKey -ErrorAction Stop).DisplayVersion }
function Install([string]$Installer) {
    $process = Start-Process $Installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/LOG=$Results\install-$(Split-Path (Split-Path $Installer -Parent) -Leaf).log" -Wait -PassThru
    Assert ($process.ExitCode -eq 0) "instalador retornou $($process.ExitCode)"
}
function Start-Tray { Start-Process (Join-Path $App "Pace.exe"); Wait-Until { (Send-Pace "ping" -Read) -eq "pong" } 90 "PACE responder na bandeja" }
function Serve([string]$Tag, [string]$Installer) {
    [ordered]@{ tag = $Tag; installer = $Installer } | ConvertTo-Json | Set-Content -Path "$Run\release.json" -Encoding utf8
}
function Cli([string[]]$Arguments) {
    $output = & $Cli @Arguments 2>"$Run\cli-stderr.txt"
    $script:CliExit = $LASTEXITCODE
    return ($output -join "`n")
}
function Data-Fingerprint {
    $files = @((Join-Path $App "config.json"), (Join-Path $App ".env"), (Join-Path $Data "recordings\manter.txt"))
    ($files | ForEach-Object { if (Test-Path $_) { (Get-FileHash $_).Hash } else { "ausente" } }) -join ","
}
function Last-UpdateResult {
    $latest = Get-ChildItem (Join-Path $Data "updates\runs\*\result.json") -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($latest) { Get-Content -Raw $latest.FullName | ConvertFrom-Json }
}
function Start-McpServer {
    $info = New-Object Diagnostics.ProcessStartInfo $Cli, "mcp"
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.CreateNoWindow = $true
    return [Diagnostics.Process]::Start($info)
}
function Mcp([Diagnostics.Process]$Process, [string]$Json, [int]$Seconds = 60) {
    $Process.StandardInput.WriteLine($Json)
    $Process.StandardInput.Flush()
    $task = $Process.StandardOutput.ReadLineAsync()
    Assert ($task.Wait($Seconds * 1000)) "sem resposta do MCP"
    return ($task.Result | ConvertFrom-Json)
}

try {
    Log "== preparação: GitHub falso (certificado de teste + hosts)"
    $cert = New-SelfSignedCertificate -DnsName "api.github.com", "github.com" -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddDays(2)
    Export-Certificate -Cert $cert -FilePath "$Run\fake.cer" | Out-Null
    Import-Certificate -FilePath "$Run\fake.cer" -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    & netsh http add sslcert ipport=0.0.0.0:443 certhash=$($cert.Thumbprint) appid="{8c8b6e4e-1a7c-4a4e-9a5f-5ace0000a6e1}" | Out-Null
    Add-Content -Path "$env:windir\System32\drivers\etc\hosts" -Value "`n127.0.0.1 api.github.com`n127.0.0.1 github.com" -Encoding ascii
    & ipconfig /flushdns | Out-Null
    Serve "v1.6.0" "$Lab\candidate\Pace_Installer.exe"
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$Lab\fake_github.ps1", "-State", "$Run\release.json"
    Check "GitHub falso responde com TLS confiável" {
        Wait-Until { (Invoke-RestMethod "https://api.github.com/repos/luizfernando608/whisper-dictation-tray/releases/latest").tag_name -eq "v1.6.0" } 30 "servidor"
        "v1.6.0"
    }

    Log "== 1. versão publicada 1.5.2 (como no computador de quem já usa)"
    Check "instala 1.5.2 do zero" { Install "$Lab\baseline\Pace_Installer.exe"; Installed-Version }
    Check "1.5.2 abre na bandeja" { Start-Tray; Running-Version }
    Check "dados do usuário semeados" {
        Send-Pace "quit"
        Wait-Until { -not (Get-Process Pace -ErrorAction SilentlyContinue) } 60 "fechar 1.5.2"
        $config = Get-Content -Raw (Join-Path $App "config.json") | ConvertFrom-Json
        $config.language = "pt"
        $config | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $App "config.json") -Encoding utf8
        Set-Content (Join-Path $App ".env") "GROQ_API_KEY=chave-ficticia-do-laboratorio" -Encoding ascii
        New-Item -ItemType Directory -Force (Join-Path $Data "recordings") | Out-Null
        Set-Content (Join-Path $Data "recordings\manter.txt") "gravação que precisa sobreviver" -Encoding utf8
        $script:Fingerprint = Data-Fingerprint
        $script:Fingerprint
    }

    Log "== 2. atualização automática real: 1.5.2 -> 1.6.0"
    Check "1.5.2 atualiza sozinha para 1.6.0 pelo fluxo do app" {
        Start-Tray
        Send-Pace "apply-update"
        Wait-Until { (Installed-Version) -eq "1.6.0" -and (Running-Version) -eq "1.6.0" } 600 "1.6.0 instalada e confirmada"
        $result = Last-UpdateResult
        Assert ($result.status -eq "updated") "resultado do auxiliar: $($result | ConvertTo-Json -Compress)"
        "1.6.0 confirmada"
    }
    Check "dados, chave e configurações preservados (1.5.2 -> 1.6.0)" {
        $now = Data-Fingerprint
        Assert ($now -eq $script:Fingerprint) "antes $script:Fingerprint, depois $now"
        "iguais"
    }
    Check "pace-cli instalado e funcional" {
        $version = Cli @("--version")
        Assert ($version -eq "PACE 1.6.0") "versão: $version"
        $doctor = Cli @("doctor", "--json") | ConvertFrom-Json
        Assert ($doctor.api_key_configured -eq $true) "chave não encontrada após a atualização"
        "doctor ok, chave detectada (sem exibir)"
    }

    Log "== 3. agentes de IA (perfis falsos dentro da sandbox)"
    Check "conectar aos agentes instala MCP e skill" {
        New-Item -ItemType Directory -Force "$HOME\.claude", "$HOME\.codex", "$env:APPDATA\Claude" | Out-Null
        Set-Content "$HOME\.claude.json" '{"numStartups": 1}' -Encoding ascii
        Set-Content "$HOME\.codex\config.toml" "model = `"gpt-5`"`n" -Encoding ascii
        Set-Content "$env:APPDATA\Claude\claude_desktop_config.json" '{"preferences": {"laboratorio": true}}' -Encoding ascii
        $report = Cli @("agents", "connect", "--json") | ConvertFrom-Json
        Assert ($script:CliExit -eq 0) "agents connect saiu com $script:CliExit"
        $claude = Get-Content -Raw "$HOME\.claude.json" | ConvertFrom-Json
        Assert ($claude.mcpServers.pace.command -eq $Cli) "Claude Code sem o PACE"
        Assert ((Get-Content -Raw "$HOME\.codex\config.toml") -match "mcp_servers\.pace") "Codex sem o PACE"
        Assert (Test-Path "$HOME\.claude\skills\pace-transcricao\SKILL.md") "skill ausente"
        ($report.PSObject.Properties.Name -join ",")
    }
    Check "servidor MCP: handshake, ferramentas e diagnóstico" {
        $script:Mcp = Start-McpServer
        $init = Mcp $script:Mcp '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
        Assert ($init.result.serverInfo.version -eq "1.6.0") "versão MCP $($init.result.serverInfo.version)"
        $tools = Mcp $script:Mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
        $status = Mcp $script:Mcp '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"pace_status","arguments":{}}}' 120
        Assert (-not $status.result.isError) "pace_status falhou: $($status.result.content[0].text)"
        ($tools.result.tools.name -join ",")
    }

    Log "== 4. transcrição offline (sem chave real) com falantes"
    Check "pace-cli transcreve áudio de WhatsApp offline" {
        $models = Join-Path $Data "models"
        if (Test-Path "$Lab\models") {
            New-Item -ItemType Directory -Force $models | Out-Null
            Copy-Item "$Lab\models\*" $models -Recurse -Force  # evita baixar 200 MB de novo; o preparo do ASR tiny ainda baixa
        }
        $config = Get-Content -Raw (Join-Path $App "config.json") | ConvertFrom-Json
        $config.model_size = "tiny"
        $config | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $App "config.json") -Encoding utf8
        $script:Fingerprint = Data-Fingerprint
        $started = Get-Date
        $json = Cli @("transcribe", "$Lab\fixtures\whatsapp.ogg", "--local", "--json", "-q", "--timeout", "1500")
        Assert ($script:CliExit -eq 0) "saída $script:CliExit; stderr: $(Get-Content -Raw "$Run\cli-stderr.txt")"
        $result = $json | ConvertFrom-Json
        Assert ($result.turns.Count -ge 2) "poucas falas: $($result.turns.Count)"
        $words = ($result.turns.text -join " ")
        Assert ($words -match "cronograma|entrega|reuni") "texto inesperado: $words"
        "{0} falas, {1} falante(s), {2:n0} s" -f $result.turns.Count, $result.speakers.Count, ((Get-Date) - $started).TotalSeconds
    }
    Check "transcrição aparece na biblioteca do PACE" {
        $list = Cli @("list", "--json") | ConvertFrom-Json
        Assert ($list[0].requested_by -eq "agent" -and $list[0].status -eq "ready") "lista: $($list | ConvertTo-Json -Compress)"
        $list[0].id
    }

    Log "== 5. 1.6.0 -> 1.6.1-teste com servidor MCP de agente aberto"
    Check "1.6.0 atualiza para 1.6.1 mesmo com MCP aberto" {
        Assert (-not $script:Mcp.HasExited) "servidor MCP já tinha fechado"
        Serve "v1.6.1" "$Lab\next\Pace_Installer.exe"
        Send-Pace "apply-update"
        Wait-Until { (Installed-Version) -eq "1.6.1" -and (Running-Version) -eq "1.6.1" } 600 "1.6.1 instalada e confirmada"
        $result = Last-UpdateResult
        Assert ($result.status -eq "updated") "resultado: $($result | ConvertTo-Json -Compress)"
        Assert ($script:Mcp.HasExited) "o servidor MCP antigo continuou rodando"
        "1.6.1 confirmada; MCP antigo encerrado pelo auxiliar"
    }
    Check "dados preservados (1.6.0 -> 1.6.1)" {
        $now = Data-Fingerprint
        Assert ($now -eq $script:Fingerprint) "antes $script:Fingerprint, depois $now"
        $list = Cli @("list", "--json") | ConvertFrom-Json
        Assert ($list.Count -ge 1) "biblioteca vazia"
        "iguais; biblioteca com $($list.Count) item(ns)"
    }
    Check "skill e MCP dos agentes atualizados para 1.6.1" {
        Wait-Until { (Get-Content -Raw "$HOME\.claude\skills\pace-transcricao\SKILL.md") -match "v1\.6\.1" } 60 "skill v1.6.1"
        Assert ((Cli @("--version")) -eq "PACE 1.6.1") "pace-cli não é 1.6.1"
        $server = Start-McpServer
        $init = Mcp $server '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
        $server.Kill()
        Assert ($init.result.serverInfo.version -eq "1.6.1") "MCP $($init.result.serverInfo.version)"
        "skill v1.6.1, MCP 1.6.1"
    }

    Log "== 6. rollback: instalador que falha de propósito"
    Check "falha na instalação volta para 1.6.1 funcionando" {
        Copy-Item "$Lab\abort\Pace_Failure_Installer.exe" "$Run\Pace_Installer.exe" -Force
        Serve "v1.6.2" "$Run\Pace_Installer.exe"
        $before = @(Get-ChildItem (Join-Path $Data "updates\runs\*\result.json") -ErrorAction SilentlyContinue).Count
        Send-Pace "apply-update"
        Wait-Until { @(Get-ChildItem (Join-Path $Data "updates\runs\*\result.json") -ErrorAction SilentlyContinue).Count -gt $before } 300 "resultado da tentativa"
        Assert ((Last-UpdateResult).status -eq "failed") "esperava falha"
        $updateLog = Get-ChildItem (Join-Path $Data "updates\runs\*\update.log") | Sort-Object LastWriteTime | Select-Object -Last 1
        # Só conta como rollback se o auxiliar chegou a restaurar os binários (falha antes disso não testa nada).
        Assert ((Get-Content -Raw -Encoding utf8 $updateLog.FullName) -match "Binários anteriores restaurados") "o auxiliar não restaurou os binários"
        Wait-Until { (Running-Version) -eq "1.6.1" } 120 "1.6.1 reaberta"
        Assert ((Cli @("--version")) -eq "PACE 1.6.1") "pace-cli quebrado após rollback"
        Assert ((Data-Fingerprint) -eq $script:Fingerprint) "dados mudaram no rollback"
        "rollback ok; pace-cli e dados intactos"
    }

    Log "== 7. desinstalação remove as conexões com agentes"
    Check "desinstalar limpa MCP e skill dos agentes" {
        Send-Pace "quit"
        Wait-Until { -not (Get-Process Pace -ErrorAction SilentlyContinue) } 60 "fechar PACE"
        $process = Start-Process (Join-Path $App "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
        Assert ($process.ExitCode -eq 0) "desinstalador retornou $($process.ExitCode)"
        Wait-Until { -not (Test-Path $Cli) } 120 "arquivos removidos"
        $claude = Get-Content -Raw "$HOME\.claude.json" | ConvertFrom-Json
        Assert (-not $claude.mcpServers.pace) "entrada do PACE ficou no Claude Code"
        Assert ($claude.numStartups -eq 1) "~/.claude.json perdeu dados (numStartups)"
        $desktop = Get-Content -Raw "$env:APPDATA\Claude\claude_desktop_config.json" | ConvertFrom-Json
        Assert (-not $desktop.mcpServers.pace) "entrada do PACE ficou no Claude Desktop"
        Assert ($desktop.preferences.laboratorio -eq $true) "config do Claude Desktop perdeu dados"
        Assert (-not (Test-Path "$HOME\.claude\skills\pace-transcricao")) "skill ficou"
        Assert (-not ((Get-Content -Raw "$HOME\.codex\config.toml") -match "mcp_servers\.pace")) "Codex ficou com o PACE"
        Assert ((Get-Content -Raw "$HOME\.codex\config.toml") -match "gpt-5") "config do Codex perdeu dados"
        "código de saída $($process.ExitCode)"
    }
} catch {
    $Report.checks += [ordered]@{ name = "roteiro"; ok = $false; detail = "$($_.Exception.Message)" }
    Log "ERRO no roteiro: $($_.Exception.Message)"
} finally {
    foreach ($source in @((Join-Path $Data "updates\runs"), (Join-Path $App "logs"))) {
        if (Test-Path $source) { Copy-Item $source (Join-Path $Results ("evidencias-" + (Split-Path $source -Leaf))) -Recurse -Force -ErrorAction SilentlyContinue }
    }
    $Report.finished = $true
    $Report.ended = (Get-Date).ToString("o")
    $Report.passed = @($Report.checks | Where-Object { -not $_.ok }).Count -eq 0
    Save
    Log "== fim: $(@($Report.checks | Where-Object { $_.ok }).Count)/$($Report.checks.Count) verificações ok"
    if (-not (Test-Path "$Lab\keep-open.flag")) { & shutdown.exe /s /t 5 }
}
