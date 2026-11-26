"""
Voice generation, short-chunk anti-run-on VoiceStudio synthesis, Doubao seed-audio-1.0,
edge-tts, and human recording final-take pause tightening + optional word-level ASR export.

Usage:
  # 1) Human voice pause tightening (final-take intervals optional via --takes-json):
  python -X utf8 scripts/voice_pipeline.py --mode human_tighten --audio raw.mp3 --out-dir work/voice

  # 2) VoiceStudio short-chunk synthesis (1-2 sentences / 35-75 chars per chunk):
  python -X utf8 scripts/voice_pipeline.py --mode voicestudio --voice <VOICE_ID> --chunks-json chunks.json --out-dir work/voice

  # 3) Clone a new VoiceStudio voice profile from 11-14s clean clip:
  python -X utf8 scripts/voice_pipeline.py --mode clone --name "VoiceName" --audio ref.wav --ref-text "准确参考文本"

  # 4) edge-tts zero-VRAM narration:
  python -X utf8 scripts/voice_pipeline.py --mode edgetts --edge-voice zh-CN-YunjianNeural --chunks-json chunks.json --out-dir work/voice

  # 5) Doubao seed-audio-1.0 zero-shot cloning (reads API key ONLY from env DOUBAO_SPEECH_API_KEY / DOUBAO_API_KEY):
  python -X utf8 scripts/voice_pipeline.py --mode seed_audio --ref-audio ref_12s.mp3 --chunks-json chunks.json --out-dir work/voice
"""
import argparse
import asyncio
import base64
import json
import os
import site
import subprocess
import sys
import time
import urllib.request
import wave
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from media_utils import get_ffmpeg

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VS_CLI = os.environ.get(
    "VOICESTUDIO_CLI",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\VoiceStudio\resources\cli\voicestudio.exe"),
)
SR = 48000


def setup_proxy_env(proxy_url=None):
    if proxy_url:
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")


def read_f32(path, ffmpeg=None):
    ff = ffmpeg or get_ffmpeg()
    raw = subprocess.run(
        [ff, "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def load_stereo_hp(path, ffmpeg=None):
    ff = ffmpeg or get_ffmpeg()
    raw = subprocess.run(
        [ff, "-v", "error", "-i", path, "-af", "highpass=f=75", "-ac", "2", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return arr.reshape(-1, 2)


def save_stereo_wav(arr_2ch, out_wav):
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    if arr_2ch.ndim == 1:
        arr_2ch = np.stack([arr_2ch, arr_2ch], axis=1)
    pk = float(np.max(np.abs(arr_2ch))) if len(arr_2ch) else 0.0
    if pk > 0.92:
        arr_2ch = arr_2ch * (0.90 / pk)
    pcm = (np.clip(arr_2ch, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


def trim_edges(samples, thresh=0.002, pad_head_ms=45, pad_tail_ms=35):
    idx = np.where(np.abs(samples) > thresh)[0]
    if len(idx) == 0:
        return samples
    s = max(0, idx[0] - int(SR * pad_head_ms / 1000))
    e = min(len(samples), idx[-1] + int(SR * pad_tail_ms / 1000))
    out = samples[s:e].copy()
    fade = min(int(SR * 0.008), len(out) // 4)
    if fade > 0:
        out[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out


def _rms_db_10ms(mono):
    win = SR // 100
    n = len(mono) // win
    if n == 0:
        return np.array([-60.0], dtype=np.float32)
    rms = np.sqrt(np.mean(mono[: n * win].reshape(-1, win) ** 2, axis=1) + 1e-12)
    return 20.0 * np.log10(rms)


def _crossfade_join(left, right, xfade_ms=15):
    n = int(SR * xfade_ms / 1000)
    if n <= 2 or len(left) <= n or len(right) <= n:
        return np.concatenate([left, right], axis=0)
    fade_out = np.linspace(1.0, 0.0, n, dtype=np.float32)[:, None]
    fade_in = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    mid = left[-n:] * fade_out + right[:n] * fade_in
    return np.concatenate([left[:-n], mid, right[n:]], axis=0)


def tighten_silences(arr, lead_ms=70, trail_ms=180):
    """
    Uniformly compress overly long breath/thinking pauses while keeping natural
    word decay and consonant attacks:
      - <= 130 ms: keep unchanged
      - 130..340 ms (intra-sentence): compress to ~95 ms (45 ms head + 50 ms tail)
      - > 340 ms (inter-sentence): compress to ~155 ms (65 ms head + 90 ms tail)
    Supports 1D mono or 2D (N, 2) stereo numpy float32 arrays.
    """
    was_1d = arr.ndim == 1
    arr2 = arr[:, None] if was_1d else arr
    mono = arr2.mean(axis=1)
    db = _rms_db_10ms(mono)
    speech_p75 = float(np.percentile(db[db > -45.0], 75)) if np.any(db > -45.0) else -21.0
    thresh_db = max(-40.0, min(-34.0, speech_p75 - 18.0))
    silent = db < thresh_db

    silent_runs = []
    i, n = 0, len(silent)
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            silent_runs.append((i, j))
            i = j
        else:
            i += 1

    if not silent_runs:
        out = arr2
        return (out[:, 0] if was_1d else out), 0, 0.0, thresh_db

    first_speech_frame = silent_runs[0][1] if silent_runs[0][0] == 0 else 0
    last_speech_frame = silent_runs[-1][0] if silent_runs[-1][1] == n else n
    lead_samples = int(lead_ms * SR / 1000)
    trail_samples = int(trail_ms * SR / 1000)

    segments_to_join = []
    cur_sample = max(0, first_speech_frame * 480 - lead_samples)
    end_limit = min(len(arr2), last_speech_frame * 480 + trail_samples)
    if end_limit <= cur_sample:
        return (arr2[:, 0] if was_1d else arr2), 0, 0.0, thresh_db

    compressed_count = 0
    for s_f, e_f in silent_runs:
        if s_f == 0 or e_f == n:
            continue
        gap_ms = (e_f - s_f) * 10
        if gap_ms <= 130:
            continue
        s_smp = s_f * 480
        e_smp = e_f * 480
        if gap_ms <= 340:
            keep_head = int(0.045 * SR)
            keep_tail = int(0.050 * SR)
            xf = 14
        else:
            keep_head = int(0.065 * SR)
            keep_tail = int(0.090 * SR)
            xf = 16
        cut_end = min(e_smp, s_smp + keep_head + int(xf * SR / 2000))
        resume_start = max(s_smp, e_smp - keep_tail - int(xf * SR / 2000))
        if resume_start <= cut_end or cut_end <= cur_sample:
            continue
        segments_to_join.append((arr2[cur_sample:cut_end], xf))
        cur_sample = resume_start
        compressed_count += 1

    segments_to_join.append((arr2[cur_sample:end_limit], 0))

    out = segments_to_join[0][0]
    for idx in range(len(segments_to_join) - 1):
        xf = segments_to_join[idx][1]
        nxt = segments_to_join[idx + 1][0]
        out = _crossfade_join(out, nxt, xfade_ms=xf if xf > 0 else 14)

    out_db = _rms_db_10ms(out.mean(axis=1))
    out_sil = out_db < thresh_db
    max_gap = 0.0
    i, m = 0, len(out_sil)
    while i < m:
        if out_sil[i]:
            j = i
            while j < m and out_sil[j]:
                j += 1
            if i > 0 and j < m:
                max_gap = max(max_gap, (j - i) * 0.01)
            i = j
        else:
            i += 1
    return (out[:, 0] if was_1d else out), compressed_count, max_gap, thresh_db


def run_whisper(wav_path):
    for sp in site.getsitepackages():
        for sub in [r"nvidia\cublas\bin", r"nvidia\cudnn\bin", "ctranslate2"]:
            d = os.path.join(sp, sub)
            if os.path.isdir(d):
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    except Exception:
        model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=8)
    segs, _ = model.transcribe(wav_path, language="zh", word_timestamps=True, vad_filter=False)
    words, lines = [], []
    for seg in segs:
        lines.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip()})
        if seg.words:
            for w in seg.words:
                words.append({"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)})
    return lines, words


def stitch_chunks(chunk_records, out_dir, lead_s=0.16, tail_s=0.45):
    ffmpeg = get_ffmpeg()
    parts = [np.zeros(int(SR * lead_s), dtype=np.float32)]
    cursor = lead_s
    timeline = []
    for i, rec in enumerate(chunk_records):
        arr = trim_edges(read_f32(rec["wav"], ffmpeg=ffmpeg))
        dur = len(arr) / SR
        st = round(cursor, 3)
        ed = round(cursor + dur, 3)
        timeline.append({
            "id": rec["id"],
            "text": rec.get("text", ""),
            "subtitle": rec.get("subtitle", rec.get("text", "")),
            "start": st,
            "end": ed,
            "dur": round(dur, 3),
        })
        parts.append(arr)
        cursor += dur
        if i + 1 < len(chunk_records):
            gap = float(rec.get("pause_after", 0.18))
            parts.append(np.zeros(int(SR * gap), dtype=np.float32))
            cursor += gap
    parts.append(np.zeros(int(SR * tail_s), dtype=np.float32))
    total_dur = round(cursor + tail_s, 3)
    master = os.path.join(out_dir, "voice_dry.wav")
    save_stereo_wav(np.concatenate(parts), master)
    return master, total_dur, timeline


def do_voicestudio(voice_id, chunks, out_dir, only_ids=None, skip_asr=False, proxy_url=None):
    if not voice_id:
        raise ValueError("voicestudio 模式必须通过 --voice 指定 VoiceStudio 音色 ID。")
    setup_proxy_env(proxy_url)
    os.makedirs(out_dir, exist_ok=True)
    records = []
    for ch in chunks:
        cid = ch["id"]
        text = ch["text"]
        if len(text) > 85:
            print(f"WARNING: chunk '{cid}' has {len(text)} chars (>85); consider splitting to 1-2 sentences to avoid run-ons.")
        txt_p = os.path.join(out_dir, f"{cid}.txt")
        wav_p = os.path.join(out_dir, f"{cid}_raw.wav")
        with open(txt_p, "w", encoding="utf-8") as f:
            f.write(text)
        need_gen = (not os.path.exists(wav_p)) or (only_ids is None) or (cid in only_ids)
        if need_gen:
            t0 = time.time()
            cmd = [
                VS_CLI, "--json", "--timeout", "300",
                "speak", "--voice", voice_id, "--language", "Chinese",
                "--text-file", txt_p, "-o", wav_p, "--overwrite",
            ]
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(f"VS {cid} ({len(text)} chars): rc={p.returncode} in {time.time()-t0:.1f}s")
            if p.returncode != 0:
                raise RuntimeError(p.stderr[-500:])
        records.append({**ch, "wav": wav_p})
    master, total_dur, tl = stitch_chunks(records, out_dir)
    lines, words = ([], []) if skip_asr else run_whisper(master)
    meta = {"mode": "voicestudio", "voice_id": voice_id, "total_duration": total_dur, "chunks": tl, "asr_lines": lines, "words": words}
    meta_path = os.path.join(out_dir, "voice_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"DONE: {master} ({total_dur:.3f}s) | META: {meta_path}")
    print("REMINDER: Listen to the generated WAV via read_file before final delivery, and ask user whether any lines need adjustment.")


def do_edgetts(edge_voice, rate, chunks, out_dir):
    import edge_tts
    os.makedirs(out_dir, exist_ok=True)

    async def gen_all():
        records = []
        for ch in chunks:
            cid = ch["id"]
            mp3_p = os.path.join(out_dir, f"{cid}_raw.mp3")
            comm = edge_tts.Communicate(ch["text"], edge_voice, rate=rate)
            await comm.save(mp3_p)
            records.append({**ch, "wav": mp3_p})
        return records

    records = asyncio.run(gen_all())
    master, total_dur, tl = stitch_chunks(records, out_dir)
    meta = {"mode": "edgetts", "voice": edge_voice, "total_duration": total_dur, "chunks": tl}
    meta_path = os.path.join(out_dir, "voice_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"DONE: {master} ({total_dur:.3f}s) | META: {meta_path}")


def do_human_tighten(audio_path, out_dir, takes_json=None, skip_asr=False):
    """
    Clean & tighten a human raw recording:
      - 75Hz highpass -> 48kHz stereo
      - Optional takes_json ([{start, end}, ...] or [[start, end], ...]) to keep ONLY final satisfied takes
      - Adaptive pause compression (~95ms intra-sentence, ~155ms inter-sentence, 70ms lead, 180ms trail)
      - Optional word-level Whisper transcription
    """
    os.makedirs(out_dir, exist_ok=True)
    src_arr = load_stereo_hp(audio_path)

    if takes_json:
        with open(takes_json, "r", encoding="utf-8-sig") as f:
            takes = json.load(f)
        pieces = []
        for item in takes:
            st = float(item["start"] if isinstance(item, dict) else item[0])
            ed = float(item["end"] if isinstance(item, dict) else item[1])
            seg = src_arr[max(0, int(st * SR)):min(len(src_arr), int(ed * SR))]
            if len(seg) > 0:
                pieces.append(seg)
        if not pieces:
            raise ValueError("takes_json 未切出有效音频段。")
        combined = pieces[0]
        for p in pieces[1:]:
            combined = _crossfade_join(combined, p, xfade_ms=15)
    else:
        combined = src_arr

    tight, n_comp, max_gap, thresh_db = tighten_silences(combined, lead_ms=70, trail_ms=180)
    clean_wav = os.path.join(out_dir, "voice_clean.wav")
    save_stereo_wav(tight, clean_wav)
    total_dur = round(len(tight) / SR, 3)

    lines, words = ([], []) if skip_asr else run_whisper(clean_wav)
    meta = {
        "mode": "human_tighten",
        "source": audio_path,
        "total_duration": total_dur,
        "compressed_pauses": n_comp,
        "max_internal_gap_sec": round(max_gap, 3),
        "adaptive_thresh_db": round(thresh_db, 2),
        "asr_lines": lines,
        "words": words,
    }
    meta_path = os.path.join(out_dir, "voice_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"CLEAN HUMAN VOICE: {clean_wav} ({total_dur:.3f}s, compressed={n_comp}, max_gap={max_gap:.2f}s) | META: {meta_path}")
    for ln in lines:
        print(f"  [{ln['start']:.2f} -> {ln['end']:.2f}] {ln['text']}")


def do_seed_audio(ref_audio, chunks, out_dir, only_ids=None, style_hint="自然清晰、节奏利落、充满活力的解说口吻", skip_asr=False):
    api_key = (os.environ.get("DOUBAO_SPEECH_API_KEY") or os.environ.get("DOUBAO_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("缺少环境变量 DOUBAO_SPEECH_API_KEY 或 DOUBAO_API_KEY（严禁写入文件）。")
    ffmpeg = get_ffmpeg()
    os.makedirs(out_dir, exist_ok=True)
    ref_mp3 = os.path.join(out_dir, "_seed_ref.mp3")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", ref_audio, "-t", "12", "-ac", "1", "-ar", "24000", "-b:a", "96k", ref_mp3], check=True)
    with open(ref_mp3, "rb") as f:
        ref_b64 = base64.b64encode(f.read()).decode("ascii")

    records = []
    for ch in chunks:
        cid = ch["id"]
        text = ch["text"]
        mp3_p = os.path.join(out_dir, f"{cid}_seed.mp3")
        need_gen = (not os.path.exists(mp3_p)) or (only_ids is None) or (cid in only_ids)
        if need_gen:
            prompt = f"纯净人声干声，没有任何背景音乐和环境音效。@Audio1 用{style_hint}说道：“{text}”"
            payload = {
                "model": "seed-audio-1.0",
                "text_prompt": prompt,
                "references": [{"audio_data": ref_b64}],
                "audio_config": {"format": "mp3", "sample_rate": 48000, "pitch_rate": 0, "speech_rate": 0, "loudness_rate": 0},
                "watermark": {},
            }
            for attempt in range(3):
                try:
                    t0 = time.time()
                    req = urllib.request.Request(
                        "https://openspeech.bytedance.com/api/v3/tts/create",
                        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=90) as r:
                        obj = json.loads(r.read().decode("utf-8"))
                    with open(mp3_p, "wb") as wf:
                        wf.write(base64.b64decode(obj["audio"]))
                    print(f"SEED {cid} ({len(text)} chars): dur={obj.get('duration')}s in {time.time()-t0:.1f}s")
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(1.5)
        records.append({**ch, "wav": mp3_p})

    master, total_dur, tl = stitch_chunks(records, out_dir)
    lines, words = ([], []) if skip_asr else run_whisper(master)
    meta = {"mode": "seed_audio", "model": "seed-audio-1.0", "total_duration": total_dur, "chunks": tl, "asr_lines": lines, "words": words}
    meta_path = os.path.join(out_dir, "voice_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"DONE SEED-AUDIO: {master} ({total_dur:.3f}s) | META: {meta_path}")


def main():
    ap = argparse.ArgumentParser(description="Benson Video Multi-Engine Voice Pipeline")
    ap.add_argument("--mode", required=True, choices=["voicestudio", "edgetts", "clone", "human_tighten", "human_clean", "seed_audio"])
    ap.add_argument("--voice", default="", help="VoiceStudio profile ID (required for voicestudio mode)")
    ap.add_argument("--edge-voice", default="zh-CN-YunjianNeural")
    ap.add_argument("--rate", default="+10%")
    ap.add_argument("--chunks-json", help="JSON list of {id, text, subtitle?, pause_after?}")
    ap.add_argument("--only-ids", default="", help="Comma-separated chunk IDs to regenerate")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--name", help="New voice name for clone mode")
    ap.add_argument("--audio", help="Input audio for clone or human_tighten mode")
    ap.add_argument("--takes-json", default=None, help="Optional JSON of [[start, end], ...] final-take intervals for human_tighten")
    ap.add_argument("--ref-audio", help="Reference audio clip (10-12s) for seed_audio zero-shot cloning")
    ap.add_argument("--ref-text", help="Reference text for clone mode")
    ap.add_argument("--proxy", default=None, help="Optional HTTP/HTTPS proxy URL for VoiceStudio CLI")
    ap.add_argument("--skip-asr", action="store_true", help="Skip faster-whisper alignment")
    args = ap.parse_args()

    if args.mode == "clone":
        setup_proxy_env(args.proxy)
        p = subprocess.run([
            VS_CLI, "--json", "--timeout", "180", "voices", "clone",
            "--name", args.name, "--language", "Chinese",
            "--audio", args.audio, "--ref-text", args.ref_text,
        ], capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(p.stdout)
        if p.returncode != 0:
            raise RuntimeError(p.stderr[-500:])
    elif args.mode == "voicestudio":
        with open(args.chunks_json, "r", encoding="utf-8-sig") as f:
            chunks = json.load(f)
        only = set(x.strip() for x in args.only_ids.split(",") if x.strip()) or None
        do_voicestudio(args.voice, chunks, args.out_dir, only, skip_asr=args.skip_asr, proxy_url=args.proxy)
    elif args.mode == "seed_audio":
        with open(args.chunks_json, "r", encoding="utf-8-sig") as f:
            chunks = json.load(f)
        only = set(x.strip() for x in args.only_ids.split(",") if x.strip()) or None
        do_seed_audio(args.ref_audio, chunks, args.out_dir, only, skip_asr=args.skip_asr)
    elif args.mode == "edgetts":
        with open(args.chunks_json, "r", encoding="utf-8-sig") as f:
            chunks = json.load(f)
        do_edgetts(args.edge_voice, args.rate, chunks, args.out_dir)
    elif args.mode in ("human_tighten", "human_clean"):
        do_human_tighten(args.audio, args.out_dir, takes_json=args.takes_json, skip_asr=args.skip_asr)


if __name__ == "__main__":
    main()
