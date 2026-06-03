"""
ランチャー: recorder / transcriber / summarizer を順に制御する

起動フロー:
  1. recorder.py   : 録音（バックグラウンド）
  2. transcriber.py: 文字起こし（バックグラウンド）
  3. Ctrl+C        : recorder を即時停止
                     transcriber は自分で残ファイルを処理して終了するまで待つ
  4. summarizer.py : .last_transcript を読んで会议纪要を生成
"""
import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, ".last_transcript")


def launch(script: str, *args) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, os.path.join(BASE, script), *args])


def _is_mic_enabled() -> bool:
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(BASE, "config.ini"), encoding="utf-8")
    return cfg.getboolean("recording", "enable_mic", fallback=True)


def main():
    mic_enabled = _is_mic_enabled()

    print("=== 起動中 ===")
    print("  [1] recorder.py     - loopback recording")
    if mic_enabled:
        print("  [2] mic_recorder.py - microphone recording")
    print("  [3] transcriber.py  - transcription")
    print("Ctrl+C to stop\n")

    rec_proc   = launch("recorder.py")
    mic_proc   = launch("mic_recorder.py") if mic_enabled else None
    trans_proc = launch("transcriber.py")

    try:
        rec_proc.wait()
        if mic_proc:
            mic_proc.wait()
        trans_proc.wait()
    except KeyboardInterrupt:
        print("\n=== 停止中 ===")

        # recorder と mic_recorder は即時停止
        rec_proc.terminate()
        try:
            rec_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            rec_proc.kill()

        if mic_proc:
            mic_proc.terminate()
            try:
                mic_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mic_proc.kill()

        # transcriber は残ファイルを処理して自然終了するまで待つ
        # （Ctrl+C は既に transcriber 自身にも届いている）
        print("[start.py] transcriber の後処理を待機中... (もう一度 Ctrl+C で強制終了)")
        try:
            trans_proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            trans_proc.kill()
        except KeyboardInterrupt:
            print("[start.py] 強制終了します。")
            trans_proc.kill()
            trans_proc.wait()

    # transcriber が書いたトランスクリプトパスを読んで summarizer を起動
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            transcript_path = f.read().strip()

        if transcript_path and os.path.exists(transcript_path):
            print(f"\n[start.py] 会议纪要を生成します: {transcript_path}")
            sum_proc = launch("summarizer.py", transcript_path)
            try:
                sum_proc.wait(timeout=600)
            except subprocess.TimeoutExpired:
                sum_proc.kill()
            except KeyboardInterrupt:
                sum_proc.kill()
        else:
            print(f"[start.py] トランスクリプトが見つかりません: {transcript_path}")
    else:
        print("[start.py] .last_transcript が見つからないため会议纪要をスキップします。")

    print("=== 終了 ===")


if __name__ == "__main__":
    main()
