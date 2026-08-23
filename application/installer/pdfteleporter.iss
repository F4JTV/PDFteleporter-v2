; PDF Teleporter installer -- requires Inno Setup 7.0 or later.
;
; Inno Setup 7 introduced SetupArchitecture, used below to produce a native
; 64-bit installer. Under Inno Setup 6 that directive does not exist and
; compilation fails with an unknown-directive error rather than silently
; building something different, which is the behaviour we want.
;
; Build:
;   iscc installer\pdfteleporter.iss
;
; The application must already be built into dist\PDFteleporter\ by
; PyInstaller. build.cmd in the project root does both steps in order.

#define AppName        "PDF Teleporter"
#define AppVersion     "2.0.0"
#define AppPublisher   "PDF Teleporter project"
#define AppExeName     "PDFteleporter.exe"
#define CliExeName     "psditool.exe"
#define SourceDir      "..\dist\PDFteleporter"

[Setup]
AppId={{8F3A6C21-4D7E-4B92-A5C3-1E7D9B240A66}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\PDFteleporter
DefaultGroupName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=PDFteleporter-{#AppVersion}-setup
SetupIconFile=..\assets\pdfteleporter.ico
UninstallDisplayIcon={app}\{#AppExeName}
LicenseFile=..\LICENSE.txt

; PyQt6 requires Windows 10 or later; the Inno default of 6.1sp1 would let
; Setup run on Windows 7 and produce an application that cannot start.
MinVersion=10.0

; Inno Setup 7: build a native 64-bit installer. This also defaults
; ArchitecturesAllowed and ArchitecturesInstallIn64BitMode to x64compatible,
; which matches both x64 Windows and Arm64 Windows 11 running x64 under
; emulation -- the plain x64 identifier would exclude Arm64 machines.
SetupArchitecture=x64

; Offer a per-user install. An operator on a locked-down machine in a
; coordination room may have no administrator rights, and this application
; needs none: it writes only to HKEY_CURRENT_USER.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Follow the system light/dark setting, matching the application itself.
WizardStyle=modern dynamic

Compression=lzma2/max
SolidCompression=yes

; --- Code signing ----------------------------------------------------------
; Authenticode signing is what SmartScreen actually looks at. Version metadata
; and a clean icon do nothing for it. Without a signature, Windows shows the
; "Windows protected your PC" screen on first run of the downloaded installer,
; whatever else the file contains.
;
; To sign, define a Sign Tool named "signtool" in the Compiler IDE under
; Tools > Configure Sign Tools, then uncomment the two directives below.
; SignedUninstaller matters as much as SignTool: the uninstaller is generated
; on the target machine at install time, so it is not covered by signing the
; installer beforehand.
;
; SignTool=signtool
; SignedUninstaller=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
en.ShellTask=Add "compress to .psdi" and "rebuild PDF" to the Explorer context menu
fr.ShellTask=Ajouter « compresser en .psdi » et « recomposer le PDF » au menu contextuel de l'Explorateur
en.DesktopTask=Create a desktop shortcut
fr.DesktopTask=Créer un raccourci sur le Bureau
en.Win11Note=On Windows 11 the new entries appear under "Show more options".
fr.Win11Note=Sous Windows 11, les nouvelles entrées apparaissent sous « Afficher plus d'options ».

[Tasks]
Name: "shellmenu"; Description: "{cm:ShellTask}"; GroupDescription: "Explorer:"
Name: "desktopicon"; Description: "{cm:DesktopTask}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
; The registration scope must match the install mode. In administrative mode
; Setup is elevated, so HKEY_CURRENT_USER would be the administrator's hive
; rather than the operator's: the menu would be registered for an account
; nobody uses, and an elevated uninstall could never find the operator's keys
; to remove them. Administrative installs therefore register under
; HKEY_LOCAL_MACHINE, which every account sees.
;
; The runasoriginaluser flag is deliberately absent. It is valid only in this
; section, has no counterpart in the uninstall one, and choosing the scope by
; install mode makes it unnecessary in both.
Filename: "{app}\{#CliExeName}"; Parameters: "shell install --scope machine"; \
    Tasks: shellmenu; Check: IsAdminInstallMode; \
    Flags: runhidden waituntilterminated; \
    StatusMsg: "Registering Explorer entries..."

Filename: "{app}\{#CliExeName}"; Parameters: "shell install --scope user"; \
    Tasks: shellmenu; Check: not IsAdminInstallMode; \
    Flags: runhidden waituntilterminated; \
    StatusMsg: "Registering Explorer entries..."

Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; These entries run before the files are removed, so psditool.exe is still
; present. Leaving the keys behind would give Explorer two dead menu entries
; pointing at a deleted executable.
;
; No scope is passed, so the tool sweeps both. That also clears keys left by
; an earlier install made in the other mode.
Filename: "{app}\{#CliExeName}"; Parameters: "shell uninstall"; \
    RunOnceId: "RemoveShellMenu"; \
    Flags: runhidden waituntilterminated

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Windows 11 hides legacy verbs behind "Show more options". Saying so once,
  // at the end, prevents the support question that otherwise always follows.
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('shellmenu') then
  begin
    if GetWindowsVersion >= $0A0055F0 then  // build 22000, Windows 11
      SuppressibleMsgBox(ExpandConstant('{cm:Win11Note}'), mbInformation, MB_OK, IDOK);
  end;
end;
