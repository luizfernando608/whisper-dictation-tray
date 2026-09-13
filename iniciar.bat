@echo off
chcp 65001 >nul
title Whisper Dictation Tray
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
