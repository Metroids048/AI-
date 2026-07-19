@echo off
REM 项目主入口：启动 AI Quant Platform（停止旧进程 -> 启动纸面交易控制台 -> 打开浏览器）
setlocal
cd /d "%~dp0"

echo ========================================
echo   AI Quant Platform - 启动中
echo ========================================
echo.

REM 第一步：停止所有旧的Python进程
echo [1/3] 停止旧进程...
taskkill /F /IM python.exe /T >nul 2>&1
if %ERRORLEVEL%==0 (
  echo       已停止旧的Python进程
) else (
  echo       没有运行中的Python进程
)
timeout /t 2 /nobreak >nul

REM 第二步：启动系统
echo.
echo [2/3] 启动系统（加载最新配置）...
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

REM 第三步：启动成功
echo.
echo ========================================
echo   AI Quant Platform started
echo ========================================
echo.
echo Trading: http://127.0.0.1:5173/trading
echo API:     http://127.0.0.1:8000
echo Logs:    logs\startup-last.log  logs\api.log  logs\frontend.log
echo.
echo [3/3] 启动完成！浏览器将自动打开。
echo.
echo 现在请运行验证脚本检查配置：
echo   python scripts\verify_config.py
echo.
pause
endlocal
