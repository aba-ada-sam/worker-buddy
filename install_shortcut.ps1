<#
  install_shortcut.ps1 -- One-click Start Menu shortcut for Worker Buddy.

  Creates  %APPDATA%\Microsoft\Windows\Start Menu\Programs\Worker Buddy.lnk
  pointing at run.bat in this folder, with the icon and a clean working
  directory. Per-user, no admin required.

  Optional: pass -Autostart to also drop the shortcut into the Startup
  folder so Worker Buddy launches at login.

  Run from this folder:
      powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1
      powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1 -Autostart

  To remove later, just delete the .lnk files this script created.
#>

[CmdletBinding()]
param(
    [switch]$Autostart
)

$ErrorActionPreference = "Stop"

# Resolve paths from the script's own location -- works no matter where
# you copied the project.
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$target     = Join-Path $projectDir "run.bat"
$iconPath   = Join-Path $projectDir "icon.ico"

if (-not (Test-Path $target)) {
    Write-Error "run.bat not found at $target"
    exit 1
}

# Per-user Start Menu Programs folder
$startMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$startupDir   = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"

function New-WBShortcut {
    param(
        [Parameter(Mandatory=$true)] [string]$Folder,
        [string]$Name = "Worker Buddy.lnk"
    )
    if (-not (Test-Path $Folder)) {
        New-Item -ItemType Directory -Path $Folder -Force | Out-Null
    }
    $path = Join-Path $Folder $Name
    $wsh  = New-Object -ComObject WScript.Shell
    $sc   = $wsh.CreateShortcut($path)
    $sc.TargetPath        = $target
    $sc.WorkingDirectory  = $projectDir
    $sc.WindowStyle       = 7   # 7 = minimized; run.bat fires pythonw.exe and exits
    $sc.Description       = "Worker Buddy -- AI desktop / browser agent"
    if (Test-Path $iconPath) { $sc.IconLocation = $iconPath }
    $sc.Save()
    Write-Host "  created: $path"
}

Write-Host "Installing Worker Buddy shortcut..."
New-WBShortcut -Folder $startMenuDir

if ($Autostart) {
    Write-Host "Installing autostart entry..."
    New-WBShortcut -Folder $startupDir -Name "Worker Buddy.lnk"
    Write-Host "Worker Buddy will launch at next login."
} else {
    Write-Host ""
    Write-Host "Tip: re-run with -Autostart to also start at login."
}

Write-Host ""
Write-Host "Done. Search the Start menu for `"Worker Buddy`"."
