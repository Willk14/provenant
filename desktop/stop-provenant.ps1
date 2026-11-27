# Stop the Provenant API (port 8000) and web app (port 3100) started by start-provenant.ps1.
# Only processes that belong to this checkout are stopped: another program that happens to use either port is left
# alone. The embedded Postgres server keeps running in the background; the next start attaches to it.
$root = (Split-Path $PSScriptRoot -Parent).TrimEnd("\")

function Get-ProvenantOwner([int]$id) {
    # The listening process, or the nearest parent, whose program or command line is inside this checkout. A venv's
    # python.exe hands over to the base interpreter, and npm starts node, so the match may be a few levels up.
    for ($depth = 0; $depth -lt 5 -and $id; $depth++) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
        if (-not $p) { return $null }
        foreach ($text in $p.ExecutablePath, $p.CommandLine) {
            if ($text -and $text.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $p.ProcessId }
        }
        $id = $p.ParentProcessId
    }
    return $null
}

foreach ($port in 8000, 3100) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($id in $listeners) {
        $owner = Get-ProvenantOwner $id
        if ($owner) {
            # taskkill /T also stops the child processes (npm's node, uvicorn's workers).
            & taskkill.exe /PID $owner /T /F | Out-Null
        } else {
            Write-Host "Port $port is used by a program outside $root; leaving it running."
        }
    }
}
