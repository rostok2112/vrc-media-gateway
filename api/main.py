from fastapi import FastAPI
from .routers import (
    telegram, sc, yt, img, segments, infrastructure, spotify, local_media
)
from .websockets import router as ws_router

app = FastAPI(title="vrc-media-gateway")

API_ROUTE_PREFIX = "/api"
WS_ROUTE_PREFIX = API_ROUTE_PREFIX + "/ws"

# include routers
app.include_router(telegram.router, prefix=API_ROUTE_PREFIX)
app.include_router(sc.router, prefix=API_ROUTE_PREFIX)
app.include_router(yt.router, prefix=API_ROUTE_PREFIX)
app.include_router(img.router, prefix=API_ROUTE_PREFIX)
app.include_router(segments.router, prefix=API_ROUTE_PREFIX)
app.include_router(infrastructure.router, prefix=API_ROUTE_PREFIX)
app.include_router(spotify.router, prefix=API_ROUTE_PREFIX)
app.include_router(local_media.router, prefix="/local-api")

app.include_router(ws_router, prefix=WS_ROUTE_PREFIX)
