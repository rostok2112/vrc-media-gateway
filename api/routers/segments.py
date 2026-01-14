from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from ..config import STREAMS

router = APIRouter()

@router.get("/stream_segment/{stream_id}/{filename}")
def stream_segment(stream_id: str, filename: str):
    path = STREAMS / stream_id / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    if filename.endswith(".ts"):
        return FileResponse(path, media_type="video/mp2t")
    return FileResponse(path, media_type="application/vnd.apple.mpegurl")
