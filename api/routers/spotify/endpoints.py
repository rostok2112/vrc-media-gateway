import math
from pathlib import Path
from fastapi import APIRouter, Query, Request, Response, HTTPException
from api import config, utils
from api.routers.spotify import utils  as spotify_utils
from api.segments_engine.gate import get_gate_for_sid
from api.segments_engine.registry import ensure_segment, set_total_segments
from api.segments_engine import utils as segments_engine_utils


router = APIRouter()

@router.get("/stream-spotify")
async def stream_spotify(url: str = Query(...)):
    sid = utils.sid_for_url(url)
    out_dir = utils.out_dir_for_sid(sid)
    phys = out_dir / "index.m3u8"

    if phys.exists():
        return Response(status_code=200, headers={
            "X-Accel-Redirect": f"/internal_streams/{sid}/index.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl"
        })

    duration_ms = await spotify_utils.get_duration_ms_via_ws(url)
    
    segments_count = segments_engine_utils.get_segment_count(
        duration_ms, spotify_utils.SEG_TIME
    )
    await set_total_segments(sid, segments_count)
    spotify_utils.write_metadata_to_file(out_dir, duration_ms, 
                                         spotify_utils.SEG_TIME, segments_count, url)
    
    
    if not duration_ms or duration_ms <= 0:
        raise HTTPException(
            status_code=503,
            detail="spotify metadata not available"
        )

    m3u8 = utils.build_virtual_m3u8(sid, duration_ms, spotify_utils.SEG_TIME)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.m3u8").write_text(m3u8, encoding="utf-8")

    return Response(m3u8, media_type="application/vnd.apple.mpegurl")


@router.get("/api/stream-spotify-segment/{sid}/segment_{idx:05d}.ts")
async def stream_spotify_segment(request: Request, sid: str, idx: int, seek: bool = Query(False)):
    """
    Gate-protected segment endpoint.
    seek=True можно выставлять в случае явной seek операции (ws-событие), тогда сегмент отдадим сразу.
    """
    gate = get_gate_for_sid(sid, spotify_utils.SEG_TIME)

    allowed, wait = await gate.try_allow(idx, is_seek=seek)
    if not allowed:
        # возвращаем 503 + Retry-After (в секундах, округление вверх)
        retry_after = str(math.ceil(wait)) if wait > 0 else "1"
        return Response(status_code=503, headers={"Retry-After": retry_after}, content=b"")

    # allowed == True -> убедиться, что сегмент есть (или генерируем его через существующую логику)
    seg_path = segments_engine_utils.segment_path_for(sid, idx)
    if not seg_path.exists():
        try:
            await segments_engine_utils.ensure_segment_exists(sid, idx)
        except Exception as e:
            # если генерация упала — откатим gate (можно логировать)
            return Response(status_code=503, headers={"Retry-After": "1"}, content=b"")

        # повторная проверка
        if not seg_path.exists():
            return Response(status_code=503, headers={"Retry-After": "1"}, content=b"")

    # файл доступен — читаем и возвращаем
    data = seg_path.read_bytes()
    return Response(content=data, media_type="video/MP2T")
