[Setup]
AppId=ZERMORE.ResearchDesk
AppName=Research Desk
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\ResearchDesk
DefaultGroupName=Research Desk
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=ResearchDesk-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ResearchDesk.exe
DisableProgramGroupPage=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Research Desk"; Filename: "{app}\ResearchDesk.exe"
Name: "{autoprograms}\Stop Research Desk"; Filename: "{app}\ResearchDesk.exe"; Parameters: "--stop"
Name: "{autodesktop}\Research Desk"; Filename: "{app}\ResearchDesk.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "{cm:CreateDesktopIcon}"

[Run]
Filename: "{app}\ResearchDesk.exe"; Description: "{cm:LaunchProgram,Research Desk}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\ResearchDesk.exe"; Parameters: "--stop --no-browser"; RunOnceId: "StopResearchDesk"; Flags: waituntilterminated

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  Launcher: String;
begin
  Result := '';
  Launcher := ExpandConstant('{app}\ResearchDesk.exe');
  if FileExists(Launcher) then
    if not Exec(Launcher, '--stop --no-browser', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      Result := 'Stop the running Research Desk before installing this update.';
end;
