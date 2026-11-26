"""
Preprocess raw/web gameplay clips into clean silent (-an) 1920x1080 30fps MP4s:
  - Always strips original audio (-an)
  - Optional crop_vf (e.g. zoom-crop top-right watermark)
  - Optional localized Gaussian blur rects (x,y,w,h[,sigma]) over third-party hard subtitles/watermarks
  - Optional Style-2 24px rounded-rectangle feathered pixel mosaic boxes (x1,y1,x2,y2)
  - Optional translucent top-left `测试期画面` badge
  - Optional labeled 4-column QA contact sheet (--qa-sheet)
"""
import argparse
import json
import os
import subprocess
import sys
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from media_utils import find_font, get_cjk_sans_candidates, get_ffmpeg


def _escape_drawtext_path(p):
    return p.replace("\\", "/").replace(":", r"\:")


def build_filter_complex(crop_vf="", blur_rects=None, test_badge=False, font_path=None):
    """Build deterministic 1920x1080 filter_complex for localized blur + optional test-period badge."""
    base_chain = []
    if crop_vf:
        base_chain.append(crop_vf.strip().rstrip(","))
    base_chain.append("scale=1920:1080:flags=lanczos:force_original_aspect_ratio=increase")
    base_chain.append("crop=1920:1080")
    base_chain.append("fps=30,format=yuv420p")

    filters = [f"[0:v]{','.join(base_chain)}[base0]"]
    cur = "base0"

    for idx, r in enumerate(blur_rects or []):
        x, y, w, h = int(r[0]), int(r[1]), int(r[2]), int(r[3])
        sigma = float(r[4]) if len(r) > 4 else 18.0
        x = max(0, min(1916, x))
        y = max(0, min(1076, y))
        w = max(4, min(1920 - x, w))
        h = max(4, min(1080 - y, h))
        main_lbl = f"m{idx}"
        patch_src = f"ps{idx}"
        patch_blur = f"pb{idx}"
        out_lbl = f"v{idx + 1}"
        filters.append(f"[{cur}]split=2[{main_lbl}][{patch_src}]")
        filters.append(f"[{patch_src}]crop={w}:{h}:{x}:{y},gblur=sigma={sigma}:steps=2[{patch_blur}]")
        filters.append(f"[{main_lbl}][{patch_blur}]overlay={x}:{y}:shortest=1[{out_lbl}]")
        cur = out_lbl

    if test_badge:
        fp = font_path or find_font(get_cjk_sans_candidates(), env_var="FONT_SANS_VF")
        badge_lbl = "v_badged"
        if fp:
            esc = _escape_drawtext_path(fp)
            filters.append(
                f"[{cur}]drawbox=x=28:y=22:w=148:h=38:color=black@0.58:t=fill,"
                f"drawtext=fontfile='{esc}':text='测试期画面':fontsize=22:fontcolor=white@0.92:x=46:y=30[{badge_lbl}]"
            )
            cur = badge_lbl

    return ";".join(filters), cur


def _rounded_mask(w, h, boxes, radius=24):
    mask_img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask_img)
    for x1, y1, x2, y2 in boxes:
        draw.rounded_rectangle((int(x1), int(y1), int(x2), int(y2)), radius=radius, fill=255)
    mask_np = np.array(mask_img, dtype=np.uint8)
    mask_np = cv2.GaussianBlur(mask_np, (9, 9), 2.5)
    return (mask_np.astype(np.float32) / 255.0)[..., None]


def _apply_mosaic_bgr(frame, boxes, alpha_mask, block=24):
    h, w = frame.shape[:2]
    mosaic_full = frame.copy()
    for x1, y1, x2, y2 in boxes:
        cx1, cy1 = max(0, int(x1)), max(0, int(y1))
        cx2, cy2 = min(w, int(x2)), min(h, int(y2))
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        roi = frame[cy1:cy2, cx1:cx2]
        rh, rw = roi.shape[:2]
        sw = max(1, rw // block)
        sh = max(1, rh // block)
        small = cv2.resize(roi, (sw, sh), interpolation=cv2.INTER_LINEAR)
        mosaic_full[cy1:cy2, cx1:cx2] = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
    blended = mosaic_full.astype(np.float32) * alpha_mask + frame.astype(np.float32) * (1.0 - alpha_mask)
    return np.clip(blended, 0, 255).astype(np.uint8)


def prepare_clip(
    src,
    out,
    ss=0.0,
    dur=None,
    crop_vf="",
    blur_rects=None,
    mosaic_boxes=None,
    mosaic_block=24,
    test_badge=False,
    overwrite=True,
):
    if not overwrite and os.path.exists(out) and os.path.getsize(out) > 10000:
        return out
    ffmpeg = get_ffmpeg()
    font_path = find_font(get_cjk_sans_candidates(), env_var="FONT_SANS_VF")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    fc, out_lbl = build_filter_complex(
        crop_vf=crop_vf, blur_rects=blur_rects, test_badge=test_badge, font_path=font_path
    )

    if not mosaic_boxes:
        cmd = [ffmpeg, "-y", "-v", "error"]
        if ss and float(ss) > 0:
            cmd += ["-ss", f"{float(ss):.3f}"]
        cmd += ["-i", src]
        if dur and float(dur) > 0:
            cmd += ["-t", f"{float(dur):.3f}"]
        cmd += [
            "-filter_complex", fc,
            "-map", f"[{out_lbl}]",
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out,
        ]
        subprocess.run(cmd, check=True)
        return out

    w, h = 1920, 1080
    frame_bytes = w * h * 3
    alpha_mask = _rounded_mask(w, h, mosaic_boxes, radius=24)

    dec_cmd = [ffmpeg, "-v", "error"]
    if ss and float(ss) > 0:
        dec_cmd += ["-ss", f"{float(ss):.3f}"]
    dec_cmd += ["-i", src]
    if dur and float(dur) > 0:
        dec_cmd += ["-t", f"{float(dur):.3f}"]
    dec_cmd += ["-filter_complex", fc, "-map", f"[{out_lbl}]", "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]

    enc_cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", "30", "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", out,
    ]
    dec = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE)
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE)
    assert dec.stdout is not None and enc.stdin is not None
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
            out_frame = _apply_mosaic_bgr(frame, mosaic_boxes, alpha_mask, block=int(mosaic_block))
            enc.stdin.write(out_frame.tobytes())
    finally:
        enc.stdin.close()
        dec.wait()
        enc.wait()
    if enc.returncode != 0 or dec.returncode != 0:
        raise RuntimeError(f"FFmpeg mosaic pipeline failed for {out}")
    return out


def build_qa_sheet(items, qa_out_path):
    if not items:
        return None
    ffmpeg = get_ffmpeg()
    tw, th = 480, 270
    cols = 4
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (15, 18, 26))
    draw = ImageDraw.Draw(sheet)
    fp = find_font(get_cjk_sans_candidates(), env_var="FONT_SANS_VF")
    try:
        font = ImageFont.truetype(fp, 20) if fp else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    os.makedirs(os.path.dirname(os.path.abspath(qa_out_path)), exist_ok=True)
    tmp = qa_out_path + "._tmp.jpg"
    for idx, it in enumerate(items):
        clip_path = it["out"]
        if not os.path.exists(clip_path):
            continue
        dur = float(it.get("dur") or 2.0)
        mid = max(0.2, dur * 0.45)
        subprocess.run([
            ffmpeg, "-y", "-v", "error",
            "-ss", f"{mid:.3f}", "-i", clip_path, "-frames:v", "1",
            "-vf", f"scale={tw}:{th}", tmp,
        ], check=False)
        if not os.path.exists(tmp):
            continue
        im = Image.open(tmp).convert("RGB")
        r, c = divmod(idx, cols)
        sheet.paste(im, (c * tw, r * th))
        im.close()
        label_id = it.get("id") or os.path.basename(clip_path)
        n_m = len(it.get("mosaic_boxes") or [])
        n_b = len(it.get("blur_rects") or [])
        tag = f"{label_id} [M:{n_m} B:{n_b}]"
        draw.rectangle((c * tw + 4, r * th + 4, c * tw + 340, r * th + 32), fill=(0, 0, 0))
        draw.text((c * tw + 8, r * th + 6), tag, fill=(255, 235, 60) if (n_m or n_b) else (140, 240, 180), font=font)

    if os.path.exists(tmp):
        os.remove(tmp)
    sheet.save(qa_out_path, quality=90)
    print("QA_SHEET:", qa_out_path)
    return qa_out_path


def main():
    ap = argparse.ArgumentParser(description="Sanitize clips: strip audio, crop watermark, localized blur, 24px mosaic")
    ap.add_argument("--spec", help="JSON list of {id?, src, out, ss, dur, crop_vf, blur_rects, mosaic_boxes, mosaic_block, test_badge}")
    ap.add_argument("--only", default="", help="Comma-separated item ids/filenames when using --spec")
    ap.add_argument("--qa-sheet", default="", help="Optional output path for batch QA contact sheet")
    ap.add_argument("--src", help="Single input video path")
    ap.add_argument("--out", help="Single output MP4 path")
    ap.add_argument("--ss", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=0.0)
    ap.add_argument("--crop-vf", default="")
    ap.add_argument("--blur", action="append", default=[], help="x,y,w,h[,sigma] (repeatable)")
    ap.add_argument("--mosaic", action="append", default=[], help="x1,y1,x2,y2 (repeatable, 24px rounded mosaic)")
    ap.add_argument("--mosaic-block", type=int, default=24)
    ap.add_argument("--test-badge", action="store_true")
    args = ap.parse_args()

    if args.spec:
        with open(args.spec, "r", encoding="utf-8-sig") as f:
            items = json.load(f)
        only = {x.strip() for x in args.only.split(",") if x.strip()}
        for it in items:
            key = it.get("id") or os.path.basename(it["out"])
            if only and key not in only:
                continue
            out = prepare_clip(
                src=it["src"],
                out=it["out"],
                ss=it.get("ss", 0.0),
                dur=it.get("dur", 0.0),
                crop_vf=it.get("crop_vf", ""),
                blur_rects=it.get("blur_rects"),
                mosaic_boxes=it.get("mosaic_boxes"),
                mosaic_block=it.get("mosaic_block", args.mosaic_block),
                test_badge=bool(it.get("test_badge", False)),
                overwrite=bool(it.get("overwrite", True)),
            )
            print("OK:", out)
        if args.qa_sheet:
            build_qa_sheet(items, args.qa_sheet)
    elif args.src and args.out:
        blurs = [[float(v) for v in b.split(",")] for b in args.blur]
        mosaics = [[int(float(v)) for v in m.split(",")] for m in args.mosaic]
        out = prepare_clip(
            src=args.src,
            out=args.out,
            ss=args.ss,
            dur=args.dur,
            crop_vf=args.crop_vf,
            blur_rects=blurs,
            mosaic_boxes=mosaics,
            mosaic_block=args.mosaic_block,
            test_badge=args.test_badge,
        )
        print("OK:", out)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
