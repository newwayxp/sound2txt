"""
文字起こしプロセス: AUDIO_DIR を監視し、新しい WAV ファイルを順番に文字起こしする
処理済みファイルは done/ サブディレクトリに移動して管理する

停止時の処理:
  1. 録音プロセスの書き込み完了を少し待つ
  2. audio_dir に残っている全ファイルを変換してから終了
  3. トランスクリプトパスを .last_transcript に書いて終了
     （会议纪要の生成は start.py が summarizer.py を呼んで行う）
"""
import os
import glob
import time
import configparser
import warnings
from faster_whisper import WhisperModel
from datetime import datetime

warnings.filterwarnings("ignore")

POLL_SEC        = 1.0
EVAL_CHUNKS     = 3
MIN_CONFIDENCE  = 0.5
SUPPORTED_LANGS = {"zh", "ja", "en"}
LANG_ALIAS      = {"yue": "zh", "zh-TW": "zh", "zh-HK": "zh"}

# UI からの停止シグナルファイル（ui.py が作成する）
STOP_SIGNAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stop_signal")
DRAIN_WAIT_SEC  = 3

STATE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_transcript")
LANG_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_language")

HALLUCINATION_PHRASES = [
    "ご視聴ありがとうございました", "チャンネル登録", "字幕",
    "thank you for watching", "please subscribe",
]

VOCAB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vocabulary.txt")

# 言語ごとのトランスクリプトヘッダー / フッター
_TRANSCRIPT_HEADER = {
    "zh": "=== 转写开始 {ts} ===",
    "ja": "=== 文字起こし開始 {ts} ===",
    "en": "=== Transcription started {ts} ===",
}
_TRANSCRIPT_FOOTER = {
    "zh": "=== 结束 {ts} ===",
    "ja": "=== 終了 {ts} ===",
    "en": "=== Ended {ts} ===",
}

def _header(lang: str | None, ts: str) -> str:
    return _TRANSCRIPT_HEADER.get(lang or "en", _TRANSCRIPT_HEADER["en"]).format(ts=ts)

def _footer(lang: str | None, ts: str) -> str:
    return _TRANSCRIPT_FOOTER.get(lang or "en", _TRANSCRIPT_FOOTER["en"]).format(ts=ts)

# 言語ごとの base initial_prompt
# zh は簡体字に誘導し、用語リストを後ろに追加する
_LANG_PROMPT_BASE: dict[str, str] = {
    "zh": "以下是普通话录音的简体中文转写内容，可能包含专有名词：",
    "ja": "以下は日本語の録音です。固有名詞・専門用語が含まれる場合があります：",
    "en": "The following is an audio transcript. It may include proper nouns and technical terms:",
}


def load_vocabulary() -> list[str]:
    """vocabulary.txt から有効な用語を読み込む。"""
    if not os.path.exists(VOCAB_FILE):
        return []
    terms = []
    with open(VOCAB_FILE, encoding="utf-8") as f:
        for line in f:
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
    return terms


def build_initial_prompt(lang: str | None, vocab: list[str]) -> str | None:
    """
    Whisper の initial_prompt を組み立てる。
    - lang が None（自動検出フェーズ）でも vocab があれば渡す
    - 用語リストをプロンプトに埋め込んでデコーダーを誘導する
    """
    base = _LANG_PROMPT_BASE.get(lang or "", "")
    if vocab:
        terms_str = "、".join(vocab) if lang == "ja" else ", ".join(vocab)
        return f"{base}{terms_str}。" if base else terms_str
    return base if base else None


def main():
    _cfg = configparser.ConfigParser()
    _cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")
    audio_dir      = _cfg.get("paths", "audio_dir")
    transcript_dir = _cfg.get("paths", "transcript_dir")
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(transcript_dir, exist_ok=True)
    done_dir = os.path.join(audio_dir, "done")
    os.makedirs(done_dir, exist_ok=True)

    cfg_file   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    output_file = os.path.join(transcript_dir, f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

    # config.ini の language 設定を読む
    # "auto" → 自動検出、"zh"/"ja"/"en" → 固定して即時開始
    cfg_lang = _cfg.get("recording", "language", fallback="auto").strip().lower()
    language  = cfg_lang if cfg_lang in SUPPORTED_LANGS else None

    # デバイス・モデルサイズ設定
    cfg_device     = _cfg.get("recording", "device",     fallback="auto").strip().lower()
    model_size     = _cfg.get("recording", "model_size", fallback="small").strip()

    if cfg_device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    else:
        device = cfg_device

    compute_type = "float16" if device == "cuda" else "int8"

    print(f"[Transcriber] faster-whisper {model_size} モデル読み込み中... (device={device}, compute={compute_type})")
    whisper = WhisperModel(model_size, device=device, compute_type=compute_type)
    print(f"[Transcriber] 準備完了 → 保存先: {output_file}")
    if language:
        print(f"[Transcriber] 言語固定: {language}（config.ini）")
    else:
        print(f"[Transcriber] 言語自動検出（{EVAL_CHUNKS}チャンク待機）")
    print("[Transcriber] 新規音声ファイルを待機中... (Ctrl+C で停止)\n")

    pending     = []
    lang_scores = {}
    seen        = set()

    vocab = load_vocabulary()
    if vocab:
        print(f"[Transcriber] 用語リスト: {len(vocab)} 件読み込み")

    def _transcribe(wav_path, lang):
        prompt = build_initial_prompt(lang, vocab)
        segments, info = whisper.transcribe(
            wav_path, language=lang,
            initial_prompt=prompt,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text = " ".join(s.text.strip() for s in segments)
        if any(p in text for p in HALLUCINATION_PHRASES):
            text = ""
        return text, info

    def _write_and_move(wav_path, text, out):
        if text:
            ts = datetime.now().strftime("%H:%M:%S")
            out.write(f"[{ts}] {text}\n")
            out.flush()
        os.rename(wav_path, os.path.join(done_dir, os.path.basename(wav_path)))
        print(f"[Transcriber] 変換完了: {os.path.basename(wav_path)}")

    def _process_with_autodetect(wav_path):
        text, info = _transcribe(wav_path, lang=None)
        raw_lang   = LANG_ALIAS.get(info.language, info.language)
        prob       = info.language_probability
        valid      = raw_lang in SUPPORTED_LANGS and prob >= MIN_CONFIDENCE
        print(
            f"[Transcriber] チャンク {len(pending)+1}: "
            f"検出={info.language} ({prob:.0%})"
            + (f" → 票: {raw_lang}" if valid else " → 低信頼・スキップ")
        )
        if valid:
            lang_scores[raw_lang] = lang_scores.get(raw_lang, 0) + prob
        pending.append((wav_path, text))

    def _decide_language():
        if lang_scores:
            lang   = max(lang_scores, key=lambda k: lang_scores[k])
            detail = " | ".join(f"{l}:{s:.2f}" for l, s in sorted(lang_scores.items(), key=lambda x: -x[1]))
            print(f"[Transcriber] 言語確定: {lang}  (スコア: {detail})\n")
        else:
            lang = "zh"
            print(f"[Transcriber] 信頼度不足 → デフォルト: {lang}\n")
        return lang

    def _flush_all(out):
        nonlocal language

        print(f"[Transcriber] 録音ファイルの書き込み完了を {DRAIN_WAIT_SEC}秒待機...")
        time.sleep(DRAIN_WAIT_SEC)

        late_files = sorted(
            f for f in glob.glob(os.path.join(audio_dir, "audio_*.wav"))
            if f not in seen
        )
        if late_files:
            print(f"[Transcriber] 未処理ファイル {len(late_files)} 件を追加します。")
        for wav_path in late_files:
            seen.add(wav_path)
            if language is not None:
                text, _ = _transcribe(wav_path, lang=language)
                _write_and_move(wav_path, text, out)
            else:
                _process_with_autodetect(wav_path)

        if pending:
            if language is None:
                language = _decide_language()
            print(f"[Transcriber] pending {len(pending)} チャンクを {language} で変換中...")
            for wav_path, text in pending:
                # zh 確定の場合は必ず簡体字プロンプトで再変換
                if language in _LANG_PROMPT_BASE or not text:
                    text, _ = _transcribe(wav_path, lang=language)
                _write_and_move(wav_path, text, out)
            pending.clear()

    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(output_file, "w", encoding="utf-8-sig") as out:
        # 言語が既知の場合はすぐヘッダーを書く
        # auto（language=None）の場合は言語確定後に書く
        header_written = False
        if language is not None:
            out.write(_header(language, start_ts) + "\n\n")
            out.flush()
            header_written = True

        try:
            while True:
                new_files = sorted(
                    f for f in glob.glob(os.path.join(audio_dir, "audio_*.wav"))
                    if f not in seen
                )
                for wav_path in new_files:
                    seen.add(wav_path)
                    if language is None:
                        _process_with_autodetect(wav_path)
                        if len(pending) >= EVAL_CHUNKS:
                            language = _decide_language()
                            # 言語確定後にヘッダーを書く（開始時刻は録音開始時のもの）
                            if not header_written:
                                out.write(_header(language, start_ts) + "\n\n")
                                out.flush()
                                header_written = True
                            for wp, tx in pending:
                                if language in _LANG_PROMPT_BASE:
                                    tx, _ = _transcribe(wp, lang=language)
                                _write_and_move(wp, tx, out)
                            pending.clear()
                    else:
                        text, _ = _transcribe(wav_path, lang=language)
                        _write_and_move(wav_path, text, out)

                # UI からの停止シグナルを確認
                if os.path.exists(STOP_SIGNAL):
                    os.remove(STOP_SIGNAL)
                    raise KeyboardInterrupt
                time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            print("\n[Transcriber] 停止します...")
            _flush_all(out)
            # ヘッダーがまだ未書き込みなら（検出前に停止）ここで書く
            if not header_written:
                out.seek(0)
                out.write(_header(language, start_ts) + "\n\n")
            out.write("\n" + _footer(language, datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + "\n")

    # トランスクリプトパスと検出言語をステートファイルに書く
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(output_file)
    with open(LANG_FILE, "w", encoding="utf-8") as f:
        f.write(language if language else "")

    # 自動検出だった場合、次回のために config.ini を更新する
    if cfg_lang not in SUPPORTED_LANGS and language:
        try:
            _cfg.set("recording", "language", language)
            with open(cfg_file, "w", encoding="utf-8") as f:
                _cfg.write(f)
            print(f"[Transcriber] config.ini を更新しました: language = {language}")
        except Exception as e:
            print(f"[Transcriber] config.ini 更新失敗（無視）: {e}")

    print(f"[Transcriber] 保存しました: {output_file}")
    print(f"[Transcriber] 検出言語: {language}")


if __name__ == "__main__":
    main()
