@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-paper-console.ps1"
set EXIT_CODE=%ERRORLEVEL%
echo.
if not %EXIT_CODE%==0 (
  echo [失败] 启动未完成，请看上面错误和 logs\api.log
  pause
  exit /b %EXIT_CODE%
)
echo.
echo ========================================
echo   启动成功
echo ========================================
echo.
echo 交易页: http://127.0.0.1:5173/trading
echo API:    http://127.0.0.1:8000
echo 日志:   logs\api.log  logs\frontend.log
echo.
echo 浏览器应已自动打开；若没有请复制上面交易页链接。
echo 按回车可关本窗口，服务继续在后台运行。
pause
