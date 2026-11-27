' Runs a PowerShell script with no console window. Used by the Provenant shortcuts (see install.ps1).
If WScript.Arguments.Count <> 1 Then WScript.Quit 1
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & WScript.Arguments(0) & """", 0, False
