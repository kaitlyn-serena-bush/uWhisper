# Builds uWhisper.exe and installs it to %LOCALAPPDATA%\Programs\uWhisper.
# Run again after changing the code:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
# The build happens outside OneDrive (it's ~2 GB of GPU libraries).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
$out = Join-Path $env:USERPROFILE ".uwhisper\build"
$dest = Join-Path $env:LOCALAPPDATA "Programs\uWhisper"

& $py (Join-Path $here "uwhisper_logo.py")   # refresh the icon

& $py -m PyInstaller --noconfirm --clean --windowed --name uWhisper `
    --icon (Join-Path $here "uWhisper.ico") `
    --version-file (Join-Path $here "version_info.txt") `
    --add-data "$(Join-Path $here 'uWhisper.ico');." `
    --collect-data faster_whisper `
    --collect-binaries ctranslate2 `
    --collect-binaries nvidia.cublas `
    --collect-binaries nvidia.cudnn `
    --collect-binaries nvidia.cuda_nvrtc `
    --collect-binaries onnxruntime `
    --collect-binaries _sounddevice_data `
    --collect-binaries _soundfile_data `
    --hidden-import pystray._win32 `
    --distpath "$out\dist" --workpath "$out\work" --specpath $out `
    (Join-Path $here "uWhisper.pyw")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Stop a running copy, then install.
Get-Process uWhisper -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name like 'pythonw%'" |
    Where-Object { $_.CommandLine -like '*uWhisper.pyw*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Milliseconds 800
robocopy "$out\dist\uWhisper" $dest /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copy failed ($LASTEXITCODE)" }
"installed to $dest"

# Shortcuts (Start Menu + this folder) point at the exe.
$exe = Join-Path $dest "uWhisper.exe"
$shell = New-Object -ComObject WScript.Shell
foreach ($path in @((Join-Path ([Environment]::GetFolderPath('Programs')) "uWhisper.lnk"), (Join-Path $here "uWhisper.lnk"))) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $exe
    $s.Arguments = ""
    $s.WorkingDirectory = $dest
    $s.IconLocation = "$exe,0"
    $s.Description = "uWhisper - press your shortcut to record and transcribe"
    $s.Save()
}

# If "Start with Windows" is on, point it at the exe too.
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $run -Name uWhisper -ErrorAction SilentlyContinue) {
    Set-ItemProperty -Path $run -Name uWhisper -Value "`"$exe`""
    "updated Start with Windows"
}
