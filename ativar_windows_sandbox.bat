@echo off
chcp 65001 >nul
title Ativando Área Restrita do Windows (Windows Sandbox)

:: Verifica se está executando como Administrador
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Solicitando privilégios de Administrador...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ====================================================================
echo         Ativando o Windows Sandbox (Área Restrita do Windows)
echo ====================================================================
echo.
echo Aguarde a ativação do recurso nativo do Windows...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All -NoRestart"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ====================================================================
    echo  [SUCESSO] Recurso 'Área Restrita do Windows' ativado com sucesso!
    echo ====================================================================
    echo.
    echo Para que o Windows Sandbox passe a funcionar, o Windows precisa ser reiniciado.
    echo.
    set /p REBOOT="Deseja reiniciar o computador agora? (S/N): "
    if /i "%REBOOT%"=="S" (
        echo Reiniciando em 5 segundos... Pressione Ctrl+C para cancelar.
        shutdown /r /t 5 /c "Reiniciando para concluir a ativacao da Area Restrita do Windows."
    ) else (
        echo.
        echo Tudo pronto! Assim que reiniciar o computador, basta dar um duplo clique
        echo no arquivo 'test_in_sandbox.wsb' para abrir o Sandbox e testar o instalador!
        echo.
        pause
    )
) else (
    echo.
    echo [ERRO] Ocorreu uma falha ao ativar o recurso.
    echo Verifique se a virtualização está ativa na BIOS da sua placa-mãe.
    echo.
    pause
)
