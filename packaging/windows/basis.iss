; Basis installer (Inno Setup 6). Built by .github/workflows/build.yml after tools/build_release.py has laid out
; dist/stage/app (the program) and dist/stage/python (the embeddable Python it runs on).
; Installs for the current user only, with no administrator rights. The user's data lives in %LOCALAPPDATA%\Basis,
; outside this folder, and is left alone by install, update and uninstall.

#define AppVersion GetEnv("BASIS_VERSION")

[Setup]
AppId={{8F3B5C1E-6A2D-4E9B-9C47-B45151A0C0DE}
AppName=Basis
AppVersion={#AppVersion}
AppVerName=Basis {#AppVersion}
AppPublisher=Connor Aherne
AppPublisherURL=https://connoraherne.com
AppSupportURL=https://github.com/Connor-03/Basis
DefaultDirName={localappdata}\Programs\Basis
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Basis-Setup
SetupIconFile=..\..\web\basis.ico
UninstallDisplayIcon={app}\app\web\basis.ico
UninstallDisplayName=Basis
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"

[Files]
Source: "..\..\dist\stage\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion
Source: "..\..\dist\stage\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Basis"; Filename: "{app}\python\pythonw.exe"; Parameters: "-B -X utf8 ""{app}\app\basis.pyw"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\web\basis.ico"; Comment: "Portfolio dashboard"
Name: "{autodesktop}\Basis"; Filename: "{app}\python\pythonw.exe"; Parameters: "-B -X utf8 ""{app}\app\basis.pyw"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\web\basis.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-B -X utf8 ""{app}\app\basis.pyw"""; WorkingDir: "{app}\app"; Description: "Open Basis"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"

[Code]
// Stop a running copy before its files are replaced or removed: only processes started from this install's folder.
procedure StopBasis();
var
  Code: Integer;
  Cmd: String;
begin
  Cmd := '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like ''' +
         ExpandConstant('{app}') + '\python\*'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"';
  Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopBasis();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopBasis();
  Result := True;
end;
