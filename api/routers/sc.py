import asyncio
import hashlib
import subprocess
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils


router = APIRouter()

_SC_BUILD_JOBS: Dict[str, Dict[str, Any]] = {}
_SC_BUILD_TASKS: Dict[str, asyncio.Task] = {}
_SC_BUILD_LOCK = asyncio.Lock()


async def clear_sc_build_jobs() -> None:
    async with _SC_BUILD_LOCK:
        tasks = list(_SC_BUILD_TASKS.values())
        _SC_BUILD_TASKS.clear()
        _SC_BUILD_JOBS.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _sc_sid(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _stream_path_for_sid(sid: str) -> str:
    return f"/streams/{sid}/index.m3u8"


def _hls_response(sid: str) -> Response:
    return Response(status_code=200, headers={
        "X-Accel-Redirect": _stream_path_for_sid(sid),
        "Content-Type": "application/vnd.apple.mpegurl",
    })


def _is_hls_ready(sid: str) -> bool:
    return utils.is_hls_output_ready(config.STREAMS / sid)


def _sc_job_id(url: str) -> str:
    return hashlib.md5(f"sc-build|{url}".encode()).hexdigest()


def _job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    job = _SC_BUILD_JOBS.get(job_id)
    if not job:
        return None

    snapshot = {k: v for k, v in job.items()}
    sid = snapshot.get("result_sid")
    snapshot["ready"] = snapshot.get("state") == "ready" and bool(sid)
    if sid:
        snapshot["stream_path"] = _stream_path_for_sid(sid)
    return snapshot


def _build_sc_sync(url: str, out_dir, sid: str) -> None:
    audio_out = out_dir / "audio.m4a"

    if not audio_out.exists() or audio_out.stat().st_size == 0:
        base_cmd = [config.YTDLP, "-f", "bestaudio", "-o", str(audio_out), url]
        if config.COOKIES.exists():
            try:
                subprocess.run(
                    [config.YTDLP, "--cookies", str(config.COOKIES), "-f", "bestaudio", "-o", str(audio_out), url],
                    check=True,
                )
            except subprocess.CalledProcessError:
                subprocess.run(base_cmd, check=True)
        else:
            subprocess.run(base_cmd, check=True)

    utils.audio_to_hls(audio_out, out_dir, sid)


async def _ensure_sc_stream(url: str) -> str:
    sid = _sc_sid(url)
    if _is_hls_ready(sid):
        return sid

    out_dir = config.STREAMS / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        await asyncio.to_thread(_build_sc_sync, url, out_dir, sid)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="soundcloud HLS build failed")

    return sid


async def _run_sc_build_job(job_id: str, url: str) -> None:
    job = _SC_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        result_sid = await _ensure_sc_stream(url)
        job["state"] = "ready"
        job["result_sid"] = result_sid
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
    finally:
        async with _SC_BUILD_LOCK:
            _SC_BUILD_TASKS.pop(job_id, None)


async def _ensure_sc_build_job(url: str) -> Dict[str, Any]:
    sid = _sc_sid(url)
    job_id = _sc_job_id(url)

    async with _SC_BUILD_LOCK:
        job = _SC_BUILD_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "url": url,
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

        task = _SC_BUILD_TASKS.get(job_id)
        if task and not task.done():
            return _job_snapshot(job_id)

        job["state"] = "pending"
        job["result_sid"] = None
        job["error"] = None
        _SC_BUILD_TASKS[job_id] = asyncio.create_task(_run_sc_build_job(job_id, url))
        return _job_snapshot(job_id)


@router.get("/stream-sc-build-start")
async def stream_sc_build_start(url: str = Query(...)):
    return await _ensure_sc_build_job(url)


@router.get("/stream-sc-build-status")
async def stream_sc_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="soundcloud build job not found")
    return snapshot


@router.get("/stream-sc")
async def stream_sc(url: str = Query(...)):
    sid = await _ensure_sc_stream(url)
    return _hls_response(sid)
