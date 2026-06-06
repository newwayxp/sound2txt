# Sound2Text

实时音频转写 + AI 纠错 + 会议纪要自动生成的 Windows 桌面工具。

通过 Windows WASAPI 捕获系统音频和麦克风，使用 faster-whisper 进行本地语音识别，并调用 LLM 生成结构化会议纪要。

[English](README_en.md) | [日本語](README_ja.md) | 简体中文

---

## 功能特性

- **实时录音转写** — WASAPI 环回捕获系统音频，无需手动配置设备，发言结束后立即转写
- **麦克风支持** — 点击音量条随时开始/停止麦克风录音，转写内容带说话人标记（`【自己】`/`[Me]`）
- **回声消除（AEC）** — 自动抑制扬声器声音被麦克风拾取造成的二次转写
- **两种录音模式** — 会议模式（系统音 + 可选麦克风）和本地 Mic 模式（仅麦克风）
- **多语言支持** — 自动识别中文 / 日语 / 英语，每次会话重新读取语言设置
- **GPU 加速** — 有 NVIDIA GPU 时自动启用 CUDA，速度提升约 10 倍
- **AI 纠错** — LLM 修正同音字、补全标点、整理段落（可通过 `enable_correction = false` 关闭）
- **会议纪要** — 自动生成结构化纪要，语言与录音一致
- **自定义术语** — 术语表文件提升专有名词识别准确率
- **企业代理支持** — 支持 HTTP/HTTPS 代理及自签名证书

---

## 系统要求

| 项目 | 要求 |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Python | 3.10 以上 |
| RAM | 4 GB 以上（large-v3 模型建议 8 GB）|
| GPU | 可选，NVIDIA CUDA（有 GPU 时自动启用）|

---

## 安装

### 方法一：运行 setup.bat（推荐）

1. 先安装 [Python 3.10+](https://www.python.org/downloads/)（勾选 **Add Python to PATH**）
2. 双击 `setup.bat`

脚本会自动完成：安装 Python 依赖、安装 ffmpeg、创建 `run.bat` 和桌面快捷方式。

> **企业代理环境：** 运行 `setup.bat` 时会询问代理地址，填入后自动应用。  
> 也可以在启动后的 **🌐 Network** 设置标签页中配置。

### 方法二：手动安装

```powershell
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
pip install -r requirements.txt
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
copy config_default.ini config.ini
```

企业代理环境の場合：

```powershell
pip install -r requirements.txt --proxy http://プロキシアドレス:ポート --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

---

## 启动

### 使用安装包（.exe）安装后

- 点击**开始菜单** → `Sound2Text` 即可启动
- 安装时若选择了创建桌面图标，也可以直接双击桌面图标启动

### 使用 setup.bat 安装后

- 双击桌面上自动创建的 **Sound2Text 快捷方式**
- 或者打开安装目录，双击 `run.bat`

### 命令行启动（开发者）

在安装目录下运行：

```powershell
python ui_qt.py
```

---

## 使用方法

### 1. 配置 API Key（首次使用）

启动后点击 **纪要/API** 标签页，填入 LLM API Key：

| 服务 | 地址 | 费用 |
|---|---|---|
| **Groq**（推荐） | https://console.groq.com | 每天免费额度 |
| DeepSeek | https://platform.deepseek.com | 极低（按量计费）|
| 阿里云百炼 | https://bailian.console.aliyun.com | 新用户有赠送 |
| Ollama（本地） | 本地运行，无需 Key | 完全免费 |

### 2. 选择录音模式

控制栏中间的切换按钮：

| 模式 | 说明 |
|---|---|
| **会议模式** | 录制系统音频（对方声音）+ 可选麦克风（自己声音）|
| **本地 Mic** | 仅录制麦克风输入（适合单人讲述场景）|

### 3. 开始录音

点击绿色 **▶ 开始** 按钮，按钮变为红色 **■ 停止**，同时右侧出现音量指示区。

### 4. 麦克风 ON AIR 控制

| 操作 | 效果 |
|---|---|
| 点击音量条 | 开始麦克风录音，左侧圆点变红 🔴 |
| 再次点击 | 停止麦克风录音，圆点变蓝 🔵 |

会议模式下，系统音频全程录制；点击音量条只控制麦克风。

### 5. 停止并生成纪要

点击红色 **■ 停止**，程序依次执行：
1. 停止录音
2. 完成剩余文件的转写
3. AI 纠错（Step 1/2）
4. 生成会议纪要（Step 2/2）
5. 按钮恢复为绿色 **▶ 开始**

---

## 界面说明

```
[▶ 开始/■ 停止] | [会议模式][本地Mic] | [🔵 音量条] | ... | 语言 [▼]
```

- **开始/停止** — 单一切换按钮，绿色=待机，红色=录音中
- **模式切换** — 会议模式 / 本地 Mic 切换
- **🔵/🔴 圆点** — ON AIR 指示灯（蓝=空闲，红=麦克风录音中）
- **音量条** — 点击开始/停止麦克风，动态显示音量

---

## 输出文件

| 文件 | 默认位置 |
|---|---|
| 原始转写 | `C:\Users\Public\Sound2Text\transcript\transcript_*.txt` |
| 纠错文本 | `C:\Users\Public\Sound2Text\corrected\corrected_*.txt` |
| 会议纪要 | `C:\Users\Public\Sound2Text\memo\summary_*.md` |
| 音频（含麦克风混合）| `C:\Users\Public\Sound2Text\audio\audio_*.mp3` |

路径可在 **📁 路径** 设置标签页中修改。

---

## Whisper 模型对比

| 模型 | CPU（30s音频）| GPU（30s音频）| 精度 | 大小 |
|---|---|---|---|---|
| tiny | ~0.3s | ~0.1s | 低 | 75 MB |
| small | ~3s | ~0.3s | 中（推荐）| 244 MB |
| medium | ~15s | ~0.8s | 高 | 769 MB |
| large-v3 | ~40s | ~2s | 最高 | 1.5 GB |

首次使用时自动下载，存储在 `%USERPROFILE%\.cache\huggingface\`。

---

## 自定义术语

编辑术语表文件（**与输出文件放在一起，方便管理**）：

```
C:\Users\Public\Sound2Text\corrected\vocabulary.txt
```

每行一个词：

```
Anthropic
ChatGPT
田中一郎
Docker Compose
```

术语同时传入 Whisper `initial_prompt` 和 LLM 提示词，提升专有名词识别准确率。  
首次启动时，程序会自动将程序目录的 `vocabulary.txt` 复制到上記位置。

路径は **📁 路径** 设置标签页的 `vocab_file` 项目で変更可能。

---

## 企业代理设置

在 **🌐 Network** 设置标签页中填入代理地址，或直接编辑 `config.ini`：

```ini
[network]
https_proxy = http://proxy.company.com:8080
http_proxy  = http://proxy.company.com:8080
ssl_verify  = true   # 自签名证书时设为 false
```

---

## 调试工具

`debug_modules.py` 提供独立的管道测试，无需完整启动 UI：

```powershell
# 测试环回录音设备列表 + 5秒采样
python debug_modules.py loopback

# 录制15秒系统音频并转写（验证 WASAPI 捕获）
python debug_modules.py audio

# 录制15秒麦克风并转写
python debug_modules.py mic

# 同时录制系统音+麦克风，验证合并转写
python debug_modules.py pipeline

# 自定义时长（秒）
python debug_modules.py audio 30
```

---

## 文件结构

```
ui_qt.py            # PyQt6 主界面（MVP View）
widgets_qt.py       # 自定义 QPainter 控件（VU 表、七段数码管）
presenter.py        # 业务逻辑（MVP Presenter）
pipeline.py         # 音频捕获 → VAD → 转写 → 纠错 一体化管道进程
appconfig.py        # 配置读写 + CUDA 检测
i18n.py             # 多语言翻译
log_util.py         # 结构化日志工具
summarizer.py       # 会议纪要生成
device_utils.py     # WASAPI 设备自动选择
mic_recorder.py     # 麦克风独立录制工具（诊断用）
transcriber.py      # 转写独立工具（诊断用）
debug_modules.py    # 诊断测试工具
config_default.ini  # 默认配置模板
requirements.txt    # Python 依赖
setup.bat           # 新机器一键安装脚本
```

> `config.ini` は機器固有の設定のため `.gitignore` で除外。

---

## 常见问题

**未检测到环回设备**
> 控制面板 → 声音 → 录制 → 立体声混音 → 启用

**音量条没有反应**
> 运行 `python debug_modules.py loopback` 查看设备选择和音量。  
> 会议软件可能使用了独立的音频输出设备，需在 Windows 声音设置中检查默认输出。

**FP16 警告**
> 使用 CUDA GPU 时自动解决；CPU 模式下不影响运行。

**模型下载慢 / SSL 错误**
> 在 **🌐 Network** 标签页设置企业代理并将 `ssl_verify` 设为 `false`。  
> 中国大陆可设置镜像：`HF_ENDPOINT=https://hf-mirror.com`

**停止后长时间不结束**
> 正在处理录音结束前残留的音频文件，进度显示在日志中，请等待完成。

---

## 注意事项

> **录音和转写前，请务必取得所有参会者的同意。**  
> 未经许可的录音可能违反相关法律法规。

---

## 依赖

| 库 | 用途 |
|---|---|
| pyaudiowpatch | Windows WASAPI 音频捕获（环回 + 麦克风）|
| faster-whisper | 语音识别（OpenAI Whisper 优化版）|
| PyQt6 | GUI 框架 |
| scipy | 回声消除（AEC）信号处理 |
| requests | LLM API 调用 |
| numpy | 音频数据处理 |
| ffmpeg | MP3 转换 + 麦克风混合（adelay/amix）|
