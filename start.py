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

    Suppresses all log output during processing.
    Final results are displayed after completion.
    """

    def __init__(self, silent: bool = True):
        self._last_transcript = None
        self._last_corrected = None
        self._silent = silent

    def put_log(self, msg: str) -> None:
        # Silent mode: suppress all logs during processing
        if not self._silent:
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
    args = parser.parse_args()

    try:
        # Setup CUDA if available
        _setup_cuda_dlls()

        # Load config with CLI overrides
        config = _load_config_with_overrides(args.model, args.llm, args.language)

        # Create minimal view (silent mode: no output during processing)
        view = CLIView(silent=True)
        sys.stderr.write("[DEBUG] View created\n")

        # Import and initialize Presenter
        from presenter import Presenter

        presenter = Presenter(config)
        presenter.set_view(view)
        sys.stderr.write("[DEBUG] Presenter initialized\n")

        # Warm up (CUDA detection)
        sys.stderr.write("[DEBUG] Warm-up...\n")
        presenter.warm_up()
        sys.stderr.write("[DEBUG] Warm-up complete\n")

        # For CLI mode, skip presenter.initialize() which requires UI
        sys.stderr.write("[DEBUG] Skipping UI initialization for CLI mode\n")

        # Auto-start recording
        sys.stderr.write("[DEBUG] Starting recording...\n")
        sys.stderr.flush()
        presenter.start()
        sys.stderr.write("[DEBUG] Recording started - waiting for completion\n")
        sys.stderr.flush()

        # Wait for recording to complete (user presses Ctrl+C or session ends naturally)
        # The presenter runs pipeline in background
        try:
            sys.stderr.write("[DEBUG] Entering wait loop...\n")
            sys.stderr.flush()
            wait_count = 0
            while presenter._running:
                wait_count += 1
                if wait_count % 20 == 0:  # Log every 10 seconds
                    sys.stderr.write(f"[DEBUG] Still running... ({wait_count * 0.5}s elapsed)\n")
                    sys.stderr.flush()
                time.sleep(0.5)
            sys.stderr.write(f"[DEBUG] Loop exited after {wait_count * 0.5}s\n")
            sys.stderr.flush()
        except KeyboardInterrupt:
            sys.stderr.write("\n[INFO] Stopping recording and waiting for summarization...\n")
            sys.stderr.flush()
            presenter.stop()

        # Wait for summarization to complete (up to 300 seconds)
        sys.stderr.write("[INFO] Processing... (waiting for summarization)\n")
        sys.stderr.flush()
        for attempt in range(300):
            if not presenter._running:
                sys.stderr.write("[INFO] Processing complete.\n")
                sys.stderr.flush()
                break
            remaining = 300 - attempt
            if attempt % 10 == 0:  # Show progress every 10 seconds
                sys.stderr.write(f"[INFO] Still processing... ({remaining}s remaining)\n")
                sys.stderr.flush()
            time.sleep(1)
        else:
            sys.stderr.write("[WARN] Timeout waiting for summarization\n")
            sys.stderr.flush()

        # Read and output the results
        time.sleep(2)  # ensure files are fully written

        # Get corrected file path from signal file
        corrected_state_file = os.path.join(BASE, ".last_corrected")
        corrected_file = None

        # Read .last_corrected signal file
        if os.path.exists(corrected_state_file):
            try:
                with open(corrected_state_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        corrected_file = content
            except Exception as e:
                sys.stderr.write(f"[DEBUG] Failed to read signal file: {e}\n")
        else:
            sys.stderr.write(f"[DEBUG] Signal file not found: {corrected_state_file}\n")

        # Get summary file (latest summary_*.md in summary_dir)
        summary_dir = config.get("summary", "summary_dir",
                                fallback=os.path.join(os.path.expanduser("~"), "Public", "Sound2Text", "memo"))
        summary_file = None
        if os.path.exists(summary_dir):
            try:
                from pathlib import Path
                summaries = sorted(Path(summary_dir).glob("summary_*.md"), key=os.path.getmtime, reverse=True)
                if summaries:
                    summary_file = str(summaries[0])
            except Exception as e:
                sys.stderr.write(f"[DEBUG] Failed to find summary: {e}\n")
        else:
            sys.stderr.write(f"[DEBUG] Summary dir not found: {summary_dir}\n")

        # Output results (final text only)
        if corrected_file:
            if os.path.exists(corrected_file):
                try:
                    with open(corrected_file, "r", encoding="utf-8") as f:
                        corrected_text = f.read()
                        if corrected_text:
                            print(corrected_text)
                except Exception as e:
                    sys.stderr.write(f"[ERROR] Failed to read corrected file: {e}\n")
            else:
                sys.stderr.write(f"[DEBUG] Corrected file not found: {corrected_file}\n")
        else:
            sys.stderr.write(f"[DEBUG] No corrected file path found\n")

        if summary_file:
            if os.path.exists(summary_file):
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        summary_text = f.read()
                        if summary_text:
                            print(summary_text)
                except Exception as e:
                    sys.stderr.write(f"[ERROR] Failed to read summary file: {e}\n")
            else:
                sys.stderr.write(f"[DEBUG] Summary file not found: {summary_file}\n")
        else:
            sys.stderr.write(f"[DEBUG] No summary file found\n")

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
