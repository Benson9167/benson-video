"""
Bilibili official & gameplay media fetcher + timestamped contact-sheet generator.
Usage:
  python -X utf8 scripts/fetch_bili_media.py --work-dir <DIR> --keywords "版本前瞻,实机演示" --max-per-kw 5
  python -X utf8 scripts/fetch_bili_media.py --work-dir <DIR> --bvids "BV1xxx,alias=BV1yyy" --download
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from PIL import Image, ImageDraw

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from media_utils import get_ffmpeg, get_ffprobe

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def get_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def search_bili(keyword, order="click", limit=8):
    url = (
        f"https://api.bilibili.com/x/web-interface/search/type?"
        f"search_type=video&keyword={urllib.parse.quote(keyword)}&order={order}&page=1&page_size={limit}"
    )
    data = get_json(url)
    rows = []
    for v in (data.get("data", {}).get("result") or [])[:limit]:
        title = v.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
        pub = datetime.datetime.fromtimestamp(v.get("pubdate", 0)).strftime("%Y-%m-%d")
        rows.append({
            "bvid": v.get("bvid"),
            "pubdate": pub,
            "duration": v.get("duration"),
            "play": v.get("play"),
            "author": v.get("author"),
            "title": title,
        })
    return rows


def download_bvid(bvid, media_dir, alias=None):
    info = get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")["data"]
    stem = alias or bvid
    pic_url = info.get("pic")
    if pic_url:
        cov_path = os.path.join(media_dir, f"cover_{stem}.jpg")
        if not os.path.exists(cov_path):
            req = urllib.request.Request(pic_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r, open(cov_path, "wb") as f:
                f.write(r.read())

    mp4_path = os.path.join(media_dir, f"{stem}.mp4")
    if not (os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 100000):
        cmd = [
            "yt-dlp", "--no-update", "--no-warnings",
            "-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080][ext=mp4]/b",
            "--merge-output-format", "mp4",
            "-o", mp4_path, f"https://www.bilibili.com/video/{bvid}",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            print(f"DOWNLOAD ERR {bvid}: {p.stderr[-250:]}")
            return None
    return {
        "bvid": bvid,
        "file": mp4_path,
        "title": info.get("title"),
        "author": info.get("owner", {}).get("name"),
        "duration": info.get("duration"),
    }


def build_contact_sheet(media_dir, sheets_dir, samples=5):
    ffmpeg = get_ffmpeg()
    ffprobe = get_ffprobe()
    os.makedirs(sheets_dir, exist_ok=True)
    mp4s = sorted(f for f in os.listdir(media_dir) if f.endswith(".mp4"))
    if not mp4s:
        print("No MP4 files found for contact sheet.")
        return None
    tw, th = 320, 180
    sheet = Image.new("RGB", (tw * samples, th * len(mp4s)), (12, 12, 12))
    draw = ImageDraw.Draw(sheet)
    tmp = os.path.join(sheets_dir, "_tmp_frame.jpg")
    fracs = [0.08 + i * (0.82 / max(1, samples - 1)) for i in range(samples)]
    for r, fn in enumerate(mp4s):
        fp = os.path.join(media_dir, fn)
        pr = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=width,height:format=duration", "-of", "json", fp],
            capture_output=True, text=True,
        )
        meta = json.loads(pr.stdout)
        dur = float(meta["format"]["duration"])
        st = meta["streams"][0]
        print(f"MEDIA: {fn:24s} | {st['width']}x{st['height']} | {dur:.1f}s")
        for c, fr in enumerate(fracs):
            t = round(dur * fr, 1)
            subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", str(t), "-i", fp, "-frames:v", "1", "-q:v", "5", tmp], check=False)
            if os.path.exists(tmp):
                im = Image.open(tmp).convert("RGB").resize((tw, th), Image.Resampling.LANCZOS)
                x, y = c * tw, r * th
                sheet.paste(im, (x, y))
                draw.rectangle([x, y, x + 210, y + 20], fill=(0, 0, 0))
                draw.text((x + 4, y + 3), f"{fn[:-4]} @ {t}s", fill=(255, 235, 95))
    if os.path.exists(tmp):
        os.remove(tmp)
    out = os.path.join(sheets_dir, "videos_sheet.jpg")
    sheet.save(out, quality=86)
    print("CONTACT SHEET:", out)
    return out


def main():
    ap = argparse.ArgumentParser(description="Search & download Bilibili media + build shot contact sheet.")
    ap.add_argument("--work-dir", required=True, help="Task working directory (creates media/ and sheets/)")
    ap.add_argument("--keywords", default="", help="Comma-separated search keywords")
    ap.add_argument("--order", default="click", choices=["click", "pubdate", "totalrank"])
    ap.add_argument("--max-per-kw", type=int, default=6)
    ap.add_argument("--bvids", default="", help="Comma-separated BVIDs or alias=BVID pairs to download")
    ap.add_argument("--download", action="store_true", help="Download specified --bvids")
    ap.add_argument("--sheet-only", action="store_true", help="Only build contact sheet from existing media/")
    ap.add_argument("--samples", type=int, default=5, help="Sample frames per video in contact sheet")
    args = ap.parse_args()

    media_dir = os.path.join(args.work_dir, "media")
    sheets_dir = os.path.join(args.work_dir, "sheets")
    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(sheets_dir, exist_ok=True)

    if args.keywords:
        for kw in [k.strip() for k in args.keywords.split(",") if k.strip()]:
            print(f"=== SEARCH: {kw} ({args.order}) ===")
            try:
                for row in search_bili(kw, args.order, args.max_per_kw):
                    print(f"{row['pubdate']} | {row['bvid']} | {row['duration']} | play={row['play']} | {row['author']} | {row['title']}")
            except Exception as e:
                print(f"SEARCH ERR ({kw}): {e}")
            time.sleep(0.5)

    if args.download and args.bvids:
        manifest = []
        for item in [x.strip() for x in args.bvids.split(",") if x.strip()]:
            if "=" in item:
                alias, bvid = item.split("=", 1)
            else:
                alias, bvid = None, item
            res = download_bvid(bvid.strip(), media_dir, alias.strip() if alias else None)
            if res:
                manifest.append(res)
                print("OK:", res["bvid"], "->", os.path.basename(res["file"]))
        with open(os.path.join(media_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        build_contact_sheet(media_dir, sheets_dir, args.samples)
    elif args.sheet_only:
        build_contact_sheet(media_dir, sheets_dir, args.samples)


if __name__ == "__main__":
    main()
