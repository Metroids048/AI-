@echo off
REM 项目主入口：启动 AI Quant Platform（清理旧控制台 -> 启动纸面交易控制台 -> 打开浏览器）
REM UTF-8：必须先切代码页，再用 UTF-8 输出中文（与 scripts\上传.cmd 同模式）
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo   AI Quant Platform - 启动中
echo ========================================
echo.

REM 旧 API/前端由 scripts\launch-paper-console.ps1 按 pid 文件与端口精准停止
REM 禁止 taskkill /IM python.exe：会误杀 Cursor/Agent，且在多 Python 进程时易卡住
echo [1/3] 检查环境...

REM 第二步：启动系统（已在运行时会走快速路径，几秒内打开浏览器）
echo.
echo [2/3] 启动系统（若需准备数据库，可能约 1 分钟，请勿关闭窗口）...
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
echo API:     http://127.0.0.1:8016
echo Logs:    logs\startup-last.log  logs\api.log  logs\frontend.log
echo.
echo [3/3] 启动完成！浏览器将自动打开。
echo.
echo 现在请运行验证脚本检查配置：
echo   python scripts\verify_config.py
echo.
pause
endlocal
