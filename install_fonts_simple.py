#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_fonts_simple.py - 最简化字体安装脚本

这是最简单稳定的版本，直接尝试复制字体文件。
"""
import sys
import shutil
from pathlib import Path

# 确保 UTF-8 输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def main():
    print("\n" + "=" * 60)
    print("Sound2Text 字体安装工具")
    print("=" * 60 + "\n")

    fonts_dir = Path(__file__).parent / "fonts"
    windows_fonts_dir = Path("C:\\Windows\\Fonts")

    fonts_to_install = [
        "ShareTechMono-Regular.ttf",
        "JetBrainsMono-Regular.ttf",
        "JetBrainsMono-Bold.ttf",
        "JetBrainsMono-BoldItalic.ttf",
    ]

    # 检查字体目录
    if not fonts_dir.exists():
        print(f"错误：字体目录不存在")
        print(f"路径: {fonts_dir}")
        return False

    print(f"字体目录: {fonts_dir}")
    print(f"目标目录: {windows_fonts_dir}\n")
    print("安装字体...\n")

    success_count = 0
    failed_count = 0

    for font_file in fonts_to_install:
        src = fonts_dir / font_file
        dst = windows_fonts_dir / font_file

        if not src.exists():
            print(f"⊘ 跳过: {font_file} (文件不存在)")
            continue

        if dst.exists():
            print(f"✓ 已存在: {font_file}")
            success_count += 1
            continue

        try:
            shutil.copy2(src, dst)
            print(f"✓ 已安装: {font_file}")
            success_count += 1
        except PermissionError:
            print(f"✗ 权限不足: {font_file}")
            print("   (需要管理员权限)")
            failed_count += 1
        except Exception as e:
            print(f"✗ 失败: {font_file} - {str(e)}")
            failed_count += 1

    # 总结
    print("\n" + "=" * 60)
    print("安装总结")
    print("=" * 60)
    print(f"成功: {success_count}")
    print(f"失败: {failed_count}\n")

    if failed_count > 0:
        print("⚠️  部分字体安装失败")
        print("\n解决方案:")
        print("1. 右键点击本脚本")
        print("2. 选择'以管理员身份运行'")
        print("\n或者:")
        print("1. 打开 CMD (Win+R, 输入 cmd)")
        print("2. 右键选择'以管理员身份运行'")
        print("3. 输入: python install_fonts_simple.py")
        return False
    else:
        print("✓ 字体安装完成！")
        print("\n提示: 请重启应用以使用新字体。")
        return True


if __name__ == "__main__":
    try:
        success = main()
        print("\n按任意键退出...", end="")
        sys.stdout.flush()
        input()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n取消安装")
        sys.exit(1)
    except Exception as e:
        print(f"\n错误: {e}")
        print("按任意键退出...", end="")
        sys.stdout.flush()
        input()
        sys.exit(1)
