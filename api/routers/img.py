import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils


router = APIRouter()

_IMG_BUILD_JOBS: Dict[str, Dict[str, Any]] = {}
_IMG_BUILD_TASKS: Dict[str, asyncio.Task] = {}
_IMG_BUILD_LOCK = asyncio.Lock()


async def clear_img_build_jobs() -> None:
    async with _IMG_BUILD_LOCK:
        tasks = list(_IMG_BUILD_TASKS.values())
        _IMG_BUILD_TASKS.clear()
        _IMG_BUILD_JOBS.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _normalize_image_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="invalid url")
    return url


def _stream_path_for_sid(sid: str) -> str:
    return f"/streams/{sid}/index.m3u8"


def _hls_response(sid: str) -> Response:
    return Response(status_code=200, headers={
        "X-Accel-Redirect": _stream_path_for_sid(sid),
        "Content-Type": "application/vnd.apple.mpegurl",
    })


def _is_hls_ready(sid: str) -> bool:
    return utils.is_hls_output_ready(config.STREAMS / sid)


def _img_job_id(url: str, duration: int, width: int, height: int) -> str:
    return utils.image_build_job_id(url, duration, width, height, scope="img-build")


def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    job = _IMG_BUILD_JOBS.get(job_id)
    if not job:
        return None

    snapshot = {k: v for k, v in job.items()}
    sid = snapshot.get("result_sid")
    snapshot["ready"] = snapshot.get("state") == "ready" and bool(sid)
    if sid:
        snapshot["stream_path"] = _stream_path_for_sid(sid)
    return snapshot


async def _ensure_image_stream(url: str, duration: int, width: int, height: int) -> str:
    url = _normalize_image_url(url)
    sid = utils.image_stream_sid(url, duration, width, height)

    if _is_hls_ready(sid):
        return sid

    out_dir = config.STREAMS / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(utils.build_hls_from_image, url, sid, duration, width, height)

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="image HLS build failed")

    return sid


async def _run_img_build_job(job_id: str, url: str, duration: int, width: int, height: int) -> None:
    job = _IMG_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        result_sid = await _ensure_image_stream(url, duration, width, height)
        job["state"] = "ready"
        job["result_sid"] = result_sid
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
    finally:
        async with _IMG_BUILD_LOCK:
            _IMG_BUILD_TASKS.pop(job_id, None)


async def _ensure_img_build_job(url: str, duration: int, width: int, height: int) -> Dict[str, Any]:
    url = _normalize_image_url(url)
    sid = utils.image_stream_sid(url, duration, width, height)
    job_id = _img_job_id(url, duration, width, height)

    async with _IMG_BUILD_LOCK:
        job = _IMG_BUILD_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "url": url,
                "duration": duration,
                "width": width,
                "height": height,
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

        task = _IMG_BUILD_TASKS.get(job_id)
        if task and not task.done():
            return _job_snapshot(job_id)

        job["state"] = "pending"
        job["result_sid"] = None
        job["error"] = None
        _IMG_BUILD_TASKS[job_id] = asyncio.create_task(
            _run_img_build_job(job_id, url, duration, width, height)
        )
        return _job_snapshot(job_id)


@router.get("/stream-image-build-start")
async def stream_image_build_start(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
):
    return await _ensure_img_build_job(url, duration, width, height)


@router.get("/stream-image-build-status")
async def stream_image_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="image build job not found")
    return snapshot


@router.get("/stream-image")
async def stream_image(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
):
    sid = await _ensure_image_stream(url, duration, width, height)
    return _hls_response(sid)
