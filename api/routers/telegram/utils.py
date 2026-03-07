import re
import mimetypes

from pathlib import Path
from typing import Optional, Tuple, Union
from urllib.parse import urlparse
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.types import DocumentAttributeAnimated, DocumentAttributeVideo, PeerChannel

from api import config, utils


_TG_RE = re.compile(r"https?://t\.me/([^/]+)/(\d+)")
_TG_VIDEO_HTML_RE = re.compile(
    r"(?:tgme_widget_message_video_player|js-message_video_player)",
    flags=re.I,
)
_tg_client: Optional[TelegramClient] = None


async def _create_client_from_string(session_str: str) -> TelegramClient:
    client = TelegramClient(StringSession(session_str), int(config.TG_API_ID), config.TG_API_HASH)
    await client.connect()
    try:
        authorized = await client.is_user_authorized()
    except Exception:
        authorized = False
    if not authorized:
        await client.disconnect()
        raise RuntimeError("Loaded StringSession is not authorized")
    return client


async def _create_client_from_file(session_path: Path) -> TelegramClient:
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
    global _tg_client

    if _tg_client and _tg_client.is_connected():
        return _tg_client

    if not (config.TG_API_ID and config.TG_API_HASH and config.TG_SESSION):
        raise RuntimeError("Telegram API credentials are not configured")

    session_path = Path(config.TG_SESSION)

    # якщо файл існує → читаємо StringSession
    if session_path.exists():
        session_text = session_path.read_text(encoding="utf-8").strip()
    else:
        session_text = str(config.TG_SESSION).strip()

    _tg_client = TelegramClient(
        StringSession(session_text),
        int(config.TG_API_ID),
        config.TG_API_HASH
    )

    await _tg_client.connect()

    if not await _tg_client.is_user_authorized():
        raise RuntimeError("Telegram session is not authorized")

    return _tg_client


async def get_tg_message(url: str) -> Message:
    channel_entity, msg_id = _parse_tg_post(url)
    client = await get_tg_client()
    msg: Message = await client.get_messages(channel_entity, ids=msg_id)

    if not msg:
        raise RuntimeError("Telegram post not found")

    return msg


def classify_tg_message(msg: Optional[Message]) -> Optional[str]:
    if not msg:
        return None

    if getattr(msg, "photo", None):
        return "photo"

    if getattr(msg, "video", None):
        return "video"

    file = getattr(msg, "file", None)
    mime = getattr(file, "mime_type", None) or ""
    if mime == "image/gif":
        return "video"
    if mime.startswith("video/"):
        return "video"

    document = getattr(msg, "document", None)
    attributes = getattr(document, "attributes", []) or []
    if any(isinstance(attr, DocumentAttributeAnimated) for attr in attributes):
        return "video"
    if any(isinstance(attr, DocumentAttributeVideo) for attr in attributes):
        return "video"

    return None


async def get_tg_post_media_kind(url: str) -> Optional[str]:
    msg = await get_tg_message(url)
    return classify_tg_message(msg)


async def get_tg_post_text(url: str) -> str:
    msg = await get_tg_message(url)
    text = getattr(msg, "raw_text", None) or getattr(msg, "message", None) or ""
    return str(text).strip()


def html_contains_tg_video(html: str) -> bool:
    return bool(_TG_VIDEO_HTML_RE.search(html))


async def download_tg_video(url: str) -> Path:
    """
    Download a video from a Telegram post URL and return the local Path to the mp4 file.
    Supports both:
      - https://t.me/username/<msg_id>
      - https://t.me/c/<channel_id>/<msg_id>
    """
    channel_entity, msg_id = _parse_tg_post(url)
    client = await get_tg_client()
    msg = await get_tg_message(url)

    if not getattr(msg, "file", None):
        raise RuntimeError("No media in Telegram post")

    if classify_tg_message(msg) != "video":
        raise RuntimeError("Media is not a video")

    suffix = ".mp4"
    file_name = getattr(getattr(msg, "file", None), "name", "") or ""
    if file_name:
        guessed_suffix = Path(file_name).suffix.lower()
        if guessed_suffix:
            suffix = guessed_suffix
    else:
        mime = getattr(getattr(msg, "file", None), "mime_type", None) or ""
        guessed_suffix = mimetypes.guess_extension(mime.split(";")[0].strip()) if mime else ""
        if guessed_suffix:
            suffix = guessed_suffix

    target = Path(config.OUTPUT) / f"{getattr(channel_entity, 'channel_id', str(channel_entity))}_{msg_id}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)

    expected_size = getattr(getattr(msg, "file", None), "size", None)
    if target.exists() and target.stat().st_size > 0:
        if expected_size is None or target.stat().st_size == expected_size:
            return target

    await client.download_media(msg, file=str(target))
    utils.ensure_file(target)

    return target


async def download_tg_photo(url: str) -> Path:
    _, msg_id = _parse_tg_post(url)
    client = await get_tg_client()
    msg = await get_tg_message(url)

    if not msg or not msg.photo:
        raise RuntimeError("No photo in telegram post")

    out = Path(config.OUTPUT) / f"tg_{msg_id}.jpg"

    out.parent.mkdir(parents=True, exist_ok=True)

    await client.download_media(msg.photo, file=str(out))

    return out


def _parse_tg_post(url: str) -> Tuple[Union[str, PeerChannel], int]:
    """
    Robust parser for Telegram post URLs.
    Returns either (username, message_id) or (PeerChannel(channel_id), message_id)
    Accepts:
      - https://t.me/username/8720
      - https://t.me/c/2242455380/8720
      - (also falls back to old regex)
    """
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]
    # /c/<channel_id>/<message_id>
    if len(path_parts) >= 3 and path_parts[0] == "c":
        try:
            channel_id = int(path_parts[1])
            msg_id = int(path_parts[2])
        except ValueError:
            raise ValueError("Invalid numeric parts in Telegram /c/ URL")
        return PeerChannel(channel_id), msg_id

    # /<username>/<message_id>
    if len(path_parts) >= 2:
        try:
            msg_id = int(path_parts[1])
        except ValueError:
            raise ValueError("Invalid message id in Telegram URL")
        return path_parts[0], msg_id

    # fallback to older regex (handles t.me/username/123)
    m = _TG_RE.match(url)
    if m:
        return m.group(1), int(m.group(2))

    raise ValueError("Invalid Telegram post URL")


def parse_internal_channel_id(value: str) -> int:
    if "#-100" in value:
        value = value.split("#-100", 1)[1]

    if value.startswith("-100"):
        value = value[4:]

    if not value.isdigit():
        raise ValueError("invalid internal channel id")

    return int(value)


async def resolve_public_tg_link(value: str) -> Optional[str]:
    channel_id = parse_internal_channel_id(value)
    client = await get_tg_client()

    entity = await client.get_entity(PeerChannel(channel_id))
    username = getattr(entity, "username", None)
    if not username:
        return None
    return f"https://t.me/{username}"
