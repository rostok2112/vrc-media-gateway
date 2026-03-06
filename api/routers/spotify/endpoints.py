import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from fastapi import APIRouter, Query, Request, Response, HTTPException
from fastapi.responses import RedirectResponse
from api import config, utils
from api.routers.spotify import utils  as spotify_utils

from api.segments_engine import registry


import logging

logger = logging.getLogger(__name__)


router = APIRouter()

@router.get("/stream-spotify")
async def stream_spotify(
    url: str,
    request: Request,
    segment_time: int | None = Query(default=None, ge=1),
    prefetch: int | None = Query(default=None, ge=1),
):
    segment_time, prefetch = spotify_utils.resolve_stream_options(segment_time, prefetch)
    sid = spotify_utils.spotify_stream_sid(url, segment_time=segment_time, prefetch=prefetch)

    out_dir = utils.out_dir_for_sid(sid)
    playlist = out_dir / "playlist.m3u8"
    if playlist.exists():
        return RedirectResponse(url=f"/api/stream-spotify-playlist/{sid}", status_code=302)
    meta_path = out_dir / "metadata.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not meta_path.exists():
        try:
            duration_ms = await spotify_utils.get_duration_ms_via_ws(url)
        except Exception:
            raise HTTPException(400, "spotify load failed")

        total_segments = math.ceil(duration_ms / (segment_time * 1000))
        meta = {
            "url": url,
            "segment_time": segment_time,
            "prefetch": prefetch,
            "total_segments": total_segments,
            "start_time": None,
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # PREFETCH LOCK: read timeout from config; if <=0 then wait indefinitely
    prefetch_timeout_cfg = config.SPOTIFY_HLS_OPTS.get("prefetch_timeout", None)
    if prefetch_timeout_cfg is None:
        prefetch_timeout = None
    else:
        try:
            prefetch_timeout = float(prefetch_timeout_cfg)
        except Exception:
            prefetch_timeout = None

    # if timeout <= 0 -> wait indefinitely, else wait up to timeout seconds
    if prefetch_timeout is not None and prefetch_timeout <= 0:
        prefetch_timeout = None

    try:
        prefetch_ok = await registry.start_prefetch(sid, timeout=prefetch_timeout)
        if not prefetch_ok:
            # prefetch timed out (if timeout provided) — still continue with redirect
            logger.info("prefetch timed out (cfg=%s) for %s — continuing", prefetch_timeout_cfg, sid)
    except Exception:
        logger.exception("start_prefetch failed — continuing")

    return RedirectResponse(url=f"/api/stream-spotify-playlist/{sid}", status_code=302)


@router.post("/stream-spotify-clear")
async def clear_spotify_cache(
    url: str,
    segment_time: int | None = Query(default=None, ge=1),
    prefetch: int | None = Query(default=None, ge=1),
):
    return await spotify_utils.clear_spotify_cache(url, segment_time=segment_time, prefetch=prefetch)


@router.get("/stream-spotify-playlist/{sid}")
async def playlist(sid: str):
    playlist = utils.out_dir_for_sid(sid) / "playlist.m3u8"
    if not playlist.exists():
        raise HTTPException(503, "playlist not ready")

    # Ask nginx to serve from /streams/ (your nginx uses alias -> html/streams)
    return Response(
        content="",
        headers={
            "X-Accel-Redirect": f"/streams/{sid}/playlist.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl"
        },
        status_code=200
    )


@router.get("/stream-spotify-segment/{sid}/{filename}")
async def segment(sid: str, filename: str):
    # validate
    if not filename.startswith("segment_") or not filename.endswith(".ts"):
        raise HTTPException(400, "bad filename")
    idx = int(filename.replace("segment_", "").replace(".ts", ""))

    # Block until this segment exists — this is the real prefetch gating
    try:
        await registry.ensure_segment(sid, idx)
    except Exception:
        raise HTTPException(503, "segment generation failed")

    # serve via nginx internal
    return Response(
        content="",
        headers={
            "X-Accel-Redirect": f"/streams/{sid}/{filename}",
            "Content-Type": "video/MP2T"
        },
        status_code=200
    )
