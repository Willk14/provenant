# Start the local API with Claude on: asks for the Anthropic key in this window (never stored), waits for port 8000
# to be free, then runs scripts/dev.py. Usage, from backend/:  & .\scripts\start-api.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $env:ANTHROPIC_API_KEY) {
    $secure = Read-Host "Paste your Anthropic API key (it is not shown or saved)" -AsSecureString
    $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
if (-not $env:PROVENANT_LEGAL_AGENT) { $env:PROVENANT_LEGAL_AGENT = "claude-api" }

while (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port 8000 is still taken by the old API. Stop it (close its terminal tab or press Ctrl+C there)..."
    Start-Sleep -Seconds 3
}
& .\.venv\Scripts\python scripts\dev.py
