@echo off
setlocal
cd /d "%~dp0"
set "PWSH=C:\Program Files\PowerShell\7\pwsh.exe"
if exist "%PWSH%" (
  "%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-paper-console.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-paper-console.ps1"
)
set EXIT_CODE=%ERRORLEVEL%
echo.
if not %EXIT_CODE%==0 (
  echo [ERROR] Startup failed. Check the error above and logs\startup-last.log.
  pause
  exit /b %EXIT_CODE%
)
echo.
echo ========================================
echo   AI Quant Platform started
echo ========================================
echo.
echo Trading: http://127.0.0.1:5173/trading
echo API:     http://127.0.0.1:8000
echo Logs:    logs\startup-last.log  logs\api.log  logs\frontend.log
echo.
echo The browser should open automatically.
echo Press any key to close this window. Services remain in the background.
pause
endlocal
