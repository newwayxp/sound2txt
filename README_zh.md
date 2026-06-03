# Sound2Text

实时音频转写 + AI 纠错 + 会议纪要自动生成的 Windows 桌面工具。

通过 Windows WASAPI 捕获系统音频，使用 faster-whisper 进行本地语音识别，并调用 LLM 生成结构化会议纪要。

[English](README_en.md) | [日本語](README_ja.md) | 简体中文

---

## 功能特性

- **实时录音转写** — 自动检测音频输出设备，无需手动配置
- **多语言支持** — 自动识别中文 / 日语 / 英语，下次启动直接跳过检测
- **GPU 加速** — 有 NVIDIA GPU 时自动启用 CUDA，速度提升约 10 倍
- **AI 纠错** — 调用 LLM 修正同音字、补充标点、整理段落
- **会议纪要** — 一键生成结构化纪要，语言与录音保持一致
- **自定义术语** — `vocabulary.txt` 提升专有名词识别准确率
- **企业代理支持** — 支持 HTTP/HTTPS 代理及自签名证书

---

## 系统要求

| 项目 | 要求 |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Python | 3.8 以上 |
| RAM | 4 GB 以上 |
| GPU | 可选，NVIDIA CUDA（有 GPU 时自动启用）|

---

## 安装

### 方法一：使用安装包（推荐）

从 [Releases](../../releases) 下载 `Sound2Text_Setup_x.x.x.exe`，双击运行。

> 安装前需先安装 Python 3.8+（安装时勾选 **Add Python to PATH**）

### 方法二：手动安装

```powershell
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
pip install -r requirements.txt
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
```

---

## 使用方法

### GUI 启动（推荐）

```powershell
python ui.py
```

点击 **▶ 开始** 录音，点击 **■ 停止** 结束并自动生成会议纪要。

### 命令行启动

```powershell
python start.py
```

按 `Ctrl+C` 停止。

---

## 初次配置（API Key）

启动后在 **⚙ 纪要/API** 标签页填入 API Key：

| 服务 | 获取地址 | 费用 |
|---|---|---|
| **Groq**（推荐） | https://console.groq.com | 每天 14,400 次免费 |
| DeepSeek | https://platform.deepseek.com | 极低（按量计费）|
| 阿里云百炼 | https://bailian.console.aliyun.com | 新用户赠送 token |
| Ollama（本地） | 本地运行，无需 API Key | 完全免费 |

---

## 输出文件

| 文件 | 位置 |
|---|---|
| 原始转写 | `Sound2Text\transcript\transcript_*.txt` |
| 纠错文本 | `Sound2Text\corrected\corrected_*.txt` |
| 会议纪要 | `Sound2Text\memo\summary_*.md` |
| 音频文件 | `Sound2Text\audio\audio_*.wav` |

---

## 自定义术语

编辑 `vocabulary.txt`，每行一个词：

```
Anthropic
ChatGPT
Docker
田中一郎
```

术语会同时传入 Whisper `initial_prompt` 和 LLM 纠错提示，提升识别准确率。

---

## 企业代理设置

在 **🌐 Network** 设置标签页或直接编辑 `config.ini`：

```ini
[network]
https_proxy = http://proxy.company.com:8080
http_proxy  = http://proxy.company.com:8080
ssl_verify  = true   # 自签名证书时设为 false
```

---

## Whisper 模型对比

| 模型 | CPU 速度 | GPU 速度 | 精度 |
|---|---|---|---|
| tiny | 约 0.3 秒/30 秒音频 | 约 0.1 秒 | 低 |
| small | 约 3 秒/30 秒音频 | 约 0.3 秒 | 中（推荐）|
| medium | 约 15 秒/30 秒音频 | 约 0.8 秒 | 高 |
| large-v3 | 约 40 秒/30 秒音频 | 约 2 秒 | 最高 |

---

## 注意事项

> **进行录音和转写时，请事先获得所有会议参与者的同意。**
> 未经许可的录音可能违反相关法律法规。

---

## 依赖库

| 库 | 用途 |
|---|---|
| pyaudiowpatch | Windows WASAPI 音频捕获 |
| faster-whisper | 语音识别（OpenAI Whisper 优化版）|
| customtkinter | GUI 框架 |
| requests | LLM API 调用 |
| ffmpeg | 音频格式转换 |
