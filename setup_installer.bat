@echo off
setlocal
cd /d "%~dp0"
title Etix Checker 2026 - One-Click Installer
color 0b

echo ==============================================================================
echo        ETIX CHECKER 2026 -- ADSPOWER CDP EDITION (ONE-CLICK INSTALLER)
echo ==============================================================================
echo.
echo [*] Initializing setup engine...

if not exist "%~dp0setup_installer.ps1" (
    echo [*] Downloading setup engine from repository...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13; (New-Object Net.WebClient).DownloadFile('https://raw.githubusercontent.com/KrosinGG/EtixChecker/main/setup_installer.ps1', '%~dp0setup_installer.ps1')" 2>nul
)

if exist "%~dp0setup_installer.ps1" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_installer.ps1"
) else (
    echo [ERROR] Could not download setup_installer.ps1. Please ensure internet connection is available.
)

set "EXIT_CODE=%ERRORLEVEL%"
if %EXIT_CODE% NEQ 0 (
    echo.
    echo ==============================================================================
    echo [ERROR] Installation failed or was interrupted (Exit code: %EXIT_CODE%).
    echo ==============================================================================
    echo.
    pause
)
exit /b %EXIT_CODE%
