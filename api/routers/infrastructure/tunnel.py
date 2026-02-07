import re
from api import config
from fastapi import APIRouter
from api import config

router = APIRouter()


@router.get("/tunnel")
def get_tunnel():
    try:
        with open(config.CLOUDFLARED_LOG, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        urls = re.findall(
            r"https://[a-z0-9\-]+\.trycloudflare\.com",
            text
        )

        return {"url": urls[-1] if urls else None}

    except FileNotFoundError:
        return {"error": "cloudflared.log not found"}
