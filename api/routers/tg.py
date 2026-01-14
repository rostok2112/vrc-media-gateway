
from fastapi import APIRouter, Query, Response, HTTPException
import hashlib
from api import utils

router = APIRouter()

@router.get("/stream-tg-image")
def stream_tg_image(url: str = Query(...), duration: int = Query(300), width: int = Query(1280), height: int = Query(720)):
    try:
        html, final = utils.fetch_html(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"fetch html failed: {e}")

    img_url = utils.extract_image_from_html(html, base_url=final)
    if not img_url:
        raise HTTPException(status_code=404, detail="no image found in telegram post")

    if img_url.startswith("//"):
        img_url = "https:" + img_url

    sid = hashlib.md5((img_url + f"{duration}{width}x{height}").encode()).hexdigest()
    try:
        utils.build_hls_from_image(img_url, sid, duration=duration, width=width, height=height)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(status_code=200, headers={"X-Accel-Redirect": f"/streams/{sid}/index.m3u8", "Content-Type":"application/vnd.apple.mpegurl"})
