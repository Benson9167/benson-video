"""
4:3 Bulletin Card Overlay Generator (Module A / Module B / Module C).

Generates high-readability 1440x1080 PNG cards/overlays compatible with
`render_video.py` (`layout="4x3_card"`, via `src` or `custom_overlay`):
  - module_a: Top/bottom dark perspective-grid bands + bold headline + center poster (RGB PNG)
  - module_b: Transparent RGBA overlay with centered ~86% frosted-white bullet card (RGBA PNG)
  - module_c: Full-bleed poster with subtle bottom dark scrim + optional top-left pill (RGB PNG)
"""
import argparse
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from media_utils import find_font, get_cjk_sans_candidates, get_cjk_title_candidates

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_font(size, weight="bold"):
    if weight == "title":
        tp = find_font(get_cjk_title_candidates(), env_var="FONT_TITLE")
        if tp:
            try:
                return ImageFont.truetype(tp, size)
            except Exception:
                pass

    vf = find_font(
        [p for p in get_cjk_sans_candidates() if p.lower().endswith("-vf.ttf")],
        env_var="FONT_SANS_VF",
    )
    if vf:
        try:
            f = ImageFont.truetype(vf, size)
            wmap = {"black": "Black", "bold": "Bold", "medium": "Medium", "regular": "Regular"}
            f.set_variation_by_name(wmap.get(weight, "Bold"))
            return f
        except Exception:
            pass

    cand = find_font(get_cjk_sans_candidates())
    if cand:
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap_text(draw, text, font, max_w):
    lines = []
    cur = ""
    for ch in text:
        test = cur + ch
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _draw_perspective_grid(img, W, H, y_start, y_end, from_top=True, accent_rgb=(95, 175, 255)):
    draw = ImageDraw.Draw(img, "RGBA")
    ar, ag, ab = accent_rgb
    span = max(1, y_end - y_start)
    for y in range(y_start, y_end):
        t = (y - y_start) / span
        if from_top:
            r = int(8 + 10 * t)
            g = int(12 + 18 * t)
            b = int(26 + 30 * t)
        else:
            r = int(18 - 10 * t)
            g = int(30 - 18 * t)
            b = int(56 - 30 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    cx = W // 2
    for i in range(-12, 13):
        x_edge = cx + i * 145
        if from_top:
            draw.line([(x_edge, y_start), (cx + i * 52, y_end)], fill=(ar, ag, ab, 28), width=1)
        else:
            draw.line([(cx + i * 52, y_start), (x_edge, y_end)], fill=(ar, ag, ab, 28), width=1)

    rows = 6
    for k in range(1, rows + 1):
        frac = (k / rows) ** 1.45
        y = int(y_start + span * (frac if from_top else (1 - frac)))
        draw.line([(0, y), (W, y)], fill=(ar, ag, ab, 24), width=1)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    if from_top:
        gd.ellipse([cx - 420, y_start - 30, cx + 420, y_end + 20], fill=(ar, ag, ab, 40))
    else:
        gd.ellipse([cx - 380, y_start - 20, cx + 380, y_end + 40], fill=(ar, ag, ab, 32))
    glow = glow.filter(ImageFilter.GaussianBlur(45))
    img.alpha_composite(glow)


def _cover_crop(im, target_w, target_h, x_bias=0.5, y_bias=0.5):
    pw, ph = im.size
    scale = max(target_w / pw, target_h / ph)
    nw, nh = int(round(pw * scale)), int(round(ph * scale))
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    cx = int((nw - target_w) * max(0.0, min(1.0, x_bias)))
    cy = int((nh - target_h) * max(0.0, min(1.0, y_bias)))
    return im.crop((cx, cy, cx + target_w, cy + target_h))


def render_module_a(card, W, H, out_path, resolve_fn):
    canvas = Image.new("RGBA", (W, H), (8, 12, 24, 255))
    poster_top = int(round(H * 162 / 1080.0))
    poster_h = int(round(H * 740 / 1080.0))
    poster_bot = poster_top + poster_h
    accent = tuple(card.get("accent_rgb", [95, 175, 255]))

    _draw_perspective_grid(canvas, W, H, 0, poster_top, from_top=True, accent_rgb=accent)
    _draw_perspective_grid(canvas, W, H, poster_bot, H, from_top=False, accent_rgb=accent)

    poster_path = resolve_fn(card["poster"])
    poster = Image.open(poster_path).convert("RGBA")
    poster = _cover_crop(
        poster, W, poster_h,
        x_bias=float(card.get("poster_x_bias", 0.5)),
        y_bias=float(card.get("poster_y_bias", 0.42)),
    )
    canvas.paste(poster, (0, poster_top))

    draw = ImageDraw.Draw(canvas, "RGBA")
    line_col = (min(255, accent[0] + 60), min(255, accent[1] + 45), min(255, accent[2] + 20), 210)
    draw.rectangle([0, poster_top - 2, W, poster_top + 1], fill=line_col)
    draw.rectangle([0, poster_bot - 1, W, poster_bot + 2], fill=line_col)

    title_text = card.get("title", "")
    tag_text = (card.get("tag") or "").strip()
    f_title = load_font(int(round(H * 86 / 1080.0)), "black")
    f_tag = load_font(int(round(H * 30 / 1080.0)), "bold")

    bbox = draw.textbbox((0, 0), title_text, font=f_title)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    if tag_text:
        tb = draw.textbbox((0, 0), tag_text, font=f_tag)
        tag_w = (tb[2] - tb[0]) + 34
        tag_h = (tb[3] - tb[1]) + 18
        total_w = tw + 24 + tag_w
    else:
        tag_w = tag_h = 0
        total_w = tw

    tx = (W - total_w) // 2
    ty = (poster_top - th) // 2 - 12

    flare = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(flare)
    fd.ellipse([tx - 60, ty + th // 2 - 18, tx + tw + 60, ty + th // 2 + 26], fill=(accent[0], accent[1], accent[2], 85))
    flare = flare.filter(ImageFilter.GaussianBlur(22))
    canvas.alpha_composite(flare)

    draw = ImageDraw.Draw(canvas, "RGBA")
    if title_text:
        draw.text((tx + 3, ty + 4), title_text, font=f_title, fill=(0, 0, 0, 200))
        draw.text((tx, ty), title_text, font=f_title, fill=(248, 252, 255, 255), stroke_width=2, stroke_fill=(32, 92, 175, 220))

    if tag_text:
        tag_color = tuple(card.get("tag_color", [224, 58, 62]))
        pill_x = tx + tw + 24
        pill_y = ty + (th - tag_h) // 2 + 8
        draw.rounded_rectangle(
            [pill_x, pill_y, pill_x + tag_w, pill_y + tag_h],
            radius=10,
            fill=(*tag_color[:3], 245),
            outline=(255, 215, 130, 220),
            width=2,
        )
        draw.text((pill_x + 17, pill_y + 5), tag_text, font=f_tag, fill=(255, 255, 255, 255))

    canvas.convert("RGB").save(out_path)
    return out_path


def render_module_b(card, W, H, out_path):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    vig_h = int(round(H * 220 / 1080.0))
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H - vig_h, H):
        a = int(155 * ((y - (H - vig_h)) / max(1, vig_h)))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    overlay.alpha_composite(grad)

    card_w = int(round(W * 0.864))
    card_x = (W - card_w) // 2
    pad_x = 46
    pad_top = 38
    pad_bot = 38

    f_hdr = load_font(48, "black")
    f_badge = load_font(28, "bold")
    f_key = load_font(36, "bold")
    f_body = load_font(36, "medium")

    dummy = ImageDraw.Draw(overlay)
    inner_w = card_w - pad_x * 2
    line_h = 52
    bullet_gap = 22
    rendered_bullets = []
    content_h = 0

    for item in card.get("bullets", []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            prefix = f"• 【{item[0]}】"
            desc = str(item[1])
            pw = dummy.textbbox((0, 0), prefix, font=f_key)[2]
        elif isinstance(item, dict):
            k = (item.get("key") or "").strip()
            prefix = f"• 【{k}】" if k else "• "
            desc = str(item.get("text", ""))
            pw = dummy.textbbox((0, 0), prefix, font=f_key)[2]
        else:
            prefix = "• "
            desc = str(item)
            pw = dummy.textbbox((0, 0), prefix, font=f_key)[2]

        first_max = max(120, inner_w - pw)
        rem = desc
        first_line = ""
        while rem:
            ch = rem[0]
            if dummy.textbbox((0, 0), first_line + ch, font=f_body)[2] <= first_max:
                first_line += ch
                rem = rem[1:]
            else:
                break
        extra_lines = _wrap_text(dummy, rem, f_body, max(120, inner_w - 38)) if rem else []
        rendered_bullets.append((prefix, pw, first_line, extra_lines))
        content_h += line_h * (1 + len(extra_lines)) + bullet_gap

    content_h = max(line_h, content_h - bullet_gap)
    header_h = 74
    divider_gap = 28
    card_h = pad_top + header_h + divider_gap + content_h + pad_bot

    safe_top = int(round(H * 60 / 1080.0))
    safe_bot = int(round(H * 885 / 1080.0))
    card_y = max(safe_top, safe_top + (safe_bot - safe_top - card_h) // 2)

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [card_x + 6, card_y + 12, card_x + card_w + 6, card_y + card_h + 12],
        radius=24,
        fill=(6, 12, 28, 120),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    overlay.alpha_composite(shadow)

    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=24,
        fill=(250, 252, 255, 224),
        outline=(255, 255, 255, 245),
        width=3,
    )

    badge_rgb = tuple(card.get("badge_color", [24, 112, 224]))[:3]
    badge_color = (*badge_rgb, 255)
    bar_x = card_x + pad_x
    bar_y = card_y + pad_top + 6
    draw.rounded_rectangle([bar_x, bar_y, bar_x + 10, bar_y + 48], radius=5, fill=badge_color)

    draw.text((bar_x + 24, card_y + pad_top - 2), card.get("title", ""), font=f_hdr, fill=(17, 25, 42, 255))

    badge = (card.get("badge") or "").strip()
    if badge:
        bb = draw.textbbox((0, 0), badge, font=f_badge)
        bw = (bb[2] - bb[0]) + 34
        bh = (bb[3] - bb[1]) + 18
        bx = card_x + card_w - pad_x - bw
        by = card_y + pad_top + 6
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=12, fill=badge_color)
        draw.text((bx + 17, by + 5), badge, font=f_badge, fill=(255, 255, 255, 255))

    div_y = card_y + pad_top + header_h + 2
    draw.line([(card_x + pad_x, div_y), (card_x + card_w - pad_x, div_y)], fill=(195, 210, 232, 220), width=2)

    key_rgb = tuple(card.get("key_color", [18, 86, 184]))[:3]
    cur_y = div_y + divider_gap
    for prefix, pw, first_line, extra_lines in rendered_bullets:
        draw.text((card_x + pad_x, cur_y), prefix, font=f_key, fill=(*key_rgb, 255))
        draw.text((card_x + pad_x + pw, cur_y), first_line, font=f_body, fill=(24, 30, 44, 255))
        cur_y += line_h
        for el in extra_lines:
            draw.text((card_x + pad_x + 38, cur_y), el, font=f_body, fill=(24, 30, 44, 255))
            cur_y += line_h
        cur_y += bullet_gap

    overlay.save(out_path)
    return out_path


def render_module_c(card, W, H, out_path, resolve_fn):
    poster = Image.open(resolve_fn(card["poster"])).convert("RGBA")
    canvas = _cover_crop(
        poster, W, H,
        x_bias=float(card.get("x_bias", 0.5)),
        y_bias=float(card.get("y_bias", 0.5)),
    )

    vig_h = int(round(H * 260 / 1080.0))
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H - vig_h, H):
        a = int(175 * ((y - (H - vig_h)) / max(1, vig_h)))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    canvas.alpha_composite(grad)

    pill = (card.get("pill") or "").strip()
    if pill:
        draw = ImageDraw.Draw(canvas, "RGBA")
        f_pill = load_font(34, "bold")
        tb = draw.textbbox((0, 0), pill, font=f_pill)
        pw_b = (tb[2] - tb[0]) + 44
        ph_b = (tb[3] - tb[1]) + 22
        pill_bg = tuple(card.get("pill_bg", [14, 24, 52]))[:3]
        pill_border = tuple(card.get("pill_border", [140, 215, 255]))[:3]
        draw.rounded_rectangle(
            [42, 38, 42 + pw_b, 38 + ph_b],
            radius=14,
            fill=(*pill_bg, 215),
            outline=(*pill_border, 230),
            width=2,
        )
        draw.text((64, 47), pill, font=f_pill, fill=(255, 255, 255, 255))

    canvas.convert("RGB").save(out_path)
    return out_path


def validate_spec(spec, resolve_fn):
    errors = []
    cards = spec.get("cards") or []
    if not cards:
        errors.append("cards 列表为空")
    for c in cards:
        cid = c.get("id", "unknown")
        ctype = c.get("type")
        if ctype not in ("module_a", "module_b", "module_c"):
            errors.append(f"不支持的卡片类型 ({cid}): {ctype}")
            continue
        if ctype in ("module_a", "module_c"):
            p = c.get("poster")
            rp = resolve_fn(p)
            if not rp or not os.path.exists(rp):
                errors.append(f"海报/配图文件不存在 ({cid}): {rp}")
        if ctype == "module_b" and not c.get("bullets"):
            errors.append(f"module_b 缺少 bullets 列表 ({cid})")
    return errors


def _make_preview_sheet(paths_with_ids, out_dir):
    if not paths_with_ids:
        return None
    tw, th = 720, 540
    cols = 2
    rows = (len(paths_with_ids) + cols - 1) // cols
    sheet = Image.new("RGB", (tw * cols, th * rows), (18, 24, 38))
    draw = ImageDraw.Draw(sheet)
    f_lbl = load_font(24, "bold")
    for idx, (cid, p) in enumerate(paths_with_ids):
        im = Image.open(p).convert("RGBA")
        bg = Image.new("RGBA", im.size, (34, 42, 64, 255))
        bg.alpha_composite(im)
        tile = bg.convert("RGB").resize((tw, th), Image.Resampling.LANCZOS)
        x = (idx % cols) * tw
        y = (idx // cols) * th
        sheet.paste(tile, (x, y))
        draw.rectangle([x + 12, y + 12, x + 280, y + 48], fill=(10, 14, 26))
        draw.text((x + 20, y + 17), cid, font=f_lbl, fill=(255, 232, 120))
    sheet_path = os.path.join(out_dir, "cards_preview.jpg")
    sheet.save(sheet_path, quality=92)
    return sheet_path


def main():
    ap = argparse.ArgumentParser(description="Generate 4:3 Module A/B/C Card Overlays for render_video.py")
    ap.add_argument("--spec", required=True, help="Path to card_overlays.json")
    ap.add_argument("--out-dir", default=None, help="Output directory (overrides spec out_dir)")
    ap.add_argument("--width", type=int, default=None, help="Canvas width (default 1440)")
    ap.add_argument("--height", type=int, default=None, help="Canvas height (default 1080)")
    ap.add_argument("--sheet", action="store_true", help="Also generate cards_preview.jpg contact sheet")
    ap.add_argument("--dry-run", action="store_true", help="Validate spec and image paths without rendering PNGs")
    args = ap.parse_args()

    spec_abs = os.path.abspath(args.spec)
    base_dir = os.path.dirname(spec_abs)
    with open(spec_abs, "r", encoding="utf-8-sig") as f:
        spec = json.load(f)

    def _resolve(p):
        if not p:
            return None
        if os.path.isabs(p) or os.path.exists(p):
            return os.path.abspath(p)
        return os.path.abspath(os.path.join(base_dir, p))

    errors = validate_spec(spec, _resolve)
    if args.dry_run:
        print(json.dumps({
            "passed": len(errors) == 0,
            "errors": errors,
            "card_count": len(spec.get("cards") or []),
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if not errors else 2)

    if errors:
        raise RuntimeError("card_overlays spec 校验失败：\n  - " + "\n  - ".join(errors))

    W = args.width or int(spec.get("width", 1440))
    H = args.height or int(spec.get("height", 1080))
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else _resolve(spec.get("out_dir") or "cards")
    os.makedirs(out_dir, exist_ok=True)

    generated = []
    for card in spec["cards"]:
        cid = card["id"]
        ctype = card["type"]
        out_path = os.path.join(out_dir, f"{cid}.png")
        if ctype == "module_a":
            render_module_a(card, W, H, out_path, _resolve)
        elif ctype == "module_b":
            render_module_b(card, W, H, out_path)
        else:
            render_module_c(card, W, H, out_path, _resolve)
        generated.append((cid, out_path))
        print(f"CARD_SAVED: {cid} ({ctype}) -> {out_path}")

    sheet_path = _make_preview_sheet(generated, out_dir) if args.sheet else None
    if sheet_path:
        print(f"CARDS_PREVIEW: {sheet_path}")

    print(json.dumps({
        "out_dir": out_dir,
        "cards": {cid: p for cid, p in generated},
        "preview": sheet_path,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
