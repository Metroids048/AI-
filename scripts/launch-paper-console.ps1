# One-click launcher — same pattern as 辅助面试/scripts/launch-experience.ps1
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root

$ApiPort = 8000
$FrontendPort = 5173
$ApiHealthUrl = "http://127.0.0.1:$ApiPort/health"
$FrontendUrl = "http://127.0.0.1:$FrontendPort/trading"
$LogsDir = Join-Path $Root "logs"
$ApiLog = Join-Path $LogsDir "api.log"
$FrontendLog = Join-Path $LogsDir "frontend.log"
$DbPath = Join-Path $Root ".local_paper_console.db"
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
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python not found. Install Python 3.11+."
    }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "Node.js/npm not found."
    }
    if (-not (Test-Path (Join-Path $Root "node_modules"))) {
        Write-Host "Installing npm dependencies (first run)..."
        npm install
    }
    if (-not (Test-Path -LiteralPath $LogsDir)) {
        New-Item -ItemType Directory -Path $LogsDir | Out-Null
    }
    . (Join-Path $PSScriptRoot "load-dotenv.ps1")
    $envPath = Join-Path $Root ".env"
    if (Test-Path -LiteralPath $envPath) {
        Import-DotEnv $envPath | Out-Null
    }
    $env:POSTGRES_URL = $SqliteUrl
    $env:APP_ENV = "development"
    $env:BINANCE_USE_TESTNET = "true"
    $env:RUNTIME_SCHEDULER_MODE = "inprocess"
    $env:RUNTIME_SCHEDULER_AUTOSTART = "true"
}

$apiReady = Test-EndpointReady $ApiHealthUrl
$frontendReady = Test-EndpointReady "http://127.0.0.1:$FrontendPort/"

if ($apiReady -and $frontendReady) {
    Write-Host "Paper console already running."
    Write-Host "Frontend: $FrontendUrl"
    Write-Host "Opening browser..."
    [void](Open-Frontend $FrontendUrl)
    exit 0
}

Ensure-Runtime

if (-not $apiReady) {
    Write-Host "Starting API http://127.0.0.1:$ApiPort ..."
    $runApi = Join-Path $PSScriptRoot "run-api-local.ps1"
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $runApi, "-Root", $Root, "-PostgresUrl", $SqliteUrl, "-Port", $ApiPort, "-LogPath", $ApiLog) `
        -WorkingDirectory $Root
}

if (-not $frontendReady) {
    Write-Host "Starting frontend http://127.0.0.1:$FrontendPort ..."
    $frontendCmd = "Set-Location -LiteralPath '$Root'; npm --workspace frontend/admin run dev -- --host 127.0.0.1 --port $FrontendPort *>> '$FrontendLog'"
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", $frontendCmd) `
        -WorkingDirectory $Root
}

Write-Host "Waiting for services (up to 90s)..."
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if (-not $apiReady) { $apiReady = Test-EndpointReady $ApiHealthUrl }
    if (-not $frontendReady) { $frontendReady = Test-EndpointReady "http://127.0.0.1:$FrontendPort/" }
    if ($apiReady -and $frontendReady) { break }
    Start-Sleep -Seconds 1
}

if ($apiReady -and $frontendReady) {
    Write-Host "Ready."
    Write-Host "Frontend: $FrontendUrl"
    Write-Host "API:      http://127.0.0.1:$ApiPort"
    Write-Host "Opening browser..."
    [void](Open-Frontend $FrontendUrl)
    exit 0
}

Write-Host "Startup failed."
Write-Host "Check logs: $ApiLog ; $FrontendLog"
if (Test-Path -LiteralPath $ApiLog) {
    Write-Host "--- api.log (tail) ---"
    Get-Content -LiteralPath $ApiLog -Tail 15 -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $FrontendLog) {
    Write-Host "--- frontend.log (tail) ---"
    Get-Content -LiteralPath $FrontendLog -Tail 15 -ErrorAction SilentlyContinue
}
Write-Host "Browser was not opened because startup did not finish."
exit 1
