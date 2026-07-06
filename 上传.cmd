@echo off
chcp 65001 >nul
cd /d "%~dp0"
node "%~dp0scripts\sync-to-github.mjs"
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
timeout /t 2 >nul
