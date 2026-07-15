# 多设备环境一致性保障方案

## 问题根源

启动脚本依赖 `C:\Users\win\.ai-workspace\venv\` 虚拟环境，但：
1. 不同设备可能没有这个虚拟环境
2. 虚拟环境可能缺少项目依赖
3. 没有自动检测和修复机制

---

## 解决方案：启动前自动检查和修复

### 1. 创建环境验证脚本

**文件**: `scripts/ensure-venv-ready.ps1`

```powershell
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
return $PythonExe
```

---

### 2. 修改启动脚本自动调用验证

**修改 `scripts/launch-paper-console.ps1` 的 Line 175-183**：

```powershell
# 旧代码（容易出错）：
if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
    $globalPython = Join-Path $HOME ".ai-workspace\venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $globalPython) {
        $env:AGENT_PYTHON = $globalPython
    }
    else {
        throw "AGENT_PYTHON is unavailable. Run verify-global-agent-stack.ps1."
    }
}

# 新代码（自动修复）：
if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
    Write-Step "checking Python environment"
    $ensureScript = Join-Path $PSScriptRoot "ensure-venv-ready.ps1"
    $env:AGENT_PYTHON = & $ensureScript
    if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
        throw "Failed to prepare Python environment"
    }
    Write-Step "✓ Python environment ready: $env:AGENT_PYTHON"
}
```

---

### 3. 快速验证（一次性运行）

在当前设备上立即验证：

```powershell
cd C:\Users\win\Desktop\AI--main
.\scripts\ensure-venv-ready.ps1
```

如果成功，会输出：
```
[venv-check] ✓ 项目依赖已就绪
[venv-check] ✓ 虚拟环境就绪: C:\Users\win\.ai-workspace\venv\Scripts\python.exe
```

---

## 为什么这个方案能解决多设备问题？

### 当前问题
- ❌ 手动在每个设备上设置虚拟环境
- ❌ 手动安装依赖
- ❌ 依赖缺失时启动失败，无提示

### 方案优势
- ✅ **自动检测**：启动时自动检查环境
- ✅ **自动创建**：虚拟环境不存在时自动创建
- ✅ **自动修复**：依赖缺失时自动安装
- ✅ **跨设备一致**：每个设备首次启动时自动配置
- ✅ **零手动操作**：点击启动按钮就够了

---

## 新设备使用流程

**以前（容易出错）**：
1. 克隆代码
2. 手动创建虚拟环境
3. 手动安装依赖
4. 配置环境变量
5. 启动

**现在（一键完成）**：
1. 克隆代码
2. 点击启动按钮 ✅

首次启动时会自动：
- 创建虚拟环境
- 安装所有依赖
- 验证关键模块
- 然后正常启动

---

## 验证清单

- [ ] 运行 `.\scripts\ensure-venv-ready.ps1` 确认脚本可用
- [ ] 修改 `launch-paper-console.ps1` 集成自动检查
- [ ] 重新启动验证
- [ ] （可选）在另一台设备上验证零配置启动

---

## 紧急修复（当前设备）

你的当前设备已经手动修复完成：
- ✅ alembic 已安装
- ✅ 项目依赖 (`pip install -e .`) 已安装

**现在可以直接重新点击启动按钮，应该能成功启动了。**

如果还有问题，运行：
```powershell
cd C:\Users\win\Desktop\AI--main
& "C:\Users\win\.ai-workspace\venv\Scripts\python.exe" scripts\prepare_database.py --database-url "sqlite:///C:/Users/win/Desktop/AI--main/.local_paper_console.db"
```

查看具体的数据库准备错误。
