param(
    [string]$PreferredPythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"

Write-Host "--- Whisper Dictation Tray: Instalação ---" -ForegroundColor Cyan

# 1. Verificar FFmpeg
try {
    ffmpeg -version | Out-Null
    Write-Host "[OK] FFmpeg detectado." -ForegroundColor Green
} catch {
    Write-Host "[INFO] FFmpeg não encontrado. Tentando instalar via winget..." -ForegroundColor Cyan
    try {
        winget install --id=Gyan.FFmpeg --exact --silent --accept-source-agreements --accept-package-agreements
        Write-Host "[OK] FFmpeg instalado com sucesso via winget." -ForegroundColor Green
        Write-Host "Nota: Você pode precisar reiniciar o terminal para o comando 'ffmpeg' ser reconhecido." -ForegroundColor Yellow
    } catch {
        Write-Host "[AVISO] Não foi possível instalar o FFmpeg automaticamente." -ForegroundColor Yellow
        Write-Host "Por favor, instale manualmente: winget install ffmpeg"
        Write-Host ""
    }
}

# 2. Localizar Python
$PythonCmd = "py"
try {
    & $PythonCmd --version | Out-Null
} catch {
    $PythonCmd = "python"
    try {
        & $PythonCmd --version | Out-Null
    } catch {
        Write-Error "Python não encontrado. Por favor, instale o Python 3.10+."
    }
}

# 3. Criar Ambiente Virtual
if (-not (Test-Path $VenvPath)) {
    Write-Host "Criando ambiente virtual em $VenvPath..." -ForegroundColor Cyan
    if ($PythonCmd -eq "py") {
        & $PythonCmd -$PreferredPythonVersion -m venv $VenvPath
    } else {
        & $PythonCmd -m venv $VenvPath
    }
} else {
    Write-Host "[OK] Ambiente virtual já existe." -ForegroundColor Green
}

$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

# 4. Instalar Dependências
Write-Host "Atualizando pip e instalando dependências..." -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade pip --quiet
& $PythonExe -m pip install -r $Requirements --quiet

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
$ShortcutPath = Join-Path $StartupFolder "WhisperDictation.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\run.ps1`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Save()
Write-Host "[OK] Atalho de inicialização criado." -ForegroundColor Green

Write-Host ""
Write-Host "Instalação concluída com sucesso!" -ForegroundColor Green
Write-Host "Próximos passos:"
Write-Host "1. Edite o arquivo .env e coloque sua GROQ_API_KEY (opcional)."
Write-Host "2. Inicie o app executando: .\scripts\run.ps1 (ou apenas reinicie o computador)"
Write-Host ""
