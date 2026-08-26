param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PytestArgs
)

$ErrorActionPreference = "Stop"

# Canonical project test entry: avoid PATH selecting an unrelated interpreter.
& py -3 -m pytest @PytestArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
