# Save the Brevo (or any SMTP) sign-in into the git-ignored backend/.env, asking in this window. The key is hidden as
# you type and is never printed. Usage, from backend/:  powershell -ExecutionPolicy Bypass -File .\scripts\set-email.ps1
$ErrorActionPreference = "Stop"
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"

Write-Host "Brevo: SMTP & API -> SMTP tab shows your SMTP login and lets you generate an SMTP key."
Write-Host "The sender must be an address you have verified in Brevo (Senders, domains & dedicated IPs)."
Write-Host ""
$login = (Read-Host "SMTP login (looks like xxxx@smtp-brevo.com)").Trim()
$secure = Read-Host "SMTP key (hidden)" -AsSecureString
$key = ([Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)) -replace "\x1b\[20[01]~", "" -replace "[\x00-\x20\x7f]", "")
$sender = (Read-Host "Sender email you verified in Brevo").Trim()
if (-not $login -or -not $key -or $sender -notmatch "@") { throw "All three values are needed." }

$values = [ordered]@{
    "PROVENANT_SMTP_HOST" = "smtp-relay.brevo.com"
    "PROVENANT_SMTP_PORT" = "587"
    "PROVENANT_SMTP_USER" = $login
    "PROVENANT_SMTP_PASSWORD" = $key
    "PROVENANT_SMTP_FROM" = "Provenant <$sender>"
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

Write-Host ""
Write-Host "Saved to backend/.env (git-ignored). Sending a test email to $sender ..."
& .\.venv\Scripts\python scripts\send_test_email.py $sender
Write-Host "Now restart the API (Ctrl+C in its window, then run scripts\start-api.ps1) so it uses these settings."
