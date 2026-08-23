@echo off
cd /d "%~dp0"
title Etix Checker 2026 - Release Packager
echo [*] Building client release archive...
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe pack_release.py
) else (
    python pack_release.py
)
echo.
pause
