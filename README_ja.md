# Sound2Text

リアルタイム音声文字起こし + AI 校正 + 会議議事録自動生成 デスクトップツール（Windows / macOS 対応）。

システム音声・マイクを捕捉し（Windows: WASAPI、macOS: BlackHole）、faster-whisper でローカル音声認識、LLM で構造化された議事録を生成します。

[English](README_en.md) | 日本語 | [简体中文](README_zh.md)

---

## 機能

- **リアルタイム録音・文字起こし** — WASAPI ループバックで音声出力を自動取得、発話終了後すぐに文字起こし
- **マイク対応** — 音量バーをクリックするだけでマイク録音 ON/OFF、発言者ラベル付き（`【自分】`）
- **エコー除去（AEC）** — スピーカー音声をマイクが拾って二重転写される問題を自動抑制
- **2 種類の録音モード** — 会議モード（システム音 + マイク）/ ローカル Mic モード（マイクのみ）
- **多言語対応** — 中国語 / 日本語 / 英語を自動検出、セッションごとに言語設定を再読み込み
- **GPU アクセラレーション** — NVIDIA GPU があれば自動で CUDA を使用
- **AI 校正** — LLM による誤変換修正・句読点補完・段落整理（`enable_correction = false` で無効化可）
- **議事録生成** — 構造化議事録を自動生成
- **カスタム用語辞書** — `vocabulary.txt` で固有名詞の認識精度を向上
- **社内プロキシ対応** — HTTP/HTTPS プロキシ・自己署名証明書をサポート

---

## 動作環境

### Windows

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Python | 3.10 以上 |
| RAM | 4 GB 以上（large-v3 は 8 GB 推奨）|
| GPU | 任意、NVIDIA CUDA（あれば自動使用）|

### macOS

| 項目 | 要件 |
|---|---|
| OS | macOS 12 Monterey 以上 |
| チップ | Apple Silicon（M1/M2/M3）または Intel |
| Python | Homebrew の Python 3.10 以上 |
| 仮想デバイス | **BlackHole 2ch**（システム音声キャプチャに必須）|

---

## インストール

### Windows — setup.bat を実行（推奨）

1. [Python 3.10+](https://www.python.org/downloads/) をインストール（**Add Python to PATH** にチェック）
2. `setup.bat` をダブルクリック

Python 依存パッケージ・ffmpeg のインストール、`run.bat` とデスクトップショートカットの作成が自動で完了します。

### Windows — 手動インストール

```powershell
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
pip install -r requirements.txt
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
copy config_default.ini config.ini
```

### macOS — setup_mac.sh を実行

```bash
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
bash setup_mac.sh
```

スクリプトが自動で行う処理：
- Homebrew で ffmpeg・BlackHole・portaudio をインストール
- Python パッケージをインストール（pyaudio・faster-whisper・scipy 等）
- `config.ini` を作成

**⚡ スクリプト実行中に Audio MIDI Setup が自動で開きます（手動操作が必要）：**

| # | 操作 |
|---|---|
| 1 | 左下の **[+]** ボタンをクリック |
| 2 | **「複数出力装置を作成」** を選択 |
| 3 | 右パネルで **BlackHole 2ch** と **お使いのスピーカー** の両方にチェック |
| 4 | 作成した装置を右クリック → **「このサウンド出力装置を使用」** |
| 5 | または：システム設定 → サウンド → 出力 → 「複数出力装置」を選択 |

Enter を押すとセットアップが完了します。

---

## 起動

### Windows — インストーラー（.exe）でインストールした場合

- **スタートメニュー** → `Sound2Text` をクリックして起動

### Windows — setup.bat でインストールした場合

- デスクトップに自動作成された **Sound2Text ショートカット** をダブルクリック
- またはインストールフォルダ内の `run.bat` をダブルクリック

### macOS

```bash
/opt/homebrew/bin/python3 ui_qt.py
```

> **注意：** macOS でシステム音声をキャプチャするには、事前に Audio MIDI Setup の設定が必要です（setup_mac.sh 実行時に案内されます）。

### Windows — コマンドライン（開発者向け）

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
| 音声（マイク混合済み）| `C:\Users\Public\Sound2Text\audio\audio_*.mp3` |

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
ui_qt.py            GUI メインウィンドウ（PyQt6、MVP View）
widgets_qt.py       カスタム QPainter ウィジェット（VU メーター・7 セグ時計）
presenter.py        ビジネスロジック（MVP Presenter）
pipeline.py         音声取得 → VAD → 転写 → 校正 一体型パイプライン
appconfig.py        設定 I/O + CUDA 検出
i18n.py             多言語翻訳
log_util.py         構造化ログユーティリティ
summarizer.py       議事録生成
device_utils.py     WASAPI デバイス自動選択
mic_recorder.py     マイク単独録音ツール（診断用）
transcriber.py      転写単独ツール（診断用）
debug_modules.py    診断テストツール
config_default.ini  デフォルト設定テンプレート
requirements.txt    Python 依存ライブラリ
setup.bat           新規マシン用セットアップスクリプト
```

---

## トラブルシューティング

**ループバックデバイスが見つからない（Windows）**
> コントロールパネル → サウンド → 録音タブ → ステレオミキサー → 有効化

**No audio input device found（macOS）**
> BlackHole が未インストール、またはシステム出力に設定されていません。
> `brew install --cask blackhole-2ch` を実行後、Audio MIDI Setup で複数出力装置を作成してください（インストール手順参照）。

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
| scipy | エコー除去（AEC）信号処理 |
| requests | LLM API 呼び出し |
| numpy | 音声データ処理 |
| ffmpeg | MP3 変換 + マイク音声混合（adelay/amix）|
