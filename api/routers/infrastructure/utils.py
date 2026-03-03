import re
from pathlib import Path

from api import config


def get_latest_tunnel_url() -> str | None:
    try:
        log_path = Path(config.CLOUDFLARED_LOG)

        if not log_path.exists():
            return None

        text = log_path.read_text(encoding="utf-8", errors="ignore")

        urls = re.findall(
            r"https://[a-z0-9\-]+\.trycloudflare\.com",
            text
        )

        return urls[-1] if urls else None

    except Exception:
        return None