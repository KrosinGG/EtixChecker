@echo off
setlocal
cd /d "%~dp0"

title ETIX CHECKER
color 07
mode con: cols=90 lines=30

set "VENV=venv"
set "VPY=%VENV%\Scripts\python.exe"
set "STAMP=%VENV%\.deps_installed_gui"
set "LOGDIR=logs"
set "LOGFILE=%LOGDIR%\setup.log"

if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

rem 1) venv
if not exist "%VPY%" (
  echo Installing components...
  py -3 -m venv "%VENV%" >nul 2>&1 || python -m venv "%VENV%" >nul 2>&1 || (echo [ERROR] venv create failed & pause & exit /b 1)
)

rem 2) deps
if not exist "%STAMP%" (
  echo Installing components...
  "%VPY%" -m pip install --upgrade pip -q >>"%LOGFILE%" 2>&1
  "%VPY%" -m pip install -r requirements.txt -q >>"%LOGFILE%" 2>&1

  set "PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright"
  "%VPY%" -m playwright install chromium >>"%LOGFILE%" 2>&1

  >"%STAMP%" echo ok
)

rem 3) playwright cache
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright"

rem 4) run GUI
set "PYTHONUTF8=1"
"%VPY%" gui_app.py
set EC=%ERRORLEVEL%

if /I "%~1"=="--no-pause" (
  exit /b %EC%
)

echo.
echo Exit code: %EC%
pause
exit /b %EC%
