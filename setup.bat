@echo off
setlocal enabledelayedexpansion
title Sound2Text Setup

echo.
echo  +=========================================+
echo  ^|         Sound2Text  Setup              ^|
echo  +=========================================+
echo.

set SCRIPT_DIR=%~dp0
set ERROR=0
set PROXY_ARG=
set PROXY_HOST=

rem --------------------------------------------------
rem Step 0: Proxy configuration (optional)
rem --------------------------------------------------
echo [0/6] Proxy configuration
echo  Enter your corporate proxy address if required.
echo  Example: proxy.company.com:8080
echo  Leave blank and press Enter to skip.
echo.
set /p PROXY_INPUT="  Proxy address (blank to skip): "
if not "%PROXY_INPUT%"=="" (
    set PROXY_ARG=--proxy http://%PROXY_INPUT% --trusted-host pypi.org --trusted-host files.pythonhosted.org
    set PROXY_HOST=%PROXY_INPUT%
    echo  OK: Proxy set to http://%PROXY_INPUT%
)
echo.

rem --------------------------------------------------
rem Step 1: Check Python (3.10+)
rem --------------------------------------------------
echo [1/6] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Python not found.
    echo  Please install Python from:
    echo    https://www.python.org/downloads/
    echo  Check "Add Python to PATH" during installation.
    echo.
    set ERROR=1
    goto :end
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo  OK: Python %PY_VER%

for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo  [WARNING] Python 3.10+ is recommended. Found: %PY_VER%
)

rem --------------------------------------------------
rem Step 2: Install pip packages
rem --------------------------------------------------
echo.
echo [2/6] Installing Python packages...
echo  (faster-whisper and PyQt6 may take a few minutes to download)
echo.
pip install -r "%SCRIPT_DIR%requirements.txt" %PROXY_ARG%
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Package installation failed.
    set ERROR=1
    goto :end
)
echo  OK: Packages installed

rem --------------------------------------------------
rem Step 3: Patch ctranslate2 + verify faster-whisper
rem --------------------------------------------------
echo.
echo [3/6] Checking faster-whisper...

rem Patch ctranslate2 __init__.py: add try/except around CDLL loading
rem (ctranslate2 tries to load all bundled DLLs without error handling)
python -c "import importlib.util,os; p=os.path.join(os.path.dirname(importlib.util.find_spec('ctranslate2').origin),'__init__.py'); t=open(p).read(); t2=t.replace('        ctypes.CDLL(library)','        try:\n            ctypes.CDLL(library)\n        except OSError:\n            pass') if 'except OSError' not in t else t; open(p,'w').write(t2); print('  ctranslate2 patch: ' + ('applied' if 'except OSError' not in t else 'already OK'))"

python -c "from faster_whisper import WhisperModel" >nul 2>&1
if %errorlevel% neq 0 goto :ct2_fix_dlls

rem faster-whisper import OK - check for GPU
nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo  OK: faster-whisper OK (CPU mode)
    goto :ct2_ok
)

rem NVIDIA GPU found - install CUDA 12 compute packages via pip
echo  NVIDIA GPU detected. Installing CUDA 12 compute packages...
pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 %PROXY_ARG%
if %errorlevel% equ 0 (
    echo  OK: CUDA packages installed - GPU acceleration enabled
    goto :ct2_ok
)
echo  [WARNING] CUDA packages install failed.
echo.
echo  =====================================================
echo   GPU acceleration requires CUDA 12 Toolkit.
echo   Download and install from:
echo     https://developer.nvidia.com/cuda-downloads
echo   Select: Windows - x86_64 - exe (local)
echo   App will run in CPU mode until CUDA is installed.
echo  =====================================================
echo.
goto :ct2_ok

:ct2_fix_dlls
rem faster-whisper import failed - remove bundled CUDA DLLs as fallback
echo  Removing bundled CUDA DLLs (CPU fallback)...
python -c "import os,importlib.util,glob; s=importlib.util.find_spec('ctranslate2'); d=os.path.dirname(s.origin) if s and s.origin else ''; removed=[f for p in ['cu*.dll','nv*.dll'] for f in glob.glob(os.path.join(d,p))]; [os.remove(f) for f in removed]; print(len(removed), 'CUDA DLL(s) removed')"
python -c "from faster_whisper import WhisperModel" >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] faster-whisper repair failed.
    set ERROR=1
    goto :end
)
echo  OK: faster-whisper OK (CPU mode)
:ct2_ok

rem --------------------------------------------------
rem Step 4: Install ffmpeg
rem --------------------------------------------------
echo.
echo [4/6] Checking ffmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% equ 0 (
    echo  OK: ffmpeg already installed
) else (
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo  [WARNING] ffmpeg auto-install failed.
        echo  Please install manually: winget install Gyan.FFmpeg
    ) else (
        echo  OK: ffmpeg installed
    )
)

rem --------------------------------------------------
rem Step 5: Create config and launcher files
rem --------------------------------------------------
echo.
echo [5/6] Setting up config and launcher...

if not exist "%SCRIPT_DIR%config.ini" (
    copy "%SCRIPT_DIR%config_default.ini" "%SCRIPT_DIR%config.ini" >nul
    echo  OK: config.ini created from config_default.ini
) else (
    echo  OK: config.ini already exists (skipping)
)

if not "%PROXY_HOST%"=="" (
    python -c "import configparser; f=r'%SCRIPT_DIR%config.ini'; cfg=configparser.ConfigParser(); cfg.read(f,encoding='utf-8'); cfg.has_section('network') or cfg.add_section('network'); cfg.set('network','https_proxy','http://%PROXY_HOST%'); cfg.set('network','http_proxy','http://%PROXY_HOST%'); cfg.set('network','ssl_verify','false'); fp=open(f,'w',encoding='utf-8'); cfg.write(fp); fp.close(); print('  OK: Proxy settings saved')"
)

(
    echo @echo off
    echo cd /d "%%~dp0"
    echo python -X utf8 ui_qt.py
    echo if %%errorlevel%% neq 0 pause
) > "%SCRIPT_DIR%run.bat"
echo  OK: run.bat created

set SHORTCUT=%USERPROFILE%\Desktop\Sound2Text.lnk
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
    "$s.TargetPath='%SCRIPT_DIR%run.bat';" ^
    "$s.WorkingDirectory='%SCRIPT_DIR%';" ^
    "$s.Description='Sound2Text';" ^
    "$s.Save()" >nul 2>&1
if exist "%SHORTCUT%" (
    echo  OK: Desktop shortcut created
)

rem --------------------------------------------------
rem Step 6: Download Japanese recognition model (kotoba-whisper)
rem --------------------------------------------------
echo.
echo [6/6] Downloading Japanese model (kotoba-whisper-v2.0, ~1.5GB)...
echo.

set MODEL_DIR=%SCRIPT_DIR%models\kotoba-whisper-v2.0-ct2
set MODEL_BIN=%MODEL_DIR%\model.bin

if exist "%MODEL_BIN%" (
    echo  OK: kotoba-whisper already downloaded (skipping)
    goto :model_done
)

echo  Downloading from HuggingFace (10-30 min on first run)...

if not "%PROXY_HOST%"=="" (
    set HTTPS_PROXY=http://%PROXY_HOST%
    set HTTP_PROXY=http://%PROXY_HOST%
    set HF_HUB_DISABLE_SSL_VERIFICATION=1
)
set HUGGINGFACE_HUB_DISABLE_SYMLINKS_WARNING=1

rem Find ct2-transformers-converter
set CT2_CONV=
for /f "delims=" %%i in ('python -c "import sys,os; print(os.path.join(os.path.dirname(sys.executable), 'Scripts', 'ct2-transformers-converter.exe'))" 2^>nul') do set CT2_CONV=%%i

if not exist "%CT2_CONV%" (
    for /f "delims=" %%i in ('python -c "import site,os; s=[p for p in site.getsitepackages() if 'site-packages' in p]; print(os.path.join(os.path.dirname(s[0]),'Scripts','ct2-transformers-converter.exe')) if s else print('')" 2^>nul') do set CT2_CONV=%%i
)

if not exist "%CT2_CONV%" (
    echo  [ERROR] ct2-transformers-converter not found.
    echo  Make sure Step 2 completed successfully (transformers package required).
    set ERROR=1
    goto :end
)

"%CT2_CONV%" --model kotoba-tech/kotoba-whisper-v2.0 ^
    --output_dir "%MODEL_DIR%" ^
    --quantization int8 ^
    --force

if not exist "%MODEL_BIN%" (
    echo.
    echo  [ERROR] Download failed - model.bin not created.
    echo  Check network/proxy settings and retry setup.bat.
    set ERROR=1
    goto :end
)
echo  OK: kotoba-whisper ready

:model_done

rem --------------------------------------------------
:end
echo.
if %ERROR% equ 0 (
    echo  +=========================================+
    echo  ^|  Setup complete!                       ^|
    echo  ^|                                        ^|
    echo  ^|  To launch:                            ^|
    echo  ^|    Double-click run.bat                ^|
    echo  ^|    or: python ui_qt.py                 ^|
    echo  ^|                                        ^|
    echo  ^|  Set API Key in the Summary/API tab    ^|
    echo  ^|  before starting recording.            ^|
    echo  +=========================================+
) else (
    echo  +=========================================+
    echo  ^|  Setup encountered errors.             ^|
    echo  ^|  Check the messages above.             ^|
    echo  +=========================================+
)
echo.
pause
