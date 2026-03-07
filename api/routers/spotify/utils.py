import asyncio
import json
import math
from pathlib import Path
import shutil
import urllib.parse
import winappaudiorouter as war
from fastapi import HTTPException
from api.segments_engine import registry
from api.websockets.clients_name import ClientName
from api.websockets.registry import ws_registry

from api import config, utils

SPOTIFY_STREAM_LAYOUT_VERSION = "spotify-hls-v2"
DEFAULT_SEGMENT_TIME = int(config.SPOTIFY_HLS_OPTS.get("hls_time", 2))
DEFAULT_PREFETCH_SEGMENTS = int(config.SPOTIFY_HLS_OPTS.get("prefetch", 10))


def _bool_from_value(value, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


DEFAULT_SHOW_INFO = _bool_from_value(config.SPOTIFY_HLS_OPTS.get("show_info", True), True)


def to_spotify_uri(url: str) -> str:
    if url.startswith("spotify:"):
        return url

    p = urllib.parse.urlparse(url)
    parts = p.path.split("/")

    if len(parts) >= 3 and parts[1] == "track":
        track_id = parts[2]
        return f"spotify:track:{track_id}"

    return url

def normalize_spotify_cover_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("spotify:image:"):
        image_id = value.split(":")[-1].strip()
        if image_id:
            return f"https://i.scdn.co/image/{image_id}"
    if value.startswith("//"):
        return "https:" + value
    return value


async def get_track_metadata_via_ws(url: str) -> dict:
    uri = to_spotify_uri(url)

    await ws_registry.rpc_call(
        ClientName.SPOTIFY,
        "load",
        {"uri": uri},
        timeout=5
    )

    await asyncio.sleep(0.5)

    resp = await ws_registry.rpc_call(
        ClientName.SPOTIFY,
        "metadata",
        {"uri": uri},
        timeout=5
    )

    if not resp:
        return {}

    title = str(resp.get("title") or "").strip() or "Spotify track"
    performer = str(resp.get("performer") or "").strip()
    cover_url = normalize_spotify_cover_url(resp.get("cover_url") or "")

    try:
        duration_ms = int(resp.get("duration_ms", 0) or 0)
    except (TypeError, ValueError):
        duration_ms = 0

    return {
        "duration_ms": max(0, duration_ms),
        "title": title,
        "performer": performer,
        "cover_url": cover_url,
    }


async def get_duration_ms_via_ws(url: str) -> int:
    meta = await get_track_metadata_via_ws(url)
    if not meta:
        return 0

    return int(meta.get("duration_ms", 0) or 0)


def resolve_stream_options(
    segment_time: int | str | None = None,
    prefetch: int | str | None = None,
    show_info: bool | str | int | None = None,
) -> tuple[int, int, bool]:
    try:
        resolved_segment_time = int(segment_time) if segment_time not in (None, "") else DEFAULT_SEGMENT_TIME
    except (TypeError, ValueError):
        resolved_segment_time = DEFAULT_SEGMENT_TIME

    try:
        resolved_prefetch = int(prefetch) if prefetch not in (None, "") else DEFAULT_PREFETCH_SEGMENTS
    except (TypeError, ValueError):
        resolved_prefetch = DEFAULT_PREFETCH_SEGMENTS

    resolved_show_info = _bool_from_value(show_info, DEFAULT_SHOW_INFO)

    return max(1, resolved_segment_time), max(1, resolved_prefetch), resolved_show_info


def spotify_stream_sid(
    url: str,
    segment_time: int | str | None = None,
    prefetch: int | str | None = None,
    show_info: bool | str | int | None = None,
) -> str:
    resolved_segment_time, resolved_prefetch, resolved_show_info = resolve_stream_options(segment_time, prefetch, show_info)
    return utils.sid_for_url(
        url,
        SPOTIFY_STREAM_LAYOUT_VERSION,
        f"segment_time={resolved_segment_time}",
        f"prefetch={resolved_prefetch}",
        f"show_info={int(resolved_show_info)}",
        utils.AUDIO_POSTER_LAYOUT_VERSION if resolved_show_info else "audio-only",
    )


def build_spotify_stream_metadata(
    url: str,
    *,
    duration_ms: int,
    segment_time: int,
    prefetch: int | None = None,
    show_info: bool | str | int | None = None,
    title: str = "",
    performer: str = "",
    cover_url: str = "",
) -> dict:
    resolved_segment_time, resolved_prefetch, resolved_show_info = resolve_stream_options(
        segment_time,
        prefetch,
        show_info,
    )
    total_segments = int(math.ceil(max(0, int(duration_ms or 0)) / (resolved_segment_time * 1000))) if duration_ms else 0
    return {
        "duration_ms": int(duration_ms or 0),
        "seg_time": resolved_segment_time,
        "segment_time": resolved_segment_time,
        "prefetch": resolved_prefetch,
        "show_info": resolved_show_info,
        "title": str(title or "").strip() or "Spotify track",
        "performer": str(performer or "").strip(),
        "cover_url": normalize_spotify_cover_url(cover_url),
        "total_segments": total_segments,
        "url": url,
        "start_time": None,
    }


def ensure_spotify_poster_assets(out_dir: Path, meta: dict) -> None:
    if not _bool_from_value(meta.get("show_info"), DEFAULT_SHOW_INFO):
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    cover_url = normalize_spotify_cover_url(meta.get("cover_url") or "")
    cover_path = None

    if cover_url:
        suffix = Path(urllib.parse.urlparse(cover_url).path or "").suffix.lower() or ".jpg"
        maybe_cover = out_dir / f"spotify_cover{suffix}"
        if maybe_cover.exists() and maybe_cover.stat().st_size > 0:
            cover_path = maybe_cover
        else:
            try:
                cover_path = utils.download_file(cover_url, maybe_cover, max_bytes=16 * 1024 * 1024)
            except Exception:
                cover_path = None

    utils.render_audio_poster(
        out_dir / "audio_poster.png",
        title=str(meta.get("title") or "").strip() or "Spotify track",
        performer=str(meta.get("performer") or "").strip(),
        cover_image=cover_path,
        duration_seconds=(int(meta.get("duration_ms") or 0) / 1000.0) if meta.get("duration_ms") else None,
        source_label="Spotify",
    )


def write_metadata_to_file(
    out_dir: Path,
    duration_ms: int,
    segment_time: int,
    segments_count: int,
    url: str,
    prefetch: int | None = None,
    show_info: bool | str | int | None = None,
    title: str = "",
    performer: str = "",
    cover_url: str = "",
):
    meta = build_spotify_stream_metadata(
        url,
        duration_ms=duration_ms,
        segment_time=segment_time,
        prefetch=prefetch,
        show_info=show_info,
        title=title,
        performer=performer,
        cover_url=cover_url,
    )
    meta["total_segments"] = int(segments_count)
    try:
        (out_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    except Exception:
        pass


async def clear_spotify_cache(
    url: str,
    segment_time: int | str | None = None,
    prefetch: int | str | None = None,
    show_info: bool | str | int | None = None,
) -> dict:
    if not url:
        raise HTTPException(400, "missing url")

    sid = spotify_stream_sid(url, segment_time=segment_time, prefetch=prefetch, show_info=show_info)
    sids_to_remove = {sid, utils.sid_for_url(url)}
    for info_mode in (False, True):
        sids_to_remove.add(
            spotify_stream_sid(
                url,
                segment_time=segment_time,
                prefetch=prefetch,
                show_info=info_mode,
            )
        )
    removed_sids = []

    for target_sid in sids_to_remove:
        out_dir = utils.out_dir_for_sid(target_sid)

        try:
            await registry.stop_stream(target_sid)
        except Exception:
            pass

        if not out_dir.exists():
            continue

        try:
            shutil.rmtree(out_dir)
            removed_sids.append(target_sid)
        except Exception:
            raise HTTPException(500, "cache remove failed")

    return {"ok": True, "sid": sid, "removed": bool(removed_sids), "removed_sids": removed_sids}


async def restore_audio_route(process_id: int | None = None, process_name: str | None = None) -> dict:
    if not process_id and not process_name:
        process_name = config.SPOTIFY

    try:
        result = war.clear_app_output_device(process_id=process_id, process_name=process_name)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
