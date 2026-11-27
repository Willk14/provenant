# Start Provenant on this PC and open it in its own app window.
# Starts the API (scripts/dev.py, embedded Postgres) and the web app (npm run dev) in the background if they are not
# already running, waits until both answer, then opens http://localhost:3100/home as an app window in Edge or Chrome.
# Logs: %LOCALAPPDATA%\Provenant\logs. Stop everything with stop-provenant.ps1 (or the "Stop Provenant" shortcut).
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$data = Join-Path $env:LOCALAPPDATA "Provenant"
$logs = Join-Path $data "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$url = "http://localhost:3100/home"

# Keep the embedded database out of %TEMP%, which Windows clean-up can empty while Postgres still needs it.
if (-not $env:PROVENANT_DEV_PGDATA) { $env:PROVENANT_DEV_PGDATA = Join-Path $data "pg" }
# Claude runs the agents only when ANTHROPIC_API_KEY is already set for your Windows account; otherwise the rules-based
# fallbacks are used. The key is never asked for or saved by this script.
if ($env:ANTHROPIC_API_KEY -and -not $env:PROVENANT_LEGAL_AGENT) { $env:PROVENANT_LEGAL_AGENT = "claude-api" }

function Test-Listening([int]$port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Show-Problem([string]$text) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($text, "Provenant", "OK", "Warning") | Out-Null
}

$python = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Show-Problem "The backend is not set up: $python is missing."; exit 1 }
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Show-Problem "The web app is not set up: run 'npm install' in $root\frontend first."; exit 1
}

if (-not (Test-Listening 8000)) {
    Start-Process -FilePath $python -ArgumentList "scripts\dev.py" -WorkingDirectory (Join-Path $root "backend") `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "api.log") -RedirectStandardError (Join-Path $logs "api-errors.log")
}
if (-not (Test-Listening 3100)) {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" -WorkingDirectory (Join-Path $root "frontend") `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "web.log") -RedirectStandardError (Join-Path $logs "web-errors.log")
}

# The first start creates the database and compiles the web app, which can take a few minutes.
$deadline = (Get-Date).AddMinutes(5)
while (-not ((Test-Listening 8000) -and (Test-Listening 3100))) {
    if ((Get-Date) -gt $deadline) {
        Show-Problem "Provenant did not start within 5 minutes. See the logs in $logs."
        exit 1
    }
    Start-Sleep -Seconds 2
}

$browsers = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ }
if ($browsers) {
    Start-Process -FilePath $browsers[0] -ArgumentList "--app=$url"
} else {
    Start-Process $url
}
