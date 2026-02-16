import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from fastapi import APIRouter, Query, Response, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from api import config

from api.segments_engine import registry


import logging

logger = logging.getLogger(__name__)


router = APIRouter()

# @router.get("/stream-spotify")
# async def stream_spotify(url: str):
#     sid = utils.sid_for_url(url)
#     out_dir = Path(utils.out_dir_for_sid(sid))
#     out_dir.mkdir(parents=True, exist_ok=True)

#     meta_path = out_dir / "metadata.json"
#     index_path = out_dir / "index.m3u8"

#     # 1) Ensure base metadata exists (no start_time)
#     meta = utils.read_metadata(out_dir)
#     if not meta:
#         # fetch duration if needed (your existing WS logic)
#         duration_ms = await spotify_utils.get_duration_ms_via_ws(url)
#         seg_time = int(getattr(config, "SPOTIFY_SEG_TIME", int(config.SPOTIFY_HLS_OPTS.get("hls_time", "6"))))
#         total_segments = int((duration_ms / 1000) // seg_time) + 1 if duration_ms else None
#         base_meta = {
#             "url": url,
#             "duration_ms": duration_ms,
#             "segment_time": seg_time,
#             "total_segments": total_segments
#         }
#         utils.atomic_write(meta_path, json.dumps(base_meta))
#         meta = base_meta

#     # 2) Best-effort: kick writer in background so first-seg generation starts fast.
#     try:
#         start_writer_background(sid, start_position_ms=0)
#     except Exception:
#         logger.exception("start_writer_background failed for %s", sid)

#     # 3) ALWAYS regenerate playlist on each request (or at least when metadata newer).
#     #    This prevents stale index.m3u8 being served indefinitely.
#     try:
#         m3u8 = utils.build_live_m3u8(out_dir, now_dt=datetime.now(timezone.utc), window_seconds=300)
#         utils.atomic_write(index_path, m3u8)
#         logger.debug("INDEX GENERATED sid=%s len=%d", sid, len(m3u8))
#     except Exception:
#         logger.exception("Failed to build/write m3u8 for sid=%s", sid)
#         # fallback: if index exists, serve it; otherwise error
#         if index_path.exists():
#             return Response(index_path.read_text(encoding="utf-8"), media_type="application/vnd.apple.mpegurl")
#         raise HTTPException(status_code=500, detail="failed to build playlist")

#     # 4) Prefer X-Accel-Redirect if nginx internal mapping configured (keeps nginx serving)
#     #    Otherwise return content directly.
#     try:
#         # if you rely on nginx internal location `/internal_streams/`, use header
#         return Response(status_code=200, headers={
#             "X-Accel-Redirect": f"/internal_streams/{sid}/index.m3u8",
#             "Content-Type": "application/vnd.apple.mpegurl"
#         })
#     except Exception:
#         # fallback direct body
#         return Response(m3u8, media_type="application/vnd.apple.mpegurl")

# @router.get("/stream-spotify-segment/{sid}/{filename}")
# async def stream_spotify_segment(sid: str, filename: str):
#     out_dir = utils.out_dir_for_sid(sid)
#     seg_path = out_dir / filename

#     try:
#         idx = int(filename.replace("segment_", "").replace(".ts", ""))
#     except Exception:
#         raise HTTPException(400, "bad filename")

#     # remove 0-byte placeholder
#     try:
#         if seg_path.exists() and seg_path.stat().st_size == 0:
#             try:
#                 seg_path.unlink(missing_ok=True)
#             except Exception:
#                 pass
#     except Exception:
#         pass

#     # ensure segment exists (this triggers writer start/seek/prefetch logic)
#     try:
#         await ensure_segment(sid, idx)
#     except Exception:
#         # if ensure_segment failed — try seek/regenerate as fallback
#         try:
#             w = await get_or_create_writer(sid, kind="spotify")
#             pos_ms = idx * w.segment_time * 1000
#             await w.request_seek(pos_ms)
#             await w.wait_for_segment(idx, timeout=5.0)
#         except Exception:
#             pass

#     if not seg_path.exists() or seg_path.stat().st_size == 0:
#         # still missing -> inform client to retry later
#         raise HTTPException(503, "segment not ready")

#     return Response(
#         status_code=200,
#         headers={
#             "X-Accel-Redirect": f"/internal_streams/{sid}/{filename}",
#             "Content-Type": "video/MP2T"
#         }
#     )
router.mount("/streams", StaticFiles(directory=config.STREAMS), name="streams")

def extract_spotify_id(url: str) -> str:

    # spotify:track:xxxxx
    if url.startswith("spotify:track:"):
        return url.split(":")[-1]

    parsed = urlparse(url)

    # https://open.spotify.com/track/xxxxx
    match = re.search(r"/track/([a-zA-Z0-9]+)", parsed.path)
    if match:
        return match.group(1)

    raise ValueError("Invalid Spotify track URL")
@router.get("/stream-spotify")
async def stream_spotify(url: str = Query(...)):
    try:
        track_id = extract_spotify_id(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid spotify url")

    sid = await registry.start_stream_for_track(track_id, start_position_ms=0)

    # wait readiness (playlist + first ts)
    ready = await registry.wait_ready(sid, timeout=8.0)
    if not ready:
        # можно пробовать дольше, но AVPro нельзя кормить 404
        raise HTTPException(status_code=503, detail="Stream not ready")

    # redirect to HLS playlist (use public host in prod)
    return RedirectResponse(url=f"/streams/{sid}/playlist.m3u8", status_code=302)

@router.get("/streams/{sid}/playlist.m3u8")
async def get_playlist(sid: str):
    # Ensure writer exists and start background generation if needed
    registry.start_writer_background(sid, start_position_ms=0)
    w = await registry.get_or_create_writer(sid)

    playlist = Path(w.out_dir) / "playlist.m3u8"
    # if playlist not yet present — return placeholder minimal playlist so AVPro can retry
    if not playlist.exists():
        # minimal placeholder (server side) with target duration
        txt = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:%d\n#EXT-X-ALLOW-CACHE:NO\n#EXT-X-MEDIA-SEQUENCE:0\n" % max(1, int(w.segment_time))
        return PlainTextResponse(content=txt, media_type="application/vnd.apple.mpegurl", status_code=202)

    return FileResponse(str(playlist), media_type="application/vnd.apple.mpegurl")

@router.get("/streams/{sid}/segment/{idx}.ts")
async def get_segment(sid: str, idx: int):
    w = await registry.get_or_create_writer(sid)
    seg = Path(w.out_dir) / f"segment_{idx:05d}.ts"
    if not seg.exists():
        # возьмём writer и попробуем дождаться его (маленький таймаут) — лучше, чем 404
        try:
            await w.wait_for_segment(idx, timeout=5.0)
        except Exception:
            pass
    if not seg.exists():
        raise HTTPException(status_code=404, detail="segment not ready")
    return FileResponse(str(seg), media_type="video/MP2T")

@router.post("/streams/{sid}/stop")
async def stop_stream(sid: str):
    await registry.stop_writer(sid)
    return {"ok": True}

@router.post("/streams/{sid}/seek")
async def seek_stream(sid: str, position_ms: int):
    w = await registry.get_or_create_writer(sid)
    await w.request_seek(position_ms)
    return {"ok": True}