# Known Issues / 現在の未解決問題

Last updated: 2026-06-05

---

## 🔴 Active: 文字起こし出力なし（転写ゼロ）

**症状:**
- エラーなし、MP3 保存も成功
- ログに `[PL]` の転写結果が全く出力されない
- セッション開始・停止は正常

**状態ファイルの状況:**
- `.pipeline_session` が正常に作成・削除される
- `.last_transcript` が空 or 作成されない
- 纪要生成がスキップされる（"Transcript file was not created"）

**推定原因（未確認）:**
1. `TurnVAD` の `turn_silence=2.0s` が発動しない
   - 実際の音声に 2 秒以上の無音がない可能性
   - ログの `TurnVAD flushed` が出ているか要確認
2. `_process()` が呼ばれているが出力が空
   - Whisper が全セグメントを `no_speech_prob > 0.7` で除外している可能性
   - `WHISPER_EMPTY` ログが出ているか要確認

**デバッグ手順:**
```powershell
# テスト後にログで確認
Get-Content "C:\vscode\data\sound2txt.log" -Encoding UTF8 |
  Select-String "TurnVAD|WHISPER_IN|WHISPER_OUT|WHISPER_EMPTY|pipeline.*original" |
  Select-Object -Last 50
```

**ログで確認すべきパターン:**
| ログキー | 意味 |
|---|---|
| `TurnVAD flushed [turn]` | VAD が正常にセグメントを送信 |
| `TurnVAD flushed [force]` | 20秒強制送信 |
| `WHISPER_IN` | Whisper に送信された |
| `WHISPER_EMPTY` | Whisper が全除外 → 閾値が厳しすぎる |
| `[pipeline] original:` | 転写成功 |

**設定確認ポイント:**
```ini
[subtitle]
silence_sec = 2.0    ; 発話後2秒の無音でターン終了
min_accum_sec = 1.0  ; 最低1秒の発話が必要
max_sec = 20.0       ; 20秒で強制送信
```

---

## ✅ 解決済み（直近）

| 日付 | 問題 | 解決 |
|---|---|---|
| 2026-06-05 | UnboundLocalError: session_lang | session_lang の定義をモデルロードより前に移動 |
| 2026-06-05 | 纪要が英語で出力 | pipeline._close_session で LANG_FILE を書き込むよう修正 |
| 2026-06-05 | RAW ファイルが WAV に変換されない | _finalize_recorder_raw + session_done シグナル |
| 2026-06-05 | 認識精度が低い (tiny モデル) | kotoba-whisper-v2.0-ct2 (日本語特化) を導入 |
| 2026-06-05 | VAD が force flush のみ (5秒毎) | AccumulatingVAD → TurnVAD に変更 (2秒無音でターン終了) |
| 2026-06-05 | VAD 時間計測が3倍ズレ | len(chunk)/SAMPLE_RATE で実時間計算に変更 |

---

## 環境

| 項目 | 値 |
|---|---|
| OS | Windows 11 |
| Python | 3.14 |
| faster-whisper | 1.2.1 |
| 認識モデル | kotoba-whisper-v2.0-ct2 (int8, 726MB) |
| デバイス | CPU (GPU なし) |
| プロキシ | tkyproxy-std.intra.tis.co.jp:8080 |

---

## 新規 PC セットアップ

```
1. git clone https://github.com/newwayxp/sound2txt.git
2. setup.bat を実行（プロキシ: tkyproxy-std.intra.tis.co.jp:8080）
3. config.ini の api_key を設定
4. kotoba-whisper は setup.bat の Step 5 で自動ダウンロード
```
