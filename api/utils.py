import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from shutil import copyfile
import tempfile
import time
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple, Union
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import psutil
import requests
from fastapi import HTTPException
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.types import Message as TelethonMessage, PeerChannel

from api import config

import logging

from api.segments_engine import registry

logger = logging.getLogger(__name__)


# =========================
# LOW LEVEL
# =========================

def run_cmd(cmd: List[str], timeout: Optional[int] = None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(
            f"CMD FAILED:\n{' '.join(cmd)}\n\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}"
        )
    return p


def wait_hls_ready(out_dir: Path, timeout: int = 30) -> bool:
    m3u8 = out_dir / "index.m3u8"
    start = time.time()
    while time.time() - start < timeout:
        if m3u8.exists() and m3u8.stat().st_size > 200:
            ts = list(out_dir.glob("*.ts"))
            if ts and any(t.stat().st_size > 1024 for t in ts):
                return True
        time.sleep(0.3)
    return False


def download_file(url: str, dest: Path, max_bytes: int = 8 * 1024 * 1024):
    headers = {"User-Agent": "Mozilla/5.0"}
    with requests.get(url, headers=headers, stream=True, timeout=15) as r:
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(8192):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(400, "file too large")
                f.write(chunk)
    return dest


# =========================
# AUDIO PARAMS
# =========================

def ffmpeg_audio_params() -> List[str]:
    at = config.AUDIO_TARGET
    args = [
        "-c:a", at.get("codec", "aac"),
        "-ac", str(at.get("channels", 2)),
        "-ar", str(at.get("samplerate", 48000)),
        "-b:a", at.get("bitrate", "256k"),
    ]

    # корректный downmix 5.1 → stereo
    if int(at.get("channels", 2)) == 2:
        args += [
            "-af",
            "pan=stereo|FL<FL+0.0*FC+0.6*BL|FR<FR+0.0*FC+0.6*BR"
        ]
    return args


# =========================
# IMAGE → MP4
# =========================

def image_to_mp4(
    image: Path,
    out_mp4: Path,
    duration: int,
    width: int,
    height: int,
):
    cmd = [
        config.FFMPEG, "-y",
        "-loop", "1",
        "-i", str(image),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(duration),
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "fast",
        "-profile:v", "high",
        "-level", "4.2",
        *ffmpeg_audio_params(),
        "-shortest",
        str(out_mp4)
    ]
    run_cmd(cmd)


# =========================
# HLS BUILDERS
# =========================

def video_to_hls(video: Path, out_dir: Path, stream_id: str):
    m3u8 = out_dir / "index.m3u8"
    hls = config.HLS_OPTS

    cmd = [
        config.FFMPEG, "-y",
        "-i", str(video),
        "-c:v", "copy",
        *ffmpeg_audio_params(),
        "-f", "hls",
        "-hls_time", str(hls.get("hls_time", 4)),
        "-hls_list_size", str(hls.get("hls_list_size", 0)),
        "-hls_playlist_type", hls.get("hls_playlist_type", "vod"),
        "-hls_base_url", f"/streams/{stream_id}/",
        str(m3u8)
    ]
    run_cmd(cmd)


def audio_to_hls(audio: Path, out_dir: Path, stream_id: str):
    m3u8 = out_dir / "index.m3u8"
    hls = config.HLS_OPTS

    cmd = [
        config.FFMPEG, "-y",
        "-i", str(audio),
        "-vn",
        *ffmpeg_audio_params(),
        "-f", "hls",
        "-hls_time", str(hls.get("hls_time", 4)),
        "-hls_list_size", str(hls.get("hls_list_size", 0)),
        "-hls_playlist_type", hls.get("hls_playlist_type", "vod"),
        "-hls_base_url", f"/streams/{stream_id}/",
        str(m3u8)
    ]
    run_cmd(cmd)


# =========================
# IMAGE → HLS (ОРКЕСТРАТОР)
# =========================

def build_hls_from_image(
    image_url: str,
    stream_id: str,
    duration: int = 300,
    width: int = 1280,
    height: int = 720,
):
    out_dir = config.STREAMS / stream_id
    m3u8 = out_dir / "index.m3u8"

    if m3u8.exists():
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    img = out_dir / "src.jpg"
    mp4 = out_dir / "video.mp4"

    # FIX: support local files from Telethon
    if Path(image_url).exists():
        copyfile(image_url, img)
    else:
        download_file(image_url, img)

    image_to_mp4(img, mp4, duration, width, height)
    video_to_hls(mp4, out_dir, stream_id)

    try:
        mp4.unlink()
    except Exception:
        pass

    if not wait_hls_ready(out_dir):
        raise HTTPException(500, "HLS build timeout")


# =========================
# HTML IMAGE EXTRACTION (TG / WEB)
# =========================

def extract_image_from_html(html: str, base_url: Optional[str] = None) -> Optional[str]:
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'data-src=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+)["\']',
    ]

    for p in patterns:
        m = re.search(p, html, flags=re.I)
        if m:
            url = m.group(1)
            if base_url and url.startswith("/"):
                return requests.compat.urljoin(base_url, url)
            if url.startswith("//"):
                return "https:" + url
            return url
    return None


def fetch_html(url: str) -> Tuple[str, str]:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
    r.raise_for_status()
    return r.text, r.url


def ensure_file(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("file does not exist or empty")


def normalize_url(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not (k.lower().startswith("utm_") or k.lower() == "fbclid")]
    q.sort()
    return urlunparse((p.scheme, p.netloc, p.path or "/", p.params, urlencode(q, doseq=True), ""))

def sid_for_url(url: str, *extra_parts) -> str:
    s = normalize_url(url)
    if extra_parts:
        s += "|" + "|".join(str(x) for x in extra_parts)
    return hashlib.md5(s.encode()).hexdigest()

def out_dir_for_sid(sid: str) -> Path:
    return config.STREAMS / sid

def build_virtual_m3u8(sid: str, duration_ms: int, seg_time: int) -> str:
    total_sec = duration_ms / 1000
    segments = int(total_sec // seg_time) + 1

    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{seg_time}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]

    for i in range(segments):
        lines.append(f"#EXTINF:{seg_time:.3f},")
        lines.append(f"/api/stream-spotify-segment/{sid}/segment_{i:05d}.ts")

    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)

def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)

def read_metadata(out_dir: Path) -> dict:
    meta_path = out_dir / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read metadata.json at %s", meta_path)
        return {}

def build_live_m3u8(out_dir: Path, now_dt: Optional[datetime] = None, window_seconds: Optional[int] = 300) -> str:
    now = now_dt or datetime.now(timezone.utc)
    meta = read_metadata(out_dir)
    seg_time = int(meta.get("segment_time", getattr(config, "SPOTIFY_SEG_TIME", int(config.SPOTIFY_HLS_OPTS.get("hls_time", )))))

    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{seg_time}"]

    # If no metadata at all -> expose only first-segment URL to trigger writer
    if not meta:
        lines.append("#EXT-X-MEDIA-SEQUENCE:0")
        lines.append(f"#EXTINF:{seg_time:.3f},")
        lines.append(f"/api/stream-spotify-segment/{out_dir.name}/segment_00000.ts")
        return "\n".join(lines)

    start_time_str = meta.get("start_time")
    total_segments = meta.get("total_segments", None)

    # If start_time absent -> only expose first segment url
    if not start_time_str:
        lines.append("#EXT-X-MEDIA-SEQUENCE:0")
        lines.append(f"#EXTINF:{seg_time:.3f},")
        lines.append(f"/api/stream-spotify-segment/{out_dir.name}/segment_00000.ts")
        return "\n".join(lines)

    # parse start_time and compute published_count
    st = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
    elapsed = (now - st).total_seconds()
    published_count = int(elapsed // seg_time) + 1 if elapsed >= 0 else 0
    if total_segments is not None:
        published_count = min(published_count, int(total_segments))

    # present files
    files = sorted(out_dir.glob("segment_*.ts"))
    present_indices = [int(p.name.replace("segment_", "").replace(".ts", "")) for p in files if p.exists() and p.stat().st_size > 0]

    if not present_indices:
        lines.append("#EXT-X-MEDIA-SEQUENCE:0")
        return "\n".join(lines)

    first_written = min(present_indices)
    last_written = max(present_indices)
    last_publish_idx = min(last_written, published_count - 1) if published_count > 0 else -1

    if last_publish_idx < first_written:
        lines.append(f"#EXT-X-MEDIA-SEQUENCE:{first_written}")
        return "\n".join(lines)

    max_back_segments = math.ceil(window_seconds / seg_time) if window_seconds and window_seconds > 0 else None
    if max_back_segments is None:
        start_idx = first_written
    else:
        start_idx = max(first_written, last_publish_idx - max_back_segments + 1)

    lines.append(f"#EXT-X-MEDIA-SEQUENCE:{start_idx}")

    for idx in range(start_idx, last_publish_idx + 1):
        seg_start = st + timedelta(seconds=idx * seg_time)
        lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{seg_start.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z")
        lines.append(f"#EXTINF:{seg_time:.3f},")
        lines.append(f"/api/stream-spotify-segment/{out_dir.name}/segment_{idx:05d}.ts")

    if total_segments is not None and published_count >= int(total_segments):
        lines.append("#EXT-X-ENDLIST")

    return "\n".join(lines)