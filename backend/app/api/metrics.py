from fastapi import APIRouter

from app.api.blocks import _ensure_block
from app.schemas.geo import MetricsResponse
from app.services.metrics import compute_metrics

router = APIRouter(prefix="/api/blocks", tags=["metrics"])


@router.get("/{block_id}/metrics", response_model=MetricsResponse)
def get_metrics(block_id: str) -> MetricsResponse:
    _ensure_block(block_id)
    return compute_metrics(block_id)
