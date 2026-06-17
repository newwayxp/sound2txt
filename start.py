#!/usr/bin/env python3
"""
start.py – CLI mode launcher for Sound2Text

Usage:
  python start.py [--model MODEL] [--llm LLM_MODEL] [--language LANG]

Reads transcript from corrected tab and outputs to console.
No recording capability in CLI mode.
"""
import sys
import os
import time
import argparse
import threading
from typing import TYPE_CHECKING

from appconfig import AppConfig, BASE, CFG_FILE, cuda_status, _setup_cuda_dlls

if TYPE_CHECKING:
    from presenter import Presenter


class CLIView:
    """Minimal ViewProtocol implementation for CLI mode.

    Displays dynamic recording status while suppressing detailed logs.
    """

    def __init__(self, show_status: bool = True):
        self._last_transcript = None
        self._last_corrected = None
        self._show_status = show_status
        self._last_status = ""

    def put_log(self, msg: str) -> None:
        # Show only key status updates (REC, TR, SUM)
        if not self._show_status:
            return

        # Extract status keywords
        if any(x in msg for x in ["[REC]", "[TR]", "[SUM]", "[UI]"]):
            # Show brief status without full details
            if "started" in msg or "complete" in msg or "done" in msg or "ERROR" in msg:
                print(msg)

    def schedule(self, fn) -> None:
        fn()

    def set_start_enabled(self, v: bool) -> None:
        pass

    def set_stop_enabled(self, v: bool) -> None:
        pass

    def show_onair(self) -> None:
        pass

    def hide_onair(self) -> None:
        pass

    def set_onair_level(self, level: float) -> None:
        pass

    def dashboard_start(self) -> None:
        pass

    def dashboard_stop(self) -> None:
        pass

    def dashboard_reset(self) -> None:
        pass

    def dashboard_add_audio(self, secs: float) -> None:
        pass

    def dashboard_add_trans(self, secs: float) -> None:
        pass

    def set_window_title(self, title: str) -> None:
        pass

    def get_window_title(self) -> str:
        return "Sound2Text CLI"

    def destroy(self) -> None:
        pass

    def lock_to_cpu(self) -> None:
        pass

    def unlock_gpu_buttons(self) -> None:
        pass

    def set_cuda_btn_text(self, text: str) -> None:
        pass

    def set_cuda_btn_state(self, enabled: bool) -> None:
        pass

    def set_rec_status(self, text_key: str, color: str) -> None:
        pass

    def set_tr_status(self, text_key: str, color: str) -> None:
        pass

    def set_sum_status(self, text_key: str, color: str) -> None:
        pass

    def show_ptt_button(self) -> None:
        pass

    def hide_ptt_button(self) -> None:
        pass

    def clear_results(self) -> None:
        pass


def _load_config_with_overrides(model: str | None, llm: str | None, language: str | None) -> AppConfig:
    """Load AppConfig and apply CLI parameter overrides."""
    cfg = AppConfig()

    if model:
        cfg.set("recording", "model_size", model)
    if llm:
        cfg.set("llm", "model", llm)
    if language:
        cfg.set("recording", "language", language)

    return cfg


def main():
    # Load current config for help display
    help_epilog = ""
    try:
        import configparser
        cfg_parser = configparser.ConfigParser()
        cfg_parser.read(CFG_FILE, encoding="utf-8")
        current_model = cfg_parser.get("recording", "model_size", fallback="auto")
        current_llm = cfg_parser.get("llm", "model", fallback="(not configured)")
        current_lang = cfg_parser.get("recording", "language", fallback="auto")

        help_epilog = f"""
Current Configuration (from config.ini):
  Whisper model (--model):  {current_model}
  LLM model (--llm):        {current_llm}
  Language (--language):    {current_lang}

Available Options:
  Whisper models:  tiny, base, small, medium, large (or custom path)
  Languages:       auto, ja (Japanese), en (English), zh (Chinese), fr, de, es, etc.
  LLM models:      claude-3-5-sonnet-20241022, claude-opus-4-1, gpt-4, etc.

Examples:
  python start.py
  python start.py --model small
  python start.py --language ja --llm claude-3-5-sonnet-20241022
  python start.py --model base --language en --llm claude-3-5-sonnet-20241022
"""
    except Exception as e:
        help_epilog = f"\nNote: Could not read config.ini ({e})\n"

    parser = argparse.ArgumentParser(
        description="CLI mode for Sound2Text - transcribe and correct without GUI",
        prog="sound2text-cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=help_epilog
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        metavar="MODEL",
        help="Whisper model size (tiny, base, small, medium, large) or custom path"
    )
    parser.add_argument(
        "--llm",
        type=str,
        default=None,
        metavar="LLM",
        help="LLM model name for correction (e.g., claude-3-5-sonnet-20241022)"
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        metavar="LANG",
        help="Language code (auto, ja, en, zh, etc.)"
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        metavar="N",
        help="Auto-stop after N seconds, then wait for processing to finish "
             "(non-interactive testing; without this, stop with Ctrl+C)"
    )
    args = parser.parse_args()

    try:
        # Setup CUDA if available
        _setup_cuda_dlls()

        # Load config with CLI overrides
        config = _load_config_with_overrides(args.model, args.llm, args.language)

        # Create minimal view (show status: display key updates only)
        view = CLIView(show_status=True)

        # Import and initialize Presenter
        from presenter import Presenter

        presenter = Presenter(config)
        presenter.set_view(view)

        # Warm up (CUDA detection)
        presenter.warm_up()

        # For CLI mode, skip presenter.initialize() which requires UI
        # Auto-start recording immediately
        presenter.start()

        # Wait for recording to complete (user presses Ctrl+C, fixed --seconds
        # duration elapses, or session ends naturally).
        # The presenter runs pipeline in background.
        try:
            if args.seconds:
                _t0 = time.time()
                while presenter._running and (time.time() - _t0) < args.seconds:
                    time.sleep(0.5)
                _stop_clock = time.strftime("%H:%M:%S")
                print(f"\n[*] Auto-stop after {args.seconds:.0f}s "
                      f"(stop wall-clock={_stop_clock}); waiting for processing...")
                presenter.stop()
            else:
                while presenter._running:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            print(f"\n[*] Stopping recording (stop wall-clock={time.strftime('%H:%M:%S')}) "
                  f"and waiting for summarization...")
            presenter.stop()

        # Wait for summarization to complete (up to 300 seconds)
        for attempt in range(300):
            if not presenter._running:
                break
            time.sleep(1)
        else:
            print("[!] Timeout waiting for summarization")

        # Processing complete
        # Files are automatically saved to:
        # - corrected_*.txt in config.ini [paths] corrected_dir
        # - summary_*.md in config.ini [summary] summary_dir
        presenter._stop_pipeline(timeout=1800)
        sys.stderr.write("[INFO] Session complete. Results saved to disk.\n")

        sys.exit(0)

    except KeyboardInterrupt:
        print("\n[CLI] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
