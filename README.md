# Sound2Text

[English](README_en.md) | [日本語](README_ja.md) | 简体中文

实时音频转写 + AI 纠错 + 会议纪要自动生成的 Windows 桌面工具。

通过 Windows WASAPI 捕获系统音频，使用 faster-whisper 进行本地语音识别，并调用 LLM 生成结构化会议纪要。

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

## 初次配置

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
| 原始转写 | `C:\Users\Public\Sound2Text\transcript\transcript_*.txt` |
| 纠错文本 | `C:\Users\Public\Sound2Text\corrected\corrected_*.txt` |
| 会议纪要 | `C:\Users\Public\Sound2Text\memo\summary_*.md` |
| 音频文件 | `C:\Users\Public\Sound2Text\audio\audio_*.wav` |

保存路径可在 **📁 路径** 设置标签页中修改。

---

## 自定义术语

编辑 `vocabulary.txt`，每行一个词：

```
Anthropic
ChatGPT
Docker
田中一郎
```

术语会同时传入 Whisper 的 `initial_prompt` 和 LLM 纠错提示，提升专有名词识别准确率。

---

## 文件结构

```
├── ui.py               # GUI 主界面（customtkinter）
├── start.py            # 命令行启动器
├── recorder.py         # 录音进程
├── transcriber.py      # 转写进程
├── summarizer.py       # 纠错 + 会议纪要生成
├── device_utils.py     # 音频设备自动检测
├── config.ini          # 用户配置（不含 API Key 时可提交）
├── config_default.ini  # 安装包默认配置
├── vocabulary.txt      # 自定义术语表
├── requirements.txt    # Python 依赖
├── setup.bat           # 手动安装脚本
├── installer.iss       # Inno Setup 安装包脚本
└── build_installer.bat # 构建安装包
```

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

## Whisper モデル比較

| モデル | 速度（CPU） | 速度（GPU） | 精度 |
|---|---|---|---|
| tiny | 約 0.3秒/30秒音声 | 約 0.1秒 | 低 |
| small | 約 3秒/30秒音声 | 約 0.3秒 | 中（推奨）|
| medium | 約 15秒/30秒音声 | 約 0.8秒 | 高 |
| large-v3 | 約 40秒/30秒音声 | 約 2秒 | 最高 |

モデルは **🎙 録音** 設定タブで変更できます。

---

## トラブルシューティング

**ループバックデバイスが見つからない**
> `コントロールパネル → サウンド → 録音タブ → ステレオミキサー → 有効化`

**文字起こしが出ない**
> `record_test.py` で音声が録音できているか確認してください。

**FP16 警告が表示される**
> GPU（CUDA）使用時は自動的に解消されます。CPU 使用時は動作に影響ありません。

**インストーラーのビルド**
> `build_installer.bat` をダブルクリック。初回は自動で Inno Setup を検索してパスをキャッシュします。

---

## 注意事項

> **録音・文字起こしを行う際は、会議参加者全員の同意を事前に得てください。**
> 無断録音は各国の法律に抵触する可能性があります。

---

## 依存ライブラリ

| ライブラリ | 用途 |
|---|---|
| pyaudiowpatch | Windows WASAPI ループバック録音 |
| faster-whisper | 高速音声認識（OpenAI Whisper 最適化版）|
| customtkinter | GUI フレームワーク |
| requests | LLM API 呼び出し |
| ffmpeg | 音声フォーマット変換 |
