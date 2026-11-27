# Deploy Provenant to Fly.io: the API (with the worker inside it) and the web app, on the Supabase project already
# configured in backend/.env and frontend/.env.local. Run it again to deploy a new version.
#   powershell -ExecutionPolicy Bypass -File .\deploy\fly\deploy.ps1 [-ApiApp name] [-WebApp name] [-SecretsOnly]
# Needs: flyctl installed and signed in (fly auth login), the Supabase secret key saved by backend/scripts/set-signin.ps1,
# and Supabase Storage S3 access keys (asked for once, hidden, and kept in the git-ignored backend/.env).
# Secrets go straight from those files to Fly's secret store; nothing secret is printed or written anywhere else.
param(
    [string]$ApiApp = "provenant-ke-api",
    [string]$WebApp = "provenant-ke",
    [switch]$SecretsOnly,
    [switch]$NoDepot   # build with Fly's classic remote builder instead of Depot (try it if an upload stalls)
)
$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$envFile = Join-Path $backend ".env"
$webEnvFile = Join-Path $frontend ".env.local"

function Read-EnvFile([string]$path) {
    $values = @{}
    if (Test-Path $path) {
        foreach ($line in [IO.File]::ReadAllLines($path)) {
            if ($line.TrimStart().StartsWith("#") -or -not $line.Contains("=")) { continue }
            $pair = $line.Split("=", 2)
            if ($pair[1].Trim()) { $values[$pair[0].Trim()] = $pair[1].Trim() }
        }
    }
    $values
}

function Save-EnvValues([string]$path, $values) {
    $lines = if (Test-Path $path) { [IO.File]::ReadAllLines($path) } else { @() }
    $out = New-Object System.Collections.Generic.List[string]
    $done = @{}
    foreach ($line in $lines) {
        $name = ($line -split "=", 2)[0].Trim()
        if ($values.Contains($name)) { $out.Add("$name=$($values[$name])"); $done[$name] = $true } else { $out.Add($line) }
    }
    foreach ($name in $values.Keys) { if (-not $done[$name]) { $out.Add("$name=$($values[$name])") } }
    [IO.File]::WriteAllLines($path, $out, (New-Object Text.UTF8Encoding $false))
}

function Read-Hidden([string]$prompt) {
    $secure = Read-Host $prompt -AsSecureString
    ([Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)) -replace "\x1b\[20[01]~", "" -replace "[\x00-\x1f\x7f]", "").Trim()
}

# ---- flyctl
$fly = @((Get-Command flyctl -ErrorAction SilentlyContinue), (Get-Command fly -ErrorAction SilentlyContinue) |
    Where-Object { $_ } | Select-Object -ExpandProperty Source)
$fly += @(Join-Path $env:USERPROFILE ".fly\bin\flyctl.exe") | Where-Object { Test-Path $_ }
if (-not $fly) { throw "flyctl is not installed. See https://fly.io/docs/flyctl/install/ then run: fly auth login" }
$fly = $fly[0]

function Test-Fly {
    # Exit code of a fly command, with its output hidden. Windows PowerShell turns a native program's error output
    # into an error record, which "Stop" would make fatal, so relax that for the check only.
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $fly @args 2>&1 | Out-Null; $LASTEXITCODE } finally { $ErrorActionPreference = $saved }
}
$savedToken = (Read-EnvFile $envFile)["FLY_API_TOKEN"]
if (-not $env:FLY_API_TOKEN -and $savedToken) { $env:FLY_API_TOKEN = $savedToken }
if ((Test-Fly auth whoami) -ne 0) {
    # Not signed in: take an organization token from the Fly dashboard (Tokens -> Create token) for this run only.
    Write-Host "Not signed in to Fly.io. In the Fly dashboard: Tokens -> Create token (organization), then copy it."
    $env:FLY_API_TOKEN = Read-Hidden "Fly token (hidden)"
    if ((Test-Fly auth whoami) -ne 0) { throw "Fly did not accept that token. Create a new one, or run: fly auth login" }
    if ((Read-Host "Save the token in backend/.env (git-ignored) so the next deploy does not ask? (y/N)").Trim() -match "^[yY]") {
        Save-EnvValues $envFile ([ordered]@{ "FLY_API_TOKEN" = $env:FLY_API_TOKEN })
        Write-Host "Saved. Delete the FLY_API_TOKEN line from backend/.env to forget it."
    }
}

# ---- settings
$cfg = Read-EnvFile $envFile
$web = Read-EnvFile $webEnvFile
foreach ($name in "PROVENANT_SUPABASE_URL", "PROVENANT_SUPABASE_REF", "PROVENANT_SUPABASE_HOST",
                  "PROVENANT_SUPABASE_OWNER_PASSWORD", "PROVENANT_SUPABASE_APP_PASSWORD") {
    if (-not $cfg[$name]) { throw "$name is missing from backend/.env" }
}
if (-not $cfg["PROVENANT_SUPABASE_SECRET_KEY"]) {
    throw "The Supabase secret key is missing from backend/.env. Run backend\scripts\set-signin.ps1 first."
}
foreach ($name in "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") {
    if (-not $web[$name]) { throw "$name is missing from frontend/.env.local" }
}

if (-not ($cfg["PROVENANT_S3_ACCESS_KEY_ID"] -and $cfg["PROVENANT_S3_SECRET_ACCESS_KEY"])) {
    Write-Host "Files are kept in a private Supabase Storage bucket. In Supabase: Storage -> S3 Configuration ->"
    Write-Host "Access keys -> New access key. Copy the access key ID and the secret access key."
    $keyId = (Read-Host "S3 access key ID").Trim()
    $keySecret = Read-Hidden "S3 secret access key (hidden)"
    if (-not $keyId -or -not $keySecret) { throw "Both S3 values are needed." }
    Save-EnvValues $envFile ([ordered]@{ "PROVENANT_S3_ACCESS_KEY_ID" = $keyId; "PROVENANT_S3_SECRET_ACCESS_KEY" = $keySecret })
    $cfg = Read-EnvFile $envFile
    Write-Host "Saved the S3 keys to backend/.env (git-ignored)."
}

# Database URLs through the Supabase session pooler: the owner for migrations, the app role for the API.
$python = Join-Path $backend ".venv\Scripts\python.exe"
Push-Location $backend
try {
    $urls = & $python -c "import sys; sys.path.insert(0, 'scripts'); import dev; o, a = dev.supabase_urls(); print(o); print(a)"
    if ($LASTEXITCODE -ne 0) { throw "Could not build the database URLs from backend/.env" }
} finally { Pop-Location }
$ownerUrl, $appUrl = @($urls)[-2], @($urls)[-1]

$appUrlPublic = "https://$WebApp.fly.dev"
$secrets = [ordered]@{
    "DATABASE_URL" = $appUrl
    "MIGRATION_DATABASE_URL" = $ownerUrl
    "PROVENANT_SUPABASE_URL" = $cfg["PROVENANT_SUPABASE_URL"]
    "PROVENANT_SUPABASE_SECRET_KEY" = $cfg["PROVENANT_SUPABASE_SECRET_KEY"]
    "PROVENANT_APP_URL" = $appUrlPublic
    "PROVENANT_S3_ENDPOINT" = $cfg["PROVENANT_SUPABASE_URL"].TrimEnd("/") + "/storage/v1/s3"
    "AWS_ACCESS_KEY_ID" = $cfg["PROVENANT_S3_ACCESS_KEY_ID"]
    "AWS_SECRET_ACCESS_KEY" = $cfg["PROVENANT_S3_SECRET_ACCESS_KEY"]
}
foreach ($name in "PROVENANT_SMTP_HOST", "PROVENANT_SMTP_PORT", "PROVENANT_SMTP_USER", "PROVENANT_SMTP_PASSWORD", "PROVENANT_SMTP_FROM") {
    if ($cfg[$name]) { $secrets[$name] = $cfg[$name] }
}
if ($env:ANTHROPIC_API_KEY) {  # only when already set in this window; never read from or written to a file
    $secrets["ANTHROPIC_API_KEY"] = $env:ANTHROPIC_API_KEY
    $secrets["PROVENANT_LEGAL_AGENT"] = "claude-api"
}

# ---- apps
foreach ($app in $ApiApp, $WebApp) {
    if ((Test-Fly status --app $app) -ne 0) {
        Write-Host "Creating the Fly app $app ..."
        & $fly apps create $app
        if ($LASTEXITCODE -ne 0) { throw "Could not create $app (the name may be taken: pass -ApiApp / -WebApp)" }
    }
}

Write-Host "Saving $($secrets.Count) settings to Fly's secret store for $ApiApp (values not shown) ..."
# Windows PowerShell can start text piped to a program with a UTF-8 byte-order mark, and Fly then reads the first name
# as "﻿DATABASE_URL" and refuses it. Both encodings must be UTF-8 without the mark for the pipe below.
$noBom = New-Object Text.UTF8Encoding $false
$savedInput = [Console]::InputEncoding
$OutputEncoding = $noBom
[Console]::InputEncoding = $noBom
try {
    ($secrets.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join "`n" | & $fly secrets import --app $ApiApp --stage
} finally { [Console]::InputEncoding = $savedInput }
if ($LASTEXITCODE -ne 0) { throw "Saving the secrets failed" }
if ($SecretsOnly) { Write-Host "Secrets saved; they apply on the next deploy."; exit 0 }

$buildFlags = @()
if ($NoDepot) { $buildFlags += "--depot=false" }

# ---- deploy (one machine each: the worker runs inside the API, so two API machines would mean two workers)
Push-Location $backend
try {
    & $fly deploy --app $ApiApp --ha=false @buildFlags
    if ($LASTEXITCODE -ne 0) { throw "The API deploy failed" }
} finally { Pop-Location }

Push-Location $frontend
try {
    & $fly deploy --app $WebApp --ha=false @buildFlags `
        --build-arg "PROVENANT_API_URL=https://$ApiApp.fly.dev" `
        --build-arg "NEXT_PUBLIC_SUPABASE_URL=$($web['NEXT_PUBLIC_SUPABASE_URL'])" `
        --build-arg "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$($web['NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY'])"
    if ($LASTEXITCODE -ne 0) { throw "The web deploy failed" }
} finally { Pop-Location }

Write-Host ""
Write-Host "Provenant is online at $appUrlPublic"
Write-Host "In Supabase -> Authentication -> URL Configuration, set the Site URL to $appUrlPublic and add"
Write-Host "$appUrlPublic/auth/callback to the Redirect URLs, so invitation links open the online app."
