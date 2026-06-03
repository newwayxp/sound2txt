@echo off
setlocal enabledelayedexpansion
title Build Sound2Text Installer
set "CACHE=%~dp0.iscc_path"
set "ISCC="

echo.
echo Building installer...
echo.

:: Step 1: Read from cache
if exist "%CACHE%" (
    set /p _CACHED=<"%CACHE%"
    if exist "!_CACHED!" (
        set "ISCC=!_CACHED!"
        echo [cache] !ISCC!
    ) else (
        echo [cache] Cached path not found. Re-searching...
    )
)

:: Step 2: Check known install locations
if "!ISCC!"=="" (
    for %%p in (
        "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) do (
        if "!ISCC!"=="" if exist %%p set "ISCC=%%~p"
    )
)

:: Step 3: Deep search in AppData
if "!ISCC!"=="" (
    echo [search] Scanning AppData...
    for /f "delims=" %%f in ('dir /s /b "%LOCALAPPDATA%\ISCC.exe" 2^>nul') do (
        if "!ISCC!"=="" set "ISCC=%%f"
    )
)

:: Step 4: Deep search in Program Files
if "!ISCC!"=="" (
    echo [search] Scanning Program Files...
    for /f "delims=" %%f in ('dir /s /b "C:\Program Files\ISCC.exe" 2^>nul') do (
        if "!ISCC!"=="" set "ISCC=%%f"
    )
)
if "!ISCC!"=="" (
    for /f "delims=" %%f in ('dir /s /b "C:\Program Files (x86)\ISCC.exe" 2^>nul') do (
        if "!ISCC!"=="" set "ISCC=%%f"
    )
)

:: Not found
if "!ISCC!"=="" (
    echo.
    echo [ERROR] ISCC.exe not found.
    echo Install Inno Setup with: winget install JRSoftware.InnoSetup
    pause
    exit /b 1
)

:: Save path to cache (no trailing space)
>"%CACHE%" echo !ISCC!
echo [found] !ISCC!
echo.

:: Step 5: Build
if not exist "%~dp0dist" mkdir "%~dp0dist"

"!ISCC!" "%~dp0installer.iss"
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo +------------------------------------------+
echo ^|  Build complete!                        ^|
echo ^|  dist\Sound2Text_Setup_1.0.0.exe        ^|
echo +------------------------------------------+
echo.
explorer "%~dp0dist"
pause
