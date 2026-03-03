
import re

from pathlib import Path
from typing import Optional, Tuple
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message
from telethon.tl.types import PeerChannel

from api import config, utils


_TG_RE = re.compile(r"https?://t\.me/([^/]+)/(\d+)")
_tg_client: Optional[TelegramClient] = None


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
    utils.ensure_file(target)

    return target


def _parse_tg_post(url: str) -> Tuple[str, int]:
    m = _TG_RE.match(url)
    if not m:
        raise ValueError("Invalid Telegram post URL")
    return m.group(1), int(m.group(2))

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
