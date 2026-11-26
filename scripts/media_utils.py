"""
Cross-platform FFmpeg/FFprobe & CJK Font Discovery Helpers for benson-video.
Avoids hardcoded machine paths by checking env vars, PATH, and standard OS font directories.
"""
import os
import shutil


def get_ffmpeg():
    cand = os.environ.get("FFMPEG_BIN", "").strip()
    if cand and os.path.exists(cand):
        return cand
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError(
        "未找到 ffmpeg。请将 ffmpeg 加入系统 PATH，或设置环境变量 FFMPEG_BIN。"
    )


def get_ffprobe():
    cand = os.environ.get("FFPROBE_BIN", "").strip()
    if cand and os.path.exists(cand):
        return cand
    found = shutil.which("ffprobe")
    if found:
        return found
    ff = os.environ.get("FFMPEG_BIN", "").strip()
    if ff:
        sibling = os.path.join(os.path.dirname(ff), "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.exists(sibling):
            return sibling
    raise FileNotFoundError(
        "未找到 ffprobe。请将 ffprobe 加入系统 PATH，或设置环境变量 FFPROBE_BIN。"
    )


def find_font(candidates=None, env_var=None):
    if env_var:
        ev = os.environ.get(env_var, "").strip()
        if ev and os.path.exists(ev):
            return ev
    for p in (candidates or []):
        if p and os.path.exists(p):
            return p
    return None


def get_cjk_sans_candidates():
    return [
        r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]


def get_cjk_title_candidates():
    return [
        r"C:\Windows\Fonts\YouSheBiaoTiHei-2 1.ttf",
        r"C:\Windows\Fonts\YouSheBiaoTiHei.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
