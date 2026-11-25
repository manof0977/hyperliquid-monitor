@echo off
cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$desk = [Environment]::GetFolderPath('Desktop'); " ^
  "$sc = $ws.CreateShortcut((Join-Path $desk 'Binance Intelligence Scanner.lnk')); " ^
  "$sc.TargetPath = (Join-Path '%SCRIPT_DIR%' 'run_scanner.bat'); " ^
  "$sc.WorkingDirectory = '%SCRIPT_DIR%'; " ^
  "$sc.Description = 'Launch Binance Crypto Intelligence Scanner'; " ^
  "$sc.Save();"
echo.
echo [OK] Shortcut "Binance Intelligence Scanner" created on your Desktop!
pause
