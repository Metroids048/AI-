@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo AI Quant Platform - starting
echo ========================================
echo.
echo [1/3] Checking environment...
echo.
echo [2/3] Starting system. Database preparation may take about one minute.

set "PWSH=C:\Program Files\PowerShell\7\pwsh.exe"
if exist "%PWSH%" (
  "%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-paper-console.ps1" -AutomatedTradingEngine v2_active -EnableNaturalTestnet -PreserveExternalTestnetBaseline
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-paper-console.ps1" -AutomatedTradingEngine v2_active -EnableNaturalTestnet -PreserveExternalTestnetBaseline
)
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] Startup failed. Check logs\startup-last.log.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo ========================================
echo AI Quant Platform started
echo ========================================
echo Trading: http://127.0.0.1:5173/trading
echo API:     http://127.0.0.1:8016
echo Logs:    logs\startup-last.log logs\api.log logs\frontend.log
echo Research: logs\research-runtime-state.json logs\research-worker.log
echo.
echo [3/3] Startup completed. Browser will open automatically.
pause
endlocal
