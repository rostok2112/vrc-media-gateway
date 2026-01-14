from fastapi import APIRouter, Query, Response, HTTPException
from pathlib import Path
import hashlib, subprocess
from ..config import STREAMS, YTDLP, FFMPEG
from ..utils import run_cmd, rewrite_m3u8, ffmpeg_audio_params

router = APIRouter()

@router.get("/stream-sc")
def stream_sc(url: str = Query(...)):
    track_id = hashlib.md5(url.encode()).hexdigest()
    out_dir = STREAMS / track_id
    m3u8 = out_dir / "index.m3u8"
    if not m3u8.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        audio = out_dir / "audio.m4a"
        subprocess.run([str(YTDLP), "-f", "bestaudio", "-o", str(audio), url], check=True)
        ff = [
            str(FFMPEG), "-y", "-i", str(audio), "-vn"
        ] + ffmpeg_audio_params() + [
            "-f", "hls", "-hls_time", "4", "-hls_list_size", "0", "-hls_playlist_type", "vod",
            str(m3u8)
        ]
        run_cmd(ff)
        rewrite_m3u8(m3u8, track_id)
    return Response(content=m3u8.read_text(), media_type="application/vnd.apple.mpegurl")
