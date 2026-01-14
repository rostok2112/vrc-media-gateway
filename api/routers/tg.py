from fastapi import APIRouter, Query, Response, HTTPException
from pathlib import Path
import hashlib
from ..utils import try_fetch_telegram_post, extract_image_from_telegram_html, run_cmd, ffmpeg_audio_params, rewrite_m3u8
from ..config import STREAMS, FFMPEG

router = APIRouter()

@router.get("/stream-tg-image")
def stream_tg_image(url: str = Query(...), duration: int = Query(300), width: int = Query(1280), height: int = Query(720)):
    if duration <= 0 or duration > 3600:
        raise HTTPException(status_code=400, detail="duration out of range")
    html, final_url = try_fetch_telegram_post(url)
    img_url = extract_image_from_telegram_html(html, base_url=final_url)
    if not img_url:
        raise HTTPException(status_code=404, detail="no image found in telegram post")
    if img_url.startswith("//"):
        img_url = "https:" + img_url

    img_id = hashlib.md5((img_url + f"{duration}{width}x{height}").encode()).hexdigest()
    out_dir = STREAMS / img_id
    m3u8 = out_dir / "index.m3u8"
    if m3u8.exists():
        return Response(content=m3u8.read_text(), media_type="application/vnd.apple.mpegurl")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_img = out_dir / "src.jpg"
    import requests
    try:
        with requests.get(img_url, stream=True, headers={"User-Agent":"Mozilla/5.0"}, timeout=15) as r:
            r.raise_for_status()
            total = 0
            with tmp_img.open("wb") as f:
                for chunk in r.iter_content(8192):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 8 * 1024 * 1024:
                        raise HTTPException(status_code=400, detail="image too large")
                    f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"failed to download image: {e}")

    video_tmp = out_dir / "video.mp4"
    cmd = [
        FFMPEG, "-y", "-loop", "1", "-i", str(tmp_img),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p",
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(video_tmp)
    ]
    run_cmd(cmd)

    hls_cmd = [
        FFMPEG, "-y", "-i", str(video_tmp),
        "-c:v", "libx264", "-c:a", "aac",
        "-profile:a", "aac_low", "-ac", "2", "-ar", "48000", "-b:a", "128k",
        "-f", "hls", "-hls_time", "4", "-hls_list_size", "0", "-hls_playlist_type", "vod",
        str(m3u8)
    ]
    run_cmd(hls_cmd)
    rewrite_m3u8(m3u8, img_id)
    return Response(content=m3u8.read_text(), media_type="application/vnd.apple.mpegurl")
