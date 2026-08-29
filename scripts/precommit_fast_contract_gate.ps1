[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Filenames)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$contractPath = Join-Path $repoRoot "contracts\automated_trading_frozen_contract.json"
$criticalPrefixes = @("services/execution/", "services/validation/", "services/strategy_library/", "apps/api/routers/")
$critical = @($Filenames | ForEach-Object { $_.Replace("\\", "/") } | Where-Object { $path = $_; $criticalPrefixes | Where-Object { $path.StartsWith($_) } })
if ($critical.Count -eq 0) { exit 0 }

try { $contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json } catch { Write-Error "FAIL: cannot read frozen contract: $_"; exit 1 }
$nodeids = @($contract.fast_contract_tests)
$timeoutSeconds = $contract.fast_contract_timeout_seconds
if ($nodeids.Count -eq 0 -or @($nodeids | Where-Object { $_ -notmatch "::" }).Count -gt 0 -or $timeoutSeconds -le 0) {
    Write-Error "FAIL: invalid fast_contract_tests manifest"; exit 1
}
if ($nodeids.Count -ne @($nodeids | Select-Object -Unique).Count) { Write-Error "FAIL: duplicate fast contract nodeid"; exit 1 }

$python = (& py -3.12 -c "import sys; print(sys.executable)").Trim()
if (-not $python) { Write-Error "FAIL: Python 3.12 with pytest is unavailable"; exit 1 }
Write-Output "[pre-commit] fixed fast contract gate: $($nodeids.Count) nodeids, ${timeoutSeconds}s each"
foreach ($nodeid in $nodeids) {
    Write-Output "[pre-commit] FAST_CONTRACT_TEST: $nodeid"
    $process = Start-Process -FilePath $python -ArgumentList @("-m", "pytest", "-q", "--no-header", $nodeid) -WorkingDirectory $repoRoot -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds($timeoutSeconds)
    while (-not $process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (-not $process.HasExited) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        Write-Error "FAIL_HUNG_CONTRACT_TEST: nodeid=$nodeid timeout_seconds=$timeoutSeconds"
        exit 1
    }
    if ($process.ExitCode -ne 0) { Write-Error "FAIL_FAST_CONTRACT_TEST: nodeid=$nodeid exit_code=$($process.ExitCode)"; exit 1 }
}
Write-Output "[pre-commit] frozen fast contract gate passed."
