; ============================================================
;  Sound2Text Installer Script  v1.3.12
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
#define AppVersion "1.3.12"
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
Source: "start.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "pipeline.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "log_util.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "recorder.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "mic_recorder.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "transcriber.py";     DestDir: "{app}"; Flags: ignoreversion
Source: "summarizer.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "subtitle_window.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "device_utils.py";    DestDir: "{app}"; Flags: ignoreversion
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
; Step 1: Upgrade pip
Filename: "python"; Parameters: "-m pip install --upgrade pip --quiet"; WorkingDir: "{app}"; StatusMsg: "Upgrading pip..."; Flags: postinstall waituntilterminated runascurrentuser

; Step 2: Install Python packages (faster-whisper, PyQt6, transformers, ctranslate2>=4.7, ...)
Filename: "pip"; Parameters: "install -r ""{app}\requirements.txt"""; WorkingDir: "{app}"; StatusMsg: "Installing Python packages..."; Description: "Install Python packages (faster-whisper, PyQt6, etc.)"; Flags: postinstall waituntilterminated runascurrentuser

; Step 3: Patch ctranslate2 __init__.py and fix CUDA DLL issues
;   - Adds try/except around ctypes.CDLL so missing CUDA DLLs don't crash import
;   - Tests faster-whisper import; removes CUDA DLLs if still failing (CPU fallback)
Filename: "python"; Parameters: "-c ""import importlib.util,os,glob,subprocess,sys; p=os.path.join(os.path.dirname(importlib.util.find_spec('ctranslate2').origin),'__init__.py'); t=open(p).read(); open(p,'w').write(t.replace('        ctypes.CDLL(library)','        try:\n            ctypes.CDLL(library)\n        except OSError:\n            pass') if 'except OSError' not in t else t); r=subprocess.run([sys.executable,'-c','from faster_whisper import WhisperModel'],capture_output=True); s=importlib.util.find_spec('ctranslate2'); d=os.path.dirname(s.origin) if s and s.origin else ''; r.returncode and [os.remove(f) for p2 in ['cu*.dll','nv*.dll'] for f in glob.glob(os.path.join(d,p2))]"""; WorkingDir: "{app}"; StatusMsg: "Configuring ctranslate2..."; Flags: postinstall waituntilterminated runascurrentuser

; Step 4: Install NVIDIA CUDA packages for GPU acceleration (skipped if no GPU)
Filename: "python"; Parameters: "-c ""import subprocess,sys; r=subprocess.run(['nvidia-smi'],capture_output=True); r.returncode==0 and subprocess.run([sys.executable,'-m','pip','install','nvidia-cuda-runtime-cu12','nvidia-cublas-cu12'],capture_output=True)"""; WorkingDir: "{app}"; StatusMsg: "Configuring GPU acceleration (NVIDIA only)..."; Flags: postinstall waituntilterminated runascurrentuser

; Step 5: Install ffmpeg
Filename: "powershell"; Parameters: "-NoProfile -Command ""if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {{ winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements }} else {{ Write-Host 'ffmpeg already installed' }}"""; StatusMsg: "Checking ffmpeg..."; Description: "Install ffmpeg (required for audio processing)"; Flags: postinstall waituntilterminated runascurrentuser

; Step 6: Install PyTorch CPU (needed by ct2-transformers-converter to convert the model)
Filename: "python"; Parameters: "-c ""import subprocess,sys; r=subprocess.run([sys.executable,'-c','import torch'],capture_output=True); r.returncode and subprocess.run([sys.executable,'-m','pip','install','torch','--index-url','https://download.pytorch.org/whl/cpu','--trusted-host','download.pytorch.org'])"""; WorkingDir: "{app}"; StatusMsg: "Installing PyTorch for model conversion (~200MB)..."; Flags: postinstall waituntilterminated runascurrentuser

; Step 7: Download and convert kotoba-whisper Japanese model (~1.5GB, 10-30 min)
;   Skipped if model.bin already exists. Uses ct2-transformers-converter installed with ctranslate2.
Filename: "python"; Parameters: "-c ""import subprocess,sys,os; base=os.getcwd(); mdir=os.path.join(base,'models','kotoba-whisper-v2.0-ct2'); mbin=os.path.join(mdir,'model.bin'); conv=os.path.join(os.path.dirname(sys.executable),'Scripts','ct2-transformers-converter.exe'); not os.path.exists(mbin) and os.path.exists(conv) and subprocess.run([conv,'--model','kotoba-tech/kotoba-whisper-v2.0','--output_dir',mdir,'--quantization','int8','--force'])"""; WorkingDir: "{app}"; StatusMsg: "Downloading Japanese model kotoba-whisper (~1.5GB, 10-30 min)..."; Description: "Download Japanese speech model kotoba-whisper-v2.0 (~1.5GB)"; Flags: postinstall waituntilterminated runascurrentuser

; Step 7b: Create preprocessor_config.json for kotoba-whisper (uses 128 mel bins, not default 80)
Filename: "python"; Parameters: "-c ""import json,os; mdir=os.path.join(os.getcwd(),'models','kotoba-whisper-v2.0-ct2'); cfg_path=os.path.join(mdir,'preprocessor_config.json'); os.path.exists(mdir) and not os.path.exists(cfg_path) and json.dump({'chunk_length':30,'feature_extractor_type':'WhisperFeatureExtractor','feature_size':128,'hop_length':160,'n_samples':480000,'nb_max_frames':3000,'padding_side':'right','padding_value':0.0,'processor_class':'WhisperProcessor','return_attention_mask':False,'sampling_rate':16000},open(cfg_path,'w'),indent=2)"""; WorkingDir: "{app}"; StatusMsg: "Configuring kotoba-whisper feature extractor..."; Flags: postinstall waituntilterminated runascurrentuser

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
