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

:: ─────────────────────────────────────────────
:: Step 1: Python チェック (3.10 以上)
:: ─────────────────────────────────────────────
echo [1/4] Python を確認しています...
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

:: 3.10 未満は警告
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% LSS 3 (
    echo  [WARNING] Python 3.10 以上を推奨します。現在: %PY_VER%
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo  [WARNING] Python 3.10 以上を推奨します。現在: %PY_VER%
)

:: ─────────────────────────────────────────────
:: Step 2: pip パッケージインストール
:: ─────────────────────────────────────────────
echo.
echo [2/4] Python パッケージをインストールしています...
echo  (faster-whisper / PyQt6 のダウンロードに数分かかる場合があります)
echo.

pip install -r "%SCRIPT_DIR%requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] パッケージのインストールに失敗しました。
    echo  社内プロキシを使用している場合は以下のコマンドを試してください:
    echo    pip install -r requirements.txt --proxy http://プロキシアドレス:ポート
    echo.
    set ERROR=1
    goto :end
)
echo.
echo  OK: パッケージのインストール完了

:: ─────────────────────────────────────────────
:: Step 3: ffmpeg インストール（faster-whisper が使用）
:: ─────────────────────────────────────────────
echo.
echo [3/4] ffmpeg を確認・インストールしています...
ffmpeg -version >nul 2>&1
if %errorlevel% equ 0 (
    echo  OK: ffmpeg はインストール済みです
) else (
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo.
        echo  [WARNING] ffmpeg の自動インストールに失敗しました。
        echo  手動でインストールしてください:
        echo    winget install Gyan.FFmpeg
        echo  ※ ffmpeg がないと音声ファイルの処理に失敗する場合があります
    ) else (
        echo  OK: ffmpeg のインストール完了
    )
)

:: ─────────────────────────────────────────────
:: Step 4: 設定ファイルと起動ファイルを準備
:: ─────────────────────────────────────────────
echo.
echo [4/4] 設定ファイルと起動ファイルを準備しています...

:: config.ini がなければ config_default.ini からコピー
if not exist "%SCRIPT_DIR%config.ini" (
    copy "%SCRIPT_DIR%config_default.ini" "%SCRIPT_DIR%config.ini" >nul
    echo  OK: config.ini を config_default.ini から作成しました
) else (
    echo  OK: config.ini は既に存在します (上書きしません)
)

:: run.bat 作成（ダブルクリックで GUI 起動）
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo python -X utf8 ui_qt.py
    echo if %%errorlevel%% neq 0 pause
) > "%SCRIPT_DIR%run.bat"
echo  OK: run.bat を作成しました

:: デスクトップショートカット作成
set SHORTCUT=%USERPROFILE%\Desktop\Sound2Text.lnk
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
    "$s.TargetPath='%SCRIPT_DIR%run.bat';" ^
    "$s.WorkingDirectory='%SCRIPT_DIR%';" ^
    "$s.Description='Sound2Text - 音声文字起こしツール';" ^
    "$s.Save()" >nul 2>&1
if exist "%SHORTCUT%" (
    echo  OK: デスクトップにショートカットを作成しました
) else (
    echo  NOTE: デスクトップショートカットの作成をスキップしました
)

:: ─────────────────────────────────────────────
:: 完了
:: ─────────────────────────────────────────────
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
    echo  ^|  初回起動時に Whisper モデルを         ^|
    echo  ^|  ダウンロードします（small: 約244MB、  ^|
    echo  ^|  large-v3: 約1.5GB）                   ^|
    echo  ^|                                        ^|
    echo  ^|  API Key を設定してから録音を開始      ^|
    echo  ^|  してください（纪要/API タブ）         ^|
    echo  +=========================================+
) else (
    echo  +=========================================+
    echo  ^|  エラーが発生しました。               ^|
    echo  ^|  上記のメッセージを確認してください。 ^|
    echo  +=========================================+
)
echo.
pause
