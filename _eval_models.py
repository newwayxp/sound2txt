# -*- coding: utf-8 -*-
"""モデル比較用の簡易評価スクリプト。

指定した音声ファイルを複数のモデルサイズで文字起こしし、所要時間・セグメント数・
本文を並べて比較する。どの級別のモデルが実用に足るかの目安に使う。

使い方:
    python _eval_models.py [audio.mp3] [size1 size2 ...]
    # 例: python _eval_models.py C:\\vscode\\data\\audio\\xxx.mp3 small medium
    # 引数なしなら data/audio 内の最新 mp3 を tiny/small/medium で比較
"""
import sys, time, io, os, glob

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from faster_whisper import WhisperModel

DEFAULT_AUDIO_DIR = r"C:\vscode\data\audio"


def _latest_audio() -> str:
    files = glob.glob(os.path.join(DEFAULT_AUDIO_DIR, "*.mp3")) + \
            glob.glob(os.path.join(DEFAULT_AUDIO_DIR, "*.wav"))
    if not files:
        raise SystemExit(f"音声が見つかりません: {DEFAULT_AUDIO_DIR}")
    return max(files, key=os.path.getmtime)


AUDIO = sys.argv[1] if len(sys.argv) > 1 else _latest_audio()
SIZES = sys.argv[2:] if len(sys.argv) > 2 else ["tiny", "small", "medium"]

KW = dict(
    beam_size=5,
    temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 400, "threshold": 0.4},
    condition_on_previous_text=False,
    no_speech_threshold=0.7,
    log_prob_threshold=-1.0,
)

print(f"audio: {AUDIO}")
for size in SIZES:
    print("\n" + "=" * 70)
    print(f"MODEL = {size}")
    t0 = time.time()
    m = WhisperModel(size, device="cpu", compute_type="int8")
    load = time.time() - t0
    t1 = time.time()
    segs, info = m.transcribe(AUDIO, language="ja", **KW)
    segs = list(segs)
    dur = time.time() - t1
    print(f"audio={info.duration:.1f}s  load={load:.1f}s  "
          f"transcribe={dur:.1f}s  segments={len(segs)}")
    txt = " ".join(s.text.strip() for s in segs).strip()
    print("--- text ---")
    print(txt[:1500] if txt else "(EMPTY)")
    del m
