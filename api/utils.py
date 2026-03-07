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
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple, Union
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import psutil
import requests
from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.types import Message as TelethonMessage, PeerChannel

from api import config

import logging

from api.segments_engine import registry

logger = logging.getLogger(__name__)

IMAGE_EXPORT_LAYOUT_VERSION = "fit-pad-v1"
VIDEO_HLS_LAYOUT_VERSION = "video-h264-v2"
GIF_EXPORT_LAYOUT_VERSION = "gif-motion-v1"
POST_TEXT_EXPORT_LAYOUT_VERSION = "post-text-v7"
DEFAULT_HLS_SEGMENT_TIME = int(config.HLS_OPTS.get("hls_time", 4))
TWEMOJI_BASE_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72"


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
    start = time.time()
    while time.time() - start < timeout:
        if is_hls_output_ready(out_dir):
            return True
        time.sleep(0.3)
    return False


def is_hls_output_ready(out_dir: Path) -> bool:
    m3u8 = out_dir / "index.m3u8"
    if not m3u8.exists() or m3u8.stat().st_size <= 0:
        return False

    try:
        playlist = m3u8.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    if "#EXTM3U" not in playlist or "#EXTINF:" not in playlist:
        return False

    segment_lines = [
        line.strip()
        for line in playlist.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not segment_lines:
        return False

    for line in segment_lines:
        seg_name = Path(urlparse(line).path).name
        if not seg_name:
            continue
        seg_path = out_dir / seg_name
        if seg_path.exists() and seg_path.stat().st_size > 0:
            return True

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


def is_probably_gif_source(source: Union[str, Path]) -> bool:
    if isinstance(source, Path):
        return source.suffix.lower() == ".gif"

    value = str(source or "").strip()
    if not value:
        return False

    if value.lower().startswith("data:image/gif"):
        return True

    parsed = urlparse(value)
    candidate = parsed.path if parsed.scheme else value
    return Path(candidate).suffix.lower() == ".gif"


def is_gif_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            header = fh.read(6)
    except Exception:
        return False

    return header in {b"GIF87a", b"GIF89a"}


def normalize_hls_segment_time(segment_time: Optional[int]) -> int:
    try:
        value = int(segment_time) if segment_time is not None else DEFAULT_HLS_SEGMENT_TIME
    except (TypeError, ValueError):
        value = DEFAULT_HLS_SEGMENT_TIME
    return max(1, value)


def hls_opts_with_segment_time(segment_time: Optional[int] = None) -> Dict[str, Any]:
    hls = dict(config.HLS_OPTS)
    hls["hls_time"] = str(normalize_hls_segment_time(segment_time))
    return hls


def hls_segment_cache_part(segment_time: Optional[int] = None) -> str:
    return f"segment_time={normalize_hls_segment_time(segment_time)}"


def normalize_overlay_payload(text: Any) -> Dict[str, Any]:
    if isinstance(text, dict):
        normalized_text = re.sub(r"\r\n?", "\n", str(text.get("text", "") or "")).strip()
        custom_emojis: List[Dict[str, Any]] = []
        for item in text.get("custom_emojis", []) or []:
            path = str(item.get("path", "") or "").strip()
            if not path:
                continue
            try:
                sequence_index = int(item.get("sequence_index"))
            except (TypeError, ValueError):
                continue
            custom_emojis.append({
                "sequence_index": sequence_index,
                "emoji_text": str(item.get("emoji_text", "") or ""),
                "document_id": int(item.get("document_id", 0) or 0),
                "mime_type": str(item.get("mime_type", "") or ""),
                "path": path,
                "animated": bool(item.get("animated")),
            })
        custom_emojis.sort(key=lambda row: row["sequence_index"])
        return {
            "text": normalized_text,
            "custom_emojis": custom_emojis,
        }
    return {
        "text": re.sub(r"\r\n?", "\n", str(text or "")).strip(),
        "custom_emojis": [],
    }


def overlay_custom_emojis(text: Any) -> List[Dict[str, Any]]:
    return normalize_overlay_payload(text)["custom_emojis"]


def text_cache_part(text: Any) -> str:
    payload = normalize_overlay_payload(text)
    normalized = json.dumps(
        {
            "text": payload["text"],
            "custom_emojis": [
                {
                    "sequence_index": item["sequence_index"],
                    "emoji_text": item["emoji_text"],
                    "document_id": item["document_id"],
                    "mime_type": item["mime_type"],
                    "animated": item["animated"],
                }
                for item in payload["custom_emojis"]
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"text={hashlib.md5(normalized.encode('utf-8')).hexdigest()}"


def even_int(value: int) -> int:
    value = int(value)
    return value if value % 2 == 0 else value + 1


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


def ffprobe_binary() -> str:
    ffmpeg_path = Path(config.FFMPEG)
    if ffmpeg_path.suffix.lower() == ".exe":
        sibling = ffmpeg_path.with_name("ffprobe.exe")
        if sibling.exists():
            return str(sibling)
        return "ffprobe.exe"
    sibling = ffmpeg_path.with_name("ffprobe")
    if sibling.exists():
        return str(sibling)
    return "ffprobe"


def probe_primary_video_codec(path: Path) -> str:
    try:
        probe = subprocess.run(
            [
                ffprobe_binary(),
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return ""

    if probe.returncode != 0:
        return ""

    return (probe.stdout or "").strip().splitlines()[0].strip().lower() if probe.stdout else ""


def probe_media_duration(path: Path) -> float:
    try:
        probe = subprocess.run(
            [
                ffprobe_binary(),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return 0.0

    if probe.returncode != 0:
        return 0.0

    try:
        return max(0.0, float((probe.stdout or "").strip() or 0.0))
    except (TypeError, ValueError):
        return 0.0


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
    # Preserve the source aspect ratio and letterbox/pillarbox into the target frame.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1"
    )
    cmd = [
        config.FFMPEG, "-y",
        "-loop", "1",
        "-i", str(image),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(duration),
        "-vf", vf,
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

def video_to_hls(
    video: Path,
    out_dir: Path,
    stream_id: str,
    segment_time: Optional[int] = None,
):
    m3u8 = out_dir / "index.m3u8"
    hls = hls_opts_with_segment_time(segment_time)
    video_codec = probe_primary_video_codec(video)
    video_args: List[str]

    if video_codec == "h264":
        video_args = [
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-sn",
            "-dn",
            "-c:v", "copy",
        ]
    else:
        # Re-encode non-H.264 sources so MPEG-TS HLS carries a broadly compatible video stream.
        video_args = [
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-sn",
            "-dn",
            "-vf", "scale=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1",
            "-c:v", "libx264",
            "-preset", "fast",
            "-profile:v", "high",
            "-level", "4.2",
            "-pix_fmt", "yuv420p",
        ]

    cmd = [
        config.FFMPEG, "-y",
        "-i", str(video),
        *video_args,
        *ffmpeg_audio_params(),
        "-f", "hls",
        "-hls_time", str(hls.get("hls_time", 4)),
        "-hls_list_size", str(hls.get("hls_list_size", 0)),
        "-hls_playlist_type", hls.get("hls_playlist_type", "vod"),
        "-hls_flags", hls.get("hls_flags", "independent_segments"),
        "-hls_base_url", f"/streams/{stream_id}/",
        str(m3u8)
    ]
    run_cmd(cmd)


def audio_to_hls(
    audio: Path,
    out_dir: Path,
    stream_id: str,
    segment_time: Optional[int] = None,
):
    m3u8 = out_dir / "index.m3u8"
    hls = hls_opts_with_segment_time(segment_time)

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


def normalize_overlay_text(text: Any) -> str:
    return normalize_overlay_payload(text)["text"]


def wrap_overlay_text(text: str, line_width: int = 52) -> str:
    normalized = normalize_overlay_text(text)
    if not normalized:
        return ""

    wrapper = textwrap.TextWrapper(
        width=max(12, line_width),
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=True,
        drop_whitespace=True,
    )

    lines: List[str] = []
    for paragraph in normalized.split("\n"):
        compact = re.sub(r"\s+", " ", paragraph).strip()
        if not compact:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.extend(wrapper.wrap(compact) or [""])

    if not lines:
        return ""

    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


def overlay_font_path() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = [
        windir / "Fonts" / "arial.ttf",
        windir / "Fonts" / "segoeui.ttf",
        windir / "Fonts" / "tahoma.ttf",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("No suitable Windows font file found for post-text overlay")


def ffmpeg_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def emoji_cache_dir() -> Path:
    cache_dir = Path(config.OUTPUT) / "_emoji_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def is_regional_indicator(ch: str) -> bool:
    cp = ord(ch)
    return 0x1F1E6 <= cp <= 0x1F1FF


def is_skin_tone_modifier(ch: str) -> bool:
    cp = ord(ch)
    return 0x1F3FB <= cp <= 0x1F3FF


def is_variation_selector(ch: str) -> bool:
    return ord(ch) in {0xFE0E, 0xFE0F}


def is_keycap_base(ch: str) -> bool:
    return ch.isdigit() or ch in {"#", "*"}


def is_emoji_base_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F000 <= cp <= 0x1FAFF or
        0x2600 <= cp <= 0x26FF or
        0x2700 <= cp <= 0x27BF or
        cp in {
            0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139,
            0x2194, 0x2195, 0x2196, 0x2197, 0x2198, 0x2199,
            0x21A9, 0x21AA, 0x231A, 0x231B, 0x2328, 0x23CF,
            0x23E9, 0x23EA, 0x23EB, 0x23EC, 0x23ED, 0x23EE,
            0x23EF, 0x23F0, 0x23F1, 0x23F2, 0x23F3, 0x24C2,
            0x25AA, 0x25AB, 0x25B6, 0x25C0, 0x25FB, 0x25FC,
            0x25FD, 0x25FE, 0x2934, 0x2935, 0x2B05, 0x2B06,
            0x2B07, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55, 0x3030,
            0x303D, 0x3297, 0x3299,
        }
    )


def consume_emoji_sequence(text: str, start: int) -> str:
    if start >= len(text):
        return ""

    first = text[start]
    if is_keycap_base(first):
        end = start + 1
        if end < len(text) and is_variation_selector(text[end]):
            end += 1
        if end < len(text) and ord(text[end]) == 0x20E3:
            return text[start:end + 1]
        return ""

    if is_regional_indicator(first):
        if start + 1 < len(text) and is_regional_indicator(text[start + 1]):
            return text[start:start + 2]
        return ""

    if not is_emoji_base_char(first):
        return ""

    end = start + 1
    while end < len(text):
        ch = text[end]
        if is_variation_selector(ch) or is_skin_tone_modifier(ch):
            end += 1
            continue
        if ord(ch) == 0x200D:
            end += 1
            if end < len(text):
                end += 1
            continue
        break
    return text[start:end]


def iter_text_and_emoji_fragments(text: str) -> List[Dict[str, str]]:
    fragments: List[Dict[str, str]] = []
    text_buf: List[str] = []
    idx = 0

    def flush_text() -> None:
        if text_buf:
            fragments.append({"kind": "text", "value": "".join(text_buf)})
            text_buf.clear()

    while idx < len(text):
        emoji_seq = consume_emoji_sequence(text, idx)
        if emoji_seq:
            flush_text()
            fragments.append({"kind": "emoji", "value": emoji_seq})
            idx += len(emoji_seq)
            continue
        text_buf.append(text[idx])
        idx += 1

    flush_text()
    return fragments


def twemoji_codepoint_name(value: str, include_variation_selectors: bool = True) -> str:
    codepoints = []
    for ch in value:
        cp = ord(ch)
        if not include_variation_selectors and cp == 0xFE0F:
            continue
        codepoints.append(f"{cp:x}")
    return "-".join(codepoints)


def fetch_twemoji_asset(emoji_text: str) -> Optional[Path]:
    cache_dir = emoji_cache_dir()
    primary = twemoji_codepoint_name(emoji_text, include_variation_selectors=True)
    fallback = twemoji_codepoint_name(emoji_text, include_variation_selectors=False)
    candidates = [primary]
    if fallback and fallback != primary:
        candidates.append(fallback)

    for candidate in candidates:
        target = cache_dir / f"{candidate}.png"
        if target.exists() and target.stat().st_size > 0:
            return target
        try:
            download_file(f"{TWEMOJI_BASE_URL}/{candidate}.png", target, max_bytes=256 * 1024)
            if target.exists() and target.stat().st_size > 0:
                return target
        except Exception:
            continue
    return None


def load_emoji_image(emoji_text: str, size: int) -> Optional[Image.Image]:
    asset_path = fetch_twemoji_asset(emoji_text)
    if not asset_path:
        return None
    try:
        with Image.open(asset_path) as im:
            return im.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def custom_emoji_preview_path(asset_path: Path) -> Path:
    return emoji_cache_dir() / f"{hashlib.md5(str(asset_path).encode('utf-8')).hexdigest()}-preview.png"


def ensure_custom_emoji_preview(asset_path: Path) -> Optional[Path]:
    if not asset_path.exists():
        return None

    if asset_path.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}:
        return asset_path

    preview = custom_emoji_preview_path(asset_path)
    if preview.exists() and preview.stat().st_size > 0:
        return preview

    try:
        run_cmd([
            config.FFMPEG,
            "-y",
            "-i", str(asset_path),
            "-frames:v", "1",
            str(preview),
        ], timeout=30)
    except Exception:
        return None

    return preview if preview.exists() and preview.stat().st_size > 0 else None


def load_custom_emoji_image(item: Dict[str, Any], size: int) -> Optional[Image.Image]:
    preview_path = ensure_custom_emoji_preview(Path(item["path"]))
    if not preview_path:
        return None
    try:
        with Image.open(preview_path) as im:
            return im.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def animated_custom_emoji_entries(text: Any) -> List[Dict[str, Any]]:
    return [item for item in overlay_custom_emojis(text) if item.get("animated") and item.get("path")]


def overlay_layout(width: int, height: int, text: str) -> Dict[str, Any]:
    normalized = normalize_overlay_text(text)
    if not normalized:
        raise HTTPException(400, "post text is empty")

    max_font_size = max(24, min(36, width // 34))
    min_font_size = 14
    min_media_height = max(120, height // 6)
    side_padding = max(24, min(60, width // 20))

    def build_layout(font_size: int) -> Dict[str, Any]:
        line_spacing = max(4, font_size // 4)
        line_width = max(18, int((width - (side_padding * 2)) / max(font_size * 0.58, 1)))
        wrapped = wrap_overlay_text(normalized, line_width=line_width)
        if not wrapped:
            raise HTTPException(400, "post text is empty")
        line_count = len(wrapped.split("\n"))
        caption_height = even_int(max(110, line_count * (font_size + line_spacing) + 40))
        media_height = height - caption_height
        return {
            "wrapped_text": wrapped,
            "font_size": font_size,
            "line_spacing": line_spacing,
            "media_height": media_height,
            "caption_height": caption_height,
            "side_padding": side_padding,
        }

    best_layout: Optional[Dict[str, Any]] = None
    for font_size in range(max_font_size, min_font_size - 1, -1):
        candidate = build_layout(font_size)
        best_layout = candidate
        if candidate["media_height"] >= min_media_height:
            return candidate

    if best_layout is None:
        raise HTTPException(400, "post text is empty")

    best_layout["media_height"] = even_int(max(80, best_layout["media_height"]))
    best_layout["caption_height"] = height - best_layout["media_height"]
    return best_layout


def media_with_text_filter_complex(
    width: int,
    height: int,
    text: str,
    media_input_index: int,
    caption_input_index: int,
) -> Tuple[Dict[str, Any], str]:
    layout = overlay_layout(width, height, text)
    filter_complex = (
        f"color=c=black:s={width}x{layout['media_height']}[media_bg];"
        f"[{media_input_index}:v]"
        f"scale={width}:{layout['media_height']}:force_original_aspect_ratio=decrease:flags=lanczos,"
        "setsar=1[scaled];"
        "[media_bg][scaled]overlay=(W-w)/2:0[media];"
        f"[{caption_input_index}:v]format=rgba[caption];"
        "[media][caption]vstack=inputs=2[stacked];"
        f"[stacked]pad={width}:{height}:0:0:color=black[final]"
    )
    return layout, filter_complex


def write_overlay_text_file(out_dir: Path, text: str) -> Path:
    layout = overlay_layout(1280, 720, text)
    text_file = out_dir / "post_text.txt"
    with text_file.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(layout["wrapped_text"])
    return text_file


def render_text_image(
    output_path: Path,
    layout: Dict[str, Any],
    width: int,
    height: int,
    text: Any = "",
    omit_animated_custom: bool = False,
) -> List[Dict[str, Any]]:
    image = Image.new("RGB", (width, height), color="black")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(overlay_font_path()), layout["font_size"])
    emoji_size = layout["font_size"] + max(4, layout["font_size"] // 8)
    line_step = max(layout["font_size"] + layout["line_spacing"], emoji_size + layout["line_spacing"])
    lines = layout["wrapped_text"].split("\n")
    total_text_height = len(lines) * line_step - layout["line_spacing"]
    start_y = max(0, (height - total_text_height) // 2)
    block_x = layout.get("side_padding", max(24, min(60, width // 20)))
    payload = normalize_overlay_payload(text if text else layout["wrapped_text"])
    custom_by_sequence = {item["sequence_index"]: item for item in payload["custom_emojis"]}
    emoji_sequence_index = 0
    overlays: List[Dict[str, Any]] = []

    for idx, line in enumerate(lines):
        if not line:
            continue
        y = start_y + idx * line_step
        cursor_x = block_x
        baseline_y = y
        for fragment in iter_text_and_emoji_fragments(line):
            value = fragment["value"]
            if fragment["kind"] == "emoji":
                custom_item = custom_by_sequence.get(emoji_sequence_index)
                emoji_sequence_index += 1
                paste_y = baseline_y + max(0, (line_step - emoji_size) // 2)

                if custom_item:
                    if custom_item.get("animated"):
                        overlays.append({
                            "x": int(cursor_x),
                            "y": int(paste_y),
                            "size": int(emoji_size),
                            "path": custom_item["path"],
                            "mime_type": custom_item.get("mime_type", ""),
                            "document_id": custom_item.get("document_id", 0),
                        })
                        if not omit_animated_custom:
                            custom_image = load_custom_emoji_image(custom_item, emoji_size)
                            if custom_image is not None:
                                image.paste(custom_image, (int(cursor_x), int(paste_y)), custom_image)
                            else:
                                emoji_image = load_emoji_image(value, emoji_size)
                                if emoji_image is not None:
                                    image.paste(emoji_image, (int(cursor_x), int(paste_y)), emoji_image)
                        cursor_x += emoji_size
                        continue

                    custom_image = load_custom_emoji_image(custom_item, emoji_size)
                    if custom_image is not None:
                        image.paste(custom_image, (int(cursor_x), int(paste_y)), custom_image)
                        cursor_x += emoji_size
                        continue

                emoji_image = load_emoji_image(value, emoji_size)
                if emoji_image is None:
                    bbox = draw.textbbox((0, 0), value, font=font)
                    draw.text((cursor_x - bbox[0], baseline_y - bbox[1]), value, font=font, fill="white")
                    cursor_x += bbox[2] - bbox[0]
                    continue

                image.paste(emoji_image, (int(cursor_x), int(paste_y)), emoji_image)
                cursor_x += emoji_size
                continue

            bbox = draw.textbbox((0, 0), value, font=font)
            draw.text((cursor_x - bbox[0], baseline_y - bbox[1]), value, font=font, fill="white")
            cursor_x += bbox[2] - bbox[0]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return overlays


def build_animated_text_panel_video(
    base_image: Path,
    overlays: List[Dict[str, Any]],
    output_path: Path,
    duration: float,
) -> None:
    cmd = [
        config.FFMPEG, "-y",
        "-loop", "1",
        "-t", f"{max(0.5, duration):.3f}",
        "-i", str(base_image),
    ]

    filter_parts = ["[0:v]format=rgba[base0]"]
    current_label = "base0"
    for idx, overlay in enumerate(overlays, start=1):
        overlay_path = Path(overlay["path"])
        overlay_mime = str(overlay.get("mime_type", "")).lower()
        cmd.extend(["-stream_loop", "-1"])
        if overlay_mime == "video/webm" or overlay_path.suffix.lower() == ".webm":
            # Telegram custom emoji webm assets need libvpx-vp9 to preserve alpha.
            cmd.extend(["-c:v", "libvpx-vp9"])
        cmd.extend(["-i", str(overlay_path)])
        in_label = f"emoji{idx}"
        out_label = f"base{idx}"
        filter_parts.append(
            f"[{idx}:v]fps=25,scale={int(overlay['size'])}:{int(overlay['size'])}:flags=lanczos,format=rgba[{in_label}]"
        )
        filter_parts.append(
            f"[{current_label}][{in_label}]overlay={int(overlay['x'])}:{int(overlay['y'])}:shortest=1[{out_label}]"
        )
        current_label = out_label

    cmd.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{current_label}]",
        "-an",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ])
    run_cmd(cmd)


def render_text_panel_source(out_dir: Path, text: Any, width: int, height: int) -> Tuple[Path, Dict[str, Any], bool]:
    out_dir.mkdir(parents=True, exist_ok=True)
    layout = overlay_layout(width, height, text)
    text_file = out_dir / "post_text.txt"
    caption_png = out_dir / "caption.png"
    caption_video = out_dir / "caption.mp4"

    with text_file.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(layout["wrapped_text"])

    overlays = render_text_image(
        caption_png,
        layout,
        width,
        layout["caption_height"],
        text=text,
        omit_animated_custom=True,
    )
    animated_overlays = [item for item in overlays if item.get("path")]
    if not animated_overlays:
        return caption_png, layout, False

    durations = [probe_media_duration(Path(item["path"])) for item in animated_overlays]
    panel_duration = max([1.0] + [value for value in durations if value > 0.0])
    build_animated_text_panel_video(caption_png, animated_overlays, caption_video, panel_duration)
    return caption_video, layout, True


def video_to_hls_with_text(
    video: Path,
    out_dir: Path,
    stream_id: str,
    text: Any,
    width: int = 1280,
    height: int = 720,
    segment_time: Optional[int] = None,
):
    m3u8 = out_dir / "index.m3u8"
    hls = hls_opts_with_segment_time(segment_time)
    out_dir.mkdir(parents=True, exist_ok=True)
    caption_source, layout, caption_animated = render_text_panel_source(out_dir, text, width, height)
    layout, filter_complex = media_with_text_filter_complex(width, height, text, 0, 1)

    cmd = [
        config.FFMPEG, "-y",
        "-i", str(video),
    ]
    if caption_animated:
        cmd.extend(["-stream_loop", "-1", "-i", str(caption_source)])
    else:
        cmd.extend(["-loop", "1", "-i", str(caption_source)])
    cmd.extend([
        "-sn",
        "-dn",
        "-filter_complex", filter_complex,
        "-map", "[final]",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "16",
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        *ffmpeg_audio_params(),
        "-shortest",
        "-f", "hls",
        "-hls_time", str(hls.get("hls_time", 4)),
        "-hls_list_size", str(hls.get("hls_list_size", 0)),
        "-hls_playlist_type", hls.get("hls_playlist_type", "vod"),
        "-hls_flags", hls.get("hls_flags", "independent_segments"),
        "-hls_base_url", f"/streams/{stream_id}/",
        str(m3u8)
    ])
    run_cmd(cmd)


def image_to_hls_with_text(
    image: Path,
    out_dir: Path,
    stream_id: str,
    text: Any,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
):
    m3u8 = out_dir / "index.m3u8"
    hls = hls_opts_with_segment_time(segment_time)
    out_dir.mkdir(parents=True, exist_ok=True)
    caption_source, layout, caption_animated = render_text_panel_source(out_dir, text, width, height)
    layout, filter_complex = media_with_text_filter_complex(width, height, text, 0, 2)

    cmd = [
        config.FFMPEG, "-y",
        "-loop", "1",
        "-i", str(image),
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
    ]
    if caption_animated:
        cmd.extend(["-stream_loop", "-1", "-i", str(caption_source)])
    else:
        cmd.extend(["-loop", "1", "-i", str(caption_source)])
    cmd.extend([
        "-t", str(duration),
        "-filter_complex", filter_complex,
        "-map", "[final]",
        "-map", "1:a:0",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "16",
        "-profile:v", "high",
        "-level", "4.2",
        *ffmpeg_audio_params(),
        "-shortest",
        "-f", "hls",
        "-hls_time", str(hls.get("hls_time", 4)),
        "-hls_list_size", str(hls.get("hls_list_size", 0)),
        "-hls_playlist_type", hls.get("hls_playlist_type", "vod"),
        "-hls_flags", hls.get("hls_flags", "independent_segments"),
        "-hls_base_url", f"/streams/{stream_id}/",
        str(m3u8)
    ])
    run_cmd(cmd)


def text_to_hls(
    out_dir: Path,
    stream_id: str,
    text: Any,
    duration: int = 300,
    width: int = 1280,
    height: int = 720,
    segment_time: Optional[int] = None,
):
    m3u8 = out_dir / "index.m3u8"
    hls = hls_opts_with_segment_time(segment_time)
    out_dir.mkdir(parents=True, exist_ok=True)
    layout = overlay_layout(width, height, text)
    text_file = out_dir / "post_text.txt"
    text_png = out_dir / "text_only.png"
    with text_file.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(layout["wrapped_text"])
    overlays = render_text_image(text_png, layout, width, height, text=text, omit_animated_custom=True)
    animated_overlays = [item for item in overlays if item.get("path")]

    cmd = [config.FFMPEG, "-y"]
    if animated_overlays:
        text_video = out_dir / "text_only.mp4"
        durations = [probe_media_duration(Path(item["path"])) for item in animated_overlays]
        panel_duration = max([1.0] + [value for value in durations if value > 0.0])
        build_animated_text_panel_video(text_png, animated_overlays, text_video, panel_duration)
        cmd.extend(["-stream_loop", "-1", "-i", str(text_video)])
    else:
        cmd.extend(["-loop", "1", "-i", str(text_png)])

    cmd.extend([
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(duration),
        "-shortest",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "16",
        "-profile:v", "high",
        "-level", "4.2",
        *ffmpeg_audio_params(),
        "-f", "hls",
        "-hls_time", str(hls.get("hls_time", 4)),
        "-hls_list_size", str(hls.get("hls_list_size", 0)),
        "-hls_playlist_type", hls.get("hls_playlist_type", "vod"),
        "-hls_flags", hls.get("hls_flags", "independent_segments"),
        "-hls_base_url", f"/streams/{stream_id}/",
        str(m3u8),
    ])
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
    segment_time: Optional[int] = None,
):
    out_dir = config.STREAMS / stream_id
    m3u8 = out_dir / "index.m3u8"

    if m3u8.exists():
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    src_suffix = ".gif" if is_probably_gif_source(image_url) else ".jpg"
    download_limit = 256 * 1024 * 1024 if src_suffix == ".gif" else 8 * 1024 * 1024
    img = out_dir / f"src{src_suffix}"
    mp4 = out_dir / "video.mp4"

    if Path(image_url).exists():
        copyfile(image_url, img)
    else:
        download_file(image_url, img, max_bytes=download_limit)

    if is_gif_file(img):
        video_to_hls(img, out_dir, stream_id, segment_time=segment_time)
    else:
        image_to_mp4(img, mp4, duration, width, height)
        video_to_hls(mp4, out_dir, stream_id, segment_time=segment_time)

        try:
            mp4.unlink()
        except Exception:
            pass

    if not wait_hls_ready(out_dir):
        raise HTTPException(500, "HLS build timeout")


def build_hls_from_image_with_text(
    image_url: str,
    stream_id: str,
    text: str,
    duration: int = 300,
    width: int = 1280,
    height: int = 720,
    segment_time: Optional[int] = None,
):
    out_dir = config.STREAMS / stream_id

    if is_hls_output_ready(out_dir):
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    src_suffix = ".gif" if is_probably_gif_source(image_url) else ".jpg"
    download_limit = 256 * 1024 * 1024 if src_suffix == ".gif" else 8 * 1024 * 1024
    img = out_dir / f"src{src_suffix}"

    if Path(image_url).exists():
        copyfile(image_url, img)
    else:
        download_file(image_url, img, max_bytes=download_limit)

    if is_gif_file(img):
        video_to_hls_with_text(img, out_dir, stream_id, text, width, height, segment_time=segment_time)
    else:
        image_to_hls_with_text(img, out_dir, stream_id, text, duration, width, height, segment_time=segment_time)

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


def image_stream_sid(
    url: str,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    if is_probably_gif_source(url):
        return sid_for_url(url, GIF_EXPORT_LAYOUT_VERSION, hls_segment_cache_part(segment_time))
    return sid_for_url(
        url,
        IMAGE_EXPORT_LAYOUT_VERSION,
        hls_segment_cache_part(segment_time),
        f"{duration}{width}x{height}",
    )


def image_build_job_id(
    url: str,
    duration: int,
    width: int,
    height: int,
    scope: str = "img-build",
    segment_time: Optional[int] = None,
) -> str:
    if is_probably_gif_source(url):
        return sid_for_url(url, scope, GIF_EXPORT_LAYOUT_VERSION, hls_segment_cache_part(segment_time))
    return sid_for_url(
        url,
        scope,
        IMAGE_EXPORT_LAYOUT_VERSION,
        hls_segment_cache_part(segment_time),
        f"{duration}{width}x{height}",
    )


def audio_stream_sid(url: str, *extra_parts, segment_time: Optional[int] = None) -> str:
    return sid_for_url(url, hls_segment_cache_part(segment_time), *extra_parts)


def audio_build_job_id(url: str, scope: str = "audio-build", *extra_parts, segment_time: Optional[int] = None) -> str:
    return sid_for_url(url, scope, hls_segment_cache_part(segment_time), *extra_parts)


def video_stream_sid(url: str, *extra_parts, segment_time: Optional[int] = None) -> str:
    return sid_for_url(url, VIDEO_HLS_LAYOUT_VERSION, hls_segment_cache_part(segment_time), *extra_parts)


def video_build_job_id(url: str, scope: str = "video-build", *extra_parts, segment_time: Optional[int] = None) -> str:
    return sid_for_url(url, scope, VIDEO_HLS_LAYOUT_VERSION, hls_segment_cache_part(segment_time), *extra_parts)

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
    seg_time = int(
        meta.get(
            "segment_time",
            meta.get("seg_time", getattr(config, "SPOTIFY_SEG_TIME", int(config.SPOTIFY_HLS_OPTS.get("hls_time")))),
        )
    )

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
