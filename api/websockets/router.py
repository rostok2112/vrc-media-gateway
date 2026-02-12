from fastapi import APIRouter

from .endpoints import (
    spotify,
)

router = APIRouter()
router.include_router(spotify.router)

