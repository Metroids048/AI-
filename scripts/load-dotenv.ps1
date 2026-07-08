function Import-DotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }
        $equalsAt = $line.IndexOf("=")
        if ($equalsAt -lt 1) {
            return
        }
        $name = $line.Substring(0, $equalsAt).Trim()
        $value = $line.Substring($equalsAt + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($name) {
            Set-Item -Path "env:$name" -Value $value
        }
    }
    return $true
}
