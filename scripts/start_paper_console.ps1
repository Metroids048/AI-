param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$DatabasePath = ".local_paper_console.db"
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

Set-Location $Root

Write-Step "checking local runtimes"
Assert-Command "py" "Install Python 3.11+ and make the py launcher available."
Assert-Command "npm.cmd" "Install Node.js/npm before starting the frontend."

Write-Step "initializing local Paper database: $DatabasePath"
$env:POSTGRES_URL = $SqliteUrl
$env:APP_ENV = "development"
$env:BINANCE_USE_TESTNET = "true"
$env:LIVE_TRADING_ENABLED = "false"
$env:RUNTIME_SCHEDULER_MODE = "inprocess"
$env:RUNTIME_SCHEDULER_AUTOSTART = "true"
$env:PAPER_RUNTIME_CYCLE_SECONDS = "60"
$env:BINANCE_LIVE_UNIVERSE_ENABLED = "true"
$env:BINANCE_LIVE_MARKET_ENABLED = "true"
$env:BINANCE_LIVE_WS_ENABLED = "true"
py -3 -c "from services.database import create_relational_schema, get_engine, reset_database_caches; from services.data.repository import create_timeseries_schema; reset_database_caches(); create_relational_schema(); create_timeseries_schema(get_engine()); print('schema ready')"

if (-not (Test-Path (Join-Path $Root "node_modules"))) {
    Write-Step "installing frontend workspace dependencies"
    npm install
}

Stop-ExistingProjectProcess $ApiPort "apps.api.main:app"
Stop-ExistingProjectProcess $FrontendPort "vite"

Write-Step "starting FastAPI on $ApiUrl"
$apiCommand = @"
`$env:POSTGRES_URL = '$SqliteUrl'
`$env:APP_ENV = 'development'
`$env:BINANCE_USE_TESTNET = 'true'
`$env:LIVE_TRADING_ENABLED = 'false'
`$env:RUNTIME_SCHEDULER_MODE = 'inprocess'
`$env:RUNTIME_SCHEDULER_AUTOSTART = 'true'
`$env:PAPER_RUNTIME_CYCLE_SECONDS = '60'
`$env:BINANCE_LIVE_UNIVERSE_ENABLED = 'true'
`$env:BINANCE_LIVE_MARKET_ENABLED = 'true'
`$env:BINANCE_LIVE_WS_ENABLED = 'true'
Set-Location '$Root'
py -3 -m uvicorn apps.api.main:app --host 127.0.0.1 --port $ApiPort *> '$Logs\api.log'
"@
Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $apiCommand) -WindowStyle Hidden

Write-Step "starting Vite admin console on $FrontendUrl"
$frontendCommand = @"
Set-Location '$Root'
npm --workspace frontend/admin run dev -- --host 127.0.0.1 --port $FrontendPort *> '$Logs\frontend.log'
"@
Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand) -WindowStyle Hidden

Write-Step "waiting for API"
if (-not (Wait-HttpOk "$ApiUrl/health" 30)) {
    throw "FastAPI did not become ready. See logs/api.log"
}

Write-Step "waiting for frontend"
if (-not (Wait-HttpOk "$FrontendUrl/" 45)) {
    throw "Frontend did not become ready. See logs/frontend.log"
}

Write-Step "opening browser: $FrontendUrl"
Start-Process $FrontendUrl

Write-Host ""
Write-Host "Paper console is running:"
Write-Host "  API:      $ApiUrl"
Write-Host "  Frontend: $FrontendUrl"
Write-Host "  Logs:     $Logs"
Write-Host ""
Write-Host "Close this window whenever you like; the services continue in the background."
