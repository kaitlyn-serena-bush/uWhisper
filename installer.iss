; uWhisper installer (Inno Setup 6). Build with:  powershell -ExecutionPolicy Bypass -File build_installer.ps1
;
; - Installs for the current user only: no admin prompt.
; - Creates Documents\uWhisper\audio and \text on this PC (never the OneDrive Documents).
; - The speech model (~1.6 GB) and, on NVIDIA PCs, GPU support (~553 MB) download on first run.
; - Uninstalling keeps recordings unless the user says otherwise.

#define AppName "uWhisper"
#define DistDir GetEnv("USERPROFILE") + "\.uwhisper\build\dist\uWhisper"
#define AppVersion GetVersionNumbersString(DistDir + "\uWhisper.exe")

[Setup]
AppId={{6F1E2B7A-5C3D-4E8F-9A21-7B4C0D9E3F15}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=uWhisper
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=uWhisper-Setup-{#AppVersion}
SetupIconFile=uWhisper.ico
UninstallDisplayIcon={app}\uWhisper.exe
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start uWhisper when I sign in to Windows"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Local Documents (not the OneDrive-redirected one). Never removed on uninstall.
Name: "{%USERPROFILE}\Documents\uWhisper\audio"; Flags: uninsneveruninstall
Name: "{%USERPROFILE}\Documents\uWhisper\text"; Flags: uninsneveruninstall

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\uWhisper.exe"; Comment: "Press your shortcut to record and transcribe"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\uWhisper.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "uWhisper"; ValueData: """{app}\uWhisper.exe"""; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "uWhisper"; \
    Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\uWhisper.exe"; Description: "Start uWhisper now"; Flags: nowait postinstall

[UninstallDelete]
; The whole app folder (also catches leftovers from older versions).
Type: filesandordirs; Name: "{app}"
; GPU support uWhisper downloaded for itself (~770 MB unpacked).
Type: filesandordirs; Name: "{%USERPROFILE}\.uwhisper\gpu"

[Messages]
FinishedLabel=uWhisper is installed.%n%nPress Ctrl+Shift+R anywhere to start recording, and again to stop. The text is pasted where you were typing.%n%nThe first start downloads the speech model (about 1.6 GB), so the first recording may take a few minutes. Look for the uWhisper icon in the taskbar tray (you may need to click the ^ arrow).

[Code]
procedure StopRunningApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM uWhisper.exe', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Sleep(500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Models: String;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  // Speech models live in the shared Hugging Face cache; only remove the ones uWhisper uses.
  Models := ExpandConstant('{%USERPROFILE}\.cache\huggingface\hub');
  if (not UninstallSilent) and DirExists(Models) and
     (MsgBox('Also delete the downloaded speech model (about 1.6 GB)?', mbConfirmation, MB_YESNO) = IDYES) then
  begin
    DelTree(Models + '\models--mobiuslabsgmbh--faster-whisper-large-v3-turbo', True, True, True);
    DelTree(Models + '\models--dropbox-dash--faster-whisper-large-v3-turbo', True, True, True);
    DelTree(Models + '\models--Systran--faster-whisper-large-v3', True, True, True);
    DelTree(Models + '\models--Systran--faster-distil-whisper-large-v3', True, True, True);
    DelTree(Models + '\models--Systran--faster-whisper-medium.en', True, True, True);
    DelTree(Models + '\models--Systran--faster-whisper-small.en', True, True, True);
    DelTree(Models + '\models--Systran--faster-whisper-base.en', True, True, True);
  end;
  if (not UninstallSilent) and
     (MsgBox('Also delete your recordings and transcripts in Documents\uWhisper?' + #13#10 +
             'Choose No to keep them.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    DelTree(ExpandConstant('{%USERPROFILE}\Documents\uWhisper'), True, True, True);
end;
