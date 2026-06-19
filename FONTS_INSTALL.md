# Sound2Text 字体安装指南

Sound2Text 使用自定义字体来获得最佳的 UI 视觉效果：
- **Share Tech Mono** - 计时器显示
- **JetBrains Mono** - 日志和代码显示

## 自动安装方法（推荐）

### 方法 1: 双击批处理文件（最简单）

1. 在项目目录中找到 `install_fonts.bat`
2. **右键点击** → **"以管理员身份运行"**
3. 按照提示完成安装

### 方法 2: 使用 Python 脚本（简易版）

**推荐！** 这个版本最易用，直接双击即可：

1. 找到 `install_fonts_easy.py` 或 `install_fonts_easy.bat`
2. **双击** 运行
3. 按照提示完成安装

或在命令行运行：
```bash
python install_fonts_easy.py
```

### 方法 2B: 使用完整 Python 脚本

如需更多选项，使用原版脚本：

```bash
# 从项目目录运行（需要管理员权限）
python install_fonts.py
```

### 方法 3: 使用 PowerShell

```powershell
# 以管理员身份打开 PowerShell，然后运行：
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
.\install_fonts.ps1
```

## 检查安装

安装完成后，可以通过以下方式验证：

1. **Windows 设置** → **应用** → **字体**
2. 搜索 "Share Tech Mono" 或 "JetBrains Mono"
3. 应该能看到已安装的字体

## 手动安装（如果自动安装失败）

如果上述方法无效，可以手动安装：

1. 打开 `fonts` 文件夹
2. 对于每个 `.ttf` 文件：
   - **右键点击** → **安装**
   - 或者复制到 `C:\Windows\Fonts`

## 故障排除

### 问题：权限被拒绝

**解决方案：**
- 确保以**管理员身份**运行脚本
- 右键点击并选择"以管理员身份运行"

### 问题：找不到字体文件

**解决方案：**
- 确保 `fonts/` 文件夹存在于项目目录
- 文件夹应包含 `.ttf` 字体文件

### 问题：安装后字体仍未显示

**解决方案：**
- 重启应用
- 如需要，重启 Windows

## 包含的字体

### Share Tech Mono
- 用途：计时器和数字显示
- 文件：`ShareTechMono-Regular.ttf`

### JetBrains Mono
- 用途：日志、代码显示
- 文件：
  - `JetBrainsMono-Regular.ttf`
  - `JetBrainsMono-Bold.ttf`
  - `JetBrainsMono-BoldItalic.ttf`
  - 其他变体...

## 技术细节

- 字体存储在项目的 `fonts/` 目录
- 安装脚本会将字体复制到 `C:\Windows\Fonts`
- 安装后，字体对所有应用可用

## 相关文件

- `install_fonts.bat` - Windows 批处理脚本（推荐）
- `install_fonts.py` - Python 脚本
- `install_fonts.ps1` - PowerShell 脚本
- `fonts/` - 字体文件目录
