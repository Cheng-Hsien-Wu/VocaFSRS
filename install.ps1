$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$EnvFile = Join-Path $BackendDir ".env"
$RuntimeConfig = Join-Path $RootDir ".vocafsrs.conf"

function Read-Default {
    param([string]$Message, [string]$Default)
    $Value = Read-Host "$Message [$Default]"
    if ([string]::IsNullOrWhiteSpace($Value)) { return $Default }
    return $Value.Trim()
}

function Read-SecretText {
    param([string]$Message)
    $SecureValue = Read-Host "$Message (leave blank to skip)" -AsSecureString
    return ([pscredential]::new("value", $SecureValue)).GetNetworkCredential().Password
}

function Require-Command {
    param([string]$Name, [string]$InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name`n$InstallHint"
    }
}

function ConvertTo-DotEnvValue {
    param([string]$Value)
    $Escaped = $Value.Replace("\", "\\").Replace('"', '\"')
    return '"' + $Escaped + '"'
}

function Get-ExistingSetting {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path $Path)) { return $null }
    $Prefix = "$Name="
    $Line = Get-Content $Path | Where-Object { $_.StartsWith($Prefix) } |
        Select-Object -Last 1
    if (-not $Line) { return $null }
    return $Line.Substring($Prefix.Length).Trim('"')
}

function Get-LanAddress {
    $Route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric |
        Select-Object -First 1
    $Address = if ($Route) {
        Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $Route.InterfaceIndex -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "169.254*" } |
            Select-Object -First 1 -ExpandProperty IPAddress
    }
    if ($Address) { return $Address }
    return "127.0.0.1"
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    $Prefix = "$Name="
    $Found = $false
    $Lines = foreach ($Line in Get-Content $Path) {
        if ($Line.StartsWith($Prefix)) {
            if (-not $Found) {
                "$Prefix$Value"
                $Found = $true
            }
        }
        else {
            $Line
        }
    }
    if (-not $Found) {
        $Lines += "$Prefix$Value"
    }
    [IO.File]::WriteAllLines($Path, $Lines, [Text.UTF8Encoding]::new($false))
}

function Invoke-Checked {
    param([scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

Write-Host "`nVocaFSRS interactive setup"
Write-Host "This installer builds the app, creates the database, and can import one TXT/CSV dataset."

Require-Command "uv" "Install uv from https://docs.astral.sh/uv/getting-started/installation/"
Require-Command "node" "Install a supported Node.js release from https://nodejs.org/"
Require-Command "npm" "npm is normally included with Node.js."

$NodeParts = (& node -p 'process.versions.node').Trim().Split(".")
$NodeMajor = [int]$NodeParts[0]
$NodeMinor = [int]$NodeParts[1]
$SupportedNode = ($NodeMajor -eq 20 -and $NodeMinor -ge 19) -or
    ($NodeMajor -gt 22) -or ($NodeMajor -eq 22 -and $NodeMinor -ge 12)
if (-not $SupportedNode) {
    throw "Node.js 20.19+ or 22.12+ is required. Current version: $(& node --version)"
}

$ExistingUrl = Get-ExistingSetting $EnvFile "APP_PUBLIC_URL"
$DefaultAddress = Get-LanAddress
if ($ExistingUrl -match "^https?://([^/:]+)") {
    $DefaultAddress = $Matches[1]
}

$DefaultPort = Get-ExistingSetting $RuntimeConfig "PORT"
if (-not $DefaultPort) { $DefaultPort = "8080" }

$LanAddress = Read-Default "IP or hostname used by another device" $DefaultAddress
$PortText = Read-Default "Web port" $DefaultPort
$Port = 0
if (-not [int]::TryParse($PortText, [ref]$Port) -or $Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be a number from 1 to 65535."
}

$ReplaceEnv = $true
if (Test-Path $EnvFile) {
    $ReplaceEnv = (Read-Default "backend/.env already exists. Replace it? (y/N)" "N") -match "^[Yy]$"
}

$PublicUrl = "http://${LanAddress}:$Port"
if ($ReplaceEnv) {
    $Timezone = Read-Default "Report timezone" "Asia/Taipei"
    $OpenRouterKey = Read-SecretText "OpenRouter API key"
    $GoogleKey = Read-SecretText "Gemini API key (fallback)"
    if (-not $OpenRouterKey -and -not $GoogleKey) {
        Write-Warning "Formal review answers cannot be graded until an LLM key is added to backend/.env."
    }
    $DiscordWebhook = Read-SecretText "Discord webhook URL for due-review reminders"

    $EnvLines = @(
        "VOCAB_ENV=production"
        "DATABASE_URL="
        "DATABASE_PATH=data/vocab.db"
        "ALLOWED_ORIGINS=$(ConvertTo-DotEnvValue $PublicUrl)"
        "OPENROUTER_API_KEY=$(ConvertTo-DotEnvValue $OpenRouterKey)"
        "OPENROUTER_MODEL=openrouter/owl-alpha"
        "OPENROUTER_SITE_URL=$(ConvertTo-DotEnvValue $PublicUrl)"
        "OPENROUTER_APP_NAME=VocaFSRS"
        "GOOGLE_API_KEY=$(ConvertTo-DotEnvValue $GoogleKey)"
        "LLM_MODEL=gemini-2.5-flash"
        "LLM_TIMEOUT_SECONDS=45"
        "REPORT_TIMEZONE=$(ConvertTo-DotEnvValue $Timezone)"
        "DISCORD_WEBHOOK_URL=$(ConvertTo-DotEnvValue $DiscordWebhook)"
        "APP_PUBLIC_URL=$(ConvertTo-DotEnvValue $PublicUrl)"
        "NOTIFICATION_POLL_SECONDS=60"
    )
}
else {
    Write-Host "Keeping the existing backend configuration."
}

Write-Host "`nInstalling backend dependencies"
Push-Location $BackendDir
try {
    Invoke-Checked { & uv sync --no-dev }
}
finally {
    Pop-Location
}

Write-Host "`nInstalling and building the frontend"
Push-Location $FrontendDir
try {
    Invoke-Checked { & npm ci }
    Invoke-Checked { & npm run build }
}
finally {
    Pop-Location
}

$EnvExisted = Test-Path $EnvFile
$ConfigExisted = Test-Path $RuntimeConfig
$EnvBackup = if ($EnvExisted) { [IO.File]::ReadAllBytes($EnvFile) } else { $null }
$ConfigBackup = if ($ConfigExisted) { [IO.File]::ReadAllBytes($RuntimeConfig) } else { $null }

try {
    if ($ReplaceEnv) {
        [IO.File]::WriteAllLines($EnvFile, $EnvLines, [Text.UTF8Encoding]::new($false))
    }
    else {
        Set-DotEnvValue $EnvFile "ALLOWED_ORIGINS" (ConvertTo-DotEnvValue $PublicUrl)
        Set-DotEnvValue $EnvFile "OPENROUTER_SITE_URL" (ConvertTo-DotEnvValue $PublicUrl)
        Set-DotEnvValue $EnvFile "APP_PUBLIC_URL" (ConvertTo-DotEnvValue $PublicUrl)
    }
    [IO.File]::WriteAllLines(
        $RuntimeConfig,
        @("HOST=0.0.0.0", "PORT=$Port"),
        [Text.UTF8Encoding]::new($false)
    )

    Write-Host "`nUpdating the database"
    Push-Location $BackendDir
    try {
        Invoke-Checked { & uv run alembic upgrade head }
    }
    finally {
        Pop-Location
    }
}
catch {
    if ($EnvExisted) {
        [IO.File]::WriteAllBytes($EnvFile, $EnvBackup)
    }
    else {
        Remove-Item $EnvFile -ErrorAction SilentlyContinue
    }
    if ($ConfigExisted) {
        [IO.File]::WriteAllBytes($RuntimeConfig, $ConfigBackup)
    }
    else {
        Remove-Item $RuntimeConfig -ErrorAction SilentlyContinue
    }
    throw "Installation failed; previous configuration was restored. $($_.Exception.Message)"
}

while ($true) {
    $DatasetPath = Read-Host "Dataset path to import now (.txt/.csv, absolute or relative to the current directory; blank to import later)"
    if ([string]::IsNullOrWhiteSpace($DatasetPath)) { break }

    try {
        $ResolvedDataset = (Resolve-Path $DatasetPath.Trim()).Path
        Write-Host "`nImporting vocabulary"
        Push-Location $BackendDir
        try {
            $env:PYTHONPATH = "."
            Invoke-Checked { & uv run python scripts/import_vocabulary.py $ResolvedDataset }
        }
        finally {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
            Pop-Location
        }
        break
    }
    catch {
        Write-Warning $_.Exception.Message
        if ((Read-Default "Import failed. Try another file? (Y/n)" "Y") -match "^[Nn]$") {
            break
        }
    }
}

Write-Host "`nInstallation complete"
Write-Host "Open: $PublicUrl"
Write-Host "Start later: .\start.ps1"
Write-Host "Dataset files may stay anywhere; imported vocabulary is stored in backend\data\vocab.db."

if ((Read-Default "Start VocaFSRS now? (Y/n)" "Y") -notmatch "^[Nn]$") {
    & (Join-Path $RootDir "start.ps1")
}
