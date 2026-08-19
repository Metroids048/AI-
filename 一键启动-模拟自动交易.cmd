@echo off
setlocal
cd /d "%~dp0"

set "PWSH=C:\Program Files\PowerShell\7\pwsh.exe"
if exist "%PWSH%" (
  "%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-paper-console.ps1" -AutomatedTradingEngine v2_active -EnableNaturalTestnet -PreserveExternalTestnetBaseline
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-paper-console.ps1" -AutomatedTradingEngine v2_active -EnableNaturalTestnet -PreserveExternalTestnetBaseline
)
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] Natural Testnet startup failed. Check logs\startup-last.log.
  pause
  exit /b %EXIT_CODE%
)
echo.
echo Natural Testnet sampling mode started.
echo Trading: http://127.0.0.1:5173/trading
pause
endlocal
