from fastapi import FastAPI
from .routers import (
    tg, sc, yt, img, segments, infrastructure
)

app = FastAPI(title="vrc-media-gateway")

API_ROUTE_PREFIX = "/api"
# include routers
app.include_router(tg.router, prefix=API_ROUTE_PREFIX)
app.include_router(sc.router, prefix=API_ROUTE_PREFIX)
app.include_router(yt.router, prefix=API_ROUTE_PREFIX)
app.include_router(img.router, prefix=API_ROUTE_PREFIX)
app.include_router(segments.router, prefix=API_ROUTE_PREFIX)
app.include_router(infrastructure.router, prefix=API_ROUTE_PREFIX)
