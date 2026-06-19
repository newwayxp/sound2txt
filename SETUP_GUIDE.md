# Sound2Text 设置和启动指南

## 📋 概述

Sound2Text 需要以下步骤来设置和运行：
1. **安装字体**（第一次运行时）
2. **启动应用**

## 🚀 快速开始

### 方式 1: 双击启动（推荐）

1. 打开项目文件夹
2. **双击** `run_gui.bat`
3. 如果提示字体缺失，选择"是"自动安装

### 方式 2: 使用 Python 直接启动

```bash
python run_gui.py
```

## 🔤 字体设置

### 自动安装（首次运行时）

应用启动时会自动检查字体：
- 如果字体已安装 ✓ → 直接启动应用
- 如果字体缺失 ⚠️  → 弹出安装提示

### 手动安装字体

如果自动安装失败，可以手动安装：

**方式 1: 双击 Python 脚本（推荐！）**
```
双击 install_fonts_easy.py 或 install_fonts_easy.bat
```
最简单，自动请求管理员权限。

**方式 2: 双击批处理脚本**
```
右键点击 install_fonts.bat → "以管理员身份运行"
```

**方式 3: 使用 Python 完整版**
```bash
python install_fonts.py
```

**方式 4: 使用 PowerShell**
```powershell
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
.\install_fonts.ps1
```

详见 [FONTS_INSTALL.md](FONTS_INSTALL.md)

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| **`install_fonts_easy.py`** | 🎯 **推荐** - 简易 Python 字体安装器（自动请求管理员权限） |
| **`install_fonts_easy.bat`** | 简易字体安装批处理脚本 |
| **`run_gui.bat`** | 🎯 **推荐** - 启动应用的脚本 |
| `run_gui.py` | Python GUI 启动器 + 字体检查 |
| `install_fonts.bat` | 标准字体安装脚本 |
| `install_fonts.py` | 完整 Python 字体安装器 |
| `install_fonts.ps1` | PowerShell 字体安装脚本 |
| `font_checker.py` | 字体检查模块 |
| `fonts/` | 字体文件目录 |

## 🔧 故障排除

### 问题：双击 run_gui.bat 后没有反应

**解决方案：**
1. 打开 CMD 运行 `python run_gui.py` 查看错误信息
2. 检查 Python 是否已安装：`python --version`

### 问题：应用启动时提示字体缺失

**解决方案：**
1. 点击"是"自动安装字体
2. 如果自动安装失败，双击 `install_fonts_easy.py` 或 `install_fonts_easy.bat`
3. 重启应用

### 问题：安装字体时提示权限不足

**解决方案：**
1. **右键点击** install_fonts.bat
2. 选择 **"以管理员身份运行"**

## 📊 系统要求

- **Windows 10/11**
- **Python 3.8+**
- **管理员权限**（首次安装字体时）

## 🎯 首次运行清单

- [ ] Python 已安装 (`python --version`)
- [ ] 打开 `run_gui.bat` 或 `run_gui.py`
- [ ] 按提示安装字体（如需要）
- [ ] 应用启动成功

## 📝 自定义

### 使用其他字体

如果要更改字体，编辑 `ui_qt_design.py` 中的字体定义：

```python
# 查找以下行并修改
font = QFont("Share Tech Mono", 22)  # 改为其他字体名称
```

### 禁用字体检查

在 `run_gui.py` 中改为：
```python
fonts_ok = ensure_fonts_installed(use_gui=False)
```

## 🔗 相关文件

- [FONTS_INSTALL.md](FONTS_INSTALL.md) - 详细的字体安装说明
- [README.md](README.md) - 应用功能说明
- [ARCHITECTURE.md](ARCHITECTURE.md) - 代码架构说明

## 📞 获取帮助

如果遇到问题：
1. 查看上面的"故障排除"部分
2. 检查控制台错误信息（直接运行 `python run_gui.py`）
3. 查看 [FONTS_INSTALL.md](FONTS_INSTALL.md) 的字体相关问题
