from fastapi import APIRouter, HTTPException

from app.data import demo_v001 as demo
from app.schemas.geo import BlockSummary
from app.services.metrics import _block_extent_m2

router = APIRouter(prefix="/api/blocks", tags=["blocks"])

_BLOCKS = {demo.BLOCK_ID: demo.BLOCK_NAME}


@router.get("", response_model=list[BlockSummary])
def list_blocks() -> list[BlockSummary]:
    return [
        BlockSummary(id=block_id, name=name, extent_m2=round(_block_extent_m2(), 2))
        for block_id, name in _BLOCKS.items()
    ]


def _ensure_block(block_id: str) -> None:
    if block_id not in _BLOCKS:
        raise HTTPException(status_code=404, detail=f"Unknown block '{block_id}'")


@router.get("/{block_id}", response_model=BlockSummary)
def get_block(block_id: str) -> BlockSummary:
    _ensure_block(block_id)
    return BlockSummary(
        id=block_id, name=_BLOCKS[block_id], extent_m2=round(_block_extent_m2(), 2)
    )
