param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$DatabasePath = ".local_paper_console.db",
    [switch]$TradingOnly
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Logs = Join-Path $Root "logs"
$DatabaseFullPath = Join-Path $Root $DatabasePath
$SqliteUrl = "sqlite:///$($DatabaseFullPath.Replace('\', '/'))"
$ApiUrl = "http://127.0.0.1:$ApiPort"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Write-Step($Message) {
    Write-Host "[paper-console] $Message"
}

function Test-HttpOk($Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec 2
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

function Wait-HttpOk($Url, $Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-HttpOk $Url) {
            return $true
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Test-PortOpen($Port) {
    try {
        return [bool](Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    }
    catch {
        return $false
    }
}

function Show-LogTail($Path, $Lines = 40) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Step "log file missing: $Path"
        return
    }
    Write-Host ""
    Write-Host "---- tail $Path ----"
    Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue
    Write-Host "--------------------"
}

function Stop-ExistingProjectProcess($Port, $ExpectedPattern) {
    $connections = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        $ownerPid = $connection.OwningProcess
        if (-not $ownerPid -or $ownerPid -eq $PID) {
            continue
        }
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
        $commandLine = $processInfo.CommandLine
        if ($commandLine -and ($commandLine.Contains($Root) -or $commandLine.Contains($ExpectedPattern))) {
            Write-Step "stopping previous project process on port $Port (pid $ownerPid)"
            Stop-Process -Id $ownerPid -Force
            Start-Sleep -Seconds 1
            continue
        }
        throw "Port $Port is already used by another process. Close it or start with a different port."
    }
}

function Assert-Command($Name, $InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $InstallHint"
    }
}

. (Join-Path $PSScriptRoot "load-dotenv.ps1")

function Ensure-LocalEnvFile($RootPath) {
    $envPath = Join-Path $RootPath ".env"
    $examplePath = Join-Path $RootPath ".env.example"
    if (Test-Path -LiteralPath $envPath) {
        return $envPath
    }
    if (-not (Test-Path -LiteralPath $examplePath)) {
        Write-Step "WARNING: .env missing and .env.example not found; Binance Testnet keys will not load"
        return $null
    }
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    Write-Step "created .env from .env.example — add BINANCE_API_KEY and BINANCE_API_SECRET, then restart"
    return $envPath
}

function Set-GeoFriendlyBinanceEndpointsIfNeeded {
    $blocked = $false
    try {
        $response = Invoke-WebRequest -Uri "https://api.binance.com/api/v3/ping" -TimeoutSec 5 -UseBasicParsing
        if ($response.StatusCode -eq 451) {
            $blocked = $true
        }
    }
    catch {
        $statusCode = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -eq 451) {
            $blocked = $true
        }
    }

    if (-not $blocked) {
        return
    }

    Write-Step "Binance mainnet is geo-restricted here; switching to public data endpoints"
    Write-Step "Tip: set BINANCE_HTTPS_PROXY=http://127.0.0.1:7890 in .env for Testnet API only (no global VPN)"
    $env:BINANCE_SPOT_REST_BASE = "https://data-api.binance.vision"
    $env:BINANCE_USDM_REST_BASE = "https://demo-fapi.binance.com"
    $env:BINANCE_SPOT_WS_BASE = "wss://data-stream.binance.vision/ws"
    $env:BINANCE_USDM_WS_BASE = "wss://stream.binancefuture.com/ws"
}

Set-Location $Root

Write-Step "checking local runtimes"
Assert-Command "py" "Install Python 3.11+ and make the py launcher available."
Assert-Command "npm.cmd" "Install Node.js/npm before starting the frontend."
$envPath = Ensure-LocalEnvFile $Root
if ($envPath) {
    Import-DotEnv $envPath | Out-Null
}
if (-not $env:BINANCE_API_KEY -or -not $env:BINANCE_API_SECRET) {
    Write-Step "WARNING: BINANCE_API_KEY/SECRET not loaded — local Paper works, Testnet mirror and exchange records will not"
}
else {
    Write-Step "Binance Mock Trading credentials loaded from .env"
}
Set-GeoFriendlyBinanceEndpointsIfNeeded

$requiredPythonModules = @(
    "prometheus_client",
    "fastapi",
    "uvicorn",
    "ccxt"
)
$missingPythonModules = @()
foreach ($moduleName in $requiredPythonModules) {
    py -3 -c "import $moduleName" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $missingPythonModules += $moduleName
    }
}

if ($missingPythonModules.Count -gt 0) {
    Write-Step "installing Python project dependencies; missing: $($missingPythonModules -join ', ')"
    py -3 -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency install failed. Run: py -3 -m pip install -e ."
    }
}

Write-Step "initializing local Paper database: $DatabasePath"
$env:POSTGRES_URL = $SqliteUrl
$env:APP_ENV = "development"
$env:BINANCE_USE_TESTNET = "true"
$env:LIVE_TRADING_ENABLED = "false"
$env:RUNTIME_SCHEDULER_MODE = "inprocess"
$env:RUNTIME_SCHEDULER_AUTOSTART = "true"
$env:PAPER_RUNTIME_CYCLE_SECONDS = "60"
$env:BINANCE_AUTO_EXECUTE = "true"
$env:BINANCE_LIVE_UNIVERSE_ENABLED = "true"
$env:BINANCE_LIVE_MARKET_ENABLED = "true"
$env:BINANCE_LIVE_WS_ENABLED = "true"
if (-not $env:BINANCE_LIVE_WS_SYMBOLS) { $env:BINANCE_LIVE_WS_SYMBOLS = "top20" }
if (-not $env:PAPER_RUNTIME_ENABLE_DECISION_VETO) { $env:PAPER_RUNTIME_ENABLE_DECISION_VETO = "true" }
if (-not $env:LLM_USE_CATALOG_SEEDS_ONLY) { $env:LLM_USE_CATALOG_SEEDS_ONLY = "true" }
$binanceSpotRestBase = if ($env:BINANCE_SPOT_REST_BASE) { $env:BINANCE_SPOT_REST_BASE } else { "" }
$binanceUsdmRestBase = if ($env:BINANCE_USDM_REST_BASE) { $env:BINANCE_USDM_REST_BASE } else { "" }
$binanceSpotWsBase = if ($env:BINANCE_SPOT_WS_BASE) { $env:BINANCE_SPOT_WS_BASE } else { "" }
$binanceUsdmWsBase = if ($env:BINANCE_USDM_WS_BASE) { $env:BINANCE_USDM_WS_BASE } else { "" }
py -3 -c "from services.database import create_relational_schema, get_engine, reset_database_caches; from services.data.repository import create_timeseries_schema; reset_database_caches(); create_relational_schema(); create_timeseries_schema(get_engine()); print('schema ready')"

$requiredNodeModules = @(
    "node_modules\@tanstack\react-query",
    "node_modules\lightweight-charts",
    "node_modules\react-router-dom",
    "node_modules\vite",
    "node_modules\vitest"
)
$missingNodeModules = @()
foreach ($modulePath in $requiredNodeModules) {
    if (-not (Test-Path (Join-Path $Root $modulePath))) {
        $missingNodeModules += $modulePath
    }
}

if ((-not (Test-Path (Join-Path $Root "node_modules"))) -or $missingNodeModules.Count -gt 0) {
    if ($missingNodeModules.Count -gt 0) {
        Write-Step "installing frontend workspace dependencies; missing: $($missingNodeModules -join ', ')"
    }
    else {
        Write-Step "installing frontend workspace dependencies"
    }
    npm install
}

Stop-ExistingProjectProcess $ApiPort "apps.api.main:app"
if (-not $TradingOnly) {
    Stop-ExistingProjectProcess $FrontendPort "vite"
}

Write-Step "starting FastAPI on $ApiUrl"
$runApiScript = Join-Path $PSScriptRoot "run-api-local.ps1"
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $runApiScript,
    "-Root", $Root,
    "-PostgresUrl", $SqliteUrl,
    "-Port", $ApiPort,
    "-LogPath", (Join-Path $Logs "api.log")
) -WindowStyle Hidden

if (-not $TradingOnly) {
    Write-Step "starting Vite admin console on $FrontendUrl"
    $frontendCommand = @"
Set-Location '$Root'
npm --workspace frontend/admin run dev -- --host 127.0.0.1 --port $FrontendPort *> '$Logs\frontend.log'
"@
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand) -WindowStyle Hidden
}

Write-Step "waiting for API"
if (-not (Wait-HttpOk "$ApiUrl/health" 120)) {
    Show-LogTail (Join-Path $Logs "api.log")
    throw "FastAPI did not become ready. See logs/api.log"
}

if ($TradingOnly) {
    Write-Host ""
    Write-Host "Paper trading engine is running (no frontend):"
    Write-Host "  API:  $ApiUrl"
    Write-Host "  Logs: $Logs"
    Write-Host ""
    Write-Host "Close this window anytime — scheduler keeps running in the background."
    return
}

Write-Step "waiting for frontend"
if (-not (Wait-HttpOk "$FrontendUrl/" 45)) {
    Show-LogTail (Join-Path $Logs "frontend.log")
    throw "Frontend did not become ready. See logs/frontend.log"
}

Write-Step "opening browser: $FrontendUrl/trading"
Start-Process "$FrontendUrl/trading"

Write-Host ""
Write-Host "Paper console is running:"
Write-Host "  API:      $ApiUrl"
Write-Host "  Frontend: $FrontendUrl"
Write-Host "  Logs:     $Logs"
Write-Host ""
Write-Host "Close this window whenever you like; the services continue in the background."
