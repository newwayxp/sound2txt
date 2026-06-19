#!/usr/bin/env python3
"""
install_fonts.py – Automatically install custom fonts on Windows.
Installs Share Tech Mono and JetBrains Mono from the fonts/ directory.

Usage:
  python install_fonts.py              # Interactive mode
  python install_fonts.py --silent     # Silent mode (no prompts)
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path


FONTS_DIR = Path(__file__).parent / "fonts"
WINDOWS_FONTS_DIR = Path("C:\\Windows\\Fonts")

FONTS_TO_INSTALL = [
    "ShareTechMono-Regular.ttf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Bold.ttf",
    "JetBrainsMono-BoldItalic.ttf",
]


def is_admin():
    """Check if running with administrator privileges."""
    try:
        import ctypes
        return ctypes.windll.shell.IsUserAnAdmin()
    except Exception:
        return False


def check_installed_fonts():
    """Check which fonts are already installed."""
    installed = []
    missing = []

    for font_file in FONTS_TO_INSTALL:
        font_path = WINDOWS_FONTS_DIR / font_file
        if font_path.exists():
            installed.append(font_file)
        else:
            missing.append(font_file)

    return installed, missing


def install_fonts_admin():
    """Install fonts using direct file copy (requires admin)."""
    if not FONTS_DIR.exists():
        print(f"❌ Font directory not found: {FONTS_DIR}")
        return False

    if not is_admin():
        print("❌ Administrator privileges required!")
        print("\n💡 Please run this script as Administrator:")
        print("   Right-click on 'install_fonts.bat' and select 'Run as administrator'")
        return False

    success_count = 0
    for font_file in FONTS_TO_INSTALL:
        src = FONTS_DIR / font_file
        dst = WINDOWS_FONTS_DIR / font_file

        if not src.exists():
            print(f"⚠️  Source font not found: {src}")
            continue

        try:
            shutil.copy2(src, dst)
            print(f"✓ {font_file}")
            success_count += 1
        except Exception as e:
            print(f"✗ Failed to install {font_file}: {e}")

    return success_count > 0


def install_fonts_via_reg():
    """Install fonts via Windows Registry (no admin needed, but fonts stored elsewhere)."""
    print("\n⚙️  Attempting alternate installation method...")
    print("(This method doesn't require admin privileges)")

    fonts_dir_abs = FONTS_DIR.resolve()
    appdata_fonts = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    appdata_fonts.mkdir(parents=True, exist_ok=True)

    success_count = 0
    for font_file in FONTS_TO_INSTALL:
        src = FONTS_DIR / font_file
        dst = appdata_fonts / font_file

        if not src.exists():
            continue

        try:
            shutil.copy2(src, dst)
            print(f"✓ {font_file} (user fonts directory)")
            success_count += 1
        except Exception as e:
            print(f"✗ {font_file}: {e}")

    return success_count > 0


def main():
    """Main installation routine."""
    print("=" * 60)
    print("Sound2Text Font Installer")
    print("=" * 60)

    # Check what's already installed
    installed, missing = check_installed_fonts()

    if not missing:
        print("\n✓ All required fonts are already installed!")
        return True

    print(f"\n📦 Found {len(FONTS_TO_INSTALL)} fonts to install:")
    print(f"   ✓ Already installed: {len(installed)}")
    print(f"   ⚠️  Missing: {len(missing)}")

    print(f"\nFonts directory: {FONTS_DIR}")
    print(f"Target directory: {WINDOWS_FONTS_DIR}")

    # Check admin rights
    if is_admin():
        print("\n✓ Running with administrator privileges")
        return install_fonts_admin()
    else:
        print("\n⚠️  Not running as administrator")
        print("\nTrying to install with available permissions...")

        # Try with admin via UAC
        if sys.platform == "win32":
            try:
                print("Requesting administrator privileges...")
                subprocess.run([
                    sys.executable, __file__, "--admin"
                ], check=True)
                return True
            except subprocess.CalledProcessError:
                pass

        # Fallback to user fonts directory
        return install_fonts_via_reg()


if __name__ == "__main__":
    try:
        if "--admin" in sys.argv:
            success = install_fonts_admin()
        else:
            success = main()

        if success:
            print("\n" + "=" * 60)
            print("✓ Font installation completed!")
            print("=" * 60)
            print("\n💡 Tip: Restart the application for fonts to take effect.")
            sys.exit(0)
        else:
            print("\n" + "=" * 60)
            print("✗ Font installation encountered issues.")
            print("=" * 60)
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹️  Installation cancelled.")
        sys.exit(1)
