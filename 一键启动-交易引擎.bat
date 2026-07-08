@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-paper-engine.ps1"
if errorlevel 1 (
  echo.
  echo Trading engine startup failed. Check logs\api.log
  pause
)
endlocal
