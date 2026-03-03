import re
from api import config
from api.routers.infrastructure.utils import get_latest_tunnel_url
from fastapi import APIRouter
from api import config

router = APIRouter()


@router.get("/tunnel")
def get_tunnel():
    url = get_latest_tunnel_url()
    return {"url": url}