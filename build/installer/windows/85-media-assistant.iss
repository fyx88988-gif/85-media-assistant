#ifndef AppVersion
  #error AppVersion must be provided
#endif
#ifndef SourceRoot
  #error SourceRoot must be provided
#endif
#ifndef OutputRoot
  #error OutputRoot must be provided
#endif

#define AppName "85数字多媒体下载助手"
#define LauncherName "85数字多媒体下载助手.exe"

[Setup]
AppId={{A850D852-85D0-4A85-8515-85D185D185D1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=浙江八五数字科技有限公司
DefaultDirName={localappdata}\85Digital\MediaAssistant
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
OutputDir={#OutputRoot}
OutputBaseFilename=85数字多媒体下载助手-Setup-{#AppVersion}
UninstallDisplayName={#AppName}
DisableProgramGroupPage=yes

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\launcher\{#LauncherName}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "85DigitalMediaAssistant"; ValueData: """{app}\launcher\{#LauncherName}"" --background"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\launcher\{#LauncherName}"; Description: "打开 {#AppName}"; Flags: nowait postinstall skipifsilent
