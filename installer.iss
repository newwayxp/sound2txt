; ============================================================
;  Sound2Text Installer Script
;  Inno Setup 6.x  —  build_installer.bat で .exe を生成
; ============================================================

#define AppName    "Sound2Text"
#define AppVersion "1.0.0"
#define AppPublisher "Sound2Text"

[Setup]
AppId={{B7F3A2E1-4C9D-4B8E-A1F5-2D6E8C0F3A7B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=Sound2Text_Setup_{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "ui.py";            DestDir: "{app}"; Flags: ignoreversion
Source: "start.py";         DestDir: "{app}"; Flags: ignoreversion
Source: "recorder.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "transcriber.py";   DestDir: "{app}"; Flags: ignoreversion
Source: "summarizer.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "device_utils.py";  DestDir: "{app}"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "setup.bat";        DestDir: "{app}"; Flags: ignoreversion
; API キーを含まないデフォルト設定を初回のみインストール（ユーザー設定を上書きしない）
Source: "config_default.ini"; DestDir: "{app}"; DestName: "config.ini"; Flags: ignoreversion onlyifdoesntexist
Source: "vocabulary.txt";     DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#AppName}\{#AppName}"; Filename: "{code:GetPythonW}"; Parameters: "-X utf8 ""{app}\ui.py"""; WorkingDir: "{app}"; Comment: "音声文字起こし・会议纪要生成ツール"
Name: "{autoprograms}\{#AppName}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{code:GetPythonW}"; Parameters: "-X utf8 ""{app}\ui.py"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "pip"; Parameters: "install -r ""{app}\requirements.txt"""; WorkingDir: "{app}"; StatusMsg: "Python パッケージをインストール中..."; Description: "Python パッケージをインストール (faster-whisper, customtkinter 等)"; Flags: postinstall waituntilterminated runascurrentuser
Filename: "winget"; Parameters: "install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements"; StatusMsg: "ffmpeg をインストール中..."; Description: "ffmpeg をインストール (音声変換に必要)"; Flags: postinstall waituntilterminated runascurrentuser skipifsilent
Filename: "{code:GetPythonW}"; Parameters: "-X utf8 ""{app}\ui.py"""; WorkingDir: "{app}"; Description: "{#AppName} を起動する"; Flags: postinstall nowait skipifsilent unchecked

[Code]
// python.exe のフルパスを取得する
function FindPython: String;
var
  ResultCode: Integer;
  TempFile: String;
  Lines: TArrayOfString;
begin
  Result := '';
  TempFile := ExpandConstant('{tmp}\py_path.txt');
  if Exec('cmd', '/c python -c "import sys; print(sys.executable)" > "' + TempFile + '" 2>nul',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if (ResultCode = 0) and LoadStringsFromFile(TempFile, Lines) and (GetArrayLength(Lines) > 0) then
      Result := Trim(Lines[0]);
  end;
  DeleteFile(TempFile);
end;

// pythonw.exe のパスを返す（コンソールウィンドウが開かない起動用）
function GetPythonW(Param: String): String;
var
  PyPath: String;
begin
  PyPath := FindPython;
  if PyPath <> '' then
    Result := ChangeFileExt(PyPath, 'w.exe')
  else
    Result := 'pythonw.exe';
end;

// Python がインストールされているか確認
function IsPythonInstalled: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('python', '--version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

// セットアップ開始前に Python を確認
function InitializeSetup: Boolean;
var
  ErrCode: Integer;
begin
  Result := True;
  if not IsPythonInstalled then
  begin
    if MsgBox('Python が見つかりません。' + #13#10 +
              '{#AppName} には Python 3.8 以上が必要です。' + #13#10 + #13#10 +
              'Python ダウンロードページを開きますか？' + #13#10 +
              '（インストール時に "Add Python to PATH" にチェックを入れてください）',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://www.python.org/downloads/', '', '', SW_SHOWNORMAL, ewNoWait, ErrCode);
    Result := False;
  end;
end;
