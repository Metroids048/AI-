$ErrorActionPreference = "Stop"

$AuditRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$RepoRoot = (Resolve-Path (Join-Path $AuditRoot "..\..\..")).Path
$RawRoot = Join-Path $AuditRoot "RAW"
$SourcePath = Join-Path $RepoRoot "docs\audit\AI-global-project-acceptance-audit-master-prompt.md"
$SnapshotPath = Join-Path $RawRoot "master-prompt.snapshot.md"
$StartedAt = (Get-Date).ToString("o")

Copy-Item -LiteralPath $SourcePath -Destination $SnapshotPath -Force
$SourceHash = (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
$SnapshotHash = (Get-FileHash -LiteralPath $SnapshotPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ExitCode = $(if ($SourceHash -eq $SnapshotHash) { 0 } else { 1 })
$EndedAt = (Get-Date).ToString("o")

$Record = [ordered]@{
    command = "Copy docs/audit/AI-global-project-acceptance-audit-master-prompt.md to RAW/master-prompt.snapshot.md and compare SHA256"
    working_directory = $RepoRoot
    environment_variable_names = @()
    started_at = $StartedAt
    ended_at = $EndedAt
    exit_code = $ExitCode
    stdout = [ordered]@{
        source_sha256 = $SourceHash
        snapshot_sha256 = $SnapshotHash
        identical = ($SourceHash -eq $SnapshotHash)
    }
    stderr = ""
}
$Record | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath (Join-Path $RawRoot "18-master-prompt-snapshot.command.json") -Encoding UTF8

if ($ExitCode -ne 0) {
    throw "Master prompt snapshot hash mismatch"
}

Write-Output "SHA256=$SnapshotHash"
