param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$DatabasePath = ".local_paper_console.db"
)

# Compatibility entrypoint for older shortcuts. The implementation lives in
# launch-paper-console.ps1 so startup behavior cannot drift between scripts.
& (Join-Path $PSScriptRoot "launch-paper-console.ps1") `
    -ApiPort $ApiPort `
    -FrontendPort $FrontendPort `
    -DatabasePath $DatabasePath
exit $LASTEXITCODE
