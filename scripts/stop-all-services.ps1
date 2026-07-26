# Stop All Services Script
# This script stops all running services (API, Frontend, Scheduler, Workers)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  停止所有服务" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$stopped = 0
$failed = 0

# Stop Node.js processes (Frontend)
Write-Host "[1/4] 停止前端服务 (Node.js)..." -NoNewline
$nodeProcesses = Get-Process -Name "node" -ErrorAction SilentlyContinue
if ($nodeProcesses) {
    try {
        $nodeProcesses | Stop-Process -Force -ErrorAction Stop
        Write-Host " ✓ 已停止 $($nodeProcesses.Count) 个进程" -ForegroundColor Green
        $stopped += $nodeProcesses.Count
    } catch {
        Write-Host " ✗ 停止失败" -ForegroundColor Red
        $failed++
    }
} else {
    Write-Host " - 没有运行的进程" -ForegroundColor Gray
}

# Stop Python processes (API, Scheduler, Workers)
Write-Host "[2/4] 停止后端服务 (Python)..." -NoNewline
$pythonProcesses = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*uvicorn*" -or
    $_.CommandLine -like "*celery*" -or
    $_.CommandLine -like "*scheduler*" -or
    $_.Path -like "*量化项目*"
}
if ($pythonProcesses) {
    try {
        $pythonProcesses | Stop-Process -Force -ErrorAction Stop
        Write-Host " ✓ 已停止 $($pythonProcesses.Count) 个进程" -ForegroundColor Green
        $stopped += $pythonProcesses.Count
    } catch {
        Write-Host " ✗ 停止失败" -ForegroundColor Red
        $failed++
    }
} else {
    Write-Host " - 没有运行的进程" -ForegroundColor Gray
}

# Check for any remaining processes on ports 8000 and 5173
Write-Host "[3/4] 检查端口占用..." -NoNewline
$port8000 = netstat -ano | Select-String ":8000" | Select-String "LISTENING"
$port5173 = netstat -ano | Select-String ":5173" | Select-String "LISTENING"

$portsCleared = $true
if ($port8000) {
    $pid = ($port8000 -split '\s+')[-1]
    try {
        Stop-Process -Id $pid -Force -ErrorAction Stop
        Write-Host " ✓ 已释放端口 8000" -ForegroundColor Green
        $stopped++
    } catch {
        Write-Host " ✗ 无法释放端口 8000" -ForegroundColor Red
        $portsCleared = $false
        $failed++
    }
}
if ($port5173) {
    $pid = ($port5173 -split '\s+')[-1]
    try {
        Stop-Process -Id $pid -Force -ErrorAction Stop
        Write-Host " ✓ 已释放端口 5173" -ForegroundColor Green
        $stopped++
    } catch {
        Write-Host " ✗ 无法释放端口 5173" -ForegroundColor Red
        $portsCleared = $false
        $failed++
    }
}
if (-not $port8000 -and -not $port5173) {
    Write-Host " - 端口已清空" -ForegroundColor Gray
}

# Summary
Write-Host ""
Write-Host "[4/4] 清理完成" -ForegroundColor Cyan
Write-Host "  已停止进程: $stopped" -ForegroundColor Green
if ($failed -gt 0) {
    Write-Host "  失败: $failed" -ForegroundColor Red
}
Write-Host ""

if ($failed -eq 0) {
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  所有服务已成功停止！" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    exit 0
} else {
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  部分服务停止失败，请手动检查" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    exit 1
}
