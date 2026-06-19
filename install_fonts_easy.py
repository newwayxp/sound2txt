#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_fonts_easy.py - 简易字体安装脚本
直接运行此脚本即可自动安装字体

使用方式：
  1. 双击此文件运行
  2. 或在命令行运行: python install_fonts_easy.py
"""
import os
import sys
import shutil
from pathlib import Path

# 确保输出使用 UTF-8 编码
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


class FontInstaller:
    """字体安装器"""

    FONTS_DIR = Path(__file__).parent / "fonts"
    WINDOWS_FONTS_DIR = Path("C:\\Windows\\Fonts")

    FONTS_TO_INSTALL = [
        "ShareTechMono-Regular.ttf",
        "JetBrainsMono-Regular.ttf",
        "JetBrainsMono-Bold.ttf",
        "JetBrainsMono-BoldItalic.ttf",
    ]

    def __init__(self):
        self.installed_count = 0
        self.failed_count = 0
        self.skipped_count = 0

    @staticmethod
    def is_admin():
        """检查是否有管理员权限"""
        try:
            import ctypes
            return ctypes.windll.shell.IsUserAnAdmin()
        except Exception:
            return False

    @staticmethod
    def request_admin():
        """请求管理员权限并重新运行脚本"""
        import subprocess
        import ctypes
        try:
            # 在 Windows 上请求 UAC 提升
            ctypes.windll.shell.ShellExecuteW(None, "runas", sys.executable, __file__, None, 1)
            return True
        except Exception:
            # 如果失败，尝试用 subprocess
            try:
                subprocess.run(
                    [sys.executable, __file__],
                    check=False
                )
                return True
            except Exception:
                return False

    def print_header(self):
        """打印标题"""
        print("\n" + "=" * 60)
        print("🔤 Sound2Text 字体安装工具")
        print("=" * 60 + "\n")

    def check_prerequisites(self) -> bool:
        """检查必要条件"""
        print("📋 检查条件...")

        # 检查字体目录
        if not self.FONTS_DIR.exists():
            print(f"❌ 错误：字体目录不存在: {self.FONTS_DIR}")
            return False

        print(f"✓ 字体目录: {self.FONTS_DIR}")
        print(f"✓ 目标目录: {self.WINDOWS_FONTS_DIR}")

        # 检查管理员权限
        if not self.is_admin():
            print("\n⚠️  需要管理员权限才能安装字体")
            print("正在请求管理员权限...\n")

            if self.request_admin():
                return False  # 脚本已以管理员身份重新运行

            print("❌ 无法获取管理员权限")
            print("\n💡 解决方案：")
            print("   1. 右键点击此脚本")
            print("   2. 选择'以管理员身份运行'")
            return False

        print("✓ 已获得管理员权限\n")
        return True

    def install_fonts(self):
        """安装字体"""
        print("📦 安装字体...\n")

        for font_file in self.FONTS_TO_INSTALL:
            src = self.FONTS_DIR / font_file
            dst = self.WINDOWS_FONTS_DIR / font_file

            # 检查源文件
            if not src.exists():
                print(f"⊘ 跳过: {font_file} (文件不存在)")
                self.skipped_count += 1
                continue

            # 检查是否已安装
            if dst.exists():
                print(f"✓ 已安装: {font_file}")
                self.installed_count += 1
                continue

            # 安装字体
            try:
                shutil.copy2(src, dst)
                print(f"✓ 已安装: {font_file}")
                self.installed_count += 1
            except Exception as e:
                print(f"✗ 失败: {font_file} - {str(e)}")
                self.failed_count += 1

    def print_summary(self):
        """打印总结"""
        print("\n" + "=" * 60)
        print("📊 安装总结")
        print("=" * 60)
        print(f"✓ 新安装: {self.installed_count}")
        print(f"✗ 失败: {self.failed_count}")
        print(f"⊘ 已存在: {self.skipped_count}\n")

        if self.failed_count == 0:
            print("✅ 字体安装完成！")
            print("\n💡 提示：请重启应用以使用新字体。")
            return True
        else:
            print("⚠️  部分字体安装失败，请以管理员身份重新运行。")
            return False

    def run(self):
        """运行安装程序"""
        self.print_header()

        if not self.check_prerequisites():
            return False

        self.install_fonts()
        success = self.print_summary()

        return success


def main():
    """主程序"""
    try:
        installer = FontInstaller()
        success = installer.run()

        print("\n按任意键退出...")
        input()

        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n\n⏹️ 已取消")
        return 1
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        print("\n按任意键退出...")
        input()
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
