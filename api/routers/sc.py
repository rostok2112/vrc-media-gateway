from fastapi import APIRouter, Query, Response, HTTPException
import hashlib, subprocess

from api import config, utils

router = APIRouter()

@router.get("/stream-sc")
def stream_sc(url: str = Query(...)):
    sid = hashlib.md5(url.encode()).hexdigest()
    out_dir = config.STREAMS / sid
    m3u8 = out_dir / "index.m3u8"
    if m3u8.exists():
        return Response(status_code=200, headers={"X-Accel-Redirect": f"/streams/{sid}/index.m3u8", "Content-Type":"application/vnd.apple.mpegurl"})

    out_dir.mkdir(parents=True, exist_ok=True)
    audio_out = out_dir / "audio.m4a"
    try:
        base_cmd = [config.YTDLP, "-f", "bestaudio", "-o", str(audio_out), url]
        if config.COOKIES.exists():
            try:
                subprocess.run([config.YTDLP, "--cookies", str(config.COOKIES), "-f", "bestaudio", "-o", str(audio_out), url], check=True)
            except subprocess.CalledProcessError:
                subprocess.run(base_cmd, check=True)
        else:
            subprocess.run(base_cmd, check=True)
        utils.audio_to_hls(audio_out, out_dir, sid)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(status_code=200, headers={"X-Accel-Redirect": f"/streams/{sid}/index.m3u8", "Content-Type":"application/vnd.apple.mpegurl"})
