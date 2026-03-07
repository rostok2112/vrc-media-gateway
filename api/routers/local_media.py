import asyncio
import hashlib
import mimetypes
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api import config, utils
from api.segments_engine import registry


router = APIRouter()

LOCAL_MEDIA_BUILD_VERSION = "local-media-v1"
LOCAL_UPLOAD_MAX_BYTES = config.LOCAL_UPLOAD_MAX_BYTES
LOCAL_UPLOAD_CACHE_DIR = config.OUTPUT / "local_uploads"
LOCAL_UPLOAD_TMP_DIR = config.OUTPUT / "local_upload_tmp"

IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
}
VIDEO_SUFFIXES = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".ts", ".mts", ".m2ts", ".flv",
}
AUDIO_SUFFIXES = {
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wma",
}

_LOCAL_MEDIA_BUILD_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCAL_MEDIA_BUILD_TASKS: Dict[str, asyncio.Task] = {}
_LOCAL_MEDIA_BUILD_LOCK = asyncio.Lock()
PRESERVED_CACHE_NAMES = {".gitkeep", ".gitignore"}


class LocalPathBuildRequest(BaseModel):
    path: str
    duration: int = Field(default=300, ge=1, le=86400)
    width: int = Field(default=1280, ge=1, le=7680)
    height: int = Field(default=720, ge=1, le=4320)


async def clear_local_media_build_jobs() -> None:
    async with _LOCAL_MEDIA_BUILD_LOCK:
        tasks = list(_LOCAL_MEDIA_BUILD_TASKS.values())
        _LOCAL_MEDIA_BUILD_TASKS.clear()
        _LOCAL_MEDIA_BUILD_JOBS.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _stream_path_for_sid(sid: str) -> str:
    return f"/streams/{sid}/index.m3u8"


def _ensure_loopback_request(request: Request) -> None:
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="local media routes are loopback-only")


def _remove_dir_contents(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    removed = 0

    for child in path.iterdir():
        if child.name in PRESERVED_CACHE_NAMES:
            continue

        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=False)
        else:
            child.unlink(missing_ok=True)
        removed += 1

    return removed


def _is_hls_ready(sid: str) -> bool:
    return utils.is_hls_output_ready(config.STREAMS / sid)


def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    job = _LOCAL_MEDIA_BUILD_JOBS.get(job_id)
    if not job:
        return None

    snapshot = {k: v for k, v in job.items() if k not in {"source_path", "cleanup_path"}}
    sid = snapshot.get("result_sid")
    snapshot["ready"] = snapshot.get("state") == "ready" and bool(sid)
    if sid:
        snapshot["stream_path"] = _stream_path_for_sid(sid)
    return snapshot


def _ffprobe_binary() -> str:
    ffmpeg_path = Path(config.FFMPEG)
    if ffmpeg_path.suffix.lower() == ".exe":
        sibling = ffmpeg_path.with_name("ffprobe.exe")
        if sibling.exists():
            return str(sibling)
        return "ffprobe.exe"
    sibling = ffmpeg_path.with_name("ffprobe")
    if sibling.exists():
        return str(sibling)
    return "ffprobe"


def _probe_av_kind(path: Path) -> Optional[str]:
    try:
        probe = subprocess.run(
            [
                _ffprobe_binary(),
                "-v", "error",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None

    if probe.returncode != 0:
        return None

    kinds = {line.strip().lower() for line in probe.stdout.splitlines() if line.strip()}
    if "audio" in kinds and "video" not in kinds:
        return "audio"
    if "video" in kinds:
        return "video"
    return None


def _detect_media_kind(path: Path, content_type: Optional[str] = None) -> str:
    candidates = [content_type, mimetypes.guess_type(path.name)[0]]
    for mime in candidates:
        if not mime:
            continue
        if mime == "image/gif":
            return "video"
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"

    suffix = path.suffix.lower()
    if suffix == ".gif":
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in VIDEO_SUFFIXES:
        return "video"

    av_kind = _probe_av_kind(path)
    if av_kind:
        return av_kind

    raise HTTPException(status_code=400, detail="unsupported local media type")


def _normalize_local_path(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="missing local path")

    if value.lower().startswith("file://"):
        parsed = urlparse(value)
        file_path = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc.lower() not in {"", "localhost"}:
            remote_path = file_path.replace("/", "\\")
            value = "\\\\" + parsed.netloc + remote_path
        else:
            value = file_path
        if len(value) >= 3 and value[0] == "/" and value[2] == ":":
            value = value[1:]
        value = value.replace("/", "\\")

    path = Path(value)
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="absolute local path required")

    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="local file not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="local path must point to a file")

    return resolved


def _local_media_sid(
    source_key: str,
    media_kind: str,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    parts = [LOCAL_MEDIA_BUILD_VERSION, media_kind, utils.hls_segment_cache_part(segment_time)]
    if media_kind == "video":
        parts.append(utils.VIDEO_HLS_LAYOUT_VERSION)
    if media_kind == "audio":
        parts.append(utils.AUDIO_HLS_LAYOUT_VERSION)
    if media_kind == "image":
        parts.append(f"{duration}")
        parts.append(f"{width}x{height}")
    return utils.sid_for_url(source_key, *parts)


def _local_media_job_id(
    source_key: str,
    media_kind: str,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> str:
    parts = ["local-media-build", LOCAL_MEDIA_BUILD_VERSION, media_kind, utils.hls_segment_cache_part(segment_time)]
    if media_kind == "video":
        parts.append(utils.VIDEO_HLS_LAYOUT_VERSION)
    if media_kind == "audio":
        parts.append(utils.AUDIO_HLS_LAYOUT_VERSION)
    if media_kind == "image":
        parts.append(f"{duration}")
        parts.append(f"{width}x{height}")
    return utils.sid_for_url(source_key, *parts)


def _path_source_key(path: Path) -> str:
    stat = path.stat()
    return f"path|{str(path).lower()}|{stat.st_size}|{stat.st_mtime_ns}"


def _build_local_media_sync(
    source_path: Path,
    sid: str,
    media_kind: str,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
) -> None:
    out_dir = config.STREAMS / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    if media_kind == "image":
        utils.build_hls_from_image(str(source_path), sid, duration, width, height, segment_time)
    elif media_kind == "audio":
        utils.audio_to_hls(source_path, out_dir, sid, segment_time=segment_time)
    elif media_kind == "video":
        utils.video_to_hls(source_path, out_dir, sid, segment_time=segment_time)
    else:
        raise HTTPException(status_code=400, detail="unsupported local media type")

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="local media HLS build failed")


async def _run_local_media_build_job(job_id: str) -> None:
    job = _LOCAL_MEDIA_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        source_path = Path(job["source_path"])
        await asyncio.to_thread(
            _build_local_media_sync,
            source_path,
            job["result_sid"],
            job["media_kind"],
            job["duration"],
            job["width"],
            job["height"],
            job.get("segment_time"),
        )
        job["state"] = "ready"
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
        job["result_sid"] = None
    finally:
        cleanup_path = job.get("cleanup_path")
        if cleanup_path:
            try:
                Path(cleanup_path).unlink(missing_ok=True)
            except Exception:
                pass

        async with _LOCAL_MEDIA_BUILD_LOCK:
            _LOCAL_MEDIA_BUILD_TASKS.pop(job_id, None)


async def _ensure_local_media_job(
    source_key: str,
    source_path: Path,
    media_kind: str,
    duration: int,
    width: int,
    height: int,
    segment_time: Optional[int] = None,
    *,
    source_label: str,
    cleanup_path: Optional[Path] = None,
) -> Dict[str, Any]:
    segment_time = utils.normalize_hls_segment_time(segment_time)
    sid = _local_media_sid(source_key, media_kind, duration, width, height, segment_time)
    job_id = _local_media_job_id(source_key, media_kind, duration, width, height, segment_time)

    async with _LOCAL_MEDIA_BUILD_LOCK:
        job = _LOCAL_MEDIA_BUILD_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "state": "pending",
                "result_sid": sid,
                "error": None,
                "media_kind": media_kind,
                "source_label": source_label,
                "duration": duration,
                "width": width,
                "height": height,
                "segment_time": segment_time,
                "source_path": str(source_path),
                "cleanup_path": str(cleanup_path) if cleanup_path else None,
            },
        )

        job["media_kind"] = media_kind
        job["duration"] = duration
        job["width"] = width
        job["height"] = height
        job["segment_time"] = segment_time
        job["source_label"] = source_label
        job["source_path"] = str(source_path)
        job["cleanup_path"] = str(cleanup_path) if cleanup_path else None
        job["result_sid"] = sid

        if _is_hls_ready(sid):
            job["state"] = "ready"
            job["error"] = None
            return _job_snapshot(job_id)

        task = _LOCAL_MEDIA_BUILD_TASKS.get(job_id)
        if task and not task.done():
            return _job_snapshot(job_id)

        job["state"] = "pending"
        job["error"] = None
        _LOCAL_MEDIA_BUILD_TASKS[job_id] = asyncio.create_task(_run_local_media_build_job(job_id))
        return _job_snapshot(job_id)


async def _save_uploaded_media(
    request: Request,
    filename: str,
    content_type: Optional[str],
) -> tuple[Path, str, int]:
    LOCAL_UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = LOCAL_UPLOAD_TMP_DIR / f"{uuid.uuid4().hex}.upload"
    sha256 = hashlib.sha256()
    total_size = 0

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > LOCAL_UPLOAD_MAX_BYTES:
                raise HTTPException(status_code=413, detail="uploaded file is too large")
        except ValueError:
            pass

    try:
        with temp_path.open("wb") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total_size += len(chunk)
                if total_size > LOCAL_UPLOAD_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="uploaded file is too large")
                sha256.update(chunk)
                fh.write(chunk)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    if total_size == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    safe_name = Path(filename or "upload.bin").name
    suffix = Path(safe_name).suffix.lower()
    if not suffix and content_type:
        suffix = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""

    LOCAL_UPLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    final_path = LOCAL_UPLOAD_CACHE_DIR / f"{sha256.hexdigest()}{suffix}"
    if final_path.exists() and final_path.stat().st_size == total_size:
        temp_path.unlink(missing_ok=True)
    else:
        os.replace(temp_path, final_path)

    return final_path, sha256.hexdigest(), total_size


@router.post("/stream-local-path-build-start")
async def stream_local_path_build_start(
    payload: LocalPathBuildRequest,
    request: Request,
    segment_time: int | None = Query(default=None, ge=1),
):
    _ensure_loopback_request(request)
    resolved_path = _normalize_local_path(payload.path)
    media_kind = _detect_media_kind(resolved_path)
    source_key = _path_source_key(resolved_path)
    return await _ensure_local_media_job(
        source_key,
        resolved_path,
        media_kind,
        payload.duration,
        payload.width,
        payload.height,
        segment_time,
        source_label=resolved_path.name,
    )


@router.post("/stream-local-upload-build-start")
async def stream_local_upload_build_start(
    request: Request,
    filename: str = Query(default="upload.bin"),
    content_type: Optional[str] = Query(default=None),
    duration: int = Query(default=300, ge=1, le=86400),
    width: int = Query(default=1280, ge=1, le=7680),
    height: int = Query(default=720, ge=1, le=4320),
    segment_time: int | None = Query(default=None, ge=1),
):
    _ensure_loopback_request(request)
    upload_path, sha256_hex, _ = await _save_uploaded_media(request, filename, content_type)
    try:
        media_kind = _detect_media_kind(upload_path, content_type=content_type)
        source_key = f"upload|{sha256_hex}"
        snapshot = await _ensure_local_media_job(
            source_key,
            upload_path,
            media_kind,
            duration,
            width,
            height,
            segment_time,
            source_label=Path(filename or upload_path.name).name,
            cleanup_path=upload_path,
        )
    except Exception:
        upload_path.unlink(missing_ok=True)
        raise

    if snapshot.get("ready"):
        upload_path.unlink(missing_ok=True)

    return snapshot


@router.get("/stream-local-build-status")
async def stream_local_build_status(request: Request, job_id: str = Query(...)):
    _ensure_loopback_request(request)
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="local media build job not found")
    return snapshot


@router.post("/clear-cache-all")
async def clear_cache_all(request: Request):
    _ensure_loopback_request(request)

    from api.routers import img, sc, yt, video, audio
    from api.routers.telegram import telegram as telegram_router

    await registry.stop_all_streams()
    await asyncio.gather(
        img.clear_img_build_jobs(),
        sc.clear_sc_build_jobs(),
        yt.clear_yt_build_jobs(),
        video.clear_video_build_jobs(),
        audio.clear_audio_build_jobs(),
        telegram_router.clear_tg_build_jobs(),
        clear_local_media_build_jobs(),
        return_exceptions=True,
    )

    try:
        removed_stream_entries = _remove_dir_contents(config.STREAMS)
        removed_output_entries = _remove_dir_contents(config.OUTPUT)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"cache remove failed: {e}")

    return {
        "ok": True,
        "removed_stream_entries": removed_stream_entries,
        "removed_output_entries": removed_output_entries,
    }
