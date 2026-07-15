# 确保虚拟环境就绪并安装了所有依赖
param(
    [string]$VenvPath = "$env:USERPROFILE\.ai-workspace\venv"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Status([string]$Message) {
    Write-Host "[venv-check] $Message" -ForegroundColor Cyan
}

# 1. 检查虚拟环境是否存在
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    Write-Status "虚拟环境不存在，正在创建: $VenvPath"
    python -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        throw "创建虚拟环境失败"
    }
}

# 2. 检查项目是否已安装
Write-Status "检查项目依赖..."
$installed = & $PythonExe -c "import sys; import importlib.util; sys.exit(0 if importlib.util.find_spec('services') else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Status "项目依赖缺失，正在安装..."
    Set-Location -LiteralPath $Root
    & $PythonExe -m pip install -e . --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "安装项目依赖失败"
    }
    Write-Status "✓ 项目依赖安装完成"
} else {
    Write-Status "✓ 项目依赖已就绪"
}

# 3. 检查关键模块
$criticalModules = @("alembic", "fastapi", "sqlalchemy", "ccxt")
$missing = @()
foreach ($module in $criticalModules) {
    $check = & $PythonExe -c "import $module" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $missing += $module
    }
}

if ($missing.Count -gt 0) {
    Write-Status "发现缺失模块: $($missing -join ', ')，重新安装项目..."
    Set-Location -LiteralPath $Root
    & $PythonExe -m pip install -e . --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "重新安装依赖失败"
    }
}

Write-Status "✓ 虚拟环境就绪: $PythonExe"
Write-Output $PythonExe
