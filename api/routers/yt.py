from fastapi import APIRouter, Query, Response, HTTPException
import hashlib, subprocess, urllib.parse as urlparse

from api import config, utils


router = APIRouter()

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

@router.get("/stream-yt")
def stream_yt(url: str = Query(...)):
    norm = normalize_yt_url(url)
    sid = hashlib.md5(norm.encode()).hexdigest()
    out_dir = config.STREAMS / sid
    m3u8 = out_dir / "index.m3u8"

    if m3u8.exists():
        return Response(
            status_code=200,
            headers={
                "X-Accel-Redirect": f"/streams/{sid}/index.m3u8",
                "Content-Type": "application/vnd.apple.mpegurl",
            },
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    video_out = out_dir / "video.mp4"

    try:
        cmd = [
            config.YTDLP,
            "--js-runtimes", "node",
            "--remote-components", "ejs:github",
            "--cookies", str(config.COOKIES),
            "-f", "best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", str(video_out),
            norm,
        ]

        subprocess.run(cmd, check=True)

    except subprocess.CalledProcessError:
        try:
            subprocess.run([
                config.YTDLP,
                "--js-runtimes", "node",
                "--remote-components", "ejs:github",
                "-f", "best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "-o", str(video_out),
                norm,
            ], check=True)

        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"yt-dlp failed: {e}")

    try:
        utils.video_to_hls(video_out, out_dir, sid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": f"/streams/{sid}/index.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl",
        },
    )