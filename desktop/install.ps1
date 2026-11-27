# Install Provenant as an app on this PC: "Provenant" and "Stop Provenant" in the Start menu, and "Provenant" on the
# desktop. Nothing is copied: the shortcuts run this checkout, so the app always reflects the code in this folder.
# Usage:  powershell -ExecutionPolicy Bypass -File .\desktop\install.ps1      (add -Uninstall to remove the shortcuts)
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$root = Split-Path $here -Parent
$icon = Join-Path $root "frontend\public\icons\provenant.ico"
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Provenant"
$desktop = [Environment]::GetFolderPath("Desktop")
$links = @{
    (Join-Path $startMenu "Provenant.lnk") = "start-provenant.ps1"
    (Join-Path $startMenu "Stop Provenant.lnk") = "stop-provenant.ps1"
    (Join-Path $desktop "Provenant.lnk") = "start-provenant.ps1"
}

if ($Uninstall) {
    foreach ($link in $links.Keys) { if (Test-Path $link) { Remove-Item $link } }
    if ((Test-Path $startMenu) -and -not (Get-ChildItem $startMenu)) { Remove-Item $startMenu }
    Write-Host "Removed the Provenant shortcuts."
    exit 0
}

New-Item -ItemType Directory -Force $startMenu | Out-Null
$shell = New-Object -ComObject WScript.Shell
foreach ($link in $links.Keys) {
    $script = Join-Path $here $links[$link]
    $shortcut = $shell.CreateShortcut($link)
    # wscript runs the hidden launcher so no console window flashes up.
    $shortcut.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
    $shortcut.Arguments = "`"$(Join-Path $here 'run-hidden.vbs')`" `"$script`""
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = if ($links[$link] -like "stop*") { "Stop the Provenant servers" } else { "Open Provenant" }
    $shortcut.Save()
    Write-Host "Created $link"
}
Write-Host "Provenant is installed. Open it from the Start menu or the desktop."
