"""Manually-drawn vineyard block boundaries.

The brief only auto-derives block extent from canopy annotations, but a
reviewer often needs to set/correct a block's outer boundary by hand before
that annotation exists or to fix it. Persisted to a small JSON file — a
stand-in for a database, adequate for a hackathon-scale single-project
store; swap for a real DB if this needs to survive concurrent writers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

STORE_PATH = Path(__file__).parent.parent / "data" / "real" / "custom_blocks.json"


def _load() -> list[dict[str, Any]]:
    if not STORE_PATH.exists():
        return []
    with open(STORE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(blocks: list[dict[str, Any]]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(blocks, f, indent=2)


def list_blocks() -> list[dict[str, Any]]:
    return _load()


def add_block(vineyard_id: str, polygon_local: list[list[float]]) -> dict[str, Any]:
    blocks = _load()
    entry = {
        "id": f"custom-{int(time.time() * 1000)}",
        "vineyard_id": vineyard_id,
        "polygon": polygon_local,
        "created_at": time.time(),
    }
    blocks.append(entry)
    _save(blocks)
    return entry


def delete_block(block_id: str) -> bool:
    blocks = _load()
    remaining = [b for b in blocks if b["id"] != block_id]
    if len(remaining) == len(blocks):
        return False
    _save(remaining)
    return True
