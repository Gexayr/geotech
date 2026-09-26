from fastapi import APIRouter

from app.api.blocks import _ensure_block
from app.schemas.geo import RouteResponse
from app.services.routing import compute_route

router = APIRouter(prefix="/api/blocks", tags=["route"])


@router.get("/{block_id}/route", response_model=RouteResponse)
def get_route(block_id: str) -> RouteResponse:
    _ensure_block(block_id)
    return compute_route(block_id)
