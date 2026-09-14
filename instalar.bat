@echo off
chcp 65001 >nul
title PACE - Instalacao
cd /d "%~dp0"

echo ===================================================
echo             PACE - Instalando...
echo ===================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ===================================================
    echo  [ERRO] A instalacao encontrou um problema.
    echo ===================================================
    echo.
    pause
) else (
    echo.
    echo ===================================================
    echo  [SUCESSO] Instalacao finalizada com sucesso!
    echo ===================================================
    echo.
    echo Pressione qualquer tecla para encerrar...
    pause >nul
)
