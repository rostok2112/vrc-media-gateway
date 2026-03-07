import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils


router = APIRouter()

GENERIC_VIDEO_LAYOUT_VERSION = "generic-video-v1"
_VIDEO_BUILD_JOBS: Dict[str, Dict[str, Any]] = {}
_VIDEO_BUILD_TASKS: Dict[str, asyncio.Task] = {}
_VIDEO_BUILD_LOCK = asyncio.Lock()


async def clear_video_build_jobs() -> None:
    async with _VIDEO_BUILD_LOCK:
        tasks = list(_VIDEO_BUILD_TASKS.values())
        _VIDEO_BUILD_TASKS.clear()
        _VIDEO_BUILD_JOBS.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _normalize_video_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="invalid video url")
    return url


def _normalize_referer(referer: Optional[str]) -> str:
    value = str(referer or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not value.lower().startswith(("http://", "https://")):
        return ""
    return value


def _video_sid(url: str, referer: str = "") -> str:
    return utils.sid_for_url(url, GENERIC_VIDEO_LAYOUT_VERSION, f"referer={referer}")


def _video_job_id(url: str, referer: str = "") -> str:
    return utils.sid_for_url(url, "video-build", GENERIC_VIDEO_LAYOUT_VERSION, f"referer={referer}")


def _stream_path_for_sid(sid: str) -> str:
    return f"/streams/{sid}/index.m3u8"


def _hls_response(sid: str) -> Response:
    return Response(status_code=200, headers={
        "X-Accel-Redirect": _stream_path_for_sid(sid),
        "Content-Type": "application/vnd.apple.mpegurl",
    })


def _is_hls_ready(sid: str) -> bool:
    return utils.is_hls_output_ready(config.STREAMS / sid)


def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    job = _VIDEO_BUILD_JOBS.get(job_id)
    if not job:
        return None

    snapshot = {k: v for k, v in job.items()}
    sid = snapshot.get("result_sid")
    snapshot["ready"] = snapshot.get("state") == "ready" and bool(sid)
    if sid:
        snapshot["stream_path"] = _stream_path_for_sid(sid)
    return snapshot


def _http_input_headers(referer: str) -> str:
    lines = ["User-Agent: Mozilla/5.0"]
    if referer:
        lines.append(f"Referer: {referer}")
    return "".join(f"{line}\r\n" for line in lines)


def _build_video_sync(url: str, referer: str, out_dir, sid: str) -> None:
    m3u8 = out_dir / "index.m3u8"
    hls = config.HLS_OPTS

    cmd = [
        config.FFMPEG, "-y",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-headers", _http_input_headers(referer),
        "-i", url,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-sn",
        "-dn",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
        "-c:v", "libx264",
        "-preset", "fast",
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        *utils.ffmpeg_audio_params(),
        "-f", "hls",
        "-hls_time", str(hls.get("hls_time", 4)),
        "-hls_list_size", str(hls.get("hls_list_size", 0)),
        "-hls_playlist_type", hls.get("hls_playlist_type", "vod"),
        "-hls_flags", hls.get("hls_flags", "independent_segments"),
        "-hls_base_url", f"/streams/{sid}/",
        str(m3u8)
    ]
    utils.run_cmd(cmd)


async def _ensure_video_stream(url: str, referer: str) -> str:
    url = _normalize_video_url(url)
    referer = _normalize_referer(referer)
    sid = _video_sid(url, referer)

    if _is_hls_ready(sid):
        return sid

    out_dir = config.STREAMS / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_build_video_sync, url, referer, out_dir, sid)

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="video HLS build failed")

    return sid


async def _run_video_build_job(job_id: str, url: str, referer: str) -> None:
    job = _VIDEO_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        result_sid = await _ensure_video_stream(url, referer)
        job["state"] = "ready"
        job["result_sid"] = result_sid
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
    finally:
        async with _VIDEO_BUILD_LOCK:
            _VIDEO_BUILD_TASKS.pop(job_id, None)


async def _ensure_video_build_job(url: str, referer: str) -> Dict[str, Any]:
    url = _normalize_video_url(url)
    referer = _normalize_referer(referer)
    sid = _video_sid(url, referer)
    job_id = _video_job_id(url, referer)

    async with _VIDEO_BUILD_LOCK:
        job = _VIDEO_BUILD_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "url": url,
                "referer": referer,
                "state": "pending",
                "result_sid": None,
                "error": None,
            },
        )

        if _is_hls_ready(sid):
            job["state"] = "ready"
            job["result_sid"] = sid
            job["error"] = None
            return _job_snapshot(job_id)

        task = _VIDEO_BUILD_TASKS.get(job_id)
        if task and not task.done():
            return _job_snapshot(job_id)

        job["state"] = "pending"
        job["result_sid"] = None
        job["error"] = None
        _VIDEO_BUILD_TASKS[job_id] = asyncio.create_task(_run_video_build_job(job_id, url, referer))
        return _job_snapshot(job_id)


@router.get("/stream-video-build-start")
async def stream_video_build_start(
    url: str = Query(...),
    referer: Optional[str] = Query(default=""),
):
    return await _ensure_video_build_job(url, referer or "")


@router.get("/stream-video-build-status")
async def stream_video_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="video build job not found")
    return snapshot


@router.get("/stream-video")
async def stream_video(
    url: str = Query(...),
    referer: Optional[str] = Query(default=""),
):
    sid = await _ensure_video_stream(url, referer or "")
    return _hls_response(sid)
