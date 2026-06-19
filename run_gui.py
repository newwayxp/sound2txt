#!/usr/bin/env python3
"""
run_gui.py – Launcher for Sound2Text GUI with font checking and loading

This script:
1. Checks if required fonts are installed
2. Prompts to install fonts if missing
3. Loads fonts from local fonts/ directory into QFontDatabase
4. Launches the GUI application

Usage:
  python run_gui.py
"""
import sys
import os
from pathlib import Path

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

# Check fonts before launching GUI
from font_checker import ensure_fonts_installed

print("[Startup] Checking required fonts...")
fonts_ok = ensure_fonts_installed(use_gui=True)

if not fonts_ok:
    print("[Warning] Continuing without system fonts. Will load from local fonts/.")

# Import and launch the main app
if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFontDatabase
    from appconfig import AppConfig
    from presenter import Presenter
    from ui_qt_design import App

    print("[Startup] Initializing application...")

    app_qt = QApplication(sys.argv)

    # Load fonts from local fonts/ directory
    fonts_dir = Path(__file__).parent / "fonts"
    fonts_to_load = [
        "ShareTechMono-Regular.ttf",
        "JetBrainsMono-Regular.ttf",
        "JetBrainsMono-Bold.ttf",
        "JetBrainsMono-BoldItalic.ttf",
    ]

    for font_file in fonts_to_load:
        font_path = fonts_dir / font_file
        if font_path.exists():
            result = QFontDatabase.addApplicationFont(str(font_path))
            if result >= 0:
                print(f"  ✓ Loaded: {font_file}")
            else:
                print(f"  ✗ Failed to load: {font_file}")
        else:
            print(f"  ⊘ Not found: {font_file}")

    # Apply stylesheet with font family
    from ui_qt_design import _STYLESHEET
    _font_family = (
        '"SF Pro Text", "Helvetica Neue", "Hiragino Sans", "PingFang SC"'
        if sys.platform == "darwin" else
        '"Noto Sans JP", "Segoe UI", "Meiryo UI", "Microsoft YaHei"'
    )
    app_qt.setStyleSheet(_STYLESHEET.replace("__FONT_FAMILY__", _font_family))

    # Create and show main window
    config = AppConfig()
    presenter = Presenter(config)
    window = App(presenter)
    window.show()

    print("[Startup] Application ready!")
    sys.exit(app_qt.exec())
