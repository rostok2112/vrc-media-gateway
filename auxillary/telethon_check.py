import os
import sqlite3
from pathlib import Path
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from api import config


def detect_file_type(path: Path):
    data = path.read_bytes()

    print("\n===== SESSION FILE INFO =====")
    print("Path:", path)
    print("Size:", len(data))

    # show first bytes
    head = data[:32]
    print("First bytes:", head)

    # check sqlite header
    if data.startswith(b"SQLite format 3"):
        print("TYPE: SQLite session")
        return "sqlite"

    # check printable
    try:
        text = data.decode("utf-8").strip()
        print("First chars:", text[:20])

        if len(text) > 50 and " " not in text:
            print("TYPE: StringSession")
            return "string"

    except Exception:
        pass

    print("TYPE: Unknown / corrupted")
    return "unknown"


async def try_string_session(path: Path):
    print("\n===== TRY StringSession =====")

    try:
        session = path.read_text().strip()

        client = TelegramClient(
            StringSession(session),
            int(config.TG_API_ID),
            config.TG_API_HASH
        )

        await client.connect()

        print("Connected:", client.is_connected())
        print("Authorized:", await client.is_user_authorized())

        await client.disconnect()

    except Exception as e:
        print("StringSession failed:", e)


async def try_sqlite_session(path: Path):
    print("\n===== TRY SQLite session =====")

    try:
        client = TelegramClient(
            str(path),
            int(config.TG_API_ID),
            config.TG_API_HASH
        )

        await client.connect()

        print("Connected:", client.is_connected())
        print("Authorized:", await client.is_user_authorized())

        await client.disconnect()

    except Exception as e:
        print("SQLite session failed:", e)


async def main():
    path = Path(config.TG_SESSION)

    if not path.exists():
        print("Session file not found:", path)
        return

    t = detect_file_type(path)

    import asyncio

    if t == "string":
        await try_string_session(path)
        await try_sqlite_session(path)

    elif t == "sqlite":
        await try_sqlite_session(path)
        await try_string_session(path)

    else:
        await try_string_session(path)
        await try_sqlite_session(path)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())