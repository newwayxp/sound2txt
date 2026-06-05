# Known Issues / 現在の未解決問題

Last updated: 2026-06-05

---

## 🔴 Active: pipeline.py 転写ゼロ（アプリ起動時）

**症状:**
- debug_modules.py audio → ✅ 正常に日本語転写される（GPU large-v3 動作確認済み）
- アプリからの録音 → 転写コンテンツが全くない

**確認済み動作:**
- GPU (3060 Ti) + CUDA 12: `from faster_whisper import WhisperModel` → OK
- `ctranslate2.get_cuda_device_count()` → 1
- `debug_modules.py audio 20` → 3セグメント転写成功
- WAV/MP3 保存 → 正常（音声キャプチャ自体は OK）

**推定原因（未確認）:**
1. VAD が 30 秒の force-flush に達していない
   - noise-skip で accum_dur がリセットされていた → 修正済み
   - 修正後の動作未確認
2. pipeline.py バックグラウンドスレッドがセグメントを受け取っていない
   - `[TR] VAD force accum=30.xs` がログに出るか要確認
3. pipeline.py がセッションモードで起動しているか
   - `.pipeline_session` ファイルが存在するか確認

**次回デバッグ手順:**
```powershell
# アプリで録音後、ログで確認
Get-Content "C:\code\data\sound2txt.log" -Encoding UTF8 -Tail 80 |
  Select-String "VAD|WHISPER|pipeline.*original|session"
```

**ログで確認すべきパターン:**
| ログキー | 意味 |
|---|---|
| `VAD force accum=30.xs` | force-flush 発動 → キューに入った |
| `VAD turn-end` | 発話終了検出 |
| `WHISPER_IN` | バックグラウンドスレッドが受け取った |
| `WHISPER_EMPTY` | Whisper が全フィルタリング |
| `[pipeline] original:` | 転写成功 |

---

## ✅ 解決済み（直近）

| 日付 | 問題 | 解決 |
|---|---|---|
| 2026-06-05 | CUDA DLL (cublas64_12.dll) import 失敗 | ctranslate2 `__init__.py` に try/except パッチ |
| 2026-06-05 | GPU 推論時 cublas not found | nvidia pip パッケージを ctranslate2 import 前にプリロード |
| 2026-06-05 | kotoba-whisper shape mismatch (80 vs 128 mel) | preprocessor_config.json を作成 (feature_size=128) |
| 2026-06-05 | VAD noise-skip で accumulator リセット | noise-skip 時は tracking のみリセット、accum_dur は保持 |
| 2026-06-05 | pipeline.py 転写ゼロ（旧 VAD バグ） | ↑ と同上 |
| 2026-06-05 | 転写パラメータがモデル非依存でハードコード | `_make_transcribe_kwargs(model_path)` で封装 |
| 2026-06-05 | 転写が同期でメインループをブロック | バックグラウンドスレッド + queue に変更 |
| 2026-06-05 | リアルタイム翻訳で品質低下 | 翻訳機能を削除、転写に集中 |
| 2026-06-05 | setup.bat 各種バグ | Unicode コメント、ネスト if、PyTorch 未インストール等 |

---

## アーキテクチャ（pipeline.py 現在）

```
メインスレッド:
  音声キャプチャ → AccumulatingVAD
    silence_sec=2.0s で発話終了検出
    max_sec=30.0s で強制フラッシュ
    noise-skip 時は accum_dur 保持（要確認）
  → queue.put(audio_bytes) ← ノンブロッキング

バックグラウンドスレッド (_transcribe_loop):
  queue.get() → 一時 WAV 書込 → WhisperModel.transcribe()
  → フィルタリング → transcript ファイル追記
  セッション終了時: queue.join() で完了待ち

モデル設定 (config.ini):
  model_size = large-v3 (GPU では large-v3 使用)
  [models] ja = large-v3
```

---

## 環境

| 項目 | 値 |
|---|---|
| OS | Windows 11 |
| Python | 3.11 |
| GPU | NVIDIA RTX 3060 Ti |
| faster-whisper | 1.x |
| ctranslate2 | 4.7.2 (patched __init__.py) |
| 認識モデル | large-v3 on CUDA |
| プロキシ | tkyproxy-std.intra.tis.co.jp:8080 |

---

## 新規 PC セットアップ

```
1. git clone https://github.com/newwayxp/sound2txt.git
2. setup.bat を実行（プロキシ設定、torch/kotoba-whisper ダウンロード含む）
3. config.ini の api_key を設定
4. GPU あり: setup が nvidia-cuda-runtime-cu12, nvidia-cublas-cu12 を自動インストール
```
