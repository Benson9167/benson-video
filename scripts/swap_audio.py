"""
5-Second Lossless Video Audio Swapper (-c:v copy).

When only swapping BGM, tweaking `--bgm-volume`, or replacing the voice track,
remuxes the video stream losslessly (`-c:v copy`) while running the full
-11.5 ~ -12.0 LUFS / -1.0 dBTP two-pass loudnorm + sidechain-ducked mastering.
Zero video frames are re-encoded.
"""
import argparse
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from media_utils import get_ffmpeg, get_ffprobe
from render_video import mix_audio, run_ff

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def probe_duration(video_path, ffprobe):
    p = subprocess.run([
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError(f"无法读取视频时长 ({video_path}): {p.stderr}")
    return float(p.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description="Losslessly swap/remaster BGM or voice track via FFmpeg -c:v copy")
    ap.add_argument("--video", required=True, help="Existing rendered MP4 (video stream copied untouched)")
    ap.add_argument("--out", required=True, help="Output MP4 path")
    ap.add_argument("--voice", default=None, help="Dry voice WAV path (required unless --premastered-wav is set)")
    ap.add_argument("--bgm", default=None, help="Optional BGM audio path (omit to export voice-only master)")
    ap.add_argument("--bgm-volume", type=float, default=0.25, help="BGM gain before sidechain ducking (default 0.25)")
    ap.add_argument("--premastered-wav", default=None, help="Skip mix_audio and mux this already-mastered WAV")
    ap.add_argument("--work-dir", default=None, help="Temp directory for premix WAVs (defaults next to --out)")
    ap.add_argument("--dry-run", action="store_true", help="Validate inputs without running FFmpeg")
    args = ap.parse_args()

    video_abs = os.path.abspath(args.video)
    out_abs = os.path.abspath(args.out)
    voice_abs = os.path.abspath(args.voice) if args.voice else None
    bgm_abs = os.path.abspath(args.bgm) if args.bgm else None
    pre_abs = os.path.abspath(args.premastered_wav) if args.premastered_wav else None

    errors = []
    if not os.path.exists(video_abs):
        errors.append(f"视频文件不存在：{video_abs}")
    if pre_abs:
        if not os.path.exists(pre_abs):
            errors.append(f"母带音频不存在：{pre_abs}")
    else:
        if not voice_abs or not os.path.exists(voice_abs):
            errors.append(f"干声音频不存在：{voice_abs}")
        if bgm_abs and not os.path.exists(bgm_abs):
            errors.append(f"BGM 文件不存在：{bgm_abs}")

    if args.dry_run:
        print(json.dumps({
            "passed": len(errors) == 0,
            "errors": errors,
            "mode": "premastered" if pre_abs else ("voice+bgm" if bgm_abs else "voice_only"),
            "bgm_volume": args.bgm_volume,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if not errors else 2)

    if errors:
        raise RuntimeError("参数预检失败：\n  - " + "\n  - ".join(errors))

    ffmpeg = get_ffmpeg()
    ffprobe = get_ffprobe()
    dur = probe_duration(video_abs, ffprobe)
    out_dir = os.path.dirname(out_abs) or "."
    work_dir = os.path.abspath(args.work_dir) if args.work_dir else os.path.join(out_dir, "_swap_audio_tmp")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    if pre_abs:
        final_wav = pre_abs
    else:
        final_wav = mix_audio(voice_abs, bgm_abs, args.bgm_volume, dur, work_dir, ffmpeg)

    run_ff([
        ffmpeg, "-y", "-v", "error",
        "-i", video_abs, "-i", final_wav,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-shortest", out_abs,
    ], "lossless_audio_remux")
    print("REMUXED:", out_abs)

    p_chk = subprocess.run([
        ffmpeg, "-i", out_abs, "-af", "loudnorm=I=-11.8:TP=-1.0:LRA=7:print_format=json", "-f", "null", "-",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p_chk.stderr, re.S)
    if m:
        st = json.loads(m.group(0))
        print(f"LOUDNESS: {st['input_i']} LUFS | TP: {st['input_tp']} dBTP | LRA: {st['input_lra']} LU")


if __name__ == "__main__":
    main()
