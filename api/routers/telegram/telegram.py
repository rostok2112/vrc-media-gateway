import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils
from api.routers.telegram import utils as telegram_utils


router = APIRouter()
TG_AUDIO_POSTER_LAYOUT_VERSION = "tg-audio-poster-v1"

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


def _tg_video_sid(url: str, segment_time: Optional[int] = None) -> str:
    return utils.video_stream_sid(url, "telegram", segment_time=segment_time)


def _tg_audio_sid(
    url: str,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    return utils.sid_for_url(
        url,
        "telegram",
        "audio",
        TG_AUDIO_POSTER_LAYOUT_VERSION,
        utils.hls_segment_cache_part(segment_time),
        f"{width}x{height}",
    )


def _tg_video_text_sid(
    url: str,
    text: str,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    return utils.sid_for_url(
        url,
        "telegram",
        "video",
        utils.POST_TEXT_EXPORT_LAYOUT_VERSION,
        utils.text_cache_part(text),
        utils.hls_segment_cache_part(segment_time),
        f"{width}x{height}",
    )


def _tg_image_text_sid(
    url: str,
    text: str,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    return utils.sid_for_url(
        url,
        "telegram",
        "image",
        utils.POST_TEXT_EXPORT_LAYOUT_VERSION,
        utils.text_cache_part(text),
        utils.hls_segment_cache_part(segment_time),
        f"{duration}{width}x{height}",
    )


def _tg_text_only_sid(
    url: str,
    text: str,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    return utils.sid_for_url(
        url,
        "telegram",
        "text-only",
        utils.POST_TEXT_EXPORT_LAYOUT_VERSION,
        utils.text_cache_part(text),
        utils.hls_segment_cache_part(segment_time),
        f"{duration}{width}x{height}",
    )


def _tg_build_job_id(
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str] = None,
    segment_time: Optional[int] = None,
    caption_text: str = "",
) -> str:
    if caption_text:
        return utils.sid_for_url(
            url,
            "tg-build",
            media_kind or "media",
            utils.POST_TEXT_EXPORT_LAYOUT_VERSION,
            utils.text_cache_part(caption_text),
            utils.hls_segment_cache_part(segment_time),
            f"{duration}{width}x{height}",
        )
    if media_kind == "video":
        return utils.video_build_job_id(url, "tg-build", "telegram", segment_time=segment_time)
    if media_kind == "audio":
        return utils.sid_for_url(
            url,
            "tg-build",
            "telegram",
            "audio",
            TG_AUDIO_POSTER_LAYOUT_VERSION,
            utils.hls_segment_cache_part(segment_time),
            f"{width}x{height}",
        )
    return utils.image_build_job_id(
        url,
        duration,
        width,
        height,
        scope="tg-build",
        segment_time=segment_time,
    )


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


async def _resolve_post_text(url: str, with_text: bool) -> Any:
    if not with_text:
        return ""

    try:
        payload = await telegram_utils.get_tg_post_text_payload(url)
        return payload if utils.normalize_overlay_text(payload) else ""
    except Exception as e:
        print("Telegram post text fetch failed, keeping plain media flow:", e)
        return ""


async def _ensure_tg_text_only_stream(
    url: str,
    caption_text: Any,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    caption_value = utils.normalize_overlay_text(caption_text)
    if not caption_value:
        raise HTTPException(status_code=404, detail="telegram post has no text")

    segment_time = utils.normalize_hls_segment_time(segment_time)
    sid = _tg_text_only_sid(url, caption_text, duration, width, height, segment_time)
    out_dir = config.STREAMS / sid

    if _is_hls_ready(sid):
        return sid

    await asyncio.to_thread(
        utils.text_to_hls,
        out_dir,
        sid,
        caption_text,
        duration,
        width,
        height,
        segment_time,
    )

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="telegram text-only HLS build failed")

    return sid


async def _ensure_tg_video_stream(
    url: str,
    segment_time: Optional[int] = None,
    width: int = 1280,
    height: int = 720,
    caption_text: Any = "",
) -> str:
    url = _normalize_tg_url(url)
    segment_time = utils.normalize_hls_segment_time(segment_time)
    if isinstance(caption_text, dict):
        caption_text = utils.normalize_overlay_payload(caption_text)
    caption_value = utils.normalize_overlay_text(caption_text)
    sid = (
        _tg_video_text_sid(url, caption_text, width, height, segment_time)
        if caption_value
        else _tg_video_sid(url, segment_time)
    )
    out_dir = config.STREAMS / sid

    if _is_hls_ready(sid):
        return sid

    out_dir.mkdir(parents=True, exist_ok=True)
    video = await telegram_utils.download_tg_video(url)
    if caption_value:
        await asyncio.to_thread(
            utils.video_to_hls_with_text,
            video,
            out_dir,
            sid,
            caption_text,
            width,
            height,
            segment_time,
        )
    else:
        await asyncio.to_thread(utils.video_to_hls, video, out_dir, sid, segment_time)

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="telegram video HLS build failed")

    return sid


async def _ensure_tg_audio_stream(
    url: str,
    segment_time: Optional[int] = None,
    width: int = 1280,
    height: int = 720,
) -> str:
    url = _normalize_tg_url(url)
    segment_time = utils.normalize_hls_segment_time(segment_time)
    sid = _tg_audio_sid(url, width, height, segment_time)
    out_dir = config.STREAMS / sid

    if _is_hls_ready(sid):
        return sid

    out_dir.mkdir(parents=True, exist_ok=True)
    audio = await telegram_utils.download_tg_audio(url)
    metadata = await telegram_utils.get_tg_audio_metadata(url)
    await asyncio.to_thread(
        utils.audio_to_hls_with_poster,
        audio,
        out_dir,
        sid,
        title=metadata.get("title", ""),
        performer=metadata.get("performer", ""),
        cover_image=metadata.get("cover_path") or None,
        duration_seconds=metadata.get("duration", 0),
        width=width,
        height=height,
        source_label="Voice message" if metadata.get("is_voice") else "Telegram audio",
        segment_time=segment_time,
    )

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="telegram audio HLS build failed")

    return sid


async def _ensure_tg_image_stream(
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str] = None,
    segment_time: Optional[int] = None,
    caption_text: Any = "",
) -> str:
    url = _normalize_tg_url(url)
    segment_time = utils.normalize_hls_segment_time(segment_time)
    if isinstance(caption_text, dict):
        caption_text = utils.normalize_overlay_payload(caption_text)
    caption_value = utils.normalize_overlay_text(caption_text)

    if media_kind is None:
        try:
            media_kind = await telegram_utils.get_tg_post_media_kind(url)
        except Exception as e:
            print("Telethon media inspect failed, keeping image flow:", e)

    if media_kind == "video":
        print("Telegram post is a video, routing image endpoint to video pipeline")
        return await _ensure_tg_video_stream(url, segment_time, width, height, caption_text)
    if media_kind == "audio":
        print("Telegram post is an audio track, routing image endpoint to audio pipeline")
        return await _ensure_tg_audio_stream(url, segment_time, width, height)

    sid = (
        _tg_image_text_sid(url, caption_text, duration, width, height, segment_time)
        if caption_value
        else utils.image_stream_sid(url, duration, width, height, segment_time)
    )
    if _is_hls_ready(sid):
        return sid

    try:
        img = await telegram_utils.download_tg_image(url)
        if caption_value:
            await asyncio.to_thread(
                utils.build_hls_from_image_with_text,
                str(img),
                sid,
                caption_text,
                duration,
                width,
                height,
                segment_time,
            )
        else:
            await asyncio.to_thread(
                utils.build_hls_from_image,
                str(img),
                sid,
                duration,
                width,
                height,
                segment_time,
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
        return await _ensure_tg_video_stream(url, segment_time, width, height, caption_text)

    img_url = utils.extract_image_from_html(html, base_url=final)
    if not img_url:
        fallback_text = caption_text
        if not utils.normalize_overlay_text(fallback_text):
            try:
                fallback_text = await telegram_utils.get_tg_post_text_payload(url)
            except Exception as e:
                print("Telegram post text fetch failed during text-only fallback:", e)
                fallback_text = ""

        if utils.normalize_overlay_text(fallback_text):
            return await _ensure_tg_text_only_stream(
                url,
                fallback_text,
                duration,
                width,
                height,
                segment_time,
            )
        raise HTTPException(404, "no image found in telegram post")

    if img_url.startswith("//"):
        img_url = "https:" + img_url

    build_source = img_url
    if _is_hls_ready(sid):
        return sid

    if caption_value:
        await asyncio.to_thread(
            utils.build_hls_from_image_with_text,
            build_source,
            sid,
            caption_text,
            duration,
            width,
            height,
            segment_time,
        )
    else:
        await asyncio.to_thread(
            utils.build_hls_from_image,
            build_source,
            sid,
            duration,
            width,
            height,
            segment_time,
        )

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="telegram fallback image HLS build failed")

    return sid


async def _run_tg_build_job(
    job_id: str,
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str],
    segment_time: Optional[int],
    caption_text: Any,
) -> None:
    job = _TG_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        if media_kind == "video":
            result_sid = await _ensure_tg_video_stream(url, segment_time, width, height, caption_text)
        elif media_kind == "audio":
            result_sid = await _ensure_tg_audio_stream(url, segment_time, width, height)
        else:
            result_sid = await _ensure_tg_image_stream(
                url,
                duration,
                width,
                height,
                media_kind=media_kind,
                segment_time=segment_time,
                caption_text=caption_text,
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
    segment_time: Optional[int] = None,
    caption_text: Any = "",
) -> Dict[str, Any]:
    url = _normalize_tg_url(url)
    segment_time = utils.normalize_hls_segment_time(segment_time)
    if isinstance(caption_text, dict):
        caption_text = utils.normalize_overlay_payload(caption_text)
    caption_value = utils.normalize_overlay_text(caption_text)
    job_id = _tg_build_job_id(url, duration, width, height, media_kind, segment_time, caption_text)

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
                "segment_time": segment_time,
                "caption_text": caption_text if caption_value else "",
                "state": "pending",
                "result_sid": None,
                "error": None,
            },
        )
        if media_kind and not job.get("media_kind"):
            job["media_kind"] = media_kind
        job["segment_time"] = segment_time
        job["caption_text"] = caption_text if caption_value else ""

        if media_kind == "video":
            sid = (
                _tg_video_text_sid(url, caption_text, width, height, segment_time)
                if caption_value
                else _tg_video_sid(url, segment_time)
            )
            if _is_hls_ready(sid):
                job["state"] = "ready"
                job["result_sid"] = sid
                job["error"] = None
                return _job_snapshot(job_id)
        elif media_kind == "audio":
            sid = _tg_audio_sid(url, width, height, segment_time)
            if _is_hls_ready(sid):
                job["state"] = "ready"
                job["result_sid"] = sid
                job["error"] = None
                return _job_snapshot(job_id)
        elif caption_value:
            sid = (
                _tg_image_text_sid(url, caption_text, duration, width, height, segment_time)
                if media_kind
                else _tg_text_only_sid(url, caption_text, duration, width, height, segment_time)
            )
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
                segment_time,
                job.get("caption_text", ""),
            )
        )
        return _job_snapshot(job_id)


@router.get("/stream-tg-build-start")
async def stream_tg_build_start(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
    segment_time: int | None = Query(default=None, ge=1),
    with_text: bool = Query(default=False),
):
    url = _normalize_tg_url(url)

    try:
        media_kind = await telegram_utils.get_tg_post_media_kind(url)
    except Exception as e:
        media_kind = None
        print("Telethon media inspect failed, defaulting to image build job:", e)

    caption_text = await _resolve_post_text(url, with_text)
    return await _ensure_tg_build_job(url, duration, width, height, media_kind, segment_time, caption_text)


@router.get("/stream-tg-build-status")
async def stream_tg_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="telegram build job not found")
    return snapshot


@router.get("/tg-post-info")
async def tg_post_info(url: str = Query(...)):
    url = _normalize_tg_url(url)

    try:
        media_kind = await telegram_utils.get_tg_post_media_kind(url)
    except Exception as e:
        media_kind = None
        print("Telegram media inspect failed for tg-post-info:", e)

    try:
        post_text = utils.normalize_overlay_text(await telegram_utils.get_tg_post_text(url))
    except Exception as e:
        post_text = ""
        print("Telegram text fetch failed for tg-post-info:", e)

    return {
        "url": url,
        "media_kind": media_kind,
        "has_text": bool(post_text),
        "is_text_only": not media_kind and bool(post_text),
    }


async def _stream_tg_video_impl(
    url: str,
    segment_time: Optional[int] = None,
    width: int = 1280,
    height: int = 720,
    caption_text: str = "",
) -> Response:
    sid = await _ensure_tg_video_stream(url, segment_time, width, height, caption_text)
    return _hls_response(sid)


async def _stream_tg_audio_impl(
    url: str,
    segment_time: Optional[int] = None,
    width: int = 1280,
    height: int = 720,
) -> Response:
    sid = await _ensure_tg_audio_stream(url, segment_time, width, height)
    return _hls_response(sid)


async def _stream_tg_image_impl(
    url: str,
    duration: int,
    width: int,
    height: int,
    media_kind: Optional[str] = None,
    segment_time: Optional[int] = None,
    caption_text: str = "",
) -> Response:
    sid = await _ensure_tg_image_stream(
        url,
        duration,
        width,
        height,
        media_kind=media_kind,
        segment_time=segment_time,
        caption_text=caption_text,
    )
    return _hls_response(sid)


@router.get("/stream-tg-media")
async def stream_tg_media(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
    segment_time: int | None = Query(default=None, ge=1),
    with_text: bool = Query(default=False),
):
    try:
        media_kind = await telegram_utils.get_tg_post_media_kind(url)
    except Exception as e:
        media_kind = None
        print("Telethon media inspect failed, defaulting to image flow:", e)

    caption_text = await _resolve_post_text(url, with_text)
    if media_kind == "video":
        return await _stream_tg_video_impl(url, segment_time, width, height, caption_text)
    if media_kind == "audio":
        return await _stream_tg_audio_impl(url, segment_time, width, height)

    return await _stream_tg_image_impl(
        url,
        duration,
        width,
        height,
        media_kind=media_kind,
        segment_time=segment_time,
        caption_text=caption_text,
    )


@router.get("/stream-tg-image")
async def stream_tg_image(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
    segment_time: int | None = Query(default=None, ge=1),
    with_text: bool = Query(default=False),
):
    caption_text = await _resolve_post_text(url, with_text)
    return await _stream_tg_image_impl(
        url,
        duration,
        width,
        height,
        segment_time=segment_time,
        caption_text=caption_text,
    )


@router.get("/stream-tg-video")
async def stream_tg_video(
    url: str = Query(...),
    segment_time: int | None = Query(default=None, ge=1),
    width: int = Query(1280),
    height: int = Query(720),
    with_text: bool = Query(default=False),
):
    caption_text = await _resolve_post_text(url, with_text)
    return await _stream_tg_video_impl(url, segment_time, width, height, caption_text)


@router.get("/stream-tg-audio")
async def stream_tg_audio(
    url: str = Query(...),
    segment_time: int | None = Query(default=None, ge=1),
    width: int = Query(1280),
    height: int = Query(720),
):
    return await _stream_tg_audio_impl(url, segment_time, width, height)


@router.get("/resolve-tg-public-link")
async def resolve_tg_public_link(internal: str = Query(...)):
    try:
        link = await telegram_utils.resolve_public_tg_link(internal)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not link:
        raise HTTPException(status_code=404, detail="public username not found")

    return {"url": link}
