"""The 'model' of this pipeline = its tuned parameter set (classical CV, no neural weights).

    python -m vine.model save model/vine_model_v1.json     # snapshot current parameters
    python run.py --model model/vine_model_v1.json          # run with a saved model

Every parameter lives as a module constant; a model file simply overrides them.
"""
import datetime
import json
import sys

from . import candidates, rows, waste, route, cvat, assemble  # noqa: F401

PARAMS = {
    "candidates": ["RES", "N", "S", "NS"],
    "rows": ["RES", "CHUNK_M", "GAP_INSPECT_M", "TREE_OPEN_M", "T_CANOPY_BAND", "MIN_BRIGHT",
             "PLANT_JOIN_M", "CUT_GAP_M", "CUT_MIN_ROWS", "PLANT_MIN_M",
             "PLANT_SPLIT", "PLANT_SPLIT_LEN", "PLANT_MIN_SEP", "PLANT_SP_MIN", "CLF_THRESHOLD",
             "CANOPY_MODE", "CIVE_ROW_WEIGHT"],
    "waste": ["RES"],
    "route": ["RES", "VISIT_M", "CONNECT_M", "CONNECT_COST", "MERGE_M", "MAX_OUTSIDE", "TSP_TIME_S"],
    "cvat": ["GAP_DISRUPT_M", "COVER_RES", "VEG_EXG"],
}


def snapshot():
    mods = sys.modules
    p = {m: {k: getattr(mods[f"vine.{m}"], k) for k in ks} for m, ks in PARAMS.items()}
    return dict(name="siret3-vine-classical", version="3.0",
                created=datetime.date.today().isoformat(),
                method="classical CV: ExG + windowed FFT row periodicity, projection-histogram row angle, "
                       "chunked peak tracking, CIVE vegetation index + Otsu canopy, occupancy-profile plant splitting, colour-anomaly waste, "
                       "grid Dijkstra + 2-opt/Or-opt TSP routing",
                calibrated_on="Sireț3 example tiles r021_c012, r006_c004 (reference annotations)",
                params=p)


def save(path):
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(snapshot(), open(path, "w"), indent=2)
    return path


def load(path):
    m = json.load(open(path))
    for mod, kv in m["params"].items():
        for k, v in kv.items():
            setattr(sys.modules[f"vine.{mod}"], k, v)
    return m


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "save":
        print("saved", save(sys.argv[2]))
    else:
        print(json.dumps(snapshot(), indent=2))
