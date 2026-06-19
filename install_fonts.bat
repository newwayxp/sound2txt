@echo off
REM install_fonts.bat - Install custom fonts with administrator privileges
REM This script will request UAC elevation if not running as administrator

setlocal enabledelayedexpansion

:: Check if running as administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ==================== Sound2Text Font Installer ====================
    echo.
    echo Administrator privileges are required to install fonts.
    echo Requesting elevation...
    echo.

    :: Re-launch with administrator privileges
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process '%~0' -Verb RunAs"
    exit /b 0
)

:: Running as administrator
cls
echo.
echo ==================== Sound2Text Font Installer ====================
echo.

set FONT_DIR=%~dp0fonts
set WINDOWS_FONT_DIR=C:\Windows\Fonts

if not exist "%FONT_DIR%" (
    echo Error: Font directory not found: %FONT_DIR%
    pause
    exit /b 1
)

echo Fonts directory: %FONT_DIR%
echo Target directory: %WINDOWS_FONT_DIR%
echo.
echo Installing fonts...
echo.

set SUCCESS=0
set FAILED=0

REM Install fonts
for %%F in (
    "ShareTechMono-Regular.ttf"
    "JetBrainsMono-Regular.ttf"
    "JetBrainsMono-Bold.ttf"
    "JetBrainsMono-BoldItalic.ttf"
) do (
    if exist "%FONT_DIR%\%%~F" (
        copy "%FONT_DIR%\%%~F" "%WINDOWS_FONT_DIR%\%%~F" >nul 2>&1
        if !errorlevel! equ 0 (
            echo [OK] %%~F
            set /a SUCCESS=!SUCCESS! + 1
        ) else (
            echo [FAIL] %%~F
            set /a FAILED=!FAILED! + 1
        )
    ) else (
        echo [SKIP] %%~F - not found
    )
)

echo.
echo ==================== Installation Summary ====================
echo Success: %SUCCESS% fonts installed
echo Failed: %FAILED% fonts
echo.

if %SUCCESS% gtr 0 (
    echo ✓ Font installation completed successfully!
    echo.
    echo Tip: Restart the application for changes to take effect.
) else (
    echo ✗ No fonts were installed.
)

echo.
pause
