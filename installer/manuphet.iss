; Manuphet Windows インストーラ定義（Inno Setup 6.x）
; https://jrsoftware.org/isinfo.php
;
; ビルド方法:
;   scripts\build_installer.ps1 を実行（PyInstaller → Inno Setup を一括ビルド）
;   または手動:
;     1. pyinstaller installer\run_web.spec      --distpath installer\dist --workpath installer\build
;     2. pyinstaller installer\setup_wizard.spec --distpath installer\dist --workpath installer\build
;     3. "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\manuphet.iss
;
; 出力: installer\Output\Manuphet_Setup_x.x.x.exe

#define AppName      "Manuphet"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
; このファイルの親 = project/
#define SourceDir    ".."
#define DistDir      "dist"

[Setup]
AppId={{B6F68B50-7575-4ADF-9D44-3C3DB11E8F94}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Manuphet Contributors
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#SourceDir}\installer\Output
OutputBaseFilename=Manuphet_Setup_{#AppVersion}
SetupIconFile={#SourceDir}\assets\manuphet.ico
UninstallDisplayIcon={app}\manuphet.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成"; GroupDescription: "追加タスク:"; Flags: unchecked
Name: "autostart";   Description: "Windows 起動時に自動起動する（推奨）"; GroupDescription: "サービス:"; Flags: checkedonce
Name: "runwizard";   Description: "インストール後にセットアップウィザードを起動する"; GroupDescription: "初回設定:"; Flags: checkedonce

[Files]
; PyInstaller ビルド済み exe（先に scripts\build_installer.ps1 を実行してください）
Source: "{#SourceDir}\installer\{#DistDir}\Manuphet_Web.exe";          DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\installer\{#DistDir}\Manuphet_Setup_Wizard.exe"; DestDir: "{app}"; Flags: ignoreversion

; スクリプト・設定テンプレート・アイコン
Source: "{#SourceDir}\scripts\register_startup.ps1";   DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceDir}\scripts\unregister_startup.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#SourceDir}\settings.example.json";          DestDir: "{app}";         Flags: ignoreversion
Source: "{#SourceDir}\mysql_config.example.json";      DestDir: "{app}";         Flags: ignoreversion
Source: "{#SourceDir}\assets\manuphet.ico";            DestDir: "{app}";         Flags: ignoreversion

; サンプルデータ
Source: "{#SourceDir}\examples\*"; DestDir: "{app}\examples"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\Manuphet Web（手動起動）";  Filename: "{app}\Manuphet_Web.exe"; WorkingDir: "{app}"; IconFilename: "{app}\manuphet.ico"
Name: "{group}\Manuphet をブラウザで開く";  Filename: "http://localhost:8000/"; IconFilename: "{app}\manuphet.ico"
Name: "{group}\セットアップウィザード";     Filename: "{app}\Manuphet_Setup_Wizard.exe"; WorkingDir: "{app}"; IconFilename: "{app}\manuphet.ico"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Manuphet"; Filename: "http://localhost:8000/"; IconFilename: "{app}\manuphet.ico"; Tasks: desktopicon

[Run]
; 自動起動タスクをタスクスケジューラに登録（SYSTEM アカウント・起動時・ウィンドウなし）
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\register_startup.ps1"" -InstallDir ""{app}"" -PythonExe ""{app}\Manuphet_Web.exe"""; \
  StatusMsg: "自動起動タスクを登録中..."; \
  Tasks: autostart; Flags: runhidden waituntilterminated

; セットアップウィザードを起動（インストール後・非同期）
Filename: "{app}\Manuphet_Setup_Wizard.exe"; \
  WorkingDir: "{app}"; \
  StatusMsg: "セットアップウィザードを起動中..."; \
  Tasks: runwizard; Flags: postinstall nowait

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\unregister_startup.ps1"""; \
  StatusMsg: "自動起動タスクを削除中..."; \
  RunOnceId: "UnregisterTask"; Flags: runhidden waituntilterminated

[Dirs]
Name: "{commonappdata}\Manuphet\data\config"
Name: "{commonappdata}\Manuphet\data\models"
Name: "{commonappdata}\Manuphet\data\logs"

[Code]
// settings.json が無ければテンプレートからコピーする
procedure CurStepChanged(CurStep: TSetupStep);
var
  SettingsFile, ExampleFile: string;
begin
  if CurStep = ssPostInstall then
  begin
    SettingsFile := ExpandConstant('{commonappdata}\Manuphet\data\config\settings.json');
    ExampleFile  := ExpandConstant('{app}\settings.example.json');
    if not FileExists(SettingsFile) and FileExists(ExampleFile) then
      CopyFile(ExampleFile, SettingsFile, False);
  end;
end;
