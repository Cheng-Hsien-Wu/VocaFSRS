$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $RootDir ".vocafsrs.conf"
$EnvFile = Join-Path $RootDir "backend\.env"
$HostAddress = "0.0.0.0"
$Port = 8080

if (Test-Path $ConfigFile) {
    foreach ($Line in Get-Content $ConfigFile) {
        if ($Line -match "^HOST=(.+)$") {
            $HostAddress = $Matches[1]
        }
        elseif ($Line -match "^PORT=(\d+)$") {
            $Port = [int]$Matches[1]
        }
    }
}

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Invalid PORT in .vocafsrs.conf: $Port"
}

$PublicUrl = $null
if (Test-Path $EnvFile) {
    $UrlLine = Get-Content $EnvFile |
        Where-Object { $_.StartsWith("APP_PUBLIC_URL=") } |
        Select-Object -Last 1
    if ($UrlLine) {
        $PublicUrl = $UrlLine.Substring("APP_PUBLIC_URL=".Length).Trim('"')
    }
}
if (-not $PublicUrl) {
    $PublicUrl = "http://localhost:$Port"
}
Write-Host "Open: $PublicUrl"

Set-Location (Join-Path $RootDir "backend")
& uv run uvicorn main:app --host $HostAddress --port $Port
exit $LASTEXITCODE
