from fastapi import APIRouter, Query, Response, HTTPException
import hashlib

from api import utils


router = APIRouter()

@router.get("/stream-image")
def stream_image(url: str = Query(...), duration: int = Query(300), width: int = Query(1280), height: int = Query(720)):
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="invalid url")

    sid = hashlib.md5((url + f"{duration}{width}x{height}").encode()).hexdigest()
    try:
        utils.build_hls_from_image(url, sid, duration=duration, width=width, height=height)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(status_code=200, headers={"X-Accel-Redirect": f"/streams/{sid}/index.m3u8", "Content-Type":"application/vnd.apple.mpegurl"})
