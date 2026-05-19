#define AppVersion GetEnv("BASECLAW_INSTALLER_VERSION")
#if AppVersion == ""
#define AppVersion "0.1.0-alpha"
#endif

#define SourceDir "..\dist\windows-installer-stage"

[Setup]
AppId={{7D8D98D7-380B-4C04-9819-A42B283D34BA}
AppName=BaseClaw
AppVersion={#AppVersion}
AppPublisher=BaseClaw
DefaultDirName={localappdata}\BaseClaw
DefaultGroupName=BaseClaw
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=BaseClawSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BaseClaw Installer Wizard"; Filename: "{app}\install-wizard.cmd"; WorkingDir: "{app}"
Name: "{group}\BaseClaw UI"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\start-ui.ps1"""; WorkingDir: "{app}"
Name: "{autodesktop}\BaseClaw"; Filename: "{app}\install-wizard.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\install-wizard.cmd"; Description: "Open BaseClaw installer wizard"; Flags: postinstall shellexec skipifsilent
