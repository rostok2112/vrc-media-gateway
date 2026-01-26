import asyncio
import hashlib
import time
import re
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import requests
from fastapi import HTTPException
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.types import Message

from api import config


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

_TG_RE = re.compile(r"https?://t\.me/([^/]+)/(\d+)")
_tg_client: Optional[TelegramClient] = None


def _parse_tg_post(url: str) -> Tuple[str, int]:
    m = _TG_RE.match(url)
    if not m:
        raise ValueError("Invalid Telegram post URL")
    return m.group(1), int(m.group(2))


def ensure_file(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("file does not exist or empty")


async def _create_client_from_string(session_str: str) -> TelegramClient:
    """
    Try to create a TelegramClient using a StringSession (session string).
    Raises exceptions from Telethon if something goes wrong.
    """
    client = TelegramClient(StringSession(session_str), int(config.TG_API_ID), config.TG_API_HASH)
    await client.connect()
    # verify authorization
    try:
        authorized = await client.is_user_authorized()
    except Exception:
        # In some Telethon versions is_user_authorized may raise; treat as not authorized
        authorized = False

    if not authorized:
        await client.disconnect()
        raise RuntimeError("Loaded StringSession is not authorized")
    return client


async def _create_client_from_file(session_path: Path) -> TelegramClient:
    """
    Fallback: treat session_path as a Telethon session filename (sqlite or .session)
    """
    client = TelegramClient(str(session_path), int(config.TG_API_ID), config.TG_API_HASH)
    await client.connect()
    try:
        authorized = await client.is_user_authorized()
    except Exception:
        authorized = False

    if not authorized:
        await client.disconnect()
        raise RuntimeError("Session file is not authorized")
    return client


async def get_tg_client() -> TelegramClient:
    """
    Return a connected, authorized TelegramClient reusing a global instance.
    Will try:
      1) If config.TG_SESSION is a path to an existing file that contains a session string -> use StringSession
      2) If step 1 fails, try using the config.TG_SESSION value as a session filename (Telethon will create/open it)
    Raises RuntimeError if credentials or session are misconfigured.
    """
    global _tg_client
    if not (config.TG_API_ID and config.TG_API_HASH and config.TG_SESSION):
        raise RuntimeError("Telegram API credentials are not configured")

    if _tg_client is not None and getattr(_tg_client, "is_connected", lambda: False)():
        # already connected
        return _tg_client

    session_cfg = Path(config.TG_SESSION)
    last_exc: Optional[Exception] = None

    # First try: if file exists and contains a non-empty string, try as StringSession
    if session_cfg.exists() and session_cfg.is_file():
        try:
            session_text = session_cfg.read_text(encoding="utf-8").strip()
            if session_text:
                _tg_client = await _create_client_from_string(session_text)
                return _tg_client
        except Exception as e:
            last_exc = e
            # fall through to try as session filename

    # Second try: treat config.TG_SESSION as a session filename / path (Telethon session file)
    try:
        # If config.TG_SESSION is relative or string path, pass it directly
        _tg_client = await _create_client_from_file(session_cfg)
        return _tg_client
    except Exception as e:
        last_exc = e

    # Third try: maybe config.TG_SESSION contains session string but file didn't exist (user kept the string in config)
    try:
        session_text = str(config.TG_SESSION).strip()
        if session_text and "\n" not in session_text and len(session_text) > 50:
            _tg_client = await _create_client_from_string(session_text)
            return _tg_client
    except Exception as e:
        last_exc = e

    # nothing worked
    if last_exc:
        raise RuntimeError(f"Failed to create Telegram client: {last_exc}") from last_exc
    raise RuntimeError("Failed to create Telegram client for unknown reason")


async def download_tg_video(url: str) -> Path:
    """
    Download a video from a Telegram post URL and return the local Path to the mp4 file.
    """
    channel, msg_id = _parse_tg_post(url)
    client = await get_tg_client()
    msg: Message = await client.get_messages(channel, ids=msg_id)

    if not msg or not getattr(msg, "file", None):
        raise RuntimeError("No media in Telegram post")

    mime = getattr(msg.file, "mime_type", None)
    if not mime or not mime.startswith("video/"):
        raise RuntimeError("Media is not a video")

    target = Path(config.OUTPUT) / f"{channel}_{msg_id}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)

    # Telethon can download directly to the file path
    await client.download_media(msg, file=str(target))
    ensure_file(target)

    return target


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
