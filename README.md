# Zoom 会議 リアルタイム文字起こしツール

Zoom 会議の音声をリアルタイムでテキスト変換し、ファイルに保存するツールです。

---

## 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows 10 / 11 |
| Python | 3.8 以上 |
| GPU | 不要（CPU のみで動作） |
| Zoom | ヘッドセット・イヤホン使用可 |

---

## セットアップ

### 1. ライブラリのインストール

社内プロキシ経由でインストールする場合：

```powershell
pip install pyaudiowpatch faster-whisper --proxy http://<社内プロキシ>:<ポート> --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

プロキシなしの場合：

```powershell
pip install pyaudiowpatch faster-whisper
```

### 2. ffmpeg のインストール

```powershell
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
```

インストール後、PowerShell を再起動してください。

---

## ファイル構成

```
├── transcribe.py       # メイン：リアルタイム文字起こし
├── record_test.py      # デバッグ用：音声録音テスト
├── .gitignore          # *.wav, transcript_*.txt を除外
└── README.md           # このファイル
```

---

## 使い方

### Step 1: 音声キャプチャの確認（初回推奨）

Zoom を起動した状態で実行し、音声が正しく取れているか確認します。

```powershell
python record_test.py
```

- ループバックデバイス一覧が表示されます
- **ヘッドセット/イヤホンの番号**を選択してください
- 30秒録音後 `test_audio_日時.wav` が保存されます
- Windows Media Player で再生して相手の声が聞こえるか確認してください

### Step 2: リアルタイム文字起こし

```powershell
python transcribe.py
```

1. ループバックデバイスを選択（ヘッドセットの番号）
2. 5秒間の音量確認（Zoom の音声を流してください）
3. 文字起こし開始
4. `Ctrl+C` で停止 → `transcript_日時.txt` に保存

**出力例：**
```
=== Zoom文字起こし 2026-06-02 14:30:22 ===

[14:30:27] 本日はよろしくお願いします。
[14:30:35] では議題に入りましょう。
[14:30:41] 先週の進捗について報告します。

=== 終了 2026-06-02 15:00:10 ===
```

---

## 仕組み

```
Zoom音声
  → Windows WASAPI ループバック（ヘッドセット出力を取得）
  → 5秒ごとに WAV ファイルへ保存
  → faster-whisper (small モデル / int8量子化) で日本語認識
  → transcript_日時.txt に追記保存
```

---

## パラメータ調整

`transcribe.py` 冒頭の定数を変更することで動作を調整できます。

```python
RECORD_SEC        = 5    # 何秒ごとに文字変換するか（小さいほどリアルタイムに近い）
SILENCE_THRESHOLD = 800  # 無音判定の閾値（大きいほど静かな音を無視）
```

**モデルの変更（精度 vs 速度）：**

```python
model = WhisperModel("small", device="cpu", compute_type="int8")
#                     ↑ここを変更
# tiny  → 約0.6秒/5秒音声（高速・低精度）
# small → 約2.7秒/5秒音声（推奨）
# medium→ 約15秒/5秒音声（高精度・CPU では遅い）
```

---

## トラブルシューティング

### ループバックデバイスが見つからない

Windows のサウンド設定でステレオミキサーを有効にしてください：
`コントロールパネル → サウンド → 録音タブ → ステレオミキサー → 有効化`

### 文字起こしが全く出ない

`record_test.py` で音声が録音できているか確認してください。  
録音できているのに文字起こしが出ない場合は `SILENCE_THRESHOLD` を下げてください。

### 識別精度が低い

- `small` → `medium` モデルに変更する
- ヘッドセットの音量を上げる
- Zoom の「オーディオ設定 → スピーカー音量」を確認する

### ffmpeg が見つからないエラー

PowerShell を再起動してください（PATH の反映に再起動が必要です）。

### FP16 警告が表示される

CPU 環境では正常な警告です。動作に影響はありません。  
`transcribe.py` の `warnings.filterwarnings("ignore")` で非表示になります。

---

## 注意事項

> **録音・文字起こしを行う際は、会議参加者全員の同意を事前に得てください。**  
> 無断録音は日本の法律（不正競争防止法・プライバシー権）に抵触する可能性があります。

---

## 依存ライブラリ

| ライブラリ | バージョン | 用途 |
|---|---|---|
| pyaudiowpatch | 0.2.x | Windows WASAPI ループバック録音 |
| faster-whisper | 1.x | 高速音声認識（OpenAI Whisper の最適化版） |
| ffmpeg | 8.x | 音声フォーマット変換 |
