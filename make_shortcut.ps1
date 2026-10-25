# Creates uWhisper shortcuts (Start Menu + this folder). Run again if you move the folder.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
$pyw = Join-Path $here ".venv\Scripts\pythonw.exe"
$ico = Join-Path $here "uWhisper.ico"

# Build the icon from the same art the tray uses (the flame logo).
& $py (Join-Path $here "uwhisper_logo.py")

$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) "uWhisper.lnk"
foreach ($path in @($startMenu, (Join-Path $here "uWhisper.lnk"))) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $pyw
    $s.Arguments = "`"$(Join-Path $here 'uWhisper.pyw')`""
    $s.WorkingDirectory = $here
    $s.IconLocation = $ico
    $s.Description = "uWhisper - Ctrl+Shift+R to record and transcribe"
    $s.Save()
    "created $path"
}
