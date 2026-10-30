# GitHub falso para o laboratório. Roda SOMENTE dentro da Windows Sandbox (descartável).
# Atende o que o atualizador do PACE consulta, com o mesmo formato da API real:
#   https://api.github.com/repos/<repo>/releases/latest
#   https://github.com/<repo>/releases/download/<tag>/Pace_Installer.exe
# O release servido é lido a cada pedido de -State (JSON: {"tag": "v1.6.0", "installer": "C:\\...\\Pace_Installer.exe"}).
param(
    [Parameter(Mandatory = $true)][string]$State,
    [string]$Repo = "luizfernando608/whisper-dictation-tray",
    [string]$Log = "C:\results\fake_github.log"
)
$ErrorActionPreference = "Stop"

function Write-Log([string]$Message) {
    Add-Content -Path $Log -Value ("{0} {1}" -f (Get-Date -Format o), $Message) -Encoding utf8
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("https://+:443/")
$listener.Start()
Write-Log "fake GitHub ouvindo em 443"
$digests = @{}

while ($listener.IsListening) {
    $context = $listener.GetContext()
    $request, $response = $context.Request, $context.Response
    try {
        $release = Get-Content -Raw -Path $State | ConvertFrom-Json
        $installer = Get-Item $release.installer
        $key = "$($installer.FullName)|$($installer.Length)|$($installer.LastWriteTimeUtc.Ticks)"
        if (-not $digests.ContainsKey($key)) {
            $digests[$key] = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLower()
        }
        $path = $request.Url.AbsolutePath
        $hostName = $request.Url.Host
        Write-Log "$($request.HttpMethod) $hostName$path"
        if ($hostName -eq "api.github.com" -and $path -eq "/repos/$Repo/releases/latest") {
            $body = [ordered]@{
                tag_name = $release.tag
                name     = "PACE $($release.tag) (laboratório)"
                body     = "Release falso do laboratório local."
                assets   = @([ordered]@{
                    name                 = "Pace_Installer.exe"
                    size                 = $installer.Length
                    digest               = "sha256:$($digests[$key])"
                    browser_download_url = "https://github.com/$Repo/releases/download/$($release.tag)/Pace_Installer.exe"
                })
            } | ConvertTo-Json -Depth 5
            $bytes = [Text.Encoding]::UTF8.GetBytes($body)
            $response.ContentType = "application/json; charset=utf-8"
            $response.ContentLength64 = $bytes.Length
            $response.OutputStream.Write($bytes, 0, $bytes.Length)
        } elseif ($hostName -eq "github.com" -and $path -eq "/$Repo/releases/download/$($release.tag)/Pace_Installer.exe") {
            $response.ContentType = "application/octet-stream"
            $response.ContentLength64 = $installer.Length
            $stream = [IO.File]::OpenRead($installer.FullName)
            try { $stream.CopyTo($response.OutputStream) } finally { $stream.Dispose() }
        } else {
            $response.StatusCode = 404
        }
    } catch {
        Write-Log "erro: $($_.Exception.Message)"
        try { $response.StatusCode = 500 } catch {}
    } finally {
        try { $response.Close() } catch {}
    }
}
