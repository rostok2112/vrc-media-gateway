from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from api import config

router = APIRouter()

@router.get("/stream_segment/{stream_id}/{filename}")
def stream_segment(stream_id: str, filename: str):
    path = config.STREAMS / stream_id / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    if filename.endswith(".ts"):
        return FileResponse(path, media_type="video/mp2t")
    if filename.endswith(".m3u8"):
        return FileResponse(path, media_type="application/vnd.apple.mpegurl")
    if filename.endswith(".vtt"):
        return FileResponse(path, media_type="text/vtt")
    return FileResponse(path)
