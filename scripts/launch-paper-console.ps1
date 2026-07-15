param(
    [int]$ApiPort = 8016,
    [int]$FrontendPort = 5173,
    [string]$DatabasePath = ".local_paper_console.db"
)

# One-click launcher - same pattern as 辅助面试/scripts/launch-experience.ps1
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

$ApiHealthUrl = "http://127.0.0.1:$ApiPort/health"
$FrontendUrl = "http://127.0.0.1:$FrontendPort/trading"
$LogsDir = Join-Path $Root "logs"
$ApiLog = Join-Path $LogsDir "api.log"
$FrontendLog = Join-Path $LogsDir "frontend.log"
$StartupLog = Join-Path $LogsDir "startup-last.log"
$ApiPidFile = Join-Path $LogsDir "api.pid"
$SchedulerPidFile = Join-Path $LogsDir "scheduler.pid"
$SchedulerStateFile = Join-Path $LogsDir "scheduler-state.json"
$SchedulerLog = Join-Path $LogsDir "scheduler.log"
$SchedulerErrorLog = Join-Path $LogsDir "scheduler-error.log"
$FrontendPidFile = Join-Path $LogsDir "frontend.pid"
$DbPath = Join-Path $Root $DatabasePath
$SqliteUrl = "sqlite:///$($DbPath.Replace('\', '/'))"

$env:NO_PROXY = "127.0.0.1,localhost"
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""

function Test-EndpointReady([string]$Url) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -Proxy $null
        return $resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Write-Step([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [paper-console] $Message"
    Write-Host $line
    Add-Content -LiteralPath $StartupLog -Value $line -Encoding utf8
}

function Reset-LogFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType File -Path $Path | Out-Null
        return
    }
    try {
        [System.IO.File]::WriteAllText($Path, "")
    }
    catch {
        Write-Host "Log file is in use, continuing without reset: $Path"
    }
}

function Stop-RecordedProcess([string]$PidFile, [int]$Port) {
    if (Test-Path -LiteralPath $PidFile) {
        $recordedPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
        if ($recordedPid -match '^\d+$') {
            $processInfo = Get-Process -Id ([int]$recordedPid) -ErrorAction SilentlyContinue
            if ($processInfo) {
                Write-Step "stopping prior launcher process on port $Port (pid $recordedPid)"
                Stop-ProcessTree -RootPid ([int]$recordedPid)
                Start-Sleep -Milliseconds 500
            }
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }

    $listeners = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        $owner = $listeners | Select-Object -First 1 -ExpandProperty OwningProcess
        $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue
        $commandLine = [string]$ownerInfo.CommandLine
        if ($commandLine -match "apps\.api\.(main|local_server)" -or $commandLine -match "vite\.js") {
            Write-Step "removing legacy project listener on port $Port (pid $owner)"
            Stop-ProcessTree -RootPid ([int]$owner)
            Start-Sleep -Milliseconds 500
        }
        else {
            throw "Port $Port is already in use by pid $owner. The launcher will not stop an unrecorded process."
        }
    }
}

function Stop-ProcessTree([int]$RootPid) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootPid" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -RootPid $child.ProcessId
    }
    Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Save-ListenerPid([int]$Port, [string]$PidFile) {
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) {
        throw "No listener was found on port $Port after startup."
    }
    Set-Content -LiteralPath $PidFile -Value $listener.OwningProcess -Encoding ascii
}

function Test-ProjectListener([int]$Port) {
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) { return $false }
    $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    $commandLine = [string]$ownerInfo.CommandLine
    if ($commandLine -match "apps\.api\.(main|local_server)") {
        return $commandLine -match "--local-console"
    }
    return $commandLine -match "量化项目.*vite\.js"
}

function Stop-RecordedScheduler {
    if (-not (Test-Path -LiteralPath $SchedulerPidFile)) { return }
    $recordedPid = (Get-Content -LiteralPath $SchedulerPidFile -Raw).Trim()
    if ($recordedPid -match '^\d+$') {
        $ownerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $recordedPid" -ErrorAction SilentlyContinue
        if ([string]$ownerInfo.CommandLine -match "run-local-paper-scheduler\.py") {
            Write-Step "stopping prior local scheduler (pid $recordedPid)"
            Stop-ProcessTree -RootPid ([int]$recordedPid)
        }
    }
    Remove-Item -LiteralPath $SchedulerPidFile -Force -ErrorAction SilentlyContinue
}

function Test-SchedulerHealthy {
    if (-not (Test-Path -LiteralPath $SchedulerPidFile) -or -not (Test-Path -LiteralPath $SchedulerStateFile)) {
        return $false
    }
    $schedulerPid = (Get-Content -LiteralPath $SchedulerPidFile -Raw).Trim()
    if ($schedulerPid -notmatch '^\d+$' -or -not (Get-Process -Id ([int]$schedulerPid) -ErrorAction SilentlyContinue)) {
        return $false
    }
    try {
        $state = Get-Content -LiteralPath $SchedulerStateFile -Raw | ConvertFrom-Json
        if (-not $state.running -or -not $state.heartbeat_at) { return $false }
        $heartbeat = [datetimeoffset]::Parse($state.heartbeat_at)
        return ((([datetimeoffset]::UtcNow - $heartbeat).TotalSeconds) -le 120)
    }
    catch {
        return $false
    }
}

function Open-Frontend([string]$Url) {
    foreach ($browser in @("msedge.exe", "chrome.exe")) {
        $command = Get-Command $browser -ErrorAction SilentlyContinue
        if ($command) {
            Start-Process -FilePath $command.Source -ArgumentList $Url | Out-Null
            return $true
        }
    }
    Start-Process $Url | Out-Null
    return $true
}

function Ensure-Runtime {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "Node.js/npm not found."
    }
    if (-not (Test-Path -LiteralPath $LogsDir)) {
        New-Item -ItemType Directory -Path $LogsDir | Out-Null
    }
    Reset-LogFile $StartupLog
    Reset-LogFile $ApiLog
    Reset-LogFile $FrontendLog

    if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
        Write-Step "checking Python environment"
        $ensureScript = Join-Path $PSScriptRoot "ensure-venv-ready.ps1"
        $env:AGENT_PYTHON = & $ensureScript
        if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
            throw "Failed to prepare Python environment"
        }
        Write-Step "✓ Python environment ready: $env:AGENT_PYTHON"
    }

    Write-Step "checking frontend dependency versions"
    npm ls --workspace frontend/admin --depth=0 *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "frontend dependencies are missing or stale; running npm install"
        npm install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed. See npm output above."
        }
    }
    . (Join-Path $PSScriptRoot "load-dotenv.ps1")
    $envPath = Join-Path $Root ".env"
    if (Test-Path -LiteralPath $envPath) {
        Import-DotEnv $envPath | Out-Null
    }
    if (-not $env:BINANCE_HTTPS_PROXY -and $env:HTTPS_PROXY) {
        $env:BINANCE_HTTPS_PROXY = $env:HTTPS_PROXY
    }
    if (-not $env:BINANCE_HTTPS_PROXY) {
        $env:PAPER_CONSOLE_DISABLE_LIVE_WS = "true"
        $env:PAPER_CONSOLE_SKIP_BACKGROUND_BOOTSTRAP = "true"
        Write-Step "no Binance proxy configured; disabling live WebSocket collectors for startup stability"
    }
    else {
        $env:PAPER_CONSOLE_DISABLE_LIVE_WS = "false"
        $env:PAPER_CONSOLE_SKIP_BACKGROUND_BOOTSTRAP = "false"
    }
    $env:POSTGRES_URL = $SqliteUrl
    $env:VITE_API_BASE_URL = "http://127.0.0.1:$ApiPort"
    $env:CORS_ALLOWED_ORIGINS = "http://127.0.0.1:$FrontendPort,http://localhost:$FrontendPort"
    $env:APP_ENV = "development"
    $env:BINANCE_USE_TESTNET = "true"
    $env:LIVE_TRADING_ENABLED = "false"
    $env:RUNTIME_SCHEDULER_MODE = "inprocess"
    $env:RUNTIME_SCHEDULER_AUTOSTART = "true"
    $env:BINANCE_LIVE_UNIVERSE_ENABLED = "true"
    $env:BINANCE_LIVE_MARKET_ENABLED = "true"
    $env:BINANCE_LIVE_WS_ENABLED = if ($env:PAPER_CONSOLE_DISABLE_LIVE_WS -eq "true") { "false" } else { "true" }
    # The isolated scheduler owns background work; foreground market endpoints
    # must still be allowed to read Binance REST data for the trading console.
    $env:PAPER_CONSOLE_API_ONLY = "true"
    Remove-Item Env:VITE_LOCAL_CONSOLE_API_ONLY -ErrorAction SilentlyContinue
    Write-Step "starting isolated Paper scheduler; Testnet mirror remains cost-gated"
}

function Initialize-LocalDatabase {
    Write-Step "preparing local database"
    & $env:AGENT_PYTHON scripts/prepare_database.py --database-url $SqliteUrl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Local database preparation failed; API will not start against an incomplete schema." }
}

$apiReady = Test-EndpointReady $ApiHealthUrl
$frontendReady = Test-EndpointReady "http://127.0.0.1:$FrontendPort/"

if ($apiReady -and $frontendReady -and (Test-ProjectListener $ApiPort) -and (Test-ProjectListener $FrontendPort)) {
    if (-not (Test-SchedulerHealthy)) {
        Ensure-Runtime
        Stop-RecordedScheduler
        Reset-LogFile $SchedulerLog
        Reset-LogFile $SchedulerErrorLog
        $schedulerScript = Join-Path $PSScriptRoot "run-local-paper-scheduler.py"
        $schedulerProcess = Start-Process -FilePath $env:AGENT_PYTHON `
            -ArgumentList @($schedulerScript, "--database-url", $SqliteUrl) `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $SchedulerLog `
            -RedirectStandardError $SchedulerErrorLog `
            -PassThru
        Set-Content -LiteralPath $SchedulerPidFile -Value $schedulerProcess.Id -Encoding ascii
        Start-Sleep -Seconds 2
        if (-not (Test-SchedulerHealthy)) {
            throw "Paper scheduler failed its startup health check. See $SchedulerLog"
        }
    }
    if (-not (Test-Path -LiteralPath $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }
    if (-not (Test-Path -LiteralPath $StartupLog)) { New-Item -ItemType File -Path $StartupLog | Out-Null }
    Write-Step "paper console already running"
    Save-ListenerPid $ApiPort $ApiPidFile
    Save-ListenerPid $FrontendPort $FrontendPidFile
    Write-Step "frontend: $FrontendUrl"
    Write-Step "opening browser"
    [void](Open-Frontend $FrontendUrl)
    exit 0
}

Ensure-Runtime
Initialize-LocalDatabase

if (-not $apiReady) { Stop-RecordedProcess $ApiPidFile $ApiPort }
if (-not $frontendReady) { Stop-RecordedProcess $FrontendPidFile $FrontendPort }

if (-not $apiReady) {
    Write-Step "starting API http://127.0.0.1:$ApiPort"
    # Start Uvicorn directly. PowerShell wrapper processes can retain inherited
    # handles and make the Windows ASGI server accept connections without serving them.
    $apiProcess = Start-Process -FilePath $env:AGENT_PYTHON `
        -ArgumentList @("-m", "apps.api.local_server", "--host", "127.0.0.1", "--port", $ApiPort, "--log-level", "warning", "--local-console") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $ApiPidFile -Value $apiProcess.Id -Encoding ascii
}

Stop-RecordedScheduler
Reset-LogFile $SchedulerLog
Reset-LogFile $SchedulerErrorLog
$schedulerScript = Join-Path $PSScriptRoot "run-local-paper-scheduler.py"
$schedulerProcess = Start-Process -FilePath $env:AGENT_PYTHON `
    -ArgumentList @($schedulerScript, "--database-url", $SqliteUrl) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $SchedulerLog `
    -RedirectStandardError $SchedulerErrorLog `
    -PassThru
Set-Content -LiteralPath $SchedulerPidFile -Value $schedulerProcess.Id -Encoding ascii

if (-not $frontendReady) {
    Write-Step "starting frontend http://127.0.0.1:$FrontendPort"
    $frontendCmd = "npm --workspace frontend/admin run dev -- --host 127.0.0.1 --port $FrontendPort >> `"$FrontendLog`" 2>&1"
    $frontendProcess = Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/d", "/s", "/c", $frontendCmd) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $FrontendPidFile -Value $frontendProcess.Id -Encoding ascii
}

Write-Step "waiting for services (up to 90s)"
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if (-not $apiReady) { $apiReady = Test-EndpointReady $ApiHealthUrl }
    if (-not $frontendReady) { $frontendReady = Test-EndpointReady "http://127.0.0.1:$FrontendPort/" }
    if ($apiReady -and $frontendReady) { break }
    Start-Sleep -Seconds 1
}

if ($apiReady -and $frontendReady) {
    Save-ListenerPid $ApiPort $ApiPidFile
    Save-ListenerPid $FrontendPort $FrontendPidFile
    Write-Step "services ready"
    Write-Step "frontend: $FrontendUrl"
    Write-Step "API: http://127.0.0.1:$ApiPort"
    Write-Step "opening browser"
    [void](Open-Frontend $FrontendUrl)
    exit 0
}

Write-Step "startup failed"
Write-Step "check logs: $ApiLog ; $FrontendLog"
Remove-Item -LiteralPath $ApiPidFile,$FrontendPidFile -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $ApiLog) {
    Write-Host "--- api.log (tail) ---"
    Get-Content -LiteralPath $ApiLog -Tail 15 -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $FrontendLog) {
    Write-Host "--- frontend.log (tail) ---"
    Get-Content -LiteralPath $FrontendLog -Tail 15 -ErrorAction SilentlyContinue
}
Write-Step "browser was not opened because startup did not finish"
exit 1
