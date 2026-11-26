# Switch Provenant from the demo people to real sign-in with Supabase, and invite the first partner.
# Asks for the Supabase secret key in this window (hidden, never printed) and saves it in the git-ignored backend/.env.
# Usage, from backend/:  powershell -ExecutionPolicy Bypass -File .\scripts\set-signin.ps1
$ErrorActionPreference = "Stop"
$backend = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $backend ".env"
$webEnv = Join-Path (Split-Path $backend -Parent) "frontend\.env.local"
$projectUrl = if (Test-Path $webEnv) { (Select-String -Path $webEnv -Pattern "^NEXT_PUBLIC_SUPABASE_URL=(.+)$").Matches.Groups[1].Value } else { "" }
if (-not $projectUrl) { throw "Set NEXT_PUBLIC_SUPABASE_URL in frontend/.env.local first (see frontend/.env.local.example)." }

Write-Host "Supabase: Project Settings -> API Keys -> Secret keys. Copy the key that starts with sb_secret_."
Write-Host ""
$secure = Read-Host "Supabase secret key (hidden)" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()
if (-not $key.StartsWith("sb_secret_")) { throw "That does not look like a Supabase secret key (sb_secret_...)." }
$partner = (Read-Host "Email of the first partner (you)").Trim()
if ($partner -notmatch "@") { throw "Enter an email address." }

$values = [ordered]@{
    "PROVENANT_SUPABASE_URL" = $projectUrl
    "PROVENANT_SUPABASE_SECRET_KEY" = $key
    "PROVENANT_APP_URL" = "http://localhost:3100"
}
$lines = if (Test-Path $envFile) { [IO.File]::ReadAllLines($envFile) } else { @() }
$out = New-Object System.Collections.Generic.List[string]
$done = @{}
foreach ($line in $lines) {
    $name = ($line -split "=", 2)[0].Trim()
    if ($values.Contains($name)) { $out.Add("$name=$($values[$name])"); $done[$name] = $true } else { $out.Add($line) }
}
foreach ($name in $values.Keys) { if (-not $done[$name]) { $out.Add("$name=$($values[$name])") } }
[IO.File]::WriteAllLines($envFile, $out, (New-Object Text.UTF8Encoding $false))  # no byte-order mark
Write-Host "Saved the sign-in settings to backend/.env (git-ignored)."

Set-Location $backend
& .\.venv\Scripts\python scripts\invite_first_partner.py $partner
Write-Host ""
Write-Host "Next: restart the API (scripts\start-api.ps1) and the web app (npm run dev in frontend), then open the email."
