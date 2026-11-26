"""
High-Craft Style-C Multi-Ratio Cover Generator (HTML/CSS + Local Playwright Chrome).

Generates zero-crop covers for Douyin & Bilibili + combined inspection sheet:
  - cover_16x9.jpg / .png (1920x1080, Bilibili 个人空间封面)
  - cover_4x3.jpg  / .png (1440x1080, Douyin 横封面4:3 + Bilibili 首页推荐封面4:3)
  - cover_3x4.jpg  / .png (1080x1440, Douyin 竖封面3:4)
  - covers_preview.jpg    (side-by-side visual inspection sheet)

Supports optional `--mosaic x1,y1,x2,y2` (24px rounded pixel mosaic) on the hero artwork!
"""
import argparse
import base64
import os
import sys
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from media_utils import find_font, get_cjk_sans_candidates, get_cjk_title_candidates


def _apply_hero_mosaic(hero_img, mosaic_boxes, out_path, block=24):
    im = cv2.imread(hero_img)
    if im is None:
        raise FileNotFoundError(f"无法读取封面原图：{hero_img}")
    h, w = im.shape[:2]
    mask_img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask_img)
    for x1, y1, x2, y2 in mosaic_boxes:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=24, fill=255)
    mask_np = cv2.GaussianBlur(np.array(mask_img, dtype=np.uint8), (9, 9), 2.5)
    alpha = (mask_np.astype(np.float32) / 255.0)[..., None]
    mosaic_full = im.copy()
    for x1, y1, x2, y2 in mosaic_boxes:
        cx1, cy1 = max(0, int(x1)), max(0, int(y1))
        cx2, cy2 = min(w, int(x2)), min(h, int(y2))
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        roi = im[cy1:cy2, cx1:cx2]
        rh, rw = roi.shape[:2]
        small = cv2.resize(roi, (max(1, rw // block), max(1, rh // block)), interpolation=cv2.INTER_LINEAR)
        mosaic_full[cy1:cy2, cx1:cx2] = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
    blended = np.clip(mosaic_full.astype(np.float32) * alpha + im.astype(np.float32) * (1.0 - alpha), 0, 255).astype(np.uint8)
    cv2.imwrite(out_path, blended)
    return out_path


def file_to_data_uri(path, mime=None):
    if mime is None:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_style_c_html(
    w,
    h,
    hero_uri,
    title_font_uri,
    sans_font_uri,
    spark_tag,
    game_tag,
    main_title,
    highlight_pill,
    sub_text,
    layout_kind,
    hero_pos_override=None,
):
    if layout_kind == "16x9":
        hero_pos = hero_pos_override or "center 16%"
        bar_h, bar_bottom, skew = "436px", "-28px", "-2.6deg"
        spark_fs, spark_pad, spark_top, spark_left = "60px", "10px 38px", "40px", "48px"
        game_fs, game_top, game_right = "34px", "48px", "52px"
        content_left, content_right, content_bottom, content_align = "66px", "60px", "92px", "flex-start"
        title_fs, stroke_w, pill_fs, sub_fs = "198px", "24px", "54px", "66px"
        row_dir, row_gap, sub_extra = "row", "24px", ""
    elif layout_kind == "4x3":
        hero_pos = hero_pos_override or "center 13%"
        bar_h, bar_bottom, skew = "424px", "-25px", "-3.2deg"
        spark_fs, spark_pad, spark_top, spark_left = "56px", "10px 34px", "38px", "42px"
        game_fs, game_top, game_right = "32px", "48px", "44px"
        content_left, content_right, content_bottom, content_align = "54px", "44px", "92px", "flex-start"
        title_fs, stroke_w, pill_fs, sub_fs = "174px", "22px", "48px", "58px"
        row_dir, row_gap, sub_extra = "row", "20px", ""
    elif layout_kind == "9x16":
        hero_pos = hero_pos_override or "center 18%"
        bar_h, bar_bottom, skew = "680px", "-42px", "-3.2deg"
        spark_fs, spark_pad, spark_top, spark_left = "54px", "10px 32px", "64px", "40px"
        game_fs, game_top, game_right = "30px", "72px", "40px"
        content_left, content_right, content_bottom, content_align = "44px", "44px", "240px", "center"
        title_fs, stroke_w, pill_fs, sub_fs = "136px", "20px", "48px", "54px"
        row_dir, row_gap, sub_extra = "column", "18px", "text-align: center;"
    else:
        hero_pos = hero_pos_override or "74% 12%"
        bar_h, bar_bottom, skew = "546px", "-34px", "-3.5deg"
        spark_fs, spark_pad, spark_top, spark_left = "52px", "10px 30px", "44px", "36px"
        game_fs, game_top, game_right = "30px", "52px", "36px"
        content_left, content_right, content_bottom, content_align = "40px", "40px", "126px", "center"
        title_fs, stroke_w, pill_fs, sub_fs = "138px", "20px", "48px", "54px"
        row_dir, row_gap, sub_extra = "column", "16px", "text-align: center;"

    font_faces = []
    if title_font_uri:
        font_faces.append(f"@font-face {{ font-family: 'TitleFont'; src: url('{title_font_uri}'); font-weight: 400 900; }}")
    if sans_font_uri:
        font_faces.append(f"@font-face {{ font-family: 'SansFont'; src: url('{sans_font_uri}'); font-weight: 100 900; }}")

    title_family = "'TitleFont', 'SansFont', 'Microsoft YaHei', sans-serif" if title_font_uri else "'SansFont', 'Microsoft YaHei', sans-serif"
    sans_family = "'SansFont', 'Microsoft YaHei', sans-serif"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<style>
  {chr(10).join(font_faces)}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: {w}px; height: {h}px; overflow: hidden; position: relative;
    font-family: {sans_family};
    background: #1b6fe8;
  }}
  .bg {{
    position: absolute; inset: 0;
    background: url('{hero_uri}') {hero_pos} / cover no-repeat;
    filter: brightness(0.99) saturate(1.15) contrast(1.06);
  }}
  .diag-bar {{
    position: absolute; left: -50px; right: -50px; bottom: {bar_bottom}; height: {bar_h};
    background: linear-gradient(102deg,
      rgba(16, 90, 224, 0.94) 0%,
      rgba(28, 142, 250, 0.86) 60%,
      rgba(28, 142, 250, 0.28) 100%);
    transform: skewY({skew});
    border-top: 9px solid #ffe94d;
    box-shadow: 0 -10px 30px rgba(10, 55, 155, 0.34);
  }}
  .spark-tag {{
    position: absolute; top: {spark_top}; left: {spark_left};
    background: linear-gradient(135deg, #ff5e3a, #ff2a68);
    color: #ffffff; font-family: {title_family};
    font-size: {spark_fs}; padding: {spark_pad}; border-radius: 16px;
    border: 5px solid #ffffff;
    transform: rotate(-3deg);
    box-shadow: 0 10px 24px rgba(255, 42, 104, 0.44);
    letter-spacing: 3px;
    white-space: nowrap;
  }}
  .game-tag {{
    position: absolute; top: {game_top}; right: {game_right};
    background: rgba(255, 255, 255, 0.94); color: #0e53b8;
    font-size: {game_fs}; font-weight: 900; padding: 10px 26px; border-radius: 999px;
    box-shadow: 0 6px 18px rgba(0, 60, 150, 0.22);
    white-space: nowrap;
  }}
  .content {{
    position: absolute; left: {content_left}; right: {content_right}; bottom: {content_bottom};
    display: flex; flex-direction: column; align-items: {content_align}; gap: 14px;
    {sub_extra}
  }}
  .line1 {{
    position: relative; font-family: {title_family}; font-weight: 900;
    font-size: {title_fs}; line-height: 1.02; letter-spacing: 3px;
    white-space: nowrap;
  }}
  .line1 .s {{
    position: absolute; inset: 0; color: #092e78;
    -webkit-text-stroke: {stroke_w} #092e78;
    filter: drop-shadow(6px 10px 0 rgba(5, 25, 75, 0.52));
  }}
  .line1 .f {{
    position: relative;
    background: linear-gradient(180deg, #ffffff 0%, #fff48c 45%, #ffca1a 100%);
    -webkit-background-clip: text; color: transparent;
  }}
  .line2-row {{
    display: flex; flex-direction: {row_dir}; align-items: center; gap: {row_gap};
  }}
  .pill-hl {{
    background: #ffe94d; color: #0b3480;
    font-family: {title_family}; font-weight: 900;
    font-size: {pill_fs}; padding: 6px 28px; border-radius: 14px;
    box-shadow: 0 6px 16px rgba(5, 25, 75, 0.36);
    white-space: nowrap;
  }}
  .line2 {{
    font-family: {sans_family}; font-weight: 900;
    font-size: {sub_fs}; color: #ffffff; letter-spacing: 2px;
    text-shadow: 4px 4px 0 #092e78, -2px -2px 0 #092e78, 2px -2px 0 #092e78, -2px 2px 0 #092e78;
    white-space: nowrap;
  }}
</style>
</head>
<body>
  <div class="bg"></div>
  <div class="spark-tag">{spark_tag}</div>
  <div class="game-tag">{game_tag}</div>
  <div class="diag-bar"></div>
  <div class="content">
    <div class="line1">
      <span class="s">{main_title}</span>
      <span class="f">{main_title}</span>
    </div>
    <div class="line2-row">
      <div class="pill-hl">{highlight_pill}</div>
      <div class="line2">{sub_text}</div>
    </div>
  </div>
</body>
</html>"""


def _draw_preview_safe_zones(im, kind, font):
    rgba = im.convert("RGBA")
    ov = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov, "RGBA")
    w, h = rgba.size
    if kind == "9x16":
        zone_h = int(round(h * (240.0 / 1920.0)))
        left_w = int(round(w * 0.62))
        d.rectangle([0, h - zone_h, left_w, h], fill=(255, 68, 68, 82), outline=(255, 224, 64, 240), width=3)
        d.text((10, h - zone_h + 8), "♡ 底部文案/点赞避让区", fill=(255, 248, 180, 255), font=font)
        rw = int(round(w * 0.14))
        d.rectangle([w - rw, int(h * 0.45), w, int(h * 0.85)], fill=(255, 68, 68, 72), outline=(255, 224, 64, 220), width=2)
        d.text((w - rw + 4, int(h * 0.45) + 6), "右侧按钮", fill=(255, 248, 180, 255), font=font)
        return Image.alpha_composite(rgba, ov).convert("RGB")

    zone_h = int(round(h * (126.0 / 1440.0 if kind == "3x4" else 92.0 / 1080.0)))
    left_w = int(round(w * (0.42 if kind == "3x4" else 0.28)))
    d.rectangle([0, h - zone_h, left_w, h], fill=(255, 68, 68, 82), outline=(255, 224, 64, 240), width=3)
    lbl = "♡ 点赞安全避让区" if kind == "3x4" else "♡ 点赞/播放避让区"
    d.text((10, h - zone_h + 8), lbl, fill=(255, 248, 180, 255), font=font)
    if kind in ("16x9", "4x3"):
        right_w = int(round(w * 0.18))
        d.rectangle([w - right_w, h - zone_h, w, h], fill=(255, 68, 68, 82), outline=(255, 224, 64, 240), width=3)
        d.text((w - right_w + 10, h - zone_h + 8), "时长避让区", fill=(255, 248, 180, 255), font=font)
    return Image.alpha_composite(rgba, ov).convert("RGB")


def generate_covers(
    out_dir,
    hero_img,
    spark_tag="1分钟省流",
    game_tag="版本快讯",
    main_title="核心结论速览!",
    highlight_pill="重点福利",
    sub_text="机制拆解 · 实用规划",
    hero_pos_3x4=None,
    title_font=None,
    sans_font=None,
    mosaic_boxes=None,
    draw_safe_guides=False,
    include_9x16=False,
    hero_pos_9x16=None,
):
    os.makedirs(out_dir, exist_ok=True)
    src_hero = hero_img
    tmp_mosaic = None
    if mosaic_boxes:
        tmp_mosaic = os.path.join(out_dir, "_hero_mosaic.png")
        src_hero = _apply_hero_mosaic(hero_img, mosaic_boxes, tmp_mosaic)

    hero_uri = file_to_data_uri(src_hero)
    t_path = find_font(([title_font] if title_font else []) + get_cjk_title_candidates(), env_var="FONT_TITLE")
    s_path = find_font(([sans_font] if sans_font else []) + get_cjk_sans_candidates(), env_var="FONT_SANS_VF")
    title_uri = file_to_data_uri(t_path, "font/ttf") if (t_path and t_path.lower().endswith((".ttf", ".otf"))) else ""
    sans_uri = file_to_data_uri(s_path, "font/ttf") if (s_path and s_path.lower().endswith((".ttf", ".otf"))) else ""

    specs = [
        ("16x9", 1920, 1080, os.path.join(out_dir, "cover_16x9"), "16:9 B站个人空间 (1920x1080)", None),
        ("4x3",  1440, 1080, os.path.join(out_dir, "cover_4x3"),  "4:3 抖音横封 & B站首页 (1440x1080)", None),
        ("3x4",  1080, 1440, os.path.join(out_dir, "cover_3x4"),  "3:4 抖音竖封 (1080x1440)", hero_pos_3x4),
    ]
    if include_9x16:
        specs.append(("9x16", 1080, 1920, os.path.join(out_dir, "cover_9x16"), "9:16 竖屏全屏封面 (1080x1920)", hero_pos_9x16))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for kind, w, h, stem, _, pos_ovr in specs:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            html = build_style_c_html(
                w, h, hero_uri, title_uri, sans_uri,
                spark_tag, game_tag, main_title, highlight_pill, sub_text,
                kind, hero_pos_override=pos_ovr,
            )
            page.set_content(html, wait_until="load")
            page.evaluate("() => document.fonts.ready")
            page.wait_for_timeout(300)
            png_path = stem + ".png"
            jpg_path = stem + ".jpg"
            page.screenshot(path=png_path, type="png")
            page.close()
            Image.open(png_path).convert("RGB").save(jpg_path, quality=95)
            print(f"COVER_SAVED: {kind} -> {jpg_path}")
        browser.close()

    if tmp_mosaic and os.path.exists(tmp_mosaic):
        os.remove(tmp_mosaic)

    th = 720
    tiles = []
    for kind, w, h, stem, lbl, _ in specs:
        im = Image.open(stem + ".jpg").convert("RGB")
        resized = im.resize((int(round(th * w / h)), th), Image.Resampling.LANCZOS)
        tiles.append((kind, resized, lbl))
    pad, header_h = 24, 62
    total_w = sum(t[1].width for t in tiles) + pad * (len(tiles) + 1)
    sheet = Image.new("RGB", (total_w, th + header_h + pad), (242, 246, 252))
    draw = ImageDraw.Draw(sheet)
    lbl_fp = find_font(get_cjk_sans_candidates())
    try:
        lbl_font = ImageFont.truetype(lbl_fp, 28) if lbl_fp else ImageFont.load_default()
        guide_font = ImageFont.truetype(lbl_fp, 18) if lbl_fp else ImageFont.load_default()
    except Exception:
        lbl_font = ImageFont.load_default()
        guide_font = lbl_font
    x = pad
    for kind, im, lbl in tiles:
        draw.text((x + 4, 18), lbl, fill=(14, 52, 118), font=lbl_font)
        tile = _draw_preview_safe_zones(im, kind, guide_font) if draw_safe_guides else im
        sheet.paste(tile, (x, header_h))
        x += im.width + pad
    preview = os.path.join(out_dir, "covers_preview.jpg")
    sheet.save(preview, quality=94)
    print("COVERS_PREVIEW:", preview)
    res = {f"cover_{kind}": stem + ".jpg" for kind, _, _, stem, _, _ in specs}
    res["preview"] = preview
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Style-C Multi-Ratio Cover Generator (16:9 + 4:3 + 3:4 + optional 9:16)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--hero", required=True)
    ap.add_argument("--spark", "--badge", dest="spark", default="1分钟省流")
    ap.add_argument("--game-tag", default="版本快讯")
    ap.add_argument("--title", default="核心结论速览!")
    ap.add_argument("--pill", default="重点福利")
    ap.add_argument("--sub", default="机制拆解 · 实用规划")
    ap.add_argument("--hero-pos-3x4", default=None)
    ap.add_argument("--include-9x16", action="store_true", help="Also generate optional 9:16 (1080x1920) vertical cover")
    ap.add_argument("--hero-pos-9x16", default=None, help="Optional CSS object-position for 9:16 cover")
    ap.add_argument("--title-font", default=None)
    ap.add_argument("--sans-font", default=None)
    ap.add_argument("--mosaic", action="append", default=[], help="x1,y1,x2,y2 (repeatable 24px pixel mosaic)")
    ap.add_argument("--safe-zone-guide", action="store_true", help="Overlay bottom-left like/play & duration safe zones on covers_preview.jpg")
    args = ap.parse_args()
    mboxes = [[int(float(v)) for v in m.split(",")] for m in args.mosaic]
    generate_covers(
        args.out_dir,
        args.hero,
        spark_tag=args.spark,
        game_tag=args.game_tag,
        main_title=args.title,
        highlight_pill=args.pill,
        sub_text=args.sub,
        hero_pos_3x4=args.hero_pos_3x4,
        title_font=args.title_font,
        sans_font=args.sans_font,
        mosaic_boxes=mboxes,
        draw_safe_guides=args.safe_zone_guide,
        include_9x16=args.include_9x16,
        hero_pos_9x16=args.hero_pos_9x16,
    )
