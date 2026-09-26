from fastapi import APIRouter

from app.api.blocks import _ensure_block
from app.data import demo_v001 as demo
from app.schemas.geo import Feature, FeatureCollection

router = APIRouter(prefix="/api/blocks", tags=["layers"])


def _canopy_layer() -> FeatureCollection:
    return FeatureCollection(
        features=[
            Feature(
                geometry={"type": "Polygon", "coordinates": [c["polygon"]]},
                properties={
                    "canopy_id": c["canopy_id"],
                    "row_id": c["row_id"],
                    "vineyard_id": c["vineyard_id"],
                },
            )
            for c in demo.CANOPIES
        ]
    )


def _row_layer() -> FeatureCollection:
    return FeatureCollection(
        features=[
            Feature(
                geometry={"type": "LineString", "coordinates": r["line"]},
                properties={"row_id": r["row_id"], "vineyard_id": r["vineyard_id"]},
            )
            for r in demo.ROWS
        ]
    )


def _interrow_layer() -> FeatureCollection:
    return FeatureCollection(
        features=[
            Feature(
                geometry={"type": "Polygon", "coordinates": [ir["polygon"]]},
                properties={"interrow_id": ir["interrow_id"], "vineyard_id": ir["vineyard_id"]},
            )
            for ir in demo.INTERROWS
        ]
    )


def _waste_layer() -> FeatureCollection:
    return FeatureCollection(
        features=[
            Feature(
                geometry={"type": "Polygon", "coordinates": [w["bbox"]]},
                properties={"waste_id": w["waste_id"], "vineyard_id": w["vineyard_id"]},
            )
            for w in demo.WASTE
        ]
    )


def _target_layer() -> FeatureCollection:
    features = [
        Feature(
            geometry={"type": "Point", "coordinates": list(t["point"])},
            properties={"target_id": t["target_id"], "kind": t["kind"]},
        )
        for t in demo.TARGETS
    ]
    features.append(
        Feature(
            geometry={"type": "Point", "coordinates": list(demo.START_POINT)},
            properties={"target_id": "START", "kind": "start_end"},
        )
    )
    return FeatureCollection(features=features)


@router.get("/{block_id}/layers")
def get_layers(block_id: str) -> dict[str, FeatureCollection]:
    _ensure_block(block_id)
    return {
        "canopies": _canopy_layer(),
        "rows": _row_layer(),
        "interrows": _interrow_layer(),
        "waste": _waste_layer(),
        "targets": _target_layer(),
    }
