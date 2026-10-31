; 额度悬浮窗 Inno Setup 安装脚本
; 默认安装到 %LocalAppData%\LLM Usage Widget（无需管理员），用户可在向导中改到任意位置
; 用户数据（config.json / history.jsonl / error.log）不放在安装目录，
; 而是固定在 %APPDATA%\LLM Usage Widget，重装或换目录都不会丢

#define MyAppName "额度悬浮窗"
#define MyAppNameEn "LLM Usage Widget"
#ifndef MyAppVersion
  #define MyAppVersion "3.1.1"
#endif
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

[Code]
// 安装前检测「额度悬浮窗」是否正在运行：正在运行就询问用户是否结束它。
// 检测用程序自身的单实例互斥体（与 src/usage_widget.pyw 中的名字保持一致），
// 比按进程名查更准确，也不会误杀同名的其他程序。
const
  AppMutexName = 'UsageWidget_ZV_SingleInstance';
  AppExeName = '{#MyAppExeName}';

function IsAppRunning(): Boolean;
begin
  Result := CheckForMutexes(AppMutexName);
end;

procedure KillApp(Force: Boolean);
var
  Params: String;
  ResultCode: Integer;
begin
  // 先发普通关闭请求（程序会正常退出并保存窗口位置），必要时再加 /F 强杀
  Params := '/T /IM ' + AppExeName;
  if Force then
    Params := '/F ' + Params;
  Exec(ExpandConstant('{sys}\taskkill.exe'), Params, '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
end;

function WaitAppExit(MaxMs: Integer): Boolean;
var
  Waited: Integer;
begin
  Waited := 0;
  while (Waited < MaxMs) and IsAppRunning() do
  begin
    Sleep(200);
    Waited := Waited + 200;
  end;
  Result := not IsAppRunning();
end;

function InitializeSetup(): Boolean;
var
  Reply: Integer;
begin
  Result := True;
  if not IsAppRunning() then
    Exit;

  if not WizardSilent() then
  begin
    Reply := MsgBox('检测到「额度悬浮窗」正在运行。' + #13#10 + #13#10 +
                    '安装新版本需要先结束正在运行的程序，是否现在自动结束并继续安装？',
                    mbConfirmation, MB_YESNO);
    if Reply <> IDYES then
    begin
      MsgBox('安装已取消。' + #13#10 + #13#10 +
             '请右键点击悬浮窗选择「退出」后重新运行安装程序。',
             mbInformation, MB_OK);
      Result := False;
      Exit;
    end;
  end;

  KillApp(False);
  if not WaitAppExit(2000) then
  begin
    KillApp(True);
    if not WaitAppExit(3000) then
    begin
      MsgBox('未能结束正在运行的「额度悬浮窗」。' + #13#10 + #13#10 +
             '请右键点击悬浮窗选择「退出」后重新运行安装程序。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;
