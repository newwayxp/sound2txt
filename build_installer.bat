@echo off
chcp 65001 >nul
title Build Sound2Text Installer

echo.
echo  インストーラーをビルドしています...
echo.

:: Inno Setup のパスを探す
set ISCC=
for %%p in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) do (
    if exist %%p set ISCC=%%p
)

if "%ISCC%"=="" (
    echo  [ERROR] Inno Setup 6 が見つかりません。
    echo  以下からインストールしてください:
    echo    winget install JRSoftware.InnoSetup
    pause
    exit /b 1
)

:: dist フォルダを作成
if not exist "%~dp0dist" mkdir "%~dp0dist"

:: ビルド実行
%ISCC% "%~dp0installer.iss"
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] ビルドに失敗しました。
    pause
    exit /b 1
)

echo.
echo  +========================================+
echo  ^|  ビルド完了！                        ^|
echo  ^|  dist\Sound2Text_Setup_1.0.0.exe     ^|
echo  +========================================+
echo.
explorer "%~dp0dist"
pause
