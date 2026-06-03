# Sound2Text

リアルタイム音声文字起こし + AI 纠错 + 会議纪要自動生成 Windows デスクトップツール。

Windows WASAPI でシステム音声をキャプチャし、faster-whisper でローカル音声認識を行い、LLM で構造化された会議纪要を生成します。

[English](README_en.md) | 日本語 | [简体中文](README_zh.md)

---

## 機能

- **リアルタイム録音・文字起こし** — 音声出力デバイスを自動検出、手動設定不要
- **多言語対応** — 中国語 / 日本語 / 英語を自動検出、次回起動時は検出をスキップ
- **GPU アクセラレーション** — NVIDIA GPU があれば自動で CUDA を使用、約 10 倍高速化
- **AI 纠错** — LLM による同音異字の修正・句読点補完・段落整理
- **会議纪要生成** — ワンクリックで構造化纪要を生成、録音の言語に合わせて出力
- **カスタム用語辞書** — `vocabulary.txt` で固有名詞の認識精度を向上
- **社内プロキシ対応** — HTTP/HTTPS プロキシ・自己署名証明書をサポート

---

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Python | 3.8 以上 |
| RAM | 4 GB 以上 |
| GPU | 任意、NVIDIA CUDA（あれば自動使用）|

---

## インストール

### 方法 1：インストーラーを使用（推奨）

[Releases](../../releases) から `Sound2Text_Setup_x.x.x.exe` をダウンロードして実行。

> Python 3.8 以上を事前にインストールしてください（**Add Python to PATH** にチェック）

### 方法 2：手動インストール

```powershell
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
pip install -r requirements.txt
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
```

---

## 使い方

### GUI 起動（推奨）

```powershell
python ui.py
```

**▶ 開始** をクリックして録音開始、**■ 停止** で終了し会議纪要を自動生成。

### コマンドライン起動

```powershell
python start.py
```

`Ctrl+C` で停止。

---

## 初期設定（API Key）

起動後、**⚙ 纪要/API** タブで API Key を設定：

| サービス | 取得先 | 費用 |
|---|---|---|
| **Groq**（推奨） | https://console.groq.com | 1日 14,400 リクエスト無料 |
| DeepSeek | https://platform.deepseek.com | 従量課金（非常に安価）|
| 阿里云百炼 | https://bailian.console.aliyun.com | 新規ユーザーに無料トークン |
| Ollama（ローカル） | ローカル実行、API Key 不要 | 完全無料 |

---

## 出力ファイル

| ファイル | 保存先 |
|---|---|
| 原文転写 | `Sound2Text\transcript\transcript_*.txt` |
| 纠错済みテキスト | `Sound2Text\corrected\corrected_*.txt` |
| 会議纪要 | `Sound2Text\memo\summary_*.md` |
| 音声ファイル | `Sound2Text\audio\audio_*.wav` |

---

## カスタム用語辞書

`vocabulary.txt` を編集し、1行1用語で記述：

```
Anthropic
ChatGPT
Docker
田中一郎
```

Whisper の `initial_prompt` と LLM 纠错プロンプトの両方に渡され、認識精度が向上します。

---

## 社内プロキシ設定

**🌐 ネットワーク** タブ、または `config.ini` を直接編集：

```ini
[network]
https_proxy = http://proxy.company.com:8080
http_proxy  = http://proxy.company.com:8080
ssl_verify  = true   # 自己署名証明書の場合は false
```

---

## Whisper モデル比較

| モデル | CPU 速度 | GPU 速度 | 精度 |
|---|---|---|---|
| tiny | 約 0.3 秒/30 秒音声 | 約 0.1 秒 | 低 |
| small | 約 3 秒/30 秒音声 | 約 0.3 秒 | 中（推奨）|
| medium | 約 15 秒/30 秒音声 | 約 0.8 秒 | 高 |
| large-v3 | 約 40 秒/30 秒音声 | 約 2 秒 | 最高 |

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
