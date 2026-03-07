import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils


router = APIRouter()

GENERIC_AUDIO_LAYOUT_VERSION = "generic-audio-v1"
_AUDIO_BUILD_JOBS: Dict[str, Dict[str, Any]] = {}
_AUDIO_BUILD_TASKS: Dict[str, asyncio.Task] = {}
_AUDIO_BUILD_LOCK = asyncio.Lock()


async def clear_audio_build_jobs() -> None:
    async with _AUDIO_BUILD_LOCK:
        tasks = list(_AUDIO_BUILD_TASKS.values())
        _AUDIO_BUILD_TASKS.clear()
        _AUDIO_BUILD_JOBS.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _normalize_audio_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="invalid audio url")
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


def _audio_sid(url: str, referer: str = "") -> str:
    return utils.sid_for_url(url, GENERIC_AUDIO_LAYOUT_VERSION, f"referer={referer}")


def _audio_job_id(url: str, referer: str = "") -> str:
    return utils.sid_for_url(url, "audio-build", GENERIC_AUDIO_LAYOUT_VERSION, f"referer={referer}")


def _stream_path_for_sid(sid: str) -> str:
    return f"/streams/{sid}/index.m3u8"


def _hls_response(sid: str) -> Response:
    return Response(status_code=200, headers={
        "X-Accel-Redirect": _stream_path_for_sid(sid),
        "Content-Type": "application/vnd.apple.mpegurl",
    })


def _is_hls_ready(sid: str) -> bool:
    out_dir = config.STREAMS / sid
    m3u8 = out_dir / "index.m3u8"
    if not m3u8.exists() or m3u8.stat().st_size <= 200:
        return False

    ts_files = list(out_dir.glob("*.ts"))
    return bool(ts_files and any(ts.stat().st_size > 1024 for ts in ts_files))


def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    job = _AUDIO_BUILD_JOBS.get(job_id)
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


def _build_audio_sync(url: str, referer: str, out_dir, sid: str) -> None:
    m3u8 = out_dir / "index.m3u8"
    hls = config.HLS_OPTS

    cmd = [
        config.FFMPEG, "-y",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-headers", _http_input_headers(referer),
        "-i", url,
        "-map", "0:a:0",
        "-vn",
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


async def _ensure_audio_stream(url: str, referer: str) -> str:
    url = _normalize_audio_url(url)
    referer = _normalize_referer(referer)
    sid = _audio_sid(url, referer)

    if _is_hls_ready(sid):
        return sid

    out_dir = config.STREAMS / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_build_audio_sync, url, referer, out_dir, sid)

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="audio HLS build failed")

    return sid


async def _run_audio_build_job(job_id: str, url: str, referer: str) -> None:
    job = _AUDIO_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        result_sid = await _ensure_audio_stream(url, referer)
        job["state"] = "ready"
        job["result_sid"] = result_sid
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
    finally:
        async with _AUDIO_BUILD_LOCK:
            _AUDIO_BUILD_TASKS.pop(job_id, None)


async def _ensure_audio_build_job(url: str, referer: str) -> Dict[str, Any]:
    url = _normalize_audio_url(url)
    referer = _normalize_referer(referer)
    sid = _audio_sid(url, referer)
    job_id = _audio_job_id(url, referer)

    async with _AUDIO_BUILD_LOCK:
        job = _AUDIO_BUILD_JOBS.setdefault(
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

        task = _AUDIO_BUILD_TASKS.get(job_id)
        if task and not task.done():
            return _job_snapshot(job_id)

        job["state"] = "pending"
        job["result_sid"] = None
        job["error"] = None
        _AUDIO_BUILD_TASKS[job_id] = asyncio.create_task(_run_audio_build_job(job_id, url, referer))
        return _job_snapshot(job_id)


@router.get("/stream-audio-build-start")
async def stream_audio_build_start(
    url: str = Query(...),
    referer: Optional[str] = Query(default=""),
):
    return await _ensure_audio_build_job(url, referer or "")


@router.get("/stream-audio-build-status")
async def stream_audio_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="audio build job not found")
    return snapshot


@router.get("/stream-audio")
async def stream_audio(
    url: str = Query(...),
    referer: Optional[str] = Query(default=""),
):
    sid = await _ensure_audio_stream(url, referer or "")
    return _hls_response(sid)
