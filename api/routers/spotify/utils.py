import asyncio
import json
from pathlib import Path
import shutil
import urllib.parse
import winappaudiorouter as war
from fastapi import HTTPException
from api.segments_engine import registry
from api.websockets.clients_name import ClientName
from api.websockets.registry import ws_registry

from api import config, utils

SPOTIFY_STREAM_LAYOUT_VERSION = "spotify-hls-v1"
DEFAULT_SEGMENT_TIME = int(config.SPOTIFY_HLS_OPTS.get("hls_time", 2))
DEFAULT_PREFETCH_SEGMENTS = int(config.SPOTIFY_HLS_OPTS.get("prefetch", 10))


def to_spotify_uri(url: str) -> str:
    if url.startswith("spotify:"):
        return url

    p = urllib.parse.urlparse(url)
    parts = p.path.split("/")

    if len(parts) >= 3 and parts[1] == "track":
        track_id = parts[2]
        return f"spotify:track:{track_id}"

    return url

async def get_duration_ms_via_ws(url: str) -> int:
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
        return 0

    return int(resp.get("duration_ms", 0))


def resolve_stream_options(
    segment_time: int | str | None = None,
    prefetch: int | str | None = None,
) -> tuple[int, int]:
    try:
        resolved_segment_time = int(segment_time) if segment_time not in (None, "") else DEFAULT_SEGMENT_TIME
    except (TypeError, ValueError):
        resolved_segment_time = DEFAULT_SEGMENT_TIME

    try:
        resolved_prefetch = int(prefetch) if prefetch not in (None, "") else DEFAULT_PREFETCH_SEGMENTS
    except (TypeError, ValueError):
        resolved_prefetch = DEFAULT_PREFETCH_SEGMENTS

    return max(1, resolved_segment_time), max(1, resolved_prefetch)


def spotify_stream_sid(
    url: str,
    segment_time: int | str | None = None,
    prefetch: int | str | None = None,
) -> str:
    resolved_segment_time, resolved_prefetch = resolve_stream_options(segment_time, prefetch)
    return utils.sid_for_url(
        url,
        SPOTIFY_STREAM_LAYOUT_VERSION,
        f"segment_time={resolved_segment_time}",
        f"prefetch={resolved_prefetch}",
    )


def write_metadata_to_file(
    out_dir: Path,
    duration_ms: int,
    segment_time: int,
    segments_count: int,
    url: str,
    prefetch: int | None = None,
):
    resolved_segment_time, resolved_prefetch = resolve_stream_options(segment_time, prefetch)
    meta = {
    "duration_ms": int(duration_ms),
    "seg_time": resolved_segment_time,
    "segment_time": resolved_segment_time,
    "prefetch": resolved_prefetch,
    "total_segments": segments_count,
    "url": url,
    }
    try:
        (out_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    except Exception:
        pass


async def clear_spotify_cache(
    url: str,
    segment_time: int | str | None = None,
    prefetch: int | str | None = None,
) -> dict:
    if not url:
        raise HTTPException(400, "missing url")

    sid = spotify_stream_sid(url, segment_time=segment_time, prefetch=prefetch)
    sids_to_remove = {sid, utils.sid_for_url(url)}
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
