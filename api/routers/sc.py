import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
import urllib.parse as urlparse

from fastapi import APIRouter, HTTPException, Query, Response

from api import config, utils


router = APIRouter()

SC_AUDIO_LAYOUT_VERSION = "soundcloud-poster-v1"
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


def normalize_sc_url(url: str) -> str:
    parsed = urlparse.urlparse(str(url or "").strip())
    if not parsed.scheme:
        return url

    filtered_query = [
        (key, value)
        for key, value in urlparse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() != "si"
    ]
    return urlparse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlparse.urlencode(filtered_query, doseq=True),
        "",
    ))


def _sc_sid(url: str, segment_time: Optional[int] = None) -> str:
    return utils.audio_stream_sid(url, "soundcloud", SC_AUDIO_LAYOUT_VERSION, segment_time=segment_time)


def _stream_path_for_sid(sid: str) -> str:
    return f"/streams/{sid}/index.m3u8"


def _hls_response(sid: str) -> Response:
    return Response(status_code=200, headers={
        "X-Accel-Redirect": _stream_path_for_sid(sid),
        "Content-Type": "application/vnd.apple.mpegurl",
    })


def _is_hls_ready(sid: str) -> bool:
    return utils.is_hls_output_ready(config.STREAMS / sid)


def _sc_job_id(url: str, segment_time: Optional[int] = None) -> str:
    return utils.audio_build_job_id(url, "sc-build", "soundcloud", SC_AUDIO_LAYOUT_VERSION, segment_time=segment_time)


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


def _run_ytdlp_capture_json(url: str) -> Dict[str, Any]:
    base_cmd = [config.YTDLP, "--dump-single-json", "--no-playlist", url]
    commands = []
    if config.COOKIES.exists():
        commands.append([config.YTDLP, "--cookies", str(config.COOKIES), "--dump-single-json", "--no-playlist", url])
    commands.append(base_cmd)

    last_error: Optional[subprocess.CalledProcessError] = None
    for cmd in commands:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, errors="replace", timeout=120)
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout or "{}")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"yt-dlp metadata parse failed: {e}")
        last_error = subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)

    if last_error:
        raise HTTPException(status_code=500, detail=f"yt-dlp metadata failed: {(last_error.stderr or '')[:2000]}")
    raise HTTPException(status_code=500, detail="yt-dlp metadata failed")


def _download_sc_audio(url: str, audio_out: Path) -> None:
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


def _best_thumbnail_url(info: Dict[str, Any]) -> str:
    thumbnails = list(info.get("thumbnails") or [])
    best = ""
    best_score = -1
    for thumb in thumbnails:
        thumb_url = str((thumb or {}).get("url") or "").strip()
        if not thumb_url:
            continue
        width = int((thumb or {}).get("width") or 0)
        height = int((thumb or {}).get("height") or 0)
        pref = int((thumb or {}).get("preference") or 0)
        score = max(width, height) * 100 + pref
        if score > best_score:
            best = thumb_url
            best_score = score

    if best:
        return best
    return str(info.get("thumbnail") or "").strip()


def _download_sc_cover(info: Dict[str, Any], out_dir: Path) -> Optional[Path]:
    thumb_url = _best_thumbnail_url(info)
    if not thumb_url:
        return None

    suffix = Path(urlparse.urlparse(thumb_url).path or "").suffix.lower() or ".jpg"
    cover_path = out_dir / f"audio_cover{suffix}"
    if cover_path.exists() and cover_path.stat().st_size > 0:
        return cover_path

    try:
        return utils.download_file(thumb_url, cover_path, max_bytes=16 * 1024 * 1024)
    except Exception:
        return None


def _sc_title(info: Dict[str, Any]) -> str:
    return (
        str(info.get("track") or "").strip()
        or str(info.get("title") or "").strip()
        or "SoundCloud track"
    )


def _sc_performer(info: Dict[str, Any]) -> str:
    return (
        str(info.get("artist") or "").strip()
        or str(info.get("uploader") or "").strip()
        or str(info.get("creator") or "").strip()
        or str(info.get("channel") or "").strip()
    )


def _build_sc_sync(
    url: str,
    out_dir,
    sid: str,
    segment_time: Optional[int] = None,
) -> None:
    info = _run_ytdlp_capture_json(url)
    audio_out = out_dir / "audio.m4a"
    _download_sc_audio(url, audio_out)
    cover_path = _download_sc_cover(info, out_dir)
    utils.audio_to_hls_with_poster(
        audio_out,
        out_dir,
        sid,
        title=_sc_title(info),
        performer=_sc_performer(info),
        cover_image=cover_path,
        duration_seconds=info.get("duration"),
        source_label="SoundCloud",
        segment_time=segment_time,
    )


async def _ensure_sc_stream(url: str, segment_time: Optional[int] = None) -> str:
    url = normalize_sc_url(url)
    segment_time = utils.normalize_hls_segment_time(segment_time)
    sid = _sc_sid(url, segment_time)
    if _is_hls_ready(sid):
        return sid

    out_dir = config.STREAMS / sid
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        await asyncio.to_thread(_build_sc_sync, url, out_dir, sid, segment_time)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not _is_hls_ready(sid):
        raise HTTPException(status_code=500, detail="soundcloud HLS build failed")

    return sid


async def _run_sc_build_job(job_id: str, url: str, segment_time: Optional[int]) -> None:
    job = _SC_BUILD_JOBS[job_id]
    job["state"] = "running"
    job["error"] = None

    try:
        result_sid = await _ensure_sc_stream(url, segment_time)
        job["state"] = "ready"
        job["result_sid"] = result_sid
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        job["state"] = "error"
        job["error"] = detail
    finally:
        async with _SC_BUILD_LOCK:
            _SC_BUILD_TASKS.pop(job_id, None)


async def _ensure_sc_build_job(url: str, segment_time: Optional[int] = None) -> Dict[str, Any]:
    url = normalize_sc_url(url)
    segment_time = utils.normalize_hls_segment_time(segment_time)
    sid = _sc_sid(url, segment_time)
    job_id = _sc_job_id(url, segment_time)

    async with _SC_BUILD_LOCK:
        job = _SC_BUILD_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "url": url,
                "segment_time": segment_time,
                "state": "pending",
                "result_sid": None,
                "error": None,
            },
        )

        job["segment_time"] = segment_time

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
        _SC_BUILD_TASKS[job_id] = asyncio.create_task(_run_sc_build_job(job_id, url, segment_time))
        return _job_snapshot(job_id)


@router.get("/stream-sc-build-start")
async def stream_sc_build_start(
    url: str = Query(...),
    segment_time: int | None = Query(default=None, ge=1),
):
    return await _ensure_sc_build_job(url, segment_time)


@router.get("/stream-sc-build-status")
async def stream_sc_build_status(job_id: str = Query(...)):
    snapshot = _job_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="soundcloud build job not found")
    return snapshot


@router.get("/stream-sc")
async def stream_sc(
    url: str = Query(...),
    segment_time: int | None = Query(default=None, ge=1),
):
    sid = await _ensure_sc_stream(url, segment_time)
    return _hls_response(sid)
