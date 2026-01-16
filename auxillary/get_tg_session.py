#!/usr/bin/env python3
import os
import sys
import asyncio
import subprocess
from pathlib import Path
import inspect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import config

from telethon import TelegramClient, functions, errors
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
import qrcode

def open_file_with_default_viewer(path: Path):
    """Open file with OS default app (best-effort). Only opens the PNG, not tg:// links."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass

async def run():
    client = TelegramClient(StringSession(), config.TG_API_ID, config.TG_API_HASH)
    await client.connect()

    try:
        qr = await client.qr_login()
        print("Open this PNG or scan QR with your phone (Settings → Devices → Scan QR).")
        print("URL (for reference only):", qr.url)

        # render QR to PNG and open it with the system image viewer
        out_img = Path("tg_qr.png")
        img = qrcode.make(qr.url)
        img.save(out_img)
        print("Saved:", out_img.resolve())
        open_file_with_default_viewer(out_img)

        try:
            # wait for QR to be scanned and approved (if account has no 2FA this returns)
            auth = await qr.wait()
        except SessionPasswordNeededError:
            # 2FA required — use SRP flow (GetPassword + CheckPassword)
            pw = config.TG_PASSWORD or os.getenv("TG_PASSWORD")
            if not pw:
                await client.disconnect()
                raise RuntimeError(
                    "Two-step verification enabled. Set TG_PASSWORD in api.config or as env var TG_PASSWORD."
                )

            try:
                pwd = await client(functions.account.GetPasswordRequest())

                # locate compute_check helper (different Telethon versions put it in different places)
                compute_check = None
                try:
                    from telethon.password import compute_check  # type: ignore
                except Exception:
                    try:
                        import telethon.password as pwd_mod  # type: ignore
                        compute_check = getattr(pwd_mod, "compute_check", None)
                    except Exception:
                        compute_check = None
                else:
                    compute_check = compute_check  # already bound

                if compute_check is None:
                    await client.disconnect()
                    raise RuntimeError(
                        "Unable to find Telethon's compute_check (telethon.password). Please upgrade Telethon: pip install -U telethon"
                    )

                srp = compute_check(pwd, pw)  # build SRP answer object expected by CheckPasswordRequest
                result = await client(functions.auth.CheckPasswordRequest(password=srp))

                # _on_login may be either a coroutine or a normal function depending on Telethon version
                on_login = getattr(client, "_on_login", None)
                if callable(on_login):
                    maybe = on_login(result.user)
                    if inspect.iscoroutine(maybe):
                        await maybe

                print("2FA handled successfully via SRP.")
                auth = result
            except errors.PasswordHashInvalidError:
                await client.disconnect()
                raise RuntimeError("Provided 2FA password is incorrect (PasswordHashInvalidError).")
            except Exception as e:
                await client.disconnect()
                raise RuntimeError(f"QR login failed while handling 2FA: {e}") from e
        except Exception as e:
            await client.disconnect()
            raise RuntimeError(f"QR login failed: {e}") from e

        sess = client.session.save()
        out_path = Path(config.TG_SESSION)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(sess, encoding="utf-8")
        print("Saved session to:", out_path.resolve())

    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as e:
        import traceback
        traceback.print_exception(type(e), e, e.__traceback__)
        sys.exit(1)
