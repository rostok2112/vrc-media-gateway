import asyncio
from http.client import HTTPException
import json
import math
from pathlib import Path
import shutil
import urllib.parse
from api.routers.spotify import utils  as spotify_utils
from api.segments_engine import registry
from api.websockets.clients_name import ClientName
from api.websockets.registry import ws_registry

from api import config, utils

SEG_TIME = int(config.SPOTIFY_HLS_OPTS.get("hls_time", 2))


def to_spotify_uri(url: str) -> str:
    if url.startswith("spotify:"):
        return url

    p = urllib.parse.urlparse(url)
    parts = p.path.split("/")

    if len(parts) >= 3 and parts[1] == "track":
        track_id = parts[2]
        return f"spotify:track:{track_id}"

    return url

async def get_duration_ms_via_ws(url: str) -> int:
    uri = to_spotify_uri(url)

    await ws_registry.rpc_call(
        ClientName.SPOTIFY,
        "load",
        {"uri": uri},
        timeout=5
    )

    await asyncio.sleep(0.5)

    resp = await ws_registry.rpc_call(
        ClientName.SPOTIFY,
        "metadata",
        {"uri": uri},
        timeout=5
    )

    if not resp:
        return 0

    return int(resp.get("duration_ms", 0))


def write_metadata_to_file(out_dir: Path, duration_ms: int, segment_time: int, segments_count: int, url: str):
    meta = {
    "duration_ms": int(duration_ms),
    "seg_time": int(spotify_utils.SEG_TIME),
    "total_segments": segments_count,
    "url": url,
    }
    try:
        (out_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    except Exception:
        pass


async def clear_spotify_cache(url: str) -> dict:
    if not url:
        raise HTTPException(400, "missing url")

    sid = utils.sid_for_url(url)
    out_dir = utils.out_dir_for_sid(sid)

    try:
        await registry.stop_stream(sid)
    except Exception:
        pass

    removed = False
    if out_dir.exists():
        try:
            shutil.rmtree(out_dir)
            removed = True
        except Exception:
            raise HTTPException(500, "cache remove failed")

    return {"ok": True, "sid": sid, "removed": removed}
