@echo off
REM 个人辅助脚本：调用 scripts/sync-to-github.mjs 将本地代码同步推送到 GitHub，与平台运行时无关
chcp 65001 >nul
cd /d "%~dp0"
node "%~dp0scripts\sync-to-github.mjs"
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
timeout /t 2 >nul
