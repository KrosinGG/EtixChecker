@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Etix Checker 2026 - One-Click Installer
color 0b

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$f = '%~f0'; $lines = [System.IO.File]::ReadAllLines($f, [System.Text.Encoding]::UTF8); $idx = 0; while ($idx -lt $lines.Count -and $lines[$idx] -notmatch '^\s*###PS_START###\s*$') { $idx++ }; if ($idx -lt $lines.Count) { $script = ($lines[($idx+1)..($lines.Count-1)] -join [Environment]::NewLine); & ([ScriptBlock]::Create($script)) }"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ==============================================================================
echo Нажмите любую клавишу для закрытия этого окна...
echo ==============================================================================
pause >nul
exit /b %EXIT_CODE%

###PS_START###

# ==============================================================================
#  ETIX CHECKER 2026 -- ADSPOWER CDP EDITION (ONE-CLICK INSTALLER)
# ==============================================================================

$Host.UI.RawUI.WindowTitle = "Etix Checker 2026 -- One-Click Installer"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "       🎟️  ETIX CHECKER 2026 -- ADSPOWER CDP EDITION (ONE-CLICK INSTALLER)      " -ForegroundColor Yellow
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""

try {
    $scriptDir = (Get-Location).Path
    Set-Location $scriptDir

    # --------------------------------------------------------------------------
    # 1. Определение целевой рабочей папки и исходных файлов
    # --------------------------------------------------------------------------
    $isRepoFolder = (Test-Path (Join-Path $scriptDir "gui_app.py")) -or (Test-Path (Join-Path $scriptDir "requirements.txt"))

    if (-not $isRepoFolder) {
        $targetDir = Join-Path $scriptDir "Etix Checker 2026"
        if (-not (Test-Path $targetDir)) {
            Write-Host "[*] Создание рабочей папки: $targetDir" -ForegroundColor Cyan
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }
        Set-Location $targetDir
        $scriptDir = $targetDir

        # Проверяем, содержит ли папка файлы репозитория
        $repoReady = (Test-Path (Join-Path $scriptDir "gui_app.py"))
        if (-not $repoReady) {
            Write-Host "[*] Загрузка файлов проекта с GitHub..." -ForegroundColor Cyan
            $downloadSuccess = $false

            # Попытка через Git
            $gitExists = Get-Command "git" -ErrorAction SilentlyContinue
            if ($gitExists) {
                Write-Host "[*] Попытка клонирования через Git..." -ForegroundColor Gray
                try {
                    & git clone https://github.com/KrosinGG/EtixChecker.git . 2>$null
                    if (Test-Path (Join-Path $scriptDir "gui_app.py")) {
                        $downloadSuccess = $true
                    }
                } catch {}
            }

            # Попытка через ZIP
            if (-not $downloadSuccess) {
                Write-Host "[*] Загрузка архива проекта..." -ForegroundColor Gray
                $zipPath = Join-Path $env:TEMP "repo_$RANDOM.zip"
                $tempExtract = Join-Path $env:TEMP "extract_$RANDOM"
                
                $wc = New-Object System.Net.WebClient
                $wc.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                
                try {
                    $downloadUrl = "https://github.com/KrosinGG/EtixChecker/archive/refs/heads/main.zip"
                    $wc.DownloadFile($downloadUrl, $zipPath)
                    Expand-Archive -Path $zipPath -DestinationPath $tempExtract -Force
                    $extractedSub = Get-ChildItem -Path $tempExtract | Select-Object -First 1
                    if ($extractedSub) {
                        Copy-Item -Path "$($extractedSub.FullName)\*" -Destination $scriptDir -Recurse -Force
                    }
                    Remove-Item -Path $zipPath, $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
                    if (Test-Path (Join-Path $scriptDir "gui_app.py")) {
                        $downloadSuccess = $true
                    }
                } catch {}
            }

            if (-not $downloadSuccess) {
                Write-Host ""
                Write-Host "==============================================================================" -ForegroundColor Yellow
                Write-Host "⚠️  ВНИМАНИЕ: РЕПОЗИТОРИЙ GITHUB ЯВЛЯЕТСЯ ПРИВАТНЫМ" -ForegroundColor Yellow
                Write-Host "==============================================================================" -ForegroundColor Yellow
                Write-Host ""
                Write-Host "Файлы проекта не могут быть скачаны анонимно без авторизации в GitHub." -ForegroundColor White
                Write-Host ""
                Write-Host "КАК УСТАНОВИТЬ ПРОГРАММУ КЛИЕНТУ:" -ForegroundColor Cyan
                Write-Host "  1. Передайте клиенту архив с файлами проекта (например, EtixChecker.zip)." -ForegroundColor White
                Write-Host "  2. Клиент распаковывает архив в любую папку." -ForegroundColor White
                Write-Host "  3. Запускает файл setup_installer.bat ВНУТРИ распакованной папки." -ForegroundColor White
                Write-Host ""
                Write-Host "Установщик автоматически настроит Python, зависимости и ярлык на Рабочем столе!" -ForegroundColor Green
                Write-Host ""
                throw "Файлы проекта не найдены. Пожалуйста, запустите setup_installer.bat внутри папки с распакованным проектом."
            }
        }
    }

    Write-Host "[+] Рабочая директория: $scriptDir" -ForegroundColor Green
    Write-Host ""

    # --------------------------------------------------------------------------
    # 2. Проверка и автоматическая установка Python 3.10+
    # --------------------------------------------------------------------------
    Write-Host "[*] Проверка наличия Python 3.10+ на компьютере..." -ForegroundColor Cyan
    $pythonExe = $null

    function Test-PythonCandidate($exe) {
        if (-not $exe) { return $null }
        try {
            if ($exe -eq "py") {
                & py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            } else {
                & "$exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            }
            if ($LASTEXITCODE -eq 0) { return $exe }
        } catch {}
        return $null
    }

    # 2.1 Проверяем команды в PATH (python, py, python3)
    foreach ($cand in @("python", "py", "python3")) {
        $found = Test-PythonCandidate $cand
        if ($found) {
            $pythonExe = $found
            break
        }
    }

    # 2.2 Проверяем стандартные пути установки Windows
    if (-not $pythonExe) {
        $searchDirs = @(
            "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
            "$env:ProgramFiles\Python314\python.exe",
            "$env:ProgramFiles\Python313\python.exe",
            "$env:ProgramFiles\Python312\python.exe",
            "$env:ProgramFiles\Python311\python.exe",
            "$env:ProgramFiles\Python310\python.exe",
            "C:\Python314\python.exe",
            "C:\Python313\python.exe",
            "C:\Python312\python.exe",
            "C:\Python311\python.exe",
            "C:\Python310\python.exe"
        )
        foreach ($path in $searchDirs) {
            if (Test-Path $path) {
                $found = Test-PythonCandidate $path
                if ($found) {
                    $pythonExe = $found
                    break
                }
            }
        }
    }

    # 2.3 Если Python не найден - скачиваем и устанавливаем Python 3.11 64-bit
    if (-not $pythonExe) {
        Write-Host ""
        Write-Host "[!] Python 3.10+ не обнаружен в системе." -ForegroundColor Yellow
        Write-Host "[*] Автоматическая загрузка официального установщика Python 3.11 (64-bit)..." -ForegroundColor Cyan
        
        $pyInstallerPath = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
        $pyUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($pyUrl, $pyInstallerPath)
        
        if (Test-Path $pyInstallerPath) {
            Write-Host "[*] Установка Python 3.11 (тихий режим, добавление в PATH)..." -ForegroundColor Cyan
            $p = Start-Process -FilePath $pyInstallerPath -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1 SimpleInstall=1" -Wait -PassThru
            Remove-Item -Path $pyInstallerPath -Force -ErrorAction SilentlyContinue
            
            $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
            $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            $env:Path = "$env:LOCALAPPDATA\Programs\Python\Python311;$env:LOCALAPPDATA\Programs\Python\Python311\Scripts;$userPath;$machinePath"
            
            $pyDirect = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
            if (Test-Path $pyDirect) {
                $pythonExe = $pyDirect
            } else {
                $pythonExe = "python"
            }
        } else {
            throw "Не удалось скачать установщик Python. Установите Python 3.10+ вручную с https://www.python.org/"
        }
    }

    Write-Host "[+] Обнаружен и используется Python: $pythonExe" -ForegroundColor Green
    Write-Host ""

    # --------------------------------------------------------------------------
    # 3. Создание изолированного виртуального окружения (venv)
    # --------------------------------------------------------------------------
    $venvDir = Join-Path $scriptDir "venv"
    $venvPy = Join-Path $venvDir "Scripts\python.exe"
    $venvPip = Join-Path $venvDir "Scripts\pip.exe"

    if (-not (Test-Path $venvPy)) {
        Write-Host "[*] Создание изолированного окружения (venv)..." -ForegroundColor Cyan
        if ($pythonExe -eq "py") {
            & py -3 -m venv "$venvDir"
        } else {
            & "$pythonExe" -m venv "$venvDir"
        }
        if ($LASTEXITCODE -ne 0 -or (-not (Test-Path $venvPy))) {
            throw "Ошибка при создании виртуального окружения venv."
        }
    }
    Write-Host "[+] Виртуальное окружение venv готово." -ForegroundColor Green
    Write-Host ""

    # --------------------------------------------------------------------------
    # 4. Установка зависимостей и браузера Playwright Chromium
    # --------------------------------------------------------------------------
    Write-Host "[*] Обновление pip и установка библиотек..." -ForegroundColor Cyan
    & "$venvPy" -m pip install --upgrade pip -q 2>$null

    $reqFile = Join-Path $scriptDir "requirements.txt"
    if (Test-Path $reqFile) {
        & "$venvPip" install -r "$reqFile" -q
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[!] Повторная попытка установки библиотек..." -ForegroundColor Yellow
            & "$venvPip" install -r "$reqFile"
        }
    } else {
        & "$venvPip" install playwright pandas customtkinter rich textual httpx python-dotenv -q
    }
    Write-Host "[+] Все библиотеки успешно установлены." -ForegroundColor Green

    Write-Host "[*] Проверка и установка браузера Playwright Chromium..." -ForegroundColor Cyan
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $scriptDir "ms-playwright"
    if (-not (Test-Path $env:PLAYWRIGHT_BROWSERS_PATH)) {
        New-Item -ItemType Directory -Path $env:PLAYWRIGHT_BROWSERS_PATH -Force | Out-Null
    }
    & "$venvPy" -m playwright install chromium
    Write-Host "[+] Playwright Chromium готов к работе." -ForegroundColor Green
    Write-Host ""

    # --------------------------------------------------------------------------
    # 5. Инициализация конфигурационных файлов
    # --------------------------------------------------------------------------
    Write-Host "[*] Проверка структуры папок и конфигурации..." -ForegroundColor Cyan
    @("data", "data\adspower_backup", "logs", "screens", "runs") | ForEach-Object {
        $folderPath = Join-Path $scriptDir $_
        if (-not (Test-Path $folderPath)) {
            New-Item -ItemType Directory -Path $folderPath -Force | Out-Null
        }
    }

    $envFile = Join-Path $scriptDir ".env"
    $envExample = Join-Path $scriptDir ".env.example"
    if (-not (Test-Path $envFile)) {
        if (Test-Path $envExample) {
            Copy-Item -Path $envExample -Destination $envFile -Force
        } else {
            $envText = "ADSPOWER_API_URL=http://127.0.0.1:50325`nADSPOWER_GROUP_NAME=Inventory Etix (DO NOT TOUCH)`nADSPOWER_ACTIVE_PROFILES_COUNT=12`nETIX_HEADLESS=false`nETIX_SLOWMO_MS=80`nETIX_NAV_TIMEOUT=18000`nETIX_CLICK_TIMEOUT=20000`nETIX_DELAY_BEFORE_CLEAR_CARTS_S=4.0`nETIX_STRICT_ALL_CARTS=true"
            $envText | Out-File -FilePath $envFile -Encoding utf8
        }
        Write-Host "[+] Создан файл конфигурации .env" -ForegroundColor Green
    }

    $showsFile = Join-Path $scriptDir "data\shows.csv"
    if (-not (Test-Path $showsFile)) {
        $showsText = "name,url,target_total,max_per_order,ticket_index`nNateSmith,https://www.etix.com/ticket/p/35196855/nate-smith-palmer-alaska-state-fair,12,4,2`nGasolina Party,https://www.etix.com/ticket/p/87677793/gasolina-party-providence-the-strand-theatre?partner_id=100,24,6,2"
        $showsText | Out-File -FilePath $showsFile -Encoding utf8
        Write-Host "[+] Создан файл примеров data\shows.csv" -ForegroundColor Green
    }

    $vbsFile = Join-Path $scriptDir "run_gui.vbs"
    if (-not (Test-Path $vbsFile)) {
        $vbsText = "Set shell = CreateObject(`"WScript.Shell`")`nSet fso = CreateObject(`"Scripting.FileSystemObject`")`nscriptDir = fso.GetParentFolderName(WScript.ScriptFullName)`ncmd = `"cmd /c `"`"`" & scriptDir & `"un_gui.bat`"`"`" --no-pause`"`nshell.Run cmd, 0, False"
        $vbsText | Out-File -FilePath $vbsFile -Encoding ascii
    }

    # --------------------------------------------------------------------------
    # 6. Создание ярлыка на Рабочем столе
    # --------------------------------------------------------------------------
    Write-Host "[*] Создание ярлыка на Рабочем столе..." -ForegroundColor Cyan
    $desktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
    $shortcutPath = Join-Path $desktopPath "Etix Checker 2026.lnk"
    $iconPath = Join-Path $scriptDir "icons\etix_robot_round.ico"

    $ws = New-Object -ComObject WScript.Shell
    $shortcut = $ws.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $vbsFile
    $shortcut.WorkingDirectory = $scriptDir
    if (Test-Path $iconPath) {
        $shortcut.IconLocation = $iconPath
    }
    $shortcut.Description = "Etix Checker 2026 -- AdsPower CDP Edition"
    $shortcut.Save()
    Write-Host "[+] Ярлык «Etix Checker 2026» создан на рабочем столе!" -ForegroundColor Green
    Write-Host ""

    # --------------------------------------------------------------------------
    # 7. Проверка доступности AdsPower Local API
    # --------------------------------------------------------------------------
    Write-Host "[*] Проверка подключения к AdsPower Local API (порт 50325)..." -ForegroundColor Cyan
    $adspowerOnline = $false
    try {
        $res = Invoke-RestMethod -Uri "http://127.0.0.1:50325/api/v1/user/list?page_size=1" -TimeoutSec 3 -ErrorAction Stop
        if ($res.code -eq 0) { $adspowerOnline = $true }
    } catch {}

    if ($adspowerOnline) {
        Write-Host "[OK] AdsPower Local API обнаружен и доступен!" -ForegroundColor Green
    } else {
        Write-Host "[INFO] AdsPower сейчас закрыт или Local API отключен." -ForegroundColor Yellow
        Write-Host "       Не забудьте запустить AdsPower перед началом проверки билетов." -ForegroundColor Gray
    }
    Write-Host ""

    Write-Host "==============================================================================" -ForegroundColor Green
    Write-Host "       🎉  УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА! ВСЕ КОМПОНЕНТЫ ГОТОВЫ К РАБОТЕ!      " -ForegroundColor Yellow
    Write-Host "==============================================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Для запуска программы используйте:" -ForegroundColor Cyan
    Write-Host "  1. Ярлык на Рабочем столе: «Etix Checker 2026» (чистый запуск без консоли)" -ForegroundColor White
    Write-Host "  2. Файл запуска GUI: run_gui.bat" -ForegroundColor White
    Write-Host "  3. Файл запуска консоли (CLI): run.bat" -ForegroundColor White
    Write-Host ""
    Write-Host "Подробное руководство пользователя: файл ИНСТРУКЦИЯ.md в папке проекта." -ForegroundColor Gray
    Write-Host ""

    $choice = Read-Host "Запустить графический интерфейс прямо сейчас? (Y/N, Enter = Y)"
    if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim().ToUpper() -eq "Y") {
        Start-Process -FilePath "wscript.exe" -ArgumentList "`"$vbsFile`""
    }

} catch {
    Write-Host ""
    Write-Host "==============================================================================" -ForegroundColor Red
    Write-Host "❌ ОШИБКА ПРИ УСТАНОВКЕ:" -ForegroundColor Red
    Write-Host "   $($_)" -ForegroundColor Yellow
    Write-Host "==============================================================================" -ForegroundColor Red
    Write-Host ""
}
