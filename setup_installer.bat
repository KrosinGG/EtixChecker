@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

title Etix Checker 2026 — Автоматический установщик (One-Click Installer)
color 0b
mode con: cols=95 lines=32

echo ==============================================================================
echo        🎟️  ETIX CHECKER 2026 — ADSPOWER CDP EDITION (ONE-CLICK INSTALLER)
echo ==============================================================================
echo.

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

rem ------------------------------------------------------------------------------
rem 1. Определение целевой папки проекта
rem ------------------------------------------------------------------------------
if exist "%SCRIPT_DIR%gui_app.py" (
    set "PROJECT_DIR=%SCRIPT_DIR%"
) else if exist "%SCRIPT_DIR%requirements.txt" (
    set "PROJECT_DIR=%SCRIPT_DIR%"
) else (
    set "PROJECT_DIR=%SCRIPT_DIR%Etix Checker 2026 — AdsPower CDP Edition"
    if not exist "!PROJECT_DIR!" (
        echo [*] Создание рабочей директории: "!PROJECT_DIR!"
        mkdir "!PROJECT_DIR!" >nul 2>&1
    )
    cd /d "!PROJECT_DIR!"
    
    echo [*] Загрузка актуального репозитория проекта...
    where git >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        git clone https://github.com/KrosinGG/EtixChecker.git .
    ) else (
        echo [*] Git не найден. Загрузка архива проекта через PowerShell...
        powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://github.com/KrosinGG/EtixChecker/archive/refs/heads/main.zip' -OutFile 'repo.zip'; Expand-Archive -Path 'repo.zip' -DestinationPath 'temp_repo' -Force; Copy-Item -Path 'temp_repo\EtixChecker-main\*' -Destination '.' -Recurse -Force; Remove-Item -Recurse -Force 'repo.zip', 'temp_repo'"
    )
)

cd /d "%PROJECT_DIR%"
echo [+] Рабочая директория: %CD%
echo.

rem ------------------------------------------------------------------------------
rem 2. Проверка и автоматическая установка Python 3.10+
rem ------------------------------------------------------------------------------
echo [*] Проверка наличия Python 3.10+...
set "PY_CMD="

where python >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    where py >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            set "PY_CMD=py -3"
        )
    )
)

if not defined PY_CMD (
    echo.
    echo [!] Python 3.10+ не обнаружен в системе.
    echo [*] Автоматическая загрузка официального Python 3.11 64-bit...
    set "PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe"
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe', '$env:TEMP\python-3.11.9-amd64.exe')"
    
    if exist "!PY_INSTALLER!" (
        echo [*] Установка Python 3.11 (тихий режим, добавление в PATH)...
        "!PY_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1 SimpleInstall=1
        del /f /q "!PY_INSTALLER!" >nul 2>&1
        
        rem Обновление PATH в текущей сессии
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"
        set "PY_CMD=python"
    ) else (
        echo [ERROR] Не удалось скачать установщик Python. Пожалуйста, установите Python 3.10+ вручную с https://www.python.org/
        pause
        exit /b 1
    )
)

echo [+] Используется Python: !PY_CMD!
echo.

rem ------------------------------------------------------------------------------
rem 3. Создание изолированного виртуального окружения (venv)
rem ------------------------------------------------------------------------------
set "VENV_DIR=%CD%\venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

if not exist "%VENV_PY%" (
    echo [*] Создание виртуального окружения venv...
    !PY_CMD! -m venv venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Ошибка создания venv.
        pause
        exit /b 1
    )
)
echo [+] Виртуальное окружение venv готово.
echo.

rem ------------------------------------------------------------------------------
rem 4. Установка библиотек и браузера Playwright Chromium
rem ------------------------------------------------------------------------------
echo [*] Обновление pip и установка зависимостей из requirements.txt...
"%VENV_PY%" -m pip install --upgrade pip -q
if exist "requirements.txt" (
    "%VENV_PIP%" install -r requirements.txt -q
    if !ERRORLEVEL! NEQ 0 (
        echo [!] Повторная попытка установки зависимостей...
        "%VENV_PIP%" install -r requirements.txt
    )
)
echo [+] Все библиотеки успешно установлены.

echo [*] Проверка и установка браузера Playwright Chromium...
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\ms-playwright"
if not exist "ms-playwright" mkdir "ms-playwright" >nul 2>&1
"%VENV_DIR%\Scripts\playwright.exe" install chromium
echo [+] Playwright Chromium готов к работе.
echo.

rem ------------------------------------------------------------------------------
rem 5. Конфигурация и рабочие директории
rem ------------------------------------------------------------------------------
echo [*] Инициализация конфигурационных файлов...
if not exist "data" mkdir "data" >nul 2>&1
if not exist "data\adspower_backup" mkdir "data\adspower_backup" >nul 2>&1
if not exist "logs" mkdir "logs" >nul 2>&1
if not exist "screens" mkdir "screens" >nul 2>&1
if not exist "runs" mkdir "runs" >nul 2>&1

if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul 2>&1
    ) else (
        (
            echo ADSPOWER_API_URL=http://127.0.0.1:50325
            echo ADSPOWER_GROUP_NAME=Inventory Etix ^(DO NOT TOUCH^)
            echo ADSPOWER_ACTIVE_PROFILES_COUNT=12
            echo ETIX_HEADLESS=false
            echo ETIX_SLOWMO_MS=80
            echo ETIX_NAV_TIMEOUT=18000
            echo ETIX_CLICK_TIMEOUT=20000
            echo ETIX_DELAY_BEFORE_CLEAR_CARTS_S=4.0
            echo ETIX_STRICT_ALL_CARTS=true
        ) > ".env"
    )
    echo [+] Создан файл конфигурации .env
)

if not exist "data\shows.csv" (
    (
        echo name,url,target_total,max_per_order,ticket_index
        echo NateSmith,https://www.etix.com/ticket/p/35196855/nate-smith-palmer-alaska-state-fair,12,4,2
        echo Gasolina Party,https://www.etix.com/ticket/p/87677793/gasolina-party-providence-the-strand-theatre?partner_id=100,24,6,2
    ) > "data\shows.csv"
    echo [+] Создан файл примеров data\shows.csv
)

rem ------------------------------------------------------------------------------
rem 6. Создание ярлыка на Рабочем столе (бесшумный запуск без черного окна CMD)
rem ------------------------------------------------------------------------------
echo [*] Создание ярлыка на Рабочем столе...
set "ICON_PATH=%CD%\icons\etix_robot_round.ico"
set "LAUNCHER_PATH=%CD%\run_gui.vbs"

if not exist "run_gui.vbs" (
    (
        echo Set shell = CreateObject^("WScript.Shell"^)
        echo Set fso = CreateObject^("Scripting.FileSystemObject"^)
        echo scriptDir = fso.GetParentFolderName^(WScript.ScriptFullName^)
        echo cmd = "cmd /c """ ^& scriptDir ^& "\run_gui.bat"" --no-pause"
        echo shell.Run cmd, 0, False
    ) > "run_gui.vbs"
)

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = [System.Environment]::GetFolderPath('Desktop'); $s = $ws.CreateShortcut(\"$desktop\Etix Checker 2026.lnk\"); $s.TargetPath = '%LAUNCHER_PATH%'; $s.WorkingDirectory = '%CD%'; if (Test-Path '%ICON_PATH%') { $s.IconLocation = '%ICON_PATH%' }; $s.Description = 'Etix Checker 2026 — AdsPower CDP Edition'; $s.Save()"
echo [+] Ярлык «Etix Checker 2026» создан на рабочем столе.
echo.

rem ------------------------------------------------------------------------------
rem 7. Проверка доступности AdsPower Local API
rem ------------------------------------------------------------------------------
echo [*] Проверка подключения к AdsPower Local API (порт 50325)...
powershell -NoProfile -Command "try { $res = Invoke-RestMethod -Uri 'http://127.0.0.1:50325/api/v1/user/list?page_size=1' -TimeoutSec 3; if ($res.code -eq 0) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] AdsPower Local API обнаружен и доступен!
) else (
    echo [INFO] AdsPower сейчас закрыт или Local API отключен.
    echo        Не забудьте запустить AdsPower перед началом проверки билетов.
)
echo.

echo ==============================================================================
echo       🎉  УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА! ВСЕ КОМПОНЕНТЫ ГОТОВЫ К РАБОТЕ!
echo ==============================================================================
echo.
echo Для запуска программы используйте:
echo   1. Ярлык на Рабочем столе: «Etix Checker 2026»
echo   2. Файл запуска GUI: run_gui.bat (или run_gui.vbs)
echo   3. Файл запуска консоли (CLI): run.bat
echo.
echo Подробная инструкция: файл ИНСТРУКЦИЯ.md в папке проекта.
echo.

set /p START_NOW="Запустить графический интерфейс прямо сейчас? (Y/N, Enter = Y): "
if /i "%START_NOW%"=="" set "START_NOW=Y"
if /i "%START_NOW%"=="Y" (
    start "" run_gui.bat
)

exit /b 0
