import asyncio
import hashlib
import logging
import subprocess
import urllib.parse as urlparse
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils


logger = logging.getLogger(__name__)
router = APIRouter()

_YT_BUILD_JOBS: Dict[str, Dict[str, Any]] = {}
_YT_BUILD_TASKS: Dict[str, asyncio.Task] = {}
_YT_BUILD_LOCK = asyncio.Lock()


async def clear_yt_build_jobs() -> None:
    async with _YT_BUILD_LOCK:
        tasks = list(_YT_BUILD_TASKS.values())
        _YT_BUILD_TASKS.clear()
        _YT_BUILD_JOBS.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def normalize_yt_url(url: str) -> str:
    p = urlparse.urlparse(url)
    if p.netloc.endswith("youtu.be"):
        vid = p.path.lstrip("/")
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
    if "youtube" in p.netloc:
        qs = urlparse.parse_qs(p.query)
        if "v" in qs:
            return f"https://www.youtube.com/watch?v={qs['v'][0]}"
    return url


def _yt_sid(norm_url: str) -> str:
    return hashlib.md5(norm_url.encode()).hexdigest()


def _yt_job_id(norm_url: str) -> str:
    return hashlib.md5(f"yt-build|{norm_url}".encode()).hexdigest()


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


def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    job = _YT_BUILD_JOBS.get(job_id)
    if not job:
        return None

    snapshot = {k: v for k, v in job.items()}
    sid = snapshot.get("result_sid")
    snapshot["ready"] = snapshot.get("state") == "ready" and bool(sid)
    if sid:
        snapshot["stream_path"] = _stream_path_for_sid(sid)
    return snapshot


def _download_youtube_to_file(norm_url: str, video_out) -> None:
    if video_out.exists() and video_out.stat().st_size > 0:
        return

    try:
        cmd = [
            config.YTDLP,
            "--js-runtimes", config.JS_RUNTIME,
            "--remote-components", "ejs:github",
            "--cookies", str(config.COOKIES),
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", str(video_out),
            norm_url,
        ]
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            logger.error("yt-dlp stderr: %s", p.stderr)
            raise subprocess.CalledProcessError(p.returncode, cmd, output=p.stdout, stderr=p.stderr)
    except subprocess.CalledProcessError:
        try:
            cmd2 = [
                config.YTDLP,
                "--js-runtimes", config.JS_RUNTIME,
                "--remote-components", "ejs:github",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "-o", str(video_out),
                norm_url,
            ]
            p2 = subprocess.run(cmd2, check=False, capture_output=True, text=True, timeout=300)
            if p2.returncode != 0:
                logger.error("yt-dlp fallback stderr: %s", p2.stderr)
                raise subprocess.CalledProcessError(p2.returncode, cmd2, output=p2.stdout, stderr=p2.stderr)
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"yt-dlp failed. stderr: {e.stderr[:2000]}")


def _build_yt_sync(norm_url: str, out_dir, sid: str) -> None:
    video_out = out_dir / "video.mp4"
    _download_youtube_to_file(norm_url, video_out)
    utils.video_to_hls(video_out, out_dir, sid)


async def _ensure_yt_stream(url: str) -> str:
    norm = normalize_yt_url(url)
    sid = _yt_sid(norm)
    if _is_hls_ready(sid):
        return sid

    out_dir = config.STREAMS / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        await asyncio.to_thread(_build_yt_sync, norm, out_dir, sid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="youtube HLS build failed")

    return sid


async def _run_yt_build_job(job_id: str, url: str) -> None:
    job = _YT_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        result_sid = await _ensure_yt_stream(url)
        job["state"] = "ready"
        job["result_sid"] = result_sid
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
    finally:
        async with _YT_BUILD_LOCK:
            _YT_BUILD_TASKS.pop(job_id, None)


async def _ensure_yt_build_job(url: str) -> Dict[str, Any]:
    norm = normalize_yt_url(url)
    sid = _yt_sid(norm)
    job_id = _yt_job_id(norm)

    async with _YT_BUILD_LOCK:
        job = _YT_BUILD_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "url": url,
                "normalized_url": norm,
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

        task = _YT_BUILD_TASKS.get(job_id)
        if task and not task.done():
            return _job_snapshot(job_id)

        job["state"] = "pending"
        job["result_sid"] = None
        job["error"] = None
        _YT_BUILD_TASKS[job_id] = asyncio.create_task(_run_yt_build_job(job_id, url))
        return _job_snapshot(job_id)


@router.get("/stream-yt-build-start")
async def stream_yt_build_start(url: str = Query(...)):
    return await _ensure_yt_build_job(url)


@router.get("/stream-yt-build-status")
async def stream_yt_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="youtube build job not found")
    return snapshot


@router.get("/stream-yt")
async def stream_yt(url: str = Query(...)):
    sid = await _ensure_yt_stream(url)
    return _hls_response(sid)
