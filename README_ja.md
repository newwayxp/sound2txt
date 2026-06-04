# Sound2Text

リアルタイム音声文字起こし + AI 校正 + 会議議事録自動生成 Windows デスクトップツール。

Windows WASAPI でシステム音声・マイクを捕捉し、faster-whisper でローカル音声認識、LLM で構造化された議事録を生成します。

[English](README_en.md) | 日本語 | [简体中文](README_zh.md)

---

## 機能

- **リアルタイム録音・文字起こし** — WASAPI ループバックで音声出力を自動取得
- **マイク対応** — 音量バーをクリックするだけでマイク録音 ON/OFF、発言者ラベル付き
- **2 種類の録音モード** — 会議モード（システム音 + マイク）/ ローカル Mic モード（マイクのみ）
- **多言語対応** — 中国語 / 日本語 / 英語を自動検出
- **GPU アクセラレーション** — NVIDIA GPU があれば自動で CUDA を使用
- **AI 校正** — LLM による誤変換修正・句読点補完・段落整理
- **議事録生成** — 構造化議事録を自動生成
- **カスタム用語辞書** — `vocabulary.txt` で固有名詞の認識精度を向上
- **社内プロキシ対応** — HTTP/HTTPS プロキシ・自己署名証明書をサポート

---

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Python | 3.10 以上 |
| RAM | 4 GB 以上（large-v3 は 8 GB 推奨）|
| GPU | 任意、NVIDIA CUDA（あれば自動使用）|

---

## インストール

### 方法 1：setup.bat を実行（推奨）

1. [Python 3.10+](https://www.python.org/downloads/) をインストール（**Add Python to PATH** にチェック）
2. `setup.bat` をダブルクリック

Python 依存パッケージ・ffmpeg のインストール、`run.bat` とデスクトップショートカットの作成が自動で完了します。

### 方法 2：手動インストール

```powershell
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
pip install -r requirements.txt
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
copy config_default.ini config.ini
```

---

## 起動

### インストーラー（.exe）でインストールした場合

- **スタートメニュー** → `Sound2Text` をクリックして起動
- インストール時にデスクトップアイコンを作成した場合は、そちらをダブルクリックしても起動できます

### setup.bat でインストールした場合

- デスクトップに自動作成された **Sound2Text ショートカット** をダブルクリック
- またはインストールフォルダ内の `run.bat` をダブルクリック

### コマンドラインから起動（開発者向け）

インストールフォルダで以下を実行：

```powershell
python ui_qt.py
```

---

## 使い方

### 1. API Key の設定（初回のみ）

**纪要/API** タブで LLM API Key を設定：

| サービス | 取得先 | 費用 |
|---|---|---|
| **Groq**（推奨） | https://console.groq.com | 1日 14,400 リクエスト無料 |
| DeepSeek | https://platform.deepseek.com | 従量課金（非常に安価）|
| 阿里云百炼 | https://bailian.console.aliyun.com | 新規ユーザー無料枠あり |
| Ollama（ローカル） | ローカル実行・API Key 不要 | 完全無料 |

### 2. 録音モードを選択

コントロールバー中央の切り替えボタン：

| モード | 説明 |
|---|---|
| **会議モード** | システム音声（相手の声）+ オプションでマイク（自分の声）|
| **ローカル Mic** | マイクのみ（一人語り・講義録音に最適）|

### 3. 録音開始

緑の **▶ 開始** ボタンをクリック → 赤の **■ 停止** に変わり、右側に音量インジケーターが表示されます。

### 4. マイク ON AIR 操作

| 操作 | 効果 |
|---|---|
| 音量バーをクリック | マイク録音開始、円形インジケーターが赤 🔴 に |
| もう一度クリック | マイク録音停止、インジケーターが青 🔵 に |

### 5. 停止と議事録生成

**■ 停止** をクリックすると自動的に：文字起こし → AI 校正 → 議事録生成 が順次実行され、完了後にボタンが緑の **▶ 開始** に戻ります。

---

## コントロールバー

```
[▶開始/■停止] | [会議モード][ローカルMic] | [🔵 音量バー] | ... | 言語 [▼]
```

- **🔵/🔴 インジケーター** — 青=待機、赤=マイク録音中
- **音量バー** — クリックでマイク ON/OFF、レベルをリアルタイム表示

---

## 出力ファイル

| ファイル | デフォルトの場所 |
|---|---|
| 文字起こし | `C:\Users\Public\Sound2Text\transcript\transcript_*.txt` |
| 校正テキスト | `C:\Users\Public\Sound2Text\corrected\corrected_*.txt` |
| 議事録 | `C:\Users\Public\Sound2Text\memo\summary_*.md` |
| 音声（システム）| `C:\Users\Public\Sound2Text\audio\audio_*.wav` |
| 音声（マイク）| `C:\Users\Public\Sound2Text\mic\mic_*.wav` |

---

## Whisper モデル比較

| モデル | 速度（CPU）| 速度（GPU）| 精度 | サイズ |
|---|---|---|---|---|
| tiny | ~0.3s/30s | ~0.1s | 低 | 75 MB |
| small | ~3s/30s | ~0.3s | 中（推奨）| 244 MB |
| medium | ~15s/30s | ~0.8s | 高 | 769 MB |
| large-v3 | ~40s/30s | ~2s | 最高 | 1.5 GB |

---

## デバッグツール

```powershell
python debug_modules.py loopback    # ループバックデバイス確認 + 5秒サンプリング
python debug_modules.py audio       # 15秒録音 → 文字起こし（WASAPI 動作確認）
python debug_modules.py mic         # 15秒マイク録音 → 文字起こし
python debug_modules.py pipeline    # 同時録音 → マージ確認
python debug_modules.py audio 30    # 30秒に変更
```

---

## ファイル構成

```
ui_qt.py            GUI メインウィンドウ（PyQt6）
widgets_qt.py       カスタム QPainter ウィジェット
presenter.py        ビジネスロジック（Presenter）
appconfig.py        設定 I/O + CUDA 検出
start.py            CLI 起動スクリプト（GUI なし）
recorder.py         システム音声録音プロセス
mic_recorder.py     マイク録音プロセス
transcriber.py      文字起こしプロセス
summarizer.py       AI 校正 + 議事録生成
debug_modules.py    診断ツール
config.ini          ユーザー設定（git 管理外）
config_default.ini  デフォルト設定テンプレート
vocabulary.txt      カスタム用語辞書
requirements.txt    Python 依存ライブラリ
setup.bat           新規マシン用セットアップスクリプト
```

---

## トラブルシューティング

**ループバックデバイスが見つからない**
> コントロールパネル → サウンド → 録音タブ → ステレオミキサー → 有効化

**音量バーに反応がない**
> `python debug_modules.py loopback` でデバイス状況を確認してください。

**モデルのダウンロードが遅い**
> `set HF_ENDPOINT=https://hf-mirror.com` を設定してから実行（中国国内ミラー）

---

## 注意事項

> **録音・文字起こしを行う際は、会議参加者全員の事前同意を得てください。**
> 無断録音は法律に違反する可能性があります。

---

## 依存ライブラリ

| ライブラリ | 用途 |
|---|---|
| pyaudiowpatch | Windows WASAPI ループバック + マイク録音 |
| faster-whisper | 高速音声認識（OpenAI Whisper 最適化版）|
| PyQt6 | GUI フレームワーク |
| requests | LLM API 呼び出し |
| numpy | 音声データ処理 |
| ffmpeg | 音声デコード（faster-whisper 内部使用）|
