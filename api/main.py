from fastapi import FastAPI
from .routers import tg, sc, yt, segments

app = FastAPI(title="vrc-media-gateway")

# include routers
app.include_router(tg.router, prefix="/api")
app.include_router(sc.router, prefix="/api")
app.include_router(yt.router, prefix="/api")
app.include_router(segments.router, prefix="/api")
