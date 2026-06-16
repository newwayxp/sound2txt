#!/usr/bin/env python3
"""
分析音频处理的时间线：检查transcript、corrected、memo文件的时间戳。
用于诊断"UI显示完成但实际还在处理"的问题。
"""
import os
import sys
from datetime import datetime
from pathlib import Path

def analyze_timeline(transcript_dir="", corrected_dir="", summary_dir=""):
    """Analyze timestamps in output files to understand processing timeline."""

    # Default paths
    if not transcript_dir:
        transcript_dir = r"C:\Users\Public\Sound2Text\transcript"
    if not corrected_dir:
        corrected_dir = r"C:\Users\Public\Sound2Text\corrected"
    if not summary_dir:
        summary_dir = r"C:\Users\Public\Sound2Text\memo"

    print("=" * 70)
    print("Sound2Text 处理时间线分析")
    print("=" * 70)

    # Find the most recent files
    files_to_check = []

    # Transcript file
    if os.path.isdir(transcript_dir):
        transcripts = sorted(Path(transcript_dir).glob("transcript_*.txt"), key=os.path.getmtime)
        if transcripts:
            latest_tr = transcripts[-1]
            files_to_check.append(("Transcript (RAW)", str(latest_tr)))

    # Corrected file
    if os.path.isdir(corrected_dir):
        corrected = sorted(Path(corrected_dir).glob("corrected_*.txt"), key=os.path.getmtime)
        if corrected:
            latest_corr = corrected[-1]
            files_to_check.append(("Corrected", str(latest_corr)))

    # Summary file
    if os.path.isdir(summary_dir):
        summaries = sorted(Path(summary_dir).glob("summary_*.md"), key=os.path.getmtime)
        if summaries:
            latest_sum = summaries[-1]
            files_to_check.append(("Summary", str(latest_sum)))

    if not files_to_check:
        print("No output files found. Check your paths in config.ini")
        return

    print("\n文件修改时间（从最后修改的时间反推处理顺序）：\n")

    times = []
    for label, filepath in files_to_check:
        mtime = os.path.getmtime(filepath)
        dt = datetime.fromtimestamp(mtime)
        size = os.path.getsize(filepath)

        # Read first and last timestamp in the file
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                lines = f.readlines()
                first_line = lines[0] if lines else ""
                last_line = lines[-1] if lines else ""
                content_lines = len(lines)
        except Exception as e:
            first_line = f"Error: {e}"
            last_line = ""
            content_lines = 0

        times.append((dt, label, filepath, size, content_lines, first_line, last_line))

        print(f"{label:15} | 修改时间: {dt.strftime('%H:%M:%S')} | "
              f"大小: {size:8d} bytes | {content_lines:5d} lines")

    # Calculate intervals
    if len(times) > 1:
        print("\n处理阶段耗时：\n")
        times_sorted = sorted(times, key=lambda x: x[0])

        for i, (t1, label1, _, _, _, _, _) in enumerate(times_sorted):
            print(f"{label1:15} 完成于 {t1.strftime('%H:%M:%S')}")
            if i > 0:
                t0, label0, _, _, _, _, _ = times_sorted[i-1]
                delta = (t1 - t0).total_seconds()
                print(f"                ↑ {label0} → {label1} 耗时: {delta:.0f}s ({delta/60:.1f}min)")

    # Show file content samples
    print("\n" + "=" * 70)
    print("文件内容样本（查看时间戳）：")
    print("=" * 70)

    for label, filepath in files_to_check:
        print(f"\n{label} ({os.path.basename(filepath)}):")
        print("-" * 70)
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                lines = f.readlines()
                # Show first 3 lines
                print("开头：")
                for line in lines[:3]:
                    print(f"  {line.rstrip()}")
                # Show last 3 lines
                if len(lines) > 6:
                    print("  ...")
                    print("结尾：")
                    for line in lines[-3:]:
                        print(f"  {line.rstrip()}")
        except Exception as e:
            print(f"  Error reading file: {e}")

    # Check log file for timestamps
    log_file = os.path.join(transcript_dir, "..", "sound2txt.log")
    if os.path.exists(log_file):
        print("\n" + "=" * 70)
        print("日志文件（最后20行）：")
        print("=" * 70)
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines[-20:]:
                    print(line.rstrip())
        except Exception as e:
            print(f"Error reading log: {e}")

if __name__ == "__main__":
    # Try to read paths from config.ini
    import configparser
    try:
        cfg = configparser.ConfigParser()
        cfg.read("config.ini", encoding="utf-8")

        tr_dir = cfg.get("paths", "transcript_dir",
                        fallback=r"C:\Users\Public\Sound2Text\transcript")
        corr_dir = cfg.get("summary", "corrected_dir",
                          fallback=r"C:\Users\Public\Sound2Text\corrected")
        sum_dir = cfg.get("summary", "summary_dir",
                         fallback=r"C:\Users\Public\Sound2Text\memo")

        analyze_timeline(tr_dir, corr_dir, sum_dir)
    except Exception as e:
        print(f"Error: {e}")
        analyze_timeline()
