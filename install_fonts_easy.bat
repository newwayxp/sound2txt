@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo.
echo ========================================
echo Sound2Text 字体安装工具
echo ========================================
echo.

REM 检查 Python 是否安装
where python >nul 2>&1
if errorlevel 1 (
    echo 错误：Python 未安装或不在 PATH 中
    echo.
    echo 请从以下链接安装 Python:
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM 运行 Python 脚本
python install_fonts_easy.py

pause
