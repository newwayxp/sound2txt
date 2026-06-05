@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
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
:: Step 0: プロキシ設定（任意）
rem --------------------------------------------------
echo [0/6] 社内プロキシを使用しますか？
echo  使用する場合は アドレス:ポート を入力してください。
echo  例: proxy.company.com:8080
echo  使用しない場合はそのまま Enter を押してください。
echo.
set /p PROXY_INPUT="  プロキシアドレス (空欄でスキップ): "
if not "%PROXY_INPUT%"=="" (
    set PROXY_ARG=--proxy http://%PROXY_INPUT% --trusted-host pypi.org --trusted-host files.pythonhosted.org
    set PROXY_HOST=%PROXY_INPUT%
    echo  OK: プロキシ設定: http://%PROXY_INPUT%
)
echo.

rem --------------------------------------------------
:: Step 1: Python チェック (3.10 以上)
rem --------------------------------------------------
echo [1/6] Python を確認しています...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Python が見つかりません。
    echo  以下からインストールしてください:
    echo    https://www.python.org/downloads/
    echo  インストール時に "Add Python to PATH" にチェックを入れてください。
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
    echo  [WARNING] Python 3.10 以上を推奨します。現在: %PY_VER%
)

rem --------------------------------------------------
:: Step 2: pip パッケージインストール
rem --------------------------------------------------
echo.
echo [2/6] Python パッケージをインストールしています...
echo  (faster-whisper / PyQt6 のダウンロードに数分かかる場合があります)
echo.
pip install -r "%SCRIPT_DIR%requirements.txt" %PROXY_ARG%
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] パッケージのインストールに失敗しました。
    set ERROR=1
    goto :end
)
echo  OK: パッケージのインストール完了

rem --------------------------------------------------
rem Step 3: ctranslate2 / CUDA 確認
rem --------------------------------------------------
echo.
echo [3/6] ctranslate2 の動作確認...
python -c "import ctranslate2" >nul 2>&1
if %errorlevel% equ 0 (
    echo  OK: ctranslate2 正常
    goto :ct2_ok
)

rem --- NVIDIA GPU があるか確認 ---
nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    echo  NVIDIA GPU 検出。CUDA 12 ランタイムをインストールします...
    pip install --upgrade ctranslate2 %PROXY_ARG%
    pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 %PROXY_ARG%
    python -c "import ctranslate2" >nul 2>&1
    if %errorlevel% equ 0 (
        echo  OK: GPU+CUDA モードで動作確認
        goto :ct2_ok
    )
    echo  CUDA 修復失敗。CPU モードにフォールバックします...
) else (
    echo  GPU なし環境を検出しました。
)

rem --- CPU モード: ctranslate2 パッケージ内の CUDA DLL を除去 ---
echo  CUDA DLL を除去して CPU モードに設定します...
python -c "import os,importlib.util,glob; s=importlib.util.find_spec('ctranslate2'); d=os.path.dirname(s.origin) if s and s.origin else None; [os.remove(f) for p in ['cu*.dll','nv*.dll'] for f in (glob.glob(os.path.join(d,p)) if d else [])]"

python -c "import ctranslate2" >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] ctranslate2 の修復に失敗しました。
    set ERROR=1
    goto :end
)
echo  OK: CPU モードで動作確認
:ct2_ok

rem --------------------------------------------------
:: Step 3: ffmpeg インストール
rem --------------------------------------------------
echo.
echo [4/6] ffmpeg を確認・インストールしています...
ffmpeg -version >nul 2>&1
if %errorlevel% equ 0 (
    echo  OK: ffmpeg はインストール済みです
) else (
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo  [WARNING] ffmpeg の自動インストールに失敗しました。
        echo  手動でインストールしてください: winget install Gyan.FFmpeg
    ) else (
        echo  OK: ffmpeg のインストール完了
    )
)

rem --------------------------------------------------
:: Step 4: 設定ファイルと起動ファイルを準備
rem --------------------------------------------------
echo.
echo [5/6] 設定ファイルと起動ファイルを準備しています...

if not exist "%SCRIPT_DIR%config.ini" (
    copy "%SCRIPT_DIR%config_default.ini" "%SCRIPT_DIR%config.ini" >nul
    echo  OK: config.ini を config_default.ini から作成しました
) else (
    echo  OK: config.ini は既に存在します (上書きしません)
)

if not "%PROXY_HOST%"=="" (
    python -c "
import configparser, os
cfg = configparser.ConfigParser()
f = r'%SCRIPT_DIR%config.ini'
cfg.read(f, encoding='utf-8')
if not cfg.has_section('network'):
    cfg.add_section('network')
cfg.set('network', 'https_proxy', 'http://%PROXY_HOST%')
cfg.set('network', 'http_proxy',  'http://%PROXY_HOST%')
cfg.set('network', 'ssl_verify',  'false')
with open(f, 'w', encoding='utf-8') as fp:
    cfg.write(fp)
print('  OK: config.ini にプロキシ設定を書き込みました')
"
)

(
    echo @echo off
    echo cd /d "%%~dp0"
    echo python -X utf8 ui_qt.py
    echo if %%errorlevel%% neq 0 pause
) > "%SCRIPT_DIR%run.bat"
echo  OK: run.bat を作成しました

set SHORTCUT=%USERPROFILE%\Desktop\Sound2Text.lnk
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
    "$s.TargetPath='%SCRIPT_DIR%run.bat';" ^
    "$s.WorkingDirectory='%SCRIPT_DIR%';" ^
    "$s.Description='Sound2Text';" ^
    "$s.Save()" >nul 2>&1
if exist "%SHORTCUT%" (
    echo  OK: デスクトップにショートカットを作成しました
)

rem --------------------------------------------------
:: Step 5: 日本語専用モデル (kotoba-whisper) 準備
rem --------------------------------------------------
echo.
echo [6/6] 日本語認識モデル (kotoba-whisper-v2.0) を準備しています...
echo  ※ 約1.5GB のダウンロードが発生します（初回のみ）
echo.

set MODEL_DIR=%SCRIPT_DIR%models\kotoba-whisper-v2.0-ct2
set MODEL_BIN=%MODEL_DIR%\model.bin

if exist "%MODEL_BIN%" (
    echo  OK: kotoba-whisper は既に存在します (スキップ)
    goto :model_done
)

echo  ダウンロードと変換を開始します（10〜30分かかる場合があります）...
echo  プロキシ環境の場合、環境変数を設定してから変換します。

if not "%PROXY_HOST%"=="" (
    set HTTPS_PROXY=http://%PROXY_HOST%
    set HTTP_PROXY=http://%PROXY_HOST%
    set HF_HUB_DISABLE_SSL_VERIFICATION=1
)
set HUGGINGFACE_HUB_DISABLE_SYMLINKS_WARNING=1

:: ct2-transformers-converter を実行
set CT2_CONV=
for /f "delims=" %%i in ('python -c "import sys,os; print(os.path.join(os.path.dirname(sys.executable), 'Scripts', 'ct2-transformers-converter.exe'))" 2^>nul') do set CT2_CONV=%%i

if not exist "%CT2_CONV%" (
    for /f "delims=" %%i in ('python -c "import site,os; scripts=[p for p in site.getsitepackages() if 'site-packages' in p]; print(os.path.join(os.path.dirname(scripts[0]), 'Scripts', 'ct2-transformers-converter.exe')) if scripts else print('')" 2^>nul') do set CT2_CONV=%%i
)

if not exist "%CT2_CONV%" (
    echo  [WARNING] ct2-transformers-converter が見つかりません。
    echo  手動で変換してください:
    echo    ct2-transformers-converter --model kotoba-tech/kotoba-whisper-v2.0 --output_dir models\kotoba-whisper-v2.0-ct2 --quantization int8
    goto :model_done
)

echo  コンバーター: %CT2_CONV%
"%CT2_CONV%" --model kotoba-tech/kotoba-whisper-v2.0 ^
    --output_dir "%MODEL_DIR%" ^
    --quantization int8 ^
    --force

if %errorlevel% neq 0 (
    echo.
    echo  [WARNING] kotoba-whisper の変換に失敗しました。
    echo  標準の Whisper モデルで動作します。
    echo  後で手動で再実行してください:
    echo    cd "%SCRIPT_DIR%"
    echo    ct2-transformers-converter --model kotoba-tech/kotoba-whisper-v2.0 --output_dir models\kotoba-whisper-v2.0-ct2 --quantization int8
) else (
    echo  OK: kotoba-whisper 変換完了
)

:model_done

rem --------------------------------------------------
:: 完了
rem --------------------------------------------------
:end
echo.
if %ERROR% equ 0 (
    echo  +=========================================+
    echo  ^|  セットアップ完了！                   ^|
    echo  ^|                                        ^|
    echo  ^|  起動方法:                             ^|
    echo  ^|    run.bat をダブルクリック            ^|
    echo  ^|    または: python ui_qt.py             ^|
    echo  ^|                                        ^|
    echo  ^|  日本語認識: kotoba-whisper-v2.0        ^|
    echo  ^|  纪要/API タブで API Key を設定して    ^|
    echo  ^|  から録音を開始してください。          ^|
    echo  +=========================================+
) else (
    echo  +=========================================+
    echo  ^|  エラーが発生しました。               ^|
    echo  ^|  上記のメッセージを確認してください。 ^|
    echo  +=========================================+
)
echo.
pause
