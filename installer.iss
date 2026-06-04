; ============================================================
;  Sound2Text Installer Script  v1.3.11
;  Inno Setup 6.x  --  build with build_installer.bat
;
;  Dependency check flow:
;    1. Check winget availability
;    2. Check Python 3.10+
;       If missing: offer auto-install via winget
;       If no winget: open download page
;    3. Post-install: auto setup pip + ffmpeg
; ============================================================

#define AppName    "Sound2Text"
#define AppVersion "1.3.11"
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
; ── GUI + architecture ─────────────────────────────────────────────────────────
Source: "ui_qt.py";       DestDir: "{app}"; Flags: ignoreversion
Source: "widgets_qt.py";  DestDir: "{app}"; Flags: ignoreversion
Source: "presenter.py";   DestDir: "{app}"; Flags: ignoreversion
Source: "appconfig.py";   DestDir: "{app}"; Flags: ignoreversion
Source: "i18n.py";        DestDir: "{app}"; Flags: ignoreversion
; ── Recording / transcription pipeline ────────────────────────────────────────
Source: "start.py";       DestDir: "{app}"; Flags: ignoreversion
Source: "recorder.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "mic_recorder.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "transcriber.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "summarizer.py";  DestDir: "{app}"; Flags: ignoreversion
Source: "device_utils.py"; DestDir: "{app}"; Flags: ignoreversion
; ── Tools ──────────────────────────────────────────────────────────────────────
Source: "debug_modules.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "record_test.py"; DestDir: "{app}"; Flags: ignoreversion
; ── Setup / config ─────────────────────────────────────────────────────────────
Source: "requirements.txt";   DestDir: "{app}"; Flags: ignoreversion
Source: "setup.bat";          DestDir: "{app}"; Flags: ignoreversion
Source: "config_default.ini"; DestDir: "{app}"; DestName: "config.ini"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#AppName}\{#AppName}";                          Filename: "{code:GetPythonW}"; Parameters: "-X utf8 ""{app}\ui_qt.py"""; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName}\{cm:UninstallProgram,{#AppName}}";    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";                                       Filename: "{code:GetPythonW}"; Parameters: "-X utf8 ""{app}\ui_qt.py"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Upgrade pip then install packages
Filename: "python"; Parameters: "-m pip install --upgrade pip --quiet"; WorkingDir: "{app}"; StatusMsg: "Upgrading pip..."; Flags: postinstall waituntilterminated runascurrentuser
Filename: "pip"; Parameters: "install -r ""{app}\requirements.txt"""; WorkingDir: "{app}"; StatusMsg: "Installing Python packages (faster-whisper, PyQt6 ...)"; Description: "Install Python packages (faster-whisper, PyQt6, etc.)"; Flags: postinstall waituntilterminated runascurrentuser
; ffmpeg: install if not present, skip if already installed
Filename: "powershell"; Parameters: "-NoProfile -Command ""if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {{ winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements }} else {{ Write-Host 'ffmpeg already installed' }}"""; StatusMsg: "Checking ffmpeg..."; Description: "Install ffmpeg (required for audio processing)"; Flags: postinstall waituntilterminated runascurrentuser
; Launch app after install (optional)
Filename: "{code:GetPythonW}"; Parameters: "-X utf8 ""{app}\ui_qt.py"""; WorkingDir: "{app}"; Description: "Launch {#AppName} now"; Flags: postinstall nowait skipifsilent unchecked

; ============================================================
[Code]
// --- Utility functions ---

// Run command and capture first line of stdout
function RunAndCapture(const Cmd, Args: String): String;
var
  ResultCode: Integer;
  TempFile: String;
  Lines: TArrayOfString;
begin
  Result := '';
  TempFile := ExpandConstant('{tmp}\cap_out.txt');
  Exec('cmd', '/c ' + Cmd + ' ' + Args + ' > "' + TempFile + '" 2>&1',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if LoadStringsFromFile(TempFile, Lines) and (GetArrayLength(Lines) > 0) then
    Result := Trim(Lines[0]);
  DeleteFile(TempFile);
end;

// --- winget check ---

function IsWingetAvailable: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('winget', '--version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

// --- Python check ---

// Get Python version string (e.g. "Python 3.11.9")
function GetPythonVersionStr: String;
begin
  Result := RunAndCapture('python', '--version');
end;

// Check Python >= 3.10
function IsPythonVersionOK: Boolean;
var
  VerStr: String;
  DotPos: Integer;
  Major, Minor: Integer;
begin
  Result := False;
  VerStr := GetPythonVersionStr;
  // "Python 3.11.9" -> strip "Python " prefix, parse major.minor
  if (Length(VerStr) > 7) and (Copy(VerStr, 1, 7) = 'Python ') then
  begin
    VerStr := Copy(VerStr, 8, Length(VerStr));   // "3.11.9"
    DotPos := Pos('.', VerStr);
    if DotPos > 0 then
    begin
      Major := StrToIntDef(Copy(VerStr, 1, DotPos - 1), 0);
      VerStr := Copy(VerStr, DotPos + 1, Length(VerStr));  // "11.9"
      DotPos := Pos('.', VerStr);
      if DotPos > 0 then
        Minor := StrToIntDef(Copy(VerStr, 1, DotPos - 1), 0)
      else
        Minor := StrToIntDef(VerStr, 0);
      Result := (Major > 3) or ((Major = 3) and (Minor >= 10));
    end;
  end;
end;

// Install Python 3.11 via winget
function TryInstallPython: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('winget',
    'install Python.Python.3.11 --accept-package-agreements --accept-source-agreements',
    '', SW_SHOW, ewWaitUntilTerminated, ResultCode)
    and (ResultCode = 0);
end;

// --- Resolve python.exe / pythonw.exe path ---

function FindPython: String;
var
  ResultCode: Integer;
  TempFile: String;
  Lines: TArrayOfString;
begin
  Result := '';
  TempFile := ExpandConstant('{tmp}\py_path.txt');
  if Exec('cmd',
          '/c python -c "import sys; print(sys.executable)" > "' + TempFile + '" 2>nul',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if (ResultCode = 0) and LoadStringsFromFile(TempFile, Lines)
       and (GetArrayLength(Lines) > 0) then
      Result := Trim(Lines[0]);
  end;
  DeleteFile(TempFile);
end;

// Return pythonw.exe path (no console window)
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

// --- Pre-install dependency checks ---

function InitializeSetup: Boolean;
var
  ErrCode: Integer;
  HasWinget, HasPython: Boolean;
  PyVerStr, Msg: String;
begin
  Result := True;
  HasWinget := IsWingetAvailable;
  HasPython := IsPythonVersionOK;
  PyVerStr  := GetPythonVersionStr;

  // Python missing or version too old
  if not HasPython then
  begin
    if HasWinget then
    begin
      // Offer auto-install if winget is available
      if PyVerStr <> '' then
        Msg := 'Python 3.10 or later is required.' + #13#10 +
               'Detected: ' + PyVerStr + #13#10 + #13#10
      else
        Msg := 'Python is not installed.' + #13#10 + #13#10;

      Msg := Msg + 'Would you like to install Python 3.11 automatically via winget?';

      if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDYES then
      begin
        MsgBox('Installing Python 3.11 via winget.' + #13#10 +
               'This may take a few minutes. Click OK to continue.',
               mbInformation, MB_OK);

        if TryInstallPython then
        begin
          if IsPythonVersionOK then
            MsgBox('Python installed successfully!', mbInformation, MB_OK)
          else
          begin
            MsgBox('Python was installed but is not yet recognized.' + #13#10 +
                   'Please restart your PC and run the installer again.',
                   mbError, MB_OK);
            Result := False;
          end;
        end else
        begin
          MsgBox('Python installation failed.' + #13#10 +
                 'Please install Python 3.10+ manually from:' + #13#10 +
                 'https://www.python.org/downloads/' + #13#10 +
                 '(Check "Add Python to PATH" during installation)',
                 mbError, MB_OK);
          ShellExec('open', 'https://www.python.org/downloads/', '', '',
                    SW_SHOWNORMAL, ewNoWait, ErrCode);
          Result := False;
        end;
      end else
      begin
        // User chose No -> open download page and abort
        ShellExec('open', 'https://www.python.org/downloads/', '', '',
                  SW_SHOWNORMAL, ewNoWait, ErrCode);
        Result := False;
      end;
    end else
    begin
      // No winget available
      if PyVerStr <> '' then
        Msg := 'Python 3.10 or later is required.' + #13#10 +
               'Detected: ' + PyVerStr + #13#10 + #13#10
      else
        Msg := 'Python is not installed.' + #13#10 + #13#10;

      Msg := Msg +
        'Please install Python 3.10+ from python.org' + #13#10 +
        'and check "Add Python to PATH" during installation.' + #13#10 + #13#10 +
        'Open the download page now?';

      if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDYES then
        ShellExec('open', 'https://www.python.org/downloads/', '', '',
                  SW_SHOWNORMAL, ewNoWait, ErrCode);
      Result := False;
    end;
  end;
end;
