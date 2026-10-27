; 额度悬浮窗 Inno Setup 安装脚本
; 默认安装到 %LocalAppData%\LLM Usage Widget（无需管理员），用户可在向导中改到任意位置
; 用户数据（config.json / history.jsonl / error.log）不放在安装目录，
; 而是固定在 %APPDATA%\LLM Usage Widget，重装或换目录都不会丢

#define MyAppName "额度悬浮窗"
#define MyAppNameEn "LLM Usage Widget"
#define MyAppVersion "2.8.1"
#define MyAppPublisher "joeyxd19"
#define MyAppURL "https://github.com/joeyxd19/llm-usage-widget"
#define MyAppExeName "llm-usage-widget.exe"

[Setup]
AppId={{F4A8B2C1-7D3E-4A5F-9B2C-1E7D6F8A3B4C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\{#MyAppNameEn}
DisableDirPage=no
UsePreviousAppDir=yes
DirExistsWarning=auto
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=llm-usage-widget_v{#MyAppVersion}_setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "autostart"; Description: "开机自动启动"; GroupDescription: "附加任务:"; Flags: unchecked
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "..\dist\onedir\llm-usage-widget\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\onedir\llm-usage-widget\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
