param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8016",
    [int]$IntervalSeconds = 30,
    [int]$MaxIterations = 1440,
    [string]$OutputPath = "logs/v2-runtime-watch.jsonl"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath
} else {
    Join-Path $root $OutputPath
}
$outputDirectory = Split-Path -Parent $resolvedOutput
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}

for ($iteration = 1; $iteration -le $MaxIterations; $iteration++) {
    $observedAt = (Get-Date).ToUniversalTime().ToString("o")
    try {
        $runtime = Invoke-RestMethod `
            -Uri "$ApiBaseUrl/api/v2/automated-trading/runtime" `
            -TimeoutSec 20 `
            -Proxy $null
        $positions = Invoke-RestMethod `
            -Uri "$ApiBaseUrl/api/v2/automated-trading/positions" `
            -TimeoutSec 20 `
            -Proxy $null
        $latest = @($runtime.latest_decisions) | Select-Object -First 2
        $record = [ordered]@{
            observed_at = $observedAt
            engine_mode = $runtime.engine.mode
            activation = $runtime.engine.activation
            entry_enabled = $runtime.engine.entry_enabled
            scheduler_running = $runtime.scheduler.running
            scheduler_last_cycle_at = $runtime.scheduler.last_cycle_at
            reconciliation = $runtime.reconciliation.status
            exchange_available = $positions.exchange.available
            exchange_position_count = @($positions.exchange.positions).Count
            local_open_position_count = @($positions.local_projection.positions).Count
            exchange_order_count = @($runtime.exchange.open_orders).Count
            latest_decisions = @(
                $latest | ForEach-Object {
                    [ordered]@{
                        symbol = $_.symbol
                        terminal_stage = $_.terminal_stage
                        reason_code = $_.reason_code
                        evaluated_at = $_.evaluated_at
                        exchange_submitted = $_.exchange_submitted
                    }
                }
            )
        }
    }
    catch {
        $record = [ordered]@{
            observed_at = $observedAt
            probe_error = $_.Exception.Message
        }
    }

    Add-Content -LiteralPath $resolvedOutput -Value ($record | ConvertTo-Json -Compress -Depth 8) -Encoding utf8
    if ($iteration -lt $MaxIterations) {
        Start-Sleep -Seconds $IntervalSeconds
    }
}
