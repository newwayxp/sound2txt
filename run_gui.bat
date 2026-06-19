@echo off
REM run_gui.bat - Launcher for Sound2Text GUI
REM This script launches the GUI application with automatic font checking

setlocal enabledelayedexpansion

REM Get the directory where this batch file is located
set SCRIPT_DIR=%~dp0

REM Change to script directory
cd /d "%SCRIPT_DIR%"

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo Error: Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://www.python.org/
    echo.
    pause
    exit /b 1
)

REM Launch the GUI
echo Sound2Text is starting...
python run_gui.py

REM If the script exits unexpectedly, show error
if %errorlevel% neq 0 (
    echo.
    echo Error: Application failed to start
    echo Error code: %errorlevel%
    echo.
    pause
)
