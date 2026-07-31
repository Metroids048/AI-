$ErrorActionPreference = "Stop"

$AuditRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$RepoRoot = (Resolve-Path (Join-Path $AuditRoot "..\..\..")).Path
$RawRoot = Join-Path $AuditRoot "RAW"
$StartedAt = (Get-Date).ToString("o")
$Checks = [System.Collections.Generic.List[object]]::new()
$Failures = [System.Collections.Generic.List[string]]::new()

function Add-Check {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$Passed,
        [Parameter(Mandatory = $true)][string]$Detail
    )

    $Checks.Add([ordered]@{
        name = $Name
        passed = $Passed
        detail = $Detail
    })
    if (-not $Passed) {
        $Failures.Add("$Name`: $Detail")
    }
}

$RequiredDeliverables = @(
    "GLOBAL_ACCEPTANCE_REPORT.md",
    "GLOBAL_ACCEPTANCE_MANIFEST.json",
    "STRATEGY_READINESS.md",
    "NEXT_ACTION_PROMPT.md"
)
foreach ($name in $RequiredDeliverables) {
    Add-Check -Name "required_file:$name" `
        -Passed (Test-Path -LiteralPath (Join-Path $AuditRoot $name)) `
        -Detail "Required primary deliverable exists"
}

$ManifestPath = Join-Path $AuditRoot "GLOBAL_ACCEPTANCE_MANIFEST.json"
$BaselinePath = Join-Path $AuditRoot "BASELINE.json"
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$Baseline = Get-Content -LiteralPath $BaselinePath -Raw | ConvertFrom-Json
Add-Check -Name "manifest_json" -Passed ($null -ne $Manifest) -Detail "Manifest parses as JSON"
Add-Check -Name "baseline_json" -Passed ($null -ne $Baseline) -Detail "Baseline parses as JSON"

$AllowedVerdicts = @(
    "REJECTED_P0",
    "REJECTED_INTEGRATION",
    "BLOCKED_REAL_ENV",
    "ACCEPTED_ENGINEERING_NOT_STRATEGY_READY",
    "ACCEPTED_READY_FOR_STRATEGY_OPTIMIZATION"
)
Add-Check -Name "verdict_enum" `
    -Passed ($AllowedVerdicts -contains $Manifest.verdict) `
    -Detail "Verdict is $($Manifest.verdict)"
Add-Check -Name "expected_verdict" `
    -Passed ($Manifest.verdict -eq "REJECTED_INTEGRATION") `
    -Detail "Dirty baseline maps to REJECTED_INTEGRATION"
Add-Check -Name "locked_head" `
    -Passed ($Manifest.audit_head -eq "7351110595bc063f3db69afa1b5554cdb8de7d3a") `
    -Detail "Manifest retains the locked HEAD"

foreach ($property in $Manifest.deliverables.PSObject.Properties) {
    $path = Join-Path $AuditRoot $property.Name
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    Add-Check -Name "sha256:$($property.Name)" `
        -Passed ($actualHash -eq $property.Value.sha256) `
        -Detail "actual=$actualHash expected=$($property.Value.sha256)"
}

$statusOutput = & git status --short --untracked-files=all -- . `
    ":(exclude)audit/global_acceptance/20260729-143208-7351110" 2>&1
$statusExitCode = $LASTEXITCODE
$statusText = ($statusOutput | Out-String).Trim()
Add-Check -Name "status_command" -Passed ($statusExitCode -eq 0) -Detail "git status exit=$statusExitCode"
Add-Check -Name "outside_audit_status_unchanged" `
    -Passed ($statusText -eq "?? docs/audit/AI-global-project-acceptance-audit-master-prompt.md") `
    -Detail "status=$statusText"

$PrivateKeyMatches = Get-ChildItem -LiteralPath $AuditRoot -File -Recurse |
    Select-String -Pattern "-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----" -AllMatches
Add-Check -Name "private_key_scan" `
    -Passed (($PrivateKeyMatches | Measure-Object).Count -eq 0) `
    -Detail "No private-key block marker found"

$CredentialValueMatches = Get-ChildItem -LiteralPath $AuditRoot -File -Recurse |
    Select-String -Pattern '"BINANCE_API_(KEY|SECRET)"\s*:\s*"[A-Za-z0-9+/=_-]{8,}"' -AllMatches
Add-Check -Name "credential_value_scan" `
    -Passed (($CredentialValueMatches | Measure-Object).Count -eq 0) `
    -Detail "No non-empty Binance credential value found"

$MainnetEndpointMatches = Get-ChildItem -LiteralPath $AuditRoot -File -Recurse |
    Select-String -Pattern "https://fapi\.binance\.com" -AllMatches
Add-Check -Name "mainnet_endpoint_scan" `
    -Passed (($MainnetEndpointMatches | Measure-Object).Count -eq 0) `
    -Detail "No Binance USDT-M mainnet endpoint found in evidence"

$EndedAt = (Get-Date).ToString("o")
$Record = [ordered]@{
    command = "pwsh -File PROBES/verify_outputs.ps1"
    working_directory = $RepoRoot
    environment_variable_names = @()
    started_at = $StartedAt
    ended_at = $EndedAt
    exit_code = $(if ($Failures.Count -eq 0) { 0 } else { 1 })
    stdout = $Checks
    stderr = $Failures
}
$Record | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath (Join-Path $RawRoot "17-output-verification.command.json") -Encoding UTF8

if ($Failures.Count -gt 0) {
    $Failures | ForEach-Object { Write-Error $_ }
    exit 1
}

$Checks | ForEach-Object {
    Write-Output ("PASS {0}: {1}" -f $_.name, $_.detail)
}
