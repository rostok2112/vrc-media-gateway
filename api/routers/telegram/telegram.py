from fastapi import APIRouter, Query, Response, HTTPException
from api import config, utils
from api.routers.telegram import utils as telegram_utils


router = APIRouter()


@router.get("/stream-tg-image")
async def stream_tg_image(
    url: str = Query(...),
    duration: int = Query(300),
    width: int = Query(1280),
    height: int = Query(720),
):

    sid = utils.sid_for_url(url, f"{duration}{width}x{height}")
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

    # =========================
    # 1️⃣ PRIMARY — TELETHON
    # =========================

    try:
        img = await telegram_utils.download_tg_photo(url)

        utils.build_hls_from_image(
            str(img),
            sid,
            duration=duration,
            width=width,
            height=height
        )

        return Response(
            status_code=200,
            headers={
                "X-Accel-Redirect": f"/streams/{sid}/index.m3u8",
                "Content-Type": "application/vnd.apple.mpegurl",
            },
        )

    except Exception as e:
        print("Telethon failed, using HTML fallback:", e)


    # =========================
    # 2️⃣ FALLBACK — OLD METHOD
    # =========================

    try:
        html, final = utils.fetch_html(url)
    except Exception as e:
        raise HTTPException(400, f"fetch html failed: {e}")

    img_url = utils.extract_image_from_html(html, base_url=final)

    if not img_url:
        raise HTTPException(404, "no image found in telegram post")

    if img_url.startswith("//"):
        img_url = "https:" + img_url

    img_sid = utils.sid_for_url(img_url, f"{duration}{width}x{height}")

    img_m3u8 = config.STREAMS / img_sid / "index.m3u8"

    if img_m3u8.exists():
        return Response(
            status_code=200,
            headers={
                "X-Accel-Redirect": f"/streams/{img_sid}/index.m3u8",
                "Content-Type": "application/vnd.apple.mpegurl",
            },
        )

    try:
        utils.build_hls_from_image(
            img_url,
            img_sid,
            duration=duration,
            width=width,
            height=height
        )
    except Exception as e:
        raise HTTPException(500, str(e))

    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": f"/streams/{img_sid}/index.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl",
        },
    )
@router.get("/stream-tg-video")
async def stream_tg_video(url: str = Query(...)):
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="invalid url")

    sid = utils.sid_for_url(url)
    out_dir = config.STREAMS / sid
    m3u8 = out_dir / "index.m3u8"

    # cache hit
    if m3u8.exists():
        return Response(status_code=200, headers={
            "X-Accel-Redirect": f"/streams/{sid}/index.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl",
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        video = await telegram_utils.download_tg_video(url)
        utils.video_to_hls(video, out_dir, sid)

        return Response(status_code=200, headers={
            "X-Accel-Redirect": f"/streams/{sid}/index.m3u8",
            "Content-Type": "application/vnd.apple.mpegurl",
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/resolve-tg-public-link")
async def resolve_tg_public_link(internal: str = Query(...)):
    try:
        link = await telegram_utils.resolve_public_tg_link(internal)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not link:
        raise HTTPException(status_code=404, detail="public username not found")

    return {"url": link}
