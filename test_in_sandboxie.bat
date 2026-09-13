@echo off
chcp 65001 >nul
title Testar Instalador com Sandboxie-Plus (Sem Reiniciar o PC)

cd /d "%~dp0"
set INSTALLER="%~dp0Output\WhisperDictation_Installer.exe"

if not exist %INSTALLER% (
    echo [ERRO] O instalador nao foi encontrado em:
    echo %INSTALLER%
    echo Execute build.ps1 primeiro para gerar o instalador.
    pause
    exit /b 1
)

:: Procura o executavel do Sandboxie
set SB_EXE=
if exist "%ProgramFiles%\Sandboxie-Plus\Start.exe" set SB_EXE="%ProgramFiles%\Sandboxie-Plus\Start.exe"
if exist "%ProgramFiles%\Sandboxie\Start.exe" set SB_EXE="%ProgramFiles%\Sandboxie\Start.exe"
if exist "%ProgramFiles(x86)%\Sandboxie\Start.exe" set SB_EXE="%ProgramFiles(x86)%\Sandboxie\Start.exe"

if not defined SB_EXE (
    echo ====================================================================
    echo             Sandboxie-Plus nao encontrado no sistema
    echo ====================================================================
    echo.
    echo O Sandboxie-Plus permite testar o instalador em uma caixa de areia
    echo isolada SEM precisar reiniciar o computador.
    echo.
    set /p INSTALL_SB="Deseja instalar o Sandboxie-Plus agora via winget? (S/N): "
    if /i "%INSTALL_SB%"=="S" (
        echo.
        echo Instalando Sandboxie-Plus via winget...
        winget install --id Sandboxie.Plus -e --accept-package-agreements --accept-source-agreements
        if exist "%ProgramFiles%\Sandboxie-Plus\Start.exe" (
            set SB_EXE="%ProgramFiles%\Sandboxie-Plus\Start.exe"
        ) else (
            echo [AVISO] Instalacao concluida. Se necessario, reinicie este script.
            pause
            exit /b 0
        )
    ) else (
        echo Cancelado.
        exit /b 0
    )
)

echo.
echo ====================================================================
echo        Iniciando instalador em Sandbox isolado (DefaultBox)
echo ====================================================================
echo.
echo O instalador sera aberto em uma caixa de areia protegida.
echo Nao afetara seu app real nem os atalhos da sua maquina.
echo.

%SB_EXE% /box:DefaultBox %INSTALLER%
