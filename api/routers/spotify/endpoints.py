from pathlib import Path
from fastapi import APIRouter, Query, Response, HTTPException
from api import config, utils
from api.routers.spotify import utils  as spotify_utils
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


@router.get("/stream-spotify-segment/{sid}/{filename}")
async def stream_spotify_segment(sid: str, filename: str):
    out_dir = utils.out_dir_for_sid(sid)
    seg_path = out_dir / filename

    # вычислим индекс
    try:
        idx = int(filename.replace("segment_", "").replace(".ts", ""))
    except Exception:
        raise HTTPException(400, "bad filename")

    # 1) если файл существует но битый (0 байт) — удаляем, чтобы перегенерить
    try:
        if seg_path.exists() and seg_path.stat().st_size == 0:
            try:
                seg_path.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        # ignore filesystem races
        pass

    # 2) если файла нет — попросим registry обеспечить его
    if not seg_path.exists():
        await ensure_segment(sid, idx)

    # 3) если после ensure сегмента всё ещё нет или он нулевой — форсим regen через seek
    try:
        if (not seg_path.exists()) or seg_path.stat().st_size == 0:
            # форсируем перегенерацию через writer.request_seek
            from api.segments_engine.registry import get_or_create_writer
            w = await get_or_create_writer(sid, kind="spotify")
            pos_ms = idx * w.segment_time * 1000
            # await request_seek, затем дождёмся сегмента
            await w.request_seek(pos_ms)
            await w.wait_for_segment(idx, timeout=5.0)
    except Exception:
        # не падаем клиенту по ошибке перегенерации, ниже отдадим 500 если нет файла
        pass

    if not seg_path.exists():
        raise HTTPException(500, "segment not created")

    # отдать (X-Accel internal)
    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": f"/internal_streams/{sid}/{filename}",
            "Content-Type": "video/MP2T"
        }
    )