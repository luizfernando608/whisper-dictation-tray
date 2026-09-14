param(
    [string]$PreferredPythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"

Write-Host "--- PACE: Instalação ---" -ForegroundColor Cyan

# 1. Verificar FFmpeg
$FfmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $FfmpegCmd) {
    $WingetFfmpeg = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($WingetFfmpeg) {
        $env:Path += ";$($WingetFfmpeg.DirectoryName)"
        Write-Host "[OK] FFmpeg detectado em $($WingetFfmpeg.DirectoryName)." -ForegroundColor Green
    } else {
        Write-Host "[INFO] FFmpeg não encontrado no PATH. Tentando instalar via winget..." -ForegroundColor Cyan
        try {
            winget install --id=Gyan.FFmpeg --exact --silent --accept-source-agreements --accept-package-agreements
            Write-Host "[OK] FFmpeg instalado com sucesso via winget." -ForegroundColor Green
        } catch {
            Write-Host "[AVISO] Não foi possível instalar o FFmpeg automaticamente (opcional)." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[OK] FFmpeg detectado." -ForegroundColor Green
}

# 2. Localizar Python
$PythonCmd = $null
$PythonArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    $PyList = py -0 2>$null | Out-String
    if ($PyList -match [regex]::Escape($PreferredPythonVersion)) {
        $PythonCmd = "py"
        $PythonArgs = @("-$PreferredPythonVersion")
    } else {
        $PythonCmd = "py"
        $PythonArgs = @()
    }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCmd = "python"
    $PythonArgs = @()
}

if (-not $PythonCmd) {
    Write-Error "Python não encontrado. Por favor, instale o Python 3.10+ em https://python.org"
}

$DetectedVer = & $PythonCmd @PythonArgs --version 2>&1
Write-Host "[OK] $DetectedVer detectado." -ForegroundColor Green

# 3. Criar Ambiente Virtual
if (-not (Test-Path $VenvPath)) {
    Write-Host "Criando ambiente virtual em $VenvPath..." -ForegroundColor Cyan
    & $PythonCmd @PythonArgs -m venv $VenvPath
    if (-not (Test-Path (Join-Path $VenvPath "Scripts\python.exe"))) {
        Write-Error "Falha ao criar o ambiente virtual com $PythonCmd $PythonArgs."
    }
    Write-Host "[OK] Ambiente virtual criado." -ForegroundColor Green
} else {
    Write-Host "[OK] Ambiente virtual já existe." -ForegroundColor Green
}

$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

# 4. Instalar Dependências
Write-Host "Atualizando pip e instalando dependências (isso pode levar alguns minutos)..." -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r $Requirements

# 5. Configuração Inicial
$EnvFile = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "[INFO] Arquivo .env criado a partir do .env.example." -ForegroundColor Cyan
}

# 6. Inicialização com o Windows (Startup)
Write-Host "Configurando inicialização automática com o Windows..." -ForegroundColor Cyan
$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "PACE.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ProjectRoot\run.ps1`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Ico = Join-Path $ProjectRoot "assets\branding\pace.ico"
if (Test-Path $Ico) { $Shortcut.IconLocation = $Ico }
$Shortcut.Save()
Write-Host "[OK] Atalho de inicialização criado." -ForegroundColor Green

Write-Host ""
Write-Host "Instalação concluída com sucesso!" -ForegroundColor Green
Write-Host "Próximos passos:"
Write-Host "1. Edite o arquivo .env e coloque sua GROQ_API_KEY (opcional)."
Write-Host "2. Inicie o app executando: .\run.ps1 (ou apenas reinicie o computador)"
Write-Host ""
