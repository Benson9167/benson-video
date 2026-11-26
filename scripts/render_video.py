r"""
Universal data-driven renderer for Benson Video (`benson-video`).
Supports:
  - layout="16x9_full" (1920x1080 default): Full-screen bright original footage + sleek left-edge transparent HUD
  - layout="4x3_card"  (1440x1080 option):  Frosted white bulletin cards over gameplay for text-heavy patch notes
  - 4-mode Dynamic Chapter Progress Bar (separate_bottom / separate_top / overlay_bottom / overlay_top)
  - OpenCV subpixel floating-point warpAffine zoom for static images (zero FFmpeg zoompan jitter)
  - Per-shot clean_regions (delogo / blur / delogo_blur)
  - Seamless non-early ASS subtitles + centered kinetic callouts (stamp / pop / slide_up)
  - Douyin -11.5 ~ -12.0 LUFS / -1.0 dBTP two-pass loudnorm + 192kHz oversampled limiter + ducked BGM
  - Automatic 12-frame visual QA inspection sheet + optional delivery copy

Usage:
  python -X utf8 scripts/render_video.py --config <project.json> --dry-run
  python -X utf8 scripts/render_video.py --config <project.json>
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from media_utils import find_font, get_cjk_sans_candidates, get_ffmpeg, get_ffprobe

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_font(size, weight="bold"):
    vf = find_font([r"C:\Windows\Fonts\NotoSansSC-VF.ttf"], env_var="FONT_SANS_VF")
    if vf:
        try:
            f = ImageFont.truetype(vf, size)
            weight_map = {"black": "Black", "bold": "Bold", "medium": "Medium", "regular": "Regular"}
            f.set_variation_by_name(weight_map.get(weight, "Bold"))
            return f
        except Exception:
            pass
    cand = find_font(get_cjk_sans_candidates())
    if cand:
        return ImageFont.truetype(cand, size)
    return ImageFont.load_default()


def run_ff(cmd, desc):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(f"FFMPEG FAILED ({desc}):\n{p.stderr[-1200:]}")
        raise RuntimeError(f"FFmpeg failed on {desc}")


BAR_H = 52


def draw_chapter_bar_base(overlay, W, H, placement="overlay_bottom", bar_h=BAR_H):
    """Draw the dark translucent bar track on the video frame ONLY for overlay_* placements."""
    if placement == "overlay_bottom":
        y0 = H - bar_h
        draw = ImageDraw.Draw(overlay, "RGBA")
        draw.rectangle([0, y0, W, H], fill=(10, 14, 24, 215))
        draw.line([(0, y0), (W, y0)], fill=(255, 255, 255, 65), width=1)
    elif placement == "overlay_top":
        draw = ImageDraw.Draw(overlay, "RGBA")
        draw.rectangle([0, 0, W, bar_h], fill=(10, 14, 24, 215))
        draw.line([(0, bar_h - 1), (W, bar_h - 1)], fill=(255, 255, 255, 65), width=1)


def make_chapter_fill_and_labels(W, chapters, accent_rgb, fill_path, labels_path, bar_h=BAR_H):
    """Create two W x bar_h PNGs for smooth pixel-by-pixel continuous progress bar."""
    r, g, b = accent_rgb

    fill_im = Image.new("RGBA", (W, bar_h), (r, g, b, 118))
    fd = ImageDraw.Draw(fill_im, "RGBA")
    fd.line([(0, 0), (W, 0)], fill=(255, 255, 255, 130), width=1)
    fd.rectangle([0, bar_h - 4, W, bar_h], fill=(min(255, r + 40), min(255, g + 40), min(255, b + 40), 245))
    fd.rectangle([W - 3, 0, W, bar_h], fill=(255, 255, 255, 235))
    fill_im.save(fill_path)

    lbl_im = Image.new("RGBA", (W, bar_h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lbl_im, "RGBA")
    total_d = max(0.001, sum(max(0.5, c["end"] - c["start"]) for c in chapters))
    widths = []
    used = 0
    for i, c in enumerate(chapters):
        if i == len(chapters) - 1:
            w = W - used
        else:
            w = int(round(W * (max(0.5, c["end"] - c["start"]) / total_d)))
            used += w
        widths.append(w)

    font = load_font(27, "bold")
    x = 0
    for i, (c, seg_w) in enumerate(zip(chapters, widths)):
        label = c["label"]
        tb = ld.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        tx = x + max(6, (seg_w - tw) // 2)
        ty = (bar_h - 4 - th) // 2 - 2
        ld.text((tx + 1, ty + 2), label, font=font, fill=(0, 0, 0, 195))
        ld.text((tx, ty), label, font=font, fill=(248, 252, 255, 245))
        if i > 0:
            ld.line([(x, 8), (x, bar_h - 10)], fill=(255, 255, 255, 95), width=1)
        x += seg_w

    lbl_im.save(labels_path)
    return fill_path, labels_path


def make_left_hud_overlay(W, H, hud_cfg, chapter_bar_cfg=None, shot_mid_t=0.0, use_bottom_scrim=False, use_hud_scrim=False):
    """Minimal transparent upper-left HUD overlay + optional overlay chapter bar track."""
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    has_bar = bool(chapter_bar_cfg and chapter_bar_cfg.get("enabled") and chapter_bar_cfg.get("chapters"))
    placement = chapter_bar_cfg.get("placement", "overlay_bottom") if has_bar else "none"
    bar_h = int(chapter_bar_cfg.get("height", BAR_H)) if has_bar else BAR_H
    bar_h += bar_h % 2

    if use_bottom_scrim:
        scrim_h = 215 if (has_bar and placement == "overlay_bottom") else 185
        bot_grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bgd = ImageDraw.Draw(bot_grad)
        for y in range(H - scrim_h, H):
            a = int(168 * ((y - (H - scrim_h)) / scrim_h))
            bgd.line([(0, y), (W, y)], fill=(6, 8, 16, a))
        overlay.alpha_composite(bot_grad)

    if hud_cfg:
        pill = hud_cfg.get("pill", "")
        title = hud_cfg.get("title", "")
        sub = hud_cfg.get("sub", "")
        r, g, b = tuple(hud_cfg.get("accent", [222, 64, 54]))

        f_pill = load_font(28, "bold")
        f_main = load_font(62, "black")
        f_sub = load_font(32, "bold")
        dummy = ImageDraw.Draw(overlay)
        pb = dummy.textbbox((0, 0), pill, font=f_pill) if pill else (0, 0, 0, 0)
        mb = dummy.textbbox((0, 0), title, font=f_main) if title else (0, 0, 0, 0)
        sb = dummy.textbbox((0, 0), sub, font=f_sub) if sub else (0, 0, 0, 0)
        max_tw = max((pb[2] - pb[0]) + 34, mb[2] - mb[0], sb[2] - sb[0])

        pad_left = 48
        top_y = (bar_h + 30) if (has_bar and placement == "overlay_top") else 54
        if use_hud_scrim:
            scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(scrim)
            sd.rounded_rectangle([18, top_y - 22, pad_left + max_tw + 52, top_y + 215], radius=36, fill=(6, 10, 20, 155))
            scrim = scrim.filter(ImageFilter.GaussianBlur(28))
            overlay.alpha_composite(scrim)

        draw = ImageDraw.Draw(overlay, "RGBA")
        draw.rounded_rectangle([pad_left, top_y + 4, pad_left + 8, top_y + 186], radius=4, fill=(r, g, b, 255))
        tx = pad_left + 24

        cur_y = top_y
        if pill:
            pw, ph = (pb[2] - pb[0]) + 28, (pb[3] - pb[1]) + 14
            draw.rounded_rectangle([tx, cur_y, tx + pw, cur_y + ph], radius=9, fill=(r, g, b, 238))
            draw.text((tx + 14, cur_y + 4), pill, font=f_pill, fill=(255, 255, 255, 255))
            cur_y += ph + 12
        if title:
            draw.text((tx + 3, cur_y + 4), title, font=f_main, fill=(0, 0, 0, 225))
            draw.text((tx, cur_y), title, font=f_main, fill=(255, 250, 236, 255), stroke_width=4, stroke_fill=(10, 14, 24, 245))
            cur_y += (mb[3] - mb[1]) + 22
        if sub:
            draw.text((tx + 2, cur_y + 3), sub, font=f_sub, fill=(0, 0, 0, 225))
            draw.text((tx, cur_y), sub, font=f_sub, fill=(255, 224, 130, 255), stroke_width=3, stroke_fill=(10, 14, 24, 245))

    if has_bar and placement in ("overlay_bottom", "overlay_top"):
        draw_chapter_bar_base(overlay, W, H, placement, bar_h)

    return overlay


def fmt_srt(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_ass(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_subtitles(cues, total_dur, W, out_H, has_bar, placement, bar_h, out_srt, out_ass, font_size=78, callouts=None, y_offset=0):
    normalized = []
    for i, c in enumerate(cues):
        st = float(c["start"])
        ed = float(cues[i + 1]["start"]) if i + 1 < len(cues) else total_dur
        normalized.append({"index": i + 1, "start": st, "end": ed, "text": c["text"]})

    with open(out_srt, "w", encoding="utf-8") as f:
        for c in normalized:
            f.write(f"{c['index']}\n{fmt_srt(c['start'])} --> {fmt_srt(c['end'])}\n{c['text']}\n\n")

    if has_bar and placement == "overlay_bottom":
        margin_v = bar_h + 26
    elif has_bar and placement == "separate_bottom":
        margin_v = bar_h + 44
    else:
        margin_v = 46

    header = f"""[Script Info]
Title: Benson Video Fast-Cut
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {out_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H98000000,-1,0,0,0,100,100,1,0,1,5.0,2.4,2,44,44,{margin_v},1
Style: CalloutGold,Microsoft YaHei,78,&H0033E0FF,&H0000FFFF,&H00000000,&H98000000,-1,0,0,0,100,100,1,0,1,5.5,2.8,5,40,40,40,1
Style: CalloutWhite,Microsoft YaHei,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H98000000,-1,0,0,0,100,100,1,0,1,5.5,2.8,5,40,40,40,1
Style: CalloutCyan,Microsoft YaHei,72,&H00FFE87C,&H0000FFFF,&H00000000,&H98000000,-1,0,0,0,100,100,1,0,1,5.5,2.8,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    presets = {
        "center_1":   (int(round(W * 0.50)), 425, 5),
        "center_2":   (int(round(W * 0.50)), 555, 5),
        "top_center": (int(round(W * 0.50)), 96, 8),
        "top_sub":    (int(round(W * 0.50)), 195, 8),
        "top_left":   (int(round(W * 0.27)), 225, 5),
        "top_right":  (int(round(W * 0.73)), 225, 5),
        "mid_upper":  (int(round(W * 0.50)), 250, 5),
        "mid_left":   (int(round(W * 0.27)), 315, 5),
        "mid_right":  (int(round(W * 0.73)), 315, 5),
    }
    style_map = {
        "gold": "CalloutGold",
        "white": "CalloutWhite",
        "cyan": "CalloutCyan",
    }

    with open(out_ass, "w", encoding="utf-8-sig") as f:
        f.write(header)
        for c in normalized:
            f.write(f"Dialogue: 0,{fmt_ass(c['start'])},{fmt_ass(c['end'])},Default,,0,0,0,,{c['text']}\n")
        for co in (callouts or []):
            pos_key = co.get("pos", "top_center")
            px, py, al = presets.get(pos_key, (int(round(W * 0.50)), 120, 8))
            if "x" in co:
                px = int(co["x"])
            if "y" in co:
                py = int(co["y"])
            py += y_offset
            al = int(co.get("align", al))
            fs = int(co.get("fs", 76))
            st_name = style_map.get(co.get("color", "gold"), "CalloutGold")
            anim = co.get("anim", "stamp")
            if anim == "pop":
                tag = f"\\an{al}\\pos({px},{py})\\fs{fs}\\fscx65\\fscy65\\alpha&HFF&\\t(0,120,\\fscx105\\fscy105\\alpha&H00&)\\t(120,180,\\fscx100\\fscy100)\\fad(0,160)"
            elif anim == "slide_up":
                tag = f"\\an{al}\\move({px},{py + 34},{px},{py},0,150)\\fs{fs}\\fad(120,160)"
            else:
                tag = f"\\an{al}\\pos({px},{py})\\fs{fs}\\fscx132\\fscy132\\alpha&HFF&\\t(0,130,\\fscx100\\fscy100\\alpha&H00&)\\fad(0,160)"
            f.write(f"Dialogue: 1,{fmt_ass(float(co['start']))},{fmt_ass(float(co['end']))},{st_name},,0,0,0,,{{{tag}}}{co['text']}\n")
    return normalized


def render_shot(shot, W, H, FPS, hud_png, out_mp4, ffmpeg):
    dur = round(float(shot["end"]) - float(shot["start"]), 3)
    kind = shot.get("kind", "vid")
    src = shot["src"]
    if kind == "img":
        frames = max(1, int(round(dur * FPS)))
        pil_img = Image.open(src).convert("RGB")
        iw, ih = pil_img.size
        base_scale = max(W / iw, H / ih) * 1.10
        nw, nh = int(round(iw * base_scale)), int(round(ih * base_scale))
        pil_img = pil_img.resize((nw, nh), Image.Resampling.LANCZOS)
        src_bgr = np.ascontiguousarray(cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR))
        sh, sw = src_bgr.shape[:2]

        hud_rgba = np.array(Image.open(hud_png).convert("RGBA"))
        hud_bgr = cv2.cvtColor(hud_rgba[:, :, :3], cv2.COLOR_RGB2BGR).astype(np.float32)
        hud_a = (hud_rgba[:, :, 3:4].astype(np.float32)) / 255.0
        inv_a = 1.0 - hud_a

        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-pix_fmt", "yuv420p", "-an", out_mp4,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        cx_src, cy_src = sw / 2.0, sh / 2.0
        cx_dst, cy_dst = W / 2.0, H / 2.0
        z_start = 1.00 / 1.10
        z_end = 1.065 / 1.10
        try:
            for k in range(frames):
                t = k / max(1, frames - 1)
                z = z_start + (z_end - z_start) * t
                M = np.array([
                    [z, 0.0, cx_dst - z * cx_src],
                    [0.0, z, cy_dst - z * cy_src],
                ], dtype=np.float64)
                frame = cv2.warpAffine(
                    src_bgr, M, (W, H),
                    flags=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REFLECT_101,
                )
                for reg in (shot.get("clean_regions") or []):
                    rx = max(2, min(W - 6, int(reg["x"])))
                    ry = max(2, min(H - 6, int(reg["y"])))
                    rw = max(4, min(int(reg["w"]), W - 2 - rx))
                    rh = max(4, min(int(reg["h"]), H - 2 - ry))
                    mode = reg.get("mode", "delogo_blur")
                    if mode in ("delogo", "delogo_blur"):
                        mask = np.zeros((H, W), dtype=np.uint8)
                        mask[ry:ry + rh, rx:rx + rw] = 255
                        frame = cv2.inpaint(frame, mask, 5, cv2.INPAINT_TELEA)
                    if mode in ("blur", "delogo_blur"):
                        k_sz = max(3, int(round(float(reg.get("sigma", 10)) * 2)) | 1)
                        frame[ry:ry + rh, rx:rx + rw] = cv2.GaussianBlur(frame[ry:ry + rh, rx:rx + rw], (k_sz, k_sz), 0)
                comp = (frame.astype(np.float32) * inv_a + hud_bgr * hud_a).clip(0, 255).astype(np.uint8)
                proc.stdin.write(comp.tobytes())
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", errors="replace")
            rc = proc.wait()
            if rc != 0:
                raise RuntimeError(f"FFmpeg rawvideo img zoom failed ({shot['id']}): {err[-800:]}")
        finally:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
    else:
        ss = float(shot.get("ss", 0.0))
        pre_ss = max(0.0, ss - 1.5)
        rel_start = ss - pre_ss
        grade = shot.get("grade", "")
        grade_chain = f",{grade}" if grade else ""
        clean_regions = shot.get("clean_regions") or []
        parts = [
            f"[0:v]trim=start={rel_start:.3f}:duration={dur:.3f},setpts=PTS-STARTPTS,"
            f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}{grade_chain}[bg0]"
        ]
        cur = "bg0"
        for idx, reg in enumerate(clean_regions):
            rx = max(2, min(W - 6, int(reg["x"])))
            ry = max(2, min(H - 6, int(reg["y"])))
            rw = max(4, min(int(reg["w"]), W - 2 - rx))
            rh = max(4, min(int(reg["h"]), H - 2 - ry))
            mode = reg.get("mode", "delogo_blur")
            sigma = float(reg.get("sigma", 10))
            if mode == "delogo":
                parts.append(f"[{cur}]delogo=x={rx}:y={ry}:w={rw}:h={rh}[c{idx}]")
            elif mode == "blur":
                parts.append(
                    f"[{cur}]split=2[bm{idx}][bp{idx}];"
                    f"[bp{idx}]crop={rw}:{rh}:{rx}:{ry},gblur=sigma={sigma}[bb{idx}];"
                    f"[bm{idx}][bb{idx}]overlay={rx}:{ry}[c{idx}]"
                )
            else:
                parts.append(
                    f"[{cur}]delogo=x={rx}:y={ry}:w={rw}:h={rh},split=2[bm{idx}][bp{idx}];"
                    f"[bp{idx}]crop={rw}:{rh}:{rx}:{ry},gblur=sigma={sigma}[bb{idx}];"
                    f"[bm{idx}][bb{idx}]overlay={rx}:{ry}[c{idx}]"
                )
            cur = f"c{idx}"
        parts.append(f"[{cur}][1:v]overlay=0:0:shortest=1,format=yuv420p[v]")
        fc = ";".join(parts)
        run_ff([
            ffmpeg, "-y", "-v", "error",
            "-ss", f"{pre_ss:.3f}", "-i", src,
            "-loop", "1", "-framerate", str(FPS), "-i", hud_png,
            "-filter_complex", fc, "-map", "[v]", "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-pix_fmt", "yuv420p", "-an", out_mp4,
        ], shot["id"])


def mix_audio(voice_wav, bgm_path, bgm_vol, total_dur, work_dir, ffmpeg):
    pre_mix = os.path.join(work_dir, "master_mix_pre.wav")
    final_wav = os.path.join(work_dir, "master_mix_final.wav")
    voice_chain = (
        "aresample=48000,highpass=f=80,"
        "acompressor=threshold=-23dB:ratio=3.8:attack=5:release=85:makeup=6.5dB,"
        "alimiter=limit=0.68:attack=3:release=40:level=false"
    )
    if bgm_path and os.path.exists(bgm_path):
        fade_out = max(0.0, total_dur - 1.6)
        mix_fc = (
            f"[0:a]{voice_chain},asplit=2[v_main][v_sc];"
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{total_dur:.3f},aresample=48000,"
            f"equalizer=f=1400:t=q:w=1.2:g=-3.5,equalizer=f=3000:t=q:w=1.2:g=-3.0,lowpass=f=14500,"
            f"volume={bgm_vol},afade=t=in:st=0:d=0.4,afade=t=out:st={fade_out:.3f}:d=1.6[bgm_bed];"
            f"[bgm_bed][v_sc]sidechaincompress=threshold=0.08:ratio=2.2:attack=15:release=140[bgm_duck];"
            f"[v_main][bgm_duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
        )
        run_ff([
            ffmpeg, "-y", "-v", "error", "-i", voice_wav, "-i", bgm_path,
            "-filter_complex", mix_fc, "-map", "[mix]",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", pre_mix,
        ], "premix")
    else:
        run_ff([
            ffmpeg, "-y", "-v", "error", "-i", voice_wav,
            "-af", voice_chain,
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", pre_mix,
        ], "voice_only_premix")

    def _measure(wav_path):
        p_meas = subprocess.run([
            ffmpeg, "-i", wav_path, "-af", "loudnorm=I=-11.7:TP=-1.5:LRA=7:print_format=json", "-f", "null", "-",
        ], capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p_meas.stderr, re.S)
        return json.loads(m.group(0))

    stats = _measure(pre_mix)
    pred_tp = float(stats["input_tp"]) + (-11.7 - float(stats["input_i"]))
    if pred_tp > -1.7:
        pre_mix_tamed = os.path.join(work_dir, "master_mix_pre_tamed.wav")
        gain_db = min(8.0, pred_tp + 2.4)
        run_ff([
            ffmpeg, "-y", "-v", "error", "-i", pre_mix,
            "-af", f"volume=+{gain_db:.2f}dB,alimiter=limit=0.64:attack=3:release=40:level=false",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", pre_mix_tamed,
        ], "premix_peak_tame")
        pre_mix = pre_mix_tamed
        stats = _measure(pre_mix)

    ln = (
        f"loudnorm=I=-11.7:TP=-1.5:LRA=7:"
        f"measured_I={stats['input_i']}:measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true"
    )
    run_ff([
        ffmpeg, "-y", "-v", "error", "-i", pre_mix,
        "-af", f"{ln},aresample=192000,alimiter=limit=0.79:attack=5:release=50:level=false,aresample=48000",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", final_wav,
    ], "loudnorm_pass2")
    return final_wav


def validate_project(cfg, base_dir):
    def _res(p):
        if not p:
            return None
        if os.path.isabs(p) or os.path.exists(p):
            return os.path.abspath(p)
        return os.path.abspath(os.path.join(base_dir, p))

    errors = []
    if not cfg.get("total_duration"):
        errors.append("缺少 total_duration")
    if not cfg.get("shots"):
        errors.append("shots 为空")
    if not cfg.get("cues"):
        errors.append("cues 为空")

    has_pre = bool(cfg.get("premastered_wav") and os.path.exists(_res(cfg["premastered_wav"])))
    if not has_pre:
        vw = _res(cfg.get("voice_wav"))
        if not vw or not os.path.exists(vw):
            errors.append(f"配音音频不存在：{vw}")

    for s in (cfg.get("shots") or []):
        sp = _res(s.get("src"))
        if not sp or not os.path.exists(sp):
            errors.append(f"分镜素材不存在 ({s.get('id')}): {sp}")

    cb = cfg.get("chapter_bar") or {}
    if cb.get("enabled") and cb.get("chapters"):
        pl = cb.get("placement", "overlay_bottom")
        if pl not in ("overlay_bottom", "overlay_top", "separate_bottom", "separate_top"):
            errors.append(f"不支持的 chapter_bar.placement: {pl}")

    return {"passed": len(errors) == 0, "errors": errors, "resolver": _res}


def main():
    ap = argparse.ArgumentParser(description="Render 16:9 / 4:3 Fast-Cut Video from project.json")
    ap.add_argument("--config", required=True, help="Path to project.json")
    ap.add_argument("--dry-run", action="store_true", help="Validate project.json paths and schema without rendering")
    args = ap.parse_args()

    cfg_abs = os.path.abspath(args.config)
    base_dir = os.path.dirname(cfg_abs)
    with open(cfg_abs, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    chk = validate_project(cfg, base_dir)
    _res = chk["resolver"]
    if args.dry_run:
        print(json.dumps({"passed": chk["passed"], "errors": chk["errors"], "layout": cfg.get("layout", "16x9_full")}, ensure_ascii=False, indent=2))
        sys.exit(0 if chk["passed"] else 2)

    if not chk["passed"]:
        raise RuntimeError("project.json 预检失败：\n  - " + "\n  - ".join(chk["errors"]))

    ffmpeg = get_ffmpeg()
    ffprobe = get_ffprobe()

    layout = cfg.get("layout", "16x9_full")
    W, H = (1440, 1080) if layout == "4x3_card" else (1920, 1080)
    FPS = int(cfg.get("fps", 30))
    total_dur = float(cfg["total_duration"])

    work_dir = _res(cfg["work_dir"])
    out_dir = _res(cfg["out_dir"])
    huds_dir = os.path.join(work_dir, "render_huds")
    clips_dir = os.path.join(work_dir, "render_clips")
    for d in (work_dir, out_dir, huds_dir, clips_dir):
        os.makedirs(d, exist_ok=True)

    chapter_bar = cfg.get("chapter_bar", {"enabled": False, "chapters": []})
    has_bar = bool(chapter_bar.get("enabled") and chapter_bar.get("chapters"))
    placement = chapter_bar.get("placement", "overlay_bottom") if has_bar else "none"
    bar_h = int(chapter_bar.get("height", BAR_H)) if has_bar else BAR_H
    bar_h += bar_h % 2
    out_H = (H + bar_h) if (has_bar and placement.startswith("separate_")) else H
    huds_def = cfg.get("huds", {})
    use_bottom_scrim = bool(cfg.get("use_bottom_scrim", False))
    use_hud_scrim = bool(cfg.get("use_hud_scrim", False))

    clip_list = []
    for shot in cfg["shots"]:
        shot = dict(shot)
        shot["src"] = _res(shot["src"])
        sid = shot["id"]
        mid_t = (float(shot["start"]) + float(shot["end"])) / 2.0
        hud_png = _res(shot.get("custom_overlay")) if shot.get("custom_overlay") else None
        if not hud_png:
            hcfg = huds_def.get(shot.get("hud")) if shot.get("hud") else None
            ovl = make_left_hud_overlay(W, H, hcfg, chapter_bar, mid_t, use_bottom_scrim=use_bottom_scrim, use_hud_scrim=use_hud_scrim)
            hud_png = os.path.join(huds_dir, f"{sid}_hud.png")
            ovl.save(hud_png)
        out_clip = os.path.join(clips_dir, f"{sid}.mp4")
        print(f"SHOT {sid:14s} | {shot['start']:6.3f} -> {shot['end']:6.3f} | {os.path.basename(shot['src'])}")
        render_shot(shot, W, H, FPS, hud_png, out_clip, ffmpeg)
        clip_list.append(out_clip)

    concat_txt = os.path.join(clips_dir, "concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for c in clip_list:
            f.write(f"file '{c.replace(chr(92), '/')}'\n")
    joined_mp4 = os.path.join(work_dir, "joined_nosub.mp4")
    run_ff([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", joined_mp4], "concat")

    srt_out = os.path.join(out_dir, cfg.get("srt_name", "subtitles.srt"))
    ass_out = os.path.join(work_dir, "subtitles.ass")
    font_size = int(cfg.get("subtitle_font_size", 78 if W == 1920 else 72))
    y_offset = bar_h if (has_bar and placement == "separate_top") else 0
    build_subtitles(cfg["cues"], total_dur, W, out_H, has_bar, placement, bar_h, srt_out, ass_out, font_size, callouts=cfg.get("callouts"), y_offset=y_offset)

    if cfg.get("premastered_wav") and os.path.exists(_res(cfg["premastered_wav"])):
        final_wav = _res(cfg["premastered_wav"])
    else:
        bgm = _res(cfg.get("bgm_path")) if cfg.get("bgm_path") else None
        bgm_vol = float(cfg.get("bgm_volume", 0.24))
        final_wav = mix_audio(_res(cfg["voice_wav"]), bgm, bgm_vol, total_dur, work_dir, ffmpeg)

    out_name = cfg.get("output_name", "output_fastcut.mp4")
    final_mp4 = os.path.join(out_dir, out_name)
    ass_esc = ass_out.replace("\\", "/").replace(":", "\\:")

    if has_bar:
        fill_png, labels_png = make_chapter_fill_and_labels(
            W,
            chapter_bar["chapters"],
            tuple(chapter_bar.get("accent", [90, 185, 255])),
            os.path.join(huds_dir, "progress_fill.png"),
            os.path.join(huds_dir, "chapter_labels.png"),
            bar_h=bar_h,
        )
        if placement == "overlay_bottom":
            fc = (
                f"[0:v][2:v]overlay=x='-W+W*min(1,t/{total_dur:.3f})':y={H - bar_h}:shortest=1[vfill];"
                f"[vfill][3:v]overlay=x=0:y={H - bar_h}:shortest=1[vbar];"
                f"[vbar]ass='{ass_esc}',format=yuv420p[v]"
            )
        elif placement == "overlay_top":
            fc = (
                f"[0:v][2:v]overlay=x='-W+W*min(1,t/{total_dur:.3f})':y=0:shortest=1[vfill];"
                f"[vfill][3:v]overlay=x=0:y=0:shortest=1[vbar];"
                f"[vbar]ass='{ass_esc}',format=yuv420p[v]"
            )
        elif placement == "separate_bottom":
            fc = (
                f"[0:v]pad=w={W}:h={out_H}:x=0:y=0:color=#0a0e18[vpad];"
                f"[vpad][2:v]overlay=x='-W+W*min(1,t/{total_dur:.3f})':y={H}:shortest=1[vfill];"
                f"[vfill][3:v]overlay=x=0:y={H}:shortest=1[vbar];"
                f"[vbar]ass='{ass_esc}',format=yuv420p[v]"
            )
        else:
            fc = (
                f"[0:v]pad=w={W}:h={out_H}:x=0:y={bar_h}:color=#0a0e18[vpad];"
                f"[vpad][2:v]overlay=x='-W+W*min(1,t/{total_dur:.3f})':y=0:shortest=1[vfill];"
                f"[vfill][3:v]overlay=x=0:y=0:shortest=1[vbar];"
                f"[vbar]ass='{ass_esc}',format=yuv420p[v]"
            )
        run_ff([
            ffmpeg, "-y", "-v", "error",
            "-i", joined_mp4, "-i", final_wav,
            "-loop", "1", "-framerate", str(FPS), "-i", fill_png,
            "-loop", "1", "-framerate", str(FPS), "-i", labels_png,
            "-filter_complex", fc, "-map", "[v]", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-shortest", final_mp4,
        ], "final_encode_with_bar")
    else:
        run_ff([
            ffmpeg, "-y", "-v", "error",
            "-i", joined_mp4, "-i", final_wav,
            "-vf", f"ass='{ass_esc}'", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-shortest", final_mp4,
        ], "final_encode")

    deliver_dir = cfg.get("deliver_dir")
    if not deliver_dir and cfg.get("deliver_to_videos", False):
        deliver_dir = os.path.join(os.path.expanduser("~"), "Videos")
    if deliver_dir:
        os.makedirs(deliver_dir, exist_ok=True)
        dest = os.path.join(deliver_dir, out_name)
        shutil.copy2(final_mp4, dest)
        print("DELIVERED:", dest)

    pr = subprocess.run([
        ffprobe, "-v", "error",
        "-show_entries", "stream=codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels:format=duration,size",
        "-of", "json", final_mp4,
    ], capture_output=True, text=True, encoding="utf-8")
    print("PROBE:", pr.stdout.strip())

    p_chk = subprocess.run([
        ffmpeg, "-i", final_mp4, "-af", "loudnorm=I=-11.8:TP=-1.0:LRA=7:print_format=json", "-f", "null", "-",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    m2 = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p_chk.stderr, re.S)
    if m2:
        st2 = json.loads(m2.group(0))
        print(f"LOUDNESS: {st2['input_i']} LUFS | TP: {st2['input_tp']} dBTP | LRA: {st2['input_lra']} LU")

    n_samples = 12
    tw = 640 if W == 1920 else 720
    th = int(round(tw * out_H / W))
    cols, rows = (4, 3)
    sheet = Image.new("RGB", (tw * cols, th * rows), (10, 10, 10))
    tmp = os.path.join(work_dir, "_qa_frame.png")
    for idx in range(n_samples):
        t = round(0.8 + idx * ((total_dur - 1.8) / (n_samples - 1)), 2)
        run_ff([ffmpeg, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", final_mp4, "-frames:v", "1", tmp], f"qa_{idx}")
        im = Image.open(tmp).convert("RGB").resize((tw, th), Image.Resampling.LANCZOS)
        sheet.paste(im, ((idx % cols) * tw, (idx // cols) * th))
    if os.path.exists(tmp):
        os.remove(tmp)
    sheet_path = os.path.join(work_dir, "inspection_sheet.jpg")
    sheet.save(sheet_path, quality=92)
    print("QA SHEET:", sheet_path)


if __name__ == "__main__":
    main()
