@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo Sound2Text 字体安装工具
echo ========================================
echo.

python install_fonts_simple.py
