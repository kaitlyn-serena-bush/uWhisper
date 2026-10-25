# Builds dist\uWhisper-Setup-<version>.exe: the one file to send to friends.
#   powershell -ExecutionPolicy Bypass -File build_installer.ps1
# Builds the app first (build_exe.ps1), then packs it with Inno Setup 6.
# Get Inno Setup with:  winget install --id JRSoftware.InnoSetup -e --scope user
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

& (Join-Path $here "build_exe.ps1")
if ($LASTEXITCODE -and $LASTEXITCODE -ge 8) { throw "app build failed" }

$iscc = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found - winget install --id JRSoftware.InnoSetup -e --scope user" }

& (Join-Path $here ".venv\Scripts\python.exe") (Join-Path $here "installer_art.py")
if ($LASTEXITCODE -ne 0) { throw "installer images failed" }
& $iscc /Q (Join-Path $here "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Get-ChildItem (Join-Path $here "dist\uWhisper-Setup-*.exe") | Sort-Object LastWriteTime |
    Select-Object -Last 1 | ForEach-Object { "{0}  ({1:N0} MB)" -f $_.FullName, ($_.Length / 1MB) }
