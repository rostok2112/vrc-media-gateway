import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils
from api.routers.telegram import utils as telegram_utils


router = APIRouter()

_TG_BUILD_JOBS: Dict[str, Dict[str, Any]] = {}
_TG_BUILD_TASKS: Dict[str, asyncio.Task] = {}
_TG_BUILD_LOCK = asyncio.Lock()


async def clear_tg_build_jobs() -> None:
    async with _TG_BUILD_LOCK:
        tasks = list(_TG_BUILD_TASKS.values())
        _TG_BUILD_TASKS.clear()
        _TG_BUILD_JOBS.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _normalize_tg_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="invalid url")
    return url


def _stream_path_for_sid(sid: str) -> str:
    return f"/streams/{sid}/index.m3u8"


def _hls_response(sid: str) -> Response:
    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": _stream_path_for_sid(sid),
            "Content-Type": "application/vnd.apple.mpegurl",
        },
    )


def _is_hls_ready(sid: str) -> bool:
    return utils.is_hls_output_ready(config.STREAMS / sid)


def _tg_video_sid(url: str) -> str:
    return utils.video_stream_sid(url, "telegram")


def _tg_build_job_id(url: str, duration: int, width: int, height: int, media_kind: Optional[str] = None) -> str:
    if media_kind == "video":
        return utils.video_build_job_id(url, "tg-build", "telegram")
    return utils.image_build_job_id(url, duration, width, height, scope="tg-build")


def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    job = _TG_BUILD_JOBS.get(job_id)
    if not job:
        return None

    snapshot = {k: v for k, v in job.items()}
    sid = snapshot.get("result_sid")
    snapshot["ready"] = snapshot.get("state") == "ready" and bool(sid)
    if sid:
        snapshot["stream_path"] = _stream_path_for_sid(sid)
    return snapshot


async def _ensure_tg_video_stream(url: str) -> str:
    url = _normalize_tg_url(url)
    sid = _tg_video_sid(url)
    out_dir = config.STREAMS / sid

    if _is_hls_ready(sid):
        return sid

    out_dir.mkdir(parents=True, exist_ok=True)
    video = await telegram_utils.download_tg_video(url)
    await asyncio.to_thread(utils.video_to_hls, video, out_dir, sid)

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="telegram video HLS build failed")

    return sid


async def _ensure_tg_image_stream(
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str] = None,
) -> str:
    url = _normalize_tg_url(url)

    if media_kind is None:
        try:
            media_kind = await telegram_utils.get_tg_post_media_kind(url)
        except Exception as e:
            print("Telethon media inspect failed, keeping image flow:", e)

    if media_kind == "video":
        print("Telegram post is a video, routing image endpoint to video pipeline")
        return await _ensure_tg_video_stream(url)

    sid = utils.image_stream_sid(url, duration, width, height)
    if _is_hls_ready(sid):
        return sid

    try:
        img = await telegram_utils.download_tg_photo(url)
        await asyncio.to_thread(
            utils.build_hls_from_image,
            str(img),
            sid,
            duration,
            width,
            height,
        )
        if not _is_hls_ready(sid):
            raise HTTPException(status_code=500, detail="telegram photo HLS build failed")
        return sid
    except Exception as e:
        print("Telethon failed, using HTML fallback:", e)

    try:
        html, final = await asyncio.to_thread(utils.fetch_html, url)
    except Exception as e:
        raise HTTPException(400, f"fetch html failed: {e}")

    if telegram_utils.html_contains_tg_video(html):
        raise HTTPException(409, "telegram post contains video; use /api/stream-tg-video")

    img_url = utils.extract_image_from_html(html, base_url=final)
    if not img_url:
        raise HTTPException(404, "no image found in telegram post")

    if img_url.startswith("//"):
        img_url = "https:" + img_url

    img_sid = utils.image_stream_sid(img_url, duration, width, height)
    if _is_hls_ready(img_sid):
        return img_sid

    await asyncio.to_thread(
        utils.build_hls_from_image,
        img_url,
        img_sid,
        duration,
        width,
        height,
    )

    if not _is_hls_ready(img_sid):
        raise HTTPException(status_code=500, detail="telegram fallback image HLS build failed")

    return img_sid


async def _run_tg_build_job(
    job_id: str,
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str],
) -> None:
    job = _TG_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        if media_kind == "video":
            result_sid = await _ensure_tg_video_stream(url)
        else:
            result_sid = await _ensure_tg_image_stream(
                url,
                duration,
                width,
                height,
                media_kind=media_kind,
            )

        job["state"] = "ready"
        job["result_sid"] = result_sid
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
    finally:
        async with _TG_BUILD_LOCK:
            _TG_BUILD_TASKS.pop(job_id, None)


async def _ensure_tg_build_job(
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str],
) -> Dict[str, Any]:
    url = _normalize_tg_url(url)
    job_id = _tg_build_job_id(url, duration, width, height, media_kind)

    async with _TG_BUILD_LOCK:
        job = _TG_BUILD_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "url": url,
                "duration": duration,
                "width": width,
                "height": height,
                "media_kind": media_kind,
                "state": "pending",
                "result_sid": None,
                "error": None,
            },
        )
        if media_kind and not job.get("media_kind"):
            job["media_kind"] = media_kind

        if media_kind == "video":
            sid = _tg_video_sid(url)
            if _is_hls_ready(sid):
                job["state"] = "ready"
                job["result_sid"] = sid
                job["error"] = None
                return _job_snapshot(job_id)

        task = _TG_BUILD_TASKS.get(job_id)
        if task and not task.done():
            return _job_snapshot(job_id)

        if job.get("state") == "ready" and job.get("result_sid") and _is_hls_ready(job["result_sid"]):
            return _job_snapshot(job_id)

        job["state"] = "pending"
        job["error"] = None
        job["result_sid"] = None
        _TG_BUILD_TASKS[job_id] = asyncio.create_task(
            _run_tg_build_job(
                job_id,
                url,
                duration,
                width,
                height,
                job.get("media_kind"),
            )
        )
        return _job_snapshot(job_id)


@router.get("/stream-tg-build-start")
async def stream_tg_build_start(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
):
    url = _normalize_tg_url(url)

    try:
        media_kind = await telegram_utils.get_tg_post_media_kind(url)
    except Exception as e:
        media_kind = None
        print("Telethon media inspect failed, defaulting to image build job:", e)

    return await _ensure_tg_build_job(url, duration, width, height, media_kind)


@router.get("/stream-tg-build-status")
async def stream_tg_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="telegram build job not found")
    return snapshot


async def _stream_tg_video_impl(url: str) -> Response:
    sid = await _ensure_tg_video_stream(url)
    return _hls_response(sid)


async def _stream_tg_image_impl(
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str] = None,
) -> Response:
    sid = await _ensure_tg_image_stream(url, duration, width, height, media_kind=media_kind)
    return _hls_response(sid)


@router.get("/stream-tg-media")
async def stream_tg_media(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
):
    try:
        media_kind = await telegram_utils.get_tg_post_media_kind(url)
    except Exception as e:
        media_kind = None
        print("Telethon media inspect failed, defaulting to image flow:", e)

    if media_kind == "video":
        return await _stream_tg_video_impl(url)

    return await _stream_tg_image_impl(url, duration, width, height, media_kind=media_kind)


@router.get("/stream-tg-image")
async def stream_tg_image(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
):
    return await _stream_tg_image_impl(url, duration, width, height)


@router.get("/stream-tg-video")
async def stream_tg_video(url: str = Query(...)):
    return await _stream_tg_video_impl(url)


@router.get("/resolve-tg-public-link")
async def resolve_tg_public_link(internal: str = Query(...)):
    try:
        link = await telegram_utils.resolve_public_tg_link(internal)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not link:
        raise HTTPException(status_code=404, detail="public username not found")

    return {"url": link}
