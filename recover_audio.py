import os
import glob
import shutil
import warnings
import configparser
from datetime import datetime

warnings.filterwarnings("ignore")
os.environ["HUGGINGFACE_HUB_VERBOSITY"] = "error"

cfg = configparser.ConfigParser()
cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")

if cfg.has_section("network"):
    proxy = cfg.get("network", "https_proxy", fallback="")
    if proxy:
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", cfg.get("network", "http_proxy", fallback=proxy))
    if not cfg.getboolean("network", "ssl_verify", fallback=True):
        os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFICATION", "1")
        os.environ.setdefault("CURL_CA_BUNDLE", "")
        os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

from faster_whisper import WhisperModel

audio_dir      = cfg.get("paths", "audio_dir")
transcript_dir = cfg.get("paths", "transcript_dir")
model_size     = cfg.get("recording", "model_size", fallback="medium")
done_dir       = os.path.join(audio_dir, "done")
os.makedirs(done_dir, exist_ok=True)

wavs = sorted(glob.glob(os.path.join(audio_dir, "audio_*.wav")))
if not wavs:
    print("未処理の音声ファイルはありません。")
    exit(0)

print(f"未処理ファイル: {len(wavs)} 件")
print(f"モデル読み込み中: {model_size} ...")
model = WhisperModel(model_size, device="cpu", compute_type="int8")

out_file = os.path.join(transcript_dir, f"transcript_recovered_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

with open(out_file, "w", encoding="utf-8-sig") as f:
    f.write(f"=== 文字起こし（復元） {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
    for wav in wavs:
        print(f"変換中: {os.path.basename(wav)}")
        segs, _ = model.transcribe(
            wav, language="ja", vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}
        )
        text = " ".join(s.text.strip() for s in segs)
        if text:
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {text}"
            print(line)
            f.write(line + "\n")
            f.flush()
        shutil.move(wav, os.path.join(done_dir, os.path.basename(wav)))
    f.write(f"\n=== 終了 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

print(f"\n保存しました: {out_file}")
