# ==============================================================================
#  ETIX CHECKER 2026 -- ADSPOWER CDP EDITION (ONE-CLICK INSTALLER)
# ==============================================================================

$Host.UI.RawUI.WindowTitle = "Etix Checker 2026 -- One-Click Installer"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "       ETIX CHECKER 2026 -- ADSPOWER CDP EDITION (ONE-CLICK INSTALLER)        " -ForegroundColor Yellow
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""

try {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
    Set-Location $scriptDir

    # 1. Target working directory
    $isRepoFolder = (Test-Path (Join-Path $scriptDir "gui_app.py")) -or (Test-Path (Join-Path $scriptDir "requirements.txt"))

    if (-not $isRepoFolder) {
        $targetDir = Join-Path $scriptDir "Etix Checker 2026"
        if (-not (Test-Path $targetDir)) {
            Write-Host "[*] Creating working directory: $targetDir" -ForegroundColor Cyan
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }
        Set-Location $targetDir
        $scriptDir = $targetDir

        Write-Host "[*] Downloading repository files from GitHub..." -ForegroundColor Cyan
        $gitExists = Get-Command "git" -ErrorAction SilentlyContinue

        if ($gitExists) {
            Write-Host "[*] Cloning repository via Git..." -ForegroundColor Gray
            & git clone https://github.com/KrosinGG/EtixChecker.git .
        } else {
            Write-Host "[*] Git not found. Downloading project archive..." -ForegroundColor Gray
            $zipPath = Join-Path $scriptDir "repo.zip"
            $tempExtract = Join-Path $scriptDir "temp_repo"
            
            $wc = New-Object System.Net.WebClient
            $downloadUrl = "https://github.com/KrosinGG/EtixChecker/archive/refs/heads/main.zip"
            try {
                $wc.DownloadFile($downloadUrl, $zipPath)
            } catch {
                $downloadUrl = "https://github.com/KrosinGG/EtixChecker/archive/refs/heads/master.zip"
                $wc.DownloadFile($downloadUrl, $zipPath)
            }
            
            Expand-Archive -Path $zipPath -DestinationPath $tempExtract -Force
            $extractedSub = Get-ChildItem -Path $tempExtract | Select-Object -First 1
            if ($extractedSub) {
                Copy-Item -Path "$($extractedSub.FullName)\*" -Destination $scriptDir -Recurse -Force
            }
            Remove-Item -Path $zipPath, $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "[+] Working Directory: $scriptDir" -ForegroundColor Green
    Write-Host ""

    # 2. Check and auto-install Python 3.10+
    Write-Host "[*] Checking Python 3.10+ installation..." -ForegroundColor Cyan
    $pythonExe = $null

    function Test-PythonExe($cmd) {
        try {
            $p = Start-Process -FilePath $cmd -ArgumentList '-c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"' -Wait -PassThru -NoNewWindow
            if ($p.ExitCode -eq 0) { return $true }
        } catch {}
        return $false
    }

    if (Test-PythonExe "python") {
        $pythonExe = "python"
    } elseif (Test-PythonExe "py") {
        $pythonExe = "py"
    }

    if (-not $pythonExe) {
        Write-Host ""
        Write-Host "[!] Python 3.10+ was not detected on this system." -ForegroundColor Yellow
        Write-Host "[*] Downloading official Python 3.11 64-bit installer..." -ForegroundColor Cyan
        
        $pyInstallerPath = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
        $pyUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($pyUrl, $pyInstallerPath)
        
        if (Test-Path $pyInstallerPath) {
            Write-Host "[*] Installing Python 3.11 (silent mode, adding to PATH)..." -ForegroundColor Cyan
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
            throw "Failed to download Python installer. Please install Python 3.10+ manually from https://www.python.org/"
        }
    }

    Write-Host "[+] Using Python: $pythonExe" -ForegroundColor Green
    Write-Host ""

    # 3. Create isolated virtual environment
    $venvDir = Join-Path $scriptDir "venv"
    $venvPy = Join-Path $venvDir "Scripts\python.exe"
    $venvPip = Join-Path $venvDir "Scripts\pip.exe"

    if (-not (Test-Path $venvPy)) {
        Write-Host "[*] Creating virtual environment (venv)..." -ForegroundColor Cyan
        & $pythonExe -m venv "$venvDir"
        if ($LASTEXITCODE -ne 0 -or (-not (Test-Path $venvPy))) {
            throw "Failed to create virtual environment (venv)."
        }
    }
    Write-Host "[+] Virtual environment (venv) ready." -ForegroundColor Green
    Write-Host ""

    # 4. Install dependencies and Playwright Chromium
    Write-Host "[*] Upgrading pip and installing dependencies..." -ForegroundColor Cyan
    & $venvPy -m pip install --upgrade pip -q 2>$null

    $reqFile = Join-Path $scriptDir "requirements.txt"
    if (Test-Path $reqFile) {
        & $venvPip install -r "$reqFile" -q
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[!] Retrying pip install..." -ForegroundColor Yellow
            & $venvPip install -r "$reqFile"
        }
    }
    Write-Host "[+] All dependencies installed successfully." -ForegroundColor Green

    Write-Host "[*] Installing Playwright Chromium browser..." -ForegroundColor Cyan
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $scriptDir "ms-playwright"
    if (-not (Test-Path $env:PLAYWRIGHT_BROWSERS_PATH)) {
        New-Item -ItemType Directory -Path $env:PLAYWRIGHT_BROWSERS_PATH -Force | Out-Null
    }
    & "$venvDir\Scripts\playwright.exe" install chromium
    Write-Host "[+] Playwright Chromium ready." -ForegroundColor Green
    Write-Host ""

    # 5. Configuration & Folders
    Write-Host "[*] Verifying directory structure and config files..." -ForegroundColor Cyan
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
        Write-Host "[+] Configuration file .env created." -ForegroundColor Green
    }

    $showsFile = Join-Path $scriptDir "data\shows.csv"
    if (-not (Test-Path $showsFile)) {
        $showsText = "name,url,target_total,max_per_order,ticket_index`nNateSmith,https://www.etix.com/ticket/p/35196855/nate-smith-palmer-alaska-state-fair,12,4,2`nGasolina Party,https://www.etix.com/ticket/p/87677793/gasolina-party-providence-the-strand-theatre?partner_id=100,24,6,2"
        $showsText | Out-File -FilePath $showsFile -Encoding utf8
        Write-Host "[+] Sample shows file data\shows.csv created." -ForegroundColor Green
    }

    $vbsFile = Join-Path $scriptDir "run_gui.vbs"
    if (-not (Test-Path $vbsFile)) {
        $vbsText = "Set shell = CreateObject(`"WScript.Shell`")`nSet fso = CreateObject(`"Scripting.FileSystemObject`")`nscriptDir = fso.GetParentFolderName(WScript.ScriptFullName)`ncmd = `"cmd /c `"`"`" & scriptDir & `"un_gui.bat`"`"`" --no-pause`"`nshell.Run cmd, 0, False"
        $vbsText | Out-File -FilePath $vbsFile -Encoding ascii
    }

    # 6. Desktop Shortcut
    Write-Host "[*] Creating Desktop shortcut..." -ForegroundColor Cyan
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
    Write-Host "[+] Desktop shortcut 'Etix Checker 2026' created successfully!" -ForegroundColor Green
    Write-Host ""

    # 7. AdsPower Local API Check
    Write-Host "[*] Testing connection to AdsPower Local API (port 50325)..." -ForegroundColor Cyan
    $adspowerOnline = $false
    try {
        $res = Invoke-RestMethod -Uri "http://127.0.0.1:50325/api/v1/user/list?page_size=1" -TimeoutSec 3 -ErrorAction Stop
        if ($res.code -eq 0) { $adspowerOnline = $true }
    } catch {}

    if ($adspowerOnline) {
        Write-Host "[OK] AdsPower Local API is ONLINE and accessible!" -ForegroundColor Green
    } else {
        Write-Host "[INFO] AdsPower is currently closed or Local API is off." -ForegroundColor Yellow
        Write-Host "       Please make sure AdsPower is running before checking tickets." -ForegroundColor Gray
    }
    Write-Host ""

    Write-Host "==============================================================================" -ForegroundColor Green
    Write-Host "       INSTALLATION COMPLETED SUCCESSFULLY! ALL COMPONENTS READY!             " -ForegroundColor Yellow
    Write-Host "==============================================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Launch methods:" -ForegroundColor Cyan
    Write-Host "  1. Desktop shortcut: 'Etix Checker 2026' (clean windowless launch)" -ForegroundColor White
    Write-Host "  2. GUI Launcher: run_gui.bat (or run_gui.vbs)" -ForegroundColor White
    Write-Host "  3. CLI Launcher: run.bat" -ForegroundColor White
    Write-Host ""
    Write-Host "User Manual: Please refer to file ИНСТРУКЦИЯ.md in project directory." -ForegroundColor Gray
    Write-Host ""

    $choice = Read-Host "Launch GUI application now? (Y/N, Enter = Y)"
    if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim().ToUpper() -eq "Y") {
        Start-Process -FilePath "wscript.exe" -ArgumentList "`"$vbsFile`""
    }

} catch {
    Write-Host ""
    Write-Host "==============================================================================" -ForegroundColor Red
    Write-Host "INSTALLATION ERROR:" -ForegroundColor Red
    Write-Host "   $($_)" -ForegroundColor Yellow
    Write-Host "==============================================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Press Enter to exit..." -ForegroundColor Gray
    Read-Host | Out-Null
    exit 1
}

Write-Host "Press Enter to finish..." -ForegroundColor Gray
Read-Host | Out-Null
exit 0
