# Frontend Verification Script
# This script checks if the frontend is working correctly after fixes

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  前端功能验证脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$frontendUrl = "http://localhost:5173"
$apiUrl = "http://localhost:8000/api/v1/execution/binance-testnet-account"

# Step 1: Check if frontend is accessible
Write-Host "[1/3] 检查前端服务是否可访问..." -NoNewline
try {
    $response = Invoke-WebRequest -Uri $frontendUrl -Method GET -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host " ✓ 前端服务运行正常" -ForegroundColor Green
    } else {
        Write-Host " ✗ 前端服务返回状态码: $($response.StatusCode)" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host " ✗ 无法访问前端服务" -ForegroundColor Red
    Write-Host "  错误: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 2: Check if API is accessible
Write-Host "[2/3] 检查API服务是否可访问..." -NoNewline
try {
    $response = Invoke-WebRequest -Uri $apiUrl -Method GET -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host " ✓ API服务运行正常" -ForegroundColor Green
    } else {
        Write-Host " ✗ API服务返回状态码: $($response.StatusCode)" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host " ✗ 无法访问API服务" -ForegroundColor Red
    Write-Host "  错误: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 3: Instructions for manual verification
Write-Host "[3/3] 手动验证清单（请在浏览器中完成）：" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. 打开浏览器，访问: $frontendUrl" -ForegroundColor White
Write-Host "  2. 打开开发者工具（F12）" -ForegroundColor White
Write-Host "  3. 切换到 'Network' 标签页" -ForegroundColor White
Write-Host "  4. 刷新页面，观察API请求频率" -ForegroundColor White
Write-Host ""
Write-Host "  ✓ 正常：每8-30秒一次请求" -ForegroundColor Green
Write-Host "  ✗ 异常：毫秒级疯狂请求" -ForegroundColor Red
Write-Host ""
Write-Host "  5. 切换到 'Console' 标签页，检查是否有错误" -ForegroundColor White
Write-Host "  6. 点击 '币安账户' 标签页，检查是否显示内容" -ForegroundColor White
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  验证完成后，请向AI报告结果" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
