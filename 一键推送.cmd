@echo off
setlocal
for /f "delims=" %%R in ('git rev-parse --show-toplevel 2^>nul') do set "REPO_ROOT=%%R"
if not defined REPO_ROOT (
  echo PUBLISH_FAILED: not inside a Git repository
  exit /b 1
)
cd /d "%REPO_ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\git_publish.ps1" %*
exit /b %ERRORLEVEL%
