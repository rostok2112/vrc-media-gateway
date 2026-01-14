import subprocess
import time
import re
import urllib.parse as urlparse
import requests
from pathlib import Path
from typing import Tuple
from fastapi import HTTPException
from .config import STREAMS, FFMPEG, AUDIO_TARGET, HLS_OPTS

def run_cmd(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\nstdout:{p.stdout}\nstderr:{p.stderr}")
    return p

def rewrite_m3u8(m3u8: Path, stream_id: str):
    text = m3u8.read_text(encoding="utf-8")
    out_lines = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            out_lines.append(line)
        else:
            out_lines.append(f"/api/stream_segment/{stream_id}/{line.lstrip('./')}")
    m3u8.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

def normalize_yt_url(url: str) -> str:
    p = urlparse.urlparse(url)
    netloc = p.netloc.lower()
    if netloc.endswith("youtu.be"):
        video_id = p.path.lstrip("/")
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    if "youtube" in netloc:
        qs = urlparse.parse_qs(p.query)
        if "v" in qs:
            return f"https://www.youtube.com/watch?v={qs['v'][0]}"
    return url

# telegram helpers
def extract_image_from_telegram_html(html: str, base_url: str = None):
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        m = re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        m = re.search(r'<img[^>]+class=["\'][^"\']*tgme_widget_message_photo_wrap[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        m = re.search(r'data-src=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    if not m:
        return None
    img = m.group(1)
    if base_url and img.startswith("/"):
        return urlparse.urljoin(base_url, img)
    return img

def try_fetch_telegram_post(url: str, timeout=15) -> Tuple[str,str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r.text, r.url
    except Exception:
        pass
    p = urlparse.urlparse(url)
    if p.netloc.endswith("t.me"):
        parts = p.path.strip("/").split("/")
        if len(parts) >= 2:
            channel, msg = parts[0], parts[1]
            alt = f"https://t.me/s/{channel}/{msg}"
            try:
                r = requests.get(alt, headers=headers, timeout=timeout, allow_redirects=True)
                r.raise_for_status()
                return r.text, r.url
            except Exception:
                pass
    raise HTTPException(status_code=400, detail="can't fetch telegram post HTML; maybe it's private or blocked")

def ffmpeg_audio_params() -> list:
    return [
        "-c:a", AUDIO_TARGET["codec"],
        "-profile:a", AUDIO_TARGET["profile"],
        "-ac", AUDIO_TARGET["channels"],
        "-ar", AUDIO_TARGET["samplerate"],
        "-b:a", AUDIO_TARGET["bitrate"],
    ]
