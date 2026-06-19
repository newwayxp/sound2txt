"""
font_checker.py – Check if required fonts are installed and prompt installation if needed.
"""
import sys
from pathlib import Path


# Required fonts for UI
REQUIRED_FONTS = {
    "Share Tech Mono": "ShareTechMono-Regular.ttf",
    "JetBrains Mono": "JetBrainsMono-Regular.ttf",
}


def check_font_installed(font_name: str) -> bool:
    """
    Check if a font is installed on Windows.

    Args:
        font_name: Font name (e.g., "Share Tech Mono")

    Returns:
        True if font is installed, False otherwise
    """
    if sys.platform != "win32":
        # On non-Windows, assume fonts are available
        return True

    try:
        from pathlib import Path
        windows_fonts = Path("C:\\Windows\\Fonts")

        # Check if font files exist in system fonts directory
        for font_file in REQUIRED_FONTS.values():
            if (windows_fonts / font_file).exists():
                return True

        return False
    except Exception:
        return True  # Assume available if check fails


def check_all_fonts() -> dict[str, bool]:
    """
    Check if all required fonts are installed.

    Returns:
        Dictionary mapping font name to installation status
    """
    return {
        font_name: check_font_installed(font_name)
        for font_name in REQUIRED_FONTS.keys()
    }


def get_missing_fonts() -> list[str]:
    """
    Get list of missing fonts.

    Returns:
        List of font names that are not installed
    """
    font_status = check_all_fonts()
    return [name for name, installed in font_status.items() if not installed]


def prompt_install_fonts_gui():
    """
    Prompt user to install fonts via GUI dialog (Qt).

    Returns:
        True if user confirmed installation, False otherwise
    """
    missing = get_missing_fonts()

    if not missing:
        return True  # All fonts installed

    try:
        from PyQt6.QtWidgets import QMessageBox, QApplication
        import subprocess

        # Create a temporary app if needed
        app = QApplication.instance() or QApplication([])

        msg = QMessageBox()
        msg.setWindowTitle("Sound2Text - Font Installation Required")
        msg.setText(f"Missing fonts: {', '.join(missing)}")
        msg.setInformativeText(
            "Sound2Text requires custom fonts for proper UI rendering.\n\n"
            "Would you like to install them now?\n\n"
            "Click 'Yes' to install fonts automatically."
        )
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        result = msg.exec()

        if result == QMessageBox.StandardButton.Yes:
            # Try to run the font installer
            installer_script = Path(__file__).parent / "install_fonts.bat"
            if installer_script.exists():
                subprocess.Popen([str(installer_script)])
                return True

        return False
    except Exception as e:
        print(f"Warning: Could not prompt for font installation: {e}")
        return False


def prompt_install_fonts_cli():
    """
    Prompt user to install fonts via console.

    Returns:
        True if user confirmed, False otherwise
    """
    missing = get_missing_fonts()

    if not missing:
        return True

    print("\n" + "=" * 60)
    print("⚠️  Missing Fonts Detected")
    print("=" * 60)
    print(f"Missing: {', '.join(missing)}")
    print("\nSound2Text uses custom fonts for optimal UI rendering.")
    print("\nTo install fonts, run from project directory:")
    print("  python install_fonts.py")
    print("Or double-click:")
    print("  install_fonts.bat")
    print("\n(Restart the application after installing fonts)")
    print("=" * 60 + "\n")

    response = input("Continue without fonts? (y/n) [y]: ").lower().strip()
    return response in ("y", "", "yes")


def ensure_fonts_installed(use_gui: bool = True) -> bool:
    """
    Check fonts and prompt installation if needed.

    Args:
        use_gui: Use GUI dialog if True, console prompt if False

    Returns:
        True if all fonts are installed or user chose to continue, False to abort
    """
    missing = get_missing_fonts()

    if not missing:
        return True  # All fonts installed

    print(f"[Font Check] Missing fonts: {', '.join(missing)}")

    if use_gui and sys.platform == "win32":
        return prompt_install_fonts_gui()
    else:
        return prompt_install_fonts_cli()


if __name__ == "__main__":
    # Test the font checker
    print("Font Status:")
    for font_name, installed in check_all_fonts().items():
        status = "✓ Installed" if installed else "✗ Missing"
        print(f"  {font_name}: {status}")
