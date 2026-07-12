param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [Parameter(Mandatory = $true)]
    [string]$PostgresUrl,
    [int]$Port = 8000,
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "load-dotenv.ps1")
if (-not $env:AGENT_PYTHON -or -not (Test-Path -LiteralPath $env:AGENT_PYTHON)) {
    throw "AGENT_PYTHON is unavailable. Run verify-global-agent-stack.ps1 before starting the API."
}
$envPath = Join-Path $Root ".env"
Import-DotEnv $envPath | Out-Null

# Keep the desktop process responsive when the child has no route to Binance.
# This must live here rather than only in the parent launcher because Windows
# child-process environment inheritance is not a reliable runtime contract.
if (-not $env:BINANCE_HTTPS_PROXY -and $env:HTTPS_PROXY) {
    $env:BINANCE_HTTPS_PROXY = $env:HTTPS_PROXY
}
if (-not $env:BINANCE_HTTPS_PROXY) {
    $env:PAPER_CONSOLE_DISABLE_LIVE_WS = "true"
    $env:PAPER_CONSOLE_SKIP_BACKGROUND_BOOTSTRAP = "true"
}

# Local Paper console always wins over docker-compose POSTGRES_URL in .env.
$env:POSTGRES_URL = $PostgresUrl
$env:APP_ENV = "development"
$env:BINANCE_USE_TESTNET = "true"
$env:LIVE_TRADING_ENABLED = "false"
$env:RUNTIME_SCHEDULER_MODE = "inprocess"
$env:RUNTIME_SCHEDULER_AUTOSTART = "true"
if ($env:PAPER_CONSOLE_API_ONLY -eq "true") {
    $env:RUNTIME_SCHEDULER_AUTOSTART = "false"
    $env:BINANCE_LIVE_UNIVERSE_ENABLED = "false"
    $env:BINANCE_LIVE_MARKET_ENABLED = "false"
}
$env:PAPER_RUNTIME_CYCLE_SECONDS = if ($env:PAPER_RUNTIME_CYCLE_SECONDS) { $env:PAPER_RUNTIME_CYCLE_SECONDS } else { "60" }
$env:BINANCE_AUTO_EXECUTE = if ($env:BINANCE_AUTO_EXECUTE) { $env:BINANCE_AUTO_EXECUTE } else { "false" }
$env:BINANCE_LIVE_UNIVERSE_ENABLED = "true"
$env:BINANCE_LIVE_MARKET_ENABLED = "true"
$env:BINANCE_LIVE_WS_ENABLED = "true"
if ($env:PAPER_CONSOLE_DISABLE_LIVE_WS -eq "true") {
    $env:BINANCE_LIVE_WS_ENABLED = "false"
}
if (-not $env:BINANCE_LIVE_WS_SYMBOLS) { $env:BINANCE_LIVE_WS_SYMBOLS = "top20" }
if (-not $env:PAPER_RUNTIME_ENABLE_DECISION_VETO) { $env:PAPER_RUNTIME_ENABLE_DECISION_VETO = "true" }
if (-not $env:LOG_LEVEL) { $env:LOG_LEVEL = "INFO" }
if (-not $env:APP_BUILD_ID) {
    $env:APP_BUILD_ID = (git -C $Root rev-parse --short HEAD 2>$null)
    if (-not $env:APP_BUILD_ID) { $env:APP_BUILD_ID = "development" }
}
# Skip outbound model-catalog discovery; use seed free models unless explicitly overridden.
if (-not $env:LLM_USE_CATALOG_SEEDS_ONLY) { $env:LLM_USE_CATALOG_SEEDS_ONLY = "true" }

if (-not $env:BINANCE_SPOT_REST_BASE) { $env:BINANCE_SPOT_REST_BASE = "https://data-api.binance.vision" }
if (-not $env:BINANCE_USDM_REST_BASE) { $env:BINANCE_USDM_REST_BASE = "https://demo-fapi.binance.com" }
if (-not $env:BINANCE_SPOT_WS_BASE) { $env:BINANCE_SPOT_WS_BASE = "wss://data-stream.binance.vision/ws" }
if (-not $env:BINANCE_USDM_WS_BASE) { $env:BINANCE_USDM_WS_BASE = "wss://stream.binancefuture.com/ws" }

Set-Location $Root

function Rotate-RuntimeLog {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [long]$MaxBytes = 10485760,
        [int]$Retention = 5
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $file = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $file -or $file.Length -lt $MaxBytes) { return }
    for ($index = $Retention - 1; $index -ge 1; $index--) {
        $source = "$Path.$index"
        $target = "$Path.$($index + 1)"
        if (Test-Path -LiteralPath $source) {
            Move-Item -LiteralPath $source -Destination $target -Force
        }
    }
    Move-Item -LiteralPath $Path -Destination "$Path.1" -Force
}
& $env:AGENT_PYTHON scripts/prepare_database.py --database-url $PostgresUrl
if ($LASTEXITCODE -ne 0) {
    throw "Database preparation failed; API will not start against an incomplete schema."
}

if ($LogPath) {
    Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [paper-console] API process started" -Encoding utf8
}

# Do not invoke the long-running ASGI process through PowerShell's output pipeline.
# On Windows that can leave Uvicorn listening while request handlers stop running.
$null = Start-Process -FilePath $env:AGENT_PYTHON `
    -ArgumentList @("-m", "apps.api.local_server", "--host", "127.0.0.1", "--port", $Port, "--log-level", "warning") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru
