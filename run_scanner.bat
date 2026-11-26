@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if "%~1"=="--open-browser" (
    call :OPEN_APP "http://127.0.0.1:8000/?v=3"
    exit /b 0
)

title Binance Crypto Intelligence Scanner

echo ==============================================================
echo   BINANCE CRYPTO INTELLIGENCE SCANNER - ONE-CLICK LAUNCHER
echo ==============================================================

:: 1. Locate Python (supports 'python' or Windows 'py' launcher)
set "PY_CMD="
where python >nul 2>nul && (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    where py >nul 2>nul && (
        py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul && set "PY_CMD=py -3"
    )
)

:: If Python is not installed at all, fall back immediately to the zero-install Standalone HTML webpage!
if not defined PY_CMD (
    echo.
    echo [INFO] Python 3.10+ was not found on this PC.
    echo [INFO] Opening Standalone Webpage Edition right now in your browser...
    call :OPEN_APP "%~dp0Binance_Scanner_Standalone.html"
    exit /b 0
)

:: 2. Create local virtual environment & install Python packages on first run only
if not exist ".venv\Scripts\python.exe" (
    echo [1/2] First-time setup: creating local Python environment ^(.venv^)...
    %PY_CMD% -m venv .venv
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, pandas, numpy, aiohttp, aiosqlite" >nul 2>nul
if errorlevel 1 (
    echo [2/2] Installing scanner packages ^(one-time setup, ~30 seconds^)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist "data" mkdir data

:: Free port 8000 if a previous scanner instance was left running in the background
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    if not "%%p"=="0" taskkill /F /PID %%p >nul 2>nul
)

:: 3. Launch browser window automatically once port 8000 is ready
set "APP_URL=http://127.0.0.1:8000"
start "" /b cmd /c "ping 127.0.0.1 -n 3 >nul & call "%~f0" --open-browser"

echo.
echo [LIVE] Starting Full Python SMC + Backtesting Scanner at %APP_URL%
echo        Keep this window minimized while using the scanner. Close it to stop.
echo.

set "PYTHONPATH=%~dp0backend"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :EOF

:OPEN_APP
set "TARGET=%~1"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --app="%TARGET%"
    goto :EOF
)
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" --app="%TARGET%"
    goto :EOF
)
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
    start "" "%LocalAppData%\Google\Chrome\Application\chrome.exe" --app="%TARGET%"
    goto :EOF
)
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --app="%TARGET%"
    goto :EOF
)
start "" "%TARGET%"
goto :EOF
